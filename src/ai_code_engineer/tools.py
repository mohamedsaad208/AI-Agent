"""The tool vocabulary of the planning loop: what a turn may ask for, and what each asking returns.

`engine.plan` runs a conversation in which every model turn names exactly one action. Eight of those
actions fetch something — `list_files`, `read_file`, `search_code`, `find_symbol`,
`find_references` read the tree, and `run_tests`, `run_build`, `git_diff` run one fixed command the
runtime itself chose from the project's own build files, never a command the model wrote. Three
decide something: `propose` ends the loop with an offer, `blocked` ends it with a refusal, and
`complete` ends it with nothing to change. The fetching eight are the tools, and this module is
their whole definition as `contracts.ToolContract`s plus the handler each satisfied call runs: the
typed fields each one accepts, the `Result` it produces, and the advice that rides along when the
answer came back empty. The judgement itself — shape, dialects, field kinds, the error taxonomy —
is not restated here; it is the contract layer's one job, and the registry only dispatches on it.

The three deciding actions stay in the engine because they are control flow rather than retrieval —
`propose` returns the session path, `blocked` raises, `complete` closes the task — but they are
named here anyway as `CONTROL_ACTIONS`. The refusal for an unknown action has to list the whole
vocabulary, or it teaches the model a menu that is missing the one thing it was trying to do.

A tool writes no project file, records no event and draws no row on its own authority. The three
execution tools keep that contract in the one place it could break: the command runs against a
temporary copy of the workspace (`runner.copy_for_sandbox`), so nothing a build writes can reach
the folder the user opened. A tool mutates the context it was handed (`observed`, the declaration
index) and calls the context's own callbacks, so the ordering of the audit record stays the loop's
decision and a tool can be run in a test against nothing but a workspace and three lists.

Two things cross into this module from outside its own vocabulary. The first is the admission
decision: each contract names the `policy` class of what it does and the rung of the ladder the doing
reaches, and the registry hands both to `gate.SecurityGate` before the handler may run — this is the
checked boundary `policy` says task-originated actions need, and a class that is not allowed is
refused as an observation, not executed. The registry is not the authority on that judgement, only
its call site: it restates a shape complaint as this vocabulary's own menu and asks the gate's answer
for the class each tool names. The `SandboxGuard` on the context is the same question asked of the
run's posture rather than the folder's rule; the production default reaches every rung the ladder
has, so the guard refuses only what a tighter caller hands it. The second is the state delta: what a
tool's own run establishes — a suite's verdict, a tree's cleanliness — rides back on the `Result` as a
`taskstate.merge`-shaped fragment, written from the observation and never from model prose, so the
continuity record holds it after the history that carried the full output is trimmed away.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
import tempfile
import uuid
from typing import TYPE_CHECKING, Any

from . import contracts, gate, git_integration, policy, refusals, runner, symbols
from .contracts import (Call, Field, Result, SandboxGuard,
                        ToolContract, UnknownTool, unwrap_args)
from .errors import MissingFileError, PolicyError
from .redaction import redact

if TYPE_CHECKING:
    from .config import Settings
    from .workspace import Workspace


# How many files `list_files` returns, and the one extra it asks for so that a project holding
# exactly 300 files and one holding 400 do not answer identically. `find_symbol` plays the same
# trick on `symbols.MAX_HITS`: a truncated answer has to say so, because a small model reads a full
# page of results as the whole truth and stops looking.
LIST_LIMIT = 300

# The actions that decide rather than fetch. The engine handles all three itself, because `propose`
# returns the session path, `blocked` raises, and `complete` closes the task; they are registered
# here only so that the refusal for an unknown action can list a vocabulary the model can actually
# choose from. `complete` carries one optional field — the sentence saying why nothing needs to
# change — which the engine validates, the way it validates a proposal's own prose.
CONTROL_ACTIONS = ("propose", "blocked", "complete")

# How much of a command's output may ride back as one observation. The tail is the evidence a repair
# turn reasons over; the floor keeps even the smallest legal window able to hold a verdict and the
# failing lines, and the ceiling never lets one run crowd out the rest of the conversation.
def _evidence_chars(settings) -> int:
    return max(1200, int(getattr(settings, "context_chars", 64000)) // 6)


# The compile-only counterpart of each test recipe, where the ecosystem has a separate build verb at
# all. Only Maven does in `runner.RECIPES`; everywhere else compiling is part of running the suite,
# and `run_build` says so instead of running the tests under another name.
BUILD_RECIPE = {"maven-test": "maven-compile"}

# The posture a planning loop runs under: every rung of the ladder is inside what the policy table
# already judges, so the guard refuses nothing the folder allowed. A caller that wants a tighter
# run — a sandbox, a read-only tour — hands this field a lower ceiling and every contract above is
# measured against it without this module knowing anything about the caller. Stated once in `gate`
# and named here, because the gate and the registry have to agree on what an open run means.
OPEN_GUARD = gate.OPEN_GUARD


@dataclass
class ToolContext:
    """One turn's worth of state a tool may read or touch.

    `record`, `announce` and `progress` are the loop's own closures rather than a session object, so
    a tool cannot persist anything the loop did not decide to persist — and so the three ways one
    action is said (the audit event, the conversation row, the status strip) stay tied together by
    the id the registry puts on this context for the call in flight, and `announce` takes.

    `verdict` is the loop's answer for one policy class, and it defaults to the table alone
    (`policy.decide`). The classes the eight built-in tools run under are managed, so no folder
    states them and the table's own answer is the whole answer; the callable is a field rather than a
    direct call so a caller that can read a folder's stored rows points the same seam at them without
    the tool layer ever learning where those rows live. `guard` is the same seam for the run's
    posture rather than the folder's rule.

    `trace_id` is the run's own identity, handed in once and never judged. `call_id` is the
    transient half of the same pair: the registry puts the id of the call in flight here, and the loop
    takes it from here when it writes that call's row — which is why the two carry one key rather than
    two that have to be matched by timestamp.
    """

    ws: Workspace
    settings: Settings
    observed: dict[str, str]
    record: Callable[..., None]
    announce: Callable[..., None]
    progress: Callable[[str], None]
    rows: list[dict] = field(default_factory=list)
    verdict: Callable[[str], str] = policy.decide
    guard: SandboxGuard = OPEN_GUARD
    trace_id: str = ""
    call_id: str = ""

    def take_call_id(self) -> str:
        """The id of the call in flight, once — a second row for one call mints its own key.

        Taken rather than read because the field is a handoff, not a record: leaving it set would let
        the next thing the loop announces — a proposal, a completion — share this call's row identity,
        and two rows under one key is the failure a clicked row cannot be recovered from.
        """
        step_id, self.call_id = self.call_id, ""
        return step_id

    def index(self) -> list[dict]:
        """The parsed declaration rows, rebuilt on the call and left behind on the context.

        Leaving them behind is the part that matters. `propose` reads this same list for its impact
        analysis, so a symbol tool that just rebuilt the index has to hand the fresh copy back
        instead of letting the proposal reason over the one the turn started with — a file created
        between the two would otherwise be invisible to the very report that warns about it.
        """
        _files, self.rows = self.ws.index()
        return self.rows


Handler = Callable[[Call, ToolContext], Result]


def admitted_run(call: Call, context: ToolContext, invoke: Handler) -> Result:
    """The id cycle every provider owes a call it has already admitted, in one place.

    The id goes on the context *before* the handler runs, so the row the handler announces and the
    result handed back are written under one key, and it comes off whatever the handler did: a call
    that announced nothing must not leave its id behind for the next action's row to inherit, because
    two rows under one key is a row nobody can open. Held here rather than inside the registry's own
    dispatch because a second provider — one whose tools arrive from outside this runtime — owes the
    identical sequence, and an invariant copied twice is an invariant dropped once.
    """
    context.call_id = call_id = uuid.uuid4().hex[:8]
    call = replace(call, trace_id=context.trace_id, call_id=call_id)
    try:
        result = invoke(call, context)
    finally:
        context.call_id = ""
    return call.traced(result)


class ToolRegistry:
    """The set of tools a loop runs, in the order a refusal lists them.

    Registration order is the vocabulary order, which is why this is a dict and not a set: the
    sentence a model is refused with is also the sentence it learns from, and a menu that rearranges
    itself between turns is a menu nobody can memorise.

    The registry dispatches; it no longer judges. Shape, the folder's verdict, the run's posture and
    what the contract's provenance implies all belong to `gate.SecurityGate`, and a registry keeping
    its own copy of those rules would be a second authority over one call — the drift the gate exists
    to close. Every registry is native unless its caller hands it another gate, which is what makes a
    tool registered here be judged as code this runtime wrote.
    """

    def __init__(self, tools: Iterable[tuple[ToolContract, Handler]] = (),
                 control: Iterable[str] = (),
                 *, security_gate: gate.SecurityGate | None = None):
        self._tools: dict[str, tuple[ToolContract, Handler]] = {}
        for contract, handler in tools:
            self.register(contract, handler)
        self._control = tuple(control)
        self.security_gate = security_gate or gate.NATIVE_GATE

    def register(self, contract: ToolContract, handler: Handler) -> None:
        """Add a tool, refusing to shadow one already there.

        A second registration under the same name is a mistake in the caller, not a configuration
        preference: silently keeping the first would leave a tool that looks registered and never
        runs, which is the failure nobody can see from the menu.
        """
        if contract.name in self._tools:
            raise ValueError("Tool already registered: " + str(contract.name))
        self._tools[contract.name] = (contract, handler)

    def names(self) -> tuple[str, ...]:
        """Every action a turn may take, tools first and the caller's own control actions last."""
        return tuple(self._tools) + self._control

    def contracts(self) -> tuple[ToolContract, ...]:
        """The offered contracts in vocabulary order — what a request builder and a router both ask."""
        return tuple(contract for contract, _handler in self._tools.values())

    def contract(self, name: str) -> ToolContract | None:
        """The contract behind one action, or None when no action of this vocabulary names it."""
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def handler(self, name: str) -> Handler:
        """The handler for one action, for a provider that dispatches through this registry.

        Raises rather than returns None: a provider holding a contract it did not register here would
        otherwise get a `None` it then calls, and the traceback for that says nothing about the pair
        having drifted.
        """
        entry = self._tools.get(name)
        if entry is None:
            raise UnknownTool(self.refusal({"action": name}))
        return entry[1]

    def refusal(self, action: dict, detail: str = "") -> str:
        """The one refusal a model cannot fix without seeing itself.

        "Invalid fields" is true of eight different mistakes, and the shape example in the prompt
        does not say which of *its* keys was the problem, so the offending shape is echoed back.
        Field names are the half of a reply that carries no project content, which is what makes
        them safe to echo and to record. The contract's own complaint rides beside the echo when
        the name was right and the asking was not.
        """
        return ("Unknown action or invalid fields: "
                + contracts.action_shape(action)
                + (". " + detail.rstrip(".") if detail else "")
                + ". Allowed: " + ", ".join(self.names())
                + ". Each of those takes exactly action plus the one field named for it.")

    def run(self, action: dict[str, Any], context: ToolContext) -> Result:
        """Execute the action the turn named, or refuse it by shape, by policy, or by posture.

        The envelope is made canonical here as well as in the loop, so a registry run on its own
        answers the dialects a model actually sends and not only the flat one the prompt shows. The
        explanation is dropped rather than kept: no tool reads one, and `blocked` — the single action
        that does — is control flow and never reaches this method.

        The shape is judged first, then the policy verdict, then the sandbox's ceiling, so a
        mistyped envelope is answered as the mistake it is rather than as a refusal about a class,
        and a tool either gate refuses never reaches its handler. Judgement itself belongs to the
        gate and to the contract: `SecurityGate.admit` validates the envelope, audits what the
        contract's provenance implies, asks the folder for every class the call belongs to and
        measures the call against the run's posture — restating a `MalformedCall` through this
        registry's own menu, which is the one part of that sentence only the vocabulary can supply.

        Raises the same `PolicyError`-shaped refusals the loop already turns into observations: an
        unrecognised or disallowed action costs the model one turn and a sentence of advice rather
        than the run.
        """
        envelope = unwrap_args(action)
        name = envelope.get("action") if isinstance(envelope, dict) else None
        entry = self._tools.get(name) if isinstance(name, str) else None
        if entry is None:
            raise UnknownTool(self.refusal(
                envelope if isinstance(envelope, dict) else {}))
        contract, handler = entry
        call = self.security_gate.admit(contract, envelope, verdict=context.verdict,
                                        guard=context.guard, refusal=self.refusal)
        return self.dispatch(contract, call, context, handler=handler)

    def dispatch(self, contract: ToolContract, call: Call, context: ToolContext,
                 *, handler: Handler | None = None) -> Result:
        """Run a call the gate has already admitted — the half of `run` a provider reuses.

        The handler is normally found from the contract the gate judged rather than passed in, so a
        caller holding a `Call` cannot reach a handler that was never admitted for it. The id cycle
        is `admitted_run`'s, shared with every other provider's dispatch.
        """
        if handler is None:
            entry = self._tools.get(call.tool)
            if entry is None or entry[0] is not contract:
                raise UnknownTool(self.refusal({"action": call.tool}))
            handler = entry[1]
        return admitted_run(call, context, handler)


