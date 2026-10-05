"""Unit tests for the Unified Event Schema (Release 1 - Task 1.1).

Tests dataclass serialization, EventKind verification, EventBus level filtering,
and bidirectional round-tripping for both session and stream dialects.
"""
import json
import unittest
from ai_code_engineer.events import (
    Event, EventKind, EventLevel, Stage, VerificationStatus,
    StageChanged, StepUpdated, ToolCall, FileChanged, TestResult,
    ApprovalRequested, AgentProgressError, FinalReport,
    EventBus, coerce, make
)


class EventSchemaTests(unittest.TestCase):
    def test_stage_changed_event_serialization(self):
        sc = StageChanged(stage=Stage.PLANNING.value, previous_stage=Stage.UNDERSTANDING.value,
                          message="Drafting change proposal")
        ev = sc.to_event()
        self.assertEqual(ev.kind, EventKind.STAGE_CHANGED.value)
        self.assertEqual(ev.data["stage"], "PLANNING")
        self.assertEqual(ev.data["previous_stage"], "UNDERSTANDING")
        self.assertEqual(ev.data["level"], EventLevel.NORMAL.value)

        d = sc.to_dict()
        self.assertIn("at", d)
        self.assertEqual(d["kind"], "stage_changed")
        self.assertEqual(d["stage"], "PLANNING")

    def test_step_updated_event(self):
        su = StepUpdated(step_id="step-123", action="read_file", status="running",
                         details={"path": "auth.py"})
        ev = su.to_event()
        self.assertEqual(ev.kind, EventKind.STEP_UPDATED.value)
        self.assertEqual(ev.id, "step-123")
        self.assertEqual(ev.data["action"], "read_file")
        self.assertEqual(ev.data["status"], "running")

    def test_tool_call_event(self):
        tc = ToolCall(tool_name="list_files", args={"limit": 50}, output_summary="Found 12 files",
                      duration_ms=45.2, status="ok")
        ev = tc.to_event()
        self.assertEqual(ev.kind, EventKind.TOOL_CALL.value)
        self.assertEqual(ev.data["tool_name"], "list_files")
        self.assertEqual(ev.data["duration_ms"], 45.2)

    def test_file_changed_event(self):
        fc = FileChanged(path="src/App.java", change_type="modified",
                         diff_summary="+5 -2", lines_added=5, lines_removed=2)
        ev = fc.to_event()
        self.assertEqual(ev.kind, EventKind.FILE_CHANGED.value)
        self.assertEqual(ev.data["path"], "src/App.java")
        self.assertEqual(ev.data["lines_added"], 5)

    def test_test_result_event(self):
        tr = TestResult(command="mvn test", passed=True, total_tests=12,
                        passed_tests=12, failed_tests=0, output_tail="BUILD SUCCESS")
        ev = tr.to_event()
        self.assertEqual(ev.kind, EventKind.TEST_RESULT.value)
        self.assertTrue(ev.data["passed"])
        self.assertEqual(ev.data["total_tests"], 12)

    def test_approval_requested_event(self):
        ar = ApprovalRequested(action="delete_file", risk_level="high",
                               reason="Deleting obsolete service", approval_id="appr-99")
        ev = ar.to_event()
        self.assertEqual(ev.kind, EventKind.APPROVAL_REQUESTED.value)
        self.assertEqual(ev.id, "appr-99")
        self.assertEqual(ev.data["risk_level"], "high")

    def test_agent_progress_error_event(self):
        err = AgentProgressError(error_type="SyntaxError", message="Unexpected token",
                                 root_cause="Missing semicolon", recoverable=True)
        ev = err.to_event()
        self.assertEqual(ev.kind, EventKind.ERROR.value)
        self.assertEqual(ev.data["error_type"], "SyntaxError")
        self.assertTrue(ev.data["recoverable"])

    def test_final_report_event(self):
        fr = FinalReport(task="Implement register endpoint", success=True,
                         duration_seconds=14.5, files_changed=("AuthService.java",),
                         verification_status=VerificationStatus.VERIFIED.value,
                         test_summary="14/14 tests passed")
        ev = fr.to_event()
        self.assertEqual(ev.kind, EventKind.FINAL_REPORT.value)
        self.assertTrue(ev.data["success"])
        self.assertEqual(ev.data["verification_status"], "VERIFIED")

    def test_round_trip_json_and_dict(self):
        ev = Event(kind="test_kind", id="req-1", data={"key": "val", "num": 42})
        text = ev.to_json()
        restored = Event.from_json(text)
        self.assertEqual(ev.kind, restored.kind)
        self.assertEqual(ev.id, restored.id)
        self.assertEqual(ev.data, restored.data)

    def test_coerce_various_inputs(self):
        raw_dict = {"kind": "stage", "at": "2026-10-05T00:00:00Z", "to": "plan"}
        coerced = coerce(raw_dict)
        self.assertEqual(coerced.kind, "stage")
        self.assertEqual(coerced.data["to"], "plan")

        sc = StageChanged(stage="EXECUTING")
        coerced_sc = coerce(sc)
        self.assertEqual(coerced_sc.kind, EventKind.STAGE_CHANGED.value)
        self.assertEqual(coerced_sc.data["stage"], "EXECUTING")

    def test_event_bus_subscribe_and_emit(self):
        bus = EventBus()
        received = []
        bus.subscribe(lambda e: received.append(e))

        sc = StageChanged(stage="PLANNING")
        bus.emit(sc)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].stage, "PLANNING")
        self.assertEqual(len(bus.history), 1)

    def test_event_bus_level_filtering(self):
        bus = EventBus()
        normal_events = []
        verbose_events = []

        bus.subscribe(lambda e: normal_events.append(e), min_level=EventLevel.NORMAL)
        bus.subscribe(lambda e: verbose_events.append(e), min_level=EventLevel.VERBOSE)

        sc = StageChanged(stage="PLANNING", level=EventLevel.NORMAL)
        tc = ToolCall(tool_name="read_file", level=EventLevel.VERBOSE)

        bus.emit(sc)
        bus.emit(tc)

        # Normal subscriber receives normal events
        self.assertIn(sc, normal_events)
        # Verbose subscriber receives both normal and verbose events
        self.assertIn(sc, verbose_events)
        self.assertIn(tc, verbose_events)

    def test_event_bus_isolated_exceptions(self):
        bus = EventBus()
        def faulty(e):
            raise RuntimeError("Boom")
        good = []
        bus.subscribe(faulty)
        bus.subscribe(lambda e: good.append(e))

        bus.emit(StageChanged(stage="COMPLETED"))
        self.assertEqual(len(good), 1)


if __name__ == "__main__":
    unittest.main()
