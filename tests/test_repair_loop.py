"""Unit tests for RepairClassifier, FailureClass, and RepairLoopGuard."""
import unittest

from ai_code_engineer.repair_loop import (
    FailureClass,
    FailureDiagnosis,
    RepairClassifier,
    RepairLoopGuard,
)


class RepairLoopTests(unittest.TestCase):
    def test_classify_compilation_failure(self):
        run = {
            "status": "failed",
            "tail": "src/App.java:12: error: cannot find symbol\n  symbol:   variable foo",
            "failures": ["src/App.java:12: error: cannot find symbol"],
        }
        diag = RepairClassifier.diagnose(run)
        self.assertEqual(diag.failure_class, FailureClass.COMPILATION)
        self.assertTrue(diag.is_fixable_by_code)
        self.assertTrue(len(diag.error_sites) > 0)

    def test_classify_test_assertion_failure(self):
        run = {
            "status": "failed",
            "tail": "FAILED tests/test_math.py::test_add - AssertionError: assert 5 == 4",
            "failures": ["FAILED tests/test_math.py::test_add - AssertionError: assert 5 == 4"],
        }
        diag = RepairClassifier.diagnose(run)
        self.assertEqual(diag.failure_class, FailureClass.TEST_FAILURE)
        self.assertTrue(diag.is_fixable_by_code)
        self.assertIn("tests/test_math.py::test_add", diag.failing_tests[0])

    def test_classify_environment_unavailable(self):
        run = {
            "status": "unavailable",
            "reason": "mvn is not installed on PATH",
        }
        diag = RepairClassifier.diagnose(run)
        self.assertEqual(diag.failure_class, FailureClass.ENVIRONMENT)
        self.assertFalse(diag.is_fixable_by_code)

    def test_guard_stops_on_unfixable_failure(self):
        guard = RepairLoopGuard(max_rounds=3)
        diag = FailureDiagnosis(
            failure_class=FailureClass.ENVIRONMENT,
            raw_category="environment",
            is_fixable_by_code=False,
        )
        can_proceed, reason = guard.can_proceed(diag)
        self.assertFalse(can_proceed)
        self.assertIn("environment issue", reason)

    def test_guard_detects_stagnation(self):
        guard = RepairLoopGuard(max_rounds=3, stagnation_limit=2)
        diag = FailureDiagnosis(
            failure_class=FailureClass.TEST_FAILURE,
            raw_category="assertion",
            failing_tests=["test_login"],
            error_sites=["app.py:20"],
        )
        # Round 1 proceeds
        p1, _ = guard.can_proceed(diag)
        self.assertTrue(p1)

        # Identical failure in Round 2 triggers stagnation halt
        p2, reason2 = guard.can_proceed(diag)
        self.assertFalse(p2)
        self.assertIn("Stagnation detected", reason2)

    def test_guard_stops_at_max_rounds(self):
        guard = RepairLoopGuard(max_rounds=2)
        diag1 = FailureDiagnosis(FailureClass.COMPILATION, "syntax", error_sites=["a.py:1"])
        diag2 = FailureDiagnosis(FailureClass.COMPILATION, "syntax", error_sites=["b.py:2"])
        diag3 = FailureDiagnosis(FailureClass.COMPILATION, "syntax", error_sites=["c.py:3"])

        p1, _ = guard.can_proceed(diag1)
        self.assertTrue(p1)
        p2, _ = guard.can_proceed(diag2)
        self.assertTrue(p2)

        p3, reason3 = guard.can_proceed(diag3)
        self.assertFalse(p3)
        self.assertIn("maximum repair budget", reason3)