def _list_files(call: Call, context: ToolContext) -> Result:
    names = context.ws.files(limit=LIST_LIMIT + 1)
    shown = names[:LIST_LIMIT]
    context.record("tool", name="list_files", count=len(shown))
    # The count goes to the record, not the sentence: "Scanning project files..." is what the row
    # says, and how many it found is what opening it answers.
    context.announce("list_files", count=len(shown))
    return Result(status=contracts.OK if names else contracts.EMPTY,
                  data={"files": shown, "truncated": len(names) > LIST_LIMIT},
                  advice="" if names else (
                      "An empty project is expected for a first task. Propose the new files the "
                      "plan calls for instead of blocking: " + refusals.PROPOSE_SHAPE))


def _missing_file(relative: str, context: ToolContext) -> Result:
    """A path that is not there, answered as the two different things that can mean.

    Dropping it from `observed` first is the half that is easy to miss: a file read earlier in this
    run and removed since must not stay authorised for a proposal, because the digest a proposal is
    honoured against now names content nobody can see.
    """
    context.observed.pop(relative, None)
    try:
        context.ws.path(relative, writable=True)
    except PolicyError:
        can_create = False
    else:
        can_create = True
    context.record("file_not_found", path=relative, can_create=can_create)
    context.progress("File not found: " + relative
                     + (" — a new-file proposal is allowed." if can_create
                        else " — creation is protected."))
    if can_create:
        next_step = ("If the task requires this new file, propose its complete content; "
                     "do not read it again. Otherwise list/search existing files.")
        advice = ("A missing file is not a failure: propose it as a new file at " + relative
                  + " with its complete content. Do not return "
                    'action="blocked" for a file you are allowed to create.')
    else:
        next_step = "This path cannot be created under the current policy."
        advice = ""
    return Result(status=contracts.EMPTY,
                  data={"path": relative, "status": "not_found", "exists": False,
                        "can_create": can_create, "next_step": next_step},
                  advice=advice)


