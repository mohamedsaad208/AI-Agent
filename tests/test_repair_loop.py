import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from ai_code_engineer.core import (
    AgentCore,
    AgentState,
    AgentStatus,
    Plan,
    Task,
    VerificationResult,
)
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.repair import (
    execute_verification,
    handle_fix_evaluation,
    verification_from_run,
)


class TestRepairLoop(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.core = AgentCore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_verification_from_run_passed(self):
        run = {
            "recipe": "python-unittest",
            "label": "Python unittest",
            "command": "python -m unittest",
            "status": "passed",
            "exit_code": 0,
            "seconds": 1.2,
            "tail": "OK",
            "failures": [],
        }
        res = verification_from_run(run)
        self.assertTrue(res.passed)
        self.assertEqual(res.status, "passed")
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.command, "python -m unittest")
        self.assertEqual(res.failures, [])

    def test_verification_from_run_failed(self):
        run = {
            "recipe": "python-unittest",
            "label": "Python unittest",
            "command": "python -m unittest",
            "status": "failed",
            "exit_code": 1,
            "seconds": 2.4,
            "tail": "FAILED (failures=1)",
            "failures": ["FAIL: test_calc"],
        }
        res = verification_from_run(run)
        self.assertFalse(res.passed)
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(res.failures, ["FAIL: test_calc"])
        self.assertIn("FAILED", res.output_tail)

    def test_execute_verification_success_advances_to_done(self):
        state = self.core.intake("Run tests")
        # Move state to VERIFYING
        state.status = AgentStatus.VERIFYING

        passed_run = {
            "recipe": "python-unittest",
            "status": "passed",
            "command": "python -m unittest",
            "exit_code": 0,
            "tail": "OK",
            "failures": [],
        }

        with patch("ai_code_engineer.runner.run", return_value=passed_run):
            verif = execute_verification(self.core, state, self.root, "python-unittest")

        self.assertTrue(verif.passed)
        self.assertEqual(state.status, AgentStatus.DONE)
        self.assertEqual(state.verification_status, "PASSED")
        self.assertIn("python -m unittest", state.commands_run)

    def test_execute_verification_failure_advances_to_fixing(self):
        state = self.core.intake("Fix bug")
        state.status = AgentStatus.VERIFYING

        failed_run = {
            "recipe": "python-unittest",
            "status": "failed",
            "command": "python -m unittest",
            "exit_code": 1,
            "tail": "FAILED",
            "failures": ["AssertionError"],
        }

        with patch("ai_code_engineer.runner.run", return_value=failed_run):
            verif = execute_verification(self.core, state, self.root, "python-unittest")

        self.assertFalse(verif.passed)
        self.assertEqual(state.status, AgentStatus.FIXING)
        self.assertEqual(state.verification_status, "FAILED")
        self.assertTrue(len(state.errors) > 0)

    def test_execute_verification_rejects_illegal_state(self):
        state = self.core.intake("Task in analyzing")
        self.assertEqual(state.status, AgentStatus.ANALYZING)
        with self.assertRaises(PolicyError):
            execute_verification(self.core, state, self.root, "python-unittest")

    def test_handle_fix_evaluation_continues_under_budget(self):
        state = self.core.intake("Task in fix")
        state.status = AgentStatus.FIXING

        # Round 1 of 3 -> continue
        cont, reason = handle_fix_evaluation(self.core, state, sessions=[], round_number=1, limit=3)
        self.assertTrue(cont)
        self.assertEqual(reason, "continue")
        self.assertEqual(state.status, AgentStatus.FIXING)

    def test_handle_fix_evaluation_stops_when_exhausted(self):
        state = self.core.intake("Task in fix")
        state.status = AgentStatus.FIXING

        # Round 3 of 3 -> stop and fail
        cont, reason = handle_fix_evaluation(self.core, state, sessions=[], round_number=3, limit=3)
        self.assertFalse(cont)
        self.assertIn("stopped after 3 fix rounds", reason)
        self.assertEqual(state.status, AgentStatus.FAILED)
        self.assertIn("stopped after 3 fix rounds", state.errors[-1])

    def test_full_fix_loop_lifecycle(self):
        """End-to-end lifecycle test:
        ANALYZING -> PLANNING -> WAITING_APPROVAL -> EXECUTING -> VERIFYING
        -> FIXING -> PLANNING -> WAITING_APPROVAL -> EXECUTING -> VERIFYING -> DONE
        """
        # 1. Intake
        state = self.core.intake("Implement calculate_total in OrderService")
        self.assertEqual(state.status, AgentStatus.ANALYZING)

        # 2. First Plan & Changes
        plan1 = Plan(
            task_id=state.task.id,
            summary="Initial implementation of calculate_total",
            target_files=["src/OrderService.java"],
        )
        changes1 = [{"path": "src/OrderService.java", "after": "int calculate_total() { return 0; }"}]
        self.core.set_plan(state, plan1, changes1)
        self.assertEqual(state.status, AgentStatus.WAITING_APPROVAL)

        # 3. Approve and Execute
        applied = []
        def mock_apply(ch):
            paths = [c["path"] for c in ch]
            applied.extend(paths)
            return paths

        self.core.approve_and_execute(state, state.proposal_hash, mock_apply)
        self.assertEqual(state.status, AgentStatus.VERIFYING)

        # 4. First verification fails
        failed_run = {
            "recipe": "maven-test",
            "status": "failed",
            "command": "mvn test",
            "exit_code": 1,
            "tail": "calculate_total expected 100 but was 0",
            "failures": ["testCalculateTotal"],
        }
        with patch("ai_code_engineer.runner.run", return_value=failed_run):
            execute_verification(self.core, state, self.root, "maven-test")

        self.assertEqual(state.status, AgentStatus.FIXING)

        # 5. Fix Evaluation allows continuation
        cont, reason = handle_fix_evaluation(self.core, state, sessions=[], round_number=1, limit=3)
        self.assertTrue(cont)

        # 6. Second Plan (Fix round)
        plan2 = Plan(
            task_id=state.task.id,
            summary="Fix calculate_total to sum item prices",
            target_files=["src/OrderService.java"],
        )
        changes2 = [{"path": "src/OrderService.java", "after": "int calculate_total() { return sum; }"}]
        self.core.set_plan(state, plan2, changes2)
        self.assertEqual(state.status, AgentStatus.WAITING_APPROVAL)

        # 7. Approve and Execute Fix
        self.core.approve_and_execute(state, state.proposal_hash, mock_apply)
        self.assertEqual(state.status, AgentStatus.VERIFYING)

        # 8. Second verification passes
        passed_run = {
            "recipe": "maven-test",
            "status": "passed",
            "command": "mvn test",
            "exit_code": 0,
            "tail": "BUILD SUCCESS",
            "failures": [],
        }
        with patch("ai_code_engineer.runner.run", return_value=passed_run):
            execute_verification(self.core, state, self.root, "maven-test")

        self.assertEqual(state.status, AgentStatus.DONE)
        self.assertEqual(state.verification_status, "PASSED")

        # 9. Verify transition history integrity
        transitions = [h["to"] for h in state.history if "to" in h]
        expected_transitions = [
            "PLANNING",
            "WAITING_APPROVAL",
            "EXECUTING",
            "VERIFYING",
            "FIXING",
            "PLANNING",
            "WAITING_APPROVAL",
            "EXECUTING",
            "VERIFYING",
            "DONE",
        ]
        self.assertEqual(transitions, expected_transitions)


if __name__ == "__main__":
    unittest.main()
