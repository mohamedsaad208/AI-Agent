from __future__ import annotations

from datetime import datetime, timezone
import ast
import difflib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid
from xml.etree import ElementTree

from .config import Settings
from .errors import AgentError, Cancelled, MissingFileError, PolicyError
from . import core
from . import compass
from . import context_builder
from . import events as ux
from . import impact
from . import labels
from . import memory as memory_module
from . import policy
from . import prompts
from . import refusals
from . import risk_policy
from . import taskstate
from . import tool_provider
from . import contracts, tools
from .providers import ModelProvider, metrics_of
from .redaction import redact
from .workspace import Workspace, digest



def now() -> str:
    return datetime.now(timezone.utc).isoformat()


REPLACE_TRIES = 20
REPLACE_WAIT = 0.05


def _replace(tmp: str, path: Path) -> None:
    """Rename over the destination, waiting out a reader.

    Windows answers ERROR_ACCESS_DENIED to `os.replace` when anything at all has the destination open —
    an editor, the search index, a script polling a session file — and it is measured here, not
    theoretical: a session write failed mid-apply because a reader opened the file at that moment. The
    reader lets go within microseconds, so the write waits rather than failing the task it belongs to.
    """
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            # The bus marker is live memory; a serialized session must never carry it.
            if isinstance(value, dict):
                value = json_safe(value)
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        _replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# A run's continuation lives beside its session, not inside it: `session.json` is the record a person
# reads (events, proposal, verdict), and a resumed loop needs the conversation it was in the middle of.
TURNS_NAME = "turns.json"
TURN_CHARS = 4000
TURNS_TOTAL_CHARS = 60000


def turns_file(path: Path) -> Path:
    return path.parent / TURNS_NAME


def save_turns(path: Path, *, run_id: str, turn: int, history: list, observed: dict,
               counters: dict, elapsed: float) -> None:
    """Write what the loop needs to continue where it stopped, and nothing it does not.

    Turns are dropped from the front two at a time once the file grows past the cap, which is the same
    trim the live loop applies to `history` when the window fills (engine: `while history and ...`).
    Every string passes `redact()` before it is stored: a turn carries file contents and model output,
    and this file outlives the run that made it.
    """
    turns = [{"role": str(item.get("role", "")),
              "content": redact(str(item.get("content", ""))[:TURN_CHARS])}
             for item in history[-200:] if isinstance(item, dict)]
    while len(turns) > 2 and sum(len(item["content"]) for item in turns) > TURNS_TOTAL_CHARS:
        turns = turns[2:]
    atomic_json(path, {"schema": 1, "run_id": run_id, "turn": turn, "turns": turns,
                       "observed": dict(observed), "counters": dict(counters),
                       "elapsed": round(elapsed, 1), "written": now()})
    # Writing the continuation is also the heartbeat: a run that is still turning the page holds its
    # lock, and a run whose process died stops refreshing it, so the stale rule can be minutes.
    touch_run_lock(path.parent)


def load_turns(path: Path) -> dict | None:
    """The stored continuation, or None when there is nothing trustworthy to continue from."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("schema") != 1
            or not isinstance(data.get("turns"), list) or not isinstance(data.get("observed"), dict)):
        return None
    return data


LOCK_NAME = "run.lock"
# Long enough that a reboot cannot leave a resumable run locked, short enough that a process which died
# mid-turn stops being the reason nothing can continue. The lock is touched once per turn, so a slow
# local model does not age its own run out.
LOCK_STALE_SECONDS = 900


def acquire_run_lock(run_dir: Path) -> None:
    """One writer per run folder, or a refusal that says so instead of a torn file."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lock = run_dir / LOCK_NAME
    if lock.exists():
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = LOCK_STALE_SECONDS + 1
        if age < LOCK_STALE_SECONDS:
            raise PolicyError("Another window is working on this task right now.")
    lock.write_text(str(os.getpid()), encoding="utf-8")


def touch_run_lock(run_dir: Path) -> None:
    try:
        (run_dir / LOCK_NAME).touch()
    except OSError:
        pass


def release_run_lock(run_dir: Path) -> None:
    try:
        (run_dir / LOCK_NAME).unlink()
    except OSError:
        pass


def parse_action(raw: str) -> dict:
    """Accept a JSON object even when a model wraps it in prose or fences.

    Several small local models obey format=json but still add a preamble, so the
    outermost brace-balanced object is recovered instead of failing the turn.
    """
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if value is None:
        value = _balanced_object(raw)
    if not isinstance(value, dict):
        raise PolicyError("Return one JSON object.")
    return value