def _read_file(call: Call, context: ToolContext) -> Result:
    relative = call.get("path")
    offset, limit = call.get("offset"), call.get("limit")
    try:
        found = context.ws.read(relative, offset, limit)
    except MissingFileError:
        return _missing_file(relative, context)
    if len(found["content"]) > context.settings.context_chars // 2:
        # Refused, not truncated, and never authorised: an authorisation against half a file is
        # what lets a proposal replace the half the model never saw. The window is now the
        # model's to ask for, so the refusal says so instead of ending the task.
        raise PolicyError("This answer exceeds the model context budget; re-read the file in "
                          "smaller windows (offset/limit), or use a smaller task.")
    data = dict(found)
    # Seeing the whole file is what buys the right to rewrite it: from line 1, nothing truncated.
    if data["from_line"] == 1 and not data["truncated"]:
        context.observed[found["path"]] = found["sha256"]
    elif data["truncated"]:
        data["next_step"] = ("The file continues at line " + str(data["to_line"] + 1)
                             + " of " + str(data["total_lines"])
                             + "; re-read with offset=" + str(data["to_line"] + 1)
                             + " for the rest. A window does not authorise rewriting the file.")
    else:
        data["next_step"] = ("This window starts at line " + str(data["from_line"])
                             + " and does not authorise rewriting the file; a complete read "
                             "from line 1 does.")
    if data["from_line"] > data["to_line"]:
        context.record("tool", name="read_file", path=found["path"], lines=0)
        return Result(status=contracts.EMPTY,
                      data={"path": found["path"], "status": "empty_window",
                            "from_line": data["from_line"], "total_lines": data["total_lines"],
                            "next_step": ("Line " + str(data["from_line"]) + " is past the end; "
                                          "this file has " + str(data["total_lines"]) + " lines.")},
                      advice="Read the file from an offset inside its line count rather than "
                             "blocking on an empty window.")
    paged = offset is not None or limit is not None
    record_fields: dict[str, Any] = {"path": found["path"], "sha256": found["sha256"]}
    announce_fields: dict[str, Any] = {"path": found["path"], "digest": found["sha256"][:8]}
    if paged:
        record_fields.update({"from_line": data.get("from_line"), "to_line": data.get("to_line"),
                              "total_lines": data.get("total_lines")})
        announce_fields.update({"from_line": data.get("from_line"), "to_line": data.get("to_line"),
                                "total_lines": data.get("total_lines")})
    context.record("tool", name="read_file", **record_fields)
    # The digest goes to the row, not the sentence: which *version* the model read is the one thing
    # that explains a write that undid something it could not have seen, and it is what opening a
    # read row answers with.
    context.announce("read_file", **announce_fields)
    return Result(contracts.OK, data)


