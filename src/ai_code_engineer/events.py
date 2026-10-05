"""Unified event schema for the AI Code Engineer UX.

Two dialects of the same idea already travel through the product: the engine records
session events (``{"at": ..., "kind": ..., **data}``) while the web controller streams
UI events (``{"kind": ..., "id": ..., **data}``). This module gives both one shape —
an immutable :class:`Event` that round-trips losslessly through either wire format —
so producers and consumers share a single vocabulary, validated against
:class:`EventKind` and redacted of secrets on the way in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import threading
from typing import Any, Callable, Mapping, Optional

from .redaction import redact


class EventLevel(str, Enum):
    NORMAL = "normal"
    VERBOSE = "verbose"
    DEBUG = "debug"


class Stage(str, Enum):
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    NOT_CHECKED = "NOT CHECKED"


class EventKind(str, Enum):
    """Every kind name the UX layer may emit, stream and session alike."""

    # Streamed to the browser (transport for UI state, dialogs and one-shot notices).
    BUSY = "busy"
    STATUS = "status"
    STATE = "state"
    LOG = "log"
    LOG_CHUNK = "log_chunk"
    MESSAGE = "message"
    CHAT = "chat"
    TOAST = "toast"
    VIEW = "view"
    PLAN = "plan"
    PROJECT = "project"
    SESSION = "session"
    TOKEN = "token"
    CONFIRM = "confirm"
    PICK = "pick"
    FOLDER = "folder"
    RETRACT = "retract"

    # Core UX Structured Events
    STAGE_CHANGED = "stage_changed"
    STEP_UPDATED = "step_updated"
    TOOL_CALL = "tool_call"
    FILE_CHANGED = "file_changed"
    TEST_RESULT = "test_result"
    APPROVAL_REQUESTED = "approval_requested"
    ERROR = "error"
    FINAL_REPORT = "final_report"

    # Recorded in the session log (what happened during a run).
    STAGE = "stage"
    STEP = "step"
    TOOL = "tool"
    RUN = "run"
    VERIFICATION = "verification"
    APPROVED = "approved"
    PROPOSAL = "proposal"
    PROPOSAL_REOPENED = "proposal_reopened"
    PROPOSAL_REJECTED = "proposal_rejected"
    REJECTED_ACTION = "rejected_action"
    WRITTEN = "written"
    REMOVED = "removed"
    ROLLED_BACK = "rolled_back"
    ROLLED_BACK_FILE = "rolled_back_file"
    COMPLETED = "completed"
    STOPPED = "stopped"
    STEER = "steer"
    IMPACT = "impact"
    CONTEXT_FILE = "context_file"
    CONTEXT_EXCERPT = "context_excerpt"
    AUTO_READ = "auto_read"
    AUTO_NOTES_ATTACHED = "auto_notes_attached"
    EVIDENCE_ATTACHED = "evidence_attached"
    MEMORY_ATTACHED = "memory_attached"
    PLAN_ATTACHED = "plan_attached"
    BLOCK_CHOSEN = "block_chosen"
    BLOCKED_RETRIED = "blocked_retried"
    TOOL_PROVIDER = "tool_provider"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {str(k): _sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(x) for x in value]
    return value


@dataclass(frozen=True)
class Event:
    """One immutable UX event: a kind, a timestamp, an optional request id, and data.

    ``to_dict``/``from_dict`` match the shape already stored in sessions and pushed
    to browsers: ``kind`` and ``at`` are reserved keys, ``id`` appears only for
    request/response dialogs, and everything else lives flat beside them.
    """

    kind: str
    at: str = field(default_factory=timestamp)
    id: Optional[str] = None
    data: Mapping[str, Any] = field(default_factory=dict)
    level: EventLevel = EventLevel.NORMAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", self.kind.value if isinstance(self.kind, EventKind) else str(self.kind))
        object.__setattr__(self, "level", self.level.value if isinstance(self.level, EventLevel) else str(self.level))
        object.__setattr__(self, "data", _sanitize(dict(self.data)))

    @property
    def known(self) -> bool:
        """Whether the kind belongs to the shared :class:`EventKind` vocabulary."""
        return self.kind in {k.value for k in EventKind}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"at": self.at, "kind": self.kind, **self.data}
        if self.id is not None:
            out["id"] = self.id
        if self.level != EventLevel.NORMAL.value:
            out["level"] = self.level
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Event":
        body = dict(raw)
        kind = str(body.pop("kind", ""))
        at = str(body.pop("at", timestamp()))
        request_id = body.pop("id", None)
        level = body.pop("level", EventLevel.NORMAL.value)
        try:
            level = EventLevel(str(level))
        except ValueError:
            level = EventLevel.NORMAL
        return cls(kind=kind, at=at, id=request_id, data=body, level=level)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, text: str) -> "Event":
        return cls.from_dict(json.loads(text))


def make(kind: EventKind | str, event_id: Optional[str] = None, **data: Any) -> Event:
    """Build a timestamped event from loose keyword data, the way callers already emit."""
    return Event(kind=kind, id=event_id, data=data)


def coerce(value: "Event | Mapping[str, Any]") -> Event:
    """Accept either an Event or a raw wire dict and return the unified shape."""
    if isinstance(value, Event):
        return value
    if hasattr(value, "to_event"):
        return value.to_event()
    return Event.from_dict(value)


# ---------------------------------------------------------------------------
# The 8 Concrete Core UX Event Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StageChanged:
    stage: str
    previous_stage: str = ""
    message: str = ""
    level: EventLevel = EventLevel.NORMAL
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.STAGE_CHANGED.value, at=self.at, data={
            "stage": self.stage, "previous_stage": self.previous_stage,
            "message": self.message, "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


@dataclass(frozen=True)
class StepUpdated:
    step_id: str
    action: str
    status: str = "running"  # "running", "done", "failed"
    details: Mapping[str, Any] = field(default_factory=dict)
    level: EventLevel = EventLevel.NORMAL
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.STEP_UPDATED.value, at=self.at, id=self.step_id, data={
            "step_id": self.step_id, "action": self.action, "status": self.status,
            "details": dict(self.details), "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    args: Mapping[str, Any] = field(default_factory=dict)
    output_summary: str = ""
    duration_ms: float = 0.0
    status: str = "ok"
    level: EventLevel = EventLevel.VERBOSE
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.TOOL_CALL.value, at=self.at, data={
            "tool_name": self.tool_name, "args": dict(self.args),
            "output_summary": self.output_summary, "duration_ms": self.duration_ms,
            "status": self.status, "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


@dataclass(frozen=True)
class FileChanged:
    path: str
    change_type: str = "modified"  # "created", "modified", "deleted"
    diff_summary: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    level: EventLevel = EventLevel.NORMAL
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.FILE_CHANGED.value, at=self.at, data={
            "path": self.path, "change_type": self.change_type,
            "diff_summary": self.diff_summary, "lines_added": self.lines_added,
            "lines_removed": self.lines_removed, "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


@dataclass(frozen=True)
class TestResult:
    command: str
    passed: bool
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    output_tail: str = ""
    level: EventLevel = EventLevel.NORMAL
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.TEST_RESULT.value, at=self.at, data={
            "command": self.command, "passed": self.passed,
            "total_tests": self.total_tests, "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests, "output_tail": self.output_tail,
            "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


@dataclass(frozen=True)
class ApprovalRequested:
    action: str
    risk_level: str = "medium"  # "low", "medium", "high", "critical"
    reason: str = ""
    choices: tuple[str, ...] = ("approve", "reject", "details")
    approval_id: str = ""
    level: EventLevel = EventLevel.NORMAL
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.APPROVAL_REQUESTED.value, at=self.at, id=self.approval_id, data={
            "action": self.action, "risk_level": self.risk_level,
            "reason": self.reason, "choices": list(self.choices),
            "approval_id": self.approval_id, "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


@dataclass(frozen=True)
class AgentProgressError:
    error_type: str
    message: str
    root_cause: str = ""
    suggested_action: str = ""
    recoverable: bool = True
    level: EventLevel = EventLevel.NORMAL
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.ERROR.value, at=self.at, data={
            "error_type": self.error_type, "message": self.message,
            "root_cause": self.root_cause, "suggested_action": self.suggested_action,
            "recoverable": self.recoverable, "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


@dataclass(frozen=True)
class FinalReport:
    task: str
    success: bool
    duration_seconds: float = 0.0
    files_changed: tuple[str, ...] = ()
    verification_status: str = VerificationStatus.NOT_CHECKED.value
    test_summary: str = ""
    remaining_risks: tuple[Mapping[str, Any], ...] = ()
    level: EventLevel = EventLevel.NORMAL
    at: str = field(default_factory=timestamp)

    def to_event(self) -> Event:
        return Event(kind=EventKind.FINAL_REPORT.value, at=self.at, data={
            "task": self.task, "success": self.success,
            "duration_seconds": self.duration_seconds,
            "files_changed": list(self.files_changed),
            "verification_status": self.verification_status,
            "test_summary": self.test_summary,
            "remaining_risks": [dict(r) for r in self.remaining_risks],
            "level": self.level.value,
        })

    def to_dict(self) -> dict[str, Any]:
        return self.to_event().to_dict()


# ---------------------------------------------------------------------------
# Thread-safe EventBus
# ---------------------------------------------------------------------------

class EventBus:
    """Thread-safe event bus distributing typed events to UI, CLI and Memory subscribers."""

    def __init__(self) -> None:
        self._subscribers: list[tuple[Callable[[Any], None], EventLevel]] = []
        self._lock = threading.Lock()
        self._history: list[Any] = []

    def subscribe(self, callback: Callable[[Any], None], min_level: EventLevel = EventLevel.NORMAL) -> None:
        with self._lock:
            self._subscribers.append((callback, min_level))

    def unsubscribe(self, callback: Callable[[Any], None]) -> None:
        """Stop ``callback`` receiving events — a view that has gone away must not be kept."""
        with self._lock:
            self._subscribers = [pair for pair in self._subscribers if pair[0] != callback]

    def emit(self, event: Any) -> None:
        level = getattr(event, "level", None)
        if level is None and isinstance(event, Mapping):
            level = event.get("level")
        if level is None:
            level = EventLevel.NORMAL
        if isinstance(level, str):
            try:
                level = EventLevel(level)
            except ValueError:
                level = EventLevel.NORMAL
        with self._lock:
            self._history.append(event)
            subs = list(self._subscribers)
        for fn, min_level in subs:
            if self._should_dispatch(level, min_level):
                try:
                    fn(event)
                except Exception:
                    pass

    @staticmethod
    def _should_dispatch(event_level: EventLevel, sub_min_level: EventLevel) -> bool:
        weights = {EventLevel.NORMAL: 1, EventLevel.VERBOSE: 2, EventLevel.DEBUG: 3}
        return weights.get(event_level, 1) <= weights.get(sub_min_level, 3)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
            self._history.clear()

    @property
    def history(self) -> list[Any]:
        with self._lock:
            return list(self._history)


global_bus = EventBus()

