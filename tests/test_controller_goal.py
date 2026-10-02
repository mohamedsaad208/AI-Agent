"""The web window's half of the goal tree: what a chained run writes, and what its verify button accepts.

A goal tree is only real if the run that needs it survives without one, and the ledger is only worth
trusting if the button that closes a step cannot close it on a click. Both are window behaviour, so they
are tested here rather than in `test_goal_engine.py`, which stays about the ledger file itself.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from controller_case import ControllerCase, Scripted
from doubles import CALCULATOR_GOOD, OLLAMA_ENTRY
from ai_code_engineer import planbook
from ai_code_engineer.engine import atomic_json, load_session
from ai_code_engineer.workspace import Workspace

PLAN = """# Delivery plan

## Phase 1: scaffold
Create the application and its manifest.

## Phase 2: login endpoint
Add the login route and its tests.
"""

TREE = {"goal": "The service signs a known user in and refuses a bad password.",
        "criteria": ["Login returns a token for a known user", "Login returns 401 for a bad password"],
        "sub_goals": [{"id": 1, "title": "Auth API"}],
        "steps": [{"id": 1, "accepts": [1], "sub_goal": 1}, {"id": 2, "accepts": [2], "sub_goal": 1}]}

PROPOSAL = json.dumps({"action": "propose", "summary": "Scaffold the app", "checks": ["Run the build"],
                       "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]})


class GoalRunModel:
    """Answers the goal request first, then proposes — the two calls one chained run now makes."""

    model = "test-local"

    def __init__(self, goal_reply=None):
        self.goal_reply = json.dumps(TREE) if goal_reply is None else goal_reply
        self.calls = []

    def generate(self, messages, json_mode=True, cancelled=None):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return self.goal_reply
        return PROPOSAL


class GoalTreeRunTests(ControllerCase):
    model_factory = GoalRunModel

    def attach(self):
        (self.repo / "plan.md").write_text(PLAN, encoding="utf-8")
        self.controller.chained = True
        self.controller.plan_file = str(self.repo / "plan.md")
        self.controller.refresh_plan_status()

    def ledger(self):
        return json.loads(self.controller.ledger_path.read_text(encoding="utf-8"))

    def test_a_chained_run_writes_the_goal_tree_before_it_proposes(self):
        self.attach()
        self.controller.start_plan("Scaffold the login work")
        self.controller.join()
        book = self.ledger()
        self.assertEqual(book["schema"], 2)
        self.assertEqual(book["goal"], TREE["goal"])
        self.assertEqual(book["criteria"], TREE["criteria"])
        self.assertEqual(book["steps"][0]["accepts"], [1])
        self.assertIsNotNone(self.controller.session, "the run still reached a proposal")
        session = load_session(self.controller.session_path)
        self.assertEqual(session["goal"], TREE["goal"])
        self.assertEqual(session["accepts"], [1])
        sent = self.model.calls[1][-1]["content"]
        self.assertIn("Acceptance criteria this step must leave true", sent,
                      "the step's own criteria ride with its task")

    def test_the_tree_is_asked_for_once_and_never_rewritten(self):
        self.attach()
        self.controller.start_plan("Scaffold the login work")
        self.controller.join()
        written = self.ledger()["goal"]
        self.controller.start_plan("Step 1 is verified, continue")
        self.controller.join()
        asked = [call for call in self.model.calls
                 if "goal tree" in str(call[0]["content"])]
        self.assertEqual(len(asked), 1, "the second run reused the tree instead of re-asking for it")
        self.assertEqual(self.ledger()["goal"], written)

    def test_a_run_without_a_goal_tree_still_proposes_and_says_why(self):
        self.model = GoalRunModel(goal_reply=json.dumps({"goal": "Only a goal"}))
        with patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model):
            self.attach()
            self.controller.start_plan("Scaffold the login work")
            self.controller.join()
        self.assertFalse(self.controller.ledger_path.exists()
                         and "goal" in self.ledger(), "nothing half-written landed in the ledger")
        self.assertIsNotNone(self.controller.session, "the step still ran")
        said = [row["text"] for row in self.controller.messages if row["role"] == "tool"]
        self.assertTrue(any("without a goal tree" in line for line in said),
                        "the missing tree is a said fact, not a silent hole")


    def test_the_snapshot_carries_the_tree_the_client_draws(self):
        from ai_code_engineer import labels
        self.attach()
        self.controller.start_plan("Scaffold the login work")
        self.controller.join()
        plan = self.controller.snapshot()["plan"]
        self.assertEqual(plan["goal"], TREE["goal"])
        self.assertEqual(plan["criteria"], TREE["criteria"])
        self.assertEqual(plan["steps"][0]["accepts"], [1])
        self.assertEqual(plan["steps"][1]["accepts"], [2])
        self.assertEqual(plan["uncovered"], [])
        self.assertEqual(plan["sub_goal"], "Auth API")
        self.assertEqual(plan["strings"]["goal"], labels.NOTE_TEMPLATES["plan_goal"][0])

    def test_the_tree_words_come_from_the_language_the_window_reads_in(self):
        from ai_code_engineer import labels
        self.attach()
        self.controller.start_plan("ابنِ عملية تسجيل الدخول ورفض كلمة المرور الخطأ")
        self.controller.join()
        self.assertTrue(self.controller.arabic, "the window reads the task it was given")
        plan = self.controller.snapshot()["plan"]
        self.assertEqual(plan["strings"]["criteria"], labels.NOTE_TEMPLATES["plan_criteria"][1])
        self.assertTrue(labels.is_arabic(plan["strings"]["uncovered"]))

    def test_a_criterion_no_step_answers_arrives_marked_as_one(self):
        partial = json.loads(json.dumps(TREE))
        partial["steps"][1]["accepts"] = []

        def author(path, book, provider, **kw):
            tree = planbook.validate_tree(partial, [row["id"] for row in book["steps"]])
            book.update(schema=2, goal=tree["goal"], criteria=tree["criteria"],
                        sub_goals=tree["sub_goals"])
            for entry, row in zip(tree["steps"], book["steps"]):
                row["accepts"] = entry["accepts"]
            return book, ""

        self.attach()
        with patch("ai_code_engineer.planbook.author_goal", side_effect=author):
            self.controller.start_plan("Scaffold the login work")
            self.controller.join()
        plan = self.controller.snapshot()["plan"]
        self.assertEqual(plan["uncovered"], [2])
        self.assertEqual(plan["steps"][1]["accepts"], [])

    def test_a_new_plan_reads_every_criterion_as_not_run_yet(self):
        """The verdicts arrive with the tree: a plan that has only just been authored has no proof, and
        the window has to say which kind of "no" it is holding."""
        from ai_code_engineer import labels
        self.attach()
        self.controller.start_plan("Scaffold the login work")
        self.controller.join()
        plan = self.controller.snapshot()["plan"]
        rows = {row["number"]: row for row in plan["verdicts"]}
        self.assertEqual(sorted(rows), [1, 2])
        self.assertEqual({row["verdict"] for row in rows.values()}, {"unproven"})
        self.assertEqual({row["why"] for row in rows.values()}, {labels.note("verdict_not_run")})
        self.assertEqual(plan["verdictNote"], labels.note("plan_verdicts_line", proved=0, total=2))

    def test_a_step_closed_by_a_click_leaves_its_criterion_unproven_in_the_snapshot(self):
        """Item #12's claim, tested at the window: the ledger row says `verified` because that is the only
        status a click may write, and the verdict beside it still says no command run said so."""
        from ai_code_engineer import labels
        self.attach()
        self.controller.start_plan("Scaffold the login work")
        self.controller.join()
        self.controller.complete_step(1)
        self.assertEqual(self.ledger()["steps"][0]["status"], "verified")
        plan = self.controller.snapshot()["plan"]
        rows = {row["number"]: row for row in plan["verdicts"]}
        self.assertEqual(rows[1]["verdict"], "unproven")
        self.assertEqual(rows[1]["why"], labels.note("verdict_clicked"))
        self.assertEqual(plan["verdictNote"], labels.note("plan_verdicts_line", proved=0, total=2))

    def test_the_scripted_preview_sends_the_rows_the_window_sends(self):
        """One client template draws both windows, so a key the preview forgets is a hole no design
        review in it would ever show."""
        from ai_code_engineer.webapp.fake import FakeController
        self.attach()
        self.controller.start_plan("Scaffold the login work")
        self.controller.join()
        real, fake = self.controller.snapshot()["plan"], FakeController().snapshot()["plan"]
        self.assertEqual(set(real["verdicts"][0]), set(fake["verdicts"][0]))
        self.assertEqual({row["verdict"] for row in fake["verdicts"]} - set(planbook.VERDICTS), set())
        self.assertIn("verdictNote", fake)


class VerifyButtonProofTests(unittest.TestCase):
    """The button closes a step on recorded proof, or records that it had none."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()

    def window(self, confirm):
        controller = Scripted(self.app_dir, answers={"confirm": confirm})
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        self.addCleanup(controller.close)
        return controller

    def standing_on(self, controller, session_id=None, proved=False):
        """A ledger with step 1, optionally bound to a run whose session file is on disk."""
        repo = self.app_dir / "repo"
        repo.mkdir()
        (repo / "plan.md").write_text(PLAN, encoding="utf-8")
        controller.set_repo(str(repo))
        controller.plan_file = str(repo / "plan.md")
        path, book = planbook.open_book(controller.plans, Workspace(repo), "plan.md")
        if session_id:
            book = planbook.record_session(path, book, 1, session_id)
            atomic_json(controller.runs / session_id / "session.json", {
                "schema": 1, "id": session_id, "root": str(repo), "task": "step 1",
                "state": "CHECKS_PASSED" if proved else "BLOCKED",
                "created": "2026-09-30T00:00:00+00:00", "events": [], "model": "test-local",
                "runs": [{"recipe": "maven-test", "status": "passed", "exit_code": 0,
                          "proof": {"tests": 8, "failures": 0, "errors": 0, "skipped": 0,
                                    "source": "surefire XML"}}]})
        controller.ledger_path, controller.ledger = path, book
        return path, book

    def row(self, path, step_id=1):
        return planbook.step(json.loads(path.read_text(encoding="utf-8")), step_id)

    def test_a_step_with_no_run_is_not_closed_by_a_click(self):
        controller = self.window(confirm=False)
        path, book = self.standing_on(controller)
        controller.complete_step(1)
        self.assertEqual(book["steps"][0]["status"], "pending")
        self.assertFalse(path.exists(), "a refusal writes nothing")
        self.assertIn("stays open", controller.status)

    def test_declining_the_override_leaves_the_step_open(self):
        controller = self.window(confirm=False)
        path, _ = self.standing_on(controller, session_id="f" * 32)
        controller.complete_step(1)
        self.assertEqual(self.row(path)["status"], "in_progress")
        self.assertEqual(controller.asked[-1]["title"], "Plan step proof")

    def test_the_override_closes_the_step_and_marks_it_unproven(self):
        controller = self.window(confirm=True)
        path, _ = self.standing_on(controller, session_id="f" * 32)
        controller.complete_step(1)
        row = self.row(path)
        self.assertEqual(row["status"], "verified")
        self.assertIn("not in CHECKS_PASSED", row["unproven"])
        self.assertEqual(controller.snapshot()["plan"]["verified"], 1)

    def test_a_run_that_proved_its_tests_closes_the_step_with_no_override(self):
        controller = self.window(confirm=True)
        path, _ = self.standing_on(controller, session_id="f" * 32, proved=True)
        controller.complete_step(1)
        self.assertEqual(self.row(path)["status"], "verified")
        self.assertNotIn("unproven", self.row(path), "this one was proved by a run")
        self.assertEqual(controller.asked, [], "no dialog was needed")


if __name__ == "__main__":
    unittest.main()
