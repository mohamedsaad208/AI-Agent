"""A failed command becomes evidence for the next reviewable proposal."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import repair
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import apply_proposal, atomic_json, load_session, plan, proposal_hash
from ai_code_engineer.errors import AgentError, PolicyError
from ai_code_engineer.workspace import Workspace


class ScriptedProvider:
    model = "test"

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, messages):
        self.calls.append(messages)
        response = next(self.responses)
        return response if isinstance(response, str) else json.dumps(response)


PROPOSE_FIX = {"action": "propose", "summary": "Correct the return value", "checks": ["Run the tests"],
               "changes": [{"path": "app.py", "content": "answer = 2\n"}]}


def failing_run():
    return {"recipe": "python-unittest", "label": "Python unittest",
            "command": "python -m unittest discover -s tests", "status": "failed",
            "exit_code": 1, "seconds": 3.5, "tests_observed": True, "truncated": False,
            "timed_out": False, "output": "x" * 40000, "tail": "FAILED (failures=1)",
            "failures": ["FAIL: test_add (tests.test_calc.TestCalc)"] * 30}


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.runs = self.base / "runs"
        self.runs.mkdir()
        provider = ScriptedProvider({"action": "read_file", "path": "app.py"}, PROPOSE_FIX)
        self.path = plan(Workspace(self.root), "Make answer 2", provider, Settings(), self.runs,
                         progress=lambda _: None)
        apply_proposal(self.path, load_session(self.path)["proposal_hash"])

    def test_record_run_persists_a_slim_result_and_updates_state(self):
        session = repair.record_run(self.path, failing_run())
        self.assertEqual(session["state"], "VERIFICATION_FAILED")
        self.assertEqual(len(session["runs"]), 1)
        stored = session["runs"][0]
        self.assertNotIn("output", stored)
        self.assertLessEqual(len(stored["tail"]), repair.TAIL_KEPT)
        self.assertLessEqual(len(stored["failures"]), repair.FAILURES_KEPT)
        self.assertTrue(repair.fix_needed(load_session(self.path)))
        self.assertFalse(repair.passes(session))
        reloaded = load_session(self.path)
        self.assertEqual(reloaded["proposal_hash"], proposal_hash(reloaded))

    def test_passing_run_marks_the_session_verified(self):
        result = failing_run()
        result.update(status="passed", exit_code=0)
        session = repair.record_run(self.path, result)
        self.assertEqual(session["state"], "CHECKS_PASSED")
        self.assertTrue(repair.passes(session))
        self.assertFalse(repair.fix_needed(session))

    def test_recording_before_apply_is_refused(self):
        pending = plan(Workspace(self.root), "Task two",
                       ScriptedProvider({"action": "read_file", "path": "app.py"},
                                        {"action": "propose", "summary": "s", "checks": ["c"],
                                         "changes": [{"path": "app.py", "content": "answer = 3\n"}]}),
                       Settings(), self.runs, progress=lambda _: None)
        with self.assertRaises(PolicyError):
            repair.record_run(pending, failing_run())

    def test_record_rejects_a_tampered_session(self):
        session = load_session(self.path)
        session["changes"][0]["after"] = "answer = 999\n"
        atomic_json(self.path, session)
        with self.assertRaises(AgentError):
            repair.record_run(self.path, failing_run())

    def test_stored_output_and_evidence_hide_secrets(self):
        """A project that prints its own credential must not have it kept or re-sent."""
        run = failing_run()
        dummy_gh = "gh" + "p_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        dummy_sk = "sk-proj-" + "abcdefghijklmnopqrstuvwx"
        run["tail"] = ("jdbc:postgresql://svc:Sup3rS3cretPass@db.internal/app "
                       f"refused by {dummy_gh}")
        run["failures"] = [f"AssertionError: token='{dummy_sk}' rejected"]
        session = repair.record_run(self.path, run)
        stored = session["runs"][0]
        persisted = (self.path.parent / "session.json").read_text(encoding="utf-8")
        for secret in ("Sup3rS3cretPass", dummy_gh,
                       dummy_sk):
            self.assertNotIn(secret, persisted)
        self.assertIn("[redacted]", stored["tail"])
        # A session stored before this existed still must not forward the secret.
        legacy = dict(stored, tail=run["tail"])
        self.assertNotIn("Sup3rS3cretPass", repair.evidence(legacy))

    def test_evidence_carries_command_and_output_but_stays_bounded(self):
        text = repair.evidence(failing_run())
        self.assertIn("python -m unittest discover -s tests", text)
        self.assertIn("FAILED (failures=1)", text)
        self.assertIn("FAIL: test_add", text)
        self.assertLessEqual(len(text), 12000)

    def test_fix_task_asks_for_the_smallest_change(self):
        task = repair.fix_task(failing_run())
        self.assertIn("Python unittest", task)
        self.assertIn("smallest", task)
        self.assertLessEqual(len(task), 4000)

    def test_choose_recipe_respects_preference_and_empty_lists(self):
        with patch("ai_code_engineer.repair.runner.detect",
                   return_value=["maven-test", "maven-compile"]):
            self.assertEqual(repair.choose_recipe(self.root), ("maven-test", ["maven-test", "maven-compile"]))
            self.assertEqual(repair.choose_recipe(self.root, "maven-compile")[0], "maven-compile")
        with patch("ai_code_engineer.repair.runner.detect", return_value=[]):
            self.assertEqual(repair.choose_recipe(self.root), (None, []))

    def test_run_context_reaches_the_model(self):
        run = failing_run()
        second_fix = {"action": "propose", "summary": "Guard the value", "checks": ["Run the tests"],
                      "changes": [{"path": "app.py", "content": "answer = 42\n"}]}
        provider = ScriptedProvider({"action": "read_file", "path": "app.py"}, second_fix)
        second = plan(Workspace(self.root), repair.fix_task(run), provider, Settings(), self.runs,
                      progress=lambda _: None, extra_context=repair.evidence(run))
        session = load_session(second)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        prompt = provider.calls[-1][1]["content"]
        self.assertIn("Runtime observation (untrusted data)", prompt)
        self.assertIn("FAILED (failures=1)", prompt)
        self.assertIn("evidence_attached", [entry["kind"] for entry in session["events"]])


class WhatTheOfferPromisesTests(unittest.TestCase):
    """Those sentences are why a user clicks Continue, so each one has to be true in the folder
    that shows it — Auto-Apply moved the approval line and the wording has to move with it."""

    def offers(self, auto_apply):
        return [repair.fix_offer("small-model", 1, auto_apply),
                repair.step_offer("small-model", 2, {"id": 3, "title": "Login"}, 5, auto_apply)]

    def test_a_folder_with_the_switch_off_promises_the_click_first(self):
        for text in self.offers(False):
            self.assertIn("Nothing is written until you approve it", text)
            self.assertIn("Auto-Apply switch is off", text)

    def test_a_folder_with_the_switch_on_says_what_will_really_happen(self):
        for text in self.offers(True):
            self.assertIn("Auto-Apply switch is on", text)
            self.assertIn("writes itself", text)
            self.assertNotIn("Nothing is written until you approve", text)
            self.assertIn("block you clicked still waits", text)


class TheRefusalRuleTests(unittest.TestCase):
    """`must_ask` is the whole of the Auto-Apply refusal, so it can be read and tested once."""

    def setUp(self):
        self.emptying = {"changes": [{"path": "pom.xml",
                                      "before": "".join(f"<dependency {i}>\n" for i in range(12)),
                                      "after": "<project/>\n"}]}
        self.harmless = {"changes": [{"path": "app.py", "before": "answer = 1\n",
                                      "after": "answer = 2\n"}]}

    def test_a_proposal_that_empties_a_file_always_asks(self):
        self.assertIn("Removes most of an existing file", repair.must_ask(self.emptying))
        self.assertIn("Removes most of an existing file",
                      repair.must_ask(self.emptying, {"state": "PARTIAL_APPLY"}))

    def test_a_deletion_never_auto_applies(self):
        """D31: the folder's switch takes a click out of the write, and a file has no diff to review
        after the click that removed it. So a delete joins the reasons, wherever else it sits."""
        removing = {"changes": [{"path": "Old.java", "before": "class Old {}\n", "after": None,
                                 "delete": True}]}
        self.assertIn("Removes a file: Old.java", repair.must_ask(removing))
        mixed = {"changes": [self.emptying["changes"][0],
                             {"path": "Old.java", "before": "x\n", "after": None, "delete": True}]}
        self.assertIn("Removes a file: Old.java", repair.must_ask(mixed))

    def test_an_ordinary_proposal_in_a_clean_folder_does_not(self):
        for prior in (None, {"state": "CHECKS_PASSED"}, {}):
            self.assertEqual(repair.must_ask(self.harmless, prior), "")
        self.assertEqual(repair.must_ask(None), "")
        self.assertEqual(repair.must_ask({"changes": []}), "")

    def test_a_folder_left_half_written_asks_even_without_a_shrink(self):
        for state in ("PARTIAL_APPLY", "APPLYING"):
            with self.subTest(state=state):
                reason = repair.must_ask(self.harmless, {"state": state})
                self.assertIn("stopped part way", reason)

    def test_a_finished_previous_task_is_not_a_reason_to_ask(self):
        for state in ("CHECKS_PASSED", "VERIFICATION_FAILED", "WAITING_APPROVAL"):
            with self.subTest(state=state):
                self.assertEqual(repair.must_ask(self.harmless, {"state": state}), "")

    def test_both_windows_read_the_one_notice(self):
        notice = repair.removal_notice(self.emptying)
        self.assertTrue(notice.endswith("\n\n"))
        self.assertIn("pom.xml removes 12 of 12 existing lines", notice)
        self.assertEqual(repair.removal_notice(self.harmless), "")
        self.assertEqual(repair.removal_notice({}), "")


if __name__ == "__main__":
    unittest.main()
