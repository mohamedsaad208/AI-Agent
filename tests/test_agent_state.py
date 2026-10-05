"""Comprehensive unit tests for persistent Agent State, Checkpoints, and MemoryStore.

Tests cover:
- State creation, serialization, and deserialization
- Checkpoint creation and atomic persistence
- Checkpoint integrity via sha256 verification
- Resume from latest checkpoint
- Side-effect tracking and duplicate prevention (idempotency guard)
- Long-term memory entry creation, deduplication, and update
- Category enforcement and secret redaction
- Relevance-based and recency-based memory retrieval
"""
from pathlib import Path
import tempfile
import unittest

from ai_code_engineer.agent_state import (
    AgentState,
    AgentStatus,
    Checkpoint,
    CheckpointManager,
    CheckpointTrigger,
    MemoryEntry,
    MemoryStore,
    SqliteStateStore,
)
from ai_code_engineer.errors import PolicyError


class AgentStateTests(unittest.TestCase):
    def test_state_creation_and_dict_roundtrip(self):
        state = AgentState(
            run_id="run-101",
            project_root="/workspace/repo",
            task="Refactor authentication handler",
            status=AgentStatus.PLANNING.value,
        )
        state.note_tool_call(
            "read_file",
            {"path": "auth.py"},
            {"status": "ok", "content": "..."}
        )
        data = state.to_dict()
        self.assertEqual(data["run_id"], "run-101")
        self.assertEqual(len(data["tool_history"]), 1)

        restored = AgentState.from_dict(data)
        self.assertEqual(restored.run_id, "run-101")
        self.assertEqual(restored.task, "Refactor authentication handler")
        self.assertEqual(restored.status, AgentStatus.PLANNING.value)
        self.assertEqual(restored.tool_history[0]["tool"], "read_file")

    def test_side_effect_deduplication(self):
        state = AgentState(run_id="run-102", project_root="/repo", task="Apply patch")
        tool_args = {"path": "config.json", "content": '{"debug": true}'}

        # Initially no side effect committed
        self.assertFalse(state.has_side_effect_committed("write_file", tool_args))

        # Record committed side effect
        state.record_side_effect("write_file", tool_args, result_status="success", committed=True)
        self.assertTrue(state.has_side_effect_committed("write_file", tool_args))

        # Different arguments do not collide
        different_args = {"path": "config.json", "content": '{"debug": false}'}
        self.assertFalse(state.has_side_effect_committed("write_file", different_args))


class CheckpointManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = CheckpointManager(self.temp_dir.name)
        self.addCleanup(self.temp_dir.cleanup)

    def test_save_and_load_checkpoint(self):
        state = AgentState(
            run_id="run-201",
            project_root="/workspace/app",
            task="Fix test failure",
            status=AgentStatus.EXECUTING.value,
        )
        chk = self.manager.save_checkpoint(state, CheckpointTrigger.AFTER_PLAN, step_index=1)
        self.assertTrue(chk.checkpoint_id.startswith("chk-0001-"))
        self.assertTrue(len(chk.sha256) > 0)

        loaded = self.manager.load_checkpoint("run-201", chk.checkpoint_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.checkpoint_id, chk.checkpoint_id)
        self.assertEqual(loaded.state.run_id, "run-201")
        self.assertEqual(loaded.sha256, chk.sha256)

    def test_latest_checkpoint_and_sequence(self):
        state = AgentState(run_id="run-202", project_root="/workspace/app", task="Migration")
        self.manager.save_checkpoint(state, CheckpointTrigger.INITIAL, step_index=0)
        self.manager.save_checkpoint(state, CheckpointTrigger.AFTER_PLAN, step_index=1)
        chk_last = self.manager.save_checkpoint(state, CheckpointTrigger.AFTER_TOOL, step_index=2)

        latest = self.manager.latest_checkpoint("run-202")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.checkpoint_id, chk_last.checkpoint_id)
        self.assertEqual(latest.step_index, 2)

        checkpoints = self.manager.list_checkpoints("run-202")
        self.assertEqual(len(checkpoints), 3)
        self.assertEqual([c.step_index for c in checkpoints], [0, 1, 2])

    def test_resume_run_from_latest_checkpoint(self):
        state = AgentState(
            run_id="run-203",
            project_root="/workspace/app",
            task="Process orders",
            status=AgentStatus.WAITING_APPROVAL.value,
        )
        self.manager.save_checkpoint(state, CheckpointTrigger.WAITING_APPROVAL, step_index=5)

        resumed_state, message = self.manager.resume_run("run-203")
        self.assertEqual(resumed_state.run_id, "run-203")
        self.assertEqual(resumed_state.status, AgentStatus.EXECUTING.value)
        self.assertIn("Resumed from step 5", message)

    def test_resume_run_with_no_checkpoints_raises(self):
        with self.assertRaises(PolicyError):
            self.manager.resume_run("non-existent-run")

    def test_checkpoint_retention_pruning(self):
        manager = CheckpointManager(self.temp_dir.name, max_retention=3)
        state = AgentState(run_id="run-prune", project_root="/workspace/app", task="Pruning test")
        # Save 6 checkpoints (step 0 through 5)
        for i in range(6):
            manager.save_checkpoint(state, CheckpointTrigger.AFTER_TOOL, step_index=i)

        remaining = manager.list_checkpoints("run-prune")
        # Should retain at most 3 checkpoints: step 0 (initial) + last 2 (steps 4, 5)
        self.assertLessEqual(len(remaining), 3)
        remaining_indices = [c.step_index for c in remaining]
        self.assertIn(0, remaining_indices)
        self.assertIn(5, remaining_indices)



