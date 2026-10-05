"""Autonomous Build / Test / Fix Loop Engine.

"Repair the root cause. Do not blindly retry until something passes."

This module coordinates the autonomous repair loop:
1. Failure Classification: Categorizes failures (COMPILATION, DEPENDENCY, TEST_FAILURE, CONFIGURATION, ENVIRONMENT, TIMEOUT).
2. Targeted Repair Extraction: Extracts exact error sites, failing assertions, and stack frames for minimal context footprint.
3. Stagnation & Loop Bounds: Halts early when identical failures recur or when failures are unfixable environment defects.
4. Regression Guard: Verifies that both compilation AND test suites pass before declaring completion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from typing import Any, Optional

from .errors import PolicyError
from .redaction import redact
from . import repair


class FailureClass(str, Enum):
    """Categorized root failure types."""
    COMPILATION = "compilation"
    DEPENDENCY = "dependency"
    TEST_FAILURE = "test_failure"
    CONFIGURATION = "configuration"
    ENVIRONMENT = "environment"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class FailureDiagnosis:
    """Rich, actionable diagnosis of an execution failure."""
    failure_class: FailureClass
    raw_category: str
    error_sites: list[str] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    signature: str = ""
    is_fixable_by_code: bool = True
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.signature:
            src = f"{self.failure_class.value}:{sorted(self.error_sites)}:{sorted(self.failing_tests)}:{sorted(self.exceptions)}"
            self.signature = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]


class RepairClassifier:
    """Classifies raw test/build runs into structured diagnoses."""

    @staticmethod
    def diagnose(run: dict[str, Any]) -> FailureDiagnosis:
        if run.get("status") == "passed":
            return FailureDiagnosis(
                failure_class=FailureClass.UNKNOWN,
                raw_category="passed",
                summary="Build/test passed successfully.",
            )

        raw_cat = repair.classify(run)
        facts = run.get("extract") or repair.extract(run)
        err_sites = facts.get("error_sites", [])
        failing_tests = facts.get("failed_tests", [])
        exceptions = facts.get("exceptions", [])

        # Map raw category to FailureClass
        if raw_cat in ("syntax", "type") or any("error" in s.lower() for s in err_sites):
            f_class = FailureClass.COMPILATION
            is_fixable = True
        elif raw_cat == "dependency":
            f_class = FailureClass.DEPENDENCY
            is_fixable = True
        elif raw_cat == "assertion" or len(failing_tests) > 0:
            f_class = FailureClass.TEST_FAILURE
            is_fixable = True
        elif raw_cat == "timeout":
            f_class = FailureClass.TIMEOUT
            is_fixable = False
        elif raw_cat == "environment":
            f_class = FailureClass.ENVIRONMENT
            is_fixable = False
        else:
            f_class = FailureClass.UNKNOWN
            is_fixable = True

        summary_parts = [f"Class: {f_class.value.upper()}"]
        if err_sites:
            summary_parts.append(f"Sites: {', '.join(err_sites[:3])}")
        if failing_tests:
            summary_parts.append(f"Failed Tests: {', '.join(failing_tests[:3])}")

        return FailureDiagnosis(
            failure_class=f_class,
            raw_category=raw_cat,
            error_sites=err_sites,
            failing_tests=failing_tests,
            exceptions=exceptions,
            is_fixable_by_code=is_fixable,
            summary=" | ".join(summary_parts),
        )


class RepairLoopGuard:
    """Governs retry budgets and prevents infinite loops on recurring failures."""

    def __init__(self, max_rounds: int = 3, stagnation_limit: int = 2) -> None:
        self.max_rounds = max_rounds
        self.stagnation_limit = stagnation_limit
        self.history: list[FailureDiagnosis] = []

    def can_proceed(self, diagnosis: FailureDiagnosis) -> tuple[bool, str]:
        """Determine if another repair turn is justified.
        
        Returns:
            Tuple of (should_continue, explanation_reason).
        """
        # Unfixable environment/toolchain issue
        if not diagnosis.is_fixable_by_code:
            return False, f"Halting: {diagnosis.failure_class.value} is an environment issue, not a code defect."

        # Maximum round budget exceeded
        current_round = len(self.history) + 1
        if current_round > self.max_rounds:
            return False, f"Halting: Reached maximum repair budget of {self.max_rounds} rounds."

        # Stagnation check: repeated identical signature
        if self.history:
            recent_matches = sum(1 for h in self.history[-self.stagnation_limit:] if h.signature == diagnosis.signature)
            if recent_matches >= (self.stagnation_limit - 1):
                return False, "Halting: Stagnation detected; identical failure recurred without progress."

        self.history.append(diagnosis)
        return True, f"Proceeding with repair round {current_round}/{self.max_rounds}."
