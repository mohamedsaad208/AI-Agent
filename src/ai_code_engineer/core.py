"""Agent Core: Deterministic flow, typed interfaces, and state machine.

Implements the fundamental engineering contracts:
    Task, Plan, Action, Tool, Result, VerificationResult, AgentState

Deterministic lifecycle:
    User Request
         ↓
    Task Intake
         ↓
    Understand Request
         ↓
    Inspect Project
         ↓
    Create Plan
         ↓
    Human Approval
         ↓
    Apply Changes
         ↓
    Verify
         ↓
    Final Report
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import uuid

from .errors import AgentError, PolicyError


class AgentStatus(str, Enum):
    ANALYZING = "ANALYZING"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    FIXING = "FIXING"
    DONE = "DONE"
    FAILED = "FAILED"


# Valid transitions between states in the deterministic lifecycle
ALLOWED_TRANSITIONS: dict[AgentStatus, set[AgentStatus]] = {
    AgentStatus.ANALYZING: {AgentStatus.PLANNING, AgentStatus.FAILED},
    AgentStatus.PLANNING: {AgentStatus.WAITING_APPROVAL, AgentStatus.ANALYZING, AgentStatus.FAILED},
    AgentStatus.WAITING_APPROVAL: {AgentStatus.EXECUTING, AgentStatus.PLANNING, AgentStatus.FAILED},
    AgentStatus.EXECUTING: {AgentStatus.VERIFYING, AgentStatus.FAILED},
    AgentStatus.VERIFYING: {AgentStatus.DONE, AgentStatus.FIXING, AgentStatus.FAILED},
    AgentStatus.FIXING: {AgentStatus.PLANNING, AgentStatus.EXECUTING, AgentStatus.VERIFYING, AgentStatus.FAILED},
    AgentStatus.DONE: set(),
    AgentStatus.FAILED: {AgentStatus.ANALYZING, AgentStatus.PLANNING},  # Allow restart
}


# ---------------------------------------------------------------------------
# The workflow axis: where a run stands, which is not the same question as what happened to it.
#
# `AgentStatus` above is the lifecycle this module drives, and `labels.STATES` is the outcome a session
# file records (`APPLIED_UNVERIFIED`, `CHECKS_PASSED`) — the one buttons are enabled from. Neither answers
# "how far along is this", which is what a person watching a long task asks. The two vocabularies share
# exactly one string, `WAITING_APPROVAL`, and disagree about everything else, so the stage is a third axis
# stored on the session as `stage`, and this table is the only place that says which move counts as one.
STAGES = ("understand", "plan", "implement", "impact", "review", "approve", "build_test", "verify")

# Every row written out, including the odd ones, so a stage added later cannot inherit an answer by
# accident: `review` may go to `verify` because a static check runs before anybody approves; `build_test`
# and `verify` may go back to `review` because a roll put the proposal back on the table; `approve` may
# return there for the same reason after the files went back.
#
# The empty key is a session that never recorded one, and it accepts any stage: a run built from an attached
# plan file opens at `review` without passing through the four steps before it, and saying otherwise would
# make the record lie about a thing that simply already happened.
STAGE_MOVES: dict[str, set[str]] = {
    "": set(STAGES),
    "understand": {"plan"},
    "plan": {"implement", "review", "build_test", "verify"},
    "implement": {"impact", "review", "plan"},
    "impact": {"review", "implement"},
    "review": {"approve", "implement", "build_test", "verify"},
    "approve": {"build_test", "implement", "review"},
    "build_test": {"verify", "implement", "review"},
    "verify": {"build_test", "implement", "review"},
}


def stage_allowed(current: str, target: str) -> bool:
    """Whether a run may say it moved from one stage to the other.

    An unrecognised current stage answers False, the way `policy.decide` does: a record that cannot be read
    is not evidence that anything is permitted, and a stage nobody recognises is better left unstated than
    renamed by a guess.
    """
    return str(target or "") in STAGE_MOVES.get(str(current or ""), set())


def stage_of(status: AgentStatus) -> str:
    """The workflow stage a lifecycle status stands in, for the one path that has both."""
    return {
        AgentStatus.ANALYZING: "understand",
        AgentStatus.PLANNING: "plan",
        AgentStatus.WAITING_APPROVAL: "review",
        AgentStatus.EXECUTING: "approve",
        AgentStatus.VERIFYING: "build_test",
        AgentStatus.FIXING: "implement",
        AgentStatus.DONE: "verify",
        AgentStatus.FAILED: "review",
    }[status]


@dataclass
class Task:
    description: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project_root: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            description=str(data.get("description", "")),
            id=str(data.get("id") or uuid.uuid4().hex),
            project_root=str(data.get("project_root", "")),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Plan:
    task_id: str
    summary: str
    steps: list[str] = field(default_factory=list)
    rationale: str = ""
    target_files: list[str] = field(default_factory=list)
    verification_checks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        return cls(
            task_id=str(data.get("task_id", "")),
            summary=str(data.get("summary", "")),
            steps=list(data.get("steps") or []),
            rationale=str(data.get("rationale", "")),
            target_files=list(data.get("target_files") or []),
            verification_checks=list(data.get("verification_checks") or []),
        )


@dataclass
class Action:
    name: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Action:
        return cls(
            name=str(data.get("name", "")),
            parameters=dict(data.get("parameters") or {}),
        )


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]
    requires_approval: bool = False

    def execute(self, **kwargs) -> Any:
        return self.handler(**kwargs)


@dataclass
class Result:
    success: bool
    data: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Result:
        return cls(
            success=bool(data.get("success")),
            data=data.get("data"),
            error=data.get("error"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class VerificationResult:
    passed: bool
    status: str
    command: str = ""
    exit_code: int = 0
    summary: str = ""
    failures: list[str] = field(default_factory=list)
    output_tail: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResult:
        return cls(
            passed=bool(data.get("passed")),
            status=str(data.get("status", "unknown")),
            command=str(data.get("command", "")),
            exit_code=int(data.get("exit_code", 0)),
            summary=str(data.get("summary", "")),
            failures=list(data.get("failures") or []),
            output_tail=str(data.get("output_tail", "")),
            details=dict(data.get("details") or {}),
        )


@dataclass
class AgentState:
    task: Task
    status: AgentStatus = AgentStatus.ANALYZING
    plan: Plan | None = None
    files_read: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    verification_status: str = "PENDING"
    proposal_hash: str = ""
    changes: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def transition_to(self, new_status: AgentStatus, reason: str = "") -> None:
        """Move state machine forward strictly according to allowed transitions."""
        if new_status == self.status:
            return
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise PolicyError(
                f"Illegal state transition from {self.status.value} to {new_status.value}."
            )
        previous = self.status
        self.status = new_status
        self.history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "from": previous.value,
            "to": new_status.value,
            "reason": reason,
        })

    def record_read(self, path: str) -> None:
        if path not in self.files_read:
            self.files_read.append(path)

    def record_change(self, path: str) -> None:
        if path not in self.files_changed:
            self.files_changed.append(path)

    def record_command(self, command: str) -> None:
        self.commands_run.append(command)

    def record_error(self, error: str) -> None:
        self.errors.append(error)

    def compute_proposal_hash(self) -> str:
        """Compute SHA-256 fingerprint for approval binding."""
        digest = hashlib.sha256()
        digest.update(self.task.description.encode("utf-8"))
        if self.plan:
            digest.update(self.plan.summary.encode("utf-8"))
        for change in sorted(self.changes, key=lambda c: str(c.get("path", ""))):
            digest.update(str(change.get("path", "")).encode("utf-8"))
            digest.update(str(change.get("after", "")).encode("utf-8"))
        self.proposal_hash = digest.hexdigest()
        return self.proposal_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "status": self.status.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "files_read": list(self.files_read),
            "files_changed": list(self.files_changed),
            "commands_run": list(self.commands_run),
            "errors": list(self.errors),
            "verification_status": self.verification_status,
            "proposal_hash": self.proposal_hash,
            "changes": list(self.changes),
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentState:
        raw_task = data.get("task")
        task = Task.from_dict(raw_task) if isinstance(raw_task, dict) else Task(description=str(raw_task or ""))
        raw_status = data.get("status", "ANALYZING")
        try:
            status = AgentStatus(raw_status)
        except ValueError:
            status = AgentStatus.ANALYZING
        raw_plan = data.get("plan")
        plan = Plan.from_dict(raw_plan) if isinstance(raw_plan, dict) else None

        return cls(
            task=task,
            status=status,
            plan=plan,
            files_read=list(data.get("files_read") or []),
            files_changed=list(data.get("files_changed") or []),
            commands_run=list(data.get("commands_run") or []),
            errors=list(data.get("errors") or []),
            verification_status=str(data.get("verification_status", "PENDING")),
            proposal_hash=str(data.get("proposal_hash", "")),
            changes=list(data.get("changes") or []),
            history=list(data.get("history") or []),
        )


class AgentCore:
    """Deterministic orchestrator managing task lifecycle and state transitions."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def intake(self, description: str, metadata: dict[str, Any] | None = None) -> AgentState:
        """Task Intake: Create initial state in ANALYZING status."""
        task = Task(description=description, project_root=str(self.project_root), metadata=metadata or {})
        state = AgentState(task=task, status=AgentStatus.ANALYZING)
        state.history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "TASK_INTAKE",
            "task_id": task.id,
        })
        return state

    def set_plan(self, state: AgentState, plan: Plan, changes: list[dict[str, Any]]) -> None:
        """Create Plan and prepare for approval."""
        if state.status not in (AgentStatus.ANALYZING, AgentStatus.PLANNING, AgentStatus.FIXING):
            raise PolicyError(f"Cannot set plan from state: {state.status.value}")
        
        if state.status == AgentStatus.ANALYZING:
            state.transition_to(AgentStatus.PLANNING, reason="Analysis completed, plan drafted")
        elif state.status == AgentStatus.FIXING:
            state.transition_to(AgentStatus.PLANNING, reason="Refining plan for fix round")

        state.plan = plan
        state.changes = changes
        state.compute_proposal_hash()
        state.transition_to(AgentStatus.WAITING_APPROVAL, reason="Plan and changes ready for human approval")

    def approve_and_execute(
        self,
        state: AgentState,
        approved_hash: str,
        executor: Callable[[list[dict[str, Any]]], list[str]],
    ) -> None:
        """Apply Changes: Requires matching proposal hash and applies changes deterministically."""
        if state.status != AgentStatus.WAITING_APPROVAL:
            raise PolicyError(f"Cannot execute changes from state: {state.status.value}. Must be WAITING_APPROVAL.")
        if approved_hash != state.proposal_hash:
            raise PolicyError("Approval hash mismatch: changes were modified after review.")

        state.transition_to(AgentStatus.EXECUTING, reason="Human approval received")
        try:
            modified_files = executor(state.changes)
            for f in modified_files:
                state.record_change(f)
            state.transition_to(AgentStatus.VERIFYING, reason="Changes applied to workspace")
        except Exception as exc:
            state.record_error(str(exc))
            state.transition_to(AgentStatus.FAILED, reason=f"Execution failed: {exc}")
            raise

    def record_verification(self, state: AgentState, verification: VerificationResult) -> None:
        """Verify: Evaluates build/test outcome and decides DONE vs FIXING vs FAILED."""
        if state.status != AgentStatus.VERIFYING:
            raise PolicyError(f"Cannot record verification from state: {state.status.value}. Must be VERIFYING.")

        state.record_command(verification.command)
        state.verification_status = "PASSED" if verification.passed else "FAILED"

        if verification.passed:
            state.transition_to(AgentStatus.DONE, reason="All checks passed successfully")
        else:
            state.record_error(verification.summary or "Verification checks failed")
            state.transition_to(AgentStatus.FIXING, reason="Verification failed; fix loop requested")

    def fail(self, state: AgentState, reason: str) -> None:
        """Explicitly transition to FAILED state."""
        state.record_error(reason)
        state.transition_to(AgentStatus.FAILED, reason=reason)
