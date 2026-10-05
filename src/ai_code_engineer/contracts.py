"""The typed contract layer: what a call must look like, what a result promises, and which
failures a caller may act on.

`engine.plan` judges a turn through this layer: every tool in `tools.py` is a `ToolContract`, and
the registry validates, gates, and stamps through the types defined here. A bare tuple of field
names and one undifferentiated refusal were enough for the planning loop they served; a component
that must decide — before anything runs — whether a call is legal, whether a failure is worth
retrying, and what to tell the caller who sent it, needs vocabulary the loop's code does not
carry: a field that says its own type and its own allowed values, a result that says its own
status rather than leaving it to be inferred from whether `data` came back empty, and an error
that says its own class. This module is that vocabulary, and nothing else: no handler, no
execution, no policy table lookups.

Exactness is the whole point of `ToolContract.validate`: a call may carry `action` plus exactly
the fields the contract names, optional ones included when supplied, and a name no contract
claims for itself. A hopeful extra key is refused by name, not silently dropped, because a caller
that believes it set `limit` and was ignored will reason from a limit that never existed. The three
dialects a caller wraps its asking in — nested `args`/parameters, the `reason`/`thought`
sentence, and the working state written flat beside the action — are settled by the helpers here
(`unwrap_args`, `pop_rationale`, `pop_task_state`) before the judgement rather than refused by it,
and the loop's own control envelopes are made canonical by the same calls, so the tools and the
caller can never drift on what a dialect means.

The error taxonomy answers one question that a bare exception cannot: may this be tried again?
`ExecutionFailed` says yes — the tool ran and the world was not ready — while `MalformedCall`,
`UnknownTool` and `PolicyDenied` say no, and saying no is the part that matters: a retry of a
refused shape fails identically forever, and a retry of a denied class spends the run's time on
a door the folder closed. Every taxonomy error carries the sentence its raiser will want, too,
because a refusal a model cannot understand is a refusal it will re-make, and the code plus the
message is what lets a UI colour the row without matching strings.

`Result` is deliberately a closed three-way status rather than a flag pile. `ok` means the
observation holds; `empty` means the absence of content is itself the answer, which is the
difference between "keep going" and "the task's premise is wrong"; `failed` means the attempt
told us something is broken and carries the taxonomy code for what. Anything a caller might
want to know beyond that rides in `data`, which this layer never inspects — the shape of an
observation belongs to the tool that produces it. `state` is the one part of a result the layer
does spell out: the continuity delta a tool's own run established, written from the observation
and never from the caller's prose, so a fact like a suite's verdict survives the history that
carried the full output.

The side-effect ladder and `SandboxGuard` settle the question a shape judgement cannot: not
"is this call well-formed" but "should anything here run at all". A contract declaring its
`side_effect` level and a guard declaring a ceiling let the runtime refuse an overreaching tool
before its handler is ever reached, and refuse it in the same non-retryable class the folder's
policy already uses — a sandbox denial, like a policy denial, is a verdict about the world's
shape and not a transient state. `trace_id` and `call_id` ride the same envelope so a refusal,
an execution and the result handed back can all be pointed at by the same line in a log, and
the guard re-checks path fields it never needed to trust the first time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_code_engineer.errors import AgentError

__all__ = [
    "STRING", "INTEGER", "BOOLEAN", "STRING_LIST", "PATH",
    "KINDS",
    "OPTIONAL", "ARG_WRAPPERS", "RATIONALE_KEYS", "TRACE_KEYS", "TASK_STATE_KEYS",
    "OK", "EMPTY", "FAILED",
    "LEVEL_NONE", "LEVEL_READ", "LEVEL_WRITE", "LEVEL_EXECUTE", "LEVEL_NETWORK",
    "SIDE_EFFECT_LEVELS",
    "MALFORMED_CALL", "UNKNOWN_TOOL", "POLICY_DENIED", "SANDBOX_DENIED",
    "EXECUTION_FAILED", "TIMEOUT",
    "RETRYABLE_CODES",
    "ContractError", "MalformedCall", "UnknownTool", "PolicyDenied", "SandboxDenied",
    "ExecutionFailed", "Timeout", "is_retryable",
    "action_shape", "unwrap_args", "pop_rationale", "pop_task_state",
    "Field", "Call", "Result", "ToolContract", "SandboxGuard", "menu",
]


# The kinds a field may declare. INTEGER rather than NUMBER because every count in this agent's
# vocabulary — limits, line numbers, byte ceilings — is whole, and accepting 2.5 into one of those
# is a bug the type should refuse before the handler ever sees it.
STRING, INTEGER, BOOLEAN, STRING_LIST, PATH = "string", "integer", "boolean", "string_list", "path"
KINDS = (STRING, INTEGER, BOOLEAN, STRING_LIST, PATH)

# A required field is the default, spelled out as a constant so a contract reads as intent.
OPTIONAL = False

# The dialects a caller may wrap its arguments in, and the keys the explaining sentence arrives
# under. Both come from the function-calling dialect nearly every model is tuned on, so
# `{"action": "read_file", "args": {"path": "app.py"}}` and a `blocked` that says `thought` arrive
# in practice and mean exactly what the flat envelope the prompt shows means.
ARG_WRAPPERS = ("args", "parameters")
RATIONALE_KEYS = ("reason", "thought")

# The trace identifiers an envelope may carry. They are judged for type but never for content:
# this layer does not mint ids and does not know what an id means, it only refuses to transport
# one that is not a string, because a non-string id silently becomes "None" in every log line
# that later tries to correlate on it.
TRACE_KEYS = ("trace_id", "call_id")

# The working-state fields the loop keeps for the run, spelled the way `taskstate.SECTION_ORDER`
# writes them out. The prompt asks for them nested in one optional `state` object; a model that
# has been reasoning over them all turn reaches for them flat, next to `action`, and writes
# `{"action": "list_files", "acceptance": [...], "status": "..."}`. That is the same dialect drift
# `args` and `reason` are: the notes the model keeps for itself, not arguments the tool was asked
# for. They leave the envelope before the exact-field judgement, and `engine.plan` folds what
# arrives into the run's state, so a scratchpad said flat is remembered rather than refused.
TASK_STATE_KEYS = ("status", "constraints", "decisions", "evidence", "open_issues", "next_step",
                   "files_examined", "folded", "acceptance", "goal")

OK, EMPTY, FAILED = "ok", "empty", "failed"
STATUSES = (OK, EMPTY, FAILED)

# The side-effect ladder, ordered from the least to the most invasive, mirroring the classes
# `policy.py` gates on. A contract declares the rung its tool's doing reaches; a guard declares
# the rung its posture allows. Index order is the only comparison either needs, which is why
# the ladder is a tuple and not a set of flags — "may this write" and "may this execute" are
# never independent answers on a real folder.
LEVEL_NONE, LEVEL_READ, LEVEL_WRITE, LEVEL_EXECUTE, LEVEL_NETWORK = \
    "none", "read", "write", "execute", "network"
SIDE_EFFECT_LEVELS = (LEVEL_NONE, LEVEL_READ, LEVEL_WRITE, LEVEL_EXECUTE, LEVEL_NETWORK)

# The taxonomy, as codes rather than classes alone: a `Result` can carry the code without carrying
# an exception across a boundary that was never meant to hold one.
MALFORMED_CALL, UNKNOWN_TOOL, POLICY_DENIED = "malformed_call", "unknown_tool", "policy_denied"
SANDBOX_DENIED = "sandbox_denied"
EXECUTION_FAILED, TIMEOUT = "execution_failed", "timeout"

# The only codes a retry could help. Everything else fails identically forever, and a loop that
# cannot tell the two apart converts one mistake into a run spent re-making it.
RETRYABLE_CODES = frozenset({EXECUTION_FAILED, TIMEOUT})


class ContractError(AgentError):
    """A failure this layer can name, in the taxonomy, with the sentence to say about it.

    `advice` is addressed to whoever sent the call and is safe to show them: it names codes,
    fields and shapes, and carries no project content. `code` is what a caller branches on, so no
    consumer ever has to match this error's prose to decide whether to try again.
    """

    code = ""
    advice = ""

    def __init__(self, message: str = "", advice: str = ""):
        super().__init__(message)
        if advice:
            self.advice = advice

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


class MalformedCall(ContractError):
    """The envelope itself is wrong: a missing field, an unknown one, a value of the wrong kind.

    Nothing ran and nothing was looked at, so the fix belongs entirely to the caller: `advice`
    names the offending part and the shape that would have been accepted.
    """

    code = MALFORMED_CALL
    advice = "Fix the call's shape and send it again; retrying it unchanged will fail unchanged."


class UnknownTool(ContractError):
    """The call names a tool no contract in the menu claims.

    Refused as its own class rather than as a `MalformedCall` because the caller's mistake is a
    different one: the envelope may be perfectly formed around a name that does not exist, and
    the only useful advice is the menu itself.
    """

    code = UNKNOWN_TOOL
    advice = "Choose one of the named tools; the menu is the whole vocabulary."


class PolicyDenied(ContractError):
    """The call is well-formed and the name is real; the workspace's rule said no.

    Explicitly not retryable, in any posture, at any depth: the verdict belongs to the folder and
    a caller that re-asks is not testing anything, it is hoping the rule changed mid-run.
    """

    code = POLICY_DENIED
    advice = "Do not retry this call; the refusal is the workspace's rule, not a transient state."


class SandboxDenied(PolicyDenied):
    """The call is well-formed and permitted; the execution posture the run was given is not.

    A subclass rather than a sibling because the caller's action is identical — find a different
    call, not a different moment — and the one place that wants the distinction is the reader who
    needs to know the ceiling came from the sandbox, not from the folder's own rule.
    """

    code = SANDBOX_DENIED
    advice = ("Do not retry this call; its side-effect level or paths exceed what this sandbox "
              "allows. Ask for a call that stays inside the sandbox instead.")


class ExecutionFailed(ContractError):
    """The call passed every check and the attempt itself failed.

    Retryable by class — a build that failed for the state it found may pass for the state after
    a fix — but each caller still decides whether *this* run should spend the retry, which is why
    this is a flag on the error and not a loop in this module.
    """

    code = EXECUTION_FAILED
    advice = "The attempt failed on its own terms; retry only if something changed since."


class Timeout(ExecutionFailed):
    """The attempt was still owed an answer when its deadline closed.

    A subclass rather than a new code because the caller's decision is the same as for any failed
    execution, and the one place that wants the distinction — a message saying "it was working,
    just slowly" — already has the class to look at.
    """

    code = TIMEOUT
    advice = "The attempt ran out of time; retry it, or ask for a smaller slice."


def is_retryable(error: BaseException) -> bool:
    """Whether sending the same call again could possibly land differently.

    A non-taxonomy error is not retryable by default: an unexpected exception is a bug somewhere,
    and bugs do not fix themselves between one attempt and the next.
    """
    return isinstance(error, ContractError) and error.retryable


def action_shape(action: dict) -> str:
    """One request described by its *shape*: which action it names and the field names it
    carries — never their values. Raw replies are deliberately not recorded anywhere in a session;
    field names are the part of a refusal that can be echoed back, and kept in the history, without
    turning the record into a copy of the model's output. `unwrap_args` has already required a JSON
    object with string keys by the time a refusal is built from this."""
    return ("action " + str(action.get("action")) + " with fields "
            + (", ".join(sorted(action)) or "no fields"))


def unwrap_args(action: dict) -> dict:
    """The same envelope with any nested `args`/`parameters` object lifted into it.

    A wrapper is a dialect, not a second contract: the exact field sets the contracts are judged
    against stay the only ones, and an envelope that nested nothing comes back as it was. A field
    given both inside the wrapper and outside it is refused instead of guessed at — unless the two
    agree, because a model repeating itself is not a contradiction and refusing it costs a turn to
    settle nothing. A wrapper that is not an object is left in place, where the shape refusal can
    name it.
    """
    if not isinstance(action, dict):
        return action
    merged = dict(action)
    for wrapper in ARG_WRAPPERS:
        nested = merged.get(wrapper)
        if not isinstance(nested, dict):
            continue
        del merged[wrapper]
        clash = sorted(name for name in nested if name in merged and nested[name] != merged[name])
        if clash:
            raise MalformedCall("Contradictory envelope: " + action_shape(action) + " gives "
                                + ", ".join(clash) + " both in the envelope and inside " + wrapper
                                + ", with two different values. Send each field once.")
        merged.update(nested)
    return merged


def pop_rationale(action: dict) -> str:
    """Take the explanation out of the envelope and hand it back.

    Out, rather than read where it lies: an explanation is no part of any contract, so it leaves the
    envelope the way the trace ids do — before the strict field checks see it — and a call that
    carried one is then judged on the asking it actually made. `blocked` is the only control action
    that wants to know what the sentence says; a tool ignores it, and nothing records it, because it
    is model prose and a session keeps no raw replies. Both names are read, `reason` first, since a
    reasoning model reaches for `thought` where the prompt asked for `reason` and the loop has no
    preference which one the deliberation arrived in.
    """
    if not isinstance(action, dict):
        return ""
    said = ""
    for key in RATIONALE_KEYS:
        value = action.pop(key, None)
        if not said and isinstance(value, str):
            said = value
    return said


def pop_task_state(action: dict) -> dict:
    """Take the working state out of the envelope and hand it back, as `merge` would receive it.

    Flat, in the same sentence as the asking, is the dialect a model that has been reasoning over
    these fields all turn writes them in; nested under `state` is the dialect the prompt asked for,
    and `engine.plan` reads that one where it lies. Either way the fields are continuity, never a
    tool's arguments, so they leave the envelope before the strict field checks see it — and what
    left comes back as a delta, because the note a model made while deciding its next call is the
    note the run must not lose. A value of the wrong shape is passed through untouched: `merge`
    skips what it cannot use and this layer does not judge prose it does not own.
    """
    if not isinstance(action, dict):
        return {}
    return {key: action.pop(key) for key in TASK_STATE_KEYS if key in action}


@dataclass(frozen=True)
class Field:
    """One argument a contract accepts, typed, bounded and addressed.

    `values` restricts a string to an enumerated choice, and `PATH` says the string is a
    workspace-relative path — both are checked here, at the envelope, because a value that gets
    past validation is a value a handler must then be written to survive, and the point of a
    contract is that handlers are not.

    `minimum`/`maximum` bound an INTEGER and `min_items`/`max_items` bound a STRING_LIST, and
    neither bound is a courtesy to the handler: an unbounded `limit` is how one sloppy call reads
    the whole workspace, and a bounds check that lives in the contract cannot be forgotten by the
    hundredth handler that someone writes in a hurry.
    """

    name: str
    kind: str = STRING
    required: bool = True
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError("Unknown field kind: " + str(self.kind))
        if self.values and self.kind not in (STRING, PATH):
            raise ValueError("Only string and path fields can enumerate values: " + self.name)
        if (self.minimum is not None or self.maximum is not None) and self.kind != INTEGER:
            raise ValueError("Only integer fields can carry numeric bounds: " + self.name)
        if (self.min_items is not None or self.max_items is not None) \
                and self.kind != STRING_LIST:
            raise ValueError("Only string-list fields can carry size bounds: " + self.name)
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Bounds are inverted for field: " + self.name)
        if self.min_items is not None and self.max_items is not None \
                and self.min_items > self.max_items:
            raise ValueError("Size bounds are inverted for field: " + self.name)

    def check(self, value: Any) -> str:
        """The complaint against one supplied value, or "" when the value is accepted.

        The complaint names the value's type rather than echoing the value: a wrong-typed `path`
        can carry project content, and advice is the one channel this layer promises is safe to
        show and to record. Bound complaints echo numbers freely, because a count is never
        project content — it is the one kind of value this layer may quote back unchanged.
        """
        if self.kind == STRING or self.kind == PATH:
            if not isinstance(value, str):
                return self._complain("a string", value)
            if self.values and value not in self.values:
                return (self.name + " must be one of: " + ", ".join(self.values)
                        + " (got a value outside that list)")
            if self.kind == PATH and (value.startswith("/") or ".." in value.split("/")):
                return self.name + " must be a workspace-relative path without '..' " \
                    "(absolute paths and parent traversals are refused)"
        elif self.kind == INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                return self._complain("a whole number", value)
            if self.minimum is not None and value < self.minimum:
                return self.name + " must be at least " + str(self.minimum) + " (got " \
                    + str(value) + ")"
            if self.maximum is not None and value > self.maximum:
                return self.name + " must be at most " + str(self.maximum) + " (got " \
                    + str(value) + ")"
        elif self.kind == BOOLEAN:
            if not isinstance(value, bool):
                return self._complain("true or false", value)
        elif self.kind == STRING_LIST:
            if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
                return self._complain("a list of strings", value)
            if self.min_items is not None and len(value) < self.min_items:
                return self.name + " must carry at least " + str(self.min_items) \
                    + " entries (got " + str(len(value)) + ")"
            if self.max_items is not None and len(value) > self.max_items:
                return self.name + " must carry at most " + str(self.max_items) \
                    + " entries (got " + str(len(value)) + ")"
        return ""

    def _complain(self, expected: str, value: Any) -> str:
        return self.name + " must be " + expected + " (got " + type(value).__name__ + ")"

    def shape(self) -> str:
        """The one-line spelling this field deserves in a refusal sentence.

        Bounds are spelled out because a refusal that teaches only the shape without the bound
        produces a corrected call that is refused again for the same bound, and a caller that is
        refused twice for one mistake stops trusting the refusals.
        """
        if self.values:
            return self.name + "=" + "|".join(self.values)
        span = ""
        if self.kind == INTEGER and (self.minimum is not None or self.maximum is not None):
            span = "[" + (".." if self.minimum is None else str(self.minimum)) + ".." \
                + (".." if self.maximum is None else str(self.maximum)) + "]"
        elif self.kind == STRING_LIST and (self.min_items is not None
                                           or self.max_items is not None):
            span = "[" + (".." if self.min_items is None else str(self.min_items)) + ".." \
                + (".." if self.max_items is None else str(self.max_items)) + " items]"
        if not self.required:
            return "[" + self.name + span + "]"
        return self.name + span


@dataclass(frozen=True)
class Call:
    """A validated request: the tool named, its canonical arguments, the sender's sentence,
    and the trace identifiers it arrived under.

    Built only by `ToolContract.validate`, so holding one is proof the envelope was exact — the
    rationale is the one part of the original request a contract does not judge, because it
    explains rather than acts, and it travels on so the audit record keeps the reason beside
    the action. The ids ride along for the same reason: the guard's refusal, the handler's run,
    and the `Result` handed back are one event, and without the ids carried through the layer
    that produces each of the three, a log cannot say so.
    """

    tool: str
    args: dict[str, Any]
    rationale: str = ""
    trace_id: str = ""
    call_id: str = ""

    def get(self, name: str, default: Any = None) -> Any:
        return self.args.get(name, default)

    def traced(self, result: "Result") -> "Result":
        """Hand `result` back stamped with this call's ids, so a log line for either finds the other."""
        return Result(result.status, dict(result.data), result.message, result.error_code,
                      result.advice, self.trace_id or result.trace_id,
                      self.call_id or result.call_id, dict(result.state))


