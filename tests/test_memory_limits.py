"""The 1500-token ceiling, and the rule that a goal does not move on its own.

Two failures are being paid for here. The first is a local model whose window is eaten by its own
history: an unbounded compass pushes the task and the repository map out of a 4k context, and the
run answers a question nobody asked. The second is quieter and worse — a model restating the
project's goal until the goal matches what it just did. These tests hold the ceiling in tokens, the
priority that decides what the ceiling gives up, and the four loud ways the goal is allowed to
change.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import memory_summarizer as brief
from ai_code_engineer.memory_store import MemoryStore

CHAT = "a" * 32


def session(**over):
    row = {"id": "run-1", "chat_id": CHAT, "task": "Make the login redirect keep the query",
           "summary": "Carry the redirect query through the login route",
           "state": "APPLIED_UNVERIFIED", "created": "2026-10-05T10:00:00+00:00",
           "changes": [{"path": "app/auth.py"}, {"path": "app/login.py"}],
           "runs": [], "events": [
               {"at": "1", "kind": "step", "id": "s1", "action": "read_file", "path": "app/auth.py"},
               {"at": "2", "kind": "step", "id": "s2", "action": "search_code", "query": "redirect"},
               {"at": "3", "kind": "step", "id": "s3", "action": "read_file", "path": "app/auth.py"},
               {"at": "4", "kind": "stage", "to": "review"}],
           "task_state": {"goal": "Keep the query through login",
                          "constraints": ["Never rename the package under src/main"],
                          "decisions": ["Reuse the existing redirect allow-list"],
                          "open_issues": ["Does the proxy strip the query?"]}}
    row.update(over)
    return row


class TokenEstimateTests(unittest.TestCase):
    def test_the_ceiling_is_the_number_the_specification_named(self):
        self.assertEqual(brief.COMPASS_TOKENS, 1500)

    def test_latin_text_is_counted_at_about_four_characters_a_token(self):
        self.assertEqual(brief.estimate_tokens("a" * 400), 100)
        self.assertEqual(brief.estimate_tokens(""), 0)

    def test_arabic_text_costs_more_than_the_same_number_of_latin_characters(self):
        arabic = "مرحبا بالعالم" * 8
        self.assertGreater(brief.estimate_tokens(arabic), brief.estimate_tokens("a" * len(arabic)))

    def test_a_cjk_character_is_its_own_token(self):
        self.assertEqual(brief.estimate_tokens("漢字" * 5), 10)

    def test_longer_text_is_never_counted_as_cheaper(self):
        short = brief.estimate_tokens("a sentence about the login route")
        long = brief.estimate_tokens("a sentence about the login route, and its tests")
        self.assertGreaterEqual(long, short)

    def test_char_room_is_converted_by_the_same_rule(self):
        self.assertEqual(brief.tokens_for_chars(400), 100)
        self.assertEqual(brief.tokens_for_chars(-8), 0)


class FitTests(unittest.TestCase):
    def test_everything_that_fits_is_kept(self):
        lines = [("goal", "goal: ship the auth endpoint"), ("constraints", "constraints: java 17")]
        answer = brief.fit(lines, brief.COMPASS_TOKENS)
        self.assertEqual(answer.kept, [line for _section, line in lines])
        self.assertFalse(answer.truncated)

    def test_a_budget_too_small_gives_up_progress_before_it_gives_up_a_constraint(self):
        lines = [("progress", "progress: " + "old note " * 40),
                 ("constraints", "constraints: never touch the pom"),
                 ("goal", "goal: ship the auth endpoint")]
        answer = brief.fit(lines, 24)
        self.assertIn("goal: ship the auth endpoint", answer.kept)
        self.assertIn("constraints: never touch the pom", answer.kept)
        self.assertNotIn("progress", answer.sections)
        self.assertEqual(answer.dropped, ["progress"])
        self.assertTrue(answer.truncated)

    def test_a_line_is_kept_whole_or_not_at_all(self):
        long = "decisions: " + ("a decision that runs on and on " * 30)
        answer = brief.fit([("decisions", long)], 10)
        self.assertNotIn(long[:40], answer.text())
        self.assertEqual(answer.kept, [])

    def test_a_goal_too_big_for_the_budget_arrives_cut_and_says_so(self):
        goal = "goal: " + ("the project ships an authentication endpoint " * 30)
        answer = brief.fit([("goal", goal), ("constraints", "constraints: java 17")], 20)
        self.assertTrue(any(line.endswith("…") for line in answer.kept))
        self.assertEqual(answer.sections.count("goal"), 1)
        self.assertLessEqual(answer.tokens, 20)

    def test_a_budget_of_nothing_keeps_nothing(self):
        answer = brief.fit([("goal", "goal: ship it")], 0)
        self.assertEqual(answer.kept, [])

    def test_an_unknown_section_is_the_first_to_go(self):
        answer = brief.fit([("mood", "mood: optimistic " * 20),
                            ("goal", "goal: ship it")], 12)
        self.assertIn("mood", answer.dropped)
        self.assertIn("goal", answer.sections)


class GoalProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_the_first_goal_is_written_and_becomes_the_thing_later_turns_are_measured_against(self):
        answer = brief.record_goal(self.store, "Ship the auth endpoint")
        self.assertTrue(answer.applied)
        self.assertEqual(answer.reason, "opened")
        self.assertEqual(self.store.goal(), "Ship the auth endpoint")

    def test_a_model_may_not_move_the_goal_by_proposing_a_new_one(self):
        brief.record_goal(self.store, "Ship the auth endpoint", source="user")
        answer = brief.record_goal(self.store, "Ship the whole billing rewrite")
        self.assertFalse(answer.applied)
        self.assertEqual(answer.reason, "pending")
        self.assertEqual(self.store.goal(), "Ship the auth endpoint")
        self.assertEqual(brief.pending_goal(self.store), "Ship the whole billing rewrite")
        self.assertIn("Ship the whole billing rewrite", self.store.read_meta()["pending_goal"])
        self.assertNotIn("billing rewrite", self.store.project_path().read_text(encoding="utf-8"))

    def test_approval_is_one_step_and_changes_the_file_it_protects(self):
        brief.record_goal(self.store, "Ship the auth endpoint")
        brief.record_goal(self.store, "Ship the billing rewrite")
        answer = brief.approve_goal(self.store)
        self.assertTrue(answer.applied)
        self.assertEqual(self.store.goal(), "Ship the billing rewrite")
        self.assertEqual(brief.pending_goal(self.store), "")
        self.assertTrue(self.store.goal_is_current())

    def test_refusal_leaves_the_stored_goal_standing(self):
        brief.record_goal(self.store, "Ship the auth endpoint")
        brief.record_goal(self.store, "Ship the billing rewrite")
        answer = brief.reject_goal(self.store)
        self.assertFalse(answer.applied)
        self.assertEqual(self.store.goal(), "Ship the auth endpoint")
        self.assertEqual(brief.pending_goal(self.store), "")

    def test_a_goal_the_user_typed_themselves_needs_no_second_approval(self):
        brief.record_goal(self.store, "Ship the auth endpoint")
        answer = brief.record_goal(self.store, "Ship the billing rewrite", approved=True)
        self.assertTrue(answer.applied)
        self.assertEqual(self.store.goal(), "Ship the billing rewrite")

    def test_only_one_proposal_waits_at_a_time_and_the_newest_is_the_one_shown(self):
        brief.record_goal(self.store, "Ship the auth endpoint")
        brief.record_goal(self.store, "First guess")
        brief.record_goal(self.store, "Second guess")
        self.assertEqual(brief.pending_goal(self.store), "Second guess")

    def test_the_same_goal_written_differently_is_not_a_change(self):
        brief.record_goal(self.store, "Ship the auth endpoint")
        stamp = self.store.project_path().stat().st_mtime_ns
        answer = brief.record_goal(self.store, "  ship   the AUTH endpoint  ")
        self.assertFalse(answer.applied)
        self.assertEqual(answer.reason, "same")
        self.assertEqual(self.store.project_path().stat().st_mtime_ns, stamp)

    def test_an_empty_statement_is_not_a_way_to_erase_a_goal_quietly(self):
        brief.record_goal(self.store, "Ship the auth endpoint")
        answer = brief.record_goal(self.store, "   ")
        self.assertFalse(answer.applied)
        self.assertEqual(answer.reason, "empty")
        self.assertEqual(self.store.goal(), "Ship the auth endpoint")

    def test_a_goal_typed_into_the_file_by_hand_is_the_user_and_the_record_follows_it(self):
        brief.record_goal(self.store, "Ship the auth endpoint")
        project = self.store.project_path()
        project.write_text(project.read_text(encoding="utf-8").replace(
            "Ship the auth endpoint", "Ship the billing rewrite"), encoding="utf-8")
        answer = brief.record_goal(self.store, "Ship something else entirely")
        self.assertEqual(answer.reason, "hand-edit")
        self.assertFalse(answer.applied)
        self.assertEqual(self.store.goal(), "Ship the billing rewrite")
        self.assertTrue(self.store.goal_is_current())
        self.assertEqual(brief.pending_goal(self.store), "")

    def test_nothing_is_waiting_is_said_plainly_rather_than_inventing_a_change(self):
        self.assertEqual(brief.approve_goal(self.store).reason, "none")
        self.assertEqual(brief.reject_goal(self.store).reason, "none")


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_a_run_that_never_ran_a_command_is_remembered_as_untested(self):
        update = brief.memory_update(session())
        line = update["project"]["progress"][0]
        self.assertIn("untested", line)
        self.assertIn("2 files", line)
        self.assertIn("2026-10-05", line)

    def test_the_numbers_the_runner_reported_are_the_ones_remembered(self):
        row = session(runs=[{"status": "passed",
                             "proof": {"tests": 8, "failures": 0, "errors": 0}}])
        self.assertIn("8 tests, 0 failed", brief.memory_update(row)["project"]["progress"][0])

    def test_a_rolled_back_task_is_corrected_rather_than_left_claiming_the_work(self):
        row = session(state="ROLLED_BACK")
        self.assertEqual(brief.status_of(row), "rolled_back")
        self.assertIn("rolled back", brief.memory_update(row)["project"]["progress"][0])

    def test_a_model_claiming_its_tests_passed_does_not_make_the_record_claim_it(self):
        row = session(summary="Done, all tests pass", runs=[{"status": "failed"}])
        line = brief.memory_update(row)["project"]["progress"][0]
        self.assertIn("last command failed", line)

    def test_the_steps_are_the_ones_the_run_announced_and_repeat_lines_are_said_once(self):
        steps = brief.steps_of(session())
        self.assertEqual(len(steps), 2, "the same file read twice is one step")
        self.assertTrue(any("auth" in line for line in steps))

    def test_the_step_list_is_bounded_however_many_turns_the_run_took(self):
        row = session(events=[{"at": str(index), "kind": "step", "id": index,
                               "action": "search_code", "query": "term" + str(index)}
                              for index in range(40)])
        self.assertEqual(len(brief.steps_of(row)), brief.STEP_ITEMS)

    def test_an_event_that_refuses_to_phrase_itself_costs_the_summary_nothing(self):
        row = session(events=[{"at": "1", "kind": "step", "id": "s1", "action": "read_file",
                               "mystery_field": "value"}])
        self.assertEqual(len(brief.steps_of(row)), 1)

    def test_the_project_keeps_what_outlives_the_chat_and_the_chat_keeps_what_does_not(self):
        update = brief.memory_update(session())
        self.assertEqual(sorted(update["project"]),
                         ["constraints", "decisions", "open_issues", "progress"])
        self.assertEqual(update["chat"]["task"], "Make the login redirect keep the query")
        self.assertIn("steps", update["chat"])

    def test_a_run_with_no_chat_of_its_own_leaves_the_chat_layer_alone(self):
        update = brief.memory_update(session(chat_id=""))
        self.assertNotIn("task", update["chat"])
        self.assertIn("progress", update["project"])

    def test_the_state_a_run_carries_is_cut_to_the_number_of_items_a_later_turn_can_read(self):
        row = session(task_state={"decisions": ["d" + str(index) for index in range(30)],
                                  "open_issues": ["o" + str(index) for index in range(30)],
                                  "constraints": []})
        update = brief.memory_update(row)
        self.assertEqual(len(update["project"]["decisions"]), brief.DECISION_ITEMS)
        self.assertEqual(len(update["project"]["open_issues"]), brief.ISSUE_ITEMS)

    def test_a_secret_in_the_task_text_never_reaches_the_update(self):
        row = session(summary="Wire datasource password=hunter01 into the service")
        text = str(brief.memory_update(row))
        self.assertNotIn("hunter01", text)
        self.assertIn("[redacted]", text)


if __name__ == "__main__":
    unittest.main()
