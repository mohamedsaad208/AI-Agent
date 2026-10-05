"""Task Decomposition, Dependency DAG, and Controlled Replanning.

"Plan enough to control execution. Do not plan so much that planning becomes the work."

This module coordinates single-agent task decomposition:
1. Subtask Model: Each subtask specifies concrete objectives, dependencies, and explicit verification criteria.
2. Dependency DAG: Evaluates prerequisites, unlocking ready subtasks and blocking downstream tasks if a dependency fails.
3. Controlled Replanning: In case of new evidence or unexpected obstacles, revises pending subtasks
   while preserving the immutable ledger of completed work.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from .errors import AgentError, PolicyError
from .redaction import redact


class SubtaskStatus(str, Enum):
    """Execution lifecycle of a decomposed subtask."""
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class Subtask:
    """A single atomic unit of work in a decomposed plan."""
    subtask_id: str
    title: str
    objective: str
    dependencies: list[str] = field(default_factory=list)
    status: SubtaskStatus = SubtaskStatus.PENDING
    expected_result: str = ""
    verification_method: str = "run_tests"
    changed_files: list[str] = field(default_factory=list)
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "title": redact(self.title),
            "objective": redact(self.objective),
            "dependencies": self.dependencies,
            "status": self.status.value,
            "expected_result": redact(self.expected_result),
            "verification_method": self.verification_method,
            "changed_files": [redact(f) for f in self.changed_files],
            "error": redact(self.error) if self.error else None,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Subtask:
        valid = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid}
        if "status" in filtered and isinstance(filtered["status"], str):
            filtered["status"] = SubtaskStatus(filtered["status"])
        return cls(**filtered)


@dataclass
class PlanRevision:
    """Audit record capturing a controlled replanning event."""
    revision_id: str
    timestamp: str
    old_assumption: str
    new_evidence: str
    reason: str
    added_subtasks: list[str] = field(default_factory=list)
    removed_subtasks: list[str] = field(default_factory=list)


class TaskDAG:
    """Governs the execution sequence and dependencies of subtasks."""

    def __init__(self, task_name: str) -> None:
        self.task_name = task_name
        self.subtasks: dict[str, Subtask] = {}
        self.revisions: list[PlanRevision] = []

    def add_subtask(self, subtask: Subtask) -> None:
        self.subtasks[subtask.subtask_id] = subtask
        self._refresh_statuses()

    def get_ready_subtasks(self) -> list[Subtask]:
        """Returns all subtasks whose prerequisite dependencies are satisfied."""
        self._refresh_statuses()
        return [s for s in self.subtasks.values() if s.status == SubtaskStatus.READY]

    def _refresh_statuses(self) -> None:
        for s in self.subtasks.values():
            if s.status in (
                SubtaskStatus.COMPLETED,
                SubtaskStatus.FAILED,
                SubtaskStatus.IN_PROGRESS,
                SubtaskStatus.CANCELLED,
            ):
                continue

            # Check prerequisite dependencies
            deps_met = True
            dep_failed = False
            for dep_id in s.dependencies:
                dep = self.subtasks.get(dep_id)
                if not dep or dep.status != SubtaskStatus.COMPLETED:
                    deps_met = False
                if dep and dep.status in (SubtaskStatus.FAILED, SubtaskStatus.BLOCKED):
                    dep_failed = True

            if dep_failed:
                s.status = SubtaskStatus.BLOCKED
            elif deps_met:
                s.status = SubtaskStatus.READY
            else:
                s.status = SubtaskStatus.PENDING

    def mark_completed(self, subtask_id: str, changed_files: Optional[list[str]] = None) -> None:
        subtask = self.subtasks.get(subtask_id)
        if not subtask:
            raise PolicyError(f"Subtask '{subtask_id}' not found.")
        subtask.status = SubtaskStatus.COMPLETED
        if changed_files:
            subtask.changed_files = changed_files
        self._refresh_statuses()

    def mark_failed(self, subtask_id: str, error: str) -> None:
        subtask = self.subtasks.get(subtask_id)
        if not subtask:
            raise PolicyError(f"Subtask '{subtask_id}' not found.")
        subtask.status = SubtaskStatus.FAILED
        subtask.error = error
        self._refresh_statuses()

    def is_fully_completed(self) -> bool:
        return bool(self.subtasks) and all(s.status == SubtaskStatus.COMPLETED for s in self.subtasks.values())

    def has_failures(self) -> bool:
        return any(s.status in (SubtaskStatus.FAILED, SubtaskStatus.BLOCKED) for s in self.subtasks.values())

    def replan(
        self,
        old_assumption: str,
        new_evidence: str,
        reason: str,
        subtasks_to_add: list[Subtask],
        subtask_ids_to_cancel: Optional[list[str]] = None,
    ) -> PlanRevision:
        """Controlled plan revision: adds new subtasks and cancels obsolete pending ones."""
        rev_id = f"rev-{len(self.revisions)+1:03d}"
        now = datetime.now(timezone.utc).isoformat()
        cancelled_ids: list[str] = []

        for c_id in (subtask_ids_to_cancel or []):
            st = self.subtasks.get(c_id)
            if st and st.status not in (SubtaskStatus.COMPLETED, SubtaskStatus.IN_PROGRESS):
                st.status = SubtaskStatus.CANCELLED
                cancelled_ids.append(c_id)

        added_ids: list[str] = []
        for new_st in subtasks_to_add:
            self.subtasks[new_st.subtask_id] = new_st
            added_ids.append(new_st.subtask_id)

        revision = PlanRevision(
            revision_id=rev_id,
            timestamp=now,
            old_assumption=redact(old_assumption),
            new_evidence=redact(new_evidence),
            reason=redact(reason),
            added_subtasks=added_ids,
            removed_subtasks=cancelled_ids,
        )
        self.revisions.append(revision)
        self._refresh_statuses()
        return revision
