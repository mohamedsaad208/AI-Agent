"""The self-healing loop's own three promises: a ceiling that survives, a shortlist that is measured,
and a rule left behind when a round finally passes.

Each of these was a hole rather than a refactor. The round count lived in whichever window pressed the
button, so closing the app made a spent budget free again. The files a fix round opens were whatever a
1.5B model guessed, and every wrong name cost a turn. And a build that took three rounds to fix taught
the next task nothing, because the pair — the failure and the change that closed it — existed for one
instant in `record_run` and was then dropped.

Everything here drives the real `record_run` and the real index, because a ledger nobody can read back
is a file with a name.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import engine, memory, repair
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import apply_proposal, plan
from ai_code_engineer.workspace import Workspace
from doubles import CALCULATOR_GOOD
from helpers import sandbox_repo
from test_cli import ScriptedProvider
from test_resume import Script

READ = {"action": "read_file", "path": "calculator.py"}
PROPOSE = {"action": "propose", "summary": "Fix addition", "checks": ["Run the addition tests"],
           "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]}
FAILING_RUN = {"status": "failed", "recipe": "python-unittest", "exit_code": 1, "seconds": 2.0,
               "command": "python -m unittest", "label": "Tests", "target": ".",
               "failures": ["AssertionError: 1 != 2"], "tail": "FAILED (failures=1)",
               "tests_observed": True}
PASSING_RUN = {"status": "passed", "recipe": "python-unittest", "exit_code": 0, "seconds": 1.0,
               "command": "python -m unittest", "label": "Tests", "target": ".",
               "failures": [], "tail": "OK", "tests_observed": True}


class FixWindow(unittest.TestCase):
    """A repo, a planned proposal and an applied one, as the loop sees them."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = sandbox_repo(self.temp.name, with_tests=False)
        self.runs = self.base / "runs"
        self.ws = Workspace(self.repo)

    def planned(self, **fields) -> Path:
        return plan(self.ws, "Fix the addition", ScriptedProvider([READ, PROPOSE]),
                    Settings(), self.runs, progress=lambda _line: None, **fields)

    def applied(self, **fields) -> Path:
        path = self.planned(**fields)
        apply_proposal(path, engine.load_session(path)["proposal_hash"])
        return path


class TheCeilingStaysSpent(FixWindow):
    """The round number belongs to the record, not to whichever window is still open."""

    def test_a_round_is_charged_on_the_session_it_produced(self):
        path = self.planned(fix_round=2, extra_context="AssertionError: 1 != 2")
        session = engine.load_session(path)
        self.assertEqual(session["fix_round"], 2)
        self.assertEqual(repair.round_of(session), 2)

    def test_charging_a_round_before_it_costs_a_request_is_the_whole_point(self):
        """The number is written on the setup turn, so a round that dies mid-way is still spent — the
        restart that finds the session finds the ceiling with it."""
        script = Script([{"action": "read_file", "path": "calculator.py"}, RuntimeError("killed")])
        with self.assertRaises(RuntimeError):
            plan(self.ws, "Fix the addition", script, Settings(), self.runs,
                 progress=lambda _line: None, fix_round=3)
        stored = [engine.load_session(path) for path in self.runs.glob("*/session.json")]
        self.assertEqual([repair.round_of(item) for item in stored], [3])

    def test_a_round_number_never_moves_the_hash_a_person_typed(self):
        session = engine.load_session(self.planned())
        before = session["proposal_hash"]
        session["fix_round"] = 2
        self.assertEqual(engine.proposal_hash(session), before,
                         "the round a run is on is not part of what was approved")

    def test_a_record_with_no_number_spent_no_rounds(self):
        self.assertEqual(repair.round_of({}), 0)
        self.assertEqual(repair.round_of({"fix_round": "three"}), 0)
        self.assertEqual(repair.round_of({"fix_round": -2}), 0)

    def test_three_rounds_spent_is_three_rounds_spent_after_a_restart(self):
        """`should_stop` compares the number it is handed, and the number now comes from the record —
        which is the sentence this test exists to keep true."""
        stop, reason = repair.should_stop([], repair.round_of({"fix_round": 3}))
        self.assertTrue(stop)
        self.assertIn("stopped after 3 fix rounds", reason)

    def test_both_windows_take_the_ceiling_off_the_record(self):
        """The seeding is the fix, and a fix in one window is a bug in the other: the Tk window restarts a
        resumed loop at zero rounds exactly as the web one used to. Read as source because that is the only
        form in which "both windows ask" is checkable without driving two GUIs."""
        src = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
        for name in (Path("gui.py"), Path("webapp") / "controller.py"):
            source = (src / name).read_text(encoding="utf-8")
            self.assertIn("repair.round_of(", source, f"{name} restarts its loop at zero rounds")
            self.assertIn("fix_round=self._fix_round", source,
                          f"{name} spends a round the record never sees")


