"""Unit tests for TaskDAG, Subtask, and Controlled Replanning."""
import unittest

from ai_code_engineer.decomposition import (
    Subtask,
    SubtaskStatus,
    TaskDAG,
)


class DecompositionTests(unittest.TestCase):
    def test_single_step_task(self):
        dag = TaskDAG("Single step")
        st = Subtask("s1", "Implement method", "Add add() function")
        dag.add_subtask(st)

        ready = dag.get_ready_subtasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].subtask_id, "s1")

        dag.mark_completed("s1", changed_files=["math.py"])
        self.assertTrue(dag.is_fully_completed())
        self.assertFalse(dag.has_failures())

    def test_dependency_chain_and_branching(self):
        dag = TaskDAG("Complex workflow")
        # s1 -> (s2, s3) -> s4
        s1 = Subtask("s1", "Step 1", "Prereq")
        s2 = Subtask("s2", "Step 2", "Branch A", dependencies=["s1"])
        s3 = Subtask("s3", "Step 3", "Branch B", dependencies=["s1"])
        s4 = Subtask("s4", "Step 4", "Join", dependencies=["s2", "s3"])

        for s in (s1, s2, s3, s4):
            dag.add_subtask(s)

        # Initially only s1 is ready
        ready1 = dag.get_ready_subtasks()
        self.assertEqual([s.subtask_id for s in ready1], ["s1"])

        dag.mark_completed("s1")
        # Now both s2 and s3 are ready
        ready2 = dag.get_ready_subtasks()
        self.assertEqual(sorted([s.subtask_id for s in ready2]), ["s2", "s3"])

        dag.mark_completed("s2")
        # s4 still waiting on s3
        ready3 = dag.get_ready_subtasks()
        self.assertEqual([s.subtask_id for s in ready3], ["s3"])

        dag.mark_completed("s3")
        # s4 is ready
        ready4 = dag.get_ready_subtasks()
        self.assertEqual([s.subtask_id for s in ready4], ["s4"])

        dag.mark_completed("s4")
        self.assertTrue(dag.is_fully_completed())

    def test_failed_dependency_blocks_downstream_tasks(self):
        dag = TaskDAG("Failure propagation")
        s1 = Subtask("s1", "Build image", "Build")
        s2 = Subtask("s2", "Deploy container", "Deploy", dependencies=["s1"])
        dag.add_subtask(s1)
        dag.add_subtask(s2)

        dag.mark_failed("s1", "Compilation error")
        self.assertEqual(dag.subtasks["s1"].status, SubtaskStatus.FAILED)
        self.assertEqual(dag.subtasks["s2"].status, SubtaskStatus.BLOCKED)
        self.assertEqual(len(dag.get_ready_subtasks()), 0)
        self.assertTrue(dag.has_failures())
        self.assertFalse(dag.is_fully_completed())

    def test_controlled_replanning(self):
        dag = TaskDAG("Replanning task")
        s1 = Subtask("s1", "Inspect schema", "Inspect")
        s2 = Subtask("s2", "Direct SQL migrate", "Migrate", dependencies=["s1"])
        dag.add_subtask(s1)
        dag.add_subtask(s2)

        dag.mark_completed("s1")

        # Discovery: project uses Alembic, not raw SQL
        alt_s = Subtask("s3", "Run Alembic migration", "Alembic", dependencies=["s1"])
        rev = dag.replan(
            old_assumption="Project uses raw SQL scripts",
            new_evidence="Found alembic.ini and versions directory",
            reason="Switch to Alembic migration command",
            subtasks_to_add=[alt_s],
            subtask_ids_to_cancel=["s2"],
        )

        self.assertEqual(rev.revision_id, "rev-001")
        self.assertEqual(dag.subtasks["s2"].status, SubtaskStatus.CANCELLED)
        self.assertEqual(dag.subtasks["s3"].status, SubtaskStatus.READY)
        self.assertEqual(dag.subtasks["s1"].status, SubtaskStatus.COMPLETED)
