"""The lifecycle every surface shares: ask, review, apply, run, and the state two threads read."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way
from ai_code_engineer import labels, memory, repair
from ai_code_engineer.engine import atomic_json, load_session
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.webapp.controller import AgentController
from doubles import (
                     CALCULATOR_BAD, CALCULATOR_GOOD, ChatModel, FREE_ENTRY, OLLAMA_ENTRY, PLAN,
                     PROOF, ProposalModel, run_result)
from helpers import sandbox_repo
from controller_case import Scripted, patched_catalog


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
class CompactReviewContract(unittest.TestCase):
    def test_summaries_cover_authored_and_legacy_changes_without_mutating_the_proposal(self):
        import copy
        controller = AgentController.__new__(AgentController)
        controller.session = {"id": "task", "proposal_hash": "hash", "state": "WAITING_APPROVAL",
                              "changes": [
            {"path": "canvas.html", "before": None, "after": "<canvas></canvas>\n",
             "description": "Added canvas\n   for the game"},
            {"path": "game.py", "before": "def old():\n    pass\n", "after": "def move():\n    pass\n"},
            {"path": "old.txt", "before": "old\n", "after": None, "delete": True},
        ]}
        controller.busy = False
        controller.review_file = 0
        controller.diff_tab = "diff"
        controller.messages = []
        controller.rejected = lambda: False
        controller.reading_only = lambda: False
        original = copy.deepcopy(controller.session)
        review = controller._review()
        self.assertEqual(review["files"][0]["summary"], "Added canvas for the game")
        self.assertIn("move", review["files"][1]["summary"])
        self.assertIn("Removed", review["files"][2]["summary"])
        self.assertEqual([(f["add"], f["del"]) for f in review["files"]], [(1, 0), (1, 1), (0, 1)])
        self.assertTrue(review["pending"])
        self.assertEqual(controller.session, original)

    def test_explicit_reply_keeps_the_selected_duplicate_message(self):
        controller = AgentController.__new__(AgentController)
        controller.messages = [{"role": "assistant", "text": "Same words"},
                               {"role": "assistant", "text": "Same words"}]
        controller._state = threading.RLock()
        controller._emit = lambda event: None
        controller._add("user", "You", controller.with_quote("Explain", 0), quote_of=0)
        self.assertEqual(controller.messages[-1]["replyTo"], 0)
class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._made = []
        self.addCleanup(self._drain)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        started = (patched_catalog(),)
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
    def test_invalid_attached_plan_reports_its_error_without_crashing(self):
        from ai_code_engineer import planbook
        count = planbook.MAX_STEPS + 1
        plan = self.repo / "too-many-steps.md"
        plan.write_text("\n".join(f"{i}. Task {i}" for i in range(1, count + 1)), encoding="utf-8")
        self.controller.answers["plan"] = str(plan)
        events = []
        with patch.object(self.controller, "_emit", side_effect=events.append):
            self.controller.browse_plan()
        self.assertEqual(self.controller.plan_file, "")
        self.assertIsNone(self.controller.ledger)
        self.assertIn(f"{count} steps", self.controller.status)
        self.assertIn(str(planbook.MAX_STEPS), self.controller.status)
        notice = next(event for event in events if event.get("kind") == "toast")
        self.assertEqual(notice["text"], self.controller.status)
        self.assertEqual(notice["level"], "bad")

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

        def fake_run(repo, recipe, timeout=60, progress=lambda _line: None, target=".",
                     sandbox=""):
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

        def fake_run(repo, recipe, timeout=60, progress=lambda _line: None, target=".",
                     sandbox=""):
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

    # ------------------------------ the container switch ------------------------------
    PINNED = "python@sha256:" + "a" * 64

    def sandbox_row(self):
        return self.controller.snapshot()["sandbox"]

    def test_the_card_says_the_machine_has_no_docker_before_anything_is_asked(self):
        with patch("ai_code_engineer.runner.sandbox_available", return_value=False):
            row = self.sandbox_row()
        self.assertFalse(row["available"])
        self.assertFalse(row["on"])
        self.assertEqual(row["note"], labels.NOTE_TEMPLATES["sandbox_missing"][0])

    def test_a_tick_without_a_digest_is_answered_rather_than_run(self):
        with patch("ai_code_engineer.runner.sandbox_available", return_value=True):
            self.controller.action("sandbox", {"on": True}, lambda _event: None)
            self.assertEqual(self.sandbox_row()["note"], labels.NOTE_TEMPLATES["sandbox_unpinned"][0])
            self.controller.action("sandbox", {"image": self.PINNED}, lambda _event: None)
            row = self.sandbox_row()
        self.assertEqual(row["note"], labels.NOTE_TEMPLATES["sandbox_on"][0])
        self.assertEqual(row["image"], self.PINNED)

    def test_the_image_reaches_the_command_only_while_the_switch_is_on(self):
        seen = []

        def fake_run(repo, recipe, timeout=60, progress=lambda _line: None, target=".",
                     sandbox=""):
            seen.append(sandbox)
            return run_result(proof=PROOF, sandbox={"image": sandbox, "container": "ai-agent-1"}
                              if sandbox else None)

        self.controller.session = {"root": str(self.repo), "state": "APPLIED_UNVERIFIED"}
        with patch("ai_code_engineer.runner.sandbox_available", return_value=True), \
                patch("ai_code_engineer.runner.run", side_effect=fake_run):
            self.controller.action("sandbox", {"on": True, "image": self.PINNED},
                                   lambda _event: None)
            self.controller.run_tests(False)
            self.controller.join()
            self.assertEqual(seen, [self.PINNED])
            self.assertIn("Docker", self.controller.snapshot()["status"],
                          "the running line says where the build is happening")
            self.controller.sandbox_on = False
            self.controller.run_tests(False)
            self.controller.join()
        self.assertEqual(seen, [self.PINNED, ""], "unticking the box has to stop the container run")

    def test_the_container_choice_survives_a_restart(self):
        with patch("ai_code_engineer.runner.sandbox_available", return_value=True):
            self.controller.action("sandbox", {"on": True, "image": self.PINNED},
                                   lambda _event: None)
        reopened = self.build()
        self.assertTrue(reopened.sandbox_on)
        self.assertEqual(reopened.sandbox_image, self.PINNED)

    def test_the_record_keeps_the_image_the_green_came_from(self):
        self.controller.session = {"root": str(self.repo), "state": "APPLIED_UNVERIFIED"}
        with patch("ai_code_engineer.runner.sandbox_available", return_value=True), \
                patch("ai_code_engineer.runner.run",
                      return_value=run_result("passed", proof=PROOF,
                                              sandbox={"image": self.PINNED,
                                                       "container": "ai-agent-1"})):
            self.controller.action("sandbox", {"on": True, "image": self.PINNED},
                                   lambda _event: None)
            self.controller.run_tests(False)
            self.controller.join()
        self.assertIn("in Docker", self.controller.snapshot()["runInfo"])

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

    def test_the_same_failure_twice_stops_the_loop_instead_of_asking_again(self):
        """D42's other half in the window: a round that moved nothing is not a reason to buy another
        model turn, and the stop has to be readable in the thread, not only in a status line."""
        controller = self.applied_controller()
        controller.answers["confirm"] = False
        stuck = run_result(status="failed", failures=["AssertionError: 3 != 4"],
                           proof={"tests": 7, "failures": 3, "errors": 0, "source": "pytest"})
        with patch("ai_code_engineer.webapp.controller.plan") as planner, \
             patch("ai_code_engineer.runner.run", return_value=stuck):
            controller.run_tests(False)
            controller.join()
            controller.asked.clear()
            controller.run_tests(False)
            controller.join()
        planner.assert_not_called()
        self.assertNotIn("Keep going?", [row["title"] for row in controller.asked],
                         "a loop that says it stopped must not also ask to continue")
        self.assertFalse(controller._auto_fix)
        self.assertIn("No progress", controller.status)
        rows = [message["text"] for message in controller.snapshot()["messages"]
                if message["role"] == "tool"]
        self.assertTrue(any(text.startswith("🛑 No progress") for text in rows), rows)
        self.assertTrue(any("Tried so far:" in text and "7 tests, 3 failing" in text
                            for text in rows), rows)

    def test_the_offer_shows_the_rounds_it_is_about_to_add_to(self):
        """The offer is the one moment the user decides whether another turn is worth it, so the
        history belongs in the question rather than in a tab they would have to go and open."""
        controller = self.applied_controller()
        controller.answers["confirm"] = False
        worse = run_result(status="failed", failures=["AssertionError: 3 != 4"],
                           proof={"tests": 7, "failures": 5, "errors": 0, "source": "pytest"})
        better = run_result(status="failed", failures=["AssertionError: 3 != 4"],
                            proof={"tests": 7, "failures": 2, "errors": 0, "source": "pytest"})
        with patch("ai_code_engineer.webapp.controller.plan"), \
             patch("ai_code_engineer.runner.run", side_effect=[worse, better]):
            controller.run_tests(False)
            controller.join()
            controller.asked.clear()
            controller.run_tests(False)
            controller.join()
        asked = [row for row in controller.asked if row["title"] == "Keep going?"]
        self.assertEqual(len(asked), 1, "a moving loop is still worth a round")
        self.assertIn("Tried so far:", asked[0]["message"])
        self.assertIn("1. Python unittest: failed (7 tests, 5 failing", asked[0]["message"])
        self.assertIn("2. Python unittest: failed (7 tests, 2 failing", asked[0]["message"])

    def test_a_passing_run_is_offered_nothing(self):
        controller = self.applied_controller()
        with patch("ai_code_engineer.runner.run", return_value=run_result(proof=PROOF)):
            controller.run_tests(False)
            controller.join()
        self.assertEqual(controller.asked, [])

    # ------------------------- one folder, several projects -------------------------
    def monorepo(self):
        """Two modules with a command each and no build file at the top of the opened folder."""
        root = self.app_dir / "mono"
        for name in ("auth-service", "product-service"):
            folder = root / name
            (folder / "tests").mkdir(parents=True)
            (folder / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (folder / "tests" / "test_one.py").write_text(
                "import unittest\n\n\nclass T(unittest.TestCase):\n"
                "    def test_ok(self):\n        self.assertEqual(2, 1 + 1)\n", encoding="utf-8")
        return root

    def monorepo_controller(self, root):
        controller = Scripted(self.app_dir, None)
        self._made.append(controller)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        with patch("ai_code_engineer.runner.available", return_value=["python-unittest"]):
            controller.set_repo(str(root))
        return controller

    def test_a_folder_of_several_modules_offers_each_one(self):
        """`detect()` on the opened folder alone found nothing here at all: the build files are one
        level down, and the window said "no command was detected" about a project that builds."""
        state = self.monorepo_controller(self.monorepo()).snapshot()
        self.assertEqual([row["path"] for row in state["targets"]],
                         ["auth-service", "product-service"])
        self.assertEqual(state["target"], "auth-service")
        self.assertEqual(state["targetLabel"], "auth-service")
        self.assertEqual(state["recipes"], ["Python unittest"])

    def test_choosing_a_module_moves_the_command_and_the_next_run(self):
        controller = self.monorepo_controller(self.monorepo())
        controller.set_target("product-service")
        self.assertEqual(controller.snapshot()["target"], "product-service")
        seen = {}

        def record(repo, recipe, timeout=60, progress=lambda _line: None, target=".",
                   sandbox=""):
            seen["target"] = target
            return run_result()

        with patch("ai_code_engineer.runner.run", side_effect=record):
            controller.run_tests(False)
            controller.join()
        self.assertEqual(seen["target"], "product-service")
        controller.set_target("nowhere")
        self.assertEqual(controller.target, "product-service",
                         "a label that is not on the list changes nothing")

    def test_a_task_rooted_elsewhere_is_never_handed_this_folders_modules(self):
        """The module list was scanned from the window's folder. A session pointed at another tree
        gets that tree's root, because `auth-service` there is a different project's directory."""
        controller = self.monorepo_controller(self.monorepo())
        controller.set_target("product-service")
        controller.session = {"root": str(self.repo), "state": "APPLIED_UNVERIFIED"}
        seen = {}

        def record(repo, recipe, timeout=60, progress=lambda _line: None, target=".",
                   sandbox=""):
            seen["target"] = target
            seen["repo"] = repo
            return run_result()

        with patch("ai_code_engineer.runner.run", side_effect=record):
            controller.run_tests(False)
            controller.join()
        self.assertEqual(seen["target"], ".")
        self.assertEqual(Path(seen["repo"]), Path(self.repo))

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
class TheStateUnderTwoThreads(unittest.TestCase):
    """UI 4.2 hardening: one writer, one HTTP thread per browser tab, and a snapshot that is a copy.

    The controller was written as a single-threaded object with a daemon thread that happened to be
    slow. Two HTTP handlers used to read `busy`, both see False, and both start a job — the second
    one overwrites the first's `on_done` chain and its writes land in whichever task was last to
    claim the branch. `snapshot()` returned live lists, so a page reading state while a job appended
    a step could be handed a list mid-append, or a list it then sorted in place.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        started = [patch("ai_code_engineer.webapp.controller.make_provider", return_value=ChatModel()),
                   patched_catalog()]
        for patcher in started:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(Path(self.temp.name).resolve())
        self.addCleanup(self.controller.close)

    def test_exactly_one_of_ten_simultaneous_claims_runs(self):
        """The claim is the lock's job, and the nine losers still get to say what their message became."""
        gate = threading.Event()
        runs = []
        busy_told = []

        def operation():
            runs.append(1)
            gate.wait(10)
            return "done"

        def call():
            self.controller.run_job(operation, lambda result: None, "Working",
                                    on_busy=lambda: busy_told.append(1))

        threads = [threading.Thread(target=call) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(runs), 1, "one job per burst of clicks")
        self.assertEqual(len(busy_told), 9, "the nine that were turned away are not silently dropped")
        gate.set()
        self.controller.join(10)
        self.assertFalse(self.controller.busy)

    def test_a_snapshot_never_shows_a_message_half_written(self):
        """A reader that never blocks on the writer must still never see a list shorter than the one
        it read a moment ago, or an entry missing the fields the page indexes by.

        The writer is paced rather than flat-out on purpose: unthrottled, it appends faster than a
        full frozen snapshot can be copied, so the test spends its time measuring lock contention and
        ends up timing out instead of reading anything.
        """
        stop = threading.Event()

        def writer():
            number = 0
            while not stop.is_set():
                number += 1
                self.controller._add("tool", "Tool", f"entry {number}")
                self.controller._note("job", f"entry {number}")
                threading.Event().wait(0.0005)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(10)))
        lengths = []
        for _ in range(80):
            state = self.controller.snapshot()
            messages = state["messages"]
            lengths.append(len(messages))
            for message in messages:
                self.assertTrue({"role", "author", "text", "time"} <= set(message), message)
            threading.Event().wait(0.002)
        self.assertEqual(lengths, sorted(lengths), "a read list went backwards — a torn list")
        self.assertGreater(lengths[-1], lengths[0], "and the writer really was writing")

    def test_a_page_reading_state_cannot_starve_the_answer_a_worker_waits_for(self):
        """The lock is never held across a wait. If it were, this loop is a deadlock: the worker
        holds the state while it waits for the reply, and the reply is delivered by a thread that
        has to read the state to find the request id."""
        answered: dict = {}

        def ask():
            answered["reply"] = self.controller._ask("confirm", {"title": "Keep going?", "message": "m"})

        thread = threading.Thread(target=ask)
        thread.start()
        asks = []
        for _ in range(150):
            asks = self.controller.snapshot()["asks"]       # taken many times while the worker waits
            if asks:
                break
            threading.Event().wait(0.02)
        self.assertTrue(asks, "the question is visible while somebody is blocked on it")
        thread_done = threading.Event()
        threading.Thread(target=lambda: (thread.join(10), thread_done.set()), daemon=True).start()
        self.controller.set_reply(asks[0]["id"], {"ok": True})
        self.assertTrue(thread_done.wait(10), "a reader polling state cannot wedge the worker")
        self.assertEqual(answered.get("reply"), {"ok": True})

    def test_the_snapshot_a_page_holds_is_not_the_state(self):
        """Deep-copied, not merely a new outer dict: a client sorting a list in place, or rewriting
        one message's text, used to change what the next snapshot showed."""
        self.controller._add("tool", "Tool", "a sentence the page did not write")
        before = self.controller.snapshot()
        state = self.controller.snapshot()
        self.assertIsNot(state["messages"], self.controller.messages)
        state["messages"].append({"role": "tool", "author": "Tool", "text": "injected", "time": ""})
        state["messages"][-1]["text"] = "rewritten"
        state["log"].clear()
        fresh = self.controller.snapshot()
        self.assertEqual([message["text"] for message in fresh["messages"]],
                         [message["text"] for message in before["messages"]])
        self.assertNotIn("injected", json.dumps(fresh, ensure_ascii=False))
        self.assertEqual(len(fresh["log"]), len(before["log"]))


if __name__ == "__main__":
    unittest.main()
