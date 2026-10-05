"""Plan Mode (T2.2): the `--plan` flag reads the whole plan without opening a write.

`review` shows what the bytes change (the diff); Plan Mode shows what the run intends —
Goal, Affected files, Steps, Risks, Test strategy — so a person can approve the plan
before the diff matters. The view is derived from the session record alone, so these
tests need no provider: only the CLI halves drive one, and they do it through a scripted
queue exactly like `test_cli.py`.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
from contextlib import redirect_stderr, redirect_stdout
import unittest
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer.cli import main
from ai_code_engineer.engine import plan_mode_text, plan_mode_view
from doubles import CALCULATOR_BAD, CALCULATOR_GOOD
from helpers import sandbox_repo

CALCULATOR_READ = {"action": "read_file", "path": "calculator.py"}
PROPOSE = {"action": "propose", "summary": "Fix addition", "checks": ["Run the addition tests"],
           "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]}

SECTIONS = ("goal", "affected_files", "steps", "risks", "test_strategy")


def change(path, before="", after="x = 1\n", delete=False):
    return {"path": path, "before": before or None, "after": after, "delete": delete}


def session(**fields) -> dict:
    base = {"id": "0" * 32, "root": "/tmp/wherever", "task": "Add a cache layer",
            "state": "WAITING_APPROVAL", "events": [], "checks": ["Run the unit tests"],
            "changes": [change("app/cache.py")], "summary": "Create the cache module",
            "task_state": {"goal": "Add a cache layer", "decisions": ["Read config first"],
                           "next_step": "propose the cache module", "acceptance": []}}
    base.update(fields)
    return base


class PlanView(unittest.TestCase):
    def test_the_view_carries_exactly_the_five_sections(self):
        self.assertEqual(sorted(plan_mode_view(session())), sorted(SECTIONS))

    def test_goal_steps_risks_and_checks_are_read_off_the_session(self):
        view = plan_mode_view(session())
        self.assertEqual(view["goal"], "Add a cache layer")
        self.assertIn("Create the cache module", view["steps"])
        self.assertIn("Read config first", view["steps"])
        self.assertIn("Next: propose the cache module", view["steps"])
        self.assertTrue(view["risks"][0].startswith("Risk level: "))
        self.assertIn("Run the unit tests", view["test_strategy"])

    def test_goal_falls_back_to_the_task_when_nothing_recorded_one(self):
        bare = {"task": "Fix the parser", "events": []}
        self.assertEqual(plan_mode_view(bare)["goal"], "Fix the parser")

    def test_each_affected_file_is_named_with_its_kind_of_change(self):
        view = plan_mode_view(session(changes=[
            change("app/cache.py"),                                    # new file
            change("app/legacy.py", before="x = 1\n"),                 # rewrite
            change("app/old.py", before="x = 1\n", delete=True),       # removal
        ]))
        self.assertEqual([(row["path"], row["action"]) for row in view["affected_files"]],
                         [("app/cache.py", "create"), ("app/legacy.py", "modify"),
                          ("app/old.py", "delete")])

    def test_risk_reasons_are_said_in_words_a_person_can_read(self):
        many = [change(f"app/module_{i}.py", before="a = 1\n") for i in range(6)]
        risks = plan_mode_view(session(changes=many))["risks"]
        self.assertIn("the proposal changes many files at once", risks)

    def test_a_massively_shrinking_rewrite_shows_up_as_a_risk(self):
        lost = session(changes=[change("app/big.py",
                                       before="".join(f"line{i} = {i}\n" for i in range(20)),
                                       after="only = 1\n")])
        self.assertTrue([line for line in plan_mode_view(lost)["risks"]
                         if line.startswith("Removal to check:")])

    def test_an_attached_plan_makes_its_read_only_line_the_first_step(self):
        view = plan_mode_view(session(plan_reference={"path": "PLAN.md", "sha256": "ab"},
                                      plan_step=3))
        self.assertEqual(view["steps"][0], "Attached plan (read-only): PLAN.md")
        self.assertIn("Implementing step 3 of the attached plan.", view["steps"])

    def test_acceptance_criteria_join_the_test_strategy(self):
        s = session(task_state={"goal": "", "acceptance": ["CI stays green"]})
        self.assertIn("CI stays green", plan_mode_view(s)["test_strategy"])

    def test_no_proposal_yet_is_said_rather_than_left_blank(self):
        view = plan_mode_view({"task": "Just look", "events": [], "changes": []})
        self.assertEqual(view["affected_files"], [])
        self.assertEqual(view["risks"], ["Risk level: low"])


class PlanModeText(unittest.TestCase):
    def test_every_section_is_headed_and_the_read_only_promise_is_said(self):
        text = plan_mode_text(session())
        for heading in ("Goal:", "Affected files (1):", "Steps (", "Risks (", "Test strategy:"):
            self.assertIn(heading, text)
        self.assertIn("PLAN MODE — read-only", text)
        self.assertIn("No project files changed", text)

    def test_the_plan_view_is_not_the_diff(self):
        text = plan_mode_text(session(changes=[change("app/cache.py", after=CALCULATOR_GOOD)]))
        self.assertNotIn("@@ -", text)
        self.assertNotIn("+++ after/", text)


class ScriptedProvider:
    model = "test-local"

    def __init__(self, responses):
        self.responses = iter(responses)

    def generate(self, messages, json_mode=True):
        value = next(self.responses)
        return value if isinstance(value, str) else json.dumps(value)


class PlanModeCli(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = sandbox_repo(self.temp.name)
        self.runs = Path(self.temp.name) / "runs"
        where = patch("ai_code_engineer.cli.app_dir", return_value=Path(self.temp.name))
        where.start()
        self.addCleanup(where.stop)
        provider = patch("ai_code_engineer.cli.make_provider",
                         return_value=ScriptedProvider([CALCULATOR_READ, PROPOSE]))
        provider.start()
        self.addCleanup(provider.stop)

    def run_command(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue()

    def planned_session(self, extra=()):
        code, text = self.run_command(["plan", "Fix add", "--repo", str(self.root),
                                       "--runs", str(self.runs), *extra])
        self.assertEqual(code, 0, text)
        return text

    def test_plan_flag_shows_the_sections_instead_of_the_diff(self):
        text = self.planned_session(["--plan"])
        for expected in ("PLAN MODE — read-only", "Goal:", "  • modify  calculator.py",
                         "Fix addition", "Run the addition tests"):
            self.assertIn(expected, text)
        self.assertNotIn("@@ -", text)

    def test_without_the_flag_the_command_answers_exactly_as_it_did(self):
        text = self.planned_session()
        self.assertNotIn("PLAN MODE", text)
        self.assertIn("--- before/calculator.py", text)

    def test_review_can_read_a_stored_session_in_plan_mode(self):
        self.planned_session()
        session_path = next(self.runs.glob("*/session.json"))
        code, text = self.run_command(["review", str(session_path), "--plan"])
        self.assertEqual(code, 0, text)
        self.assertIn("Goal:", text)
        self.assertIn("  • modify  calculator.py", text)
        self.assertNotIn("@@ -", text)

    def test_plain_review_still_prints_the_diff(self):
        self.planned_session()
        session_path = next(self.runs.glob("*/session.json"))
        code, text = self.run_command(["review", str(session_path)])
        self.assertEqual(code, 0, text)
        self.assertIn("@@ -", text)


if __name__ == "__main__":
    unittest.main()
