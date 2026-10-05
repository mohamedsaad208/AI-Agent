"""Tests for the CLI status line and engineering event renderer (Release 1 - Task 1.3).

The core broadcasts typed UX events; the CLI view must turn each one into a labelled
engineering line (never chat prose) driven by the fixed symbol vocabulary ✓ ✗ ● ○ !, and
keep a persistent status line showing the current stage and a step checklist. These tests
feed events straight into :class:`CliView` and, in one integration case, run a real
``engine.plan`` loop so the renderer is checked against the emitter it is built for.
"""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from ai_code_engineer import cli_view as cv
from ai_code_engineer import events as ux
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import plan
from ai_code_engineer.workspace import Workspace


def plain(text) -> str:
    return text.plain


class ScriptedProvider:
    model = "test"

    def __init__(self, responses):
        self.responses = iter(responses)

    def generate(self, messages):
        response = next(self.responses)
        return response if isinstance(response, str) else json.dumps(response)


class RenderHarness(unittest.TestCase):
    """Feed events through a view (optionally through the bus) and capture what it prints."""

    def setUp(self):
        self.buffer = io.StringIO()
        self.console = Console(file=self.buffer, force_terminal=False,
                               no_color=True, width=100)

    def view(self, **kwargs):
        kwargs.setdefault("console", self.console)
        kwargs.setdefault("live", False)
        return cv.CliView(**kwargs)

    def last(self, view):
        self.assertTrue(view.lines, "expected at least one rendered line")
        return plain(view.lines[-1])

    def output(self):
        return self.buffer.getvalue()


class KindRenderingTests(RenderHarness):
    def test_stage_change_is_a_running_marker_then_the_move(self):
        view = self.view()
        view.handle(ux.StageChanged(stage="UNDERSTANDING"))
        self.assertEqual(self.last(view), "\u25cf [STAGE] UNDERSTANDING")
        view.handle(ux.StageChanged(stage="EXECUTING", previous_stage="PLANNING"))
        self.assertEqual(self.last(view), "\u25cf [STAGE] PLANNING \u2192 EXECUTING")

    def test_completed_and_failed_stages_earn_their_own_glyph(self):
        view = self.view()
        view.handle(ux.StageChanged(stage=ux.Stage.COMPLETED.value))
        self.assertTrue(self.last(view).startswith("\u2713"))
        view.handle(ux.StageChanged(stage=ux.Stage.FAILED.value))
        self.assertTrue(self.last(view).startswith("\u2717"))

    def test_step_done_carries_its_action_and_target_path(self):
        view = self.view()
        view.handle(ux.StepUpdated(step_id="s1", action="read_file", status="done",
                                   details={"path": "app.py"}))
        self.assertEqual(self.last(view), "\u2713 [STEP] read_file \u2192 app.py")

    def test_running_step_is_a_spinner_and_pending_a_hollow_ring(self):
        view = self.view()
        view.handle(ux.StepUpdated(step_id="s1", action="edit", status="running"))
        self.assertTrue(self.last(view).startswith("\u25cf [STEP] edit"))
        view.handle(ux.StepUpdated(step_id="s2", action="verify", status="pending"))
        self.assertTrue(self.last(view).startswith("\u25cb [STEP] verify"))

    def test_tool_call_is_tagged_and_times_the_call(self):
        view = self.view(level=ux.EventLevel.DEBUG)
        view.handle(ux.ToolCall(tool_name="search", duration_ms=12, status="ok"))
        self.assertEqual(self.last(view), "\u2713 [TOOL] search  (12ms)")

    def test_file_change_reports_additions_and_deletions_apart(self):
        view = self.view()
        view.handle(ux.FileChanged(path="app.py", change_type="modified",
                                   lines_added=3, lines_removed=1))
        self.assertEqual(self.last(view), "\u2713 [CHANGE] modified app.py  +3 -1")
        view.handle(ux.FileChanged(path="gone.py", change_type="deleted"))
        self.assertTrue(self.last(view).startswith("! [CHANGE] deleted gone.py"))

    def test_test_result_scores_passed_and_failed_differently(self):
        view = self.view()
        view.handle(ux.TestResult(command="python -m unittest", passed=True,
                                  total_tests=4, passed_tests=4, failed_tests=0))
        self.assertEqual(self.last(view), "\u2713 [TEST] python -m unittest  4/4 passed")
        view.handle(ux.TestResult(command="pytest", passed=False,
                                  total_tests=4, passed_tests=2, failed_tests=2))
        self.assertEqual(self.last(view), "\u2717 [TEST] pytest  2/4 passed")

    def test_approval_asks_with_its_risk_and_one_key_choices(self):
        view = self.view()
        view.handle(ux.ApprovalRequested(action="write 2 files", risk_level="high"))
        self.assertEqual(self.last(view), "! [ASK] write 2 files  [risk: high]  y/n/d")

    def test_error_distinguishes_recoverable_noise_from_a_fatal_stop(self):
        view = self.view()
        view.handle(ux.AgentProgressError(error_type="retry", message="transient",
                                          recoverable=True))
        self.assertTrue(self.last(view).startswith("! [WARN] transient"))
        view.handle(ux.AgentProgressError(error_type="ProviderError", message="model offline",
                                          recoverable=False))
        self.assertTrue(self.last(view).startswith("\u2717 [WARN] model offline"))

    def test_final_report_reports_verification_and_file_count(self):
        view = self.view()
        view.handle(ux.FinalReport(task="fix", success=True, verification_status="VERIFIED",
                                   files_changed=("a.py", "b.py")))
        self.assertEqual(self.last(view), "\u2713 [DONE] VERIFIED  files:2")


