"""Tests for the Core Event Emitter (Release 1 - Task 1.2).

The engine records session events and, through the `EventAdapter`, broadcasts each one
to an `EventBus` as a typed UX event. These tests run real `plan()` loops against an
isolated bus and assert what a subscribing view (CLI status line, web SSE, memory)
would see — without the core printing anything itself.
"""
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import core, engine
from ai_code_engineer import events as ux
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import (apply_proposal, attach_bus, bus_of,
                                     event as record_event, json_safe, load_session, plan)
from ai_code_engineer.workspace import Workspace


class ScriptedProvider:
    model = "test"

    def __init__(self, responses):
        self.responses = iter(responses)

    def generate(self, messages):
        response = next(self.responses)
        return response if isinstance(response, str) else json.dumps(response)


class EmissionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.bus = ux.EventBus()

    def tearDown(self):
        self.temp.cleanup()

    def run_plan(self, responses, task="Update answer", **kwargs):
        kwargs.setdefault("progress", lambda _line: None)
        return plan(self.ws, task, ScriptedProvider(responses), Settings(),
                    self.base / "runs", event_bus=self.bus, **kwargs)

    def proposal(self, changes=None):
        return {"action": "propose", "summary": "Update answer", "checks": ["unit tests"],
                "changes": changes or [{"path": "app.py", "content": "answer = 2\n"}]}

    def typed(self, cls):
        return [item for item in self.bus.history if isinstance(item, cls)]

    def stream(self):
        return [ux.coerce(item) for item in self.bus.history]


class StageEmissionTests(EmissionCase):
    def test_stage_walk_broadcasts_stage_changed(self):
        self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        rows = self.typed(ux.StageChanged)
        self.assertTrue(rows)
        self.assertEqual(rows[0].stage, ux.Stage.UNDERSTANDING.value)
        moved = [(row.previous_stage, row.stage) for row in rows]
        self.assertIn((ux.Stage.PLANNING.value, ux.Stage.EXECUTING.value), moved)

    def test_every_move_after_the_first_names_where_it_came_from(self):
        self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        rows = self.typed(ux.StageChanged)
        self.assertEqual(rows[0].previous_stage, "", "the first move has no origin to invent")
        for row in rows[1:]:
            self.assertTrue(row.previous_stage, f"move without an origin: {row.stage}")

    def test_every_recorded_event_broadcasts_exactly_once(self):
        path = self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        session = load_session(path)
        self.assertEqual(len(self.bus.history), len(session["events"]))
        self.assertEqual([ux.coerce(item).kind for item in self.bus.history],
                         [ux.coerce(engine.structured_event(
                              session, item["kind"],
                              {k: v for k, v in item.items() if k not in ("at", "kind")})).kind
                          for item in session["events"]])

    def test_every_broadcast_is_a_known_kind(self):
        self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        for item in self.stream():
            self.assertTrue(item.known, f"unknown kind broadcast: {item.kind}")


class StepAndToolEmissionTests(EmissionCase):
    def test_read_step_broadcasts_step_updated_keyed_by_the_same_id(self):
        self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        reads = [row for row in self.typed(ux.StepUpdated) if row.action == "read_file"]
        self.assertTrue(reads)
        self.assertEqual(reads[0].status, "done")
        self.assertEqual(reads[0].details["path"], "app.py")
        stored = next(item for item in self.stream() if item.kind == "step_updated")
        self.assertEqual(stored.id, reads[0].step_id)
        self.assertTrue(reads[0].step_id)

    def test_tool_rows_broadcast_tool_call_at_verbose_level(self):
        self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        calls = self.typed(ux.ToolCall)
        self.assertTrue(any(call.tool_name == "read_file" for call in calls))
        self.assertTrue(all(call.level is ux.EventLevel.VERBOSE for call in calls))


