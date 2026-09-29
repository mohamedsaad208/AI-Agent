"""A failed command becomes evidence for the next reviewable proposal."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import repair, symbols
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


class TheShapeOfAFailure(unittest.TestCase):
    """A round used to send the same slice of output whatever the command had said, so a missing
    tool arrived dressed as a bug and the model went looking for one."""

    def test_each_kind_is_named_from_the_lines_the_tool_printed(self):
        cases = {
            "environment": "bash: mvn: command not found",
            "timeout": "the run was cancelled after 180 seconds",
            "dependency": "[ERROR] Non-resolvable parent POM for com.shop:cart:1.0",
            "syntax": "[ERROR] /src/Main.java:[12,1] class, interface, enum, or record expected",
            "type": "error: cannot find symbol\n  symbol: method total()",
            "assertion": "AssertionError: expected 5 but was 4",
        }
        for kind, tail in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(repair.classify({"status": "failed", "exit_code": 1,
                                                  "tail": tail, "failures": []}), kind)

    def test_an_assertion_about_an_expected_value_is_not_read_as_a_syntax_error(self):
        """Pytest's wording contains "error: expected", and a missing semicolon is not what it means."""
        run = {"status": "failed", "tail": "E AssertionError: expected 5 but was 4", "failures": []}
        self.assertEqual(repair.classify(run), "assertion")

    def test_a_command_that_was_never_found_is_not_a_bug_even_when_it_prints_one(self):
        """First match in a fixed order: the machine lacking the tool is the fact that decides what
        to do next, and an assertion below it is whatever the test suite said about nothing."""
        run = {"status": "failed", "tail": "mvn: command not found\nAssertionError: 1 != 2",
               "failures": []}
        self.assertEqual(repair.classify(run), "environment")

    def test_a_timed_out_run_is_a_timeout_whatever_the_output_says(self):
        for run in ({"status": "timeout", "tail": "compiling"},
                    {"status": "running", "timed_out": True, "tail": "compiling"}):
            with self.subTest(status=run["status"]):
                self.assertEqual(repair.classify(run), "timeout")

    def test_a_run_the_tool_could_not_start_is_an_environment_failure(self):
        self.assertEqual(repair.classify({"status": "unavailable", "tail": ""}), "environment")

    def test_a_run_that_never_failed_has_no_failure_to_place(self):
        """A passing build whose output happens to contain the word "assert" is not an assertion
        failure, and a report line saying "the tool could not place it" would invent a problem."""
        run = {"status": "passed", "exit_code": 0, "tail": "Ran 4 tests\nOK\n", "failures": []}
        self.assertEqual(repair.classify(run), "")

    def test_a_failure_it_cannot_place_says_so_instead_of_guessing(self):
        run = {"status": "failed", "tail": "the build stopped here\nexit 1", "failures": []}
        self.assertEqual(repair.classify(run), "unknown")

    def test_a_python_test_summary_is_an_assertion_not_an_unknown(self):
        """The unittest and pytest summary lines are what actually reach `tail` on a real run."""
        for tail in ("FAILED (failures=1)", "1 failed, 2 passed in 0.31s"):
            with self.subTest(tail=tail):
                self.assertEqual(repair.classify({"status": "failed", "tail": tail,
                                                  "failures": []}), "assertion")


MAVEN_NO_DEP = "\n".join([
    "[INFO] Scanning for projects...",
    "[INFO] Downloading from central: https://repo.maven.apache.org/x.pom",
    "[INFO] Downloading from central: https://repo.maven.apache.org/y.pom",
    "[ERROR] Failed to execute goal on project cart: Could not resolve dependencies for project "
    "com.shop:cart:jar:1.0: com.shop:common:jar:1.0 was not found",
    "[ERROR] -> [Help 1]",
    "[ERROR] Tests run: 0, Failures: 0, Errors: 0, Skipped: 0",
    "[INFO] BUILD FAILURE",
    "[ERROR] at org.apache.maven.lifecycle.internal.MojoExecutor.execute(MojoExecutor.java:210)",
    "[ERROR] at org.apache.maven.cli.MavenCli.main(MavenCli.java:960)",
])