@dataclass(frozen=True)
class Result:
    """What one satisfied call hands back, with its own status stated rather than inferred.

    `error_code` is the taxonomy's answer to "why did this fail", and it belongs on the result
    because a result travels boundaries — a queue, a log, a wire — where the exception object
    that produced it cannot. `advice` rides with it for the same reason `ContractError.advice`
    does: the reader of a failed row is often the author of the next call. The trace ids make
    the row findable by the call that produced it; `Call.traced` is the usual way they land.
    `state` is a `merge`-shaped continuity fragment — what the run itself established, written
    from the observation and never from caller prose — so the fact outlives the history that
    carried the full output.
    """

    status: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error_code: str = ""
    advice: str = ""
    trace_id: str = ""
    call_id: str = ""
    state: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError("Unknown result status: " + str(self.status))
        if (self.status == FAILED) != bool(self.error_code):
            raise ValueError("A failed result must carry an error code, and no other status may")

    @classmethod
    def ok(cls, data: dict[str, Any] | None = None, message: str = "") -> "Result":
        return cls(OK, data or {}, message)

    @classmethod
    def empty(cls, message: str = "", data: dict[str, Any] | None = None) -> "Result":
        return cls(EMPTY, data or {}, message)

    @classmethod
    def fail(cls, error: ContractError, call: "Call | None" = None) -> "Result":
        result = cls(FAILED, {}, str(error), error.code, error.advice)
        return call.traced(result) if call is not None else result

    @property
    def succeeded(self) -> bool:
        return self.status in (OK, EMPTY)

    @property
    def retryable(self) -> bool:
        return self.status == FAILED and self.error_code in RETRYABLE_CODES


