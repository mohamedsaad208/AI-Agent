import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer.core import (
    Action,
    AgentCore,
    AgentState,
    AgentStatus,
    Plan,
    Result,
    Task,
    Tool,
    VerificationResult,
)
from ai_code_engineer.errors import PolicyError


class TestAgentCore(unittest.TestCase):

    def test_task_model_serialization(self):
        task = Task(description="Add validation to login API", project_root="/test/proj")
        data = task.to_dict()
        self.assertEqual(data["description"], "Add validation to login API")
        self.assertEqual(data["project_root"], "/test/proj")
        self.assertTrue(data["id"])

        recovered = Task.from_dict(data)
        self.assertEqual(recovered.id, task.id)
        self.assertEqual(recovered.description, task.description)

    def test_plan_and_action_serialization(self):
        plan = Plan(
            task_id="task-123",
            summary="Validate email and password in login request",
            steps=["1. Inspect LoginController", "2. Add @Valid annotation", "3. Add tests"],
            target_files=["src/main/kotlin/LoginController.kt"],
            verification_checks=["./gradlew test"],
        )
        plan_dict = plan.to_dict()
        self.assertEqual(len(plan_dict["steps"]), 3)
        recovered_plan = Plan.from_dict(plan_dict)
        self.assertEqual(recovered_plan.summary, plan.summary)

        action = Action(name="read_file", parameters={"path": "src/LoginController.kt"})
        action_dict = action.to_dict()
        recovered_action = Action.from_dict(action_dict)
        self.assertEqual(recovered_action.name, "read_file")
        self.assertEqual(recovered_action.parameters["path"], "src/LoginController.kt")

    def test_result_and_verification_result(self):
        res = Result(success=True, data={"symbols_found": 5})
        self.assertTrue(res.success)
        self.assertEqual(Result.from_dict(res.to_dict()).data["symbols_found"], 5)

        verif = VerificationResult(
            passed=True,
            status="passed",
            command="./gradlew test",
            exit_code=0,
            summary="All 12 tests passed",
        )
        verif_dict = verif.to_dict()
        recovered_verif = VerificationResult.from_dict(verif_dict)
        self.assertTrue(recovered_verif.passed)
        self.assertEqual(recovered_verif.exit_code, 0)

    def test_agent_state_transitions(self):
        task = Task(description="Fix null pointer exception")
        state = AgentState(task=task, status=AgentStatus.ANALYZING)
        self.assertEqual(state.status, AgentStatus.ANALYZING)

        # Valid transition: ANALYZING -> PLANNING
        state.transition_to(AgentStatus.PLANNING, reason="Analysis complete")
        self.assertEqual(state.status, AgentStatus.PLANNING)

        # Valid transition: PLANNING -> WAITING_APPROVAL
        state.transition_to(AgentStatus.WAITING_APPROVAL, reason="Plan ready")
        self.assertEqual(state.status, AgentStatus.WAITING_APPROVAL)

        # Illegal transition: WAITING_APPROVAL -> DONE directly without EXECUTING
        with self.assertRaises(PolicyError):
            state.transition_to(AgentStatus.DONE)

    def test_deterministic_flow_success(self):
        orchestrator = AgentCore(project_root="/my/project")
        
        # 1. Task Intake
        state = orchestrator.intake("Add validation to login API")
        self.assertEqual(state.status, AgentStatus.ANALYZING)
        state.record_read("src/main/kotlin/LoginController.kt")
        self.assertIn("src/main/kotlin/LoginController.kt", state.files_read)

        # 2. Plan Creation
        plan = Plan(
            task_id=state.task.id,
            summary="Add input validation for login credentials",
            steps=["Inspect controller", "Add validation DTO", "Run tests"],
            target_files=["src/main/kotlin/LoginController.kt"],
        )
        changes = [{
            "path": "src/main/kotlin/LoginController.kt",
            "after": "// Updated with validation",
        }]
        orchestrator.set_plan(state, plan, changes)
        self.assertEqual(state.status, AgentStatus.WAITING_APPROVAL)
        self.assertTrue(state.proposal_hash)

        # 3. Execution requires correct hash
        with self.assertRaises(PolicyError):
            orchestrator.approve_and_execute(state, "wrong_hash", lambda ch: ["src/main/kotlin/LoginController.kt"])

        # Approved with correct hash
        applied = []
        def mock_executor(ch):
            paths = [c["path"] for c in ch]
            applied.extend(paths)
            return paths

        orchestrator.approve_and_execute(state, state.proposal_hash, mock_executor)
        self.assertEqual(state.status, AgentStatus.VERIFYING)
        self.assertEqual(applied, ["src/main/kotlin/LoginController.kt"])
        self.assertIn("src/main/kotlin/LoginController.kt", state.files_changed)

        # 4. Verification Passes -> DONE
        verif = VerificationResult(
            passed=True,
            status="passed",
            command="./gradlew test",
            exit_code=0,
            summary="Tests passed",
        )
        orchestrator.record_verification(state, verif)
        self.assertEqual(state.status, AgentStatus.DONE)
        self.assertEqual(state.verification_status, "PASSED")

    def test_deterministic_flow_fix_cycle(self):
        orchestrator = AgentCore(project_root="/my/project")
        state = orchestrator.intake("Fix authentication bug")

        plan = Plan(task_id=state.task.id, summary="Patch auth logic")
        orchestrator.set_plan(state, plan, [{"path": "Auth.kt", "after": "patch"}])
        
        # Approve and execute
        orchestrator.approve_and_execute(state, state.proposal_hash, lambda ch: ["Auth.kt"])
        self.assertEqual(state.status, AgentStatus.VERIFYING)

        # Verification fails -> FIXING
        fail_verif = VerificationResult(
            passed=False,
            status="failed",
            command="./gradlew test",
            exit_code=1,
            summary="Compilation error: Type mismatch",
        )
        orchestrator.record_verification(state, fail_verif)
        self.assertEqual(state.status, AgentStatus.FIXING)
        self.assertEqual(state.verification_status, "FAILED")

        # Refine plan in FIXING state -> WAITING_APPROVAL
        new_plan = Plan(task_id=state.task.id, summary="Fix type mismatch in Auth.kt")
        orchestrator.set_plan(state, new_plan, [{"path": "Auth.kt", "after": "correct patch"}])
        self.assertEqual(state.status, AgentStatus.WAITING_APPROVAL)

        # Re-approve and re-verify -> DONE
        orchestrator.approve_and_execute(state, state.proposal_hash, lambda ch: ["Auth.kt"])
        pass_verif = VerificationResult(
            passed=True,
            status="passed",
            command="./gradlew test",
            exit_code=0,
        )
        orchestrator.record_verification(state, pass_verif)
        self.assertEqual(state.status, AgentStatus.DONE)


if __name__ == "__main__":
    unittest.main()