class SendingTheUsefulPart(unittest.TestCase):
    """Two thousand five hundred characters of a Maven stack trace are one line of diagnosis with a
    lot of furniture around it, and a small context window spends itself on the furniture."""

    def maven_run(self):
        return {"status": "failed", "exit_code": 1, "command": "mvn test", "tail": MAVEN_NO_DEP,
                "failures": []}

    def test_the_lines_that_say_so_are_kept_and_the_trace_below_them_is_not(self):
        picked = repair.relevant(self.maven_run(), "dependency")
        self.assertIn("Could not resolve dependencies", picked)
        self.assertIn("BUILD FAILURE", " ".join(picked.split()), "the tool's own summary travels")
        self.assertNotIn("MojoExecutor", picked)
        self.assertNotIn("Scanning for projects", picked)

    def test_a_line_the_tool_printed_twice_is_sent_once(self):
        run = self.maven_run()
        run["tail"] = "\n".join(["[ERROR] boom"] * 8)
        self.assertEqual(repair.relevant(run, "dependency"), "[ERROR] boom")

    def test_output_with_nothing_matching_still_travels_rather_than_becoming_nothing(self):
        run = self.maven_run()
        run["tail"] = "\n".join(f"note {index}" for index in range(40))
        picked = repair.relevant(run, "syntax")
        self.assertEqual(len(picked.splitlines()), 12)
        self.assertEqual(picked.splitlines()[0], "note 28")
        self.assertIn("note 39", picked)

    def test_a_slice_of_only_frames_says_nothing_rather_than_reciting_the_trace(self):
        run = self.maven_run()
        run["tail"] = "\n".join(["\tat org.apache.maven.Mojo.execute(Mojo.java:210)",
                                 '  File "app.py", line 8, in test_add',
                                 "Traceback (most recent call last):"])
        self.assertEqual(repair.relevant(run, "type"), "")

    def test_the_slice_is_bounded(self):
        run = self.maven_run()
        run["tail"] = "\n".join("[ERROR] " + word * 60 for word in MAVEN_NO_DEP.split()[:80])
        self.assertLessEqual(len(repair.relevant(run, "dependency", limit=600)), 600)

    def test_the_evidence_names_the_kind_and_keeps_the_command_that_produced_it(self):
        text = repair.evidence(self.maven_run())
        self.assertIn("What this looks like: dependency", text)
        self.assertIn("The lines that say so:", text)
        self.assertIn("mvn test", text)
        self.assertLessEqual(len(text), 12000)

    def test_an_unplaceable_failure_says_unknown_instead_of_inventing_a_kind(self):
        run = self.maven_run()
        run["tail"] = "the build stopped here"
        self.assertIn("What this looks like: unknown", repair.evidence(run))


def change(path, after, **extra):
    return dict(extra, path=path, after=after)


def round_record(*, label="Python unittest", status="failed", tests=7, failures=3, seconds=1.5,
                 tail=None, changes=None, target="."):
    """One session that ran one command, stored the way the loop stores a round."""
    proof = ({"tests": tests, "failures": failures, "errors": 0, "source": "JUnit XML"}
             if (tests or failures) else None)
    if tail is None:
        tail = "OK (%d tests)" % tests if status == "passed" else "FAILED (failures=%d)" % failures
    return {"id": "session-" + label, "state": "VERIFICATION_FAILED", "created": "2026-09-29T10:00:00",
            "changes": changes if changes is not None else [change("app.py", "answer = 2\n")],
            "runs": [{"label": label, "status": status, "exit_code": 0 if status == "passed" else 1,
                      "seconds": seconds, "failures": [], "tail": tail, "proof": proof,
                      "target": target}]}