class TheShortlistIsMeasured(FixWindow):
    """Which files a round should open, read from the index instead of invented by the model."""

    def test_the_files_that_use_the_change_are_named(self):
        (self.repo / "uses.py").write_text("import calculator\n", encoding="utf-8", newline="\n")
        session = engine.load_session(self.applied())
        block = repair.fix_candidates(self.ws, session)
        self.assertIn("uses.py", block)
        self.assertIn("shortlist", block)

    def test_a_test_that_only_mirrors_the_name_counts_as_related(self):
        tests = self.repo / "tests"
        tests.mkdir()
        (tests / "test_calculator.py").write_text("import calculator\n", encoding="utf-8", newline="\n")
        block = repair.fix_candidates(self.ws, engine.load_session(self.applied()))
        self.assertIn("tests/test_calculator.py", block.replace("\\", "/"))

    def test_a_list_that_was_cut_says_so(self):
        """The cap is the honest part: a shortlist that hides its own cut reads like a survey of the
        project, and a model that believes it saw everything stops looking."""
        original = repair.CANDIDATE_FILES_SHOWN
        repair.CANDIDATE_FILES_SHOWN = 1
        self.addCleanup(setattr, repair, "CANDIDATE_FILES_SHOWN", original)
        for number in range(4):
            (self.repo / f"uses{number}.py").write_text("import calculator\n", encoding="utf-8",
                                                        newline="\n")
        block = repair.fix_candidates(self.ws, engine.load_session(self.applied()))
        self.assertIn("more ranked candidates exist", block)
        self.assertIn("opened none of", block)

    def test_a_task_that_wrote_nothing_proposes_no_shortlist(self):
        self.assertEqual(repair.fix_candidates(self.ws, {"changes": []}), "")

    def test_an_index_that_cannot_be_read_costs_the_round_nothing(self):
        """The failure is the point: a broken scan removes a hint, it does not stop a fix round that a
        person is waiting on."""
        with patch.object(self.ws, "index", side_effect=OSError("no drive")):
            self.assertEqual(repair.fix_candidates(self.ws, {"changes": [{"path": "calculator.py"}]}), "")

    def test_the_join_is_repairs_and_not_each_windows(self):
        """One rule about what a fix turn reads, written once: the failure, then the shortlist."""
        (self.repo / "uses.py").write_text("import calculator\n", encoding="utf-8", newline="\n")
        session = engine.load_session(self.applied())
        joined = repair.with_candidates("the failure text", self.ws, session)
        self.assertTrue(joined.startswith("the failure text\n\n"), joined)
        self.assertIn("uses.py", joined)
        self.assertEqual(repair.with_candidates("the failure text", self.ws, {"changes": []}),
                         "the failure text")


class TheRuleLeftBehind(FixWindow):
    """A passing round writes the one thing the next task in this folder could use."""

    def notes(self):
        return memory.read_auto_notes(memory.memory_dir_for(self.runs), str(self.repo))

    def test_a_round_that_closed_a_failure_writes_the_pair(self):
        path = self.applied(fix_round=2, extra_context="AssertionError: 1 != 2\nFAILED (failures=1)")
        repair.record_run(path, PASSING_RUN)
        facts = self.notes().get("facts") or {}
        self.assertEqual(len(facts), 1, facts)
        label, fact = next(iter(facts.items()))
        self.assertIn("AssertionError", label)
        self.assertIn("round 2", fact)
        self.assertIn("calculator.py", fact)

    def test_the_next_task_reads_it_back(self):
        """A ledger is only a ledger if something reads it: the same store every later turn in this folder
        is sent through `auto_notes_context`."""
        path = self.applied(fix_round=1, extra_context="AssertionError: 1 != 2")
        repair.record_run(path, PASSING_RUN)
        block = memory.auto_notes_context(memory.memory_dir_for(self.runs), str(self.repo))
        self.assertIn("AssertionError", block)

    def test_a_first_task_that_passes_leaves_no_rule(self):
        """Nothing was failing before it, so there is no pair to record — and a fact invented from a
        summary would teach the next task a thing that never happened."""
        path = self.applied(extra_context="")
        repair.record_run(path, PASSING_RUN)
        self.assertEqual(self.notes().get("facts") or {}, {})

    def test_a_round_that_is_still_failing_records_no_victory(self):
        path = self.applied(fix_round=1, extra_context="AssertionError: 1 != 2")
        repair.record_run(path, FAILING_RUN)
        self.assertEqual(self.notes().get("facts") or {}, {})

    def test_a_round_that_remembers_no_failure_names_no_rule(self):
        """The label has to come from the failure's own words. A rule keyed on a summary would be a
        second copy of the summary, and a fingerprint nobody can match against anything."""
        path = self.applied(fix_round=1, extra_context="")
        repair.record_run(path, PASSING_RUN)
        self.assertEqual(self.notes().get("facts") or {}, {})

    def test_a_store_that_cannot_be_written_still_leaves_the_run_recorded(self):
        """`record_facts` swallows what it cannot write, and the promise worth testing is the one that
        follows: the build that passed is still on record when the notes folder is not a folder."""
        path = self.applied(fix_round=1, extra_context="AssertionError: 1 != 2")
        blocked = self.base / "blocked-file"
        blocked.write_text("not a folder", encoding="utf-8")
        with patch.object(memory, "memory_dir_for", return_value=blocked / "memory"):
            session = repair.record_run(path, PASSING_RUN)
        self.assertEqual(session["state"], "CHECKS_PASSED")
        self.assertEqual(session["stage"], "verify")
        self.assertEqual(len(session["runs"]), 1)

    def test_the_earned_line_is_redacted_before_it_is_kept(self):
        """A build failure quotes the string that broke, and that string is often the credential — this
        store is read back into every later prompt."""
        path = self.applied(fix_round=1,
                            extra_context="AssertionError: token=sk-abcdefghijklmnopqrstuvwxyz123456")
        repair.record_run(path, PASSING_RUN)
        block = memory.auto_notes_context(memory.memory_dir_for(self.runs), str(self.repo))
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", block)


if __name__ == "__main__":
    unittest.main()
