"""How far along a run says it is, and what it took to get the run there.

The workflow stage is a second axis over the same session record: `state` says what happened to the task
and decides which buttons work, `stage` says where in the workflow the run stands. Both are needed and
neither may be made to mean the other — the two vocabularies share exactly one string
(`WAITING_APPROVAL`) and disagree about every other one, which is why the tables live apart and why these
tests assert the *moves*, not just the names.

`engine.plan`, `apply_proposal`, `repair.record_run` and `rollback` are exercised for real here, through the
same scripted provider the engine tests use, because a stage nobody recorded is a strip that lies.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import core, engine, labels, repair
from ai_code_engineer.config import Settings
from ai_code_engineer.workspace import Workspace
from ai_code_engineer.webapp import uistate
from ai_code_engineer.webapp.fake import FakeController
from doubles import CALCULATOR_GOOD
from helpers import sandbox_repo
from test_cli import ScriptedProvider

READ_APP = {"action": "read_file", "path": "calculator.py"}
PROPOSE = {"action": "propose", "summary": "Fix addition", "checks": ["Run the addition tests"],
           "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]}


def stage_rows(session: dict) -> list:
    """The stages this record says it passed through, in the order it said them."""
    return [event.get("to") for event in session["events"] if event["kind"] == "stage"]


def run_row(status: str) -> dict:
    """A command run shaped like `runner.run`'s answer, for the loop to record."""
    return {"status": status, "recipe": "python-unittest", "exit_code": 0 if status == "passed" else 1,
            "seconds": 1.5, "command": "python -m unittest", "label": "Tests",
            "failures": [] if status == "passed" else ["AssertionError: 1 != 2"],
            "tail": "OK" if status == "passed" else "FAILED", "tests_observed": True}


class TheAxisItself(unittest.TestCase):
    """`core` owns the names and the moves, and nothing else may invent either."""

    def test_every_stage_has_a_word_in_both_languages(self):
        self.assertEqual(sorted(core.STAGES), sorted(labels.STAGES),
                         "a stage with no wording draws its own code name in the strip")
        self.assertEqual(sorted(labels.STAGES), sorted(labels.STAGES_AR))

    def test_the_arabic_words_are_arabic_and_the_order_is_the_same(self):
        for code in core.STAGES:
            self.assertTrue(labels.is_arabic(labels.STAGES_AR[code]), code)
            self.assertNotEqual(labels.STAGES[code], labels.STAGES_AR[code], code)
        self.assertEqual(list(core.STAGES), list(labels.STAGES),
                         "the strip draws the lifecycle's order, so a dictionary that re-sorted it lies")

    def test_the_moves_are_written_out_for_every_stage(self):
        self.assertEqual(set(core.STAGE_MOVES), set(core.STAGES) | {""},
                         "a stage with no row inherits nothing — that row has to be written")
        for code, allowed in core.STAGE_MOVES.items():
            for target in allowed:
                self.assertIn(target, core.STAGES, f"{code} allows {target}, which is not a stage")

    def test_a_record_that_cannot_be_read_grants_no_move(self):
        """The same shape as `policy.decide`: an unlegible current stage is not permission to move."""
        self.assertFalse(core.stage_allowed("discovering", "plan"))
        self.assertFalse(core.stage_allowed("", ""))
        self.assertFalse(core.stage_allowed("verify", "nonsense"))

    def test_the_lifecycle_answers_for_every_status_it_has(self):
        for status in core.AgentStatus:
            self.assertIn(core.stage_of(status), core.STAGES, status.value)

    def test_the_table_keeps_its_own_edges(self):
        """The lifecycle this module drives is asserted by name in `test_core.py`; the stage axis may not
        bend it, because two tables that agree only when somebody remembers are two tables."""
        self.assertRaises(Exception, core.AgentState(
            task=core.Task(description="x"),
            status=core.AgentStatus.WAITING_APPROVAL).transition_to,
            core.AgentStatus.DONE, "no click gets a task from review to finished")