@dataclass(frozen=True)
class ToolContract:
    """One tool's whole external promise: its name, its fields, the class of what it does, and
    the rung of the side-effect ladder it reaches.

    `policy_action` names the workspace rule class this tool's doing falls under — the value the
    registry hands the loop's verdict before a handler may run — and an empty one means the tool
    asks for nothing the gate is consulted about, which is a registration decision rather than a
    model-visible one.
    `side_effect` is the sandbox's view of the same doing: coarser than the policy classes and
    mandatory, because a guard that had to be told about every new policy action would be a guard
    quietly bypassed the day one is added.
    """

    name: str
    fields: tuple[Field, ...] = ()
    purpose: str = ""
    policy_action: str = ""
    side_effect: str = LEVEL_NONE

    def __post_init__(self) -> None:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate field names in contract: " + self.name)
        if self.side_effect not in SIDE_EFFECT_LEVELS:
            raise ValueError("Unknown side-effect level: " + str(self.side_effect))

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)

    def reaches(self, level: str) -> bool:
        """Whether this tool's doing is at least as invasive as `level`."""
        if level not in SIDE_EFFECT_LEVELS:
            raise ValueError("Unknown side-effect level: " + str(level))
        return SIDE_EFFECT_LEVELS.index(self.side_effect) >= SIDE_EFFECT_LEVELS.index(level)

    def shape(self) -> str:
        """The spelling a refusal echoes back."""
        return self.name + "(" + ", ".join(f.shape() for f in self.fields) + ")"

    def validate(self, envelope: dict[str, Any]) -> Call:
        """Judge one request against this contract and return the canonical `Call`.

        The unwrapping and the rationale come out through the same helpers the loop uses for its
        control envelopes, so a nested `args` dict is answered as the same call in the flat dialect
        rather than refused for its dress, and a field said twice with two values is refused rather
        than picked between. The working state leaves the same way — `pop_task_state` takes it, and
        the loop is the layer that owns it, so what reaches the field checks is the asking and
        nothing else. The trace ids come out the same way — carried, never judged beyond
        their type, and never counted as unclaimed keys — because an id belongs to whoever sent the
        call, not to the contract. The field set must be exact — every required one present, no
        unclaimed ones — because a silently dropped key teaches the caller a lie about what it
        asked for. `MalformedCall` carries the offending name and this contract's shape in the same
        sentence, which is all a caller needs to fix it without being handed the whole menu.
        """
        if not isinstance(envelope, dict):
            raise MalformedCall("A call must be an object naming an action and its fields.")
        body = unwrap_args(envelope)
        rationale = pop_rationale(body)
        pop_task_state(body)
        trace: dict[str, str] = {}
        for key in TRACE_KEYS:
            value = body.pop(key, None)
            if value is not None and not isinstance(value, str):
                raise MalformedCall(self.name + ": " + key + " must be a string (got "
                                    + type(value).__name__ + ")")
            if value:
                trace[key] = value
        if body.get("action") != self.name:
            raise UnknownTool("This contract is for " + self.name + ", not "
                              + str(body.get("action")) + ".")
        claimed = {"action", *(f.name for f in self.fields)}
        extra = sorted(set(body) - claimed)
        if extra:
            raise MalformedCall(self.name + " does not accept: " + ", ".join(extra)
                                + ". Expected exactly: " + self.shape())
        args: dict[str, Any] = {}
        for f in self.fields:
            if f.name in body:
                complaint = f.check(body[f.name])
                if complaint:
                    raise MalformedCall(self.name + ": " + complaint)
                args[f.name] = body[f.name]
            elif f.required:
                raise MalformedCall(self.name + " is missing its field " + f.name
                                    + ". Expected exactly: " + self.shape())
            elif f.values:
                args[f.name] = f.values[0]
        return Call(self.name, args, rationale,
                    trace.get("trace_id", ""), trace.get("call_id", ""))