class UnknownKindTests(RenderHarness):
    def test_an_unknown_normal_kind_still_gets_a_line_from_its_message(self):
        view = self.view()
        view.handle(ux.Event(kind="impact", data={"message": "3 call sites touched"}))
        self.assertEqual(self.last(view), "\u25cf [IMPACT] 3 call sites touched")

    def test_a_silent_unknown_kind_writes_nothing(self):
        view = self.view()
        view.handle(ux.Event(kind="block_chosen", data={"note": "no message field"},
                             level=ux.EventLevel.DEBUG))
        self.assertEqual(view.lines, [])


class LevelFilteringTests(RenderHarness):
    def test_a_normal_view_hides_verbose_and_debug_but_shows_normal(self):
        view = self.view(level=ux.EventLevel.NORMAL)
        view.handle(ux.ToolCall(tool_name="search", status="ok"))       # verbose
        view.handle(ux.Event(kind="context_file", data={"message": "x"},
                             level=ux.EventLevel.DEBUG))               # debug
        view.handle(ux.StageChanged(stage="PLANNING"))                  # normal
        self.assertEqual([plain(line) for line in view.lines],
                         ["\u25cf [STAGE] PLANNING"])

    def test_a_verbose_view_admits_tool_calls_but_not_debug(self):
        view = self.view(level=ux.EventLevel.VERBOSE)
        view.handle(ux.ToolCall(tool_name="search", status="ok"))
        view.handle(ux.Event(kind="context_file", data={"message": "x"},
                             level=ux.EventLevel.DEBUG))
        self.assertEqual([plain(line) for line in view.lines],
                         ["\u2713 [TOOL] search"])


class StatusLineTests(RenderHarness):
    def test_status_shows_stage_then_a_running_step_checklist(self):
        view = self.view()
        view.handle(ux.StageChanged(stage="EXECUTING"))
        view.handle(ux.StepUpdated(step_id="s1", action="read", status="done"))
        view.handle(ux.StepUpdated(step_id="s2", action="edit", status="running"))
        view.handle(ux.StepUpdated(step_id="s3", action="test", status="pending"))
        status = plain(view.status())
        self.assertIn("EXECUTING", status)
        self.assertIn("\u2713 read", status)
        self.assertIn("\u25cf edit", status)
        self.assertIn("\u25cb test", status)

    def test_note_updates_activity_without_polluting_the_log(self):
        view = self.view(level=ux.EventLevel.DEBUG)  # DEBUG keeps note out of the visible stream
        lines_before = len(view.lines)
        view.note("Turn 1/4: asking model...")
        self.assertEqual(len(view.lines), lines_before)
        self.assertIn("Turn 1/4", plain(view.status()))

    def test_final_report_folds_the_status_line_shut(self):
        view = self.view()
        view.handle(ux.StepUpdated(step_id="s1", action="read", status="done"))
        view.handle(ux.FinalReport(task="fix", success=True, verification_status="VERIFIED"))
        status = plain(view.status())
        self.assertIn(ux.Stage.COMPLETED.value, status)
        self.assertNotIn("read", status)