def _search_code(call: Call, context: ToolContext) -> Result:
    query = call.get("query")
    matches = context.ws.search(query)
    context.record("tool", name="search_code", matches=len(matches))
    context.announce("search_code", query=query, count=len(matches))
    return Result(status=contracts.OK if matches else contracts.EMPTY,
                  data={"matches": matches},
                  advice="" if matches else (
                      "Nothing matched, which is normal for a new project. Propose the files the "
                      "plan calls for instead of blocking: " + refusals.PROPOSE_SHAPE))


def _find_symbol(call: Call, context: ToolContext) -> Result:
    query = call.get("query")
    rows = context.index()
    # One more than the cap, the way `list_files` learns it truncated. An answer that filled 40 and
    # an answer that is 40 arrive identical otherwise, and a small model reads the first one as
    # "this project declares this name 40 times".
    found = symbols.find_symbol(rows, query, limit=symbols.MAX_HITS + 1)
    hits = found[:symbols.MAX_HITS]
    data: dict[str, Any] = {"declarations": hits, "truncated": len(found) > len(hits)}
    if data["truncated"]:
        data["note"] = (f"Only the first {symbols.MAX_HITS} are shown; more "
                        "declarations exist in the repository. Name the file or "
                        "narrow the identifier before reading.")
    if not hits:
        # An empty answer with nothing after it is the shape a small model replies to by asking the
        # same question again. `read_file` does this already via `next_step`.
        data["next_step"] = ("Nothing declares that name, which is an answer: the "
                             "project does not define it. Propose the file the plan "
                             "calls for instead of searching again.")
    context.record("tool", name="find_symbol", query=query, count=len(hits),
                   truncated=data["truncated"])
    context.announce("find_symbol", query=query, count=len(hits))
    return Result(status=contracts.OK if hits else contracts.EMPTY,
                  data=data,
                  advice="" if hits else (
                      "No declaration of that name is in the index, which is an answer: the project "
                      "does not define it. Propose the files the plan calls for instead of blocking: "
                      + refusals.PROPOSE_SHAPE))