class TheLedgerOfRounds(unittest.TestCase):
    """What each attempt actually did, read once and shown in the offer, the stop line and the report."""

    def test_a_round_row_says_what_ran_what_it_cost_and_what_it_looks_like(self):
        rows = repair.attempts([round_record(tests=12, failures=4)])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["label"], "Python unittest")
        self.assertEqual(row["status"], "failed")
        self.assertEqual((row["tests"], row["failures"]), (12, 4))
        self.assertEqual(row["category"], "assertion")
        self.assertEqual(row["files"], 1)
        self.assertEqual(row["state"], "VERIFICATION_FAILED")

    def test_two_runs_of_one_session_are_two_rows_in_the_order_they_happened(self):
        session = round_record()
        session["runs"].append(dict(session["runs"][0], label="Maven test", seconds=9.0))
        rows = repair.attempts([session])
        self.assertEqual([row["label"] for row in rows], ["Python unittest", "Maven test"])

    def test_a_run_without_a_test_count_is_recorded_as_without_a_count(self):
        rows = repair.attempts([round_record(tail="sh: mvn: command not found", tests=0, failures=0)])
        self.assertEqual((rows[0]["tests"], rows[0]["failures"]), (0, 0))
        self.assertEqual(rows[0]["category"], "environment")

    def test_the_timeline_numbers_the_rounds_from_one_and_names_each_result(self):
        block = repair.timeline(repair.attempts([round_record(failures=3), round_record(failures=1)]))
        self.assertIn("Tried so far:", block)
        self.assertEqual(len(block.splitlines()), 3)
        self.assertIn("1. Python unittest: failed (7 tests, 3 failing, 1 file(s) changed", block)
        self.assertIn("2. Python unittest: failed (7 tests, 1 failing", block)
        self.assertIn("looks like assertion", block)

    def test_only_the_last_rounds_are_shown_and_the_numbering_still_tells_the_truth(self):
        rows = repair.attempts([round_record(failures=index) for index in range(1, 6)])
        block = repair.timeline(rows).splitlines()
        self.assertEqual(len(block), 1 + repair.MAX_FIX_ROUNDS)
        self.assertTrue(block[1].startswith("3. "), block[1])

    def test_a_passing_round_is_a_result_rather_than_a_diagnosis(self):
        block = repair.timeline(repair.attempts([round_record(status="passed", failures=0)]))
        self.assertIn("1. Python unittest: passed (7 tests, 1 file(s) changed)", block)
        self.assertNotIn("looks like", block)

    def test_nothing_said_when_nothing_ran(self):
        self.assertEqual(repair.timeline([]), "")
        self.assertEqual(repair.attempts([]), [])

    def test_the_offer_shows_the_rounds_before_asking_for_another_one(self):
        history = repair.attempts([round_record(), round_record()])
        offer = repair.fix_offer("small-model", 3, False, history=history)
        self.assertIn("Fix round 3 of 3", offer)
        self.assertIn("Tried so far:", offer)
        self.assertIn("Nothing is written until you approve it", offer,
                      "the clause the other tests hold the offer to still holds")

    def test_an_offer_with_no_history_is_the_short_question_it_used_to_be(self):
        self.assertNotIn("Tried so far", repair.fix_offer("small-model", 1, False))


class RefusingToRepeatItself(unittest.TestCase):
    """A model asked twice for the same fix often answers with exactly the same diff, and a round
    spent on it is not an attempt — it is the same attempt counted twice."""

    def test_the_digest_is_the_files_rather_than_the_sentence_around_them(self):
        one = [change("a.py", "x = 1\n"), change("b.py", "y = 2\n")]
        same_order = [change("b.py", "y = 2\n"), change("a.py", "x = 1\n")]
        self.assertEqual(repair.change_digest(one), repair.change_digest(same_order))
        self.assertNotEqual(repair.change_digest(one),
                            repair.change_digest([change("a.py", "x = 1\n"),
                                                  change("b.py", "y = 3\n")]))

    def test_emptying_a_file_is_not_the_same_attempt_as_removing_it(self):
        self.assertNotEqual(repair.change_digest([change("a.py", "", delete=True)]),
                            repair.change_digest([change("a.py", "")]))
        self.assertEqual(repair.change_digest([]), repair.change_digest(None))

    def test_a_second_attempt_with_new_words_and_the_old_files_is_recognised(self):
        earlier = [{"id": "one", "changes": [change("app.py", "answer = 2\n")]}]
        self.assertTrue(repair.already_tried(earlier, [change("app.py", "answer = 2\n")]))
        self.assertFalse(repair.already_tried(earlier, [change("app.py", "answer = 3\n")]))
        self.assertFalse(repair.already_tried([], [change("app.py", "answer = 2\n")]))
        self.assertFalse(repair.already_tried(earlier, []),
                         "a proposal with nothing in it is not a repeat of anything")


