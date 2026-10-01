"""The goal tree: what a plan carries, who may write it, and what happens when the model refuses.

`planbook` owns the ledger, and the tree is the part of it a model writes. The rules this file holds
are the ones that keep that safe: the parsed steps stay the plan's own, a plan that already has a goal
is never re-asked, and a refusal leaves the plan running with a said reason instead of a silent hole.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import planbook
from ai_code_engineer.errors import AgentError, PolicyError
from ai_code_engineer.workspace import Workspace

PLAN = """# Delivery plan

## Phase 1: scaffold
Create the application and its manifest.

## Phase 2: login endpoint
Add the login route and its tests.

## Phase 3: token filter
Validate the token on every request.
"""

TREE = {"goal": "The service signs a user in and rejects a bad password.",
        "criteria": ["Login returns a token for a known user",
                     "Login returns 401 for a bad password"],
        "sub_goals": [{"id": 1, "title": "Auth API"}, {"id": 2, "title": "Request filter"}],
        "steps": [{"id": 1, "accepts": [1, 2]},
                  {"id": 2, "accepts": [1], "sub_goal": 1},
                  {"id": 3, "accepts": [2], "sub_goal": 2}]}


class Provider:
    """A provider that answers the goal request with what the test loaded into it."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def generate(self, messages, cancelled=None):
        self.requests.append(list(messages))
        item = self.replies.pop(0) if self.replies else json.dumps(TREE)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


class GoalTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "plan.md").write_text(PLAN, encoding="utf-8")
        self.plans = root / "plans"
        self.ws = Workspace(self.repo)
        self.path, self.book = planbook.open_book(self.plans, self.ws, "plan.md")

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def author(self, provider, **fields):
        return planbook.author_goal(self.path, self.book, provider, **fields)

    def test_a_written_tree_upgrades_the_ledger_and_persists(self):
        book, refusal = self.author(Provider(), task="finish the login work", plan_text=PLAN)
        self.assertEqual(refusal, "")
        self.assertEqual(book["schema"], 2)
        self.assertEqual(book["goal"], TREE["goal"])
        self.assertEqual(book["criteria"], TREE["criteria"])
        self.assertEqual(book["steps"][2]["accepts"], [2])
        self.assertEqual(book["steps"][2]["sub_goal"], 2)
        self.assertEqual(self.stored()["goal"], TREE["goal"], "the tree is on disk, not only in memory")

    def test_a_plan_that_already_carries_a_goal_is_never_asked_again(self):
        provider = Provider()
        book, _ = self.author(provider)
        before = len(provider.requests)
        again, refusal = self.author(provider)
        self.assertEqual(len(provider.requests), before, "no second model call")
        self.assertEqual(refusal, "")
        self.assertEqual(again["goal"], book["goal"])

    def test_a_refused_tree_is_answered_once_more_with_the_field_that_broke(self):
        provider = Provider({"goal": "Only a goal"}, TREE)
        book, refusal = self.author(provider)
        self.assertEqual(len(provider.requests), 2)
        echoed = provider.requests[1][-1]["content"]
        self.assertIn("goal and criteria", echoed, "the model is told which field it got wrong")
        self.assertEqual(book["goal"], TREE["goal"], "the corrected answer is the one that lands")
        self.assertEqual(self.stored()["goal"], TREE["goal"])

    def test_two_refusals_leave_the_plan_goal_less_and_say_why(self):
        provider = Provider({"goal": "Only a goal"}, {"nope": 1})
        book, refusal = self.author(provider)
        self.assertFalse(planbook.has_goal(book))
        self.assertIn("goal and criteria", refusal)
        self.assertFalse(self.path.exists() and "goal" in self.stored(),
                         "nothing half-written reached the ledger")

    def test_a_model_that_cannot_be_reached_is_a_refusal_not_a_crash(self):
        book, refusal = self.author(Provider(AgentError("Provider connection failed.")))
        self.assertFalse(planbook.has_goal(book))
        self.assertIn("connection failed", refusal)

    def test_invented_and_missing_step_numbers_are_both_refused(self):
        step_ids = [row["id"] for row in self.book["steps"]]
        extra = json.loads(json.dumps(TREE))
        extra["steps"].append({"id": 4, "accepts": [1]})
        with self.assertRaisesRegex(PolicyError, "exactly the plan"):
            planbook.validate_tree(extra, step_ids)
        short = json.loads(json.dumps(TREE))
        short["steps"] = short["steps"][:2]
        with self.assertRaisesRegex(PolicyError, "exactly the plan"):
            planbook.validate_tree(short, step_ids)

    def test_a_criterion_number_outside_the_list_is_refused(self):
        extra = json.loads(json.dumps(TREE))
        extra["steps"][0]["accepts"] = [1, 9]
        with self.assertRaisesRegex(PolicyError, "criterion numbers"):
            planbook.validate_tree(extra, [1, 2, 3])

    def test_a_step_annotated_twice_keeps_one_sorted_copy_of_its_criteria(self):
        doubled = json.loads(json.dumps(TREE))
        doubled["steps"][0]["accepts"] = [2, 1, 1]
        tree = planbook.validate_tree(doubled, [1, 2, 3])
        self.assertEqual(tree["steps"][0]["accepts"], [1, 2])

    def test_the_written_goal_survives_reopening_the_ledger(self):
        self.author(Provider())
        _again, book = planbook.open_book(self.plans, self.ws, "plan.md")
        self.assertEqual(book["schema"], 2)
        self.assertEqual(book["criteria"], TREE["criteria"])

    def test_the_step_task_cites_the_goal_and_only_its_own_criteria(self):
        book, _ = self.author(Provider())
        row = planbook.step(book, 3)
        task = planbook.task_for(book, row)
        self.assertIn(TREE["goal"], task)
        self.assertIn("401 for a bad password", task)
        self.assertNotIn("token for a known user", task, "step 3 answers criterion 2 only")

    def test_a_goal_less_plan_sends_the_task_it_always_sent(self):
        task = planbook.task_for(self.book, planbook.step(self.book, 1))
        self.assertIn("Implement step 1 of 3", task)
        self.assertNotIn("Acceptance criteria", task)
        self.assertNotIn("goal is", task)

    def test_a_criterion_no_step_answers_is_reported_not_swallowed(self):
        partial = json.loads(json.dumps(TREE))
        partial["steps"][0]["accepts"] = [1]
        partial["steps"][1]["accepts"] = []
        partial["steps"][2]["accepts"] = [1]
        tree = planbook.validate_tree(partial, [1, 2, 3])
        self.assertEqual(tree["steps"][1]["accepts"], [],
                         "a step that answers nothing is a legal answer, not a refusal")
        book = {"schema": 2, "goal": tree["goal"], "criteria": tree["criteria"],
                "steps": [dict(row, accepts=tree["steps"][index]["accepts"])
                          for index, row in enumerate(self.book["steps"])]}
        self.assertEqual(planbook.uncovered_criteria(book), [2])

    def test_the_sub_goal_title_is_read_from_the_tree_it_was_written_with(self):
        book, _ = self.author(Provider())
        self.assertEqual(planbook.sub_goal_of(book, planbook.step(book, 2)), "Auth API")
        self.assertEqual(planbook.sub_goal_of(book, planbook.step(book, 1)), "")


