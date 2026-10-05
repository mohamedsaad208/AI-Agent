"""Tests for the final report table and its verification badges (Release 1 - Task 1.4).

The closing view must print exactly the three strict badges — VERIFIED / INFERRED /
NOT CHECKED — and pick between them empirically: only a green run with a counted test
proof earns VERIFIED, an executed-but-unproven run earns INFERRED, and nothing-checked
earns NOT CHECKED. These tests feed the view a FinalReport dataclass, a wire event, and
a raw session record, and check the tables and badges each produces.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest

from rich.console import Console

from ai_code_engineer import events as ux
from ai_code_engineer import report_view as rv
from ai_code_engineer.events import FinalReport, VerificationStatus


def plain(table) -> str:
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, no_color=True, width=100).print(table)
    return buffer.getvalue()


def session(runs=(), events=None, over=None):
    """A minimal session record, shaped the way engine and report.py store them."""
    data = {
        "id": "abc123", "task": "Add pagination to the list endpoint",
        "state": "CHECKS_PASSED", "model": "test", "created": "2026-09-28T10:00:00+00:00",
        "changes": [{"path": "app/views.py", "before": "", "after": "def list_page(): ..."}],
        "runs": list(runs),
        "events": (events if events is not None else [
            {"at": "2026-09-28T10:00:20+00:00", "kind": "proposal", "hash": "f" * 64,
             "summary": "Add pagination"},
            {"at": "2026-09-28T10:00:30+00:00", "kind": "approved", "hash": "f" * 64},
            {"at": "2026-09-28T10:00:35+00:00", "kind": "written", "path": "app/views.py",
             "sha256": "2" * 64},
        ]),
    }
    data.update(over or {})
    return data


def green_run(**over):
    run = {"recipe": "pytest", "label": "pytest", "command": "pytest -q",
           "status": "passed", "exit_code": 0, "seconds": 1.2,
           "proof": {"tests": 8, "failures": 0, "errors": 0, "source": "JUnit XML"}}
    run.update(over)
    return run


class BadgeTests(unittest.TestCase):
    def test_the_three_badges_are_the_whole_vocabulary(self):
        self.assertEqual(set(rv.BADGE), set(VerificationStatus))
        self.assertEqual(rv.as_status("VERIFIED"), VerificationStatus.VERIFIED)
        self.assertEqual(rv.as_status("not checked"), VerificationStatus.NOT_CHECKED)

    def test_unknown_status_collapses_down_never_up(self):
        """A report may not claim more certainty than the record supports."""
        self.assertEqual(rv.as_status("PROBABLY_FINE"), VerificationStatus.NOT_CHECKED)
        self.assertEqual(rv.as_status(""), VerificationStatus.NOT_CHECKED)
        self.assertEqual(rv.as_status(None), VerificationStatus.NOT_CHECKED)

    def test_badge_text_carries_the_label(self):
        self.assertIn("VERIFIED", rv.badge("VERIFIED").plain)
        self.assertIn("NOT CHECKED", rv.badge("garbage").plain)


class StatusDerivationTests(unittest.TestCase):
    def test_green_counted_proof_is_verified(self):
        self.assertEqual(rv.status_from_session(session(runs=[green_run()])),
                         VerificationStatus.VERIFIED)

    def test_exit_code_without_a_test_count_is_only_inferred(self):
        """An exit code proves a command ran, not that the change works."""
        run = green_run(proof=None)
        self.assertEqual(rv.status_from_session(session(runs=[run])),
                         VerificationStatus.INFERRED)

    def test_failures_are_never_verified(self):
        run = green_run(status="failed", exit_code=1,
                        proof={"tests": 8, "failures": 2, "errors": 0, "source": "x"})
        self.assertEqual(rv.status_from_session(session(runs=[run])),
                         VerificationStatus.INFERRED)

    def test_a_run_with_zero_tests_is_not_counted_proof(self):
        run = green_run(proof={"tests": 0, "failures": 0, "errors": 0, "source": "x"})
        self.assertEqual(rv.status_from_session(session(runs=[run])),
                         VerificationStatus.INFERRED)

    def test_mixed_runs_with_one_red_are_not_verified(self):
        """A single failing executed run removes the badge's right to claim VERIFIED."""
        red = green_run(status="failed", exit_code=1,
                        proof={"tests": 4, "failures": 1, "errors": 0, "source": "x"})
        self.assertEqual(rv.status_from_session(session(runs=[green_run(), red])),
                         VerificationStatus.INFERRED)

    def test_nothing_executed_is_not_checked(self):
        self.assertEqual(rv.status_from_session(session(runs=[])),
                         VerificationStatus.NOT_CHECKED)

    def test_recorded_verification_event_lifts_only_to_inferred(self):
        s = session(events=[{"at": "2026-09-28T10:00:40+00:00", "kind": "verification",
                             "status": "passed"}])
        self.assertEqual(rv.status_from_session(s), VerificationStatus.INFERRED)