def _find_references(call: Call, context: ToolContext) -> Result:
    query = call.get("query")
    rows = context.index()
    found = symbols.find_references(query, context.ws.sources(rows), rows,
                                    limit=symbols.MAX_HITS + 1)
    sites = found[:symbols.MAX_HITS]
    data: dict[str, Any] = {
        "sites": sites,
        "truncated": len(found) > len(sites),
        # The per-file ceiling is a rule the answer always obeys, not something this call can detect
        # after the fact, so it is stated rather than inferred: one file with twenty uses reports
        # six and looks complete.
        "caps": {"total": symbols.MAX_HITS, "per_file": symbols.PER_FILE_LIMIT},
        "summary": {kind: sum(1 for row in sites if row["kind"] == kind)
                    for kind in sorted({row["kind"] for row in sites})},
        "files": sorted({row["path"] for row in sites}),
    }
    if data["truncated"]:
        data["note"] = (f"(truncated at {symbols.MAX_HITS} matches; more references "
                        "exist in the repository)")
    if not sites:
        data["next_step"] = ("No code names it. search_code answers text, including "
                             "configuration and comments; or propose if it is new.")
    context.record("tool", name="find_references", query=query, count=len(sites),
                   truncated=data["truncated"])
    context.announce("find_references", query=query, count=len(sites))
    return Result(status=contracts.OK if sites else contracts.EMPTY,
                  data=data,
                  advice="" if sites else (
                      "Nothing in the indexed code names it. Try search_code for text, or propose: "
                      + refusals.PROPOSE_SHAPE))


