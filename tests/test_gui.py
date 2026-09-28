"""Desktop behavior tests use withdrawn windows and synthetic model responses."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import labels, memory, repair
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import (atomic_json, load_session, plan, proposal_hash)
from ai_code_engineer.errors import Cancelled
from ai_code_engineer.gui import AgentWindow, SEARCH_PLACEHOLDER
from ai_code_engineer.providers import OpenRouterProvider
from ai_code_engineer.workspace import Workspace

OLLAMA_ENTRY = {"id": "test-local", "name": "Test Local", "cloud": False,
                "description": "Synthetic local model for tests"}
FREE_ENTRY = {"id": "x:free", "name": "X Free", "free": True, "cloud": True,
              "description": "Synthetic free cloud model"}

# "Fix the add function", built from code points so this file stays ASCII and cannot itself arrive
# mangled — a mojibake string looks like Arabic in a terminal and is not.
ARABIC_TASK = "".join(map(chr, [0x0635, 0x0644, 0x0651, 0x062D])) + " " + \
              "".join(map(chr, [0x062F, 0x0627, 0x0644, 0x0629])) + " " + \
              "".join(map(chr, [0x0627, 0x0644, 0x062C, 0x0645, 0x0639]))


class FakeModel:
    model = "test-local"

    def generate(self, messages):
        return json.dumps({"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
                           "changes": [{"path": "calculator.py", "content": "def add(a, b):\n    return a + b\n"}]})


class RecordingModel(FakeModel):
    """Keeps the prompts so a test can prove what context actually reached the model."""

    def __init__(self):
        self.prompts = []

    def generate(self, messages):
        self.prompts.append(messages)
        return FakeModel.generate(self, messages)


class CancellationTests(unittest.TestCase):
    def test_cancel_after_model_reply_cannot_propose(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            repo = folder / "repo"
            repo.mkdir()
            (repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
            cancel = threading.Event()

            class CancellingModel(FakeModel):
                def generate(self, messages):
                    cancel.set()
                    return super().generate(messages)

            with self.assertRaises(Cancelled):
                plan(Workspace(repo), "Fix calculator.py", CancellingModel(), Settings(), folder / "runs",
                     progress=lambda _: None, cancelled=cancel.is_set)
            session = load_session(next((folder / "runs").glob("*/session.json")))
            self.assertEqual(session["state"], "CANCELLED")
            self.assertNotIn("changes", session)
            self.assertIn("a - b", (repo / "calculator.py").read_text())

    def test_gui_key_does_not_change_environment(self):
        from dataclasses import replace
        before = os.environ.get("OPENROUTER_API_KEY")
        provider = OpenRouterProvider(replace(Settings(), model="openrouter/free"), api_key="synthetic-test-key")
        self.assertEqual(provider.key, "synthetic-test-key")
        self.assertEqual(os.environ.get("OPENROUTER_API_KEY"), before)


class ChatModel:
    model = "test-local"

    def __init__(self):
        self.calls = []

    def generate(self, messages, json_mode=True):
        self.calls.append((messages, json_mode))
        return "A monotonic clock never moves backwards."


class ScriptedModel:
    """Answers with queued tool actions so a repair turn needs no real model."""

    model = "test-local"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, messages, json_mode=True):
        self.calls.append(messages)
        value = self.responses.pop(0) if self.responses else {"action": "blocked", "reason": "queue empty"}
        return json.dumps(value)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        self.repo.mkdir()
        (self.repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
        # The startup catalog refresh must stay offline in tests.
        patches = (patch("ai_code_engineer.gui.ollama_models", return_value=[OLLAMA_ENTRY]),
                   patch("ai_code_engineer.gui.openrouter_models", return_value=[FREE_ENTRY]))
        for started in patches:
            started.start()
            self.addCleanup(started.stop)
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.temp.cleanup()
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.ui = AgentWindow(self.root, self.app_dir)
        self.root.update_idletasks()
        self.ui.catalogs["Ollama"] = [OLLAMA_ENTRY]

    def tearDown(self):
        # A pending after() callback fires into a destroyed interpreter and Tk prints
        # 'invalid command name "…poll"' — the noise the window now has to cancel explicitly.
        window = getattr(self, "ui", None)
        if window is not None:
            window.cancel_timers()
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.temp.cleanup()

    def wait_for_job(self):
        deadline = time.monotonic() + 5
        while self.ui.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(self.ui.busy, "UI job did not finish")
        self.root.update_idletasks()

    def ask(self, text="Why is a monotonic clock safer for timeouts?"):
        """Send a message with no project selected."""
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.ui.model.set("test-local")
        self.ui.task.insert("1.0", text)
        model = ChatModel()
        with patch("ai_code_engineer.gui.make_provider", return_value=model) as factory:
            self.ui.start_plan()
            self.wait_for_job()
        return model, factory

    def draft(self):
        self.ui.repo.set(str(self.repo))
        self.ui.task.insert("1.0", "Fix calculator.py")
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.ui.model.set("test-local")
        with patch("ai_code_engineer.gui.make_provider", return_value=FakeModel()):
            self.ui.start_plan()
            self.wait_for_job()
        self.assertEqual(self.ui.session["state"], "WAITING_APPROVAL")

    def test_plan_review_approve_verify_and_rollback(self):
        self.draft()
        self.assertIn("return a + b", self.ui.code_views["diff"].get("1.0", "end"))
        self.assertEqual(str(self.ui.apply_button["state"]), "normal")
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=False):
            self.ui.apply()
        self.assertIn("a - b", (self.repo / "calculator.py").read_text())
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.apply()
            self.wait_for_job()
        self.assertIn("a + b", (self.repo / "calculator.py").read_text())
        self.assertEqual(self.ui.session["state"], "APPLIED_UNVERIFIED")
        self.ui.check_changes()
        self.wait_for_job()
        self.assertEqual(self.ui.session["state"], "VERIFICATION_BLOCKED")
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.undo()
            self.wait_for_job()
        self.assertIn("a - b", (self.repo / "calculator.py").read_text())
        self.assertEqual(self.ui.session["state"], "ROLLED_BACK")

    def test_cloud_requires_explicit_data_approval(self):
        self.ui.repo.set(str(self.repo))
        self.ui.task.insert("1.0", "Fix calculator.py")
        self.ui.catalogs["OpenRouter · Free"] = [FREE_ENTRY]
        self.ui.mode.set("OpenRouter · Free")
        self.ui.mode_changed()
        self.ui.model.set("x:free")
        with patch("ai_code_engineer.gui.make_provider") as provider:
            self.ui.start_plan()
            provider.assert_not_called()
        self.assertFalse(self.ui.busy)

    def test_reopening_session_still_confirms_before_apply(self):
        self.draft()
        self.ui.open_session(self.ui.session_path)
        self.assertEqual(str(self.ui.apply_button["state"]), "normal")
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=False):
            self.ui.apply()
        self.assertIn("a - b", (self.repo / "calculator.py").read_text())

    def test_changed_proposal_does_not_reuse_displayed_approval(self):
        self.draft()
        path = self.ui.session_path
        updated = json.loads(path.read_text())
        updated["summary"] = "Changed after review"
        updated["proposal_hash"] = proposal_hash(updated)
        path.write_text(json.dumps(updated))
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.apply()
            self.wait_for_job()
        self.assertIn("a - b", (self.repo / "calculator.py").read_text())
        self.assertEqual(self.ui.session["state"], "WAITING_APPROVAL")

    def test_window_stays_responsive_during_work(self):
        release = threading.Event()
        self.ui.run_job(lambda: release.wait(2), lambda _: None, "test")
        self.root.update()
        self.assertEqual(str(self.ui.start_button["state"]), "disabled")
        marker = []
        self.root.after(0, lambda: marker.append(True))
        self.root.update()
        self.assertEqual(marker, [True])
        release.set()
        self.wait_for_job()
        self.assertEqual(str(self.ui.start_button["state"]), "normal")

    def test_mode_changes_and_empty_input(self):
        self.ui.start_plan()
        self.assertFalse(self.ui.busy)
        self.ui.catalogs["OpenRouter · Free"] = [FREE_ENTRY]
        self.ui.mode.set("OpenRouter · Free")
        self.ui.mode_changed()
        self.ui.model.set("x:free")
        self.ui.cloud_ok.set(True)
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.assertFalse(self.ui.cloud_ok.get())
        self.assertNotEqual(self.ui.model.get(), "x:free")

    def test_switching_project_with_draft_asks_and_can_cancel(self):
        self.draft()
        other = self.app_dir / "other"
        other.mkdir()
        (other / "note.txt").write_text("keep")
        self.ui.task.delete("1.0", "end")
        self.ui.task.insert("1.0", "Unsent draft")
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=False) as confirm:
            self.ui.repo.set(str(other))
        confirm.assert_called_once()
        self.assertEqual(self.ui.repo.get(), str(self.repo))
        self.assertIn("Unsent draft", self.ui.task.get("1.0", "end"))
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.repo.set(str(other))
        self.assertEqual(self.ui.repo.get(), str(other))
        self.assertEqual(self.ui.task.get("1.0", "end").strip(), "")

    def test_preferences_survive_restart(self):
        self.ui.repo.set(str(self.repo))
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.ui.model.set("test-local")
        self.ui.model_changed()
        self.ui.catalogs["OpenRouter · Free"] = [FREE_ENTRY]
        self.ui.mode.set("OpenRouter · Free")
        self.ui.mode_changed()
        self.ui.model.set("x:free")
        self.ui.model_changed()
        self.ui.chained.set(True)
        self.ui.cancel_timers()
        self.root.destroy()

        root = tk.Tk()
        root.withdraw()
        reopened = None
        try:
            reopened = AgentWindow(root, self.app_dir)
            root.update_idletasks()
            self.assertEqual(reopened.mode.get(), "OpenRouter · Free")
            # A folder granted in an earlier session stays listed, but it is not what the window
            # opens on: a greeting must never land in Change mode because of stale state.
            self.assertEqual(reopened.repo.get(), "")
            self.assertIn(str(self.repo), reopened.projects.values())
            self.assertEqual(reopened._pending_model, "x:free")
            self.assertTrue(reopened.chained.get())
            # A restored cloud mode must show the consent UI, not hide it.
            self.assertEqual(reopened.cloud_frame.winfo_manager(), "pack")
            self.assertEqual(reopened.key_frame.winfo_manager(), "pack")
        finally:
            if reopened is not None:
                reopened.cancel_timers()
            root.destroy()

    def test_scheduled_callbacks_are_tracked_and_cancellable(self):
        """An untracked after() id fires into a destroyed interpreter and Tk shouts on stderr."""
        fired = []
        self.ui._schedule(1, lambda: fired.append("ran"))
        deadline = time.monotonic() + 3
        while not fired and time.monotonic() < deadline:
            self.root.update()
        self.assertEqual(fired, ["ran"], "a scheduled callback still runs on time")
        self.assertNotIn("ran", str(self.ui._timers), "a fired timer stops being pending")

        self.ui._schedule(10_000, lambda: fired.append("never"))
        pending = self.ui.cancel_timers()
        self.root.update()
        self.assertEqual(fired, ["ran"], "a cancelled callback must not fire")
        self.assertGreaterEqual(pending, 2, "poll and the startup check arm themselves")
        self.assertEqual(self.ui._timers, set())

    def run_result(self, status="failed", **overrides):
        result = {"recipe": "python-unittest", "label": "Python unittest",
                  "command": "python -m unittest discover -s tests", "status": status,
                  "exit_code": 0 if status == "passed" else 1, "seconds": 4.0,
                  "tests_observed": True, "truncated": False, "timed_out": False,
                  "output": "FAILED (failures=1)", "tail": "FAILED (failures=1)",
                  "failures": ["FAIL: test_add (tests.test_calc)"]}
        result.update(overrides)
        return result

    def make_runnable(self):
        (self.repo / "tests").mkdir(exist_ok=True)
        self.ui.refresh_recipes()
        self.assertIn("python-unittest", self.ui.recipes)

    def applied_draft(self):
        self.draft()
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.apply()
            self.wait_for_job()
        self.assertEqual(self.ui.session["state"], "APPLIED_UNVERIFIED")

    def test_apply_dialog_warns_when_most_of_a_file_disappears(self):
        (self.repo / "config.py").write_text("".join(f"option_{i} = {i}\n" for i in range(12)),
                                             encoding="utf-8")
        self.ui.repo.set(str(self.repo))
        self.ui.task.insert("1.0", "Strip config.py")
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.ui.model.set("test-local")
        model = ScriptedModel([{"action": "read_file", "path": "config.py"},
                               {"action": "propose", "summary": "Strip the options",
                                "checks": ["module imports"],
                                "changes": [{"path": "config.py", "content": "option_0 = 0\n"}]}])
        with patch("ai_code_engineer.gui.make_provider", return_value=model):
            self.ui.start_plan()
            self.wait_for_job()
        self.assertEqual(self.ui.session["state"], "WAITING_APPROVAL")
        self.assertIn("removes 11 of 12 existing lines", self.ui.removal_notice())
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=False) as confirm:
            self.ui.apply()
        self.assertIn("Removes most of an existing file", confirm.call_args[0][1])
        self.assertEqual((self.repo / "config.py").read_text(encoding="utf-8").count("\n"), 12)

    def test_apply_dialog_discloses_the_command_that_runs_next(self):
        """Approving a fix while Run & fix is armed also approves another project-code run."""
        self.draft()
        self.make_runnable()
        self.ui._auto_fix = True
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=False) as confirm:
            self.ui.apply()
        self.assertIn("executes the project's own build and test code", confirm.call_args[0][1])
        self.ui._auto_fix = False
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=False) as confirm:
            self.ui.apply()
        self.assertNotIn("executes the project", confirm.call_args[0][1])

    def test_the_fallback_window_reads_the_language_from_the_task_in_front_of_it(self):
        """Tk had no answer to this question at all: it read `STATES` directly, so the window that
        exists for users without Chromium stayed English under an Arabic task."""
        self.ui.session = {"state": "CHECKS_PASSED", "task": ARABIC_TASK}
        self.assertTrue(self.ui.arabic)
        self.ui.session = {"state": "CHECKS_PASSED", "task": "Fix add in calculator.py"}
        self.assertFalse(self.ui.arabic)
        self.ui.session = None
        self.ui.messages = [("You", ARABIC_TASK), ("AI Code Engineer", "Done")]
        self.assertTrue(self.ui.arabic, "no session yet, so the last thing they typed decides")
        self.ui.messages = []
        self.assertFalse(self.ui.arabic)

    def test_an_arabic_bubble_is_right_aligned_and_an_english_one_is_not(self):
        self.ui.messages = [("AI Code Engineer", ARABIC_TASK), ("AI Code Engineer", "Plain English")]
        self.ui.render_messages()
        views = [child for child in self.ui.conversation.winfo_children()
                 if isinstance(child, tk.Text)]
        self.assertEqual(len(views), 2)
        self.assertIn("rtl", views[0].tag_names("1.0"))
        self.assertNotIn("rtl", views[1].tag_names("1.0"))

    def test_the_headline_moves_to_the_side_the_sentence_is_written_on(self):
        self.ui.state_label.set(labels.STATES_AR["WAITING_APPROVAL"])
        self.assertEqual(str(self.ui.headline.cget("anchor")), "e")
        self.ui.state_label.set(labels.STATES["WAITING_APPROVAL"])
        self.assertEqual(str(self.ui.headline.cget("anchor")), "w")

    def test_new_task_is_disclosed_while_an_earlier_one_is_unverified(self):
        """Sequencing: task 2 must not silently stack on task 1's unchecked files."""
        self.applied_draft()
        self.ui.task.delete("1.0", "end")
        self.ui.task.insert("1.0", "Second task")
        with patch("ai_code_engineer.gui.make_provider", return_value=FakeModel()), \
                patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True) as confirm:
            self.ui.start_plan()
            self.wait_for_job()
        confirm.assert_not_called()  # an applied-but-unrun task is disclosed, not blocked
        notes = [text for author, text in self.ui.messages if author == "Tool"]
        self.assertTrue([note for note in notes
                         if "without a passing command run" in note])

    def test_a_second_task_after_an_interrupted_apply_needs_an_explicit_yes(self):
        self.applied_draft()
        stuck = load_session(self.ui.session_path)
        stuck["state"] = "PARTIAL_APPLY"
        atomic_json(self.ui.session_path, stuck)
        self.ui.task.delete("1.0", "end")
        self.ui.task.insert("1.0", "Second task")
        before = self.ui.session["id"]
        with patch("ai_code_engineer.gui.make_provider", return_value=FakeModel()), \
                patch("ai_code_engineer.gui.messagebox.askyesno", return_value=False):
            self.ui.start_plan()
        self.assertEqual(self.ui.session["id"], before)  # refused: nothing new was planned
        with patch("ai_code_engineer.gui.make_provider", return_value=FakeModel()), \
                patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.start_plan()
            self.wait_for_job()
        # Approved: the new task went to the model on top of the interrupted state.
        self.assertIn("Continuing on this state by your choice",
                      "\n".join(text for _, text in self.ui.messages))

    LEDGER_PLAN = """# Delivery plan

## Phase 1: scaffold
Create the manifest.

## Phase 2: login
Add the login route.

## Phase 3: filter
Validate the token.
"""

    def plan_draft(self, chained=True, note="keep the endpoint path unchanged"):
        """Attach a plan and set up a step-by-step run without the file dialog."""
        (self.repo / "plan.md").write_text(self.LEDGER_PLAN, encoding="utf-8")
        self.ui.repo.set(str(self.repo))
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.ui.model.set("test-local")
        self.ui.chained.set(chained)
        self.ui.plan_file.set(str(self.repo / "plan.md"))
        if note:
            self.ui.task.insert("1.0", note)

    def ledger(self):
        files = sorted((self.app_dir / ".agent-plans").glob("*.json"))
        self.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text(encoding="utf-8"))

    def scaffold(self):
        return {"action": "propose", "summary": "Scaffold the service", "checks": ["Run unit tests"],
                "changes": [{"path": "service.py", "content": "VALUE = 1\n"}]}

    def test_step_mode_sends_the_ledger_step_and_records_the_session(self):
        self.plan_draft()
        model = ScriptedModel([self.scaffold()])
        with patch("ai_code_engineer.gui.make_provider", return_value=model):
            self.ui.start_plan()
            self.wait_for_job()
        sent = model.calls[-1][1]["content"]
        self.assertIn("Implement step 1 of 3 from the attached plan: scaffold", sent)
        self.assertIn("keep the endpoint path unchanged", sent)
        self.assertIn("Do not run builds or tests", sent)
        self.assertEqual(self.ui.session["plan_step"], 1)
        self.assertEqual(self.ui.session["plan_reference"]["path"], "plan.md")
        row = self.ledger()["steps"][0]
        self.assertEqual(row["status"], "in_progress")
        self.assertEqual(row["session_id"], self.ui.session["id"])
        self.assertIn("Plan step 1/3", self.ui.plan_status.get())
        self.assertIn("plan.md — step 1/3", self.ui.source_name.get())

    def test_a_passing_run_with_report_proof_opens_the_next_step(self):
        self.plan_draft()
        model = ScriptedModel([self.scaffold(),
                               {"action": "propose", "summary": "Login route", "checks": ["Run unit tests"],
                                "changes": [{"path": "login.py", "content": "def login():\n    return True\n"}]}])
        passing = self.run_result("passed", failures=[], output="OK", tail="OK",
                                  proof={"tests": 8, "failures": 0, "errors": 0, "skipped": 0,
                                         "source": "JUnit XML"})
        with patch("ai_code_engineer.gui.make_provider", return_value=model), \
                patch("ai_code_engineer.gui.runner.run", return_value=passing), \
                patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.start_plan()
            self.wait_for_job()
            self.ui.apply()
            self.wait_for_job()
            self.make_runnable()
            self.ui.run_tests(False)
            self.wait_for_job()
            self.wait_for_job()
        steps = self.ledger()["steps"]
        self.assertEqual([row["status"] for row in steps[:2]], ["verified", "in_progress"])
        self.assertEqual(steps[1]["session_id"], self.ui.session["id"])
        self.assertEqual(self.ui.session["plan_step"], 2)
        self.assertIn("Plan step 2/3", self.ui.plan_status.get())
        self.assertIn("plan.md — step 2/3", self.ui.source_name.get())
        self.assertIn("Implement step 2 of 3", model.calls[-1][1]["content"])
        self.assertIn("do not redo", model.calls[-1][1]["content"])

    def test_a_green_run_without_a_test_report_keeps_the_step_open(self):
        self.plan_draft()
        model = ScriptedModel([self.scaffold()])
        green = self.run_result("passed", failures=[], output="OK", tail="OK", tests_observed=False)
        with patch("ai_code_engineer.gui.make_provider", return_value=model), \
                patch("ai_code_engineer.gui.runner.run", return_value=green), \
                patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.start_plan()
            self.wait_for_job()
            self.ui.apply()
            self.wait_for_job()
            self.make_runnable()
            self.ui.run_tests(False)
            self.wait_for_job()
        self.assertEqual(self.ledger()["steps"][0]["status"], "in_progress")
        self.assertEqual(len(model.calls), 1)
        self.assertIn("no test was observed running",
                      "\n".join(text for _, text in self.ui.messages))

    def test_rolling_back_reopens_the_step_its_run_had_verified(self):
        self.plan_draft()
        model = ScriptedModel([self.scaffold()])
        passing = self.run_result("passed", failures=[], output="OK", tail="OK",
                                  proof={"tests": 3, "failures": 0, "errors": 0, "skipped": 0,
                                         "source": "JUnit XML"})
        with patch("ai_code_engineer.gui.make_provider", return_value=model), \
                patch("ai_code_engineer.gui.runner.run", return_value=passing), \
                patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.start_plan()
            self.wait_for_job()
            self.ui.apply()
            self.wait_for_job()
            self.make_runnable()
            # Stepping is switched off for the advance so the next run is not started here.
            self.ui.chained.set(False)
            self.ui.run_tests(False)
            self.wait_for_job()
        self.assertEqual(self.ledger()["steps"][0]["status"], "verified")
        self.assertIn("Plan step 2/3", self.ui.plan_status.get())
        self.assertIn("(1 verified)", self.ui.plan_status.get())
        self.assertTrue(self.ui.task.get("1.0", "end").startswith("Implement step 2"))
        with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
            self.ui.undo()
            self.wait_for_job()
        self.assertEqual(self.ledger()["steps"][0]["status"], "in_progress")
        self.assertIn("Plan step 1/3", self.ui.plan_status.get())
        self.assertIn("plan.md — step 1/3", self.ui.source_name.get())
        self.assertIn("Reopened plan step 1",
                      "\n".join(text for _, text in self.ui.messages))

    def test_project_notes_are_stored_outside_the_repo_and_sent_with_every_task(self):
        self.ui.repo.set(str(self.repo))
        self.ui.memory_box.insert("1.0", "Java 17 only. Never rename the artifact id.")
        self.ui.save_memory()
        notes = memory.path_for(self.ui.memory_dir, self.repo)
        self.assertTrue(notes.is_file())
        self.assertFalse((self.repo / ".agent-memory").exists())
        self.assertIn("characters saved", self.ui.memory_info.get())
        model = RecordingModel()
        self.ui.task.insert("1.0", "Fix calculator.py")
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.ui.model.set("test-local")
        with patch("ai_code_engineer.gui.make_provider", return_value=model):
            self.ui.start_plan()
            self.wait_for_job()
        prompt = json.dumps(model.prompts[0])
        self.assertIn("Never rename the artifact id", prompt)
        self.assertIn("Project notes the user wrote", prompt)
        self.assertEqual(self.ui.session["memory"], "Java 17 only. Never rename the artifact id.")

    def test_switching_projects_loads_that_project_notes(self):
        other = self.app_dir / "other"
        other.mkdir()
        self.ui.repo.set(str(self.repo))
        self.ui.memory_box.insert("1.0", "One note per folder.")
        self.ui.save_memory()
        self.ui.repo.set(str(other))
        self.assertEqual(self.ui.memory_box.get("1.0", "end").strip(), "")
        self.assertIn("no notes yet", self.ui.memory_info.get())
        self.ui.memory_box.insert("1.0", "Second folder decides differently.")
        self.ui.save_memory()
        self.ui.repo.set(str(self.repo))
        self.assertEqual(self.ui.memory_box.get("1.0", "end").strip(), "One note per folder.")
        self.assertEqual(self.ui.project_notes(), "One note per folder.")

    def test_unsaved_note_edits_are_marked_before_saving(self):
        self.ui.repo.set(str(self.repo))
        self.assertIn("no notes yet", self.ui.memory_info.get())
        # Typing refreshes the label through a <KeyRelease> binding a programmatic
        # insert cannot fire, so the test calls the same handler.
        self.ui.memory_box.insert("1.0", "Typed but not saved")
        self.ui.refresh_memory_info()
        self.assertIn("unsaved", self.ui.memory_info.get())
        self.ui.save_memory()
        self.assertNotIn("unsaved", self.ui.memory_info.get())
        self.ui.memory_box.delete("1.0", "end")
        self.ui.refresh_memory_info()
        self.assertIn("unsaved", self.ui.memory_info.get())
        self.ui.save_memory()
        self.assertEqual(self.ui.project_notes(), "")
        self.assertEqual(list(self.ui.memory_dir.glob("*.md")), [])

    def test_a_streamed_build_line_is_scrubbed_before_it_reaches_the_window(self):
        """The web path redacts at capture; the Tk path shares the rule at its one drain point."""
        line = "Caused by: java.sql.SQLException: Access denied for 'app' password=hunter2secretvalue"
        self.ui.events.put(("progress", line))
        self.ui.poll()
        shown = self.ui.log.get("1.0", "end")
        self.assertNotIn("hunter2secretvalue", shown)
        self.assertIn("[redacted]", shown)
        self.assertNotIn("hunter2secretvalue", self.ui.status.get())
        self.assertLessEqual(len(shown.strip()), 501)

    def test_run_records_command_result_into_the_review(self):
        self.applied_draft()
        self.make_runnable()
        with patch("ai_code_engineer.gui.runner.run", return_value=self.run_result()) as command:
            self.ui.run_tests(False)
            self.wait_for_job()
        self.assertEqual(command.call_args.kwargs["timeout"], 600)
        self.assertEqual(self.ui.session["state"], "VERIFICATION_FAILED")
        self.assertIn("FAILED", self.ui.run_info.get())
        self.assertIn("FAIL: test_add", self.ui.code_views["checks"].get("1.0", "end"))
        self.assertFalse(self.ui._auto_fix)

    def test_run_is_locked_until_the_proposal_is_applied(self):
        self.draft()
        self.make_runnable()
        self.assertEqual(str(self.ui.run_button["state"]), "disabled")
        with patch("ai_code_engineer.gui.runner.run") as command:
            self.ui.run_tests(False)
            command.assert_not_called()
        self.assertIn("Apply a reviewed proposal", self.ui.status.get())

    def test_run_and_fix_feeds_the_failure_back_to_the_model(self):
        self.applied_draft()
        self.make_runnable()
        model = ScriptedModel([
            {"action": "read_file", "path": "calculator.py"},
            {"action": "propose", "summary": "Guard the sum", "checks": ["Run the tests"],
             "changes": [{"path": "calculator.py", "content": "def add(a, b):\n    return a + b or 0\n"}]},
        ])
        with patch("ai_code_engineer.gui.runner.run", return_value=self.run_result()), \
             patch("ai_code_engineer.gui.make_provider", return_value=model):
            self.ui.run_tests(True)
            self.wait_for_job()
            self.assertTrue(self.ui._auto_fix)
            self.wait_for_job()
        self.assertEqual(self.ui._fix_round, 1)
        self.assertIn("Runtime observation (untrusted data)", model.calls[-1][1]["content"])
        self.assertEqual(self.ui.session["state"], "WAITING_APPROVAL")
        self.assertTrue(self.ui.session["task"].startswith("The Python unittest command failed"))
        self.assertIn("FAILED (failures=1)", self.ui.session["evidence"])

    def test_applying_a_fix_reruns_the_command_automatically(self):
        self.applied_draft()
        self.make_runnable()
        model = ScriptedModel([
            {"action": "read_file", "path": "calculator.py"},
            {"action": "propose", "summary": "Guard the sum", "checks": ["Run the tests"],
             "changes": [{"path": "calculator.py", "content": "def add(a, b):\n    return a + b or 0\n"}]},
        ])
        passing = self.run_result("passed")
        with patch("ai_code_engineer.gui.runner.run", side_effect=[self.run_result(), passing]) as command, \
             patch("ai_code_engineer.gui.make_provider", return_value=model):
            self.ui.run_tests(True)
            self.wait_for_job()
            self.wait_for_job()
            with patch("ai_code_engineer.gui.messagebox.askyesno", return_value=True):
                self.ui.apply()
                self.wait_for_job()
        self.assertEqual(command.call_count, 2)
        self.assertFalse(self.ui._auto_fix)
        self.assertIn("passed", self.ui.status.get())
        self.assertEqual(self.ui.session["state"], "CHECKS_PASSED")

    def test_fix_loop_stops_at_its_round_budget(self):
        self.applied_draft()
        self.make_runnable()
        self.ui._auto_fix = True
        self.ui._fix_round = repair.MAX_FIX_ROUNDS
        self.ui.report_run(self.run_result())
        self.assertFalse(self.ui._auto_fix)
        self.assertIn("Stopped after 3 fix rounds", self.ui.status.get())

    def test_new_project_folder_is_granted_and_gates_writes(self):
        target = self.app_dir / "brand-new" / "spring-auth"
        with patch("ai_code_engineer.gui.filedialog.askdirectory", return_value=str(target)):
            self.ui.new_project()
        self.assertTrue(target.is_dir())
        self.assertEqual(self.ui.repo.get(), str(target.resolve()))
        self.assertIn("granted", self.ui.status.get())
        self.assertFalse(self.ui.recipes)
        self.assertEqual(str(self.ui.run_button["state"]), "disabled")

    def test_new_project_refuses_a_folder_that_already_has_files(self):
        with patch("ai_code_engineer.gui.filedialog.askdirectory", return_value=str(self.repo)):
            self.ui.new_project()
        self.assertEqual(self.ui.repo.get(), "")
        self.assertIn("already contains files", self.ui.status.get())

    def test_chat_without_project_answers_without_a_proposal(self):
        model, factory = self.ask()
        self.assertFalse(factory.call_args.kwargs["allow_cloud"])
        self.assertEqual(factory.call_args.kwargs["data_class"], "restricted")
        messages, json_mode = model.calls[0]
        self.assertFalse(json_mode)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("NO access", messages[0]["content"])
        self.assertIsNone(self.ui.session)
        self.assertIsNone(self.ui.session_path)
        self.assertEqual(list((self.app_dir / ".agent-runs").glob("*/session.json")), [])
        self.assertEqual(str(self.ui.apply_button["state"]), "disabled")
        stored = json.loads(next((self.app_dir / ".agent-chats").glob("*/chat.json")).read_text())
        self.assertEqual([turn["role"] for turn in stored["turns"]], ["user", "assistant"])
        self.assertEqual(self.ui.messages[-1], ("AI Code Engineer", "A monotonic clock never moves backwards."))

    def test_chat_keeps_history_across_turns(self):
        model, _ = self.ask("First question")
        self.ui.task.insert("1.0", "Second question")
        with patch("ai_code_engineer.gui.make_provider", return_value=model):
            self.ui.start_plan()
            self.wait_for_job()
        messages, _ = model.calls[-1]
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(len(list((self.app_dir / ".agent-chats").glob("*/chat.json"))), 1)

    def test_chat_failure_stores_nothing(self):
        self.ui.mode.set("Ollama")
        self.ui.mode_changed()
        self.ui.model.set("test-local")
        self.ui.task.insert("1.0", "Explain idempotency")
        with patch("ai_code_engineer.gui.make_provider", side_effect=OSError("Provider connection failed or timed out.")):
            self.ui.start_plan()
            self.wait_for_job()
        self.assertEqual(list((self.app_dir / ".agent-chats").glob("*/chat.json")), [])

    def test_chat_requires_cloud_approval_without_a_project(self):
        self.ui.catalogs["OpenRouter · Free"] = [FREE_ENTRY]
        self.ui.mode.set("OpenRouter · Free")
        self.ui.mode_changed()
        self.ui.model.set("x:free")
        self.ui.task.insert("1.0", "Explain idempotency")
        with patch("ai_code_engineer.gui.make_provider") as factory:
            self.ui.start_plan()
            factory.assert_not_called()
        self.assertFalse(self.ui.busy)
        self.assertEqual(list((self.app_dir / ".agent-chats").glob("*/chat.json")), [])

    def test_chat_reopens_from_the_chats_group_and_detaches_the_project(self):
        self.ask()
        chat_path = next((self.app_dir / ".agent-chats").glob("*/chat.json"))
        self.assertIn(("chat", chat_path), self.ui.chat_nodes.values())
        self.draft()
        node = next(key for key, value in self.ui.chat_nodes.items() if value[0] == "chat")
        self.ui.recent.selection_set(node)
        self.ui.open_recent()
        self.assertEqual(self.ui.repo.get(), "")
        self.assertIsNone(self.ui.session)
        self.assertEqual(sum(1 for author, _ in self.ui.messages if author == "You"), 1)

    def test_search_filters_the_sidebar(self):
        self.ask("Explain database indexes")
        self.draft()
        self.assertEqual(len(self.ui.chat_nodes), 2)
        self.ui.search.set("database")
        values = list(self.ui.chat_nodes.values())
        self.assertEqual([value for value in values if value[0] == "session"], [])
        self.assertEqual(len(values), 1)
        self.ui.search.set("")
        self.assertEqual(len(self.ui.chat_nodes), 2)

    def test_search_placeholder_does_not_hide_anything(self):
        self.ask("Explain database indexes")
        self.draft()
        self.assertEqual(self.ui.search.get(), SEARCH_PLACEHOLDER)
        self.assertEqual(len(self.ui.chat_nodes), 2)

    def test_chat_continues_after_restart(self):
        self.ask("First question")
        chat_id = self.ui.chat_id
        self.ui.cancel_timers()
        self.root.destroy()
        root = tk.Tk()
        root.withdraw()
        reopened = None
        try:
            reopened = AgentWindow(root, self.app_dir)
            root.update_idletasks()
            self.assertEqual(reopened.chat_id, chat_id)
            self.assertEqual(sum(1 for author, _ in reopened.messages if author == "You"), 1)
        finally:
            if reopened is not None:
                reopened.cancel_timers()
            root.destroy()


if __name__ == "__main__":
    unittest.main()