class StreamingAndColorTests(RenderHarness):
    def test_a_non_terminal_view_streams_rendered_lines_to_the_stream(self):
        view = self.view(live=False)
        view.handle(ux.StageChanged(stage="UNDERSTANDING"))
        view.handle(ux.StepUpdated(step_id="s1", action="read", status="done"))
        out = self.output()
        self.assertIn("\u25cf [STAGE] UNDERSTANDING", out)
        self.assertIn("\u2713 [STEP] read", out)

    def test_no_color_writes_glyphs_but_never_color_escape_sequences(self):
        colorless = Console(file=self.buffer, force_terminal=True, no_color=True, width=100)
        view = cv.CliView(console=colorless, live=False, level=ux.EventLevel.NORMAL)
        view.handle(ux.FileChanged(path="app.py", change_type="modified", lines_added=2))
        out = self.output()
        self.assertIn("[CHANGE]", out)
        self.assertIn("\u2713", out)          # the symbol still carries the state
        self.assertNotIn("\x1b[3", out)       # no foreground colour: NO_COLOR honoured
        self.assertNotIn("\x1b[9", out)       # no bright foreground either


class EventViewLifecycleTests(unittest.TestCase):
    def test_the_view_unsubscribes_and_leaves_no_listener_behind(self):
        bus = ux.EventBus()
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, no_color=True)
        with cv.event_view(bus, console=console, level=ux.EventLevel.DEBUG) as view:
            bus.emit(ux.StageChanged(stage="EXECUTING"))
        self.assertEqual(bus._subscribers, [])
        self.assertTrue(any("EXECUTING" in plain(line) for line in view.lines))

    def test_a_live_console_still_renders_the_engineering_lines(self):
        bus = ux.EventBus()
        buffer = io.StringIO()
        terminal = Console(file=buffer, force_terminal=True, no_color=True, width=100)
        with cv.event_view(bus, console=terminal, level=ux.EventLevel.DEBUG):
            bus.emit(ux.StageChanged(stage="EXECUTING"))
            bus.emit(ux.StepUpdated(step_id="s1", action="edit", status="done"))
        self.assertIn("STAGE", buffer.getvalue())
        self.assertIn("STEP", buffer.getvalue())


class EngineIntegrationTests(unittest.TestCase):
    """The renderer is checked against the emitter it exists to serve (Task 1.2)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.bus = ux.EventBus()
        self.buffer = io.StringIO()
        self.console = Console(file=self.buffer, force_terminal=False, no_color=True, width=100)
        self.view = cv.CliView(console=self.console, live=False, level=ux.EventLevel.DEBUG)
        self.bus.subscribe(self.view.handle, min_level=ux.EventLevel.DEBUG)

    def tearDown(self):
        self.temp.cleanup()

    def plan_run(self, responses, task="Update answer"):
        return plan(self.ws, task, ScriptedProvider(responses), Settings(),
                    self.base / "runs", event_bus=self.bus, progress=self.view.note)

    def proposal(self):
        return {"action": "propose", "summary": "Update answer", "checks": ["unit tests"],
                "changes": [{"path": "app.py", "content": "answer = 2\n"}]}

    def test_a_real_plan_walk_renders_stage_steps_and_a_read_line(self):
        self.plan_run([{"action": "read_file", "path": "app.py"}, self.proposal()])
        stream = "\n".join(plain(line) for line in self.view.lines)
        self.assertIn("[STAGE]", stream)
        self.assertIn("[STEP]", stream)
        self.assertIn("read_file", stream)
        self.assertIn("app.py", stream)

    def test_a_completed_run_leaves_the_status_line_on_completed(self):
        self.plan_run([{"action": "complete", "summary": "already correct"}],
                      task="Make the answer 1")
        self.assertIn(ux.Stage.COMPLETED.value, plain(self.view.status()))


if __name__ == "__main__":
    unittest.main()