def _run_recipe(call: Call, context: ToolContext, *, kind: str) -> Result:
    """The shared half of `run_tests` and `run_build`: choose the fixed recipe, run it on a copy.

    The model names the action and nothing else; `runner.detect` names the command, from the build
    files the project itself carries. That division is the safety property — no model text reaches
    an argv — and it is also the honesty property: the observation can say "no test command exists
    for this project" because the choice was the runtime's, not the model's guess.

    The run happens in a throwaway copy because a build writes, and a tool's contract is that the
    user's tree is untouched by a turn the user never approved. The copy is made by the same
    function the Docker sandbox uses, so the two paths can never drift on what a build does not need.
    """
    name = call.tool
    detected = runner.detect(context.ws.root)
    if kind == "build":
        wanted = [BUILD_RECIPE[item] for item in detected if item in BUILD_RECIPE]
    else:
        wanted = [item for item in detected if item not in set(BUILD_RECIPE.values())]
    if not detected:
        context.record("tool", name=name, status="unavailable")
        context.announce(name, label="")
        return Result(status=contracts.EMPTY,
                      data={"status": "unavailable",
                            "reason": "This project has no recognised build file, so there is no "
                                      "command to run.",
                            "next_step": "Base the answer on reading the files instead."},
                      advice=("A project with no build file is normal, and it is an answer: read the "
                              "files, then propose the change the task needs: "
                              + refusals.PROPOSE_SHAPE))
    if not wanted:
        reason = ("This project has no separate compile step; compiling is part of running "
                  "its tests." if kind == "build"
                  else "No test recipe matches this project's build files.")
        context.record("tool", name=name, status="unavailable")
        context.announce(name, label="")
        return Result(status=contracts.EMPTY,
                      data={"status": "unavailable", "reason": reason,
                            "next_step": "Use run_tests, or base the answer on reading the files."},
                      advice="")
    recipe = wanted[0]
    label = runner.RECIPES[recipe]["label"]
    context.progress("Running " + label + " on a copy of the project ("
                     + str(runner.timeout_for(recipe)) + "s at most)...")
    try:
        with tempfile.TemporaryDirectory(prefix="agent-tool-") as temp:
            runner.copy_for_sandbox(context.ws.root, Path(temp))
            ran = runner.run(Path(temp), recipe, timeout=runner.timeout_for(recipe))
    except (PolicyError, OSError) as exc:
        # The copy refused (project too large to duplicate) or the spawn failed: the command never
        # ran, and "blocked with the reason" is the observation a model can act on.
        context.record("tool", name=name, recipe=recipe, status="blocked")
        context.announce(name, label=label, detail="")
        return Result(status=contracts.EMPTY,
                      data={"recipe": recipe, "label": label, "status": "blocked",
                            "reason": str(exc)[:200],
                            "next_step": "Decide from the files; this check could not run."},
                      advice=("A check that could not run proves nothing about the code. Read the "
                              "files and propose the smallest change: " + refusals.PROPOSE_SHAPE))
    tail = redact(str(ran.get("tail") or ""))[-_evidence_chars(context.settings):]
    failures = [redact(str(item))[:200] for item in (ran.get("failures") or [])[:5]]
    data: dict[str, Any] = {
        "recipe": recipe, "label": label, "command": str(ran.get("command") or ""),
        "status": ran.get("status"), "exit_code": ran.get("exit_code"),
        "seconds": ran.get("seconds"), "tests_observed": bool(ran.get("tests_observed")),
        "failures": failures, "output_tail": tail,
    }
    if data["status"] == "failed":
        data["next_step"] = ("The tail names the failing file and cause; read that file, "
                             "then propose the smallest change that addresses it.")
    elif data["status"] == "unverified":
        data["next_step"] = ("The command exited green but proved no tests ran; do not read "
                             "this as passing.")
    elif data["status"] == "blocked":
        data["next_step"] = "The command timed out and proved nothing; decide from the files."
    context.record("tool", name=name, recipe=recipe, status=data["status"],
                   seconds=data["seconds"])
    context.announce(name, label=label, detail=tail)
    # What the run itself established rides to the continuity record, not only to the history the
    # model reads once: a failing verdict must survive the trim that drops the tail it was read from.
    # Written from the observation — recipe, status, the first failure — never from model prose, and
    # the two outcomes that establish nothing about the code ("could not run", "timed out") are not
    # evidence, so they leave the delta empty rather than teaching the state to say "proved nothing".
    # A suite that failed is still an `ok` result: the call produced its observation, and the
    # failure is *inside* the evidence, which is the one distinction the taxonomy refuses to blur.
    evidence = ""
    if data["status"] == "passed":
        evidence = name + " (" + recipe + "): passed"
    elif data["status"] == "failed":
        evidence = name + " (" + recipe + "): failed" + (" — " + failures[0] if failures else "")
    elif data["status"] == "unverified":
        evidence = name + " (" + recipe + "): exited 0 with no tests observed"
    return Result(status=contracts.OK, data=data,
                  advice="" if data["status"] == "passed" else (
                      "A failing or inconclusive check is evidence about the project, not a task "
                      "failure. Read what the output names, then propose the smallest change: "
                      + refusals.PROPOSE_SHAPE),
                  state={"evidence": [evidence]} if evidence else {})