class StoppingWhenItHasStoppedMoving(unittest.TestCase):
    """When to stop spending a turn is one decision with two reasons, and both windows read it here."""

    def test_the_same_counts_twice_is_said_as_no_progress(self):
        sessions = [round_record(failures=3), round_record(failures=3)]
        self.assertEqual(repair.stalled(sessions),
                         "no progress: 3 failing of 7 tests, the same as the round before")

    def test_a_round_that_made_it_worse_is_said_as_worse_rather_than_as_the_same(self):
        reason = repair.stalled([round_record(failures=1), round_record(failures=5)])
        self.assertIn("worse than the round before", reason)
        self.assertIn("5 failing of 7 tests", reason)

    def test_a_round_that_removed_a_failure_is_not_stalled(self):
        self.assertEqual(repair.stalled([round_record(failures=5), round_record(failures=2)]), "")

    def test_passing_is_not_stalling(self):
        self.assertEqual(repair.stalled([round_record(failures=3),
                                         round_record(status="passed", failures=0)]), "")

    def test_two_different_commands_are_not_a_trend(self):
        self.assertEqual(repair.stalled([round_record(label="Maven test", failures=3),
                                         round_record(label="pytest", failures=3)]), "")

    def test_the_two_rounds_have_to_be_of_the_same_session_shape_to_be_read(self):
        """A round that never produced a duration is not evidence of anything to compare against."""
        self.assertEqual(repair.stalled([round_record(seconds=None), round_record(failures=3)]), "")

    def test_a_failure_with_no_numbers_is_compared_by_its_kind(self):
        """A missing tool never produces a test count, so the numeric rule has nothing to say — and
        silence here would let the loop spend its whole budget trying to fix an absent binary."""
        missing = round_record(tail="sh: mvn: command not found", tests=0, failures=0,
                               status="unavailable")
        reason = repair.stalled([missing, dict(missing)])
        self.assertIn("the same environment failure in the last two rounds", reason)

    def test_one_round_is_not_yet_a_pattern(self):
        self.assertEqual(repair.stalled([round_record()]), "")
        self.assertEqual(repair.stalled([]), "")

    def test_the_budget_is_a_stop_and_says_so_once_it_is_spent(self):
        stopped, reason = repair.should_stop([round_record()], 3)
        self.assertTrue(stopped)
        self.assertEqual(reason, "stopped after 3 fix rounds and the command still fails")

    def test_a_spent_budget_on_a_run_that_passed_does_not_claim_it_failed(self):
        sessions = [round_record(status="passed", failures=0)]
        stopped, reason = repair.should_stop(sessions, 3)
        self.assertTrue(stopped)
        self.assertEqual(reason, "stopped after 3 fix rounds")

    def test_no_reason_to_stop_in_the_middle_of_a_moving_loop(self):
        sessions = [round_record(failures=5), round_record(failures=2)]
        self.assertEqual(repair.should_stop(sessions, 1), (False, ""))

    def test_no_progress_stops_early_even_with_budget_left(self):
        sessions = [round_record(failures=3), round_record(failures=3)]
        stopped, reason = repair.should_stop(sessions, 1)
        self.assertTrue(stopped)
        self.assertIn("no progress", reason)


class BothWindowsShareTheDecision(unittest.TestCase):
    """The loop's judgement and its sentences belong to `repair`; a window may only say what it decided.

    A rule one window follows and the other does not is how the same project came to be repaired three
    rounds in one browser tab and eleven in a Tk window — and the same asymmetry in prose is how one
    stalled loop read as two different problems.
    """

    def windows(self):
        src = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
        return {"Tk": (src / "gui.py").read_text(encoding="utf-8"),
                "web": (src / "webapp" / "controller.py").read_text(encoding="utf-8")}

    def test_both_windows_ask_the_same_five_questions(self):
        for name, source in self.windows().items():
            for call in ("repair.should_stop(", "repair.already_tried(", "repair.classify(",
                         "repair.evidence(", "repair.stop_status("):
                self.assertIn(call, source, name + " decides " + call + " for itself")

    def test_neither_window_keeps_its_own_budget(self):
        """A window may display the bound — the snapshot says "of 3" — but only `repair` may compare
        against it. Two places holding the same number is how one window offered a fourth round."""
        for name, source in self.windows().items():
            self.assertNotIn("MAX_FIX_ROUNDS =", source, name + " defines its own budget")
            self.assertNotIn(">= repair.MAX_FIX_ROUNDS", source, name + " checks the budget itself")

    def test_the_stopping_sentences_are_written_once_in_the_whole_package(self):
        """The count is the point: a second copy is how the two dialogs drifted, and the only durable
        guard is that there is nowhere else to say it."""
        src = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
        shared = (src / "repair.py").read_text(encoding="utf-8")
        for sentence in ("Try a narrower task", "proposed exactly the same files as",
                         "for the smallest change that makes the command pass"):
            self.assertIn(sentence, shared)
            for name, source in self.windows().items():
                self.assertNotIn(sentence, source, name + " writes its own " + sentence)