def _balanced_objects(raw: str) -> list:
    """Every brace-balanced JSON object in the text, in the order they open."""
    found = []
    start = raw.find("{")
    while start != -1:
        depth, in_string, escape = 0, False, False
        for index in range(start, len(raw)):
            char = raw[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        # A slice that opens with `{` and balances can only parse to an object, so
                        # there is no second shape to test for here; the caller still checks.
                        found.append(json.loads(raw[start:index + 1]))
                    except ValueError:
                        pass
                    break
        start = raw.find("{", start + 1)
    return found


def _is_action(value) -> bool:
    """Does this object look like the envelope the loop asked for, rather than something it quoted?"""
    if not isinstance(value, dict):
        return False
    if "action" in value or "changes" in value:
        return True
    return "path" in value and any(key in value for key in ("content", "edits", "delete"))


def _balanced_object(raw: str) -> dict | None:
    """The action envelope, recovered from around whatever else the model said.

    A reasoning model that leaves its thinking in `content` writes braces before the envelope — `Let me
    weigh {files: {a.py: ...}}` — and taking the *first* balanced object used to hand that back. The turn
    then died with "Return one JSON object", blaming the model for a thing the transport can settle: the
    envelope is the object that has an action in it, so that is the one that wins.
    """
    candidates = [item for item in _balanced_objects(raw) if isinstance(item, dict)]
    for item in candidates:
        if _is_action(item):
            return item
    return candidates[0] if candidates else None


# A model that edits a file it never read gets one automatic recovery: the runtime
# performs the read itself instead of letting the turn end in action="blocked".
STALE_READ = re.compile(r"^Read the current file before proposing a change: (.+)$")


# The two prose fields of a proposal describe it; they are not what a write is made of. A small model
# copying a large file runs out of care on the envelope first (measured twice in the ecommerce run, each
# refusal costing a six-minute turn), so an absent summary or check list is filled with these instead of
# ending the task. Kept as constants because "the project's own command" is also what the block-chosen
# path writes, and the two must not drift.
NO_SUMMARY = "No summary given."
DEFAULT_CHECKS = ["Run the project's own command"]

# The one task-length limit, named. It was six literals and two spellings of the same sentence
# ("1-4000" and "1–4000"), which is how a task that needed a whole pom could be refused by the
# channel it was told to use instead. Field caps -- a summary, a plan line, a project note -- are
# separate rules and keep their own numbers.
MAX_TASK_CHARS = 4000

# The route that works at any file size, added to every "your content is broken" refusal. Without it
# the only advice a large file gets is "send the whole file", which is the thing that just failed.
ANCHORED_ROUTE = (", or send one anchored edit: search for a line unique to this file and replace it "
                  "with itself plus yours. An edit is bounded by the file; whole-file content is "
                  "bounded by the task length limit.")

# Small local models answer a recoverable observation by repeating it as a blocked
# reason, which ends the run and costs the user a turn for nothing. Each observation
# the runtime knows how to recover from carries a prescriptive hint, and the first
# blocked after one is bounced back with that hint instead of being believed.
MAX_BLOCKED_RETRIES = 2

# A steering instruction is one sentence the operator adds while the run is working, and it is not a
# new task: the run keeps its turns, its history and its budget. Capped well below the task limit so
# the instruction stays an instruction, and the waiting queue is capped so a keyboard held down cannot
# bury a run in instructions it has to fold into one prompt.
MAX_STEER_CHARS = 600
STEER_QUEUE_LIMIT = 32


class SteeringInbox:
    """What the operator says to a run that is already going, and where the run comes to read it.

    A view calls :meth:`submit` from whichever thread owns its input; the loop reads the queue only at
    its own checkpoints, between turns, so an instruction never lands inside a tool call and never
    restarts the work already done. That is the whole contract: what has been said is applied to the
    steps that remain, not to the step in flight.

    ``urgent`` is the one ask that reaches into the step in flight. :meth:`interrupt_requested` is what
    the loop hands the provider as its cancel predicate, so an ask that watches its predicate is cut
    short and the instruction is read on the ask that follows — and because a queue the loop has drained
    clears the flag, an interrupt is always a message that was actually waiting, never a stale wish. A
    provider that only reads its predicate between requests is not broken by this: it simply takes the
    instruction at the next checkpoint, which is what a non-urgent one always does.
    """

    def __init__(self, limit: int = STEER_QUEUE_LIMIT) -> None:
        self._limit = max(1, int(limit))
        self._lock = threading.Lock()
        self._pending: list[dict] = []
        self._applied: list[dict] = []
        self._interrupt = threading.Event()

    def submit(self, instruction, *, urgent: bool = False) -> dict:
        """Say one thing to the running loop, from outside it.

        Newlines are flattened: an instruction is a line the thread reads, and a pasted paragraph would
        otherwise arrive in the prompt as a block the model has to re-parse.
        """
        text = redact(" ".join(str(instruction or "").split()))[:MAX_STEER_CHARS]
        if not text:
            raise PolicyError("A steering instruction needs something to say.")
        row = {"id": uuid.uuid4().hex[:8], "at": now(), "text": text, "urgent": bool(urgent)}
        with self._lock:
            if len(self._pending) >= self._limit:
                raise PolicyError("Too many instructions are waiting; the run has not read them yet.")
            self._pending.append(row)
            if row["urgent"]:
                self._interrupt.set()
        return row

    def drain(self) -> list[dict]:
        """Every waiting instruction, oldest first, read and kept as applied."""
        with self._lock:
            rows, self._pending = self._pending, []
            self._applied.extend(rows)
        self._interrupt.clear()
        return rows

    def waiting(self) -> list[dict]:
        """What has been said and not yet read — the queue a view shows before the loop takes it."""
        with self._lock:
            return list(self._pending)

    def applied(self) -> list[dict]:
        """What the loop has already folded into the run, oldest first."""
        with self._lock:
            return list(self._applied)

    def interrupt_requested(self) -> bool:
        """Whether an urgent instruction is waiting that no ask has yet been cut short for."""
        with self._lock:
            return self._interrupt.is_set() and any(row["urgent"] for row in self._pending)


def proposal_hash(session: dict) -> str:
    payload = {k: session[k] for k in ("id", "root", "task", "summary", "checks", "changes")}
    if "plan_reference" in session:
        payload["plan_reference"] = session["plan_reference"]
    if "chat_id" in session:
        payload["chat_id"] = session["chat_id"]
    return digest(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())


def read_plan_reference(ws: Workspace, plan_file: str, settings: Settings) -> dict:
    candidate = Path(plan_file)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(ws.root)
        except ValueError:
            raise PolicyError("The plan file must be inside the selected project folder.") from None
    if candidate.suffix.lower() not in {".md", ".txt"}:
        raise PolicyError("Choose a Markdown (.md) or plain text (.txt) plan file.")
    reference = ws.read(candidate.as_posix())
    if not reference["content"].strip():
        raise PolicyError("The selected plan file is empty.")
    if len(json.dumps(reference)) > min(16000, settings.context_chars // 2):
        raise PolicyError("The plan is too large for this context budget. Attach a shorter phase-specific plan.")
    return reference


def load_session(path: Path) -> dict:
    try:
        if path.stat().st_size > 3_000_000:
            raise AgentError("Session too large.")
        session = json.loads(path.read_text(encoding="utf-8"))
        if session.get("schema") != 1 or not isinstance(session.get("events"), list):
            raise AgentError("Unsupported session format.")
        if "proposal_hash" in session and proposal_hash(session) != session["proposal_hash"]:
            raise AgentError("Proposal integrity check failed.")
        return session
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AgentError("Session is unreadable or invalid.") from exc


def project_key(root: str | Path) -> str:
    """Canonical project identity, including Windows case normalization."""
    return os.path.normcase(str(Path(root).resolve()))


def chat_sessions(runs: Path, root: str | Path, chat_id: str | None = None
                  ) -> list[tuple[Path, dict]]:
    """Sessions of this project, oldest first.

    `chat_id` narrows it to one conversation, which is what a chat's own history needs: a turn from
    another chat is not context for this one. Left out, it answers the wider question the repair loop
    asks — has *this repository* already failed with this exact error, in any conversation, under a
    different task? — because a person who opens a second chat to fix what the first one could not is
    the case where the answer is worth having.
    """
    result = []
    identity = project_key(root)
    for path in runs.glob("*/session.json"):
        try:
            item = load_session(path)
            if (project_key(item["root"]) == identity
                    and (chat_id is None or item.get("chat_id", item["id"]) == chat_id)):
                result.append((path, item))
        except (AgentError, OSError, KeyError, TypeError, ValueError):
            continue
    return sorted(result, key=lambda pair: (pair[1].get("created", ""), pair[1]["id"]))


def chat_context(runs: Path, root: str | Path, chat_id: str, budget: int) -> tuple[str, list[str]]:
    turns, ids = [], []
    for _, item in reversed(chat_sessions(runs, root, chat_id)):
        turn = {"task": item["task"][:1500], "state": item["state"],
                "summary": item.get("summary", item.get("error", ""))[:1200]}
        if len(json.dumps([turn] + turns, ensure_ascii=False)) > budget:
            break
        turns.insert(0, turn)
        ids.insert(0, item["id"])
        if len(turns) == 6:
            break
    return (json.dumps(turns, ensure_ascii=False) if turns else ""), ids


def event(session: dict, kind: str, **values) -> None:
    """Record one raw event on the session and broadcast its structured twin.

    The session stays the audit record in its own dialect; the bus is what live views
    (CLI status line, web SSE, memory) subscribe to. A broadcast failure never fails a
    run: a view that broke is not a reason to lose the proposal already on disk.
    """
    session["events"].append({"at": now(), "kind": kind, **values})
    bus = bus_of(session)
    if bus is None:
        return
    try:
        bus.emit(structured_event(session, kind, values))
    except Exception:
        pass


def structured_event(session: dict, kind: str, values: dict):
    """The session's raw event, translated into the typed vocabulary views consume."""
    return EVENT_ADAPTER.translate(session, kind, values)


# The engine's lifecycle walks lowercase stage codes; the shared Stage enum is the UI's vocabulary
# for the same walk, and the two must stay one story — the code is kept in the event either way.
STAGE_TO_UI = {
    "understand": ux.Stage.UNDERSTANDING, "plan": ux.Stage.PLANNING,
    "implement": ux.Stage.EXECUTING, "impact": ux.Stage.EXECUTING,
    "review": ux.Stage.PLANNING, "approve": ux.Stage.EXECUTING,
    "build_test": ux.Stage.VALIDATING, "verify": ux.Stage.VALIDATING,
}

# How loud each recorded kind is. NORMAL is what a watching person must see; VERBOSE is a
# tool's working detail; DEBUG is context plumbing that only diagnostic views want.
EVENT_LEVELS = {
    "stage": ux.EventLevel.NORMAL, "step": ux.EventLevel.NORMAL,
    "proposal": ux.EventLevel.NORMAL, "proposal_reopened": ux.EventLevel.NORMAL,
    "proposal_rejected": ux.EventLevel.NORMAL, "approved": ux.EventLevel.NORMAL,
    "written": ux.EventLevel.NORMAL, "removed": ux.EventLevel.NORMAL,
    "rolled_back": ux.EventLevel.NORMAL, "rolled_back_file": ux.EventLevel.NORMAL,
    "completed": ux.EventLevel.NORMAL, "stopped": ux.EventLevel.NORMAL,
    "steer": ux.EventLevel.NORMAL,
    "impact": ux.EventLevel.NORMAL, "rejected_action": ux.EventLevel.NORMAL,
    "verification": ux.EventLevel.NORMAL, "test_result": ux.EventLevel.NORMAL,
    "error": ux.EventLevel.NORMAL, "final_report": ux.EventLevel.NORMAL,
    "tool": ux.EventLevel.VERBOSE, "run": ux.EventLevel.VERBOSE,
    "blocked_retried": ux.EventLevel.VERBOSE, "file_not_found": ux.EventLevel.VERBOSE,
    "context_file": ux.EventLevel.DEBUG, "context_excerpt": ux.EventLevel.DEBUG,
    "auto_read": ux.EventLevel.DEBUG, "auto_notes_attached": ux.EventLevel.DEBUG,
    "evidence_attached": ux.EventLevel.DEBUG, "memory_attached": ux.EventLevel.DEBUG,
    "compass_attached": ux.EventLevel.DEBUG,
    "plan_attached": ux.EventLevel.DEBUG, "block_chosen": ux.EventLevel.DEBUG,
    "tool_provider": ux.EventLevel.DEBUG, "session": ux.EventLevel.DEBUG,
}


class EventAdapter:
    """Translates the session dialect (`kind` + flat fields) into the 8 typed UX events.

    Kinds with a dataclass go through it; the rest still travel as validated `Event`
    objects so no subscriber loses a row to a kind the adapter has not met.
    """

    def translate(self, session: dict, kind: str, values: dict):
        level = EVENT_LEVELS.get(kind, ux.EventLevel.DEBUG)
        body = {**values, "run_id": str(session.get("id") or ""), "level": level.value}
        maker = getattr(self, "_make_" + kind, None)
        if maker is not None:
            return maker(session, body, level)
        return ux.Event(kind=kind, data=body, level=level)

    def _make_stage(self, session, values, level):
        # A stage the table does not know is passed through rather than renamed: an orientation
        # line that guesses is worse than one that says what the engine said.
        to_code = str(values.get("to") or "")
        previous_code = str(values.get("previous_stage") or "")
        return ux.StageChanged(stage=self._ui_stage(to_code),
                               previous_stage=self._ui_stage(previous_code),
                               message=to_code, level=level)

    @staticmethod
    def _ui_stage(code: str) -> str:
        return STAGE_TO_UI[code].value if code in STAGE_TO_UI else code.upper()

    def _make_step(self, session, values, level):
        details = {k: v for k, v in values.items() if k not in ("run_id", "level", "id", "action")}
        return ux.StepUpdated(step_id=str(values.get("id") or ""),
                              action=str(values.get("action") or ""),
                              status="done", details=details, level=level)

    def _make_tool(self, session, values, level):
        rest = {k: v for k, v in values.items()
                if k not in ("run_id", "level", "name", "duration_ms", "status")}
        return ux.ToolCall(tool_name=str(values.get("name") or ""), args=rest,
                           output_summary=str(values.get("detail") or "")[:300],
                           duration_ms=float(values.get("duration_ms") or 0.0),
                           status=str(values.get("status") or "ok"), level=level)

    def _make_written(self, session, values, level):
        return ux.FileChanged(path=str(values.get("path") or ""),
                              change_type=self._change_type(session, values),
                              diff_summary=str(values.get("sha256") or "")[:16], level=level)

    def _make_removed(self, session, values, level):
        return ux.FileChanged(path=str(values.get("path") or ""), change_type="deleted",
                              level=level)

    def _make_rolled_back_file(self, session, values, level):
        return ux.FileChanged(path=str(values.get("path") or ""), change_type="reverted",
                              level=level)

    def _make_test_result(self, session, values, level):
        return self._run_to_test_result(values, level)

    def _make_run(self, session, values, level):
        if "status" not in values:
            return self._generic("run", values, level)
        return self._run_to_test_result(values, level)

    def _make_verification(self, session, values, level):
        return self._run_to_test_result(values, level)

    def _make_proposal(self, session, values, level):
        # The band is read off the files in this proposal at the moment the ask is made, rather than stored
        # when it was written: a session that gained or lost a `pom.xml` since should ask with the risk it
        # has now, and one number that comes from `risk_policy` is the same number the review card shows.
        return ux.ApprovalRequested(
            action=str(session.get("summary") or "Apply proposal"),
            risk_level=risk_policy.assess_changes(session.get("changes") or [],
                                                  str(session.get("task") or "")).risk.value,
            reason=str(session.get("task") or "")[:300],
            approval_id=str(values.get("hash") or ""), level=level)

    def _make_completed(self, session, values, level):
        return ux.FinalReport(task=str(session.get("task") or ""), success=True,
                              files_changed=self._changed_paths(session),
                              verification_status=ux.VerificationStatus.NOT_CHECKED.value,
                              test_summary=str(values.get("summary") or ""), level=level)

    def _make_stopped(self, session, values, level):
        return self._make_error(session, {"message": values.get("reason", ""),
                                          "error_type": "stopped",
                                          "recoverable": False}, level)

    def _make_rejected_action(self, session, values, level):
        return self._make_error(session, {"message": values.get("reason", ""),
                                          "error_type": "rejected_action"}, level)

    def _make_error(self, session, values, level):
        return ux.AgentProgressError(
            error_type=str(values.get("error_type") or "error"),
            message=str(values.get("message") or ""),
            root_cause=str(values.get("root_cause") or ""),
            suggested_action=str(values.get("suggested_action")
                                 or values.get("next_action") or ""),
            recoverable=bool(values.get("recoverable", True)), level=level)

    def _run_to_test_result(self, values, level):
        status = str(values.get("status") or "")
        proof = values.get("proof") if isinstance(values.get("proof"), dict) else {}
        total = int(proof.get("tests") or values.get("tests") or 0)
        failed = int(proof.get("failures") or 0) + int(proof.get("errors") or 0)
        return ux.TestResult(command=str(values.get("command") or values.get("label") or ""),
                             passed=status in ("passed", "ok", "success") or (total > 0 and failed == 0),
                             total_tests=total, failed_tests=failed,
                             passed_tests=max(0, total - failed),
                             output_tail=str(values.get("tail") or values.get("output") or "")[-1200:],
                             level=level)

    def _generic(self, kind, values, level):
        return ux.Event(kind=kind, data=values, level=level)

    @staticmethod
    def _change_type(session, values):
        for change in session.get("changes", []):
            if change.get("path") == values.get("path"):
                if change.get("delete"):
                    return "deleted"
                return "created" if change.get("before") is None else "modified"
        return "modified"

    @staticmethod
    def _changed_paths(session):
        return tuple(str(change.get("path", "")) for change in session.get("changes", [])
                     if change.get("path"))


EVENT_ADAPTER = EventAdapter()

# The session dict is written to disk as JSON, so the run's bus rides under a key the
# writers strip and the loaders never produce: an in-memory marker, not stored state.
_BUS_KEY = "_event_bus"


def bus_of(session: dict) -> "ux.EventBus | None":
    """The emitter bus for this run, defaulting to the process-wide one views subscribe to."""
    if _BUS_KEY in session:
        return session[_BUS_KEY]
    return ux.global_bus


def attach_bus(session: dict, bus: "ux.EventBus | None") -> dict:
    """Point one run's events at `bus` (None silences the broadcast for this run)."""
    session[_BUS_KEY] = bus
    return session


def json_safe(session: dict) -> dict:
    """The session as it may be serialized: the bus marker is memory, not record."""
    if _BUS_KEY not in session:
        return session
    return {key: value for key, value in session.items() if key != _BUS_KEY}


def record_stage(session: dict, code: str) -> str:
    """Say how far along this run is, when the lifecycle counts the move as one.

    A stage is not a state: `state` records what happened to the task and decides which buttons work, while
    this answers the question a person watching a long job actually asks — and the two vocabularies share
    one name and nothing else, which is why they stay apart. A move `core` does not allow writes nothing and
    returns the stage that stayed: this is an orientation line, and it must never be the reason a command
    failed.
    """
    current = str(session.get("stage") or "")
    if current == code or not core.stage_allowed(current, code):
        return current
    session["stage"] = code
    event(session, "stage", to=code, previous_stage=current)
    return code


def _match_lines(text: str, needle: str) -> list[int]:
    """1-based line numbers where `needle` starts, for the "widen it" message."""
    rows, index = [], 0
    while True:
        found = text.find(needle, index)
        if found < 0:
            return rows
        rows.append(text.count("\n", 0, found) + 1)
        index = found + 1


def _apply_edits(name: str, before: str, edits: list) -> str:
    """Exact, ordered replacements against the file as it now stands.

    No fuzzy matching and no whitespace forgiveness: a wrong-but-plausible anchor puts code
    in the wrong place, and the review gate is on text, not on intent. Everything is resolved
    here rather than at apply time so the diff the user approved is the bytes written, and
    `before_hash` stays the guard that the file has not moved underneath us.
    """
    working = before
    touched: list[tuple[int, int]] = []
    for number, edit in enumerate(edits, 1):
        search, replace = edit["search"], edit["replace"]
        hits = working.count(search)
        if hits == 0:
            head = search.splitlines()[0][:80] if search.strip() else search[:80]
            raise PolicyError(f"Edit {number} in {name} matches nothing in the file as it "
                              f"stands. Its first line is {head!r} — quote the current text "
                              "exactly, including indentation, or read the file again.")
        if hits > 1:
            raise PolicyError(f"Edit {number} in {name} matches {hits} places (lines "
                              f"{', '.join(str(row) for row in _match_lines(working, search))}). "
                              "Widen the search block until it is unique.")
        start = working.find(search)
        end = start + len(search)
        if any(begin < end and start < stop for begin, stop in touched):
            raise PolicyError(f"Edit {number} in {name} overlaps an earlier edit of the same "
                              "file. Merge the two blocks into one.")
        working = working[:start] + replace + working[end:]
        shift = len(replace) - len(search)
        touched = [(begin + (shift if begin >= end else 0), stop + (shift if stop >= end else 0))
                   for begin, stop in touched]
        touched.append((start, start + len(replace)))
    return working


def _change_form(change: dict, exists: bool = True) -> tuple[str, object]:
    """("content", whole-file text) or ("edits", ordered hunks) — the shape rules live here.

    `exists` decides whose complaint is on record: an empty search block against a file that is not
    there yet is the model's way of saying "write the whole file", and `prepare_changes` has the right
    sentence for that. Measured twice on the ecommerce run, where a create task came back as
    edits-with-empty-search and was answered with advice about matching anywhere — a true statement
    about a file that does not exist, and one the model could do nothing with.
    """
    if set(change) == {"path", "delete"} and change["delete"] is True:
        # The removal form, and the only one with no content. `prepare_changes` refuses it unless the
        # file exists and was read this turn, and `repair.must_ask` refuses to let Auto-Apply do it
        # without a click; the hash it stores is the one rollback restores from.
        return "delete", None
    if set(change) == {"path", "content"}:
        return "content", change["content"]
    if set(change) == {"path", "search", "replace"}:
        # The one-hunk shorthand: the same thing, written the way a person thinks about it.
        change = {"path": change["path"],
                  "edits": [{"search": change["search"], "replace": change["replace"]}]}
    if set(change) == {"path", "edits"}:
        edits = change["edits"]
        if not isinstance(edits, list) or not 1 <= len(edits) <= 10:
            raise PolicyError("Each edits list needs between 1 and 10 hunks.")
        for number, edit in enumerate(edits, 1):
            if not isinstance(edit, dict) or set(edit) != {"search", "replace"}:
                raise PolicyError(f"Edit {number} needs only search and replace.")
            if not isinstance(edit["search"], str) or (exists and not edit["search"]):
                raise PolicyError(f"Edit {number} has an empty search block; it would match anywhere.")
            if not isinstance(edit["replace"], str):
                raise PolicyError(f"Edit {number} needs replace as a string; use \"\" to delete.")
            if "\x00" in edit["search"] or "\x00" in edit["replace"]:
                raise PolicyError("Edits must be UTF-8 text without NUL bytes.")
        return "edits", edits
    raise PolicyError('Each change needs "path" and complete content, or "edits" (search and '
                      'replace), or "delete": true.')


JAVA_DECLARATION = re.compile(r"\b(?:package|import|public|protected|private|class|interface|"
                             r"enum|record|module)\b")
JAVA_PACKAGE = re.compile(r"^[ \t]*package[ \t][\w.]*[ \t]*;", re.M)
JAVA_TYPE = re.compile(r"\b(?:class|interface|record|enum)\b")
DOCTYPE = re.compile(r"<!\s*DOCTYPE", re.IGNORECASE)


def shape_mismatch(name: str, content: str) -> str:
    """Name the file whose contents are not the language its suffix promises.

    Measured in the ecommerce run: shown a module pom beside the Java entry point it was asked for,
    a 3 B model answered with the pom, and Auto-Apply wrote it into `…Application.java`. Nothing in
    the tool reads Java, so the build discovered it eleven seconds later and the entire fix round
    spent itself on the wrong language. This is the same gate `prepare_changes` already runs for
    `.py` and `.json` — Python has an AST, Java does not, so the check is the two things about a
    compilation unit that cannot be otherwise. The keyword test is loose on purpose: a comment that
    mentions a class is not a declaration, but refusing the file would be a worse mistake than
    writing it. The markup case, which is the one that actually happened, is caught by the first rule.
    """
    head = content.lstrip("\ufeff \t\r\n")
    if Path(name).suffix.lower() != ".java":
        return ""
    if head.startswith("<"):
        return (name + " holds XML where Java was asked for. Put the compilation unit itself in "
                "content — the package line, the imports and the class — not a pom.")
    if not JAVA_DECLARATION.search(content):
        return (name + " has no Java declaration in it. A .java file needs a package line, an "
                "import, or a class, interface, enum or record.")
    return ""


def _local(tag) -> str:
    """An ElementTree tag with its namespace taken off the front.

    Every real pom binds `xmlns="http://maven.apache.org/POM/4.0.0"`, so a comparison against the raw
    tag sees `{http://…}project` and the whole check stepped out on the only files it was written for.
    The facts reader in `symbols` strips the same prefix for the same reason.
    """
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


# Where Maven reads each of these elements: the first two by their immediate parent, `resources`
# anywhere under a `build` (which is also what a profile's build is) or a plugin's `configuration`.
POM_PLACEMENT = {"dependency": ("dependencies",), "plugin": ("plugins",),
                 "resources": ("build", "configuration"), "testresources": ("build", "configuration")}


def _check_pom_shape(name: str, root) -> None:
    """A Maven model check, not a general XML check.

    Well-formed is not enough for a pom, and this was measured twice: a `<dependency>` written
    directly under `<project>` parses cleanly, passes any before/after AST comparison, and then
    answers the build with `Unrecognised tag: 'dependency'`. The parser cannot see it because the
    document is valid XML; the model can, because Maven says where those elements live.

    A misplaced `<resources>` is the quieter half of the same defect: Maven does not reject it, it
    ignores it, so the build succeeds and the changelog or properties file is simply not on the
    classpath — a failure that arrives several turns later, as a missing resource.
    """
    if _local(root.tag) != "project":
        return                                  # a changelog, a faces config, any other XML
    parents = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter():
        tag = _local(element.tag)
        wanted = POM_PLACEMENT.get(tag.casefold())
        if not wanted:
            continue
        line = [tag]
        node = parents.get(element)
        while node is not None:
            line.insert(0, _local(node.tag))
            node = parents.get(node)
        if tag.casefold() in ("dependency", "plugin"):
            # `line` is the ancestor chain with the element last, so its parent is the one before it.
            holder = line[-2] if len(line) > 1 else "(project root)"
            if holder not in wanted:
                raise PolicyError(
                    name + " has a <" + tag + "> inside <" + holder + ">. Maven reads that as "
                    "Unrecognised tag: '" + tag + "' and the build fails before compiling anything. "
                    "Put it inside <" + wanted[0] + ">.")
        elif not any(item in wanted for item in line):
            raise PolicyError(
                name + " has a <" + tag + "> outside any <build>. Maven does not reject it, it "
                "ignores it: the build passes and " + tag + " never reaches the classpath, which "
                "shows up later as a missing file. Put it inside <project><build>, or inside a "
                "plugin's <configuration> if a plugin is meant to handle it.")


def prepare_changes(ws: Workspace, changes: object, observed: dict) -> list[dict]:
    if not isinstance(changes, list) or not 1 <= len(changes) <= 8:
        raise PolicyError("A proposal needs between 1 and 8 file changes.")
    prepared, seen = [], set()
    total = 0
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("path"), str):
            raise PolicyError('Each change needs "path" and complete content, or "edits" (search and '
                      'replace), or "delete": true.')
        name = change["path"]
        path = ws.path(name, writable=True)
        form, value = _change_form(change, exists=path.exists())
        canonical = str(path).casefold() if os.name == "nt" else str(path)
        if canonical in seen:
            raise PolicyError("Duplicate change target.")
        seen.add(canonical)
        before = None
        before_hash = None
        if path.exists():
            original = ws.read(name)
            if observed.get(name) != original["sha256"]:
                raise PolicyError("Read the current file before proposing a change: " + name)
            before, before_hash = original["content"], original["sha256"]
        elif form == "edits":
            raise PolicyError("A new file needs complete content; edits only apply to a file "
                              "that already exists: " + name)
        content = _apply_edits(name, before, value) if form == "edits" else value
        if form == "delete":
            # A removal is the one change with no content to run the language gates over, so its
            # guards are only these: the file is really there, it was read this turn, and the text it
            # takes away counts against the same write budget a whole-file content would.
            if before is None:
                raise PolicyError("Cannot delete a file that does not exist: " + name)
            total += len(before.encode("utf-8"))
            if total > 100_000:
                raise PolicyError("Proposal exceeds 100 KB; split the task.")
            prepared.append({"path": name, "before": before, "before_hash": before_hash,
                             "after": None, "after_hash": None, "delete": True})
            continue
        if not isinstance(content, str) or "\x00" in content:
            raise PolicyError("Replacement must be UTF-8 text without NUL bytes.")
        try:
            # The workspace admits a suffix in any case (workspace.py:132), so this gate has to
            # fold it the same way or APP.PY is written with no AST check at all.
            suffix = path.suffix.lower()
            if suffix == ".py":
                ast.parse(content, filename=name)
            elif suffix == ".json":
                json.loads(content)
            elif suffix == ".xml":
                # The content is a model's output, so it is parsed like untrusted input: a DTD is the
                # only thing that lets an XML parser expand or fetch beyond what the proposal wrote,
                # and no Maven pom has one. Refuse it before the parser is given the text.
                if DOCTYPE.search(content):
                    raise PolicyError(name + " declares a DTD. Write the file as plain XML — "
                                      "Maven poms carry their schema in xsi:schemaLocation, not a DTD.")
                _check_pom_shape(name, ElementTree.fromstring(content))
            else:
                reason = shape_mismatch(name, content)
                if reason:
                    raise PolicyError(reason)
                if (suffix == ".java" and before is not None and JAVA_PACKAGE.search(before)
                        and not JAVA_PACKAGE.search(content)):
                    # Measured on the ecommerce run: "make this class public, every other line stays as
                    # it is" came back as the file minus its package and import lines — still a legal
                    # compilation unit, so shape_mismatch passed it, and javac found out one build later.
                    raise PolicyError(name + " lost its package line. A rewrite of a Java file keeps "
                                      "the package statement and the imports it already had; send the "
                                      "current first lines unchanged.")
                if (suffix == ".java" and not JAVA_TYPE.search(content)
                        and Path(name).name.casefold() != "package-info.java"):
                    # `UserRepository.java` arrived as 167 characters of imports and nothing else. The
                    # model's summary still read "Create UserRepository interface", Auto-Apply wrote it,
                    # and the reactor answered with nine errors — every one of them in the file that
                    # *referenced* it. A Java file that declares no type is a write that stopped early.
                    raise PolicyError(name + " declares no Java type. A file of package and import "
                                      "lines alone compiles to nothing: send the whole class, "
                                      "interface, enum or record, with its closing brace.")
        except ElementTree.ParseError as exc:
            # Maven's own complaint arrives eleven seconds later and a build later; this one costs
            # the model nothing and stops the file reaching the disk at all.
            raise PolicyError("Invalid XML in " + name + ": " + str(exc.msg)
                              + ". Put actual complete file text directly in content"
                              + ANCHORED_ROUTE) from None
        except (SyntaxError, ValueError):
            raise PolicyError("Invalid syntax in " + name + ". Put actual complete source text "
                              "directly in content, not a serialized content wrapper"
                              + ANCHORED_ROUTE) from None
        total += len(content.encode("utf-8"))
        if total > 100_000:
            raise PolicyError("Proposal exceeds 100 KB; split the task.")
        if before == content:
            raise PolicyError("Proposal contains an unchanged file: " + name)
        prepared.append({"path": name, "before": before, "before_hash": before_hash,
                         "after": content, "after_hash": digest(content.encode("utf-8"))})
    return prepared


def propose_block(ws: Workspace, task: str, name: str, content: str, runs: Path,
                  chat_id: str | None = None, model: str = "user") -> Path:
    """Open a session from one file the person in front of the window chose to keep.

    A code block in an answer is the model's text, but the decision to write it is the
    user's, so this path skips the tool loop and produces exactly the artifact the loop
    produces: a WAITING_APPROVAL session whose hash covers the same fields. That is what
    keeps Apply, rollback, the checks card and the stale-file guard working unchanged.

    prepare_changes refuses to propose over a file nobody read, because a small model that
    guessed a file's contents would quietly delete part of it. Here the read happens on
    this side of the boundary, so the diff the user reviews still shows what is lost.
    """
    if not task.strip() or len(task) > MAX_TASK_CHARS:
        raise AgentError(f"Task must contain 1-{MAX_TASK_CHARS} characters.")
    if not isinstance(content, str) or not content.strip():
        raise PolicyError("That block has no content to write.")
    if not isinstance(name, str) or not name.strip():
        raise PolicyError("That block did not name a file.")
    run_id = uuid.uuid4().hex
    path = runs.resolve() / run_id / "session.json"
    session = {"schema": 1, "id": run_id, "root": str(ws.root), "task": task,
               "state": "DISCOVERING", "created": now(), "events": [], "model": model}
    if chat_id is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", chat_id):
            raise PolicyError("Invalid chat identity.")
        session["chat_id"] = chat_id
    observed = {}
    target = ws.path(name, writable=True)        # the policy decides before anything is read
    if target.exists():
        observed[name] = ws.read(name)["sha256"]
    changes = prepare_changes(ws, [{"path": name, "content": content}], observed)
    summary = "Write " + changes[0]["path"] + " from the code block you chose."
    session.update(summary=summary, checks=list(DEFAULT_CHECKS), changes=changes,
                   state="WAITING_APPROVAL")
    session["proposal_hash"] = proposal_hash(session)
    event(session, "block_chosen", path=changes[0]["path"], from_block=True)
    event(session, "proposal", hash=session["proposal_hash"])
    # The one path that opens at `review` rather than walking to it: a person chose the block, so the
    # exploring and the drafting already happened, on their keyboard rather than here.
    record_stage(session, "review")
    atomic_json(path, session)
    return path


def plan(ws: Workspace, task: str, provider: ModelProvider, settings: Settings,
         runs: Path, progress=None, cancelled=None, plan_file: str | None = None,
         chat_id: str | None = None, extra_context: str | None = None,
         plan_step: int | None = None, memory: str | None = None, step=None,
         on_token=None, goal: str = "", criteria: list[str] | None = None,
         accepts: list[int] | None = None, resume_run: str | None = None,
         fix_round: int = 0, fast_provider=None, strong_provider=None,
         tool_providers=(), event_bus=None, steering=None) -> Path:
    """Run the tool loop until the model proposes a change.

    `tool_providers` are the *external* sources this run offers beside the built-ins — normally
    `mcp.McpToolProvider`s, and normally nothing. The loop always runs on one merged vocabulary: the
    eight native tools plus whatever these supply, each judged by the gate belonging to the provider
    that supplied it. An empty default is what keeps an unconfigured run exactly the run it was
    before providers existed: same menu, same prompt, same single policy question per call.

    `progress` receives every line the loop has to say; `step`, when the caller passes one,
    receives only the lines that announce a tool action as `step(line, step_id, action, fields)`, so a
    window with a chat can put those in the conversation without also drawing the turn counter there,
    and can open the stored event behind them later. The CLI and the Tk window pass neither and keep
    the single stream they always had. `on_token`, when given, hears the model's own writing as it
    arrives — a display consumer only, since the reply the loop acts on is the assembled one.

    `fast_provider` and `strong_provider` are the optional local fast/strong pair: mechanical
    gathering turns go to the fast model and the first turn plus any turn that has to recover
    from a refusal goes to the strong one. Both default to None, which keeps every request on
    `provider` — the pre-split behaviour, and the fallback whenever the profile names no extras.

    extra_context carries untrusted evidence captured by the runtime (a build or test
    log) so a repair turn can see the failure without widening the task text limit.
    plan_step records which ledger step this session implements; it is outside
    proposal_hash on purpose, since the number proves sequencing rather than content.
    memory is the user's own standing note for this project, kept on the session for
    audit but outside the hash, which covers what the proposal changes.
    steering is a `SteeringInbox` the view submits to while this run is in flight. Each instruction is
    read at a turn boundary, folded into the run's binding constraints and its conversation, and
    recorded on the session — the work already done is kept, not restarted, and an instruction still
    waiting when the run ends is left in the box for the next step. `urgent` asks are the exception:
    they ask the in-flight provider to stop, so the instruction is read on the next one rather than
    after a slow model finishes. Left out, the loop is exactly the run it was before steering existed.
    resume_run picks up a run that stopped before it reached a result: the stored turns come back, a
    file whose content moved since is dropped from the read set instead of trusted, and the failure
    counters carry over so a resumed run does not quietly get a second full budget for one mistake.

    `event_bus` is the structured-event channel views share (see `events.py`): every session event is
    translated by the `EventAdapter` and broadcast there, so a CLI status line, the web SSE hub and the
    memory engine subscribe to one stream instead of wrapping `progress`. Left out, the run broadcasts
    on the process-wide bus; passing a bus of your own isolates the run. `progress` is a pure sink —
    the core never prints; with no sink given, prose lines are dropped.
    """
    if not task.strip() or len(task) > MAX_TASK_CHARS:
        raise AgentError(f"Task must contain 1-{MAX_TASK_CHARS} characters.")
    if progress is None:
        def progress(_line):
            pass
    # The task decides the language the loop announces in, exactly as it decides the language the
    # model answers in — a window that has been used for both must not switch halfway through.
    # The reference block is stripped first: it is written in the language of the *quoted* message,
    # and an English question quoting an Arabic reply would otherwise announce itself in Arabic.
    arabic = labels.is_arabic(labels.asked_of(task))
    # Built below, once per run: `announce` reads the in-flight call's id off it, so the row written
    # while a tool runs and the outcome that tool hands back are keyed by the same value.
    tool_context: "tools.ToolContext | None" = None

    def announce(action: str, **fields) -> None:
        """One thing the loop just did, said three ways: the strip, the conversation, the record.

        The id is what ties the three together. A sentence is not a key — it repeats, it changes with
        the language the task was asked in, and it is the only handle a row clicked an hour later has
        for finding its details again, so the id rides on the stored event as well as the live one. A
        tool call already has that id, minted by the registry, and takes it from the context; anything
        else the loop announces mints its own.
        """
        line = labels.step_line(arabic, action, **fields)
        step_id = (tool_context.take_call_id() if tool_context is not None
                   else "") or uuid.uuid4().hex[:8]
        event(session, "step", id=step_id, action=action, **fields)
        progress(line)
        if step is not None:
            step(line, step_id, action, fields)

    def stopped() -> bool:
        """Whether the person watching asked this run to end — as opposed to interrupting to steer."""
        return cancelled is not None and bool(cancelled())

    def stop_or_steer() -> bool:
        """The predicate a provider ask is cut short by: a stop, or an urgent instruction unread yet.

        Handing the loop's own question to `generate` rather than the caller's is what makes a steer
        arrive inside the run rather than after the current ask: an urgent instruction is waiting only
        until the first checkpoint that reads it, and an ask cut short for a stop still ends the run.
        """
        if stopped():
            return True
        waiter = getattr(steering, "interrupt_requested", None)
        return bool(waiter()) if waiter is not None else False

    # Evidence is captured from a command the project itself defines; whatever it
    # printed is stored on the session and re-sent to the provider on the next turn.
    extra_context = redact(extra_context) if extra_context else extra_context
    resumed: dict = {}
    if resume_run is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", str(resume_run)):
            raise PolicyError("Invalid run identity.")
        path = runs.resolve() / str(resume_run) / "session.json"
        session = load_session(path)
        if session.get("state") != "DISCOVERING":
            raise PolicyError("That task already reached a result; there is nothing to continue.")
        stored_root = str(session.get("root") or "")
        if not stored_root or (Path(stored_root).resolve() != ws.root.resolve()):
            raise PolicyError("That task belongs to a different project folder.")
        run_id = str(session.get("id") or resume_run)
        resumed = load_turns(turns_file(path)) or {}
    else:
        run_id = uuid.uuid4().hex
        path = runs.resolve() / run_id / "session.json"
        session = {"schema": 1, "id": run_id, "root": str(ws.root), "task": task,
                   "state": "DISCOVERING", "created": now(), "events": [], "model": provider.model}
    # Everything the loop broadcasts goes through one bus, decided before the first recorded event.
    attach_bus(session, ux.global_bus if event_bus is None else event_bus)
    # The continuity record lives on the session, not in the history: trimming a turn must never
    # take with it the constraints and decisions that turn established.
    if not isinstance(session.get("task_state"), dict):
        session["task_state"] = taskstate.new_state(session.get("task") or task)
    state = session["task_state"]
    # What the operator said mid-run, kept beside the record of the run rather than inside the task
    # text: an instruction that changed the remaining turns must be readable afterwards, and it must
    # not change the number a person typed to approve the proposal the run ends with.
    if not isinstance(session.get("steering"), list):
        session["steering"] = []
    record_stage(session, "understand")
    run_dir = path.parent
    acquire_run_lock(run_dir)
    prior_context = ""
    if chat_id is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", chat_id):
            raise PolicyError("Invalid chat identity.")
        session["chat_id"] = chat_id
        prior_context, ids = chat_context(runs, ws.root, chat_id, min(4000, settings.context_chars // 6))
        session["context_session_ids"] = ids
    reference = read_plan_reference(ws, plan_file, settings) if plan_file else None
    # D42: a build error this repository has already failed on — in any chat, under any task — is said
    # here rather than rediscovered a model turn later. `stalled()` can only see the rounds of this
    # conversation, and a person who opens a second chat for a fix the first one could not land is
    # exactly the case that history was worth keeping for.
    open_errors: list[dict] = []
    try:
        from . import repair       # `repair` reads this module; one of the two directions has to wait
        open_errors = repair.unresolved([item for _, item in chat_sessions(runs, ws.root)],
                                        exclude_chat=chat_id or "")
    except OSError:
        open_errors = []
    for row in open_errors[:3]:
        announce("unresolved_error", count=row["count"], label=row["label"], detail=row["sample"])
    if reference:
        session["plan_reference"] = {"path": reference["path"], "sha256": reference["sha256"]}
        event(session, "plan_attached", **session["plan_reference"])
        progress("Reading attached plan: " + reference["path"])
    if plan_step is not None:
        if not reference:
            raise PolicyError("Only a step of an attached plan can be recorded.")
        if not isinstance(plan_step, int) or not 1 <= plan_step <= 99:
            raise PolicyError("Plan step must be a small positive number.")
        session["plan_step"] = plan_step
        if goal:
            session["goal"] = str(goal)[:300]
        if criteria:
            session["criteria"] = [str(c)[:200] for c in criteria[:8]]
        if accepts:
            session["accepts"] = [int(a) for a in accepts if isinstance(a, int)]
    if fix_round:
        # Charged on the record before the round costs a request: a restart that resumes this session has
        # to find the number here, or the ceiling it stopped at becomes free again.
        session["fix_round"] = max(1, int(fix_round))
    if memory and memory.strip():
        if len(memory) > memory_module.MAX_MEMORY:
            raise PolicyError(f"Project notes must stay within {memory_module.MAX_MEMORY} characters.")
        memory_module.record(session, memory)
        event(session, "memory_attached", characters=len(session["memory"]))
    # The request is read, the folder's map and notes are gathered, and from here the model is asked.
    record_stage(session, "plan")
    atomic_json(path, session)
    repo_map = ws.repo_map()
    # What this project's earlier tasks left behind is read once per turn from the store outside the
    # folder, so a small model is not asked to re-derive a Java version or a naming rule from a file it
    # is no longer shown. Empty on a first task, and that is the correct answer to send.
    auto_notes = memory_module.auto_block(memory_module.auto_notes_context(
        memory_module.memory_dir_for(runs), str(ws.root)))
    memory_block = memory_module.block(session["memory"]) if session.get("memory") else ""
    # The vocabulary this run offers, built before the first request rather than at the first tool
    # call: the menu the model is shown and the dispatcher that judges its answers have to be one
    # object's answer, or a advertised tool the router will not name costs a turn to discover and a
    # router that admits a tool nobody was told exists is a rule nobody was shown. External providers
    # discover their tools here, so a server that will not start is recorded before any request is
    # paid for rather than halfway through one.
    registry = tool_provider.CompositeToolProvider(
        tool_provider.default_providers(tool_providers), control=tools.CONTROL_ACTIONS)
    addendum = registry.addendum()
    for supplied in registry.providers:
        if supplied.provenance.external:
            event(session, "tool_provider", provider=supplied.id,
                  tools=len(supplied.contracts()),
                  skipped=len(getattr(supplied, "skipped", []) or []),
                  problem=str(getattr(supplied, "problem", "") or "")[:180])
    # The compass: this project's own goal, rules and progress, read out of the two memory files it
    # keeps in `.agent/memory`. Its ceiling is a token count because the thing it protects is a local
    # model's window, and the room it is offered is what this base still has — task, map, notes and
    # history charged first, so a memory that would push the request out of the prompt says nothing.
    compass_tokens_used = context_builder.compass_tokens(
        settings, used_chars=len(prompts.SYSTEM) + len(task) + len(repo_map) + len(addendum)
        + len(memory_block) + len(auto_notes) + len(prior_context) + len(extra_context or ""))
    compass_block = compass.for_prompt(ws.root, chat_id or "", tokens=compass_tokens_used)
    base = prompts.base_messages(
        task=task, repo_map=repo_map, settings=settings,
        memory_block=memory_block,
        reference=reference, open_errors=open_errors, prior_context=prior_context,
        evidence=extra_context or "", auto_notes=auto_notes, tool_addendum=addendum,
        compass=compass_block)
    if compass_block:
        event(session, "compass_attached", characters=len(compass_block),
              tokens=compass_tokens_used)
    if auto_notes:
        event(session, "auto_notes_attached", characters=len(auto_notes))
    if extra_context:
        session["evidence"] = extra_context[:4000]
        event(session, "evidence_attached", characters=len(extra_context))
    history, observed = [], {}
    if resumed:
        # The conversation the run was in, not a summary of it: a resumed loop that forgot what it had
        # already read proposes the same file twice and burns its budget re-learning the task.
        history = [{"role": item["role"], "content": str(item.get("content", ""))}
                   for item in resumed.get("turns", [])
                   if isinstance(item, dict) and item.get("role") in ("assistant", "user")]
        stale = []
        for name, digest_of in sorted((resumed.get("observed") or {}).items()):
            try:
                current = ws.read(name)["sha256"]
            except (AgentError, OSError):
                current = None
            if current != digest_of:
                stale.append(name)
            else:
                observed[name] = digest_of
        if stale:
            progress("Files changed or gone since the run stopped: "
                     + ", ".join(stale[:6]) + ("…" if len(stale) > 6 else "")
                     + ". The loop will read them again before proposing them.")
        if len(history) != len(resumed.get("turns", [])):
            progress("Some of the stored turns were unreadable and were dropped.")
    # Deterministic retrieval, so a small local model is not left guessing filenames out of a truncated
    # map: the index is scored against the sentence the operator typed, the top few files ride along as
    # already-read snapshots, and the reason each was chosen is said in the thread. The old rule was
    # "the file's name must appear in the task", which answered a person who types paths and no one
    # else; the boundary test that keeps the named case first is kept verbatim.
    # What it may spend is `prompts.retrieval_budget`, and `context_builder` is what spends it.
    used = context_builder.total_chars(base)
    _visible, rows = ws.index()
    named = []
    # Architecture-aware input: the project index, when the scanner has run, is what re-ranks the
    # symbol rows by layer. Its facts are measured now and read from the next turn on -- the build
    # files the scan opened hold the versions a later proposal has to respect, and they are kept
    # outside the folder so no proposal can edit the facts it is being checked against.
    from .repo_scanner import get_or_create_index, project_facts
    _proj_index = get_or_create_index(ws.root)
    if _proj_index is not None:
        memory_module.record_facts(memory_module.memory_dir_for(runs), str(ws.root),
                                   project_facts(_proj_index))
    # A step of a plan is named by the criteria it accepts, not by the number the operator typed:
    # `context_builder.seed_text` says which words are scored. Deterministic text, no second model
    # call, and the budget does not move -- only what is scored against it.
    seed = context_builder.seed_text(task, step=plan_step, goal=goal, criteria=criteria,
                                     accepts=accepts)
    # Which files arrive, in what shape, and what each costs the window is `context_builder.build`'s
    # rule. The loop appends what it returns and records each piece under the kind it came as: a whole
    # snapshot is a file that arrived, an excerpt is a file that did not. Neither is a read -- the
    # auto-read path below still charges the run one turn to open a file properly.
    for piece in context_builder.build(ws=ws, settings=settings, rows=rows, seed=seed,
                                       used_chars=used, observed_count=len(observed),
                                       reference=reference, index=_proj_index):
        base[1]["content"] += piece.block
        if piece.kind == context_builder.EXCERPT:
            event(session, "context_excerpt", path=piece.path, lines=piece.lines,
                  from_line=piece.from_line, truncated=piece.truncated, why=piece.reason,
                  symbol=piece.symbol)
        else:
            event(session, "context_file", path=piece.path, sha256=piece.sha256,
                  why=piece.reason, symbol=piece.symbol)
        named.append({"path": piece.path, "why": piece.reason, "symbol": piece.symbol})
    if named:
        announce("context_files", count=len(named), names=named)
    counters = resumed.get("counters") if isinstance(resumed.get("counters"), dict) else {}
    failures = int(counters.get("failures") or 0)
    blocked_retries = int(counters.get("blocked_retries") or 0)
    recoverable = str(counters.get("recoverable") or "")
    last_error = str(counters.get("last_error") or "")
    # The action name is read by the fast/strong choice at the top of every turn, and an ask this loop
    # cut short for a steering instruction never reaches the line that normally sets it. Started here so
    # the interrupted turn asks the same question as a resumed one: nothing gathered yet.
    name = ""
    # The identical-reply guard restarts on a resumed run: it counts answers seen in one sitting, and a
    # person who closed the app and came back is in a new sitting by definition.
    repeated = {}
    # A user who raises the request timeout for a slow local model has asked for
    # patience, so the whole-task budget follows it instead of staying fixed.
    budget_seconds = max(1200, settings.timeout_seconds * 3)
    started = time.monotonic() - float(resumed.get("elapsed") or 0)
    start_turn = int(resumed.get("turn") or 0)
    # Everything the prompt says that does not change between turns, frozen once the retrieval
    # snapshots have been appended. The task state rides at the end and is rewritten each turn:
    # a record that went stale after turn one would just re-create the drift it exists to fix.
    base_template = base[1]["content"]
    state_budget = context_builder.state_budget(settings, len(base[0]["content"]),
                                                len(base_template) + 4)
    # Built once per run, not per turn. The context carries this loop's own `observed` dict, so a
    # read a tool performs is the same read that later authorises a proposal against that file, and
    # `rows` is handed in rather than re-indexed because the retrieval above has just paid for it
    # and the two must start from one answer. The verdict is named here, not left to the context's
    # default, because this loop is the task-originated execution boundary `policy` asks to have
    # checked: every tool call below passes the gate with it, and the table is the answer the classes
    # these tools run under carry — no folder declares them, and none can.
    tool_context = tools.ToolContext(ws=ws, settings=settings, observed=observed, rows=rows,
                                     verdict=policy.decide, trace_id=run_id,
                                     record=lambda kind, **fields: event(session, kind, **fields),
                                     announce=announce, progress=progress)

    def take_steering(turn: int) -> None:
        """Read what the operator said since the last checkpoint, and make it part of this run.

        Said three times on purpose, because each is read by a different party. It joins the history, so
        the model is asked with the instruction in front of it in its own words; it joins the continuity
        record's binding constraints, so a turn the context later trims cannot take the instruction with
        it; and it joins the session's own steering list and the event stream, so the person who typed it
        can see the run obey it and find the row again after the fact. Nothing here renumbers the turns,
        clears the history or re-reads the repository: the run continues where it stood.
        """
        drainer = getattr(steering, "drain", None)
        if drainer is None:
            return
        for row in drainer():
            text = str(row.get("text") or "").strip()[:MAX_STEER_CHARS]
            if not text:
                continue
            row["text"] = text
            row["turn"] = turn + 1
            row["read"] = now()
            session["steering"].append(row)
            taskstate.merge(state, {"constraints": [text]})
            history.append({"role": "user",
                            "content": "Operator steering (binding instruction from the user, "
                                       "given mid-run): " + text})
            event(session, "steer", id=str(row.get("id") or ""), text=text, turn=turn + 1,
                  urgent=bool(row.get("urgent")))
            announce("steer", detail=text, count=turn + 1)
            # Written down as it is taken: an instruction read mid-run is a fact about the run, and a
            # process that dies on the ask that followed must not take the record of it with it.
            atomic_json(path, session)

    try:
        for turn in range(start_turn, settings.max_turns):
            if stopped():
                raise Cancelled("Planning cancelled; no project files changed.")
            # One reading per turn: the budget check and the turn's own record must agree about how
            # long the run has been going, and a second clock call per turn is a second tick in tests
            # that fake the clock to make a slow model exhaust the budget on purpose.
            elapsed = time.monotonic() - started
            if elapsed > budget_seconds:
                raise AgentError("Task time budget exhausted.")
            # The steering checkpoint: everything said while the last ask was in flight is read here,
            # before this turn's prompt is assembled, so it arrives as an instruction and not a retry.
            take_steering(turn)
            taskstate.note_files(state, observed)
            task_block = taskstate.block(state, state_budget)
            base[1]["content"] = (base_template + ("\n" + task_block if task_block else "")).rstrip()
            while history and sum(len(m["content"]) for m in base + history) > settings.context_chars:
                for dropped in history[:2]:
                    taskstate.fold_turn(state, dropped)
                history = history[2:]
            if sum(len(m["content"]) for m in base + history) > settings.context_chars:
                raise AgentError("Initial context exceeds the " + str(settings.context_chars)
                                 + "-character budget; narrow the task or raise `context_chars`.")
            # The fast/strong split, only when the caller built both: plain gathering after the
            # first turn runs on the fast model; planning (the first turn), recovery from a
            # rejection, and repair rounds stay on the strong one. Nothing configured → today's
            # behaviour, one model for everything.
            active = provider
            if (turn > start_turn and not last_error and name in tools.GATHER_ACTIONS
                    and fast_provider is not None):
                active = fast_provider
            elif ((turn == start_turn or last_error or int(session.get("fix_round") or 0) > 0)
                  and strong_provider is not None):
                active = strong_provider
            progress(f"Turn {turn + 1}/{settings.max_turns}: asking {active.model}...")
            gen_kwargs = {}
            if on_token is not None and getattr(active, "supports_stream", False):
                gen_kwargs["on_token"] = on_token
            try:
                raw = active.generate(base + history, cancelled=stop_or_steer, **gen_kwargs)
            except TypeError:
                raw = active.generate(base + history, **gen_kwargs)
            except Cancelled:
                # The provider cuts its own ask short on the predicate it was handed, so the whole
                # question is who asked for it. A stop ends the run as it always did; an ask broken by
                # an urgent instruction nobody has read yet is an interruption this loop caused on
                # purpose, and the half answer it left is worth nothing. The instruction is taken now
                # and the run re-asks: steering that made the person wait for a slow model to finish
                # would have arrived after the run rather than during it.
                if stopped() or not stop_or_steer():
                    raise
                take_steering(turn)
                continue
            # A reasoning model answered twice and only one of the two is the action. The thought is
            # shown, capped and redacted, as its own collapsible row — never folded into the envelope and
            # never sent back as history, because the next turn does not need to re-read the deliberation.
            thought = str(getattr(active, "reasoning", "") or "")
            if thought:
                announce("model_reasoning", count=len(thought), detail=thought)
            counted = metrics_of(active)
            if counted:
                # Summed over the task's turns, because the number worth having is what one task cost.
                # A provider that reports nothing writes no key at all: an audit that finds `metrics`
                # missing can then tell "nobody measured" from "it was free", which a 0 could not.
                spent = session.setdefault("metrics", {})
                for field, value in counted.items():
                    spent[field] = spent.get(field, 0) + value
            if stopped():
                raise Cancelled("Planning cancelled; no project files changed.")
            repeated[raw] = repeated.get(raw, 0) + 1
            if repeated[raw] >= 3:
                # Three copies of the same reply, and the history said only that they were the same.
                # What the user needed — and what the next model choice depends on — is the refusal
                # each copy got, which was in scope one turn earlier and thrown away.
                raise AgentError("Model repeated the same action without progress"
                                 + (": every copy was refused with " + last_error[:150] if last_error else "")
                                 + "; try another model or a narrower task.")
            last_error = ""
            session["model"] = active.model
            state_delta, name, rationale = None, "", ""
            try:
                # The dialects a decision arrives in are settled before a field of it is read: what
                # the model nested under `args` is lifted into the envelope, and the sentence that
                # explains the choice is taken out from under `reason` or `thought`. Each is the
                # asking the prompt made, said the way a function-calling model says it, and refusing
                # one teaches nothing — three refusals of it end the run on the invalid-action budget.
                action = contracts.unwrap_args(parse_action(raw))
                rationale = contracts.pop_rationale(action)
                # The optional continuity envelope: taken out before validation so the strict
                # per-action field checks never see it, and a malformed one is ignored rather
                # than charged — the state aids continuity, it is not part of the contract.
                state_delta = action.pop("state", None) if isinstance(action, dict) else None
                if isinstance(state_delta, dict):
                    taskstate.merge(state, state_delta)
                name = action.get("action")
                if name == "propose" and {"action", "changes"} <= set(action) <= {
                        "action", "summary", "checks", "changes"}:
                    summary = action.get("summary", "")
                    checks = action.get("checks", [])
                    if (not isinstance(summary, str) or len(summary) > 4000
                            or not isinstance(checks, list) or len(checks) > 10
                            or any(not isinstance(c, str) or not 1 <= len(c) <= 500 for c in checks)):
                        raise PolicyError("Proposal needs a short summary and 1–10 verification descriptions.")
                    changes = prepare_changes(ws, action["changes"], observed)
                    if reference and any(ws.path(change["path"]) == ws.path(reference["path"]) for change in changes):
                        raise PolicyError("The attached plan is read-only for this task; propose implementation files only.")
                    session.update(summary=summary or NO_SUMMARY, checks=checks or list(DEFAULT_CHECKS),
                                   changes=changes,
                                   state="WAITING_APPROVAL")
                    session["proposal_hash"] = proposal_hash(session)
                    # The files are written down as a proposal at this instant, which is what `implement`
                    # means here: nothing has touched the project, and the change exists as an offer.
                    record_stage(session, "implement")
                    event(session, "proposal", hash=session["proposal_hash"])
                    # Deliberately after the hash: what a proposal breaks elsewhere is an answer about
                    # the repository, not part of what is being approved, and it must never change the
                    # number a person typed to approve it.
                    session["impact"] = impact_for(ws, rows, changes)
                    record_stage(session, "impact")
                    event(session, "impact", files=len(session["impact"].get("files") or []),
                          unknown=len(session["impact"].get("unknown") or []))
                    announce("propose", count=len(changes),
                             names=[change["path"] for change in changes])
                    record_stage(session, "review")
                    state["status"] = "proposal waiting for approval"
                    # Saved after the announcement, not before: `announce` appends the proposal's own
                    # step row to this record, and a task reopened from history must not lose the one
                    # row that says what was offered.
                    atomic_json(path, session)
                    return path
                elif name == "blocked" and set(action) == {"action"}:
                    # The explanation was lifted out of the envelope before validation, so a model
                    # that gave it as `thought` blocked as properly as one that used the name the
                    # prompt asked for. What is left to judge is the sentence, not the key it came in.
                    reason = rationale
                    if not 1 <= len(reason) <= 1000:
                        raise PolicyError("A blocked action requires a short reason.")
                    if recoverable and blocked_retries < MAX_BLOCKED_RETRIES:
                        blocked_retries += 1
                        result = {"note": recoverable}
                        event(session, "blocked_retried", attempt=blocked_retries)
                        progress("The model blocked on a recoverable observation; asking it once more.")
                    else:
                        # The chat gets one row for this, not two: the reason is announced here for
                        # the strip and the log, and the failure line the caller raises carries the
                        # remedy. Both read from the same string.
                        progress(labels.step_line(arabic, "blocked", reason=reason))
                        raise AgentError("Model could not produce a proposal: " + reason)
                elif name == "complete" and set(action) <= {"action", "summary"}:
                    # The third way a run ends: nothing to write. The task was already satisfied on
                    # disk, or it was never a code change; the summary is the whole artifact, so it
                    # is validated the way a blocked reason is and stored the way a proposal summary
                    # is. No stage past "review" is walked — there is no change to implement or
                    # verify, and a record that claims otherwise would lie about an empty diff.
                    summary = action.get("summary", "")
                    if not isinstance(summary, str) or len(summary) > 1000:
                        raise PolicyError("A complete action takes at most a short summary.")
                    session["summary"] = summary or "The task needs no file changes."
                    session["state"] = "COMPLETED"
                    event(session, "completed", summary=session["summary"][:200])
                    announce("complete", detail=session["summary"])
                    record_stage(session, "review")
                    state["status"] = "task complete — no changes needed"
                    atomic_json(path, session)
                    return path
                else:
                    # The fetching actions are defined in `tools`, which also owns this
                    # refusal: an envelope that names one of them with the wrong fields lands here
                    # too, and "invalid fields" is true of a dozen different mistakes, so the shape
                    # the model actually sent is echoed back beside the whole vocabulary. The policy
                    # verdict is asked inside the same call, before any handler runs, so a class the
                    # table refuses arrives at the rejection path below like any other refusal.
                    outcome = registry.run(action, tool_context)
                    result, recoverable = outcome.data, outcome.advice
                    # What the tool's own run established — a suite's verdict, a tree's cleanliness —
                    # is folded into the continuity record here, not only into the history. The
                    # trim drops the turn that carried the full output; the record keeps the fact,
                    # and the model's next prompt reads it from the state block the way it reads
                    # its own entries. Merged through the same `taskstate.merge` as the model's
                    # optional state envelope, so the caps and the redactor are the ones already
                    # spent on model-written lists.
                    if outcome.state:
                        taskstate.merge(state, outcome.state)
                    # A symbol tool rebuilt the index in order to answer, and `propose` reads those
                    # same rows for its impact report. Taking the copy back is what stops a proposal
                    # from reasoning over the index the turn started with.
                    rows = tool_context.rows
            except (ValueError, TypeError, PolicyError, contracts.ContractError,
                    OSError) as exc:
                failures += 1
                if failures > 3:
                    raise AgentError("Model exceeded the invalid-action budget.") from exc
                result = {"error": str(exc)[:300]}
                # The taxonomy code rides beside the sentence so a reader can colour the row — or
                # decide whether a retry could ever land differently — without matching prose.
                code = getattr(exc, "code", "")
                if code:
                    result["code"] = code
                last_error = result["error"]
                recoverable = refusals.advice_for(result["error"], task)
                stale = STALE_READ.match(result["error"])
                if stale:
                    try:
                        item = ws.read(stale.group(1))
                    except (AgentError, OSError):
                        item = None
                    if item is not None and len(item["content"]) <= settings.context_chars // 2:
                        observed[item["path"]] = item["sha256"]
                        recoverable = ("Propose again with the complete current content of "
                                       + item["path"] + " from the read below, with your change applied.")
                        result = {**result, "read": item, "next_action": recoverable}
                        event(session, "auto_read", path=item["path"], sha256=item["sha256"])
                        progress("Read " + item["path"]
                                 + " for the model; it can now propose against the current content.")
                # The remedy was computed and then dropped: only the stale-read branch below ever put
                # it into the observation, so on every other rejection the model was told what was
                # wrong and nothing about what to do next — which is how a small model ends up
                # proposing the same bytes until the loop guard stops it.
                result.setdefault("next_action", recoverable)
                # The reason is the tool's own sentence, capped and redacted the same way D3 made
                # the provider's. A rejected action recorded without it leaves a BLOCKED task whose
                # history says only that something was refused — which is the reading the user comes
                # back to after a long run, and it explains nothing.
                event(session, "rejected_action", reason=redact(result["error"])[:180])
            # Never execute tool commands or persist raw prompts/model output in events.
            if not (isinstance(state_delta, dict) and state_delta.get("status")):
                state["status"] = (str(name)[:80] if name else
                                   "rejected: " + last_error[:80])
            history.extend([{"role": "assistant", "content": raw[:100000]},
                            {"role": "user", "content": "Tool observation (untrusted): " + json.dumps(result)}])
            atomic_json(path, session)
            save_turns(turns_file(path), run_id=run_id, turn=turn + 1, history=history,
                       observed=observed,
                       counters={"failures": failures, "blocked_retries": blocked_retries,
                                 "recoverable": recoverable, "last_error": last_error},
                       elapsed=elapsed)
        raise AgentError("Turn budget exhausted; no changes were made.")
    except (AgentError, OSError, KeyboardInterrupt) as exc:
        session["state"] = "CANCELLED" if isinstance(exc, (Cancelled, KeyboardInterrupt)) else "BLOCKED"
        # The state alone was the whole record, which made a blocked task unreadable afterwards:
        # "BLOCKED" and a row of blank rejections, with the reason held only in the live window. The
        # history replay reads session["error"] for exactly this moment, and it had never been set.
        session["error"] = redact(str(exc))[:300] or type(exc).__name__
        event(session, "stopped", reason=redact(str(exc))[:180] or type(exc).__name__)
        atomic_json(path, session)
        raise
    finally:
        # A provider that started a process for this run closes it here, whatever ended the run — a
        # proposal, a refusal, or a cancelled turn. The loop never owns an outside child beyond the
        # run that asked for its tools, and a child that outlives the task that spawned it is a
        # process nobody can attribute to anything in the session record.
        registry.shutdown()
        release_run_lock(run_dir)


def diff_size(changes: list) -> tuple[int, int, bool]:
    """(lines changed, lines the files already had, was every existing line replaced).

    The same computation `shrink_warning` makes, counted instead of judged: what a whole-file rewrite
    by a small model silently destroys is the lines it did not mention, and two files in this run lost
    their `package` line and their `public` modifier that way. The number is what makes the difference
    between a one-line fix and a fresh draft readable in the one row a user reads.
    """
    changed = total = 0
    swept = False
    for change in changes:
        before = [line.strip() for line in (change.get("before") or "").splitlines() if line.strip()]
        after = [line.strip() for line in (change.get("after") or "").splitlines() if line.strip()]
        kept = set(after)
        gone = sum(1 for line in before if line not in kept)
        added = sum(1 for line in after if line not in set(before))
        changed += gone + added
        total += len(before)
        swept = swept or bool(before) and gone >= len(before)
    return changed, total, swept


def shrink_warning(change: dict) -> str | None:
    """Describe a replacement that removes most of an existing file.

    Small local models rewrite whole files, and the damage that reaches a build most
    often is a manifest losing the dependencies it already declared. The proposal
    stays approvable; the reviewer is shown precisely what disappears.
    """
    before = change.get("before")
    if not before or change.get("delete"):
        # A delete is not a rewrite that lost lines: the removal is the request, and it is already
        # stated on the card. This warning is for the case where the file was meant to survive.
        return None
    original = [line.strip() for line in before.splitlines() if line.strip()]
    if len(original) < 8:
        return None
    kept = {line.strip() for line in (change["after"] or "").splitlines() if line.strip()}
    lost = [line for line in original if line not in kept]
    if len(lost) * 10 < len(original) * 6:
        return None
    examples = "; ".join(line[:70] for line in lost[:3])
    return (change["path"] + f" removes {len(lost)} of {len(original)} existing lines, including "
            + examples + ("…" if len(lost) > 3 else ""))


# The events that mean "this task has already looked at this file": the index chose it, the agent read
# it, the operator attached it as the plan, or a tool call named it.
REACHED_BY = {"context_file", "auto_read", "tool", "plan_attached"}


def reached_files(session: dict) -> set[str]:
    """Every path this task has touched, read back out of the record it leaves behind.

    Nothing new is stored to answer this: the session already carries one event per file it reached, so
    the check cannot drift from what actually happened during the turn.
    """
    seen = set()
    for item in (session or {}).get("events", []):
        if item.get("kind") in REACHED_BY and item.get("path"):
            seen.add(str(item["path"]).replace("\\", "/"))
    for name in refusals.PATH_IN_TASK.findall(str((session or {}).get("task", ""))):
        clean = str(name).strip("./").replace("\\", "/")
        if clean:
            seen.add(clean)
    return seen


def unrelated_files(session: dict) -> list[str]:
    """The proposed files this task never named, read, or had chosen for it.

    Only existing files are listed. A new file is not a surprise of the same kind — the artifact card
    already says "Created" and the task that asks for a feature expects a file it has never seen — while
    a rewrite of some other module's file, from a map the agent read and the operator did not, is exactly
    the diff nobody was expecting. Flagged, never refused: a fix that legitimately spans two files is
    ordinary, and a gate that blocked those would be trained away in a week.
    """
    seen = reached_files(session)
    out = []
    for change in (session or {}).get("changes", []):
        if change.get("before") is None or change.get("delete"):
            continue
        path = str(change.get("path", "")).replace("\\", "/")
        if any(path == item or path.endswith("/" + item) for item in seen):
            continue
        if path and path not in out:
            out.append(path)
    return out


def unexpected_notice(session: dict) -> str:
    """The approval dialog's line about the files nobody asked for, or "" when there are none."""
    if not session:
        return ""
    paths = unrelated_files(session)
    if not paths:
        return ""
    return ("Not named or read by this task: " + ", ".join(paths[:4])
            + (f" (+{len(paths) - 4} more)" if len(paths) > 4 else "")
            + ". The agent proposed these from the repository map alone; open one before approving "
              "if you did not mean to change it.\n\n")


def impact_for(ws: Workspace, rows: list[dict], changes: list[dict]) -> dict:
    """Open the files that could still name what this proposal removes, and ask them.

    Computed once, on the turn the proposal is made, and stored on the session: the answer reads the
    files as they are, so a card drawn after a person edited something by hand would otherwise show a
    different impact than the one that was approved. A failure here is recorded as an unknown rather
    than raised, because the proposal itself is already valid work and the review must not be lost to
    a read that could not happen.
    """
    try:
        paths, visible = impact.candidate_files(rows, [str(change.get("path", ""))
                                                       for change in changes])
        sources = []
        for name in paths:
            try:
                sources.append((name, ws.read(name)["content"]))
            except (AgentError, MissingFileError, OSError):
                continue
        return impact.analyze(changes, rows, sources, visible=visible)
    except (AgentError, PolicyError, OSError, ValueError, TypeError, KeyError):
        return {"files": [], "unknown": [{"key": "impact_unknown_failed"}],
                "searched": 0, "visible": 0}


def review(session: dict) -> str:
    rows = ["State: " + session["state"], "Workspace: " + session["root"],
            session.get("summary", "No proposal."), ""]
    for change in session.get("changes", []):
        rows.extend(difflib.unified_diff((change["before"] or "").splitlines(keepends=True),
                                        (change["after"] or "").splitlines(keepends=True),
                                        fromfile="before/" + change["path"],
                                        tofile="after/" + change["path"]))
    warnings = [note for note in (shrink_warning(change) for change in session.get("changes", []))
                if note]
    if warnings:
        rows.extend(["", "Check these removals before approving:",
                     *[line for note in warnings for line in ("  • " + note, "")]])
    findings = labels.impact_lines(session.get("impact") or {})
    if findings:
        rows.extend(["", labels.note("impact_heading"),
                     *[line for finding in findings for line in ("  • " + finding, "")]])
    stray = unrelated_files(session)
    if stray:
        rows.extend(["", "Files this task never named or read:",
                     *("  • " + path for path in stray)])
    rows.extend(["\nProposed checks (not executed):", *session.get("checks", []),
                 "Proposal SHA256: " + session.get("proposal_hash", "none")])
    return "\n".join(rows)


_RISK_REASON_TEXT = {
    "risk_reason_address_limited": "the target address is in a limited range",
    "risk_reason_address_public": "the target address is a public endpoint",
    "risk_reason_runs_later": "a changed file runs later, not just now",
    "risk_reason_configuration": "a changed file is configuration",
    "risk_reason_leaves_machine": "the action sends something outside this machine",
    "risk_reason_irreversible": "the action is destructive and hard to reverse",
    "risk_reason_sensitive_domain": "the task or a changed file belongs to a sensitive domain",
    "risk_reason_many_files": "the proposal changes many files at once",
}


def plan_mode_view(session: dict) -> dict:
    """The whole plan as five read-only sections: goal, files, steps, risks, checks.

    Every section is derived from the session record alone — nothing here opens a write or asks a
    provider. `review()` shows *what the bytes change* (the diff); this shows *what the run intends*
    (the plan a person approves before any diff matters), which is why the risk band and the proposed
    checks are re-read from `risk_policy` and the session's own ledger rather than stored twice.
    """
    state = session.get("task_state") or {}
    goal = str(session.get("goal") or state.get("goal") or session.get("task") or "")
    changes = [row for row in (session.get("changes") or []) if isinstance(row, dict)]
    affected = [{"path": str(row.get("path") or ""),
                 "action": ("delete" if row.get("delete")
                            else "create" if not row.get("before") else "modify")}
                for row in changes]
    steps: list[str] = []
    reference = session.get("plan_reference") or {}
    if reference.get("path"):
        steps.append("Attached plan (read-only): " + str(reference["path"]))
    if isinstance(session.get("plan_step"), int):
        steps.append(f"Implementing step {session['plan_step']} of the attached plan.")
    # What the operator said while the run was already going is part of the plan the remaining steps
    # follow, so the read-only view says it back in their words rather than leaving it in the log.
    for row in session.get("steering") or []:
        if isinstance(row, dict) and row.get("text"):
            steps.append(f"Steered at turn {row.get('turn', '?')}: {row['text']}")
    steps.extend(str(item) for item in state.get("decisions") or [])
    if session.get("summary"):
        steps.append(str(session["summary"]))
    if state.get("next_step"):
        steps.append("Next: " + str(state["next_step"]))
    assessment = risk_policy.assess_changes(changes, str(session.get("task") or ""))
    risks = ["Risk level: " + assessment.risk.value]
    if assessment.verdict in (policy.ASK, policy.DENY):
        risks.append(f"Approval is {assessment.verdict.upper()}-controlled for this class of change.")
    risks.extend(_RISK_REASON_TEXT.get(code, code) for code in assessment.reasons)
    for row in changes:
        warning = shrink_warning(row)
        if warning:
            risks.append("Removal to check: " + warning)
    risks.extend(labels.impact_lines(session.get("impact") or {}))
    checks = list(session.get("checks") or [])
    checks.extend(str(item) for item in state.get("acceptance") or [])
    return {"goal": goal, "affected_files": affected, "steps": steps,
            "risks": risks, "test_strategy": checks}


def plan_mode_text(session: dict) -> str:
    """Plan Mode as one printed block: the five sections, and the way out of reading."""
    view = plan_mode_view(session)

    def counted(title: str, lines: list[str], empty: str) -> list[str]:
        return [title, *(lines or ["  • " + empty]), ""]

    rows = ["PLAN MODE — read-only view of the full plan. Nothing has been written.", ""]
    rows.append("Goal:")
    rows.append("  " + (view["goal"] or "(none recorded)"))
    rows.append("")
    files = [f"  • {row['action']}  {row['path']}" for row in view["affected_files"]]
    rows.extend(counted(f"Affected files ({len(files)}):", files, "(none — no proposal yet)"))
    steps = ["  • " + line for line in view["steps"]]
    rows.extend(counted(f"Steps ({len(steps)}):", steps, "(none recorded yet)"))
    risks = ["  • " + line for line in view["risks"]]
    rows.extend(counted(f"Risks ({len(risks)}):", risks, "nothing the policy flags"))
    checks = ["  • " + line for line in view["test_strategy"]]
    rows.extend(counted("Test strategy:", checks,
                        "no checks proposed — the proposal will claim nothing was verified"))
    rows.append("Proposal SHA256: " + session.get("proposal_hash", "none"))
    rows.append("No project files changed. Approve this plan, then apply it with: agent apply <session>.")
    return "\n".join(rows)


def proposal_rejected(session: dict) -> bool:
    """The latest decision for this exact proposal, shared by every surface."""
    wanted = session.get("proposal_hash")
    if not wanted:
        return False
    for item in reversed(session.get("events", [])):
        if item.get("hash") == wanted and item.get("kind") in {
            "proposal_rejected", "proposal_reopened",
        }:
            return item["kind"] == "proposal_rejected"
    return False


def reopen_proposal(path: Path, approved_hash: str) -> dict:
    """Explicitly reopen a declined proposal; this never applies its files."""
    session = load_session(path)
    if session["state"] != "WAITING_APPROVAL" or approved_hash != session.get("proposal_hash"):
        raise PolicyError("Reopening requires the matching pending proposal hash.")
    if not proposal_rejected(session):
        raise PolicyError("This proposal has not been declined.")
    event(session, "proposal_reopened", hash=approved_hash)
    atomic_json(path, session)
    return session


def reject_proposal(path: Path, approved_hash: str) -> dict:
    """Decline a pending proposal: record the refusal, write nothing.

    The mirror of `reopen_proposal`. A decline belongs to one proposal hash and must be a pending
    answer, so the same guards that let a real approval through also keep a stale or forged hash from
    quietly marking some other proposal as refused.
    """
    session = load_session(path)
    if session["state"] != "WAITING_APPROVAL" or approved_hash != session.get("proposal_hash"):
        raise PolicyError("Rejecting requires the matching pending proposal hash.")
    if proposal_rejected(session):
        raise PolicyError("This proposal has already been declined.")
    event(session, "proposal_rejected", hash=approved_hash)
    atomic_json(path, session)
    return session


def apply_proposal(path: Path, approved_hash: str) -> dict:
    session = load_session(path)
    if session["state"] != "WAITING_APPROVAL" or approved_hash != session.get("proposal_hash"):
        raise PolicyError("Approval must match the pending proposal hash.")
    if proposal_rejected(session):
        raise PolicyError("This proposal was declined. Reopen it for review before applying it.")
    ws = Workspace(Path(session["root"]))
    reference = session.get("plan_reference")
    if reference and ws.read(reference["path"])["sha256"] != reference["sha256"]:
        raise PolicyError("The attached plan changed since review; generate a new proposal.")
    # Preflight all files before the first write.
    for change in session["changes"]:
        target = ws.path(change["path"], writable=True)
        actual = ws.read(change["path"])["sha256"] if target.exists() else None
        if actual != change["before_hash"]:
            raise PolicyError("Workspace changed since planning; regenerate the proposal.")
    session["state"] = "APPLYING"
    record_stage(session, "approve")
    event(session, "approved", hash=approved_hash)
    atomic_json(path, session)
    written: list[str] = []
    removed: list[str] = []
    try:
        for change in session["changes"]:
            if change.get("delete"):
                # Through the workspace, so a removal is guarded by the same path rules and the same
                # since-review hash check a write is -- and not by an unlink() that trusts the session.
                ws.remove(change["path"], change["before_hash"])
                removed.append(change["path"])
                event(session, "removed", path=change["path"], sha256=change["before_hash"])
            else:
                ws.write(change["path"], change["after"], change["before_hash"])
                # The bytes are on disk from this line, and this record is only a note about them.
                written.append(change["path"])
                event(session, "written", path=change["path"], sha256=change["after_hash"])
            atomic_json(path, session)
    except (OSError, AgentError) as exc:
        session["state"] = "PARTIAL_APPLY"
        try:
            atomic_json(path, session)
        except OSError:
            # Both writes of the same locked file fail the same way, and the second one failing
            # used to replace the first: the raw WinError reached the status line, the session
            # stayed `APPLYING`, and clicking Apply again answered "Approval must match the
            # pending proposal hash" — three true statements that together describe nothing.
            pass
        done = (" Already written: " + ", ".join(written + removed) + "." if (written or removed)
                else " No file was written.")
        raise AgentError("Apply interrupted: " + redact(str(exc))[:120] + "." + done +
                         " This task is left unfinished; review shows what is on disk. "
                         "Nothing is replayed automatically." +
                         (" Rollback undoes the files named above." if (written or removed)
                          else "")) from None
    session["state"] = "APPLIED_UNVERIFIED"
    # The project remembers what its own tasks changed, outside the folder the model can write to.
    # Recorded after the writes, because a change that is only remembered and not made is worse than
    # one that is neither: `note_task` swallows its own failures for exactly that reason.
    memory_module.note_task(memory_module.memory_dir_for(path.parent.parent),
                            session["root"], session)
    compass.note_run_for(session["root"], session)
    atomic_json(path, session)
    return session


def rollback(path: Path, approved_hash: str) -> dict:
    session = load_session(path)
    if approved_hash != session.get("proposal_hash") or session["state"] not in {
        "APPLIED_UNVERIFIED", "PARTIAL_APPLY", "APPLYING", "CHECKS_PASSED",
        "VERIFICATION_FAILED", "VERIFICATION_BLOCKED",
    }:
        raise PolicyError("Rollback requires the matching hash and an applied/interrupted session.")
    ws = Workspace(Path(session["root"]))
    pending = []
    for change in session["changes"]:
        target = ws.path(change["path"], writable=True)
        actual = ws.read(change["path"])["sha256"] if target.exists() else None
        if actual == change["before_hash"]:
            continue
        if actual != change["after_hash"]:
            raise PolicyError("Rollback would overwrite a later edit: " + change["path"])
        pending.append(change)
    session["state"] = "PARTIAL_APPLY"
    atomic_json(path, session)
    for change in pending:
        if change["before"] is None:
            target = ws.path(change["path"], writable=True)
            if ws.read(change["path"])["sha256"] != change["after_hash"]:
                raise PolicyError("Concurrent change; rollback stopped.")
            target.unlink()
        else:
            ws.write(change["path"], change["before"], change["after_hash"])
        event(session, "rolled_back_file", path=change["path"])
        atomic_json(path, session)
    session["state"] = "ROLLED_BACK"
    event(session, "rolled_back")
    # Back to the offer, because the offer is all that is left on disk: the files it described are gone.
    record_stage(session, "review")
    # The memory of the change is corrected rather than left claiming work that is no longer on disk.
    memory_module.note_task(memory_module.memory_dir_for(path.parent.parent),
                            session["root"], session, status="rolled_back")
    compass.note_run_for(session["root"], session, status="rolled_back")
    atomic_json(path, session)
    return session