def _run_tests(call: Call, context: ToolContext) -> Result:
    return _run_recipe(call, context, kind="test")


def _run_build(call: Call, context: ToolContext) -> Result:
    return _run_recipe(call, context, kind="build")


def _git_diff(call: Call, context: ToolContext) -> Result:
    """What is uncommitted right now, read through `git_integration.diff` and nothing else.

    The diff is evidence a turn can reason over — what an earlier task or an outside edit already
    changed — and an empty one is an answer of the same weight: nothing on disk is uncommitted, so
    a change the task needs has not been made yet. Untracked files ride along by name because
    `git diff` has no content for them and a list that stops at tracked files reads as complete.
    """
    found = git_integration.diff(context.ws.root)
    if not found["ok"]:
        context.record("tool", name="git_diff", status="unavailable")
        context.announce("git_diff", detail=str(found.get("reason") or ""))
        return Result(status=contracts.EMPTY,
                      data={"status": "unavailable",
                            "reason": str(found.get("reason") or "git is not available")},
                      advice=("A folder git cannot read is still a workspace: use list_files and "
                              "read_file for evidence, or propose the change the task needs: "
                              + refusals.PROPOSE_SHAPE))
    text = str(found["diff"])
    context.record("tool", name="git_diff", chars=len(text),
                   untracked=len(found["untracked"]))
    context.announce("git_diff", detail=text)
    data: dict[str, Any] = {"status": "ok", "against": found["against"], "diff": text,
                            "truncated": found["truncated"],
                            "untracked": found["untracked"]}
    advice = ""
    if found["truncated"]:
        data["note"] = ("The diff is truncated at " + str(len(text)) + " characters; "
                        "read the files themselves for the rest.")
    if not text.strip() and not found["untracked"]:
        data["next_step"] = ("No uncommitted changes: the folder matches "
                             + str(found["against"]) + ".")
        advice = ("An empty diff is an answer: nothing on disk is uncommitted, so a change "
                  "the task needs has not been made. Propose it: "
                  + refusals.PROPOSE_SHAPE)
        # The cleanliness of the tree is exactly the fact a later turn needs and the observation
        # cannot carry by itself: it is a statement about *this* instant, and the history holding
        # it will be trimmed long before the run's last turn.
        state = {"evidence": ["git_diff: nothing uncommitted against "
                              + str(found["against"])]}
    elif not text.strip():
        data["next_step"] = ("Only untracked files differ, and git diff shows no content for "
                             "them; read a file to see what it holds.")
        state = {"evidence": ["git_diff: nothing tracked differs; only untracked files ("
                              + str(len(found["untracked"])) + ")"]}
    else:
        state = {"evidence": ["git_diff: uncommitted changes present"
                              + (" (" + str(len(found["untracked"])) + " untracked)"
                                 if found["untracked"] else "")]}
    return Result(status=contracts.OK if text.strip() else contracts.EMPTY,
                  data=data, advice=advice, state=state)