class ProposalApprovalEmissionTests(EmissionCase):
    def test_proposal_broadcasts_approval_requested_keyed_by_the_proposal_hash(self):
        path = self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        approvals = self.typed(ux.ApprovalRequested)
        self.assertEqual(len(approvals), 1)
        session = load_session(path)
        self.assertEqual(approvals[0].approval_id, session["proposal_hash"])
        self.assertEqual(approvals[0].reason, session["task"])
        self.assertEqual(approvals[0].action, session["summary"])

    def test_completed_run_broadcasts_final_report(self):
        self.run_plan([{"action": "complete", "summary": "The answer is already 1."}],
                      task="Make the answer 1")
        reports = self.typed(ux.FinalReport)
        self.assertEqual(len(reports), 1)
        self.assertTrue(reports[0].success)
        self.assertEqual(reports[0].verification_status, ux.VerificationStatus.NOT_CHECKED.value)


class FileAndTestEmissionTests(EmissionCase):
    def apply(self, path, approved):
        # `apply_proposal` loads the session from disk; a live view attaches its bus to the
        # in-memory record exactly the way `plan()` does.
        with patch("ai_code_engineer.engine.load_session",
                   side_effect=lambda p: attach_bus(load_session(p), self.bus)):
            return apply_proposal(path, approved)

    def test_applied_file_broadcasts_file_changed_as_modified(self):
        path = self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        self.apply(path, load_session(path)["proposal_hash"])
        changed = self.typed(ux.FileChanged)
        self.assertEqual([row.path for row in changed], ["app.py"])
        self.assertEqual(changed[0].change_type, "modified")

    def test_removed_file_broadcasts_file_changed_as_deleted(self):
        delete = {"action": "propose", "summary": "Remove it", "checks": ["unit tests"],
                  "changes": [{"path": "app.py", "delete": True}]}
        path = self.run_plan([{"action": "read_file", "path": "app.py"}, delete])
        self.apply(path, load_session(path)["proposal_hash"])
        self.assertEqual(self.typed(ux.FileChanged)[-1].change_type, "deleted")

    def test_run_and_verification_rows_broadcast_test_result(self):
        session = {"id": "r" * 32, "events": [], "changes": []}
        attach_bus(session, self.bus)
        record_event(session, "run", recipe="python-unittest", status="passed",
                     exit_code=0, seconds=1.4)
        record_event(session, "verification", status="failed")
        results = self.typed(ux.TestResult)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].passed)
        self.assertFalse(results[1].passed)

    def test_proof_counts_reach_the_test_result(self):
        session = {"id": "r" * 32, "events": [], "changes": []}
        attach_bus(session, self.bus)
        record_event(session, "test_result", command="python -m unittest", status="passed",
                     proof={"tests": 4, "failures": 0, "errors": 0}, tail="OK")
        row = self.typed(ux.TestResult)[0]
        self.assertEqual((row.total_tests, row.passed_tests, row.failed_tests), (4, 4, 0))
        self.assertTrue(row.passed)

    def test_error_kind_maps_to_the_friendly_error_dataclass(self):
        session = {"id": "r" * 32, "events": [], "changes": []}
        attach_bus(session, self.bus)
        record_event(session, "error", error_type="ProviderError", message="model offline",
                     recoverable=False)
        row = self.typed(ux.AgentProgressError)[0]
        self.assertEqual(row.error_type, "ProviderError")
        self.assertFalse(row.recoverable)


class ErrorEmissionTests(EmissionCase):
    def test_refused_action_broadcasts_an_error_row(self):
        self.run_plan([{"action": "launch_missiles"},
                       {"action": "read_file", "path": "app.py"}, self.proposal()])
        errors = self.typed(ux.AgentProgressError)
        self.assertTrue(errors)
        self.assertEqual(errors[0].error_type, "rejected_action")
        self.assertTrue(errors[0].recoverable)

    def test_exhausted_run_broadcasts_a_stopped_error_at_normal_level(self):
        with self.assertRaises(engine.AgentError):
            self.run_plan([{"action": "list_files"}] * (Settings().max_turns + 2))
        stopped = [row for row in self.typed(ux.AgentProgressError)
                   if row.error_type == "stopped"]
        self.assertTrue(stopped)
        self.assertFalse(stopped[0].recoverable)
        self.assertTrue(stopped[0].message)


