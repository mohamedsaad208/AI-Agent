"""Human-in-the-Loop Review, Change Explainability, and Feedback Incorporation.

"Autonomy should reduce developer effort without removing developer control."

This module coordinates developer review of agent proposals:
1. ReviewPackage: Transparent, concise explanation (WHAT, WHY, WHICH files, tests run, residual risks).
2. Risk Assessment: the band `risk_policy.RiskPolicy` gives the proposal as a whole — the same band the
   event and the terminal show, so a card, an ask and a report never name one change three ways.
3. Human Feedback Actions: Supports Approve, Reject, and Request Changes (e.g. "exclude file X", "revert tests").
4. Stale/Tamper Detection: Validates that the SHA-256 digest of approved diffs matches before applying.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Optional

from .errors import PolicyError
from .redaction import redact
from .risk_policy import Risk, assess_changes

# `risk_policy` rates a proposal now; the three bands this module spelled out are three of its four, so
# the name callers import is an alias and not a second ladder.
ChangeRiskLevel = Risk

_RISK_ICON = {Risk.LOW: "\U0001f7e2 LOW", Risk.MEDIUM: "\U0001f7e1 MEDIUM",
              Risk.HIGH: "\U0001f534 HIGH", Risk.CRITICAL: "\u26d4 CRITICAL"}


class ReviewDecision(str, Enum):
    """Human operator's explicit verdict on proposed changes."""
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"


@dataclass
class ReviewPackage:
    """Consolidated change summary presented to the human reviewer."""
    review_id: str
    task: str
    what_changed: str
    why: str
    files_affected: list[str]
    tests_executed: list[str] = field(default_factory=list)
    tests_status: str = "passed"
    remaining_risks: list[str] = field(default_factory=list)
    risk_level: ChangeRiskLevel = ChangeRiskLevel.LOW
    diff_hash: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_markdown(self) -> str:
        icon = _RISK_ICON.get(self.risk_level, _RISK_ICON[Risk.LOW])
        lines = [
            f"# Engineering Change Review: `{self.review_id}`",
            f"**Task:** {self.task}",
            f"**Risk Rating:** {icon}",
            "",
            "## Summary",
            f"- **What Changed:** {self.what_changed}",
            f"- **Rationale:** {self.why}",
            f"- **Affected Files ({len(self.files_affected)}):** {', '.join(self.files_affected)}",
            "",
            "## Verification & Proof",
            f"- **Tests Executed:** {', '.join(self.tests_executed) or 'None'}",
            f"- **Test Status:** {self.tests_status}",
        ]
        if self.remaining_risks:
            lines.extend(["", "## Residual Risks & Notes", *[f"- ⚠️ {r}" for r in self.remaining_risks]])
        return "\n".join(lines)


class ReviewManager:
    """Manages the creation, risk evaluation, and human decision workflow for reviews."""

    @staticmethod
    def assess_risk(files: list[str], task_text: str) -> Risk:
        """The band of a proposal, rated by the one rule that rates every other proposal."""
        return assess_changes([{"path": path} for path in files], task_text).risk

    @classmethod
    def create_package(
        cls,
        task: str,
        what_changed: str,
        why: str,
        files_affected: list[str],
        diff_text: str = "",
        tests_executed: Optional[list[str]] = None,
        tests_status: str = "passed",
        remaining_risks: Optional[list[str]] = None,
    ) -> ReviewPackage:
        clean_files = [redact(f.replace("\\", "/")) for f in files_affected]
        risk = cls.assess_risk(clean_files, task)
        diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()[:16]
        rev_id = f"rev-{diff_hash[:8]}"

        return ReviewPackage(
            review_id=rev_id,
            task=redact(task),
            what_changed=redact(what_changed),
            why=redact(why),
            files_affected=clean_files,
            tests_executed=tests_executed or [],
            tests_status=tests_status,
            remaining_risks=[redact(r) for r in (remaining_risks or [])],
            risk_level=risk,
            diff_hash=diff_hash,
        )

    @staticmethod
    def apply_feedback(
        package: ReviewPackage,
        decision: ReviewDecision,
        current_diff: str,
        feedback_instructions: str = "",
        excluded_files: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Processes human operator verdict, enforcing diff integrity and handling partial approvals."""
        # 1. Stale / Tamper Check
        current_hash = hashlib.sha256(current_diff.encode("utf-8")).hexdigest()[:16]
        if package.diff_hash and current_hash != package.diff_hash:
            raise PolicyError("Stale Review Error: Diff has changed since review package was prepared.")

        if decision == ReviewDecision.REJECTED:
            return {
                "decision": ReviewDecision.REJECTED.value,
                "action": "discard",
                "remaining_files": [],
                "instructions": feedback_instructions,
            }

        if decision == ReviewDecision.CHANGES_REQUESTED:
            return {
                "decision": ReviewDecision.CHANGES_REQUESTED.value,
                "action": "replan",
                "instructions": feedback_instructions,
                "remaining_files": package.files_affected,
            }

        # APPROVED or Partially Approved
        remaining = [f for f in package.files_affected if f not in (excluded_files or [])]
        return {
            "decision": ReviewDecision.APPROVED.value,
            "action": "apply",
            "approved_files": remaining,
            "excluded_files": excluded_files or [],
        }