# The vocabulary, in the order the prompt introduces it and the refusal repeats it. Each contract
# carries the `policy` class its tool runs under — the checked boundary the registry asks before any
# handler may run — and the rung of the side-effect ladder the doing reaches, which is what a
# `SandboxGuard` measures the run's posture against. No path reaches a command without both having
# judged it. The execution tools reach `execute` because a build writes inside the throwaway copy;
# everything else only looks.
BUILTIN_CONTRACTS: tuple[ToolContract, ...] = (
    ToolContract("list_files", (), policy_action=policy.READ, side_effect=contracts.LEVEL_READ),
    ToolContract("read_file", (Field("path", contracts.PATH),
                               Field("offset", contracts.INTEGER, required=False, minimum=1,
                                     description="first line to return, counted from 1"),
                               Field("limit", contracts.INTEGER, required=False, minimum=1,
                                     maximum=2000, description="how many lines the window carries")),
                 policy_action=policy.READ, side_effect=contracts.LEVEL_READ),
    ToolContract("search_code", (Field("query"),),
                 policy_action=policy.READ, side_effect=contracts.LEVEL_READ),
    ToolContract("find_symbol", (Field("query"),),
                 policy_action=policy.READ, side_effect=contracts.LEVEL_READ),
    ToolContract("find_references", (Field("query"),),
                 policy_action=policy.READ, side_effect=contracts.LEVEL_READ),
    ToolContract("run_tests", (), policy_action=policy.EXECUTE_RECIPE,
                 side_effect=contracts.LEVEL_EXECUTE),
    ToolContract("run_build", (), policy_action=policy.EXECUTE_RECIPE,
                 side_effect=contracts.LEVEL_EXECUTE),
    ToolContract("git_diff", (), policy_action=policy.GIT_LOCAL, side_effect=contracts.LEVEL_READ),
)

_HANDLERS: dict[str, Handler] = {
    "list_files": _list_files,
    "read_file": _read_file,
    "search_code": _search_code,
    "find_symbol": _find_symbol,
    "find_references": _find_references,
    "run_tests": _run_tests,
    "run_build": _run_build,
    "git_diff": _git_diff,
}

# The actions that are fetching, not deciding: a turn whose previous step was one of these only
# needs a well-shaped next envelope, which is the task the optional fast local model is chosen for.
# The execution tools join the gathering five deliberately — choosing to run the project's own
# check is mechanical, and the observation they hand back is evidence, not a decision. Reading the
# shortlist is not planning; proposing, completing and recovering from a refusal are, and those stay
# strong. Derived from the table above rather than restated, so a tool added there is a gathering
# turn by construction and the two lists cannot drift.
GATHER_ACTIONS = frozenset(contract.name for contract in BUILTIN_CONTRACTS)


def default_registry() -> ToolRegistry:
    """The registry `engine.plan` runs: the eight tools, plus the caller's three control actions."""
    return ToolRegistry(((contract, _HANDLERS[contract.name])
                         for contract in BUILTIN_CONTRACTS),
                        control=CONTROL_ACTIONS)