@dataclass(frozen=True)
class SandboxGuard:
    """The execution posture one run was granted, stated as a ceiling and a confinement.

    `max_side_effect` is the highest rung of the ladder anything in this run may reach, and
    `allowed_prefixes` confines every `PATH`-typed argument to a region of the workspace — an
    empty tuple says the whole workspace is in bounds. The guard runs after `validate`, never
    instead of it: validation says the call is a legal sentence in this tool's grammar, and the
    guard says whether this run is allowed to speak it. Path judgement here is deliberately
    redundant with `Field.check`, because a contract that forgot to type a path as `PATH` must
    still not be able to walk out of the sandbox on that oversight.
    """

    max_side_effect: str = LEVEL_READ
    allowed_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_side_effect not in SIDE_EFFECT_LEVELS:
            raise ValueError("Unknown side-effect level: " + str(self.max_side_effect))

    def check(self, contract: ToolContract, call: Call) -> None:
        """Refuse the call, or return and let it run.

        The refusal sentence names the ceiling and the tool's own level, and never quotes a path:
        a denied path is often denied *because* of what its name says, and advice is the channel
        this layer promises is safe to record.
        """
        if SIDE_EFFECT_LEVELS.index(contract.side_effect) \
                > SIDE_EFFECT_LEVELS.index(self.max_side_effect):
            raise SandboxDenied(
                contract.name + " reaches side-effect level '" + contract.side_effect
                + "', above this sandbox's ceiling '" + self.max_side_effect + "'.")
        for f in contract.fields:
            if f.kind != PATH:
                continue
            value = call.get(f.name)
            if isinstance(value, str):
                self._check_path(contract, f.name, value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        self._check_path(contract, f.name, item)

    def _check_path(self, contract: ToolContract, field_name: str, value: str) -> None:
        if value.startswith("/") or (len(value) >= 2 and value[1] == ":") \
                or ".." in value.split("/"):
            raise SandboxDenied(
                contract.name + " field '" + field_name + "' escapes the workspace "
                "(absolute path or parent traversal); a sandbox-relative path was required.")
        if self.allowed_prefixes and not self._inside(value):
            raise SandboxDenied(
                contract.name + " field '" + field_name + "' is outside every region this "
                "sandbox allows.")

    def _inside(self, value: str) -> bool:
        for prefix in self.allowed_prefixes:
            p = prefix.rstrip("/")
            if value == p or value.startswith(p + "/"):
                return True
        return False

    def allows(self, contract: ToolContract, call: Call) -> bool:
        """Whether the call may run, as an answer rather than an exception — for callers that
        branch on the verdict instead of propagating it."""
        try:
            self.check(contract, call)
            return True
        except SandboxDenied:
            return False


def menu(contracts: tuple[ToolContract, ...]) -> str:
    """The one sentence that lists the whole vocabulary, shapes and all.

    Built from the contracts rather than kept beside them because a menu written by hand rots the
    moment a field changes, and a refusal that teaches a stale shape is worse than one that teaches
    nothing: the caller will send exactly what it was shown and be refused again.
    """
    if not contracts:
        return "No tools are registered."
    return "Allowed: " + "; ".join(c.shape() for c in contracts)