class LevelFilteringTests(EmissionCase):
    def test_context_rows_stay_debug_and_normal_subscribers_never_see_them(self):
        debug_seen = []
        self.bus.subscribe(debug_seen.append, min_level=ux.EventLevel.DEBUG)
        normal_seen = []
        self.bus.subscribe(normal_seen.append)
        self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()],
                      task="Update the answer in app.py")
        contexts = [ux.coerce(item) for item in debug_seen
                    if ux.coerce(item).kind in ("context_file", "context_excerpt")]
        self.assertTrue(contexts, "deterministic retrieval emitted no context row to filter")
        self.assertFalse([item for item in normal_seen
                          if ux.coerce(item).kind in ("context_file", "context_excerpt")])

    def test_debug_subscribers_see_everything_the_bus_broadcast(self):
        seen = []
        self.bus.subscribe(seen.append, min_level=ux.EventLevel.DEBUG)
        self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        self.assertEqual(len(seen), len(self.bus.history))


class CoreStaysSilentTests(EmissionCase):
    def test_plan_never_falls_back_to_print(self):
        buffer = io.StringIO()
        with patch.object(sys, "stdout", buffer):
            plan(self.ws, "Update answer",
                 ScriptedProvider([{"action": "read_file", "path": "app.py"}, self.proposal()]),
                 Settings(), self.base / "runs", event_bus=self.bus)
        self.assertEqual(buffer.getvalue(), "")

    def test_session_file_never_serializes_the_bus(self):
        path = self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("_event_bus", text)
        json.loads(text)

    def test_json_safe_strips_the_marker_without_touching_the_live_run(self):
        session = {"id": "x", "events": [], "stage": "plan"}
        attach_bus(session, self.bus)
        stripped = json_safe(session)
        self.assertNotIn("_event_bus", stripped)
        self.assertIs(bus_of(stripped), ux.global_bus)
        self.assertIs(bus_of(session), self.bus)

    def test_unknown_kinds_travel_as_events_without_dropping_rows(self):
        session = {"id": "r" * 32, "events": []}
        attach_bus(session, self.bus)
        record_event(session, "brand_new_kind", detail="kept")
        row = self.stream()[-1]
        self.assertEqual(row.kind, "brand_new_kind")
        self.assertEqual(row.data["detail"], "kept")

    def test_a_broken_subscriber_cannot_fail_the_run(self):
        self.bus.subscribe(boom)
        path = self.run_plan([{"action": "read_file", "path": "app.py"}, self.proposal()])
        self.assertTrue(path.exists())

    def test_a_run_broadcasts_on_the_process_bus_when_none_is_given(self):
        seen = []
        unsubscribe = watch_global(seen.append)
        try:
            plan(self.ws, "Update answer",
                 ScriptedProvider([{"action": "read_file", "path": "app.py"}, self.proposal()]),
                 Settings(), self.base / "runs", progress=lambda _line: None)
        finally:
            unsubscribe()
        self.assertTrue(any(ux.coerce(item).kind == "stage_changed" for item in seen))


class AdapterContractTests(unittest.TestCase):
    def test_adapter_maps_every_engine_stage_code(self):
        self.assertEqual(set(engine.STAGE_TO_UI), set(core.STAGES))

    def test_level_table_covers_every_kind_the_engine_records(self):
        source = Path(engine.__file__).read_text(encoding="utf-8")
        recorded = set(re.findall(r'event\(session, "(\w+)"', source))
        self.assertEqual(sorted(kind for kind in recorded if kind not in engine.EVENT_LEVELS), [])


def boom(event):
    raise RuntimeError("subscriber is broken")


def watch_global(callback):
    ux.global_bus.subscribe(callback, min_level=ux.EventLevel.DEBUG)

    def unsubscribe():
        with ux.global_bus._lock:
            ux.global_bus._subscribers = [pair for pair in ux.global_bus._subscribers
                                          if pair[0] is not callback]
    return unsubscribe


if __name__ == "__main__":
    unittest.main()
