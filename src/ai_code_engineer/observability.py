"""Production-Grade Agent Observability, Tracing, and Failure Diagnostics.

This module provides end-to-end tracing and correlation for autonomous agent execution:
- Structured event taxonomy (RUN_STARTED, TOOL_REQUESTED, FILE_CHANGED, etc.)
- Distributed trace & correlation IDs (trace_id, run_id, call_id)
- Automatic secret and credential redaction across all payloads
- Lightweight offline performance metrics (durations, step counts, tool failure rates)
- Compact Run Summaries and rich Failure Diagnostics for human explainability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import time
from typing import Any, Optional

from .redaction import redact


class EventType(str, Enum):
    """Standardized event taxonomy for agent lifecycle and tool activities."""
    RUN_STARTED = "RUN_STARTED"
    PLAN_CREATED = "PLAN_CREATED"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    FILE_CHANGED = "FILE_CHANGED"
    BUILD_STARTED = "BUILD_STARTED"
    BUILD_COMPLETED = "BUILD_COMPLETED"
    TEST_STARTED = "TEST_STARTED"
    TEST_COMPLETED = "TEST_COMPLETED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"


def _sanitize_data(value: Any) -> Any:
    """Recursively redact secrets and strings in arbitrary event payloads."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(k): _sanitize_data(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_data(x) for x in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_data(x) for x in value)
    return value


@dataclass
class TraceEvent:
    """An immutable, correlated trace event."""
    event_id: str
    trace_id: str
    run_id: str
    event_type: str
    step_index: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)
    error: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "step_index": self.step_index,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "payload": _sanitize_data(self.payload),
            "error": _sanitize_data(self.error) if self.error else None,
        }


@dataclass
class FailureDiagnostic:
    """Explanatory failure diagnostic answering what, where, why, and resume potential."""
    run_id: str
    step_index: int
    failed_tool: str
    error_code: str
    message: str
    why: str
    can_resume: bool
    checkpoint_available: bool = True
    suggested_fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunSummary:
    """Compact summary of a completed or failed agent execution."""
    run_id: str
    trace_id: str
    task: str
    status: str
    start_time: str
    end_time: str
    duration_seconds: float
    total_steps: int
    tools_used: list[str]
    files_changed: list[str]
    build_result: str = "not_run"
    test_result: str = "not_run"
    tool_failure_count: int = 0
    warnings: list[str] = field(default_factory=list)
    failure_diagnostic: Optional[FailureDiagnostic] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.failure_diagnostic:
            d["failure_diagnostic"] = self.failure_diagnostic.to_dict()
        return d

    def to_markdown(self) -> str:
        icon = "✅ SUCCESS" if self.status == "completed" else "❌ FAILED"
        lines = [
            f"# Run Execution Summary: `{self.run_id}` ({icon})",
            f"**Trace ID:** `{self.trace_id}`",
            f"**Task:** {self.task}",
            f"**Duration:** {self.duration_seconds:.2f}s across {self.total_steps} steps",
            "",
            "## Execution Profile",
            f"- **Tools Invoked:** {', '.join(sorted(set(self.tools_used))) or 'None'}",
            f"- **Tool Failures:** {self.tool_failure_count}",
            f"- **Files Modified:** {', '.join(self.files_changed) or 'None'}",
            f"- **Build Status:** {self.build_result}",
            f"- **Test Status:** {self.test_result}",
        ]
        if self.warnings:
            lines.extend(["", "### Warnings", *[f"- ⚠️ {w}" for w in self.warnings]])

        if self.failure_diagnostic:
            fd = self.failure_diagnostic
            resume_tag = "Yes" if fd.can_resume else "No"
            lines.extend([
                "",
                "### Failure Diagnostics",
                f"- **Failed Step:** {fd.step_index}",
                f"- **Tool / Action:** `{fd.failed_tool}`",
                f"- **Error Category:** `{fd.error_code}`",
                f"- **Reason:** {fd.why}",
                f"- **Can Resume Safely:** {resume_tag}",
            ])
            if fd.suggested_fix:
                lines.append(f"- **Suggested Action:** {fd.suggested_fix}")

        return "\n".join(lines)


class Tracer:
    """Manages correlated execution events and metrics for an agent run."""

    def __init__(self, run_id: str, trace_id: Optional[str] = None) -> None:
        self.run_id = run_id
        self.trace_id = trace_id or f"trc-{int(time.time()*1000)}"
        self.events: list[TraceEvent] = []
        self._start_time = time.perf_counter()
        self._start_iso = datetime.now(timezone.utc).isoformat()
        self._counter = 0

    def emit(
        self,
        event_type: EventType | str,
        step_index: int = 0,
        payload: Optional[dict[str, Any]] = None,
        duration_ms: float = 0.0,
        error: Optional[dict[str, Any]] = None,
    ) -> TraceEvent:
        """Record a correlated event with automatic secret redaction."""
        self._counter += 1
        t_type = event_type.value if isinstance(event_type, EventType) else str(event_type)
        ev_id = f"ev-{self._counter:04d}-{t_type.lower()}"

        event = TraceEvent(
            event_id=ev_id,
            trace_id=self.trace_id,
            run_id=self.run_id,
            event_type=t_type,
            step_index=step_index,
            duration_ms=duration_ms,
            payload=_sanitize_data(payload or {}),
            error=_sanitize_data(error) if error else None,
        )
        self.events.append(event)
        return event

    def generate_summary(
        self,
        task: str,
        status: str,
        changed_files: Optional[list[str]] = None,
        build_result: str = "not_run",
        test_result: str = "not_run",
        warnings: Optional[list[str]] = None,
        diagnostic: Optional[FailureDiagnostic] = None,
    ) -> RunSummary:
        """Produce a comprehensive, correlated summary of the run."""
        duration_sec = time.perf_counter() - self._start_time
        end_iso = datetime.now(timezone.utc).isoformat()

        tools_used = [
            str(e.payload.get("tool"))
            for e in self.events
            if e.event_type in (EventType.TOOL_REQUESTED.value, EventType.TOOL_COMPLETED.value)
            and e.payload.get("tool")
        ]
        tool_failures = sum(1 for e in self.events if e.event_type == EventType.TOOL_FAILED.value)
        steps = max((e.step_index for e in self.events), default=1)

        return RunSummary(
            run_id=self.run_id,
            trace_id=self.trace_id,
            task=redact(task),
            status=status,
            start_time=self._start_iso,
            end_time=end_iso,
            duration_seconds=round(duration_sec, 3),
            total_steps=steps,
            tools_used=tools_used,
            files_changed=[redact(f) for f in (changed_files or [])],
            build_result=build_result,
            test_result=test_result,
            tool_failure_count=tool_failures,
            warnings=[redact(w) for w in (warnings or [])],
            failure_diagnostic=diagnostic,
        )
