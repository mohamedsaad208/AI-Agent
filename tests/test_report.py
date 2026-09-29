"""The session report: every claim it makes has to be in the record, and every secret is out.

A report is the one artefact this tool produces that leaves the machine — pasted into an issue,
attached to a PR, kept for an audit — so these tests are mostly about what it must NOT do: print a
credential a build log echoed, describe an unverified run as a pass, or infer a cause the session
never recorded.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer.engine import atomic_json, proposal_hash
from ai_code_engineer.errors import AgentError
from ai_code_engineer.report import export, export_file, find_session, reads, refusals, summary
from doubles import CALCULATOR_BAD, CALCULATOR_GOOD

SECRET = "sk-proj-a-key-the-build-tool-echoed-0123456789"


def session(**over) -> dict:
    """One session as `.agent-runs/<id>/session.json` actually stores it."""
    data = {
        "schema": 1, "id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
        "root": "D:/work/demo2", "task": "Fix the add function in calculator.py",
        "state": "CHECKS_PASSED", "created": "2026-09-28T10:00:00+00:00", "model": "qwen2.5-coder:1.5b",
        "chat_id": "0123456789abcdef0123456789abcdef",
        "summary": "Make add return a + b", "checks": ["Run the addition tests"],
        "changes": [{"path": "calculator.py", "before": CALCULATOR_BAD, "after": CALCULATOR_GOOD,
                     "before_hash": "1" * 64, "after_hash": "2" * 64}],
        "proposal_hash": "3" * 64,
        "events": [
            {"at": "2026-09-28T10:00:00+00:00", "kind": "tool", "name": "list_files", "count": 4},
            {"at": "2026-09-28T10:00:03+00:00", "kind": "tool", "name": "read_file",
             "path": "calculator.py", "sha256": "1" * 64},
            {"at": "2026-09-28T10:00:04+00:00", "kind": "file_not_found", "path": "tests/test_add.py",
             "can_create": True},
            {"at": "2026-09-28T10:00:06+00:00", "kind": "rejected_action",
             "reason": "Model asked to run a command; this tool does not execute tool commands"},
            {"at": "2026-09-28T10:00:09+00:00", "kind": "proposal", "hash": "3" * 64},
            {"at": "2026-09-28T10:00:20+00:00", "kind": "approved", "hash": "3" * 64},
            {"at": "2026-09-28T10:00:21+00:00", "kind": "written", "path": "calculator.py",
             "sha256": "2" * 64},
            {"at": "2026-09-28T10:00:40+00:00", "kind": "run", "recipe": "python-unittest",
             "status": "passed", "exit_code": 0, "seconds": 1.4},
        ],
        "runs": [{"recipe": "python-unittest", "label": "Python unittest",
                  "command": "python -m unittest -q", "status": "passed", "exit_code": 0,
                  "seconds": 1.4, "tests_observed": True, "truncated": False, "timed_out": False,
                  "proof": {"tests": 4, "failures": 0, "errors": 0, "skipped": 0,
                            "source": "JUnit XML"},
                  "failures": [], "tail": "Ran 4 tests\nOK\n"}],
    }
    data.update(over)
    return data


class TheNumbers(unittest.TestCase):
    def test_the_report_says_who_asked_what_and_with_which_model(self):
        data = summary(session())
        self.assertEqual(data["request"], "Fix the add function in calculator.py")
        self.assertEqual(data["model"], "qwen2.5-coder:1.5b")
        self.assertEqual(data["state_label"], "Selected checks passed")
        self.assertEqual(data["proposal"]["hash"], "3" * 64)

    def test_files_read_separate_the_model_call_from_the_tools_own(self):
        data = summary(session())
        self.assertEqual([row["path"] for row in data["reads"]["by_model"]], ["calculator.py"])
        self.assertEqual(data["reads"]["distinct_files"], 1)

    def test_a_run_is_reported_with_the_count_that_made_it_a_pass(self):
        data = summary(session())["runs"][0]
        self.assertEqual((data["tests"], data["failures"], data["proof_source"]),
                         (4, 0, "JUnit XML"))

    def test_a_passing_run_is_not_given_a_diagnosis(self):
        """The kind of failure is the loop's own reading, and a run that passed has none — inventing
        "unknown" here would read as "the tool could not tell what went wrong"."""
        self.assertEqual(summary(session())["runs"][0]["folder"], "")
        self.assertEqual(summary(session())["runs"][0]["looks_like"], "")

    def test_the_timeline_carries_the_gap_between_two_events(self):
        rows = [row for row in summary(session())["timeline"] if row["kind"] == "written"]
        self.assertEqual(rows[0]["seconds_since_previous"], 1.0)

    def test_every_declined_step_is_a_row(self):
        rows = refusals(session())
        self.assertEqual({row["what"] for row in rows},
                         {"read tests/test_add.py", "action"},
                         "a clean-looking session is not a session that never stumbled")

    def test_an_interrupted_task_says_why_it_stopped(self):
        data = summary(session(state="BLOCKED", error="Ollama stopped answering",
                               events=[{"at": "2026-09-28T10:00:01+00:00", "kind": "stopped",
                                        "reason": "connection refused"}]))
        self.assertIn("connection refused", data["refusals"][0]["why"])
        self.assertEqual(data["error"], "Ollama stopped answering")


class WhatThePageSays(unittest.TestCase):
    def render(self, **over):
        return export(session(**over), "markdown")

    def test_no_command_run_is_called_out_instead_of_left_implied(self):
        page = self.render(runs=[], state="APPLIED_UNVERIFIED")
        self.assertIn("No project command was run", page)
        self.assertIn("Verification is incomplete", page)

    def test_success_without_evidence_is_not_printed_as_a_pass(self):
        page = self.render(runs=[{"recipe": "maven-test", "label": "Maven test",
                                  "command": "mvn -B test", "status": "passed", "exit_code": 0,
                                  "seconds": 30.0, "proof": None, "failures": [], "tail": "BUILD SUCCESS",
                                  "tests_observed": True}])
        self.assertIn("no test evidence", page)
        self.assertIn("not a pass", page)

    def test_a_timeout_is_a_timeout(self):
        page = self.render(runs=[{"recipe": "cargo-test", "label": "Cargo test", "command": "cargo test",
                                  "status": "timeout", "exit_code": None, "seconds": 600.0, "proof": None,
                                  "failures": [], "tail": "", "timed_out": True, "truncated": True}])
        self.assertIn("Timed out", page)
        self.assertIn("not all the command printed", page)

    def test_a_failure_carries_the_same_reading_the_loop_acted_on(self):
        """The report and the repair loop must not tell two stories about one run: this is the same
        `classify`, so a page that says "assertion" is a round that was offered as "assertion"."""
        page = self.render(runs=[{"recipe": "maven-test", "label": "Maven test",
                                  "command": "mvn -B test", "status": "failed", "exit_code": 1,
                                  "seconds": 30.0, "proof": {"tests": 12, "failures": 2, "errors": 0,
                                                              "source": "surefire XML"},
                                  "failures": ["AssertionError: expected 5 but was 4"],
                                  "tail": "[ERROR] Tests run: 12, Failures: 2\nBUILD FAILURE"}])
        self.assertIn("What the output looks like: assertion", page)
        self.assertEqual(summary(session(runs=[{"status": "failed", "exit_code": 1, "seconds": 30.0,
                                                 "proof": None, "failures": ["SyntaxError: nope"],
                                                 "tail": "SyntaxError: nope"}]))["runs"][0]["looks_like"],
                         "syntax")

    def test_a_passing_run_gets_no_diagnosis_line_at_all(self):
        self.assertNotIn("What the output looks like", self.render())

    def test_a_run_in_one_module_of_many_says_which(self):
        """A nine-module reactor needs the folder in the heading: "Maven test: passed" twelve times
        over is not an audit of twelve builds."""
        module = {"recipe": "maven-test", "label": "Maven test", "command": "mvn -B test",
                  "status": "failed", "exit_code": 1, "seconds": 30.0,
                  "target": "backend/auth-service", "proof": None,
                  "failures": ["AssertionError: 3 != 4"], "tail": "BUILD FAILURE"}
        page = self.render(runs=[module])
        self.assertIn("`Maven test` in `backend/auth-service` · failed", page)
        self.assertEqual(summary(session(runs=[module]))["runs"][0]["folder"],
                         "backend/auth-service")
        self.assertEqual(summary(session())["runs"][0]["folder"], "",
                         "a single-project folder gains no module column")

    def test_the_request_is_verbatim_and_in_a_block_of_its_own(self):
        task = "Fix add.\n\nThen run:\n  mvn -B test"
        page = self.render(task=task)
        self.assertIn("````text\nFix add.\n\nThen run:\n  mvn -B test\n````", page)

    def test_rollback_and_removal_are_visible_as_themselves(self):
        page = self.render(events=[{"at": "2026-09-28T10:00:00+00:00", "kind": "removed",
                                    "path": "old.py", "sha256": "9" * 64},
                                   {"at": "2026-09-28T10:00:01+00:00", "kind": "rolled_back_file",
                                    "path": "old.py"}])
        self.assertIn("`old.py`", page)
        self.assertIn("Rolled back", page)


class SecretsDoNotTravel(unittest.TestCase):
    def test_a_key_in_the_captured_output_is_redacted_in_both_formats(self):
        dirty = session(runs=[{"recipe": "python-unittest", "label": "Python unittest",
                               "command": "python -m unittest", "status": "failed", "exit_code": 1,
                               "seconds": 2.0, "proof": None, "tests_observed": False,
                               "failures": ["AssertionError: env leaked " + SECRET],
                               "tail": "setting token to " + SECRET}],
                        events=[{"at": "2026-09-28T10:00:00+00:00", "kind": "rejected_action",
                                 "reason": "bad: " + SECRET}])
        for fmt in ("markdown", "json"):
            with self.subTest(fmt=fmt):
                text = export(dirty, fmt)
                self.assertNotIn(SECRET, text)
                self.assertIn("[redacted]", text)

    def test_a_control_character_from_a_build_log_cannot_reach_the_page(self):
        page = export(session(summary="done\x1b[2J\x07"), "markdown")
        self.assertNotIn("\x1b", page)
        self.assertNotIn("\x07", page)


class FindingASession(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runs = Path(self.temp.name)
        for identifier in ("a1b2c3d4e5f60718293a4b5c6d7e8f90",
                           "a1b2ffffe5f60718293a4b5c6d7e8f90"):
            item = session(id=identifier)
            # `load_session` re-computes the proposal hash and refuses a mismatch, so the fixture has
            # to carry a real one — which also means the export path is exercised against the same
            # integrity rule the windows enforce.
            item["proposal_hash"] = proposal_hash(item)
            atomic_json(self.runs / identifier / "session.json", item)

    def test_the_id_is_a_directory_name_not_a_column_to_search(self):
        found = find_session(self.runs, "a1b2c3d4e5f60718293a4b5c6d7e8f90")
        self.assertEqual(found.name, "session.json")
        self.assertEqual(found.parent.name, "a1b2c3d4e5f60718293a4b5c6d7e8f90")

    def test_a_unique_prefix_works_and_an_ambiguous_one_refuses_to_guess(self):
        self.assertEqual(find_session(self.runs, "a1b2c3").parent.name.startswith("a1b2c3"), True)
        with self.assertRaises(AgentError) as caught:
            find_session(self.runs, "a1b2")
        self.assertIn("2 sessions", str(caught.exception))

    def test_a_path_to_the_folder_or_the_file_is_accepted(self):
        folder = self.runs / "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        self.assertEqual(find_session(self.runs, str(folder)), folder / "session.json")
        self.assertEqual(find_session(self.runs, str(folder / "session.json")), folder / "session.json")

    def test_a_missing_id_says_where_it_lookeched(self):
        with self.assertRaises(AgentError):
            find_session(self.runs, "ffffffff")

    def test_the_file_export_is_the_same_report_as_the_string(self):
        path = find_session(self.runs, "a1b2c3")
        from_file = json.loads(export_file(path, "json"))
        self.assertEqual(from_file["id"], "a1b2c3d4e5f60718293a4b5c6d7e8f90")
        self.assertEqual(from_file["runs"][0]["tests"], 4)
        self.assertIn("Fix the add function", export_file(path, "markdown"))

    def test_an_unknown_format_is_refused_rather_than_defaulting(self):
        with self.assertRaises(AgentError):
            export(session(), "pdf")


    def test_an_unexplained_stop_says_that_rather_than_printing_a_blank(self):
        rows = refusals(session(events=[{"at": "2026-09-28T10:00:00+00:00", "kind": "stopped"}]))
        self.assertEqual(rows[0]["why"], "the record does not say why it stopped")


if __name__ == "__main__":
    unittest.main()
