"""The record of what the agent did: the feed, the step rows, a reopened task's rows."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way
from ai_code_engineer import labels, memory, runner
from ai_code_engineer.engine import atomic_json, load_session
from ai_code_engineer.webapp.controller import MAX_LOG_ENTRIES
from ai_code_engineer.webapp import runresults
from doubles import OLLAMA_ENTRY, PLAN, run_result
from helpers import sandbox_repo
from controller_case import Scripted, SteppingModel, patched_catalog


class TheActivityFeed(unittest.TestCase):
    """UI 3.4: what the agent did reaches the chat, and the line that says it survives a push.

    The class of defect this holds against is a sentence that exists only in the server's own
    memory: `controller.status` was assigned in about sixty places and read by no client, so the
    progress line went into the header subtitle instead and the next state push erased it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.model = SteppingModel()
        started = [patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                   patched_catalog()]
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
            "\U0001f3af Chose 1 file(s) for this task: calculator.py (the task names this file)",
            "\U0001f4d6 Reading file: calculator.py",
            "\U0001f50d Searching code: def add",
            "\u270d\ufe0f Proposed changes for 1 file(s): calculator.py"],
            "a repeated read of the same file must collapse into one line")

    def test_the_step_language_follows_the_task_not_the_window(self):
        self.controller.start_plan("عدّل دالة الجمع في calculator.py")
        self.controller.join()
        said = self.steps()
        self.assertTrue(said, "the Arabic task announced nothing")
        self.assertIn("قراءة الملف", " ".join(said))
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

    def test_the_checks_card_keeps_a_proposal_from_reading_as_a_result(self):
        """The one line between "the model said it would check" and "a command answered".

        Nothing asserted this text before it moved into `runresults`, and it is the promise the
        README makes about the repository map: a proposed check is not proof of anything.
        """
        lines = runresults.checks_lines({"checks": ["run pytest"]})
        self.assertEqual(lines[0], "Proposed checks (not execution results):")
        self.assertIn("• run pytest", lines)
        self.assertEqual([row for row in lines if "Latest check" in row], [])
        self.assertEqual([row for row in lines if "Command runs" in row], [])
        checked = runresults.checks_lines({"checks": [], "runs": [{"command": "pytest", "status":
                                                                   "failed", "exit_code": 1,
                                                                   "seconds": 3.2,
                                                                   "failures": ["boom"]}]})
        self.assertTrue(any("last was pytest" in row for row in checked), checked)
        self.assertTrue(any("exit 1" in row for row in checked), checked)
        self.assertTrue(any(row.strip() == "boom" for row in checked), checked)

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
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.model = SteppingModel()
        for patcher in (patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                        patched_catalog()):
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
        self.assertEqual([row["action"] for row in rows],
                         ["context_files", "read_file", "search_code", "propose"])
        self.assertEqual(len({row["id"] for row in rows}), len(rows), "two rows share a handle")
        self.assertTrue(all("detail" in row for row in rows))
        by_action = {row["action"]: row for row in rows}
        self.assertTrue(by_action["read_file"]["detail"],
                        "the read row knows which version of the file it saw")
        self.assertTrue(by_action["propose"]["detail"], "a proposal always has its file list behind it")
        self.assertFalse(by_action["context_files"]["detail"],
                         "the choice line is the tool's own sentence, with nothing stored behind it")

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
class TheReplayedActivityLog(unittest.TestCase):
    """UI 4.6: a reopened task's Activity rows are sentences, not the engine's notebook.

    `display_session` rebuilt each row by joining the stored record's own fields, so the list an
    operator came back to read said `tool name=read_file path=pom.xml sha256=9f3c2…`. Worse, a
    `plan_attached` record carries the plan's whole body in `content`, so one status row printed a
    file's worth of text. The rows now go through `labels.log_line`, in the language the task was
    asked in — the same reason the step rows already had (`_history_steps`).
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.model = SteppingModel()
        for patcher in (patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model),
                        patched_catalog()):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)

    def reopen(self, task="Fix add in calculator.py", events=()):
        self.controller.start_plan(task)
        self.controller.join()
        self.controller.session["events"].extend(events)
        # The file is what `display_session` reads, so a test that only edited memory would prove nothing.
        atomic_json(self.controller.session_path, self.controller.session)
        self.controller.display_session(self.controller.session_path, select=True)
        return self.controller.snapshot()["log"]

    def test_a_reopened_task_says_what_happened_instead_of_dumping_its_fields(self):
        rows = self.reopen(events=[
            {"at": "2026-09-28T10:00:01", "kind": "tool", "name": "read_file",
             "path": "pom.xml", "sha256": "9f3c2a1b"},
            {"at": "2026-09-28T10:00:02", "kind": "written", "path": "src/a.py",
             "sha256": "aa11bb22"}])
        texts = [row["text"] for row in rows]
        joined = "\n".join(texts)
        self.assertNotIn("sha256=", joined, "an audit field is not a sentence")
        self.assertNotIn("name=read_file", joined)
        self.assertIn("\U0001f4d6 Read pom.xml", texts)
        self.assertIn("\U0001f4be Wrote src/a.py", texts)

    def test_a_plan_row_names_the_file_and_not_its_whole_body(self):
        body = "Create the package. Reject duplicate emails."
        joined = "\n".join(row["text"] for row in self.reopen(events=[
            {"at": "2026-09-28T10:00:03", "kind": "plan_attached",
             "path": "docs/PLAN.md", "content": body}]))
        self.assertNotIn(body, joined, "the plan body belongs in the prompt, not in a status row")
        self.assertIn("docs/PLAN.md", joined)

    def test_a_kind_no_window_has_met_is_still_said_by_name(self):
        rows = self.reopen(events=[{"at": "2026-09-28T10:00:04", "kind": "seismograph", "depth": 3}])
        text = next(row["text"] for row in rows if row["kind"] == "seismograph")
        self.assertEqual(text, "\u2699\ufe0f seismograph",
                         "a row that comes out blank hides a whole phase of a run")
        self.assertNotIn("depth", text)

    def test_the_replay_keeps_the_language_the_task_was_asked_in(self):
        rows = self.reopen(task="\u0635\u0644\u062d \u062f\u0627\u0644\u0629 add \u0641\u064a calculator.py",
                           events=[{"at": "2026-09-28T10:00:05", "kind": "rolled_back"}])
        text = next(row["text"] for row in rows if row["kind"] == "rolled_back")
        self.assertTrue(labels.is_arabic(text), repr(text))


    def test_a_reopened_task_marks_its_records_as_records(self):
        """The client sorts Activity by this flag: a stored record goes into the block under the list,
        a sentence the window wrote stays in the list. An unmarked replay row would read as a milestone
        that happened twice."""
        rows = self.reopen(events=[{"at": "2026-09-28T10:00:06", "kind": "proposal_rejected",
                                    "hash": "a" * 64}])
        self.assertTrue(all(row.get("audit") for row in rows), repr(rows[-1]))
        self.controller._note("progress", "still working")
        self.assertNotIn("audit", self.controller.snapshot()["log"][-1])


if __name__ == "__main__":
    unittest.main()