class FailedAndUnprovenStepsTests(unittest.TestCase):
    setUp = GoalTreeTests.setUp
    stored = GoalTreeTests.stored

    def proof_session(self, state="CHECKS_PASSED"):
        return {"id": "a" * 32, "state": state, "created": "2026-09-24T00:00:00+00:00",
                "runs": [{"recipe": "maven-test", "status": "passed", "exit_code": 0,
                          "proof": {"tests": 8, "failures": 0, "errors": 0, "skipped": 0,
                                    "source": "surefire XML"}}]}

    def test_a_failing_run_marks_the_step_failed_and_keeps_it_current(self):
        book = planbook.mark_failed(self.path, self.book, 1, "BUILD FAILURE in AuthController.java")
        self.assertEqual(book["steps"][0]["status"], "failed")
        self.assertIn("BUILD FAILURE", book["steps"][0]["failure_reason"])
        self.assertEqual(planbook.current(book)["id"], 1, "a failed step is the one to retry")
        self.assertIn("1 failed", planbook.progress_line(book))
        self.assertEqual(self.stored()["steps"][0]["status"], "failed")

    def test_a_secret_in_the_failure_reason_is_not_stored_whole(self):
        book = planbook.mark_failed(self.path, self.book, 1,
                                    "jdbc:mysql://db?password=hunter01 failed")
        self.assertNotIn("hunter01", book["steps"][0]["failure_reason"])

    def test_a_verified_step_is_not_reopened_by_a_later_failure(self):
        planbook.record_session(self.path, self.book, 1, "a" * 32)
        planbook.complete(self.path, self.book, 1, self.proof_session())
        book = planbook.mark_failed(self.path, self.book, 1, "a later run failed")
        self.assertEqual(book["steps"][0]["status"], "verified")

    def test_an_unknown_step_is_refused_by_name(self):
        with self.assertRaisesRegex(PolicyError, "Unknown plan step"):
            planbook.mark_failed(self.path, self.book, 9, "nothing")

    def test_a_retry_after_a_failure_moves_the_row_back_to_in_progress(self):
        planbook.mark_failed(self.path, self.book, 1, "failed once")
        book = planbook.record_session(self.path, self.book, 1, "b" * 32)
        self.assertEqual(book["steps"][0]["status"], "in_progress")

    def test_closing_without_proof_records_that_there_was_no_proof(self):
        book = planbook.mark_unproven(self.path, self.book, 2, "no run is recorded for this step")
        row = planbook.step(book, 2)
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["unproven"], "no run is recorded for this step")
        self.assertEqual(self.stored()["steps"][1]["unproven"], row["unproven"])
        self.assertEqual(planbook.done_titles(book), ["login endpoint"],
                         "the next step is still told not to redo it")


class ProofGateTests(unittest.TestCase):
    """The ledger may only say `verified` about a step a command run proved."""

    def test_no_proof_means_no_verified(self):
        book = {"steps": [{"id": 1, "title": "scaffold", "body": "", "status": "in_progress",
                           "session_id": "x", "verified_at": None}]}
        with self.assertRaisesRegex(PolicyError, "cannot be marked done"):
            planbook.complete(Path("unused-ledger.json"), book, 1,
                              {"id": "x", "state": "BLOCKED"})
        self.assertEqual(book["steps"][0]["status"], "in_progress", "the row was not written off")
        self.assertIn("not in CHECKS_PASSED",
                      planbook.proof_reason({"id": "x", "state": "BLOCKED"}))

    def test_a_passed_run_with_no_test_executed_is_no_proof(self):
        session = {"id": "x", "state": "CHECKS_PASSED",
                   "runs": [{"recipe": "maven-test", "status": "passed",
                             "proof": {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}}]}
        self.assertIn("no executed test", planbook.proof_reason(session))


if __name__ == "__main__":
    unittest.main()
