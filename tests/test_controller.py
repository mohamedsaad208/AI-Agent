"""The web controller's orchestration, tested with no Tk, no browser and no network.

These cover the guarantees the desktop window is judged on — review before any write,
a run that proves itself, a plan step that only closes on that proof — through the same
entry points the UI server calls.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import git_integration, memory, repair
from ai_code_engineer.engine import atomic_json, load_session, project_key
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.webapp.controller import MAX_LOG_ENTRIES, AgentController

OLLAMA_ENTRY = {"id": "test-local", "name": "Test Local", "cloud": False,
                "description": "Synthetic local model for tests"}
FREE_ENTRY = {"id": "x:free", "name": "X Free", "free": True, "cloud": True,
              "description": "Synthetic free cloud model"}
CALCULATOR_BAD = "def add(a, b):\n    return a - b\n"
CALCULATOR_GOOD = "def add(a, b):\n    return a + b\n"
PLAN = "# Build it\n\n## 1. Foundation\nCreate the package.\n\n## 2. Register\nReject duplicate emails.\n"


class ProposalModel:
    model = "test-local"

    def __init__(self, content=CALCULATOR_GOOD):
        self.content = content
        self.prompts = []

    def generate(self, messages, json_mode=True):
        self.prompts.append(messages)
        return json.dumps({"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
                           "changes": [{"path": "calculator.py", "content": self.content}]})


class ChatModel:
    model = "test-local"

    def __init__(self):
        self.calls = []

    def generate(self, messages, json_mode=True):
        self.calls.append((messages, json_mode))
        return "A monotonic clock never moves backwards."


def run_result(status="passed", proof=None, observed=True, failures=None):
    return {"recipe": "python-unittest", "label": "Python unittest", "command": "python -m unittest",
            "status": status, "exit_code": 0 if status == "passed" else 1, "seconds": 1.4,
            "tests_observed": observed, "proof": proof, "truncated": False, "timed_out": False,
            "output": "Ran 4 tests\nOK\n", "tail": "Ran 4 tests\nOK\n",
            "failures": failures or []}


PROOF = {"tests": 4, "failures": 0, "errors": 0, "skipped": 0, "source": "JUnit XML"}


class Scripted(AgentController):
    """The two places the window opened a native dialog now answer from the test."""

    def __init__(self, app_dir, answers=None):
        self.answers = {"confirm": True, **(answers or {})}
        self.asked = []
        super().__init__(app_dir)

    def confirm_choice(self, title, message, warning="", ok_label="Continue", alt_label=""):
        """The one dialog seam. `confirm` is a thin reader of this, so overriding the wrapper left
        every test that reaches the fix offer waiting on a real `_ask` for half an hour."""
        self.asked.append({"title": title, "message": message, "warning": warning, "ok": ok_label,
                           "alt": alt_label})
        answer = self.answers["confirm"]
        return answer if isinstance(answer, dict) else {"ok": bool(answer)}

    def ask_directory(self, title, hint="", mustexist=True):
        self.asked.append({"title": title, "directory": True})
        chosen = self.answers.get("directory")
        return Path(chosen) if chosen else None

    def ask_plan_file(self):
        self.asked.append({"title": "plan", "plan": True})
        chosen = self.answers.get("plan")
        return Path(chosen) if chosen else None


class FriendlyErrorTests(unittest.TestCase):
    """A stalled model and a failed connection need different advice."""

    def test_repeating_the_same_action_says_what_it_usually_means(self):
        from ai_code_engineer.labels import friendly_error
        from ai_code_engineer.engine import AgentError
        text = friendly_error(AgentError("Model repeated the same action without progress; "
                                         "try another model or a narrower task."))
        self.assertIn("already in the files", text)
        self.assertNotIn("another model", text)

    def test_a_generic_budget_failure_keeps_the_old_advice(self):
        from ai_code_engineer.labels import friendly_error
        from ai_code_engineer.errors import AgentError
        self.assertIn("smaller task", friendly_error(AgentError("Model exceeded the invalid-action budget.")))

    def test_a_message_with_no_edit_in_it_points_at_the_mode_badge(self):
        from ai_code_engineer.labels import friendly_error
        from ai_code_engineer.errors import PolicyError
        text = friendly_error(PolicyError("Proposal contains an unchanged file: calculator.py"))
        self.assertIn("Chat mode", text)
        self.assertNotIn("calculator.py", text, "the internal refusal is not advice to the user")

    def test_an_answer_cut_off_by_the_output_limit_names_both_fixes(self):
        from ai_code_engineer.labels import friendly_error
        from ai_code_engineer.providers import ProviderError
        text = friendly_error(ProviderError("Model output truncated; reduce the change size."))
        self.assertIn("output limit", text)
        self.assertIn("Settings", text)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._made = []
        self.addCleanup(self._drain)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD)
        started = (patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),
                   patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[FREE_ENTRY]))
        for patcher in started:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = ProposalModel()
        self.provider = patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model)
        self.provider.start()
        self.addCleanup(self.provider.stop)
        self.controller = self.build()

    def build(self, answers=None):
        controller = Scripted(self.app_dir, answers)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        controller.set_repo(str(self.repo))
        self._made.append(controller)
        return controller

    def _drain(self):
        """An auto-advanced plan step starts its own job; let it land before the temp dir goes."""
        for controller in self._made:
            controller.cancel_event.set()
            controller.join(timeout=10)
            threading.Event().wait(0.05)

    def plan_a_fix(self, text="Fix add in calculator.py"):
        self.controller.start_plan(text)
        self.controller.join()
        return self.controller.snapshot()

    # ---------------------------- the happy path ----------------------------
    def test_plan_then_apply_then_a_passing_run_with_proof(self):
        state = self.plan_a_fix()
        self.assertEqual(state["review"]["state"], "Changes ready for review")
        self.assertTrue(state["review"]["canApply"])
        self.assertEqual(state["review"]["files"][0]["kind"], "M")
        self.assertEqual(state["review"]["files"][0]["path"], "calculator.py")
        self.assertTrue(state["review"]["view"]["diff"])
        self.assertEqual(state["runInfo"], "No command has run yet.")
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD,
                         "planning alone must never touch the project")

        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()
            state = self.controller.snapshot()
        self.assertEqual(state["review"]["state"], "Applied — project tests have not run")
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_GOOD)
        self.assertFalse(state["review"]["canApply"])
        self.assertTrue(state["canRun"])

        with patch("ai_code_engineer.runner.run", return_value=run_result(proof=PROOF)):
            self.controller.run_tests(False)
            self.controller.join()
            state = self.controller.snapshot()
        self.assertEqual(state["review"]["state"], "Selected checks passed")
        self.assertIn("4 tests", state["runInfo"])

    def test_build_output_streams_as_redacted_log_chunks(self):
        """A running command talks while it runs, and a printed secret does not travel."""
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix()
            self.controller.apply()
            self.controller.join()

        events = []
        self.controller._emit = events.append

        def fake_run(repo, recipe, timeout=60, progress=lambda _line: None):
            progress("compiling AuthService.java")
            progress('password = "hunter2-secret-value"')
            progress("   ")
            return run_result(proof=PROOF)

        with patch("ai_code_engineer.runner.run", side_effect=fake_run):
            self.controller.run_tests(False)
            self.controller.join()

        chunks = [event["text"] for event in events if event["kind"] == "log_chunk"]
        self.assertEqual(chunks, ["compiling AuthService.java", 'password = "[redacted]"'])
        self.assertNotIn("hunter2", str(events))
        # Streamed lines stay off the stored log: a 5 000-line build must not ride along in
        # every later snapshot. The job's own notes still do.
        stored = self.controller.snapshot()["log"]
        self.assertTrue(stored)
        self.assertNotIn("compiling", str(stored))

    def test_one_tool_action_leaves_one_log_row(self):
        """`plan()` announces an action on `progress` and on `step` with the same line.

        The strip and the stored history belong to the announcement; the step belongs to the
        conversation. Two log rows for one action doubled the history of every tool call.
        """
        first, second = "Reading file: calculator.py", "Searching code: add"
        for line in (first, first, second):
            self.controller._progress(line)
            self.controller._step(line)

        logged = [entry["text"] for entry in self.controller.log if entry["text"] in (first, second)]
        self.assertEqual(logged, [first, first, second])
        announced = [message["text"] for message in self.controller.messages
                     if message["text"] in (first, second)]
        self.assertEqual(announced, [first, second])
        self.assertEqual(self.controller.status, second)

    # ------------------------- UI 3.0 §2: Auto-Apply -------------------------
    def test_auto_apply_is_off_until_it_is_asked_for(self):
        self.assertFalse(self.controller.auto_apply)
        self.assertFalse(self.controller.snapshot()["settings"]["auto_apply"])
        self.controller.set_auto_apply(True)
        self.assertTrue(self.controller.auto_apply)
        self.assertTrue(self.controller.snapshot()["settings"]["auto_apply"])
        self.assertIn("Auto-Apply is ON", self.controller.status)

    def test_a_folder_with_no_project_cannot_be_set_to_write_itself(self):
        bare = self.build()
        bare.set_repo("")
        bare.set_auto_apply(True)
        self.assertFalse(bare.auto_apply)
        self.assertIn("Choose a project", bare.status)

    # ------------------- UI 3.1 §1: the notice arrives in the language asked for -------------------
    def test_a_task_written_in_arabic_gets_its_notices_in_arabic(self):
        from ai_code_engineer import labels
        self.controller.set_auto_apply(True)
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix("عايز اصلح دالة الجمع في calculator.py")
            self.controller.join()
        state = self.controller.snapshot()
        self.assertTrue(self.controller.arabic)
        texts = [message["text"] for message in state["messages"] if message["role"] == "tool"]
        self.assertTrue(any(labels.is_arabic(text) for text in texts),
                        "the thread said nothing in the language it was asked in: " + repr(texts))
        notice = next(text for text in texts if "\u26a1" in text)
        self.assertIn("ملخص التعديل", notice,
                      "the model's own summary rides under an Arabic label, not an English one")
        self.assertIn("Fix addition", notice,
                      "and what the model wrote stays exactly as the model wrote it")
        self.assertTrue(labels.is_arabic(state["artifact"]["state"]))
        self.assertTrue(labels.is_arabic(state["artifact"]["title"]))
        self.assertTrue(labels.is_arabic(state["banner"]["text"]))
        self.assertEqual(state["review"]["files"][0]["path"], "calculator.py",
                         "a path is a path in either language, and it is not translated")

    def test_an_english_task_keeps_english_notices(self):
        from ai_code_engineer import labels
        self.controller.set_auto_apply(True)
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix("Fix add in calculator.py")
            self.controller.join()
        state = self.controller.snapshot()
        self.assertFalse(self.controller.arabic)
        for text in [message["text"] for message in state["messages"] if message["role"] == "tool"]:
            self.assertFalse(labels.is_arabic(text), text)
        self.assertFalse(labels.is_arabic(state["artifact"]["state"]))
        self.assertIn("saved to disk", state["banner"]["text"].lower())

    def test_the_language_follows_the_task_and_not_the_window(self):
        """One window, two afternoons: the second task is a different language."""
        from ai_code_engineer import labels
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix("Fix add in calculator.py")
            self.assertFalse(self.controller.arabic)
            self.controller.new_task()
            self.plan_a_fix("عدّل دالة الجمع")
            self.assertTrue(self.controller.arabic,
                            "the notice language is the task's, so a stale session cannot pin it")

    def test_the_write_notice_is_plain_text_because_the_thread_escapes_it(self):
        """Tool rows go through esc() with no markdown pass, so `**bold**` would be literal stars."""
        self.controller.set_auto_apply(True)
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix("Fix add in calculator.py")
            self.controller.join()
        for message in self.controller.snapshot()["messages"]:
            if message["role"] == "tool":
                self.assertNotIn("**", message["text"], message["text"])

    def test_an_auto_apply_still_runs_when_the_selection_was_cleared(self):
        """The post-apply run keyed off the branch's selection alone, so clearing it — a batch
        workaround I actually used — left every later card reading "Applied — project tests have not
        run". A write nobody clicked for is the one that most needs the check, and the folder's own
        detected command is the fallback: nothing is invented, runner.detect() listed it."""
        self.controller.set_auto_apply(True)
        self.controller.set_recipe("")
        self.assertIsNone(self.controller.selected_recipe())
        with patch("ai_code_engineer.runner.run", return_value=run_result()) as run:
            self.plan_a_fix("Fix add in calculator.py")
            self.controller.join()
        self.assertTrue(run.called, "an automatic write finished with no project command run")
        self.assertEqual(self.controller.snapshot()["recipe"], "Python unittest",
                         "the detected command takes over, so the card does not stay unverified")

    def test_a_folder_with_no_command_says_so_instead_of_leaving_it_implied(self):
        """The other half: with nothing detected there is no run to make, and the status has to
        carry that instead of the generic "tests have not run"."""
        self.controller.set_auto_apply(True)
        self.controller.recipes = []                       # what runner.detect() answers for a bare folder
        with patch("ai_code_engineer.runner.run", return_value=run_result()) as run:
            self.plan_a_fix("Fix add in calculator.py")
            self.controller.join()
        self.assertFalse(run.called, "there is no command to run, so nothing may claim one ran")
        self.assertIn("no runnable command", self.controller.snapshot()["status"],
                      "the line must name the reason, not just the state")

    def test_a_question_with_no_answer_yet_is_in_the_snapshot(self):
        """D33: the id of a live question existed only inside the SSE event that carried it, so a
        restart — or a page that missed the one emit — left a worker holding a question nothing on
        earth could answer. Measured as a 30-minute stall at busy=False."""
        outcome: dict = {}

        def ask():
            outcome["answer"] = self.controller._ask(
                "confirm", {"title": "Keep going?", "message": "Fix round 1 of 3"})

        thread = threading.Thread(target=ask)
        thread.start()
        self.addCleanup(thread.join, 10)
        asks = []
        for _ in range(100):
            asks = self.controller.snapshot()["asks"]
            if asks:
                break
            threading.Event().wait(0.02)
        self.assertEqual([row["title"] for row in asks], ["Keep going?"])
        self.assertTrue(asks[0].get("id"), "the snapshot has to carry the id the answer names")
        self.controller.set_reply(asks[0]["id"], {"ok": True})
        thread.join(5)
        self.assertEqual(outcome.get("answer"), {"ok": True}, "a page that connects late can answer it")
        self.assertEqual(self.controller.snapshot()["asks"], [], "and answering retires the row")

    def test_two_live_questions_both_survive(self):
        """One slot would drop the first waiter forever — which is two of these today."""
        threads = []
        for number in (1, 2):
            thread = threading.Thread(target=lambda n=number: self.controller._ask(
                "confirm", {"title": f"Question {n}", "message": "m"}))
            thread.start()
            threads.append(thread)
        self.addCleanup(lambda: [t.join(10) for t in threads])
        titles = []
        for _ in range(150):
            titles = sorted(row["title"] for row in self.controller.snapshot()["asks"])
            if len(titles) == 2:
                break
            threading.Event().wait(0.02)
        self.assertEqual(titles, ["Question 1", "Question 2"])
        for row in self.controller.snapshot()["asks"]:
            self.controller.set_reply(row["id"], {"ok": False})

    def test_an_expired_question_leaves_the_snapshot_and_withdraws_its_sheet(self):
        events = []
        self.controller._emit = events.append
        with patch("ai_code_engineer.webapp.controller.ASK_TIMEOUT", 0.2):
            self.assertEqual(self.controller._ask("confirm", {"title": "Keep going?"}), {})
        self.assertEqual(self.controller.snapshot()["asks"], [])
        self.assertIn("retract", [event.get("kind") for event in events],
                      "a withdrawn question has to leave the screen, not sit on it")

    def test_a_blocked_task_still_leaves_the_folder_buildable(self):
        """D28: Run keyed off the session's state, so the build was unreachable exactly when the last
        task went wrong — and `mvn test` on files that are on disk is the thing an operator needs then.
        The documented lock (no commands before a proposal is applied) is WAITING_APPROVAL, which is
        the only other state in the gate."""
        self.plan_a_fix()
        path = self.controller.session_path
        session = load_session(path)
        session["state"] = "BLOCKED"
        atomic_json(path, session)
        self.controller.display_session(path)
        self.assertTrue(self.controller.snapshot()["canRun"],
                        "a blocked task still has files on disk to check")
        with patch("ai_code_engineer.runner.run", return_value=run_result()) as run:
            self.controller.run_tests(False)
            self.controller.join()
        self.assertTrue(run.called)
        self.assertEqual(load_session(path)["state"], "BLOCKED",
                         "a run must not rewrite a blocked task's verdict")
        rows = [message["text"] for message in self.controller.snapshot()["messages"]
                if message["role"] == "tool"]
        self.assertTrue(any("no applied task is open to hold the result" in text for text in rows),
                        "an unrecorded run says so: " + repr(rows[-3:]))

    def test_a_folder_with_no_task_open_can_still_be_checked(self):
        self.controller.new_task()
        self.controller.session = self.controller.session_path = None
        self.assertTrue(self.controller.snapshot()["canRun"])
        with patch("ai_code_engineer.runner.run", return_value=run_result()) as run:
            self.controller.run_tests(False)
            self.controller.join()
        self.assertTrue(run.called, "the command belongs to the folder, not to a task")

    def test_a_removal_is_drawn_as_a_removal(self):
        """D31's surface half. `before is None` already means "created", so the review row cannot
        infer a delete from the missing side — and a chip reading "~ Modified" over a file that is
        gone is the card pointing at a diff of something the folder no longer has."""
        self.controller.session = {"state": "APPLIED_UNVERIFIED", "root": str(self.repo),
                                   "task": "Retire the old config",
                                   "changes": [{"path": "old.py", "before": "x = 1\n",
                                                "after": None, "delete": True}]}
        review = self.controller.snapshot()["review"]
        self.assertEqual(review["files"][0]["kind"], "D")
        self.assertEqual(review["files"][0]["del"], 1)
        self.assertEqual(review["view"]["after"], [], "the after side is empty because the file is")
        self.assertTrue(review["view"]["diff"], "and the removal still reads as lines leaving")

    def test_the_switch_belongs_to_the_folder_and_not_to_the_window(self):
        second = self.app_dir / "other"
        (second / "tests").mkdir(parents=True)
        (second / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        self.controller.set_auto_apply(True)
        self.controller.set_repo(str(second))
        self.assertFalse(self.controller.auto_apply, "a new folder starts off")
        self.controller.set_repo(str(self.repo))
        self.assertTrue(self.controller.auto_apply, "and the old one still remembers")

    def test_it_survives_a_restart_because_the_registry_keeps_it(self):
        self.controller.set_auto_apply(True)
        again = self.build()
        again.set_repo(str(self.repo))
        self.assertTrue(again.auto_apply)

    def test_with_the_switch_on_a_ready_proposal_writes_without_a_confirmation(self):
        self.controller.set_auto_apply(True)
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix()               # the proposal, the write and the check all happen here
            self.controller.join()
        self.assertEqual([row for row in self.controller.asked if row["title"] == "Apply changes"], [],
                         "the whole point of the switch is that this dialog does not open")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)
        self.assertEqual(self.controller.snapshot()["banner"]["count"], 1,
                         "the banner has to survive the run that follows the write")
        self.assertIn("saved to disk", self.controller.snapshot()["banner"]["text"].lower(),
                      "the count alone is not the notice: the sentence says where the files are")
        self.assertIn("Auto-Apply saved 1 file(s) directly to the project",
                      str(self.controller.snapshot()["messages"]))
        self.assertIn("Task summary", str(self.controller.snapshot()["messages"]),
                      "an unattended write has to say what the model decided to do")

    def test_a_proposal_that_empties_a_file_stops_for_an_answer_even_with_the_switch_on(self):
        big = "\n".join("value_%d = %d" % (i, i) for i in range(30)) + "\n"
        (self.repo / "calculator.py").write_text(big, encoding="utf-8", newline="\n")
        self.model.content = "value_0 = 0\n"
        self.controller.answers["confirm"] = False        # the answer this task will get
        self.controller.set_auto_apply(True)
        self.controller.start_plan("Trim calculator.py")
        self.controller.join()
        asked = [row for row in self.controller.asked if row["title"] == "Apply changes"]
        self.assertEqual(len(asked), 1,
                         "emptying a file the developer wrote is the one thing still worth asking")
        self.assertIn("Removes most of an existing file", asked[0]["message"])
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), big,
                         "and a declined answer writes nothing")
        self.assertEqual(self.controller.snapshot()["banner"]["count"], 0,
                         "a write that stopped for an answer was not an automatic one")
        self.assertEqual(self.controller.snapshot()["banner"]["text"], "",
                         "and with no automatic write there is no notice to show")

    def test_with_the_switch_on_the_proposal_writes_itself_the_moment_it_lands(self):
        self.controller.set_auto_apply(True)
        calls = []

        def fake_run(repo, recipe, timeout=60, progress=lambda _line: None):
            calls.append(recipe)
            return run_result(proof=PROOF)

        with patch("ai_code_engineer.runner.run", side_effect=fake_run):
            self.controller.start_plan("Fix add in calculator.py")
            self.controller.join()
        self.assertEqual([row for row in self.controller.asked if row["title"] == "Apply changes"],
                         [], "no click, no dialog")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)
        self.assertEqual(self.controller.snapshot()["banner"]["count"], 1)
        self.assertEqual(calls, ["python-unittest"], "and the project checked what was written")

    def test_a_block_the_user_clicked_still_waits_for_Apply_with_the_switch_on(self):
        self.controller.set_auto_apply(True)
        self.controller.action("apply_block", {"path": "calculator.py", "content": CALCULATOR_GOOD},
                               lambda _event: None)
        self.controller.join()
        self.assertEqual(self.controller.session["state"], "WAITING_APPROVAL",
                         "the click on the block is the request; Auto-Apply removes the click after it")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)

    def test_with_the_switch_off_the_confirmation_is_exactly_where_it_was(self):
        self.plan_a_fix()
        self.controller.asked.clear()
        self.controller.set_auto_apply(False)
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()
        self.assertEqual([row["title"] for row in self.controller.asked], ["Apply changes"])
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)
        self.assertNotIn("automatically", self.controller.status.lower())

    # ------------------------- §5: continuing is an answer -------------------------
    def applied_controller(self):
        """A project with an applied change, ready to run its own command."""
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix()
            self.controller.apply()
            self.controller.join()
        self.controller.asked.clear()
        return self.controller

    FAILING = run_result(status="failed", failures=["AssertionError: 3 != 4"])

    def test_a_failed_run_asks_before_spending_a_fix_round(self):
        controller = self.applied_controller()
        with patch("ai_code_engineer.runner.run", return_value=self.FAILING):
            controller.run_tests(False)
            controller.join()
        asked = [row for row in controller.asked if row["title"] == "Keep going?"]
        self.assertEqual(len(asked), 1, "one question, not a status line to interpret")
        self.assertIn("Fix round 1 of 3", asked[0]["message"])
        self.assertIn("test-local", asked[0]["message"], "it names who is being asked")
        self.assertIn("nothing is written until you approve", asked[0]["message"].lower())
        self.assertEqual(asked[0]["ok"], "Run the fix round")
        self.assertEqual(controller._fix_round, 1, "yes spends the round")

    def test_declining_the_fix_round_starts_no_request_and_writes_nothing(self):
        controller = self.applied_controller()
        controller.answers["confirm"] = False
        with patch("ai_code_engineer.webapp.controller.plan") as planner, \
             patch("ai_code_engineer.runner.run", return_value=self.FAILING):
            controller.run_tests(False)
            controller.join()
        self.assertIn("Keep going?", [row["title"] for row in controller.asked])
        planner.assert_not_called()
        self.assertEqual(controller._fix_round, 0)
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_GOOD,
                         "the applied change is still the only thing on disk")
        self.assertTrue(controller.status.startswith("Python unittest: FAILED"),
                        "declining leaves the run's own summary as the last word")

    def test_the_offer_carries_the_batch_answer_as_a_third_button(self):
        controller = self.applied_controller()
        with patch("ai_code_engineer.runner.run", return_value=self.FAILING):
            controller.run_tests(False)
            controller.join()
        asked = [row for row in controller.asked if row["title"] == "Keep going?"]
        self.assertEqual(asked[0]["alt"], repair.FIX_OFFER_ALT,
                         "the sentence lives in repair so both windows read one copy")

    def test_the_batch_answer_runs_no_round_and_silences_the_rest_of_the_batch(self):
        """D30: an unanswered offer blocks the worker that asked it, so every red build in a queue
        cost the whole ask timeout. The operator can now say "not again this batch" once."""
        controller = self.applied_controller()
        controller.answers["confirm"] = {"ok": False, "alt": True}
        with patch("ai_code_engineer.webapp.controller.plan") as planner, \
             patch("ai_code_engineer.runner.run", return_value=self.FAILING):
            controller.run_tests(False)
            controller.join()
            self.assertIn("Keep going?", [row["title"] for row in controller.asked])
            controller.asked.clear()
            controller.run_tests(False)
            controller.join()
        planner.assert_not_called()
        self.assertEqual(controller._fix_round, 0)
        self.assertNotIn("Keep going?", [row["title"] for row in controller.asked],
                         "the second failure in the same batch is reported, not asked about")
        rows = [message["text"] for message in controller.snapshot()["messages"]
                if message["role"] == "tool"]
        self.assertTrue(any("Fix offers are off" in text for text in rows),
                        "suppression has to say so, or it reads as the tool stopping caring")

    def test_a_task_started_by_hand_asks_again(self):
        controller = self.applied_controller()
        controller.answers["confirm"] = {"ok": False, "alt": True}
        with patch("ai_code_engineer.runner.run", return_value=self.FAILING):
            controller.run_tests(False)
            controller.join()
        self.assertTrue(controller._batch_fixes_off)
        controller.answers["confirm"] = True
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix("Fix add in calculator.py")
        self.assertFalse(controller._batch_fixes_off, "a Send by hand is a new batch")

    def test_the_last_round_is_not_offered_a_fourth_time(self):
        controller = self.applied_controller()
        controller._fix_round = 3
        with patch("ai_code_engineer.webapp.controller.plan") as planner, \
             patch("ai_code_engineer.runner.run", return_value=self.FAILING):
            controller.run_tests(False)
            controller.join()
        self.assertNotIn("Keep going?", [row["title"] for row in controller.asked])
        planner.assert_not_called()

    def test_a_passing_run_is_offered_nothing(self):
        controller = self.applied_controller()
        with patch("ai_code_engineer.runner.run", return_value=run_result(proof=PROOF)):
            controller.run_tests(False)
            controller.join()
        self.assertEqual(controller.asked, [])

    def test_the_next_plan_step_starts_on_an_answer_not_a_guess(self):
        book = {"steps": [{"id": 1, "title": "Add the model", "status": "verified"},
                          {"id": 2, "title": "Wire the filter chain", "status": "open"}],
                "plan_path": "plan.md"}
        asked, started = [], []
        self.controller.confirm = lambda *a, **k: (asked.append((a, k)), True)[1]
        self.controller.start_plan = started.append
        self.assertTrue(self.controller._offer_next_step(book, book["steps"][1], 1))
        self.assertEqual(len(started), 1, "yes starts step 2")
        question = asked[0][0][1]
        self.assertIn("step 2 of 2", question)
        self.assertIn("Wire the filter chain", question)
        self.assertEqual(asked[0][1]["ok_label"], "Start step 2")

    def test_declining_the_next_plan_step_leaves_it_in_the_message_box(self):
        book = {"steps": [{"id": 1, "title": "Add the model", "status": "verified"},
                          {"id": 2, "title": "Wire the filter chain", "status": "open"}],
                "plan_path": "plan.md"}
        started = []
        self.controller.confirm = lambda *a, **k: False
        self.controller.start_plan = started.append
        self.assertFalse(self.controller._offer_next_step(book, book["steps"][1], 1))
        self.assertEqual(started, [])

    def test_declining_the_apply_confirmation_writes_nothing(self):
        self.controller = self.build({"confirm": False})
        self.plan_a_fix()
        self.controller.apply()
        self.controller.join()
        self.assertEqual(self.controller.snapshot()["review"]["state"], "Changes ready for review")
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)
        self.assertEqual(self.controller.asked[-1]["ok"], "Apply changes")

    def test_rollback_restores_the_previous_contents(self):
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix()
            self.controller.apply()
            self.controller.join()
            self.controller.undo()
            self.controller.join()
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)
        self.assertEqual(self.controller.snapshot()["review"]["state"], "Changes rolled back")

    # ------------------------------- gating --------------------------------
    def test_a_cloud_model_needs_an_explicit_approval(self):
        self.controller.mode = self.controller.active_mode = "OpenRouter · Free"
        self.controller.catalogs["OpenRouter · Free"] = [FREE_ENTRY]
        self.controller.model = "x:free"
        self.controller.start_plan("Fix add")
        self.controller.join()
        self.assertIn("Approve sending", self.controller.status)
        self.assertIsNone(self.controller.session)
        self.assertFalse(self.controller.busy)

    def test_send_without_a_model_does_not_start_a_job(self):
        self.controller.model = ""
        self.controller.start_plan("Fix add")
        self.assertIn("select a model", self.controller.status)
        self.assertFalse(self.controller.busy)

    def test_a_missing_project_folder_is_refused(self):
        self.controller.repo = str(self.app_dir / "gone")
        self.controller.current_project = "different"
        self.controller.start_plan("Fix add")
        self.assertIn("existing project folder", self.controller.status)
        self.assertFalse(self.controller.busy)

    def test_commands_are_locked_until_a_proposal_is_applied(self):
        self.plan_a_fix()
        with patch("ai_code_engineer.runner.run") as executed:
            self.controller.run_tests(False)
        executed.assert_not_called()
        self.assertIn("Apply a reviewed proposal", self.controller.status)

    def test_an_unverified_earlier_task_is_disclosed_before_a_new_one(self):
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.plan_a_fix()
            self.controller.apply()
            self.controller.join()
        self.controller.messages = []
        self.plan_a_fix("Now extract a helper")
        self.controller.join()
        notes = [message["text"] for message in self.controller.messages if message["role"] == "tool"]
        self.assertTrue(any("without a passing command run" in note for note in notes), notes)

    def test_an_interrupted_apply_needs_a_deliberate_yes(self):
        self.plan_a_fix()
        chat_id = self.controller.chat_id
        session = load_session(self.controller.session_path)
        session["state"] = "PARTIAL_APPLY"
        from ai_code_engineer.engine import atomic_json, proposal_hash
        session["proposal_hash"] = proposal_hash(session)
        atomic_json(self.controller.session_path, session)
        self.controller = self.build({"confirm": False})
        self.controller.chat_id = chat_id          # the warning is scoped to one conversation
        self.controller.start_plan("Try again")
        self.controller.join()
        self.assertIsNone(self.controller.session)
        self.assertIn("Unfinished task", [ask["title"] for ask in self.controller.asked])

    # ------------------------------- memory --------------------------------
    def test_project_notes_live_outside_the_repo_and_reach_the_model(self):
        self.controller.save_memory("Never add a dependency.")
        notes_path = memory.path_for(self.controller.memory_dir, str(self.repo))
        self.assertTrue(notes_path.is_file())
        self.assertEqual(list(self.repo.glob("*.md")), [], "notes must not land in the project")
        self.plan_a_fix()
        joined = json.dumps(self.model.prompts)
        self.assertIn("Never add a dependency.", joined)
        self.assertIn("chars", self.controller.snapshot()["memory"]["info"])

    def test_switching_projects_warns_before_losing_an_unsent_draft(self):
        other = self.app_dir / "other"
        (other / "tests").mkdir(parents=True)
        self.controller = self.build({"confirm": False})
        self.controller._draft = "half written"
        self.controller.project_changed(str(other))
        self.assertEqual(self.controller.repo, str(self.repo), "a declined switch must revert")
        self.assertIn("Switch project", [ask["title"] for ask in self.controller.asked])

        self.controller.asked.clear()
        self.controller._draft = ""
        self.controller.project_changed(str(other))
        self.assertEqual(self.controller.repo, str(other), "nothing to lose, so nothing to ask")
        self.assertEqual(self.controller.asked, [])

    # ------------------------------ plain chat ------------------------------
    def test_a_chat_without_a_project_never_produces_a_proposal(self):
        chat = ChatModel()
        with patch("ai_code_engineer.webapp.controller.make_provider", return_value=chat):
            self.controller.set_repo("")
            self.controller.start_plan("What is a monotonic clock?")
            self.controller.join()
        self.assertIsNone(self.controller.session)
        self.assertEqual(json.loads((self.app_dir / ".agent-chats" / self.controller.chat_id /
                                     "chat.json").read_text())["turns"][0]["role"], "user")
        self.assertFalse(chat.calls[0][1], "plain chat must not ask the model for a JSON action")
        state = self.controller.snapshot()
        self.assertEqual(state["artifact"]["title"], "No proposal yet")

    # ------------------------------ plan ledger ------------------------------
    def test_a_step_closes_only_on_a_run_that_proved_tests_ran(self):
        (self.repo / "plan.md").write_text(PLAN)
        self.controller.chained = True
        self.controller.plan_file = str(self.repo / "plan.md")
        self.controller.refresh_plan_status()
        with patch("ai_code_engineer.runner.run", side_effect=[
                run_result(proof=None, observed=False), run_result(proof=PROOF)]) as executed:
            self.plan_a_fix("")
            state = self.controller.snapshot()
            self.assertEqual(state["plan"]["step"], 1)
            self.assertEqual(state["plan"]["verified"], 0)
            self.assertEqual(executed.call_count, 0)
            self.assertIn("step 1", load_session(self.controller.session_path)["task"])
            self.controller.apply()
            self.controller.join()
            # Green, but nothing proved a test executed: the step stays open.
            self.controller.run_tests(False)
            self.controller.join()
            self.assertEqual(self.controller.snapshot()["plan"]["verified"], 0)
            self.assertIn("still open", self.controller.status)
            self.controller.run_tests(False)
            self.controller.join()
        state = self.controller.snapshot()
        self.assertEqual(state["plan"]["verified"], 1)
        self.assertEqual(state["plan"]["step"], 2)

    # ----------------------------- persistence -----------------------------
    def test_preferences_survive_a_restart(self):
        self.controller.set_timeout(420)
        self.controller.set_chained(True)
        again = self.build()
        self.assertEqual(again.request_timeout, 420)
        self.assertTrue(again.chained)
        self.assertEqual(again.repo, str(self.repo))
        self.assertEqual(again.model, "test-local")

    def test_the_api_key_is_never_written_to_disk(self):
        self.controller.set_key("sk-or-secret")
        self.controller.set_timeout(500)
        registry = (self.app_dir / ".agent-projects.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-or-secret", registry)
        self.assertNotIn("sk-or-secret", json.dumps(self.controller.snapshot()))

    # ------------------------------- snapshot -------------------------------
    def test_the_snapshot_is_serialisable_json_for_a_fresh_client(self):
        state = self.controller.snapshot()
        round_tripped = json.loads(json.dumps(state))
        for key in ("projects", "chats", "review", "artifact", "plan", "settings", "messages", "log"):
            self.assertIn(key, round_tripped)
        self.assertEqual(round_tripped["header"]["title"], "New chat")

    def test_an_unknown_action_is_rejected_not_ignored(self):
        with self.assertRaises(PolicyError):
            self.controller.action("rm -rf", {}, lambda _event: None)

    def test_stop_cancels_a_cancellable_job_and_reports_it(self):
        class SlowModel(ProposalModel):
            def generate(self, messages, json_mode=True):
                self.controller.cancel_event.set()
                return super().generate(messages)

        slow = SlowModel()
        slow.controller = self.controller
        with patch("ai_code_engineer.webapp.controller.make_provider", return_value=slow):
            self.controller.start_plan("Fix add")
            self.controller.join()
        self.assertIn("Task cancelled", self.controller.status)
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)

    def test_the_folder_picker_grants_access_to_an_empty_folder(self):
        fresh = self.app_dir / "brand-new"
        self.controller = self.build({"directory": str(fresh)})
        self.controller.new_project()
        self.assertTrue(fresh.is_dir())
        self.assertEqual(self.controller.repo, str(fresh))

    def test_a_new_project_refuses_a_folder_that_already_holds_files(self):
        self.controller = self.build({"directory": str(self.repo)})
        self.controller.new_project()
        self.assertEqual(self.controller.repo, str(self.repo), "a refused grant must not detach the project")
        self.assertIn("already", self.controller.status.lower())


class DualModel:
    """Answers in prose when asked for prose, and proposes only when handed the action envelope."""

    model = "test-local"

    def __init__(self, content=CALCULATOR_GOOD):
        self.content = content
        self.calls = []

    def generate(self, messages, json_mode=True):
        self.calls.append((json_mode, messages))
        if not json_mode:
            return "A monotonic clock never moves backwards."
        return json.dumps({"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
                           "changes": [{"path": "calculator.py", "content": self.content}]})

    @property
    def prose(self):
        return [call for call in self.calls if not call[0]]

    @property
    def actions(self):
        return [call for call in self.calls if call[0]]


class BranchTests(unittest.TestCase):
    """UI 2.8: the branch selected in the sidebar owns the folder, the mode and the context."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD)
        started = (patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),
                   patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[FREE_ENTRY]))
        for patcher in started:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = DualModel()
        provider = patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model)
        provider.start()
        self.addCleanup(provider.stop)

    def fresh(self, answers=None):
        """A controller built the way a launch builds one: no folder until one is chosen."""
        controller = Scripted(self.app_dir, answers)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"

        def drain(c=controller):        # a stray background job must not outlive the temp folder
            c.cancel_event.set()
            c.join(10)
        self.addCleanup(drain)
        return controller

    def ask(self, controller, text="HI"):
        controller.start_plan(text)
        controller.join()
        return controller.snapshot()

    def open_folder(self, folder=None):
        """The sidebar gesture: grant a folder. Granting one is a decision to build in it."""
        controller = self.fresh({"directory": str(folder or self.repo)})
        controller.browse()
        controller.join()
        return controller

    def chat_in_folder(self):
        """A project branch switched to prose, which is what a bound chat answers with."""
        controller = self.open_folder()
        controller.set_composer("chat")
        return controller

    def chats(self):
        return sorted((self.app_dir / ".agent-chats").glob("*/chat.json"))

    # ------------------------------ starting apart ------------------------------
    def test_a_new_chat_carries_no_folder_and_answers_a_greeting(self):
        controller = self.open_folder()
        controller.new_chat()
        self.assertEqual(controller.repo, "")
        self.assertEqual(controller.composer, "chat")
        self.ask(controller)
        self.assertTrue(self.model.prose)
        self.assertEqual(self.model.actions, [])
        self.assertIsNone(controller.session)

    def test_a_new_chat_leaves_the_project_in_the_sidebar(self):
        controller = self.open_folder()
        group = controller.snapshot()["projects"][0]["key"]
        controller.new_chat()
        state = controller.snapshot()
        self.assertEqual([g["key"] for g in state["projects"]], [group])

    def test_granting_a_folder_starts_in_change_mode_and_says_so(self):
        controller = self.open_folder()
        self.assertEqual(controller.composer, "change")
        self.assertIn("Working in repo", controller.messages[-1]["text"])
        self.assertIn("until you click Apply", controller.messages[-1]["text"])
        self.assertIn("badge", controller.messages[-1]["text"])

    def test_a_restored_project_keeps_the_mode_it_was_left_in(self):
        first = self.open_folder()
        first.set_composer("chat")
        self.ask(first, "What does add do?")
        reopened = self.fresh()
        self.assertEqual(reopened.repo, str(self.repo))     # the branch comes back
        self.assertEqual(reopened.composer, "chat")         # with the mode the user chose for it
        self.ask(reopened)
        self.assertEqual(self.model.actions, [])

    def test_launch_after_a_standalone_chat_opens_on_nothing(self):
        first = self.open_folder()
        first.new_chat()
        self.assertEqual(self.fresh().repo, "")

    # ------------------------------ a chat in a project -----------------------------
    def test_a_chat_started_inside_a_project_records_that_project(self):
        controller = self.chat_in_folder()
        self.ask(controller, "What does add do?")
        stored = json.loads(self.chats()[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["project"]["path"], str(self.repo))
        leaves = controller.snapshot()["projects"][0]["chats"]
        self.assertEqual([leaf["kind"] for leaf in leaves], ["chat"])
        self.assertEqual(controller.snapshot()["chats"], [])

    def test_a_bound_chat_reads_the_repository_map_and_still_cannot_propose(self):
        controller = self.chat_in_folder()
        self.ask(controller, "What does add do?")
        system = self.model.prose[-1][1][0]["content"]
        self.assertIn("Repository context", system)
        self.assertIn("calculator.py", system)
        self.assertIn("Never emit JSON tool actions", system)
        self.assertIsNone(controller.session)

    def test_a_standalone_chat_sees_no_folder_at_all(self):
        controller = self.fresh()
        self.ask(controller, "What does add do?")
        system = self.model.prose[-1][1][0]["content"]
        self.assertNotIn("Repository context", system)
        self.assertNotIn("calculator.py", system)

    def test_two_chats_in_one_folder_do_not_share_their_history(self):
        controller = self.chat_in_folder()
        self.ask(controller, "Why does subtract show up here?")
        first = controller.chat_id
        controller.new_task()
        self.assertNotEqual(controller.chat_id, first)
        self.ask(controller, "And now?")
        system_and_turns = json.dumps(self.model.prose[-1][1])
        self.assertNotIn("Why does subtract show up here?", system_and_turns)

    # --------------------------------- dropping onto a node ---------------------------------
    def test_dropping_a_chat_on_a_project_binds_it_by_key(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        chat_id = controller.chat_id
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        controller.bind_chat(chat_id, key)
        controller.join()
        stored = json.loads((self.app_dir / ".agent-chats" / chat_id / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["project"]["key"], key)
        leaves = controller.snapshot()["projects"][0]["chats"]
        self.assertEqual([leaf["id"] for leaf in leaves], [chat_id])
        self.ask(controller, "So what is wrong with add?")
        self.assertEqual(self.model.actions, [], "a bound chat is still a chat")
        self.assertIsNone(controller.session)

    def test_dropping_onto_an_unknown_key_is_refused(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        for forged in ("not-a-key", str(self.repo), "..\\..\\Windows"):
            controller.bind_chat(controller.chat_id, forged)
            self.assertIn("granted", controller.status)
            stored = json.loads(self.chats()[0].read_text(encoding="utf-8"))
            self.assertIsNone(stored["project"])

    def test_a_forged_chat_id_cannot_walk_out_of_the_chat_store(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        controller.bind_chat("../../evil", project_key(str(self.repo)))
        self.assertIn("Only a chat", controller.status)
        self.assertFalse((self.app_dir.parent / "evil").exists())

    def test_detaching_a_chat_keeps_its_turns(self):
        controller = self.fresh()
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        self.ask(controller, "HI")
        controller.bind_chat(controller.chat_id, key)
        controller.join()
        before = json.loads(self.chats()[0].read_text(encoding="utf-8"))["turns"]
        controller.bind_chat(controller.chat_id, "")
        controller.join()
        after = json.loads(self.chats()[0].read_text(encoding="utf-8"))
        self.assertIsNone(after["project"])
        self.assertEqual([turn["content"] for turn in after["turns"]],
                         [turn["content"] for turn in before])
        self.assertIn(controller.chat_id, [chat["id"] for chat in controller.snapshot()["chats"]])

    def test_reopening_a_bound_chat_comes_back_in_chat_mode(self):
        controller = self.fresh()
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        self.ask(controller, "HI")
        chat_id = controller.chat_id
        controller.bind_chat(chat_id, key)
        controller.join()
        controller.set_composer("change")
        controller.open_chat(self.app_dir / ".agent-chats" / chat_id / "chat.json")
        self.assertEqual(controller.composer, "chat")
        self.assertEqual(controller.repo, str(self.repo))

    def armed_project_chat(self, controller):
        """Leave the project in Change with its switch on, then drop the open chat onto it."""
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        controller._select_branch("project", key, composer="change")
        controller.join()
        controller.set_composer("change")
        controller.set_auto_apply(True)
        controller.join()
        controller.new_chat()
        self.ask(controller, "HI")
        chat_id = controller.chat_id
        controller.bind_chat(chat_id, key)
        controller.join()
        return key, chat_id

    def test_a_chat_dropped_on_a_project_left_in_change_cannot_write(self):
        """The drop used to inherit the folder's mode and arm its Auto-Apply switch."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        self.assertTrue(controller.branch.get("bound"), "the branch is still a chat")
        self.assertEqual(controller.composer, "chat")
        self.assertFalse(controller.auto_apply)
        before = (self.repo / "calculator.py").read_text(encoding="utf-8")
        self.ask(controller, "add a logout button to the profile page")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), before,
                         "a bound chat that writes files breaks the one promise it makes")
        self.assertEqual([row for row in controller.asked if row.get("title") == "Apply changes"], [],
                         "and it must not have been offered a write to agree to")
        # Routing a build verb to a proposal is intended for a bound chat; what is not intended is
        # that proposal reaching the disk on its own, so the switch must still be off here.
        self.assertFalse(controller.auto_apply)

    def test_unbinding_and_rebinding_a_project_cannot_arm_the_chat_again(self):
        """The refusal is derived from the branch, not from a flag set once at bind time."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        controller.bind_chat(controller.chat_id, "")      # detach
        controller.join()
        controller.bind_chat(controller.chat_id, key)    # and drop it back on
        controller.join()
        self.assertEqual(controller.composer, "chat")
        self.assertFalse(controller.auto_apply)
        self.assertTrue(controller._auto_pref.get(key), "the project still keeps its own switch")
        controller.new_chat()
        controller.projects[key] = str(self.repo.resolve())
        self.ask(controller, "HI")
        controller.bind_chat(controller.chat_id, key)
        controller.join()
        self.assertEqual(controller.composer, "chat")
        self.assertFalse(controller.auto_apply)
        before = (self.repo / "calculator.py").read_text(encoding="utf-8")
        self.ask(controller, "add a logout button to the profile page")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), before)

    def test_a_bound_chat_leaves_the_projects_preferences_alone(self):
        """Switching mode or the switch here used to rewrite what the project branch remembers."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        controller.set_composer("change")
        self.assertEqual(controller.composer, "chat", "Change is refused while the chat is bound")
        self.assertIn("reads that folder only", controller.status)
        controller.set_auto_apply(False)
        self.assertFalse(controller.auto_apply)
        again = self.fresh()
        again.projects[key] = str(self.repo.resolve())
        again._select_branch("project", key)
        again.join()
        self.assertEqual(again.composer, "change", "the project still remembers Change")
        again.set_auto_apply(True)
        self.assertTrue(again.auto_apply, "and still remembers the switch being on")

    def test_a_bound_chat_never_writes_without_the_click(self):
        """The folder's switch stays off here, so nothing can write itself; the click still works."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        state = controller.action("apply_block", {"path": "calculator.py", "content": CALCULATOR_GOOD},
                                  lambda event: None)
        controller.join()
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD,
                         "a block click opens a proposal, as it always did")
        self.assertFalse(controller.auto_apply, "and this folder's switch does not reach this chat")
        controller.apply()
        controller.join()
        self.assertEqual([row["title"] for row in controller.asked], ["Apply changes"],
                         "the write is asked for, never assumed")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD,
                         "and one explicit yes is enough — a bound chat is not a read-only lock")

    # ---------------------------------- Change mode ----------------------------------
    def test_the_badge_switches_one_branch_to_the_reviewed_path(self):
        controller = self.fresh()
        controller.set_repo(str(self.repo))
        controller.set_composer("change")
        controller.start_plan("Fix add in calculator.py")
        controller.join()
        state = controller.snapshot()
        self.assertEqual(state["review"]["state"], "Changes ready for review")
        self.assertEqual(len(self.model.actions), 1)
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD,
                         "a proposal writes nothing until Apply")

    def test_the_chosen_mode_is_remembered_for_that_project_only(self):
        controller = self.fresh()
        controller.set_repo(str(self.repo))
        controller.set_composer("change")
        reopened = self.fresh()
        self.assertEqual(reopened.composer, "change")
        other = self.app_dir / "second"
        other.mkdir()
        reopened.set_repo(str(other))
        reopened.set_composer("chat")
        reopened.projects[project_key(str(self.repo))] = str(self.repo.resolve())
        reopened._select_branch("project", project_key(str(self.repo)))
        self.assertEqual(reopened.composer, "change")

    def test_change_mode_without_a_folder_is_refused(self):
        controller = self.fresh()
        controller.set_composer("change")
        self.assertIn("Choose a project", controller.status)
        self.assertEqual(controller.composer, "chat")

    def test_an_editless_message_in_change_mode_points_at_the_badge(self):
        controller = self.fresh()
        controller.set_repo(str(self.repo))
        controller.set_composer("change")
        blocked = patch("ai_code_engineer.webapp.controller.plan",
                        side_effect=PolicyError("Proposal contains an unchanged file: calculator.py"))
        with blocked:
            controller.start_plan("HI")
            controller.join()
        self.assertIn("Chat mode", controller.status)
        self.assertNotIn("unchanged file", controller.status)

    # --------------------------- reaching another disk ---------------------------
    def test_the_folder_picker_lists_the_mounted_roots_at_a_drive_root(self):
        listing = self.fresh().list_dir(str(Path(self.repo).drive) + "\\")
        self.assertIsNone(listing["parent"], "a drive root has no parent to walk up to")
        self.assertIn(Path(self.repo).drive + "\\", listing["roots"])

    def test_a_typed_path_can_name_a_project_that_does_not_exist_yet(self):
        target = self.app_dir / "spring-project"
        controller = self.fresh({"directory": str(target)})
        controller.new_project()
        self.assertTrue(target.is_dir())
        self.assertEqual(controller.repo, str(target))
        self.assertEqual(controller.composer, "change")

    def test_opening_a_folder_that_is_not_there_is_refused_rather_than_adopted(self):
        controller = self.fresh({"directory": str(self.app_dir / "missing")})
        controller.browse()
        self.assertEqual(controller.repo, "")
        self.assertIn("not available", controller.status)

    # --------------------------------- window prefs ---------------------------------
    def test_the_folded_sidebar_survives_a_restart(self):
        quiet = lambda _event: None
        self.fresh().action("set_collapsed", {"value": True}, quiet)
        self.assertTrue(self.fresh().snapshot()["prefs"]["collapsed"])
        self.fresh().action("set_collapsed", {"value": False}, quiet)
        self.assertFalse(self.fresh().snapshot()["prefs"]["collapsed"])

    # ---------------------------------- project marks ----------------------------------
    def test_a_project_mark_survives_a_restart_and_renders_in_the_tree(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        controller.set_icon(key, "\U0001F680")
        reopened = self.fresh()
        group = reopened.snapshot()["projects"][0]
        self.assertEqual(group["icon"], "\U0001F680")

    def test_the_default_mark_is_not_written_out_as_a_difference(self):
        controller = self.open_folder()
        controller.set_icon(controller.branch["key"], "\U0001F4C1")
        self.assertEqual(controller.icons[controller.branch["key"]], "")

    def test_a_mark_outside_the_palette_is_refused(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        for forged in ("<img src=x onerror=alert(1)>", "📁📁📁📁", ""):
            controller.set_icon(key, forged)
            self.assertIn("offered", controller.status)
            self.assertNotIn(key, controller.icons)
        controller.set_icon("unknown-key", "\U0001F680")
        self.assertIn("granted", controller.status)

    def test_a_registry_of_bare_paths_still_loads_after_the_format_change(self):
        (self.app_dir / ".agent-projects.json").write_text(
            json.dumps({"projects": [str(self.repo)], "ui": {}}), encoding="utf-8")
        reopened = self.fresh()
        self.assertEqual(list(reopened.projects.values()), [str(self.repo.resolve())])
        self.assertEqual(reopened.icons, {})

    def test_the_plus_on_a_project_row_starts_a_chat_bound_to_that_project(self):
        controller = self.open_folder()
        controller.set_composer("change")
        key = controller.branch["key"]
        self.ask(controller, "Fix add in calculator.py")          # a task, not a chat
        controller.new_chat_in(key)
        self.assertEqual(controller.composer, "chat")
        self.assertEqual(controller.branch["key"], key)
        self.assertIsNone(controller.session, "the earlier task is untouched, not replaced")
        self.ask(controller, "What does the filter chain do?")
        stored = json.loads(self.chats()[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["project"]["key"], key)
        kinds = [leaf.get("kind") for leaf in controller.snapshot()["projects"][0]["chats"]]
        self.assertIn("session", kinds, "the project still lists its tasks")
        self.assertIn("chat", kinds, "and now lists its chat")

    def test_a_chat_cannot_be_moved_into_a_project_that_was_never_granted(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        controller.new_chat_in("C:\\Windows")
        self.assertIn("granted", controller.status)

    # ---------------------------- §1 project drawer ----------------------------
    def test_the_drawer_reports_the_root_notes_budget_and_toolchain(self):
        controller = self.chat_in_folder()
        key = controller.branch["key"]
        controller.save_memory("Run the tests before proposing.")
        self.ask(controller, "What does calculator.py do?")
        info = controller.project_info(key)
        self.assertEqual(info["path"], str(self.repo.resolve()))
        self.assertTrue(info["exists"])
        self.assertEqual(info["notes"], "Run the tests before proposing.")
        self.assertTrue(info["notes_file"].endswith(".md"))
        # The notes live beside the app, never inside the folder a proposal can rewrite.
        self.assertNotIn(".agent-memory", str(self.repo))
        self.assertTrue(str(info["notes_dir"]).endswith(".agent-memory"))
        self.assertEqual(info["toolchain"]["detected"][0]["name"], "python-unittest")
        self.assertEqual(info["toolchain"]["timeout"], 600)
        self.assertIn("summary line", info["toolchain"]["proof"])
        budget = info["context"]
        self.assertGreater(budget["map"], 0, "the repository map is measured, not guessed")
        self.assertGreaterEqual(budget["files"], 1, "the index counted the folder")
        self.assertEqual(budget["used"], budget["system"] + budget["context"] + budget["turns"])
        self.assertEqual(budget["remaining"], budget["budget"] - budget["used"])
        self.assertEqual(budget["est_tokens"], budget["used"] // 4)
        self.assertTrue(budget["bound"], "a chat that reads this folder spends its budget")
        self.assertGreater(budget["turns"], 0)

    def test_the_drawer_payload_matches_the_one_the_preview_scripts(self):
        """--fake is the window the interface is reviewed in, so its drawer has to send the same
        fields the real controller does. A missing key reads as a blank there, never as an error."""
        from ai_code_engineer.webapp.fake import PROJECT_INFO
        controller = self.open_folder()
        info = controller.project_info(project_key(self.repo))
        scripted = PROJECT_INFO["demo2"]
        self.assertEqual(set(info), set(scripted), "the preview drawer and the real one differ")
        self.assertEqual(set(info["context"]), set(scripted["context"]))
        self.assertEqual(set(info["toolchain"]), set(scripted["toolchain"]))
        self.assertEqual({frozenset(entry) for entry in info["toolchain"]["detected"]},
                         {frozenset(entry) for entry in scripted["toolchain"]["detected"]})

    def test_the_budget_of_another_project_does_not_count_this_conversation(self):
        controller = self.open_folder()
        other = self.app_dir / "other"
        (other / "src").mkdir(parents=True)
        (other / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
        controller.projects[project_key(str(other))] = str(other.resolve())
        self.ask(controller, "What does calculator.py do?")
        key = project_key(str(other))
        self.assertFalse(controller.project_info(key)["context"]["bound"])
        self.assertEqual(controller.project_info(key)["context"]["turns"], 0)
        self.assertEqual(controller.branch["key"], project_key(str(self.repo)),
                         "opening a drawer must not repoint the window")

    def test_a_missing_folder_is_reported_rather_than_raised(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        (self.repo / "calculator.py").unlink()
        gone = self.app_dir / "gone"
        controller.projects[key] = str(gone)
        info = controller.project_info(key)
        self.assertFalse(info["exists"])
        self.assertEqual(info["context"]["map"], 0)
        self.assertEqual(info["toolchain"]["detected"], [])

    def test_an_unknown_project_key_is_refused(self):
        controller = self.fresh()
        with self.assertRaises(PolicyError):
            controller.project_info("c:\\windows")

    def test_reveal_opens_only_a_granted_folder_with_a_fixed_argv(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        seen = []
        with patch("ai_code_engineer.webapp.controller.subprocess.Popen",
                   side_effect=lambda command, **kwargs: seen.append((command, kwargs))):
            controller.reveal(key)
        self.assertEqual(len(seen), 1)
        command, kwargs = seen[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], "explorer.exe" if os.name == "nt" else command[0])
        self.assertEqual(command[-1], str(self.repo.resolve()))
        self.assertNotIn("shell", kwargs)

    def test_reveal_refuses_a_folder_the_browser_only_named(self):
        controller = self.open_folder()
        with patch("ai_code_engineer.webapp.controller.subprocess.Popen") as popen:
            controller.reveal(str(self.app_dir))          # a real path, never granted
            controller.reveal("../../Windows")
            controller.reveal("")
        popen.assert_not_called()

    def test_reveal_of_a_granted_folder_that_vanished_starts_nothing(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        controller.projects[key] = str(self.app_dir / "not-here")
        with patch("ai_code_engineer.webapp.controller.subprocess.Popen") as popen:
            controller.reveal(key)
        popen.assert_not_called()
        self.assertIn("not on this disk", controller.status)

    # ------------------- UI 3.0 §1: a change verb in a chat branch -------------------
    FIX_TEST_AR = "\u0635\u0644\u062d \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631 \u0627\u0644\u0641\u0627\u0634\u0644"
    HOW_TO_ADD_AR = "\u0643\u064a\u0641 \u0623\u0636\u064a\u0641 \u0632\u0631 \u062e\u0631\u0648\u062c\u061f"

    def test_a_change_verb_in_a_bound_chat_plans_a_proposal_instead_of_an_answer(self):
        controller = self.chat_in_folder()
        controller.start_plan("add a logout button to the profile page")
        controller.join()
        self.assertEqual(self.model.prose, [], "the prose path must not have run")
        self.assertTrue(self.model.actions, "the plan path should have asked for an action")
        self.assertEqual(controller.session["state"], "WAITING_APPROVAL")
        self.assertTrue(any("Switched to Change mode" in entry["text"] for entry in controller.log),
                        "the routing has to be visible somewhere that outlives the job")

    def test_routing_explains_itself_in_the_conversation(self):
        controller = self.chat_in_folder()
        controller.start_plan("fix the failing test")
        controller.join()
        notes = [m["text"] for m in controller.snapshot()["messages"] if m["role"] == "tool"]
        self.assertTrue(any("planned as a proposal instead" in line for line in notes),
                        "a mode that appears to change on its own has to say why")

    def test_the_route_applies_to_that_message_and_is_not_remembered(self):
        controller = self.chat_in_folder()
        controller.start_plan("add a logout button")
        controller.join()
        self.assertEqual(controller.composer, "chat", "the branch is still a chat")
        self.assertEqual(controller._composer_pref[controller.branch["key"]], "chat")
        planned = len(self.model.actions)
        controller.start_plan("thanks")
        controller.join()
        self.assertEqual(len(self.model.actions), planned, "a thank-you must not plan a change")
        self.assertTrue(self.model.prose, "and it should have been answered in prose")
        self.assertIsNone(controller.session)

    def test_an_arabic_change_verb_routes_and_an_arabic_question_does_not(self):
        controller = self.chat_in_folder()
        controller.start_plan(self.FIX_TEST_AR)
        controller.join()
        self.assertTrue(self.model.actions, "slah al-ikhtibar is a request to change files")
        self.assertEqual(self.model.prose, [])
        other = self.chat_in_folder()
        planned = len(self.model.actions)
        other.start_plan(self.HOW_TO_ADD_AR)
        other.join()
        self.assertEqual(len(self.model.actions), planned, "a question about a change is a question")
        self.assertTrue(self.model.prose)

    def test_a_change_verb_in_a_chat_with_no_project_stays_prose(self):
        controller = self.open_folder()
        controller.new_chat()
        self.assertEqual(controller.repo, "")
        controller.start_plan("create a flask app")
        controller.join()
        self.assertTrue(self.model.prose)
        self.assertEqual(self.model.actions, [], "there is no folder to write into")

    def test_a_greeting_in_a_bound_chat_is_still_answered_as_a_greeting(self):
        controller = self.chat_in_folder()
        controller.start_plan("HI")
        controller.join()
        self.assertEqual(len(self.model.prose), 1)
        self.assertEqual(self.model.actions, [])



class IntentWordTests(unittest.TestCase):
    """The verb table is the whole heuristic, so it is tested as a table."""

    def setUp(self):
        from ai_code_engineer.webapp.controller import asks_for_a_change
        self.ask = asks_for_a_change

    def check(self, expected, *texts):
        for text in texts:
            self.assertEqual(self.ask(text), expected, text)

    def test_change_verbs_route(self):
        self.check(True, "add a test", "Fix the build", "Create a Flask app", "refactor: split it",
                   "build me a page", "make the sidebar wider", "delete the old handler",
                   "scaffold an api", "implement retry", "write a tokenizer")

    def test_a_verb_after_the_opening_word_still_routes(self):
        """The request rarely starts on its verb, so the opening five tokens are scanned."""
        self.check(True, "please add a test", "I want to create a flask app",
                   "I would like to generate a report", "setup a virtualenv for this",
                   "hey, can you make the sidebar wider")

    def test_questions_about_changes_do_not(self):
        self.check(False, "how do I add a logout button?", "what does build() do",
                   "explain this refactor", "is the test fixed?", "can you add a column?",
                   "why does add() fail", "An add-on?", "additionally, note that")

    def test_a_question_mark_is_a_question_whatever_it_opens_with(self):
        """The veto is about the shape of the sentence, not only its first word."""
        self.check(False, "can you fix this?", "please add a test?", "you want me to write it?")
        self.check(True, "can you fix this", "please add a test")

    def test_ordinary_chat_does_not(self):
        self.check(False, "HI", "thanks!", "", "   ", "good morning team")

    def test_arabic_change_verbs_route_in_every_spelling_a_person_types(self):
        self.check(True, "\u0623\u0646\u0634\u0626 \u0644\u064a \u0645\u0634\u0631\u0648\u0639",   # anshif ma with hamza
                   "\u0627\u0646\u0634\u0626 \u0644\u064a \u0645\u0634\u0631\u0648\u0639",   # same word, no hamza
                   "\u0637\u0648\u0651\u0631 \u0627\u0644\u0648\u0627\u062c\u0647\u0629",               # with shadda
                   "\u0639\u062f\u0644 \u0627\u0644\u062f\u0627\u0644\u0629", "\u0635\u0644\u062d \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631",
                   "\u0627\u0643\u062a\u0628 \u062f\u0627\u0644\u0629", "\u0636\u064a\u0641 \u0632\u0631", "\u0627\u0639\u0645\u0644 \u0627\u062e\u062a\u0628\u0627\u0631",
                   "\u0627\u062d\u0630\u0641 \u0627\u0644\u0645\u0644\u0641", "\u063a\u064a\u0631 \u0627\u0644\u0648\u0627\u0646\u0647",
                   "\u0633\u0648\u064a \u0644\u064a \u0645\u0644\u0641", "\u0631\u0643\u0628 \u0644\u064a \u0627\u0644\u062d\u0632\u0645")

    def test_arabic_requests_that_open_on_the_wanting_word(self):
        """"\u0639\u0627\u064a\u0632 \u0627\u0639\u0645\u0644 \u0645\u0634\u0631\u0648\u0639" opens on neither a verb nor a question word."""
        self.check(True, "\u0639\u0627\u064a\u0632 \u0627\u0639\u0645\u0644 \u0645\u0634\u0631\u0648\u0639",
                   "\u0645\u062d\u062a\u0627\u062c \u0627\u0639\u062f\u0644 \u0627\u0644\u062f\u0627\u0644\u0629",
                   "\u064a\u0627\u0631\u064a\u062a \u0627\u0644\u0648\u0627\u062c\u0647\u0629 \u062a\u0628\u0642\u0649 \u0623\u0633\u0647\u0644",
                   "\u0627\u0631\u064a\u062f \u0627\u0628\u0646\u064a \u0635\u0641\u062d\u0629",
                   "\u0628\u062f\u064a \u0632\u0631 \u062d\u0641\u0638",
                   "\u062d\u0627\u0628\u0628 \u0627\u0636\u064a\u0641 \u0627\u062e\u062a\u0628\u0627\u0631",
                   "\u0644\u0648 \u0633\u0645\u062d\u062a \u0636\u064a\u0641 \u0632\u0631")

    def test_the_wanting_word_need_not_be_first(self):
        """A polite address or an adverb can come before it: "ya sahbi ayez taamal login"."""
        self.check(True, "\u064a\u0627 \u0635\u0627\u062d\u0628\u064a \u0639\u0627\u064a\u0632 \u062a\u0639\u0645\u0644 login",
                   "\u0628\u0631\u0636\u0648 \u0639\u0627\u064a\u0632 \u0627\u0636\u064a\u0641 \u0632\u0631",
                   "\u0648\u0643\u0645\u0627\u0646 \u0645\u062d\u062a\u0627\u062c \u0627\u0639\u0645\u0644 \u0627\u062e\u062a\u0628\u0627\u0631")
        # The question veto still runs first, so an ask-after-a-question-word stays a question.
        self.check(False, "\u0647\u0644 \u0639\u0627\u064a\u0632 \u062a\u0639\u0645\u0644 login\u061f")

    def test_arabic_questions_do_not(self):
        self.check(False, "\u0643\u064a\u0641 \u0623\u0636\u064a\u0641 \u0632\u0631\u061f", "\u0645\u0627 \u0648\u0638\u064a\u0641\u0629 build\u061f",
                   "\u0627\u0634\u0631\u062d \u0627\u0644\u0643\u0648\u062f", "\u0647\u0644 \u064a\u0645\u0643\u0646 \u0625\u0635\u0644\u0627\u062d\u0647",
                   "\u0644\u064a\u0647 \u0627\u0644\u0643\u0648\u062f \u0645\u0634 \u0634\u063a\u0627\u0644", "\u0634\u0643\u0631\u0627")
        # A request word that opens a question is still a question: the mark vetoes it.
        self.check(False, "\u0645\u0645\u0643\u0646 \u062a\u0635\u0644\u062d \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631\u061f")

    def test_the_window_is_five_words_and_no_further(self):
        self.check(False, "I was wondering if you could fix this")     # "fix" is the sixth token
        self.check(True, "Fix this, please")


@unittest.skipUnless(git_integration.git_program(), "git is not installed on this machine")
class CheckpointTests(unittest.TestCase):
    """UI 3.0 §4: an apply inside a git folder leaves a commit the developer can find."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        (self.repo / "notes.md").write_text("work in progress\n", encoding="utf-8", newline="\n")
        self.git("init", "-q")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "add", "--",
                 "calculator.py")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "commit",
                 "-q", "--no-verify", "-m", "first")
        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),
                        patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[FREE_ENTRY]),
                        patch("ai_code_engineer.webapp.controller.make_provider",
                              return_value=ProposalModel())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)

    def git(self, *args):
        finished = subprocess.run([git_integration.git_program(), *args], cwd=str(self.repo),
                                  env=git_integration.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def apply_a_fix(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()

    def test_an_apply_in_a_repository_is_committed_under_its_own_subject(self):
        before = self.git("rev-parse", "HEAD")
        self.apply_a_fix()
        self.assertEqual(self.git("log", "-1", "--pretty=%s"),
                         "agent: Fix add in calculator.py [session-"
                         + self.controller.session["id"][:12] + "]")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn("Git checkpoint", str(self.controller.snapshot()["messages"]))

    def test_the_checkpoint_carrys_only_the_files_the_task_wrote(self):
        self.apply_a_fix()
        status = self.git("status", "--porcelain")
        self.assertIn("notes.md", status, "an unrelated file must stay uncommitted")
        self.assertNotIn("calculator.py", status)

    def test_a_plain_folder_gets_no_checkpoint_and_no_message_about_one(self):
        plain = self.app_dir / "plain"
        (plain / "tests").mkdir(parents=True)
        (plain / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        other = Scripted(self.app_dir)
        other.catalogs["Ollama"] = [OLLAMA_ENTRY]
        other.model = "test-local"
        other.set_repo(str(plain))
        self.addCleanup(other.close)
        other.start_plan("Fix add in calculator.py")
        other.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            other.apply()
            other.join()
        self.assertEqual((plain / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)
        self.assertNotIn("checkpoint", str(other.snapshot()["messages"]).lower())
        self.assertFalse((plain / ".git").exists())

    def test_a_checkpoint_that_cannot_be_made_does_not_undo_the_apply(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.webapp.controller.git_integration.checkpoint",
                   return_value={"ok": False, "hash": "", "before": "",
                                 "reason": "git has no user.name configured"}):
            self.controller.apply()
            self.controller.join()
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"),
                         CALCULATOR_GOOD, "the write happened, whatever git thinks about it")
        self.assertIn("No git checkpoint: git has no user.name configured",
                      str(self.controller.snapshot()["messages"]))


@unittest.skipUnless(git_integration.git_program(), "git is not installed on this machine")
class TaskBranchTests(unittest.TestCase):
    """The branch chip is the one control that moves HEAD, so it is tested against a real git."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        self.git("init", "-q")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "add", "--",
                 "calculator.py")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "commit",
                 "-q", "--no-verify", "-m", "first")
        self.base = self.head()
        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models",
                              return_value=[OLLAMA_ENTRY]),
                        patch("ai_code_engineer.webapp.controller.openrouter_models",
                              return_value=[FREE_ENTRY])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def controller(self, answers=None):
        made = Scripted(self.app_dir, answers)
        made.catalogs["Ollama"] = [OLLAMA_ENTRY]
        made.model = "test-local"
        made.set_repo(str(self.repo))
        self.addCleanup(made.close)
        return made

    def git(self, *args):
        finished = subprocess.run([git_integration.git_program(), *args], cwd=str(self.repo),
                                  env=git_integration.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def head(self):
        return self.git("symbolic-ref", "--short", "HEAD")

    def test_the_chip_starts_a_branch_named_for_the_task_and_records_the_way_back(self):
        controller = self.controller()
        controller._draft = "Add the login endpoint"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        moved = self.head()
        self.assertTrue(moved.startswith("agent/task-add-the-login-endpoint-"), moved)
        self.assertNotEqual(moved, self.base)
        shown = controller.snapshot()["git"]
        self.assertEqual(shown["branch"], moved)
        self.assertEqual(shown["base"], self.base, "the chip has to know what it can go back to")
        self.assertIn("Started git branch " + moved, str(controller.snapshot()["messages"]))

    def test_the_confirmation_names_the_branch_before_anything_moves(self):
        controller = self.controller({"confirm": False})
        controller._draft = "Add the login endpoint"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        self.assertEqual(self.head(), self.base, "a refusal must not move HEAD")
        asked = controller.asked[-1]
        self.assertIn("agent/task-add-the-login-endpoint", asked["title"] + asked["message"])
        self.assertIn(self.base, asked["message"], "it has to say which branch is being left")
        self.assertEqual(controller.snapshot()["git"].get("base", ""), "",
                         "nothing was left, so there is nothing to return to")

    def test_a_second_click_goes_back_to_the_branch_it_left(self):
        controller = self.controller()
        controller._draft = "add b"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        self.assertNotEqual(self.head(), self.base)
        controller.action("git_branch", {"type": "git_branch", "back": self.base},
                          lambda _event: None)
        controller.join()
        self.assertEqual(self.head(), self.base)
        shown = controller.snapshot()["git"]
        self.assertNotIn("base", shown, "back where we started, so the ↩ has nothing to offer")
        self.assertIn("Back on git branch " + self.base, str(controller.snapshot()["messages"]))

    def test_a_folder_that_is_not_a_repository_says_so_and_asks_for_no_confirmation(self):
        plain = self.app_dir / "plain"
        (plain / "tests").mkdir(parents=True)
        controller = self.controller()
        controller.set_repo(str(plain))
        controller._draft = "add b"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        self.assertEqual(controller.asked, [], "there is nothing to confirm about a plain folder")
        self.assertIn("not a git repository", controller.status)
        self.assertEqual(controller.snapshot()["git"]["branch"], "")

    def test_git_refusing_is_reported_in_gits_words_and_leaves_the_chip_alone(self):
        controller = self.controller()
        controller._draft = "add b"
        with patch("ai_code_engineer.webapp.controller.git_integration.start_task_branch",
                   return_value={"ok": False, "created": False, "branch": "", "from": "",
                                 "reason": "fatal: cannot lock ref 'HEAD'"}):
            controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
            controller.join()
        self.assertEqual(self.head(), self.base)
        self.assertIn("No branch change: fatal: cannot lock ref",
                      str(controller.snapshot()["messages"]))

    def test_an_arabic_task_gets_an_arabic_sentence_about_a_latin_branch_name(self):
        controller = self.controller()
        asked_in = "أضف نقطة تسجيل الدخول"
        # The language rule reads what the user asked in, so the sentence needs a real message from
        # them; the draft is what the branch name is built from when no session exists yet.
        controller._add("user", "You", asked_in)
        controller._draft = asked_in
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        moved = self.head()
        self.assertTrue(moved.startswith("agent/task-"), moved)
        self.assertNotIn("أ", moved, "a branch name stays Latin even in an Arabic sentence")
        row = str(controller.snapshot()["messages"])
        self.assertIn("تم إنشاء فرع git باسم " + moved, row)
        self.assertNotIn("Started git branch", row)


@unittest.skipUnless(git_integration.git_program(), "git is not installed on this machine")
class GitRestoreTests(unittest.TestCase):
    """The escalation path: a rollback the session cannot do, git can, for those files only.

    These are live-repository tests on purpose. The whole claim is about bytes on disk — which copy
    survives, and whose does not — and a scripted git would only re-state the code's own assumption.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        (self.repo / "notes.md").write_text("work in progress\n", encoding="utf-8", newline="\n")
        self.git("init", "-q")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "add", "--",
                 "calculator.py", "notes.md")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "commit",
                 "-q", "--no-verify", "-m", "first")
        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models",
                              return_value=[OLLAMA_ENTRY]),
                        patch("ai_code_engineer.webapp.controller.openrouter_models",
                              return_value=[FREE_ENTRY]),
                        patch("ai_code_engineer.webapp.controller.make_provider",
                              return_value=ProposalModel())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)

    def git(self, *args):
        finished = subprocess.run([git_integration.git_program(), *args], cwd=str(self.repo),
                                  env=git_integration.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def read(self, name="calculator.py"):
        return (self.repo / name).read_text(encoding="utf-8")

    def apply_a_fix(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()

    def edit_afterwards(self, body="def add(a, b):\n    return a * b\n"):
        """Someone — the user, another tool — touched the file the task wrote."""
        (self.repo / "calculator.py").write_text(body, encoding="utf-8", newline="\n")
        git_integration.forget()

    def test_a_rollback_blocked_by_a_later_edit_offers_the_git_copy(self):
        self.apply_a_fix()
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        offer = self.controller.snapshot()["git"].get("restore")
        self.assertTrue(offer, "the refusal used to be a dead end with no way out")
        self.assertEqual(offer["paths"], 1)
        self.assertRegex(offer["commit"], r"^[0-9a-f]{4,40}$")
        self.assertIn("Rollback refused", self.controller.status)
        rows = str(self.controller.snapshot()["messages"])
        self.assertIn("git can still put 1 file(s) back to commit " + offer["commit"], rows)

    def test_the_offer_names_the_commit_before_anything_is_replaced(self):
        self.apply_a_fix()
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        commit = self.controller.snapshot()["git"]["restore"]["commit"]
        self.controller.answers["confirm"] = False
        self.controller.git_restore()
        self.controller.join()
        asked = self.controller.asked[-1]
        self.assertIn(commit, asked["message"])
        self.assertIn("1 file(s)", asked["ok"])
        self.assertEqual(self.read(), "def add(a, b):\n    return a * b\n",
                         "a refusal must leave the later edit exactly where it is")
        self.assertIn("restore", self.controller.snapshot()["git"],
                      "the offer stands until it is taken or a new task starts")

    def test_accepting_restores_the_tasks_own_file_and_leaves_the_rest_alone(self):
        self.apply_a_fix()
        (self.repo / "notes.md").write_text("someone else's newer note\n", encoding="utf-8")
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        self.controller.git_restore()
        self.controller.join()
        self.assertEqual(self.read(), CALCULATOR_BAD, "back to the copy from before the task")
        self.assertEqual(self.read("notes.md"), "someone else's newer note\n")
        self.assertNotIn("restore", self.controller.snapshot()["git"], "the offer was taken")
        rows = str(self.controller.snapshot()["messages"])
        self.assertIn("Restored 1 file(s) from commit", rows)

    def test_a_new_task_clears_an_offer_left_by_the_last_one(self):
        self.apply_a_fix()
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        self.assertIn("restore", self.controller.snapshot()["git"])
        self.controller.start_plan("Something else entirely")
        self.controller.join()
        self.assertNotIn("restore", self.controller.snapshot()["git"],
                         "the previous task's commit is the wrong answer for this one")

    def test_a_folder_with_no_checkpoint_keeps_the_old_refusal(self):
        """No commit means no honest "before", so the escalation must not appear at all."""
        plain = self.app_dir / "plain"
        (plain / "tests").mkdir(parents=True)
        (plain / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        other = Scripted(self.app_dir)
        other.catalogs["Ollama"] = [OLLAMA_ENTRY]
        other.model = "test-local"
        other.set_repo(str(plain))
        self.addCleanup(other.close)
        other.start_plan("Fix add in calculator.py")
        other.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            other.apply()
            other.join()
        (plain / "calculator.py").write_text("def add(a, b):\n    return a * b\n",
                                             encoding="utf-8", newline="\n")
        git_integration.forget()
        other.undo()
        other.join()
        self.assertEqual(other.snapshot()["git"].get("restore"), None)
        self.assertIn("changed after review", str(other.snapshot()["messages"]).lower())


class RememberedFolderModeTests(unittest.TestCase):
    """Granting a folder is a decision about *that folder*, so it has to outlive the window.

    Found by the ecommerce run: the folder was granted, landed on Change mode exactly as it should,
    Auto-Apply was remembered for it — and after a restart the same folder was back in Chat mode,
    because only `set_composer` wrote the preference map and the paths that select a branch with an
    explicit composer wrote nothing.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "svc"
        (self.repo / "src").mkdir(parents=True)

    def window(self):
        made = Scripted(self.app_dir)
        made.catalogs["Ollama"] = [OLLAMA_ENTRY]
        made.model = "test-local"
        self.addCleanup(made.close)
        return made

    def test_a_folder_granted_in_change_mode_restarts_in_change_mode(self):
        first = self.window()
        first.set_repo(str(self.repo))
        self.assertEqual(first.composer, "change")
        first.close()
        second = self.window()
        second.set_repo(str(self.repo))
        self.assertEqual(second.composer, "change", "the mode was set and then forgotten")

    def test_a_mode_the_person_picked_by_hand_is_remembered_the_same_way(self):
        """The fix must not turn into the tool overruling the person: a hand-picked Chat mode has to
        survive the restart exactly as a granted Change mode does."""
        first = self.window()
        first.set_repo(str(self.repo))
        first.set_composer("chat")
        first.close()
        second = self.window()
        second.set_repo(str(self.repo))
        self.assertEqual(second.composer, "chat")

    def test_a_plain_chat_branch_still_defaults_to_prose(self):
        window = self.window()
        self.assertEqual(window.composer, "chat", "no folder, nothing to propose against")


class BlockApplyTests(unittest.TestCase):
    """UI 3.0 §3: the ⚡ Apply to File button opens a proposal, and only Apply writes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "main.py").write_text("def add(a, b):\n    return a - b\n",
                                                   encoding="utf-8", newline="\n")
        (self.repo / ".env").write_text("TOKEN=1\n", encoding="utf-8")
        for patcher in (patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),
                        patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[FREE_ENTRY])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = []

    def project(self):
        controller = Scripted(self.app_dir)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        controller.projects["repo"] = str(self.repo)
        controller._select_branch("project", "repo", composer="change")

        def drain(c=controller):
            c.cancel_event.set()
            c.join(10)
        self.addCleanup(drain)
        return controller

    def offer(self, controller, path, content):
        controller.action("apply_block", {"path": path, "content": content}, self.events.append)
        controller.join()
        return controller.snapshot()

    def test_the_block_becomes_a_proposal_and_the_file_stays_as_it_was(self):
        controller = self.project()
        state = self.offer(controller, "src/main.py", "def add(a, b):\n    return a + b\n")
        self.assertEqual((self.repo / "src" / "main.py").read_text(encoding="utf-8"),
                         "def add(a, b):\n    return a - b\n", "the button must not write")
        self.assertEqual(controller.session["state"], "WAITING_APPROVAL")
        self.assertTrue(state["review"]["canApply"])
        self.assertIn("Review the diff", controller.status)
        self.assertIn({"kind": "view", "value": "preview"}, self.events,
                      "the side viewer has to open on its own, or the proposal is invisible")

    def test_the_reviewed_diff_shows_the_line_the_block_replaces(self):
        controller = self.project()
        self.offer(controller, "src/main.py", "def add(a, b):\n    return a + b\n")
        change = controller.session["changes"][0]
        self.assertEqual(change["before"], "def add(a, b):\n    return a - b\n")
        self.assertEqual(change["after"], "def add(a, b):\n    return a + b\n")

    def test_a_block_addressed_at_a_protected_path_writes_nothing(self):
        controller = self.project()
        for name in (".env", "../outside.py", "C:\\Windows\\win.ini"):
            self.offer(controller, name, "x = 1\n")
            self.assertIsNone(controller.session, name)
            self.assertTrue((self.repo / ".env").exists())
        self.assertEqual(list((self.app_dir / ".agent-runs").glob("*/session.json")), [])

    def test_a_block_offer_needs_a_project_and_says_so(self):
        controller = Scripted(self.app_dir)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        self.addCleanup(controller.close)
        self.offer(controller, "src/main.py", "x = 1\n")
        self.assertEqual(controller.session, None)
        self.assertIn("Choose a project", controller.status)

    def test_a_half_written_block_is_refused_rather_than_offered_empty(self):
        controller = self.project()
        self.offer(controller, "src/main.py", "def add(a, b):\n")
        self.assertIsNone(controller.session)
        self.assertIn("syntax", controller.status.lower())

    def test_an_unchanged_block_is_refused_as_nothing_to_write(self):
        controller = self.project()
        self.offer(controller, "src/main.py", "def add(a, b):\n    return a - b\n")
        self.assertIsNone(controller.session)
        self.assertIn("same text the file already holds", controller.status.lower())
        self.assertNotIn("Switch the badge", controller.status,
                         "the chat-mode advice is for a message, not for a block that was clicked")


class TheModelFilter(unittest.TestCase):
    """Searching the model list is a read over what the provider returned.

    The first version wrote the filtered subset back into the catalog, so a search cost the user
    every model that did not match until the next refresh — and then cleared ``model`` when the
    query stopped matching the one their task was running.
    """

    ENTRIES = [{"id": "qwen2.5-coder:1.5b", "name": "qwen 2.5 coder", "cloud": False,
                "description": "Runs locally on your device.  |  0.99 GB"},
               {"id": "llama3.1:70b-cloud", "name": "llama 3.1 70b", "cloud": True,
                "description": "Ollama cloud model - internet and an Ollama account required."},
               {"id": "codellama:13b", "name": "codellama 13b", "cloud": False,
                "description": "Runs locally on your device.  |  7.37 GB"}]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.controller = AgentController(Path(self.temp.name))
        self.addCleanup(self.controller.close)
        self.controller.catalogs["Ollama"] = [dict(entry) for entry in self.ENTRIES]
        self.controller.model = "codellama:13b"

    def listed(self):
        return [entry["id"] for entry in self.controller.visible_models()]

    def test_a_filter_narrows_the_list_without_losing_the_catalog(self):
        self.controller.set_filter("runs locally")
        self.assertEqual(self.listed(), ["qwen2.5-coder:1.5b", "codellama:13b"])
        self.controller.set_filter("")
        self.assertEqual(self.listed(), [entry["id"] for entry in self.ENTRIES],
                         "every model the provider listed is still there after a search")

    def test_the_view_cannot_be_used_to_rewrite_the_catalog(self):
        self.controller.visible_models().clear()
        self.assertEqual(len(self.controller.catalogs["Ollama"]), 3,
                         "the caller gets a list it may sort or drop without touching the store")

    def test_searching_never_deselects_the_model_a_task_is_running(self):
        self.controller.set_filter("cloud")
        self.assertNotIn("codellama:13b", self.listed(), "it is hidden from the list")
        self.assertEqual(self.controller.model, "codellama:13b", "but it is still the model in use")
        self.assertIn("codellama 13b", self.controller._model_info(),
                      "and the settings line still describes it")

    def test_the_snapshot_serves_the_view_rather_than_the_catalog(self):
        self.controller.set_filter("13b")
        served = [entry["id"] for entry in self.controller.snapshot()["provider"]["models"]]
        self.assertEqual(served, ["codellama:13b"])

    def test_the_settings_line_says_what_the_filter_hid(self):
        self.controller.model = ""
        self.controller.set_filter("cloud")
        info = self.controller.snapshot()["settings"]["model_info"]
        self.assertIn("1 of 3", info)
        self.assertIn("Clear the filter", info)

    def test_typing_in_the_box_does_not_write_preferences_to_disk(self):
        with patch.object(AgentController, "_save_state") as saved:
            for letter in "qwe":
                self.controller.set_filter(letter)
        self.assertEqual(saved.call_count, 0, "a filter is a view, not a preference")


class GatingModel:
    """A model that holds its first answer until the test lets it go.

    "While a task is running" has to be a real running task, not a flag set by hand: the whole
    point of the queue is that it drains from inside a job finishing.
    """

    model = "test-local"

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.prompts = []

    def generate(self, messages, json_mode=True):
        self.prompts.append(messages)
        if len(self.prompts) == 1:
            self.gate.wait(20)
        return json.dumps({"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
                           "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]})


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        self.model = GatingModel()
        started = [patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                   patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY]),
                   patch("ai_code_engineer.runner.run", return_value=run_result(proof=PROOF))]
        for patcher in started:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)
        self.events = []

    def emit(self, event):
        self.events.append(event)

    def action(self, type, **payload):
        self.controller.action(type, payload, self.emit)

    def running(self):
        """Block until the controller says it is busy, so the queue is filled mid-flight."""
        self.model.gate.clear()
        self.controller.start_plan("Fix add in calculator.py")
        for _ in range(400):
            if self.controller.busy and self.controller.session is None and self.model.prompts:
                return
            threading.Event().wait(0.02)
        self.fail("the first task never got as far as the model")

    def finish(self):
        self.model.gate.set()
        self.controller.join()

    def queued(self):
        return [item["text"] for item in self.controller.snapshot()["queue"]["items"]]

    def asked(self):
        """Every message the model was actually sent, in order.

        Assertions go through this and through the thread rather than through a count of model
        calls: the planning loop can ask more than once per task, so a count measures the loop and
        not the queue.
        """
        return [json.dumps(prompt, ensure_ascii=False) for prompt in self.model.prompts]

    def thread(self):
        return [message["text"] for message in self.controller.snapshot()["messages"]
                if message["role"] == "user"]

    # -------------------------------- the queue itself --------------------------------
    def test_sending_midflight_queues_the_message_instead_of_dropping_it(self):
        self.running()
        self.action("queue_add", text="And add a test for the duplicate case")
        self.assertEqual(self.queued(), ["And add a test for the duplicate case"])
        self.assertTrue(self.controller.busy, "queueing must not disturb the running task")

    def test_the_queued_message_starts_by_itself_when_the_running_task_ends(self):
        self.running()
        self.action("queue_add", text="And add a test")
        self.finish()
        self.assertEqual(self.queued(), [], "it left the queue")
        self.assertEqual(self.thread()[-1], "And add a test", "the queue never ran it")
        self.assertIn("And add a test", self.asked()[-1])
        self.assertEqual(self.controller.session["task"], "And add a test")

    def test_two_queued_messages_run_in_the_order_they_were_typed(self):
        self.running()
        self.action("queue_add", text="First asked")
        self.action("queue_add", text="Second asked")
        self.assertEqual(self.queued(), ["First asked", "Second asked"])
        self.finish()
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.thread()[-2:], ["First asked", "Second asked"],
                         "FIFO is the whole contract of a queue")

    def test_a_queued_message_belongs_to_the_chat_it_was_typed_in(self):
        self.running()
        self.action("queue_add", text="Only makes sense here")
        chat = self.controller.chat_id
        self.finish()
        self.assertEqual(self.controller.session["task"], "Only makes sense here")
        self.assertEqual(self.controller.chat_id, chat, "it ran in its own conversation")

    def test_a_queued_message_does_not_run_in_someone_elses_chat(self):
        """The item belongs to the conversation that typed it, and `elsewhere` is how the strip
        admits it exists without drawing a line that would fire in the wrong thread."""
        self.running()
        self.action("queue_add", text="Wait for me")
        chat = self.controller.chat_id
        self.controller.cancellable = True
        self.controller.stop()                       # a stop holds the queue
        self.model.gate.set()
        self.controller.join()
        self.controller.new_chat()
        state = self.controller.snapshot()["queue"]
        self.assertEqual(state["items"], [], "this chat has nothing of its own waiting")
        self.assertEqual(state["elsewhere"], 1, "and the strip says so rather than hiding it")
        self.assertNotIn("Wait for me", self.thread(), "it must not run in a chat that never asked")
        key = next(iter(self.controller.projects))
        self.controller._select_branch("project", key, chat_id=chat)
        self.assertEqual(self.controller.chat_id, chat)
        self.assertEqual(self.queued(), ["Wait for me"], "back home, its line is there again")
        self.action("queue_resume")
        self.controller.join()
        self.assertEqual(self.thread()[-1], "Wait for me")

    def test_stop_holds_the_queue_so_stop_means_stop(self):
        self.running()
        self.action("queue_add", text="Would have fired next")
        self.controller.cancellable = True
        self.controller.stop()
        self.model.gate.set()
        self.controller.join()
        self.assertNotIn("Would have fired next", self.thread(), "the queue ran through a stop")
        self.assertEqual(self.queued(), ["Would have fired next"])
        self.assertTrue(self.controller.snapshot()["queue"]["held"])
        self.action("queue_resume")
        self.controller.join()
        self.assertEqual(self.thread()[-1], "Would have fired next", "and ▶ lets it go again")

    # -------------------------------- the per-item actions --------------------------------
    def test_editing_changes_what_will_run(self):
        self.running()
        self.action("queue_add", text="Add a test")
        item = self.controller.queue[0]["id"]
        self.action("queue_edit", id=item, text="Add two tests, please")
        self.assertEqual(self.queued(), ["Add two tests, please"])
        self.finish()
        self.assertEqual(self.controller.session["task"], "Add two tests, please")

    def test_dropping_removes_it_without_touching_the_running_task(self):
        self.running()
        self.action("queue_add", text="Forget this one")
        self.action("queue_drop", id=self.controller.queue[0]["id"])
        self.assertEqual(self.queued(), [])
        self.assertTrue(self.controller.busy)
        self.finish()
        self.assertNotIn("Forget this one", self.thread())

    def test_run_next_moves_the_item_to_the_front(self):
        self.running()
        self.action("queue_add", text="Asked first")
        self.action("queue_add", text="Asked second")
        self.action("queue_now", id=self.controller.queue[-1]["id"])
        self.assertEqual(self.queued(), ["Asked second", "Asked first"])
        self.finish()
        self.assertEqual(self.thread()[-2:], ["Asked second", "Asked first"],
                         "the item moved to the front, so the other one runs last")

    def test_a_message_queued_after_the_task_already_ended_runs_at_once(self):
        """The click can land a moment too late; a line that says "Queued" and never fires is a lie."""
        self.action("queue_add", text="Nothing is running, so this just sends")
        self.controller.join()
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.controller.session["task"], "Nothing is running, so this just sends")

    def test_opening_in_a_separate_chat_moves_it_out_of_this_conversation(self):
        self.running()
        chat = self.controller.chat_id
        self.action("queue_add", text="This deserves its own thread")
        self.action("queue_chat", id=self.controller.queue[0]["id"])
        self.assertTrue(self.controller.queue[0]["detached"],
                        "mid-task it waits for the branch to be movable, and never runs here")
        self.model.gate.set()
        self.controller.join()
        self.assertNotEqual(self.controller.chat_id, chat, "it asked in a chat of its own")
        self.assertEqual(self.controller.repo, str(self.repo),
                         "and a new chat in this project keeps the folder it was written for")
        self.assertIn("This deserves its own thread", self.asked()[-1])
        self.assertEqual(self.thread()[-1], "This deserves its own thread",
                         "it appears in the thread of the chat it was moved to")
        self.assertEqual(self.queued(), [])

    def test_the_queue_comes_back_held_and_starts_nothing(self):
        """The old rule was "in memory only", because a queued change request can be pointed at
        files that moved on since it was typed. Losing a twelve-message batch to one restart is the
        other half of the same problem, so the queue is stored and what holds the invariant is the
        hold itself: nothing drains until the operator presses ▶."""
        self.running()
        self.action("queue_add", text="Do not start this on its own")
        again = Scripted(self.app_dir)              # the window restarts mid-batch
        self.addCleanup(again.close)
        self.model.gate.set()
        self.controller.join()
        self.assertEqual([item["text"] for item in again.queue], ["Do not start this on its own"])
        self.assertTrue(all(item.get("restored") for item in again.queue),
                        "a row typed in another session has to say so")
        again.join(timeout=5)
        self.assertIsNone(again.session, "held means no worker took it")
        self.assertTrue(again.snapshot()["queue"]["held"])
        again.queue_resume()
        self.assertFalse(any(item.get("restored") for item in again.queue),
                         "the press is the approval, and it retires the note")

    def test_a_queued_batch_says_what_it_landed_when_it_ends(self):
        self.controller.set_auto_apply(True)
        self.running()
        self.action("queue_add", text="Create exactly two new files. One of them will not appear.")
        self.finish()
        texts = [message["text"] for message in self.controller.snapshot()["messages"]
                 if message["role"] == "tool"]
        summary = [text for text in texts if "Batch finished" in text]
        self.assertEqual(len(summary), 1, "one row per batch, not one per task: " + repr(texts[-3:]))
        self.assertIn("1 task(s)", summary[0])

    def test_the_batch_row_flags_the_count_the_task_asked_for(self):
        """D36: "Create exactly two new files" answered with one. The flag reads only the literal
        scaffold phrase, so a task that never named a count is not second-guessed."""
        self.controller.set_auto_apply(True)
        self.running()
        self.action("queue_add", text="Create exactly three new files")
        self.finish()
        summary = next(text for text in (message["text"] for message in
                                         self.controller.snapshot()["messages"]
                                         if message["role"] == "tool") if "Batch finished" in text)
        self.assertIn("asked for a file count it did not deliver", summary)

    def test_a_task_that_named_no_count_is_not_judged_for_one(self):
        self.controller.set_auto_apply(True)
        self.running()
        self.action("queue_add", text="Add a helper that reads the config file")
        self.finish()
        summary = next(text for text in (message["text"] for message in
                                         self.controller.snapshot()["messages"]
                                         if message["role"] == "tool") if "Batch finished" in text)
        self.assertNotIn("did not deliver", summary)

    def test_a_message_too_long_is_refused_where_it_stands(self):
        self.running()
        before = len(self.controller.snapshot()["messages"])
        self.action("queue_add", text="x" * 4001)
        self.assertEqual(self.queued(), [])
        messages = self.controller.snapshot()["messages"]
        self.assertEqual(len(messages), before + 1, "the refusal has to be visible, not a silent no")
        self.assertIn("4,000", messages[-1]["text"])
        self.model.gate.set()
        self.controller.join()

    def test_the_same_message_twice_is_one_line(self):
        self.running()
        self.action("queue_add", text="Did that run yet?")
        self.action("queue_add", text="Did that run yet?")
        self.assertEqual(self.queued(), ["Did that run yet?"])
        self.model.gate.set()
        self.controller.join()

    def test_the_snapshot_describes_the_strip(self):
        payload = self.controller.snapshot()["queue"]
        self.assertEqual(sorted(payload), ["elsewhere", "elsewhere_note", "held", "held_note",
                                           "items", "when", "when_detached", "when_restored"],
                         "the strip needs identity, state and the server's own sentences")
        self.assertEqual(payload["items"], [])
        self.assertFalse(payload["held"])
        self.action("queue_add", text="Idle add runs at once")
        self.controller.join()
        self.assertIn("when", self.controller.snapshot()["queue"])


class SteppingModel:
    """Reads a file, reads it again, searches, then proposes — the shape of a real turn loop.

    The repeat is the point: a small model re-reading the same file is common enough that the
    thread has to say it once, not twice.
    """

    model = "test-local"
    SCRIPT = ({"action": "read_file", "path": "calculator.py"},
              {"action": "read_file", "path": "calculator.py"},
              {"action": "search_code", "query": "def add"},
              {"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
               "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]})

    def __init__(self):
        self.turn = 0
        self.prompts = []

    def generate(self, messages, json_mode=True):
        self.prompts.append(messages)
        action = self.SCRIPT[min(self.turn, len(self.SCRIPT) - 1)]
        self.turn += 1
        return json.dumps(action)


class TheActivityFeed(unittest.TestCase):
    """UI 3.4: what the agent did reaches the chat, and the line that says it survives a push.

    The class of defect this holds against is a sentence that exists only in the server's own
    memory: `controller.status` was assigned in about sixty places and read by no client, so the
    progress line went into the header subtitle instead and the next state push erased it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        self.model = SteppingModel()
        started = [patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                   patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY])]
        for patcher in started:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)

    def steps(self):
        return [message["text"] for message in self.controller.snapshot()["messages"]
                if message.get("author") == "Steps"]

    def row_by_author(self, author):
        return [message["text"] for message in self.controller.snapshot()["messages"]
                if message.get("author") == author]

    def test_each_tool_action_says_so_in_the_thread(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        self.assertEqual(self.steps(), [
            "\U0001f4d6 Reading file: calculator.py",
            "\U0001f50d Searching code: def add",
            "\u270d\ufe0f Proposed changes for 1 file(s): calculator.py"],
            "a repeated read of the same file must collapse into one line")

    def test_the_step_language_follows_the_task_not_the_window(self):
        self.controller.start_plan("عدّل دالة الجمع في calculator.py")
        self.controller.join()
        said = self.steps()
        self.assertTrue(said, "the Arabic task announced nothing")
        self.assertIn("قراءة الملف", said[0])
        self.assertNotIn("Reading file", " ".join(said))

    def test_the_snapshot_carries_the_activity_line_and_the_outcome_replaces_it(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        state = self.controller.snapshot()
        self.assertEqual(state["status"],
                         "Proposal ready. Review the changes, then apply them if you want.",
                         "the strip has to end on what happened, not on a running line")

    def test_a_manual_apply_is_recorded_in_the_thread(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()
        self.assertIn("\U0001f4be Applied changes to 1 file(s). Roll back undoes the whole task.",
                      self.row_by_author("Tool"))

    def test_running_a_command_names_the_command_it_runs(self):
        """The argv is said before the child starts — and the row ends in the past tense.

        UI 4.1 rewrote the second half of this deliberately: a thread still reading "Executing" after
        the build finished describes a moment that has passed, and the reader cannot tell a finished
        red run from one they are still waiting on.
        """
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()
            self.controller.run_tests(False)
            self.controller.join()
        said = " | ".join(self.steps())
        self.assertIn("\u2699\ufe0f Ran python -m unittest discover -s tests -v", said,
                      "the interpreter is named, not pathed, but the argv the child gets is real")
        self.assertNotIn("Executing:", said, "the row never settled into what came back")
        self.assertIn("passed", said)

    def test_a_failing_build_is_redacted_where_the_user_reads_it(self):
        """The Checks row is built from the raw runner result, and `runner` does not scrub.

        Storage and the model both get redacted copies; the chat is the surface a printed
        credential would otherwise reach, and every row in it can be copied out with one click.
        """
        secret = 'FAILED tests: password = "hunter2-secret-value"'
        failing = run_result(status="failed", failures=[secret])
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=failing):
            self.controller.run_tests(False)
            self.controller.join()
        checks = " ".join(self.row_by_author("Checks"))
        self.assertIn("hunter2", secret, "the fixture has to carry the shape being tested")
        self.assertNotIn("hunter2", checks)
        self.assertIn("[redacted]", checks)
        self.assertIn("The full output is in Activity", checks)
        self.assertNotIn("**", checks, "a tool row is escaped, never rendered")

    def test_a_running_line_does_not_outlive_the_job(self):
        """Two sentences belong on the strip after a job: an outcome, and nothing else."""
        self.controller.run_job(lambda: self.controller._progress("Turn 5/12: asking test-local..."),
                                lambda _result: None, "Connecting to the model and preparing changes…")
        self.controller.join()
        self.assertEqual(self.controller.status, "",
                         "neither the running label nor the last progress line should linger")

        def finish(_result):
            self.controller.status = "Proposal ready. Review the changes, then apply them if you want."

        self.controller.run_job(lambda: self.controller._progress("Turn 6/12: asking test-local..."),
                                finish, "Connecting to the model and preparing changes…")
        self.controller.join()
        self.assertEqual(self.controller.status,
                         "Proposal ready. Review the changes, then apply them if you want.")

    def test_a_blocked_task_says_why_in_the_language_it_was_asked(self):
        class Blocking:
            model = "test-local"

            def generate(self, messages, json_mode=True):
                return json.dumps({"action": "blocked", "reason": "the repository has no build file"})

        provider = patch("ai_code_engineer.webapp.controller.make_provider", return_value=Blocking())
        provider.start()
        self.addCleanup(provider.stop)
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        said = " ".join(self.row_by_author("Tool"))
        self.assertIn("\u26d4", said)
        self.assertIn("the repository has no build file", said)


class TheStepRows(unittest.TestCase):
    """UI 4.1: every step row is a handle on a record, and what it opens is fetched, not shipped.

    Three things had to hold at once, and each is a failure mode this window has already paid for: the
    row has to survive a restart (so the narrative is not lost when a session is reopened), a row with
    nothing behind it must not look openable (a chevron that opens onto nothing teaches the reader to
    stop opening rows), and the detail must not ride every snapshot (a stored run tail is 2 500
    characters, and a task with a build per turn would carry all of them forever).
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name)
        self.repo = self.app_dir / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        self.model = SteppingModel()
        for patcher in (patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                        patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[OLLAMA_ENTRY])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)
        self.events: list[dict] = []

    def step_rows(self):
        return [(message.get("step") or {}) for message in self.controller.snapshot()["messages"]
                if message.get("step")]

    def propose_apply_run(self, run=None):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run or run_result()):
            self.controller.apply()
            self.controller.join()
            self.controller.run_tests(False)
            self.controller.join()

    def test_every_step_row_carries_an_id_and_says_whether_it_opens(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        rows = self.step_rows()
        self.assertEqual([row["action"] for row in rows], ["read_file", "search_code", "propose"])
        self.assertEqual(len({row["id"] for row in rows}), len(rows), "two rows share a handle")
        self.assertTrue(all("detail" in row for row in rows))
        self.assertTrue(rows[0]["detail"], "the read row knows which version of the file it saw")
        self.assertTrue(rows[2]["detail"], "a proposal always has its file list behind it")

    def test_a_step_is_recorded_so_the_row_survives_reopening_the_task(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        live = self.step_rows()
        stored = [item for item in self.controller.session["events"] if item.get("kind") == "step"]
        # The loop records every action it took and the thread shows a repeated one once, so history has
        # to come back looking like the thread — not like the record, which would show a step nobody saw.
        self.assertGreater(len(stored), len(live), "the fixture must still repeat a read")
        self.assertEqual({row["id"] for row in live} - {item["id"] for item in stored}, set(),
                         "a row on screen has no record behind it")
        self.controller.display_session(self.controller.session_path, select=True)
        self.assertEqual([(row["id"], row["action"]) for row in self.step_rows()],
                         [(row["id"], row["action"]) for row in live],
                         "reopening the task did not give back the steps that were on screen")

    def test_the_rebuilt_rows_are_sentences_in_the_language_of_the_task(self):
        self.controller.start_plan("عدّل دالة الجمع في calculator.py")
        self.controller.join()
        self.controller.display_session(self.controller.session_path, select=True)
        rows = [message["text"] for message in self.controller.snapshot()["messages"]
                if message.get("author") == "Steps"]
        self.assertTrue(rows)
        self.assertTrue(any("قراءة الملف" in row for row in rows),
                        "a rebuilt row answered in English: " + " | ".join(rows))

    def test_opening_a_run_row_answers_with_the_command_and_its_output(self):
        self.propose_apply_run(run_result(status="failed", failures=["AssertionError: 3 != 4"]))
        row = next(item for item in self.step_rows() if item["action"] == "executed")
        self.controller.action("step_detail", {"id": row["id"]}, self.events.append)
        detail = self.controller.snapshot()["step_detail"]
        self.assertEqual(detail["id"], row["id"])
        flat = " ".join(title + " " + " ".join(lines) for title, lines in detail["sections"])
        for needle in ("python -m unittest", "failed", "exit 1", "1.4s", "AssertionError", "Ran 4 tests"):
            self.assertIn(needle, flat, "the stored run did not reach its row: " + flat)

    def test_a_row_with_nothing_stored_behind_it_answers_in_words(self):
        """A click that does nothing is the dead-button failure this window already had once."""
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        self.controller.action("step_detail", {"id": "never-sent"}, self.events.append)
        self.assertTrue(self.controller.snapshot()["step_detail"]["note"],
                        "an unknown row opened onto silence")
        self.controller.action("step_detail", {"id": ""}, self.events.append)
        self.assertIsNone(self.controller.snapshot()["step_detail"], "closing must clear the block")

    def test_the_detail_is_only_ever_the_row_that_was_asked_for(self):
        self.propose_apply_run()
        rows = self.step_rows()
        self.controller.action("step_detail", {"id": rows[0]["id"]}, self.events.append)
        held = self.controller.snapshot()["step_detail"]
        self.assertEqual(held["id"], rows[0]["id"])
        self.assertNotIn(rows[1]["id"], json.dumps(held))

    def test_a_run_no_session_could_hold_still_answers_its_row(self):
        """D28 made this shape possible: the task before the run blocked, so `record_run` refused the
        result. The row is on screen and has a chevron, so what it opens has to say why nothing is in it."""
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        session = load_session(self.controller.session_path)
        session["state"] = "BLOCKED"
        atomic_json(self.controller.session_path, session)
        self.controller.display_session(self.controller.session_path)
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.run_tests(False)
            self.controller.join()
        row = next(item for item in self.step_rows() if item["action"] == "executed")
        self.controller.action("step_detail", {"id": row["id"]}, self.events.append)
        detail = self.controller.snapshot()["step_detail"]
        self.assertIn("no applied task is open", detail["note"])
        self.assertEqual(detail["sections"], [])

    def test_the_activity_log_is_capped_and_says_what_it_dropped(self):
        for index in range(MAX_LOG_ENTRIES + 5):
            self.controller._note("progress", f"line {index}")
        state = self.controller.snapshot()
        self.assertEqual(len(state["log"]), MAX_LOG_ENTRIES)
        self.assertEqual(state["log_dropped"], 5)
        self.assertIn("5 earlier line(s)", state["log_note"])
        self.assertEqual(state["log"][0]["text"], "line 5", "the tail survives, not the head")

    def test_reopening_a_long_task_caps_its_rebuilt_log_the_same_way(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        self.controller.session["events"].extend(
            {"at": "2026-09-28T10:00:00", "kind": "step", "id": f"x{n}", "action": "list_files",
             "count": 1} for n in range(MAX_LOG_ENTRIES))
        # `display_session` reads the file back, so a test that only edited memory would prove nothing.
        atomic_json(self.controller.session_path, self.controller.session)
        self.controller.display_session(self.controller.session_path, select=True)
        state = self.controller.snapshot()
        self.assertLessEqual(len(state["log"]), MAX_LOG_ENTRIES)
        self.assertGreater(state["log_dropped"], 0)
        self.assertTrue(state["log_note"])


class WithdrawnQuestionTests(unittest.TestCase):
    """A modal the browser never answered outlives the job that asked for it.

    Measured during the ecommerce dogfood run: nine fix-round offers timed out over a long build
    session and all nine sheets stayed stacked over the app, so the window looked idle while the
    topmost invisible scrim swallowed every click.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.controller = AgentController(Path(self.temp.name))
        self.addCleanup(self.controller.close)
        self.events = []
        self.controller._emit = self.events.append
        timeout = patch("ai_code_engineer.webapp.controller.ASK_TIMEOUT", 0.2)
        timeout.start()
        self.addCleanup(timeout.stop)

    def kinds(self):
        return [event["kind"] for event in self.events]

    def test_a_question_that_is_never_answered_is_withdrawn(self):
        self.assertFalse(self.controller.confirm("Keep going?", "Fix round 1 of 3"))
        self.assertEqual(self.kinds(), ["confirm", "retract"])
        self.assertEqual(self.events[0]["id"], self.events[1]["id"],
                         "the window is told which question to forget, so a late answer cannot hide another")
        self.assertIn("withdrawn", self.controller.status)
        self.assertEqual(self.controller._replies, {}, "the abandoned waiter is not left behind")

    def test_an_answered_question_is_left_on_screen_for_its_own_click_to_clear(self):
        def answer():
            for _ in range(200):
                if self.events:
                    self.controller.set_reply(self.events[0]["id"], {"ok": True})
                    return
                threading.Event().wait(0.01)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        self.assertTrue(self.controller.confirm("Keep going?", "Fix round 1 of 3"))
        thread.join(5)
        self.assertEqual(self.kinds(), ["confirm"])

    def test_an_answer_that_arrives_after_the_withdrawal_is_dropped(self):
        self.controller.confirm("Keep going?", "Fix round 1 of 3")
        self.controller.set_reply(self.events[0]["id"], {"ok": True})
        self.assertEqual(self.controller._answers, {},
                         "a reply for a dead question must not sit in the store forever")


class SecondSendBecomesAQueueItem(unittest.TestCase):
    """Two clicks in one tick used to cost the second message entirely.

    Measured in the ecommerce run: three prompts were sent back to back, one started, and the other two
    reached `start_plan` while the client's `busy` flag had not yet arrived — `run_job` answered with a
    silent `return`, so two written tasks vanished without a status line or a trace.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.controller = AgentController(Path(self.temp.name))
        self.addCleanup(self.controller.close)
        self.events = []
        self.controller._emit = self.events.append

    def test_the_job_itself_hands_a_late_message_to_the_queue(self):
        """The race is at the claim, not at the check: `busy` is read by two HTTP threads before either
        job has set it, so the guard that actually drops the work is the one inside `run_job`."""
        landed = []
        self.controller.busy = True
        before = self.controller.status
        self.controller.run_job(lambda: "never runs", lambda result: None, "Working…",
                                on_busy=lambda: landed.append("queued"))
        self.assertEqual(landed, ["queued"])
        self.assertEqual(self.controller.status, before,
                         "a refused job must not leave its running line on the strip")

    def test_both_ways_a_message_arrives_are_wired_to_it(self):
        """A plan and a chat reply are the two entries; either may be the one that arrives late."""
        import inspect
        from ai_code_engineer.webapp import controller
        source = Path(inspect.getfile(controller)).read_text(encoding="utf-8")
        self.assertEqual(source.count("on_busy=lambda: self.queue_add(task)"), 2,
                         "start_plan and start_chat both hand their text to the queue")

    def test_a_refused_start_does_not_spend_the_row(self):
        """The drain used to pop before starting, so any of `start_plan`'s refusals ate the message."""
        self.controller.model = ""                      # a refusal the queue can do nothing about
        self.controller.queue.append({"id": "x1", "text": "Create exactly one new file: A.java",
                                      "chat": self.controller.chat_id})
        self.controller._drain_queue()
        self.assertEqual([item["text"] for item in self.controller.queue],
                         ["Create exactly one new file: A.java"])

    def test_a_start_that_takes_the_row_is_the_one_that_removes_it(self):
        taken = []

        def accept(text):
            taken.append(text)
            self.controller._job_started = True         # what `run_job` records on the real path

        self.controller.start_plan = accept
        self.controller.queue.append({"id": "x1", "text": "Create exactly one new file: A.java",
                                      "chat": self.controller.chat_id})
        self.controller._drain_queue()
        self.assertEqual(taken, ["Create exactly one new file: A.java"])
        self.assertEqual(self.controller.queue, [])

    def test_a_message_sent_during_a_running_task_joins_the_queue(self):
        self.controller.busy = True
        self.controller.start_plan("Create exactly one new file: A.java")
        self.assertEqual([item["text"] for item in self.controller.queue],
                         ["Create exactly one new file: A.java"])
        self.assertIn("toast", [event.get("kind") for event in self.events],
                      "the window has to be told the message was taken, not left guessing")

    def test_the_strip_names_an_unanswered_question_as_the_reason_for_waiting(self):
        """Two rows sat in the strip for half an hour with busy=False and held=False, beside a sentence
        promising they ran when the task ended. They were waiting on a fix-round dialog — and a question
        that can hold the queue for the whole timeout is not "the current task". """
        self.controller.queue.append({"id": "x1", "text": "Create exactly one new file: A.java",
                                      "chat": self.controller.chat_id})
        self.assertIn("current task ends", self.controller._queue_view()["when"])
        self.controller._replies["q1"] = threading.Event()
        self.assertIn("answer the question", self.controller._queue_view()["when"],
                      "the strip has to say what the rows are actually behind")
        self.controller._replies.pop("q1")
        self.assertIn("current task ends", self.controller._queue_view()["when"])

    def test_the_queued_messages_do_not_start_while_the_task_runs(self):
        self.controller.busy = True
        self.controller.start_plan("Create exactly one new file: A.java")
        self.controller.start_plan("Create exactly one new file: B.java")
        self.assertEqual(len(self.controller.queue), 2, "the queue holds both, in order")
        self.assertEqual([item["text"] for item in self.controller.queue],
                         ["Create exactly one new file: A.java",
                          "Create exactly one new file: B.java"])


if __name__ == "__main__":
    unittest.main()
