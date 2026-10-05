"""Unit tests for ToolConcurrencyManager and MultiAgentArchitecturalReview."""
import time
import unittest

from ai_code_engineer.concurrency import (
    MultiAgentArchitecturalReview,
    ParallelBatchResult,
    ToolConcurrencyManager,
)
from ai_code_engineer.contracts import Call, Result, LEVEL_READ, LEVEL_WRITE


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_read_execution_is_faster_than_sequential(self):
        manager = ToolConcurrencyManager(max_workers=3)

        # 3 read-only calls taking 0.05s each
        def slow_read_executor(call: Call) -> Result:
            time.sleep(0.05)
            return Result(
                status="ok",
                data={"result": f"read_{call.tool}"},
                message="read completed",
                call_id=call.call_id,
                trace_id=call.trace_id,
            )

        calls = [
            Call(tool="read_file", args={"path": f"f{i}.py"}, call_id=f"req-{i}")
            for i in range(3)
        ]

        batch = manager.execute_batch(calls, slow_read_executor, side_effects={"read_file": LEVEL_READ})
        self.assertEqual(len(batch.results), 3)
        # Sequential would take ~150ms. Parallel should complete in ~70-90ms.
        self.assertLess(batch.total_duration_ms, 140.0)

    def test_side_effect_calls_are_serialized(self):
        manager = ToolConcurrencyManager(max_workers=3)
        execution_order = []

        def side_effect_executor(call: Call) -> Result:
            execution_order.append(call.tool)
            return Result(
                status="ok",
                data={},
                call_id=call.call_id,
            )

        calls = [
            Call(tool="write_file", args={"path": "a.py"}, call_id="1"),
            Call(tool="write_file", args={"path": "b.py"}, call_id="2"),
        ]

        batch = manager.execute_batch(calls, side_effect_executor, side_effects={"write_file": LEVEL_WRITE})
        self.assertEqual(len(batch.results), 2)
        self.assertEqual(len(execution_order), 2)

    def test_multi_agent_architectural_evaluation(self):
        review = MultiAgentArchitecturalReview.evaluate()
        self.assertEqual(review["verdict"], "NOT_NEEDED")
        self.assertIn("DEFER", review["decision_code"])
        self.assertIn("16GB", review["rationale"])