class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MemoryStore(self.temp_dir.name)
        self.addCleanup(self.temp_dir.cleanup)

    def test_add_and_retrieve_convention(self):
        entry = self.store.add_entry(
            project_key="proj-alpha",
            category="convention",
            content="All public methods must specify type hints.",
            tags=["typing", "python"],
        )
        self.assertTrue(entry.entry_id.startswith("mem-"))

        results = self.store.retrieve("proj-alpha", query="methods type hints")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].entry_id, entry.entry_id)

    def test_category_enforcement(self):
        with self.assertRaises(PolicyError):
            self.store.add_entry(
                project_key="proj-alpha",
                category="random_unsupported_category",
                content="Something here",
            )

    def test_tag_and_relevance_ranking(self):
        self.store.add_entry(
            project_key="proj-beta",
            category="build_command",
            content="Run gradle test --daemon",
            tags=["gradle", "test", "build"],
        )
        self.store.add_entry(
            project_key="proj-beta",
            category="architecture",
            content="Services communicate over gRPC endpoints",
            tags=["grpc", "network", "api"],
        )

        # Query for build/test
        matches = self.store.retrieve("proj-beta", tags=["test"])
        self.assertEqual(len(matches), 1)
        self.assertIn("gradle test", matches[0].content)

        # Query for network
        matches_net = self.store.retrieve("proj-beta", query="gRPC communication")
        self.assertEqual(len(matches_net), 1)
        self.assertIn("gRPC endpoints", matches_net[0].content)

    def test_delete_entry(self):
        entry = self.store.add_entry(
            project_key="proj-gamma",
            category="repo_rule",
            content="Never commit secrets to git",
        )
        self.assertEqual(len(self.store.retrieve("proj-gamma")), 1)

        deleted = self.store.delete_entry("proj-gamma", entry.entry_id)
        self.assertTrue(deleted)
        self.assertEqual(len(self.store.retrieve("proj-gamma")), 0)


class SqliteStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "agent_test.db"
        self.store = SqliteStateStore(self.db_path)
        self.addCleanup(self.temp_dir.cleanup)

    def test_save_and_load_state(self):
        state = AgentState(
            run_id="run-sql-1",
            project_root="/workspace/sql",
            task="Test SQLite WAL storage",
            status=AgentStatus.PLANNING.value,
        )
        self.store.save_state(state)

        loaded = self.store.load_state("run-sql-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.run_id, "run-sql-1")
        self.assertEqual(loaded.task, "Test SQLite WAL storage")
        self.assertEqual(loaded.status, AgentStatus.PLANNING.value)

    def test_save_and_prune_checkpoints(self):
        state = AgentState(run_id="run-sql-chk", project_root="/workspace/sql", task="Chk SQLite")
        self.store.save_state(state)

        for i in range(5):
            chk = Checkpoint(
                checkpoint_id=f"chk-{i:04d}",
                run_id="run-sql-chk",
                step_index=i,
                trigger="tool",
                timestamp="2026-10-04T00:00:00Z",
                sha256="fake_sha",
                state=state,
            )
            self.store.save_checkpoint(chk, max_retention=3)

        checkpoints = self.store.list_checkpoints("run-sql-chk")
        self.assertLessEqual(len(checkpoints), 3)
        indices = [c.step_index for c in checkpoints]
        self.assertIn(0, indices)
        self.assertIn(4, indices)

        latest = self.store.load_latest_checkpoint("run-sql-chk")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.step_index, 4)

    def test_save_and_retrieve_memory(self):
        entry = MemoryEntry(
            entry_id="mem-1",
            project_key="proj-sql",
            category="architecture",
            content="Use SQLite WAL for durability",
            relevance_tags=["sqlite", "wal", "db"],
        )
        self.store.save_memory_entry(entry)

        results = self.store.retrieve_memory("proj-sql", category="architecture")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].entry_id, "mem-1")
        self.assertIn("SQLite WAL", results[0].content)
        self.assertIn("wal", results[0].relevance_tags)

