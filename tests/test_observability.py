"""Unit tests for Agent Observability, Tracing, and Failure Diagnostics."""
import unittest

from ai_code_engineer.observability import (
    EventType,
    FailureDiagnostic,
    RunSummary,
    TraceEvent,
    Tracer,
)


class ObservabilityTests(unittest.TestCase):
    def test_correlated_events_and_redaction(self):
        tracer = Tracer(run_id="run-obs-1")
        ev = tracer.emit(
            EventType.TOOL_REQUESTED,
            step_index=1,
            payload={
                "tool": "read_file",
                "path": "config.json",
                "secret_key": "sk-proj-supersecretkey1234567890abcdef",
            },
        )
        self.assertEqual(ev.run_id, "run-obs-1")
        self.assertEqual(ev.trace_id, tracer.trace_id)
        self.assertEqual(ev.step_index, 1)
        self.assertEqual(ev.event_type, "TOOL_REQUESTED")

        serialized = ev.to_dict()
        # Ensure secret was redacted
        self.assertNotIn("sk-proj-supersecretkey1234567890abcdef", str(serialized["payload"]))

    def test_run_summary_metrics_calculation(self):
        tracer = Tracer(run_id="run-obs-2")
        tracer.emit(EventType.RUN_STARTED, step_index=0)
        tracer.emit(EventType.TOOL_REQUESTED, step_index=1, payload={"tool": "read_file"})
        tracer.emit(EventType.TOOL_COMPLETED, step_index=1, payload={"tool": "read_file"})
        tracer.emit(EventType.TOOL_REQUESTED, step_index=2, payload={"tool": "run_tests"})
        tracer.emit(EventType.TOOL_FAILED, step_index=2, payload={"tool": "run_tests"},
                    error={"code": "TIMEOUT", "msg": "Test exceeded 120s"})

        diagnostic = FailureDiagnostic(
            run_id="run-obs-2",
            step_index=2,
            failed_tool="run_tests",
            error_code="TIMEOUT",
            message="Test exceeded 120s",
            why="Deadlock in database connection pool",
            can_resume=True,
            suggested_fix="Increase test timeout or restart test container",
        )

        summary = tracer.generate_summary(
            task="Run integration tests",
            status="failed",
            changed_files=["tests/test_db.py"],
            test_result="failed",
            diagnostic=diagnostic,
        )

        self.assertEqual(summary.run_id, "run-obs-2")
        self.assertEqual(summary.status, "failed")
        self.assertEqual(summary.tool_failure_count, 1)
        self.assertIn("read_file", summary.tools_used)
        self.assertIn("run_tests", summary.tools_used)
        self.assertIsNotNone(summary.failure_diagnostic)
        self.assertTrue(summary.failure_diagnostic.can_resume)

        md = summary.to_markdown()
        self.assertIn("# Run Execution Summary: `run-obs-2`", md)
        self.assertIn("run_tests", md)
        self.assertIn("TIMEOUT", md)
        self.assertIn("Deadlock in database connection pool", md)