class CoercionTests(unittest.TestCase):
    def test_dataclass_round_trips_through_the_wire_shape(self):
        report = FinalReport(task="fix", success=True, verification_status="VERIFIED",
                             files_changed=("a.py",), test_summary="8 tests")
        wire = ux.Event.from_dict(report.to_dict())
        self.assertEqual(rv.to_report(wire).verification_status, "VERIFIED")
        self.assertEqual(rv.to_report(report.to_dict()).files_changed, ("a.py",))

    def test_a_session_becomes_an_earned_report(self):
        report = rv.to_report(session(runs=[green_run()]))
        self.assertEqual(report.verification_status, "VERIFIED")
        self.assertTrue(report.success)
        self.assertIn("app/views.py", report.files_changed)

    def test_a_session_without_evidence_reports_not_checked(self):
        report = rv.to_report(session(runs=[]))
        self.assertEqual(report.verification_status, "NOT CHECKED")
        self.assertFalse(report.success)

    def test_completed_session_with_no_files_is_a_success(self):
        """A task that needed no change succeeded without proof — nothing was there to check."""
        s = session(runs=[], over={"state": "COMPLETED", "events": []})
        self.assertTrue(rv.to_report(s).success)

    def test_duration_is_read_from_the_record(self):
        report = rv.to_report(session(runs=[green_run()], over={
            "events": [{"at": "2026-09-28T10:00:40+00:00", "kind": "written",
                        "path": "app/views.py"}]}))
        self.assertAlmostEqual(report.duration_seconds, 40.0)

    def test_unknown_source_is_refused(self):
        with self.assertRaises(TypeError):
            rv.to_report(3)


class TableTests(unittest.TestCase):
    def setUp(self):
        self.buffer = io.StringIO()
        self.console = Console(file=self.buffer, force_terminal=False,
                               no_color=True, width=100)

    def out(self) -> str:
        return self.buffer.getvalue()

    def test_summary_table_carries_task_result_and_badge(self):
        table = plain(rv.summary_table(rv.collect(FinalReport(
            task="fix", success=True, verification_status="VERIFIED", duration_seconds=2.0))))
        self.assertIn("Final Report", table)
        self.assertIn("fix", table)
        self.assertIn("VERIFIED", table)
        self.assertIn("2.0s", table)

    def test_files_table_labels_change_types_and_says_when_there_are_none(self):
        data = rv.collect(session(runs=[green_run()]))
        table = plain(rv.files_table(data))
        self.assertIn("app/views.py", table)
        self.assertIn("created", table)
        empty = plain(rv.files_table(rv.collect(FinalReport(task="t", success=True))))
        self.assertIn("No files were changed", empty)

    def test_files_table_from_a_deleted_and_reverted_event(self):
        s = session(runs=[green_run()], over={"events": [
            {"at": "2026-09-28T10:00:35+00:00", "kind": "removed", "path": "old/calc.py"},
            {"at": "2026-09-28T10:00:36+00:00", "kind": "rolled_back_file", "path": "app/x.py"},
        ]})
        data = rv.collect(s)
        kinds = dict(data.files)
        self.assertEqual(kinds["old/calc.py"], "deleted")
        self.assertEqual(kinds["app/x.py"], "reverted")

    def test_decisions_table_records_approval_and_declines_a_refusal(self):
        s = session(runs=[green_run()], over={"events": [
            {"at": "2026-09-28T10:00:20+00:00", "kind": "approved"},
            {"at": "2026-09-28T10:00:21+00:00", "kind": "rejected_action", "reason": "path outside project"},
        ], "risks": [{"description": "race on counter", "severity": "medium"}]})
        table = plain(rv.decisions_table(rv.collect(s)))
        self.assertIn("proposal approved", table)
        self.assertIn("action refused", table)
        self.assertIn("path outside project", table)
        self.assertIn("race on counter", table)
        self.assertIn("medium", table)

    def test_decisions_table_is_honest_when_nothing_was_recorded(self):
        table = plain(rv.decisions_table(rv.collect(FinalReport(task="t", success=True))))
        self.assertIn("No decisions or remaining risks were recorded", table)

    def test_render_prints_all_three_tables_in_order(self):
        rv.render(session(runs=[green_run()]), console=self.console)
        text = self.out()
        for title in ("Final Report", "Changed Files", "Decisions & Remaining Risks"):
            self.assertIn(title, text)
        self.assertLess(text.index("Final Report"), text.index("Changed Files"))
        self.assertLess(text.index("Changed Files"), text.index("Decisions & Remaining Risks"))

    def test_tables_helper_builds_the_three_without_printing(self):
        built = rv.tables(FinalReport(task="t", success=False, verification_status="INFERRED"))
        self.assertEqual(len(built), 3)
        self.assertIn("INFERRED", plain(built[0]))


if __name__ == "__main__":
    unittest.main()
