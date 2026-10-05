"""Production-Grade Policy Engine and Human Approval Architecture.

"The LLM proposes actions. Policy determines what is allowed."

This module coordinates:
1. Risk Level Classification: delegated to `risk_policy.RiskPolicy`, which is the only place a risk is
   rated now; the names this module answers with are that module's `Operation` codes.
2. Deterministic Policy Rules: the verdict comes from `policy.decide` — the same table the gate reads —
   so an approval request can never be raised for an act the folder has already refused outright.
3. Structured Approval Model: Context-rich Human-in-the-Loop approval requests with strict
   expiration and single-use token consumption (preventing replay attacks).
4. Fail-Closed Security: Unknown or unclassified actions are denied by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import time
from typing import Any, Optional

from .errors import PolicyError
from .policy import ALLOW, ASK, DENY
from .redaction import redact
from .risk_policy import Operation, RiskPolicy

# The rating this module used to do itself, in its own words. Kept as the name callers already import:
# the codes are identical, so a level named here and a level named by `risk_policy` are one object.
RiskLevel = Operation


class ApprovalStatus(str, Enum):
    """Lifecycle status of a human approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass
class ApprovalRequest:
    """A structured, transparent proposal requesting human operator authorization."""
    request_id: str
    action: str
    risk_level: RiskLevel
    target: str
    rationale: str
    details: dict[str, Any] = field(default_factory=dict)
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: float = field(default_factory=time.time)
    expires_after_seconds: float = 300.0  # 5 minutes default TTL
    granted_by: Optional[str] = None
    rejection_reason: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.expires_after_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "action": self.action,
            "risk_level": self.risk_level.value,
            "target": redact(self.target),
            "rationale": redact(self.rationale),
            "details": {str(k): redact(str(v)) for k, v in self.details.items()},
            "status": self.status.value,
            "is_expired": self.is_expired,
            "created_at": self.created_at,
            "expires_after_seconds": self.expires_after_seconds,
        }


class ApprovalManager:
    """Tracks and governs single-use approval requests with TTL enforcement."""

    def __init__(self) -> None:
        self.requests: dict[str, ApprovalRequest] = {}

    def request_approval(
        self,
        action: str,
        risk_level: RiskLevel,
        target: str,
        rationale: str,
        details: Optional[dict[str, Any]] = None,
        ttl_seconds: float = 300.0,
    ) -> ApprovalRequest:
        """Create a new pending authorization request."""
        token_src = f"{action}:{target}:{time.time()}"
        req_id = "appr-" + hashlib.sha256(token_src.encode("utf-8")).hexdigest()[:12]
        
        req = ApprovalRequest(
            request_id=req_id,
            action=action,
            risk_level=risk_level,
            target=target,
            rationale=rationale,
            details=details or {},
            expires_after_seconds=ttl_seconds,
        )
        self.requests[req_id] = req
        return req

    def grant(self, request_id: str, operator_id: str = "human_operator") -> None:
        """Grant permission for a pending request."""
        req = self.requests.get(request_id)
        if not req:
            raise PolicyError(f"Approval request '{request_id}' not found.")
        if req.is_expired:
            req.status = ApprovalStatus.EXPIRED
            raise PolicyError(f"Cannot grant expired approval request '{request_id}'.")
        if req.status != ApprovalStatus.PENDING:
            raise PolicyError(f"Approval request '{request_id}' is already {req.status.value}.")

        req.status = ApprovalStatus.APPROVED
        req.granted_by = operator_id

    def reject(self, request_id: str, reason: str = "Operator declined") -> None:
        """Reject permission for a pending request."""
        req = self.requests.get(request_id)
        if not req:
            raise PolicyError(f"Approval request '{request_id}' not found.")
        req.status = ApprovalStatus.REJECTED
        req.rejection_reason = reason

    def consume(self, request_id: str) -> bool:
        """Atomically consume an approved token, preventing replay or reuse."""
        req = self.requests.get(request_id)
        if not req:
            return False
        if req.is_expired:
            req.status = ApprovalStatus.EXPIRED
            return False
        if req.status != ApprovalStatus.APPROVED:
            return False

        # Mark as consumed immediately
        req.status = ApprovalStatus.CONSUMED
        return True


class PolicyEngine:
    """Deterministic policy engine evaluating proposed agent actions against safety rules.

    It asks `RiskPolicy` what kind of act a call is and what the table answers for it; it owns the
    autonomy mode, the approval tokens, and nothing else about risk.
    """

    def __init__(self, approval_manager: Optional[ApprovalManager] = None,
                 risk_policy: Optional[RiskPolicy] = None) -> None:
        self.approval_mgr = approval_manager or ApprovalManager()
        self.risk_policy = risk_policy or RiskPolicy()

    def classify_risk(self, tool_name: str, target: str) -> RiskLevel:
        """The operation this call is, as one of the shared codes."""
        return self.risk_policy.assess(tool_name, target).operation

    def evaluate(
        self,
        tool_name: str,
        target: str,
        autonomy_mode: str = "change",  # chat, read, change
        approval_id: Optional[str] = None,
    ) -> tuple[str, Optional[ApprovalRequest]]:
        """Evaluate an action returning (verdict, optional_approval_request).

        Verdicts:
            ALLOW: Execution permitted immediately.
            ASK: Human approval required; returns ApprovalRequest.
            DENY: Action strictly prohibited.

        The verdict is the folder table's, with one thing layered on top of it: a read-only or chat run
        refuses every act that is not a read, whatever the table would have said to an operator standing
        at a button. A token that was already granted consumes the ask, because the human answered it.
        """
        check = self.risk_policy.assess(tool_name, target)

        if autonomy_mode in ("chat", "read") and check.operation is not RiskLevel.SAFE_READ:
            return DENY, None

        if approval_id:
            return (ALLOW, None) if self.approval_mgr.consume(approval_id) else (DENY, None)

        if check.verdict == ASK:
            return ASK, self.approval_mgr.request_approval(
                action=tool_name,
                risk_level=check.operation,
                target=target,
                rationale=f"Action '{tool_name}' on '{target}' is categorized as {check.operation.value} ({check.risk.value}).",
            )
        return check.verdict, None
