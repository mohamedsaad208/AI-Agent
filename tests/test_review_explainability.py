"""Unit tests for ReviewPackage, Risk Assessment, and ReviewManager."""
import unittest

from ai_code_engineer.errors import PolicyError
from ai_code_engineer.review import (
    ChangeRiskLevel,
    ReviewDecision,
    ReviewManager,
    ReviewPackage,
)


class ReviewExplainabilityTests(unittest.TestCase):
    def test_risk_level_assessment(self):
        # High risk domain
        risk_auth = ReviewManager.assess_risk(["src/auth/token_service.py"], "Update JWT verification")
        self.assertEqual(risk_auth, ChangeRiskLevel.HIGH)

        risk_pay = ReviewManager.assess_risk(["src/payment.py"], "Fix billing calculation")
        self.assertEqual(risk_pay, ChangeRiskLevel.HIGH)

        # Low risk domain
        risk_calc = ReviewManager.assess_risk(["src/math_helper.py"], "Fix addition logic")
        self.assertEqual(risk_calc, ChangeRiskLevel.LOW)

    def test_create_package_and_markdown(self):
        pkg = ReviewManager.create_package(
            task="Refactor math helper",
            what_changed="Extracted helper functions",
            why="Reduce function complexity",
            files_affected=["src/math_helper.py"],
            diff_text="+ def add(a, b): return a + b\n",
            tests_executed=["tests/test_math.py"],
            tests_status="passed",
            remaining_risks=["Edge case on negative floats"],
        )
        self.assertEqual(pkg.risk_level, ChangeRiskLevel.LOW)
        self.assertEqual(len(pkg.diff_hash), 16)

        md = pkg.to_markdown()
        self.assertIn("# Engineering Change Review", md)
        self.assertIn("Refactor math helper", md)
        self.assertIn("src/math_helper.py", md)
        self.assertIn("Edge case on negative floats", md)

    def test_apply_approval_and_partial_approval(self):
        pkg = ReviewManager.create_package(
            task="Update modules",
            what_changed="Changed service and tests",
            why="Feature update",
            files_affected=["service.py", "tests/test_service.py"],
            diff_text="diff_content_123",
        )
        # 1. Full approval
        res = ReviewManager.apply_feedback(pkg, ReviewDecision.APPROVED, current_diff="diff_content_123")
        self.assertEqual(res["decision"], "approved")
        self.assertEqual(res["approved_files"], ["service.py", "tests/test_service.py"])

        # 2. Partial approval: exclude tests
        res_part = ReviewManager.apply_feedback(
            pkg,
            ReviewDecision.APPROVED,
            current_diff="diff_content_123",
            excluded_files=["tests/test_service.py"],
        )
        self.assertEqual(res_part["approved_files"], ["service.py"])
        self.assertEqual(res_part["excluded_files"], ["tests/test_service.py"])

    def test_apply_rejection_and_changes_requested(self):
        pkg = ReviewManager.create_package(
            task="Task", what_changed="A", why="B",
            files_affected=["a.py"], diff_text="diff1",
        )
        # Rejection
        res_rej = ReviewManager.apply_feedback(pkg, ReviewDecision.REJECTED, current_diff="diff1")
        self.assertEqual(res_rej["action"], "discard")

        # Request changes
        res_chg = ReviewManager.apply_feedback(
            pkg,
            ReviewDecision.CHANGES_REQUESTED,
            current_diff="diff1",
            feedback_instructions="Use async await instead",
        )
        self.assertEqual(res_chg["action"], "replan")
        self.assertIn("async await", res_chg["instructions"])

    def test_stale_diff_rejection(self):
        pkg = ReviewManager.create_package(
            task="Task", what_changed="A", why="B",
            files_affected=["a.py"], diff_text="original_diff",
        )
        with self.assertRaises(PolicyError) as ctx:
            ReviewManager.apply_feedback(pkg, ReviewDecision.APPROVED, current_diff="tampered_diff_here")
        self.assertIn("Stale Review Error", str(ctx.exception))
