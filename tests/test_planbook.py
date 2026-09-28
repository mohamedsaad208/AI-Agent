"""The ledger is the only thing that decides whether a plan step is finished."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import planbook
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.workspace import Workspace

PLAN = """# Delivery plan

## Phase 1: scaffold
Create the application and its manifest.

## Phase 2: login endpoint
Add the login route and its tests.

## Phase 3: token filter
Validate the token on every request.
"""


def session_with(runs, state="CHECKS_PASSED", run_id="a" * 32):
    return {"id": run_id, "state": state, "created": "2026-09-24T00:00:00+00:00", "runs": runs}


PASSED_PROOF = [{"recipe": "maven-test", "status": "passed", "exit_code": 0,
                 "proof": {"tests": 8, "failures": 0, "errors": 0, "skipped": 0,
                           "source": "surefire XML"}}]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "plan.md").write_text(PLAN, encoding="utf-8")
        self.plans = root / "plans"
        self.ws = Workspace(self.repo)

    def open(self, plan_file="plan.md"):
        return planbook.open_book(self.plans, self.ws, plan_file)

    def test_numbered_headings_become_ordered_steps(self):
        steps = planbook.parse_steps(PLAN)
        self.assertEqual([row["title"] for row in steps],
                         ["scaffold", "login endpoint", "token filter"])
        self.assertIn("login route", planbook.parse_steps(PLAN)[1]["body"])

    def test_a_plan_without_headings_is_one_step(self):
        steps = planbook.parse_steps("Fix the rounding error in totals.\n")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["id"], 1)
        with self.assertRaises(PolicyError):
            planbook.parse_steps("   ")

    def test_the_ledger_survives_reopening_the_same_plan(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "s" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="s" * 32))
        _, again = self.open()
        self.assertEqual(again["steps"][0]["status"], "verified")
        self.assertEqual(planbook.current(again)["id"], 2)

    def test_editing_the_plan_starts_a_fresh_ledger(self):
        first, _ = self.open()
        (self.repo / "plan.md").write_text(PLAN + "\n## Phase 4: logout\nDrop the session.\n",
                                          encoding="utf-8")
        second, book = self.open()
        self.assertNotEqual(first, second)
        self.assertEqual(len(book["steps"]), 4)
        self.assertFalse(first.exists())

    def test_a_verified_step_does_not_cross_into_another_folder_of_the_same_name(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "s" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="s" * 32))
        twin = self.repo.parent / "elsewhere" / self.repo.name        # same name, other folder
        twin.mkdir(parents=True)
        (twin / "plan.md").write_text(PLAN, encoding="utf-8")
        twin_path, twin_book = planbook.open_book(self.plans, Workspace(twin), "plan.md")
        self.assertNotEqual(twin_path, path, "one ledger cannot serve two folders")
        self.assertEqual([row["status"] for row in twin_book["steps"]], ["pending"] * 3)

    def test_a_ledger_that_names_another_folder_is_not_read_as_this_one(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "s" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="s" * 32))
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["root"] = str(self.repo.parent / "moved-away")
        path.write_text(json.dumps(stored), encoding="utf-8")
        _, reread = self.open()
        self.assertEqual([row["status"] for row in reread["steps"]], ["pending"] * 3,
                         "a ledger whose root disagrees restarts instead of being trusted")

    def test_a_step_opens_only_for_the_session_recorded_against_it(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        with self.assertRaises(PolicyError):
            planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="2" * 32))
        self.assertEqual(planbook.step(book, 1)["status"], "in_progress")

    def test_a_fix_round_can_finish_the_step_it_was_rebound_to(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.record_session(path, book, 1, "2" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="2" * 32))
        self.assertEqual(planbook.step(book, 1)["status"], "verified")

    def test_green_exit_without_a_test_report_proves_nothing(self):
        cases = {
            "the session is not in CHECKS_PASSED": session_with(PASSED_PROOF, "APPLIED_UNVERIFIED"),
            "no command has been recorded for this session": session_with([]),
            "the last command did not pass": session_with([{"status": "failed", "recipe": "maven-test"}]),
            "the test report shows failures or errors": session_with(
                [{"status": "passed", "recipe": "maven-test",
                  "proof": {"tests": 8, "failures": 2, "errors": 0, "skipped": 0}}]),
            "the test report contains no executed test": session_with(
                [{"status": "passed", "recipe": "maven-test",
                  "proof": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}}]),
        }
        for reason, session in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(planbook.proof_reason(session), reason)

    def test_a_compile_only_run_proves_the_build_without_tests(self):
        session = session_with([{"recipe": "maven-compile", "status": "passed", "tests_observed": False}])
        self.assertEqual(planbook.proof_reason(session), "")

    def test_a_refused_completion_leaves_the_step_open(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        with self.assertRaises(PolicyError):
            planbook.complete(path, book, 1, session_with([{"status": "unverified", "recipe": "maven-test"}],
                                                          run_id="1" * 32))
        self.assertEqual(planbook.step(book, 1)["status"], "in_progress")
        self.assertEqual(json.loads(path.read_text())["steps"][0]["status"], "in_progress")

    def test_the_next_task_names_finished_work_and_keeps_the_note(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="1" * 32))
        row = planbook.current(book)
        task = planbook.task_for(book, row, note="keep the endpoint path unchanged")
        self.assertIn("Implement step 2 of 3", task)
        self.assertIn("do not redo", task)
        self.assertIn("scaffold", task)
        self.assertIn("keep the endpoint path unchanged", task)
        self.assertNotIn("Create the application and its manifest", task)
        self.assertLessEqual(len(task), 4000)

    def test_progress_line_and_rollback_reopen(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="1" * 32))
        self.assertIn("Plan step 2/3", planbook.progress_line(book))
        planbook.reopen(path, book, 1)
        self.assertEqual(planbook.step(book, 1)["status"], "in_progress")
        self.assertIsNone(planbook.step(book, 1)["verified_at"])
        self.assertIn("Plan step 1/3", planbook.progress_line(book))

    def test_the_ledger_is_keyed_by_the_plan_not_its_path_spelling(self):
        by_name, first = self.open("plan.md")
        by_path, second = self.open(str(self.repo / "plan.md"))
        self.assertEqual(by_name, by_path)
        self.assertEqual(first["steps"][1]["title"], second["steps"][1]["title"])
        with self.assertRaises(PolicyError):
            self.open(str(self.plans / "elsewhere.md"))

    def test_a_green_run_without_headings_is_one_verified_step(self):
        (self.repo / "flat.md").write_text("Fix the rounding error in totals.\n", encoding="utf-8")
        path, book = self.open("flat.md")
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="1" * 32))
        self.assertIn("Plan complete", planbook.progress_line(book))

    def test_an_unknown_step_is_refused(self):
        path, book = self.open()
        with self.assertRaises(PolicyError):
            planbook.record_session(path, book, 99, "1" * 32)
        with self.assertRaises(PolicyError):
            planbook.complete(path, book, 99, session_with(PASSED_PROOF))

    def test_ledgers_are_outside_the_project_and_keyed_by_its_name(self):
        path, _ = self.open()
        planbook.atomic_json(path, {"schema": 1})
        self.assertTrue(path.is_relative_to(self.plans))
        self.assertEqual(path.name.split("-")[0], "repo")
        self.assertNotIn(self.repo, path.parents)

    def test_a_ledger_written_by_an_older_version_is_replaced(self):
        path, book = self.open()
        planbook.atomic_json(path, {"schema": 99, "steps": []})
        _, reloaded = self.open()
        self.assertEqual(len(reloaded["steps"]), 3)
        self.assertEqual(reloaded["schema"], 1)
        planbook.record_session(path, reloaded, 1, "1" * 32)
        self.assertEqual(json.loads(path.read_text())["steps"][0]["status"], "in_progress")


class StepLimitTests(unittest.TestCase):
    def test_a_long_plan_stops_at_the_step_limit(self):
        text = "\n".join(f"## Phase {n}: step {n}\nbody {n}" for n in range(1, 30))
        self.assertEqual(len(planbook.parse_steps(text)), planbook.MAX_STEPS)


if __name__ == "__main__":
    unittest.main()