class OneRoundOfNineModules(unittest.TestCase):
    """In a reactor "the build failed" is not yet a sentence anyone can act on.

    The folder is recorded by `runner.run`, so the loop has to carry it to the model, into the
    timeline, and into the comparison between rounds — or a fix round reads the wrong module and a
    stalled loop is declared across two projects that never shared a build.
    """

    def module_run(self, target="backend/auth-service"):
        return {"recipe": "maven-test", "label": "Maven test", "command": "mvn -B test",
                "status": "failed", "exit_code": 1, "seconds": 30.0, "target": target,
                "proof": {"tests": 12, "failures": 2, "errors": 0, "source": "surefire XML"},
                "failures": ["AssertionError: expected 5 but was 4"],
                "tail": "[ERROR] Tests run: 12, Failures: 2\nBUILD FAILURE"}

    def test_the_evidence_names_the_module_by_its_place_in_the_project(self):
        text = repair.evidence(self.module_run())
        self.assertIn("Folder: backend/auth-service of the opened project", text)
        self.assertNotIn("D:\\", text)
        self.assertNotIn("home", text.lower(), "the record holds the relative path, not the machine")

    def test_a_run_at_the_root_of_one_project_says_nothing_about_a_module(self):
        self.assertNotIn("Folder:", repair.evidence(self.module_run(".")))
        self.assertNotIn("Folder:", repair.evidence(self.module_run("./")))

    def test_the_task_asked_about_carries_the_folder_it_fails_in(self):
        task = repair.fix_task(self.module_run())
        self.assertIn("The Maven test command failed in backend/auth-service.", task)
        self.assertEqual(repair.fix_task(self.module_run("."))
                         .startswith("The Maven test command failed. Read"), True)

    def test_the_timeline_and_the_rows_know_which_module_ran(self):
        rows = repair.attempts([round_record(target="backend/auth-service")])
        self.assertEqual(rows[0]["folder"], "auth-service")
        block = repair.timeline(rows)
        self.assertIn("1. Python unittest in auth-service: failed", block)
        self.assertEqual(repair.attempts([round_record()])[0]["folder"], "")

    def test_the_same_command_in_two_modules_is_not_no_progress(self):
        """Three failures in auth-service and three in product-service are two problems, not a loop
        that has stopped moving — and stopping the second one is how a module never gets fixed."""
        self.assertEqual(repair.stalled([round_record(target="backend/auth-service"),
                                         round_record(target="backend/product-service")]), "")

    def test_the_same_module_failing_the_same_way_twice_still_stalls(self):
        self.assertIn("no progress", repair.stalled([round_record(target="backend/auth-service"),
                                                     round_record(target="backend/auth-service")]))


class TheMapOfAMultiProjectFolder(unittest.TestCase):
    """The repository map has one budget, and a reactor used to spend all of it on module number one."""

    def test_files_are_spread_across_modules_rather_than_taken_in_order(self):
        files = [f"alpha/src/A{i}.java" for i in range(5)] + [f"beta/src/B{i}.java" for i in range(5)]
        ordered = symbols.spread(files)
        self.assertEqual(ordered[:2], ["alpha/src/A0.java", "beta/src/B0.java"])
        self.assertEqual(sorted(ordered), sorted(files))
        self.assertEqual(ordered[2], "alpha/src/A1.java")

    def test_a_single_project_map_keeps_the_order_it_always_had(self):
        files = ["src/a.py", "src/b.py", "src/deep/c.py"]
        self.assertEqual(symbols.spread(files), files)
        self.assertEqual(symbols.module_of("README.md"), ".")

    def test_a_truncated_map_names_the_modules_it_left_out(self):
        rows = [{"path": f"alpha/src/A{i}.java", "kind": "java", "package": "a", "types": [],
                 "functions": [], "imports": []} for i in range(40)]
        files = [row["path"] for row in rows] + [f"beta/src/B{i}.java" for i in range(40)]
        page = symbols.render(rows, files, limit=600)
        self.assertIn("index truncated", page)
        self.assertIn("nothing shown from beta", page,
                      "300 of 1200 files does not say that a whole module is missing")
        spread = symbols.render(rows, files, limit=600, spread_files=True)
        self.assertIn("beta/src/B0.java", spread)
        self.assertIn("alpha/src/A0.java", spread)


if __name__ == "__main__":
    unittest.main()
