"""What the agent can do, stated as data before any of it runs.

`tools.py` answers "may this call run" at the moment a turn names it; this module answers the
questions that must be settled earlier and elsewhere — what schemas to hand a provider at all,
whether a model may be offered tools in the first place, what a check is allowed to cost in
seconds and characters, and what the project picker shows beside a model's name. Those questions
need the same tools described in a second, declarative register: a category from the plan's tool
table, a summary, an execution timeout, an output ceiling, an idempotency and concurrency
guarantee, and a JSON Schema a function-calling provider can send verbatim. This module is that
register, and nothing else: no handlers, no execution, no verdicts of its own about a live call —
the live judgement stays `ToolContract.validate` plus `SandboxGuard` plus the policy table, and a
descriptor that contradicted them would be a second authority, which is the failure mode this
whole layer exists to avoid.

The descriptors wrap the contracts; they do not restate them. `BUILTIN_DESCRIPTORS` is built by
walking `tools.BUILTIN_CONTRACTS`, so a tool added to that table without a capability row fails
at import with a sentence naming the missing entry, and a row describing a tool nobody contracted
fails the same way — the registry and the vocabulary cannot drift, because one is derived from the
other. Each descriptor keeps a reference to its contract rather than a copy of its fields, so
`shape()`, `reaches()` and the schema's required list are always the contract's own answer.

`ModelCapabilities` is the other half of what the registry needs to answer, and it is deliberately
a declaration about a model, not a probe of one: whether it has passed tool-calling at all,
whether it honors a requested JSON object, whether it streams, and how large its window is. The
architecture plan's rule that a model which fails tool-calling is restricted to advisory chat is
enforced here as data — `offerable()` returns an empty tuple for such a model, so the question
"which tools go in the request" has one answer in one place, and every provider path, including a
fallback transition rebuilt from persisted state, asks it rather than remembering an older answer.
Defaults are fail-closed: an unknown model, a capability this code cannot read, and a model that
declared nothing all resolve to the advisory row — a missing capability flag is never a grant.

The posture questions read the same two authorities the loop consults at run time rather than
private copies of them: a `policy`-class verdict callable (the folder's rule, defaulting to
`policy.decide`) and a `SandboxGuard` (the run's ceiling, defaulting to `tools.OPEN_GUARD`, the
planning posture). `offerable` is therefore a *pre-flight* of `ToolRegistry.run`, not a gate in
series with it: the call still passes through the registry's own checks when it lands, because
what the folder allows can change between building a request and executing one of its answers,
and a description that let callers skip the real gate would be describing a safety property that
was not there.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from . import contracts, gate, policy, tools
from .contracts import Field, SandboxGuard, ToolContract

__all__ = [
    "CATEGORY_READ", "CATEGORY_WRITE", "CATEGORY_GIT", "CATEGORY_VERIFICATION",
    "CATEGORY_DEPENDENCY", "CATEGORY_SHELL", "CATEGORIES",
    "DEFAULT_TIMEOUT_SECONDS", "DEFAULT_OUTPUT_CHARS",
    "ModelCapabilities", "ADVISORY_CAPABILITIES",
    "ToolDescriptor", "CapabilityRegistry",
    "BUILTIN_CATEGORIES", "BUILTIN_META", "BUILTIN_DESCRIPTORS",
    "default_registry",
]


# The categories of the plan's tool table. They exist because "is this tool safe to offer" is
# asked in coarse classes before it is asked per tool: a read-only tour switches a whole category
# off, a dependency audit wants every tool that touches a build file, and a refusal sentence can
# say "no write tools are offered in this posture" without listing what that excludes. They are
# descriptive, not load-bearing for permission — that is the ladder's and the policy class's job —
# so a tool is classified, never gated, by its category.
CATEGORY_READ = "read"
CATEGORY_WRITE = "write"
CATEGORY_GIT = "git"
CATEGORY_VERIFICATION = "verification"
CATEGORY_DEPENDENCY = "dependency"
CATEGORY_SHELL = "shell"
CATEGORIES = (CATEGORY_READ, CATEGORY_WRITE, CATEGORY_GIT, CATEGORY_VERIFICATION,
              CATEGORY_DEPENDENCY, CATEGORY_SHELL)

# The floor a descriptor must state even when the tool's own class says nothing otherwise. A
# timeout of zero or an unbounded ceiling is how a slow scan becomes an indefinite hang and one
# large observation becomes the whole context; the loop's live budgets still bind tighter, and a
# provider that trusts these numbers is trusting a floor rather than the whole policy.
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_OUTPUT_CHARS = 24000


@dataclass(frozen=True)
class ModelCapabilities:
    """What has been *established* about one model, as the runtime's answer rather than its claim.

    These are test results and measured facts, not the model's marketing sheet: `tool_calling`
    means the model passed the calling contract this runtime will exercise it against, which is
    why a model that has not been exercised reads as advisory-only (the empty default) rather than
    as a tool caller that nobody bothered to check. `context_window_tokens` is the one number here
    the runtime cannot live without and still must not derive from prose — a fallback rebuild has
    to size its budget against the *target's* window before it sends, and a window of zero asks
    nothing, which is what a not-yet-known window should mean until it is probed.
    """

    tool_calling: bool = False
    structured_output: bool = False
    streaming: bool = False
    context_window_tokens: int = 0

    def __post_init__(self) -> None:
        if self.context_window_tokens < 0:
            raise ValueError("A context window cannot be negative")

    @property
    def may_use_tools(self) -> bool:
        """The one question the picker and every request builder ask about a model."""
        return self.tool_calling


# The advisory row: what is known about a model that has told us nothing, which is exactly
# nothing. Named rather than left as `ModelCapabilities()` so a caller that wants to say
# "this model only advises" says the phrase instead of reconstructing four falses.
ADVISORY_CAPABILITIES = ModelCapabilities()


@dataclass(frozen=True)
class ToolDescriptor:
    """One tool as the outside world needs it described: contract plus cost, class and schema.

    The contract reference carries every judgement the descriptor must not make privately:
    `name`, the fields and their kinds, the policy class, and the side-effect rung. What this adds
    is the declarative envelope around that judgement — what a run of the tool may cost
    (`timeout_seconds`), how much of its answer may be shown (`max_output_chars`), and the two
    scheduling guarantees the plan's tool table asks each definition to state: whether repeating
    the call is safe (`idempotent`) and whether it may run beside its siblings
    (`concurrent_reads`). A descriptor that left either guarantee unstated would be answered by
    the scheduler's default, and the default has to be the cautious one, so stating them here is
    how a tool opts *out* of caution on the record.
    """

    contract: ToolContract
    category: str
    summary: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_output_chars: int = DEFAULT_OUTPUT_CHARS
    idempotent: bool = True
    concurrent_reads: bool = True

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError("Unknown tool category: " + str(self.category))
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive for " + self.contract.name)
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars must be positive for " + self.contract.name)
        if not self.summary:
            raise ValueError("A descriptor must say what its tool does: " + self.contract.name)

    @property
    def name(self) -> str:
        return self.contract.name

    @property
    def side_effect(self) -> str:
        return self.contract.side_effect

    @property
    def policy_action(self) -> str:
        return self.contract.policy_action

    def reaches(self, level: str) -> bool:
        """Whether this tool's doing is at least as invasive as `level` — the contract's answer."""
        return self.contract.reaches(level)

    def shape(self) -> str:
        return self.contract.shape()

    def field_schemas(self) -> dict[str, dict[str, Any]]:
        """The contract's fields, spelled in the one JSON Schema vocabulary this agent uses.

        Derived from `Field` rather than restated per tool, because the kinds and bounds a call is
        judged against are the contract's, and a schema that drifted from them would advertise a
        call the validator would refuse — the model would then spend its turn being told the
        grammar is different from the one it was handed. `PATH` is only `string` plus a wording of
        the confinement the contract itself enforces.
        """
        out: dict[str, dict[str, Any]] = {}
        for f in self.contract.fields:
            out[f.name] = _field_schema(f)
        return out

    def json_schema(self) -> dict[str, Any]:
        """The tool in the dialect a function-calling provider takes verbatim."""
        properties = self.field_schemas()
        properties.update({key: {"type": "string"} for key in contracts.TRACE_KEYS})
        return {
            "type": "function",
            "function": {
                "name": self.contract.name,
                "description": self.summary,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": [f.name for f in self.contract.fields if f.required],
                    "additionalProperties": False,
                },
            },
        }

    def summary_row(self) -> dict[str, Any]:
        """The whole descriptor as plain data, for the picker and the report.

        Every field here is one the plan says the selection interface shows beside a model's name:
        what the tool is, its class, what it will be allowed to do, and what it will cost. The
        shapes of the refusals are built from the same rows, so a display and a refusal can never
        quote two different menus.
        """
        return {
            "name": self.contract.name,
            "category": self.category,
            "summary": self.summary,
            "shape": self.contract.shape(),
            "policy_action": self.contract.policy_action,
            "side_effect": self.contract.side_effect,
            "timeout_seconds": self.timeout_seconds,
            "max_output_chars": self.max_output_chars,
            "idempotent": self.idempotent,
            "concurrent_reads": self.concurrent_reads,
        }


# Field kind -> the JSON Schema it spells to. INTEGER rather than NUMBER for the same reason the
# contract insists on it: every count in this vocabulary is whole, and a schema that admitted 2.5
# into a `limit` would be handing the provider a grammar whose calls the runtime refuses.
def _field_schema(f: Field) -> dict[str, Any]:
    if f.kind == contracts.STRING_LIST:
        schema: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
        if f.min_items is not None:
            schema["minItems"] = f.min_items
        if f.max_items is not None:
            schema["maxItems"] = f.max_items
    elif f.kind == contracts.INTEGER:
        schema = {"type": "integer"}
        if f.minimum is not None:
            schema["minimum"] = f.minimum
        if f.maximum is not None:
            schema["maximum"] = f.maximum
    elif f.kind == contracts.BOOLEAN:
        schema = {"type": "boolean"}
    else:
        schema = {"type": "string"}
    if f.values:
        schema["enum"] = list(f.values)
    description = f.description or (
        "Workspace-relative path; no absolute paths and no '..'."
        if f.kind == contracts.PATH else "")
    if description:
        schema["description"] = description
    return schema


class CapabilityRegistry:
    """The declared capability surface: every descriptor, answerable by name, category or posture.

    The tool registry in `tools.py` is a dispatcher — it holds contracts paired with handlers and
    runs them; this registry holds only descriptions and is consulted before a request is built
    and after it is served, when something needs to enumerate what *could* run rather than let
    something run. That is why registration rejects a shadowed name exactly as the dispatcher
    does: two menus for one vocabulary is the drift, and both registries have to refuse it the
    same way. Order, as there, is registration order, because the enumeration this registry serves
    is a list someone reads.
    """

    def __init__(self, descriptors: Iterable[ToolDescriptor] = ()):
        self._by_name: dict[str, ToolDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: ToolDescriptor) -> None:
        if descriptor.name in self._by_name:
            raise ValueError("Capability already registered: " + descriptor.name)
        self._by_name[descriptor.name] = descriptor

    @property
    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(self._by_name.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def get(self, name: str) -> ToolDescriptor:
        """The descriptor for one tool, or a refusal naming what *is* registered.

        An unknown name raises rather than returns None because every caller of this method is
        building something a model or a user will rely on — a provider schema, a report row — and
        an absent descriptor would silently shrink that surface instead of saying the pair has
        drifted.
        """
        found = self._by_name.get(name)
        if found is None:
            raise KeyError("No descriptor registered for: " + str(name)
                           + ". Registered: " + (", ".join(self._by_name) or "none"))
        return found

    def by_category(self, category: str) -> tuple[ToolDescriptor, ...]:
        if category not in CATEGORIES:
            raise ValueError("Unknown tool category: " + str(category))
        return tuple(d for d in self._by_name.values() if d.category == category)

    def json_schemas(self, descriptors: Iterable[ToolDescriptor] | None = None) -> list[dict]:
        """The provider payload's tool list, in registration order, for the given subset or all.

        Schemas are minted here rather than stored because a stored schema is a second copy of the
        contract's judgement — the drift this module's whole design refuses — and this call is
        cheap enough that computing from the live contracts costs a provider request only
        a dict-build.
        """
        source = self.descriptors if descriptors is None else descriptors
        return [d.json_schema() for d in source]

    def offerable(self, model: ModelCapabilities,
                  guard: SandboxGuard | None = None,
                  verdict: Callable[[str], str] = policy.decide,
                  security_gate: gate.SecurityGate | None = None
                  ) -> tuple[ToolDescriptor, ...]:
        """The tools this model, in this posture, on this folder's word, may be offered.

        Two questions, in the order their refusals are most worth stating. First the model's own: a
        model that has not passed tool-calling is offered nothing and advises instead — the plan's
        advisory rule, applied at the point the request is built, which is the only point where
        offering it a tool costs anything. Then the gate's, once per contract through
        `SecurityGate.allows`: the rung's ceiling, the folder's verdict for every class the call
        belongs to, and what the contract's provenance says about its own declarations. Asking the
        gate rather than repeating its rules here is the point — a pre-flight that carried its own
        copy of the judgement would be a second authority, and the drift between two authorities is
        the bug that lets a surface advertise a class the loop then refuses, spending the run's turns
        discovering a rule it was shown.

        The result is still a pre-flight, not a pardon: whatever lands in `guard` and `verdict`, the
        dispatcher re-checks the live call, and an offer valid when the request was built can still be
        refused when the call arrives.
        """
        if not model.may_use_tools:
            return ()
        live_guard = guard or tools.OPEN_GUARD
        admission = security_gate or gate.NATIVE_GATE
        return tuple(d for d in self._by_name.values()
                     if admission.allows(d.contract, verdict=verdict, guard=live_guard))

    def describe(self, model: ModelCapabilities | None = None,
                 guard: SandboxGuard | None = None,
                 verdict: Callable[[str], str] = policy.decide) -> dict[str, Any]:
        """One plain row for the picker: the whole surface, and what a given model may use of it.

        `offered` is always the same list `offerable` would return for these arguments, and the
        per-tool rows are the descriptors' own — the two views of the menu come from one build, so
        the sentence the user reads is the sentence the request will act on.
        """
        model = model or ADVISORY_CAPABILITIES
        offered = self.offerable(model, guard, verdict)
        return {
            "tools": [d.summary_row() for d in self.descriptors],
            "model": {
                "tool_calling": model.tool_calling,
                "structured_output": model.structured_output,
                "streaming": model.streaming,
                "context_window_tokens": model.context_window_tokens,
            },
            "offered": [d.name for d in offered],
            "mode": "tools" if offered else "advisory",
        }


# The category of every built-in planning tool, in the plan's own classes. The gathering five are
# reads; the two recipe runners are verification, not shell — the distinction the table draws
# because verification runs a project's own fixed command and shell would run the model's; and
# `git_diff` is git metadata, which the plan separates from reading files because a repository's
# history is a second view of the same folder with its own failure modes.
BUILTIN_CATEGORIES: dict[str, str] = {
    "list_files": CATEGORY_READ,
    "read_file": CATEGORY_READ,
    "search_code": CATEGORY_READ,
    "find_symbol": CATEGORY_READ,
    "find_references": CATEGORY_READ,
    "run_tests": CATEGORY_VERIFICATION,
    "run_build": CATEGORY_VERIFICATION,
    "git_diff": CATEGORY_GIT,
}

# The per-tool declarative envelope. Timeouts state what a *complete* honest run should cost:
# the recipe runners carry the plan's own per-step allowance, because a build is the one tool the
# loop cannot cut short by returning early; the reads carry seconds, and a read that needs minutes
# is a project the loop should find out about rather than wait inside. The output ceilings state
# the showable amount, which is a display and transport bound — the loop's live context budgets
# still bind tighter at run time, and the smaller of the two is what the observation actually
# respects. Idempotency and concurrency are stated per tool: the recipe runners do not claim them
# because a build writes (inside the copy, but the copy's state is not guaranteed between two
# runs), while every read tool claims both, which is the plan's "reads may execute concurrently
# under snapshot consistency" row, opted into on the record.
BUILTIN_META: dict[str, dict[str, Any]] = {
    "list_files": {"timeout_seconds": 10, "max_output_chars": 20000,
                   "summary": "List the workspace's file paths, newest inventory first, "
                              "with a truncation flag when the tree is larger than the page."},
    "read_file": {"timeout_seconds": 10, "max_output_chars": 100000,
                  "summary": "Read one workspace-relative file, whole or as an offset/limit line "
                             "window, recording its digest so a later write can be checked against "
                             "the version this answer showed."},
    "search_code": {"timeout_seconds": 30, "max_output_chars": 20000,
                    "summary": "Text search across the workspace, including configuration and "
                               "comments — the widest net, and the one that answers for names no "
                               "declaration carries."},
    "find_symbol": {"timeout_seconds": 30, "max_output_chars": 20000,
                    "summary": "Find declarations (functions, classes, methods) matching a name "
                               "in the parsed index, with truncation stated rather than implied."},
    "find_references": {"timeout_seconds": 60, "max_output_chars": 20000,
                        "summary": "Find code sites that use a name, per-file and total, capped "
                                   "and summarized so a wide use is visible as wide."},
    "run_tests": {"timeout_seconds": 1800, "max_output_chars": 8000,
                  "idempotent": False, "concurrent_reads": False,
                  "summary": "Run the project's own test recipe on a throwaway copy and return "
                             "its verdict, exit code, and the failing evidence."},
    "run_build": {"timeout_seconds": 1800, "max_output_chars": 8000,
                  "idempotent": False, "concurrent_reads": False,
                  "summary": "Compile-only counterpart of the test recipe where the ecosystem "
                             "has a separate build verb; says so where it does not."},
    "git_diff": {"timeout_seconds": 30, "max_output_chars": 20000,
                 "summary": "The uncommitted state of the repository — tracked diff and untracked "
                            "names — read-only metadata, never an action on history."},
}


def _builtin_descriptors() -> tuple[ToolDescriptor, ...]:
    """The descriptors, walked off the dispatcher's own table — so neither can exist without the other.

    Two drifts are refused at import, in both directions: a contract with no capability row names
    itself in the error, because a tool that runs but cannot be described is invisible to every
    picker and provider in the system; a row naming no contract is a phantom, and the message says
    the whole registered vocabulary because the fix is to delete the row or add the tool, and
    guessing which is the caller's decision.
    """
    by_name = {c.name: c for c in tools.BUILTIN_CONTRACTS}
    missing = sorted(set(by_name) - set(BUILTIN_META))
    if missing:
        raise ValueError("Tool contracts without capability descriptors: " + ", ".join(missing))
    phantom = sorted(set(BUILTIN_META) - set(by_name))
    if phantom:
        raise ValueError("Capability descriptors with no registered contract: " + ", ".join(phantom)
                         + ". Contracts: " + (", ".join(sorted(by_name)) or "none"))
    out: list[ToolDescriptor] = []
    for contract in tools.BUILTIN_CONTRACTS:
        meta = BUILTIN_META[contract.name]
        out.append(ToolDescriptor(
            contract=contract,
            category=BUILTIN_CATEGORIES[contract.name],
            summary=meta["summary"],
            timeout_seconds=meta.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            max_output_chars=meta.get("max_output_chars", DEFAULT_OUTPUT_CHARS),
            idempotent=meta.get("idempotent", True),
            concurrent_reads=meta.get("concurrent_reads", True),
        ))
    return tuple(out)


BUILTIN_DESCRIPTORS: tuple[ToolDescriptor, ...] = _builtin_descriptors()


def default_registry() -> CapabilityRegistry:
    """The capability surface the planning loop actually has: its eight tools, described."""
    return CapabilityRegistry(BUILTIN_DESCRIPTORS)