class WhatTheEngineRecords(unittest.TestCase):
    """The axis is only true if the functions that do the work write it as they go."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.repo = sandbox_repo(self.temp.name, with_tests=False)
        self.runs = Path(self.temp.name) / "runs"
        self.ws = Workspace(self.repo)

    def planned(self) -> Path:
        return engine.plan(self.ws, "Fix the addition", ScriptedProvider([READ_APP, PROPOSE]),
                           Settings(), self.runs, progress=lambda _line: None)

    def test_a_proposal_walks_the_stages_it_actually_walked(self):
        path = self.planned()
        session = engine.load_session(path)
        self.assertEqual(stage_rows(session), ["understand", "plan", "implement", "impact", "review"])
        self.assertEqual(session["stage"], "review")

    def test_recording_a_stage_never_moves_the_hash_a_person_typed(self):
        path = self.planned()
        session = engine.load_session(path)
        before = session["proposal_hash"]
        engine.record_stage(session, "build_test")
        self.assertEqual(engine.proposal_hash(session), before,
                         "a strip that invalidates an approval would hold a task hostage to its own label")

    def test_apply_is_the_approve_step_and_a_passing_run_ends_at_verify(self):
        path = self.planned()
        approved = engine.load_session(path)["proposal_hash"]
        engine.apply_proposal(path, approved)
        self.assertEqual(engine.load_session(path)["stage"], "approve")
        repair.record_run(path, run_row("passed"))
        session = engine.load_session(path)
        self.assertEqual(stage_rows(session)[-2:], ["build_test", "verify"])

    def test_a_failing_run_puts_the_task_back_on_the_model(self):
        """The verdict comes from `core.record_verification`, not from a window reading the exit code."""
        path = self.planned()
        approved = engine.load_session(path)["proposal_hash"]
        engine.apply_proposal(path, approved)
        repair.record_run(path, run_row("failed"))
        self.assertEqual(engine.load_session(path)["stage"], "implement")

    def test_a_rollback_returns_the_run_to_the_offer_that_is_left(self):
        path = self.planned()
        approved = engine.load_session(path)["proposal_hash"]
        engine.apply_proposal(path, approved)
        repair.record_run(path, run_row("passed"))
        engine.rollback(path, approved)
        self.assertEqual(engine.load_session(path)["stage"], "review")

    def test_a_stage_survives_the_round_trip_through_disk(self):
        path = self.planned()
        session = engine.load_session(path)
        self.assertEqual(engine.load_session(path)["stage"], session["stage"])

    def test_an_illegal_move_writes_nothing_rather_than_a_second_answer(self):
        path = self.planned()
        session = engine.load_session(path)
        self.assertEqual(engine.record_stage(session, "understand"), "review")
        self.assertEqual(len(stage_rows(session)), 5)

    def test_a_session_built_from_a_chosen_block_opens_where_it_really_is(self):
        path = engine.propose_block(self.ws, "Keep this block", "calculator.py", CALCULATOR_GOOD,
                                    self.runs)
        self.assertEqual(engine.load_session(path)["stage"], "review")


class WhatTheWindowsDraw(unittest.TestCase):
    """The block both windows build, and the sentence around it."""

    def test_the_block_names_the_position_and_marks_the_steps_behind_it(self):
        block = uistate.stage_block("build_test")
        self.assertEqual(block["current"], "build_test")
        self.assertEqual(block["total"], len(core.STAGES))
        self.assertEqual([row["code"] for row in block["steps"]], list(core.STAGES))
        reached = [row["code"] for row in block["steps"] if row["reached"]]
        self.assertEqual(reached, list(core.STAGES)[:7])
        self.assertIn("step 7 of 8", block["line"])

    def test_a_run_that_recorded_nothing_says_so_instead_of_claiming_step_one(self):
        block = uistate.stage_block("")
        self.assertEqual(block["current"], "")
        self.assertEqual(block["line"], labels.stage_label(""))
        self.assertFalse(any(row["reached"] for row in block["steps"]))

    def test_an_unknown_code_is_shown_as_itself(self):
        """An older record or a stage renamed in a later release reads as its own name — a step number for
        a thing the table does not know would be a fabricated position."""
        self.assertEqual(labels.stage_line("teleported"), "teleported")

    def test_the_arabic_line_keeps_the_count_and_flips_the_language(self):
        block = uistate.stage_block("review", arabic=True)
        self.assertTrue(labels.is_arabic(block["line"]), block["line"])
        self.assertIn("5", block["line"], "the number is not translated, and the count must survive it")
        self.assertTrue(labels.is_arabic(block["steps"][0]["label"]))

    def test_the_scripted_window_carries_the_field_the_client_reads(self):
        """`test_webapp` compares the two windows' top-level keys; this asserts the one field the strip
        draws exists in the preview too, because the preview is the only window a design can be reviewed in
        on a machine that has never opened a project."""
        block = FakeController().snapshot()["stage"]
        self.assertEqual(block["current"], "review")
        self.assertEqual(sorted(block), sorted(uistate.stage_block("review")))


if __name__ == "__main__":
    unittest.main()
