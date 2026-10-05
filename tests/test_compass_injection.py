"""The compass in the prompt: what it says, what it costs, and what it must never break.

A memory the model never reads is a notebook nobody opens, so these tests follow the block all the
way from the two files on disk into the message list a provider is handed — and hold the three
things that make the injection safe rather than merely present. It is labelled with the rank each
part holds, so a recorded progress note is never mistaken for an instruction. It is charged against
the window before it is sent, so a project with a long memory cannot push its own task out of the
prompt. And it is silent on failure: a memory file that cannot be read, and a memory write that
cannot happen, cost a running task nothing.
"""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import compass, context_builder, engine, memory_summarizer as brief
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import apply_proposal, load_session, plan
from ai_code_engineer.memory_store import MemoryDoc, MemoryStore
from ai_code_engineer.workspace import Workspace

CHAT = "b" * 32
PROPOSED = "answer = 2\n"


class Script:
    model = "test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, messages):
        self.prompts.append([dict(item) for item in messages])
        item = self.responses.pop(0)
        return item if isinstance(item, str) else json.dumps(item)


def fill(store, chat_id=CHAT):
    """One project with something worth remembering, and one chat that did the work."""
    store.set_goal("Ship the auth endpoint before the freeze", source="user")
    store.add_items("constraints", ["Never rename the package under src/main"], "project")
    store.add_items("decisions", ["Reuse the session table, no new tokens"], "project")
    store.add_items("open_issues", ["Does the proxy strip the query string?"], "project")
    store.add_items("progress", ["2026-10-01: applied — Add the route (2 files) — untested"],
                    "project")
    store.set_text("task", "Make the login redirect keep the query", "chat", chat_id)
    store.add_items("steps", ["read app/auth.py", "search \"redirect\""], "chat", chat_id)


class TextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_a_project_with_nothing_remembered_sends_no_block_at_all(self):
        self.assertEqual(compass.build(self.store, CHAT), "")

    def test_the_memory_is_said_once_and_said_whole(self):
        fill(self.store)
        block = compass.build(self.store, CHAT)
        for wanted in ("Ship the auth endpoint", "Never rename the package",
                       "Reuse the session table", "proxy strip the query",
                       "Make the login redirect keep the query", "search \"redirect\"",
                       "applied — Add the route"):
            self.assertIn(wanted, block)

    def test_the_block_says_which_parts_bind_and_which_are_only_history(self):
        fill(self.store)
        block = compass.build(self.store, CHAT)
        self.assertIn("Project compass", block)
        self.assertIn("binding", block)
        self.assertIn("not an instruction", block)

    def test_a_chat_with_no_file_of_its_own_contributes_no_chat_lines(self):
        fill(self.store)
        block = compass.build(self.store, "")
        self.assertIn("Ship the auth endpoint", block)
        self.assertNotIn("steps this chat took", block)

    def test_no_room_means_no_block(self):
        fill(self.store)
        self.assertEqual(compass.build(self.store, CHAT, tokens=0), "")

    def test_the_whole_block_respects_the_ceiling_including_its_own_header(self):
        store = MemoryStore(self.root)
        store.set_goal("Ship the auth endpoint")
        store.add_items("progress", ["note " + str(index) for index in range(20)], "project")
        store.add_items("constraints", ["a rule about naming that runs on for a while"],
                        "project")
        block = compass.build(store, CHAT, tokens=200)
        self.assertLessEqual(brief.estimate_tokens(block), 200)
        self.assertIn("Ship the auth endpoint", block)

    def test_a_memory_too_long_for_the_window_gives_up_its_history_and_keeps_its_rules(self):
        fill(self.store)
        self.store.add_items("progress",
                             ["old progress note " + str(index) for index in range(60)],
                             "project")
        block = compass.build(self.store, CHAT, tokens=320)
        self.assertIn("Ship the auth endpoint", block)
        self.assertIn("Never rename the package", block)
        self.assertNotIn("old progress note 0", block)
        self.assertIn("given up to keep", block)

    def test_a_tight_budget_speaks_in_one_sentence_and_still_carries_the_goal(self):
        fill(self.store)
        block = compass.build(self.store, CHAT, tokens=120)
        self.assertIn(compass.SHORT_LABEL.strip()[:20], block)
        self.assertNotIn("Project compass —", block)
        self.assertIn("Ship the auth endpoint", block)
        self.assertLessEqual(brief.estimate_tokens(block), 120)

    def test_a_memory_file_that_cannot_be_read_is_a_missing_memory_not_a_failed_run(self):
        fill(self.store)
        with patch("ai_code_engineer.compass.MemoryStore.read_project",
                   side_effect=engine.PolicyError("unreadable")):
            self.assertEqual(compass.build(self.store, CHAT), "")


class BudgetTests(unittest.TestCase):
    def settings(self, chars):
        return SimpleNamespace(context_chars=chars)

    def test_a_generous_window_is_capped_by_the_token_ceiling_not_by_the_window(self):
        self.assertEqual(context_builder.compass_tokens(self.settings(64000), used_chars=2000),
                         brief.COMPASS_TOKENS)

    def test_a_window_with_almost_no_room_left_is_given_none(self):
        self.assertEqual(context_builder.compass_tokens(self.settings(4000), used_chars=3900), 0)
        self.assertEqual(context_builder.compass_tokens(self.settings(4000), used_chars=8000), 0)

    def test_the_share_of_a_small_window_is_smaller_than_the_ceiling(self):
        tokens = context_builder.compass_tokens(self.settings(4000), used_chars=1000)
        self.assertGreater(tokens, 0)
        self.assertLess(tokens, brief.COMPASS_TOKENS)

    def test_the_ceiling_is_measured_in_the_units_a_local_model_thinks_in(self):
        self.assertEqual(brief.COMPASS_TOKENS, 1500)


class InjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.root = self.app_dir / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.app_dir / ".agent-runs"
        self.store = MemoryStore(self.root)

    def draft(self, script=None, **over):
        script = script or Script([{"action": "read_file", "path": "app.py"},
                                   {"action": "propose", "summary": "Set answer to two",
                                    "checks": ["unit tests"],
                                    "changes": [{"path": "app.py", "content": PROPOSED}]}])
        path = plan(self.ws, "Update answer", script, Settings(), self.runs,
                    progress=lambda _: None, **over)
        return script, path

    def asked(self, script):
        return script.prompts[0][1]["content"]

    def test_the_compass_reaches_the_model(self):
        fill(self.store)
        script, _path = self.draft()
        content = self.asked(script)
        self.assertIn("Project compass", content)
        self.assertIn("Ship the auth endpoint before the freeze", content)
        self.assertIn("Never rename the package under src/main", content)

    def test_a_project_with_no_memory_is_sent_the_prompt_it_was_sent_before(self):
        script, _path = self.draft()
        self.assertNotIn("Project compass", self.asked(script))

    def test_the_users_own_note_outranks_the_compass_which_outranks_the_recorded_history(self):
        from ai_code_engineer import memory as memory_module
        memory_module.write_auto_notes(self.app_dir / ".agent-memory", str(self.root),
                                       {"enabled": True, "purpose_and_stack": "recorded stack"})
        fill(self.store)
        script = Script([{"action": "read_file", "path": "app.py"},
                         {"action": "propose", "summary": "s", "checks": ["c"],
                          "changes": [{"path": "app.py", "content": PROPOSED}]}])
        self.draft(script=script, memory="Always run the Maven build first")
        content = self.asked(script)
        self.assertLess(content.index("Always run the Maven build first"),
                        content.index("Project compass"))
        self.assertLess(content.index("Project compass"),
                        content.index("Auto-Observed Project Summary"))

    def test_the_run_records_the_block_it_was_given(self):
        fill(self.store)
        _script, path = self.draft()
        rows = [row for row in load_session(path)["events"]
                if isinstance(row, dict) and row.get("kind") == "compass_attached"]
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]["characters"], 0)
        self.assertGreater(rows[0]["tokens"], 0)

    def test_a_compass_built_for_the_wrong_chat_never_leaks_another_conversation(self):
        fill(self.store)
        script, _path = self.draft()
        self.assertNotIn("Make the login redirect keep the query", self.asked(script))

    def test_an_applied_run_writes_its_memory_back_into_the_project_layer(self):
        _script, path = self.draft()
        apply_proposal(path, load_session(path)["proposal_hash"])
        project = self.store.read_project()
        self.assertEqual(project.text("goal"), "Update answer")
        self.assertTrue(any("applied" in line for line in project.items("progress")))
        self.assertFalse(self.store.chats_dir.exists(),
                         "a run with no chat of its own writes no chat file")

    def test_a_run_that_belongs_to_a_chat_leaves_that_chats_memory_behind(self):
        _script, path = self.draft(chat_id=CHAT)
        apply_proposal(path, load_session(path)["proposal_hash"])
        chat = self.store.read_chat(CHAT)
        self.assertEqual(chat.text("task"), "Update answer")
        self.assertTrue(chat.items("steps"))

    def test_a_memory_write_that_cannot_happen_costs_the_applied_change_nothing(self):
        _script, path = self.draft()
        with patch("ai_code_engineer.compass.note_run", side_effect=OSError("disk full")):
            written = apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertEqual(written["state"], "APPLIED_UNVERIFIED")
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), PROPOSED)

    def test_a_store_that_cannot_be_opened_sends_the_task_anyway(self):
        fill(self.store)
        with patch("ai_code_engineer.compass.store_for",
                   side_effect=engine.PolicyError("no folder")):
            script, _path = self.draft()
        self.assertNotIn("Project compass", self.asked(script))


class NoteRunTests(unittest.TestCase):
    """What a finished run is allowed to leave in the memory, and what it is not."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def run_row(self, **over):
        row = {"id": "r1", "root": str(self.root), "task": "Carry the query through login",
               "summary": "Pass the redirect query through the login route",
               "state": "APPLIED_UNVERIFIED", "created": "2026-10-05T09:00:00+00:00",
               "chat_id": CHAT, "changes": [{"path": "app/auth.py"}], "runs": [],
               "events": [{"at": "1", "kind": "step", "id": "s1", "action": "read_file",
                           "path": "app/auth.py"}],
               "task_state": {"constraints": ["Never rename the package"],
                              "decisions": ["Reuse the session table"],
                              "open_issues": ["Does the proxy strip it?"]}}
        row.update(over)
        return row

    def test_the_first_run_of_a_project_states_its_goal_because_there_is_nothing_to_protect(self):
        answer = compass.note_run(self.store, self.run_row())
        self.assertEqual(answer["goal"], "opened")
        self.assertEqual(self.store.goal(), "Carry the query through login")

    def test_a_later_run_keeps_the_goal_and_still_says_what_it_changed(self):
        compass.note_run(self.store, self.run_row())
        compass.note_run(self.store, self.run_row(task="Rebuild the admin page",
                                                  summary="Rebuild the admin page header"))
        self.assertEqual(self.store.goal(), "Carry the query through login")
        self.assertEqual(len(self.store.read_project().items("progress")), 2)

    def test_a_run_that_thinks_the_project_is_for_something_else_only_proposes_it(self):
        compass.note_run(self.store, self.run_row())
        compass.note_run(self.store, self.run_row(goal="Ship the billing rewrite"))
        self.assertEqual(self.store.goal(), "Carry the query through login")
        self.assertEqual(brief.pending_goal(self.store), "Ship the billing rewrite")

    def test_the_project_keeps_the_rules_and_the_chat_keeps_the_steps(self):
        compass.note_run(self.store, self.run_row())
        project = self.store.read_project()
        chat = self.store.read_chat(CHAT)
        self.assertIn("Never rename the package", project.items("constraints"))
        self.assertIn("Reuse the session table", project.items("decisions"))
        self.assertIn("Does the proxy strip it?", project.items("open_issues"))
        self.assertEqual(chat.text("task"), "Carry the query through login")
        self.assertTrue(chat.items("steps"))

    def test_a_run_with_no_chat_takes_the_chat_layer_with_it(self):
        compass.note_run(self.store, self.run_row(chat_id=""))
        self.assertFalse(self.store.chats_dir.exists())
        self.assertTrue(self.store.read_project().items("progress"))

    def test_a_memory_that_cannot_be_written_is_a_missing_memory_not_a_failed_task(self):
        with patch("ai_code_engineer.memory_store.MemoryStore.write",
                   side_effect=OSError("disk full")):
            self.assertEqual(compass.note_run(self.store, self.run_row()), {})
        self.assertFalse(self.store.project_path().exists())
        self.assertEqual(self.store.read_project().fields, {})


class ReadoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_one_call_answers_everything_a_panel_shows(self):
        fill(self.store)
        self.store.set_goal("Ship the billing rewrite")
        answer = compass.readout(self.store, CHAT)
        self.assertEqual(answer["goal"], "Ship the billing rewrite")
        self.assertEqual(answer["chat"]["id"], CHAT)
        self.assertEqual(answer["limit"], brief.COMPASS_TOKENS)
        self.assertTrue(answer["has_memory"])
        self.assertIn("# Project memory", answer["project_text"])
        self.assertLessEqual(answer["tokens"], answer["limit"])
        self.assertTrue(answer["paths"]["project"].endswith("project.md"))

    def test_the_pending_proposal_is_said_as_needing_an_answer(self):
        self.store.set_goal("Ship the auth endpoint")
        self.store.note_pending_goal("Ship the billing rewrite")
        answer = compass.readout(self.store)
        self.assertEqual(answer["pending_goal"], "Ship the billing rewrite")
        self.assertTrue(answer["goal_needs_review"])

    def test_an_untouched_project_has_nothing_to_show(self):
        answer = compass.readout(self.store, "")
        self.assertFalse(answer["has_memory"])
        self.assertEqual(answer["compass"], "")
        self.assertEqual(answer["chats"], [])


class SharedEditTests(unittest.TestCase):
    """The edit answers both surfaces ask for: one implementation, so a terminal and a window cannot
    disagree about which layer owns a section, when it is full, or what an approval means."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.store = MemoryStore(self.root)

    def test_a_line_lands_in_the_layer_that_owns_its_section(self):
        answer = compass.edit_section(self.store, "constraints", "Never rename the package")
        self.assertEqual(answer["layer"], "project")
        self.assertIn("- Never rename the package",
                      self.store.project_path().read_text(encoding="utf-8"))
        answer = compass.edit_section(self.store, "task", "Fix the redirect", CHAT)
        self.assertEqual(answer["layer"], "chat")
        self.assertEqual(self.store.read_chat(CHAT).text("task"), "Fix the redirect")

    def test_the_same_line_written_twice_is_still_one_line(self):
        compass.edit_section(self.store, "decisions", "Reuse the session table")
        compass.edit_section(self.store, "decisions", "Reuse the session table")
        self.assertEqual(self.store.read_project().items("decisions"), ["Reuse the session table"])

    def test_an_unknown_section_and_a_goal_are_both_refused_before_anything_is_written(self):
        with self.assertRaises(Exception) as caught:
            compass.edit_section(self.store, "vibes", "good")
        self.assertIn("constraints", str(caught.exception))
        with self.assertRaises(Exception):
            compass.edit_section(self.store, "goal", "Take the goal down")
        self.assertFalse(self.store.project_path().exists())

    def test_a_chat_section_refuses_a_session_that_is_not_part_of_a_chat(self):
        with self.assertRaises(Exception) as caught:
            compass.edit_section(self.store, "steps", "read the file")
        self.assertIn("chat", str(caught.exception))

    def test_a_full_list_says_so_rather_than_dropping_the_oldest_line(self):
        for number in range(12):
            compass.edit_section(self.store, "constraints", "rule " + str(number))
        with self.assertRaises(Exception) as caught:
            compass.edit_section(self.store, "constraints", "one rule too many")
        self.assertIn("full", str(caught.exception))
        self.assertEqual(len(self.store.read_project().items("constraints")), 12)

    def test_a_blank_line_is_refused_rather_than_erasing_a_scalar(self):
        compass.edit_section(self.store, "task", "Fix the redirect", CHAT)
        with self.assertRaises(Exception):
            compass.edit_section(self.store, "task", "   ", CHAT)
        self.assertEqual(self.store.read_chat(CHAT).text("task"), "Fix the redirect")

    def test_a_stated_goal_carries_whether_a_person_approved_it(self):
        first = compass.state_goal(self.store, "Ship the auth endpoint")
        self.assertEqual(first.reason, "opened")
        self.assertEqual(compass.state_goal(self.store, "Ship the billing rewrite").reason,
                         "pending")
        self.assertEqual(self.store.goal(), "Ship the auth endpoint")
        approved = compass.state_goal(self.store, "Ship the billing rewrite", approved=True,
                                      source="user")
        self.assertEqual(approved.reason, "approved")
        self.assertEqual(self.store.goal(), "Ship the billing rewrite")

    def test_answering_the_proposal_moves_it_in_one_direction_or_throws_it_away(self):
        compass.state_goal(self.store, "Ship the auth endpoint")
        compass.state_goal(self.store, "Ship the billing rewrite")
        kept = compass.answer_goal(self.store, False)
        self.assertEqual(kept.reason, "rejected")
        self.assertEqual(self.store.goal(), "Ship the auth endpoint")
        self.assertEqual(compass.readout(self.store)["pending_goal"], "")
        compass.state_goal(self.store, "Ship the billing rewrite")
        taken = compass.answer_goal(self.store, True)
        self.assertEqual(taken.reason, "approved")
        self.assertEqual(self.store.goal(), "Ship the billing rewrite")

    def test_answering_when_nothing_waits_says_so_rather_than_inventing_a_goal(self):
        compass.state_goal(self.store, "Ship the auth endpoint")
        for approve in (True, False):
            outcome = compass.answer_goal(self.store, approve)
            self.assertEqual(outcome.reason, "none")
            self.assertEqual(self.store.goal(), "Ship the auth endpoint")

    def test_reset_returns_what_the_memory_said_before_it_was_cleared(self):
        fill(self.store)
        answer = compass.reset(self.store, "project", CHAT)
        self.assertEqual(answer["was"]["goal"], "Ship the auth endpoint before the freeze")
        self.assertIn("project", answer["removed"])
        self.assertFalse(self.store.project_path().exists())
        self.assertFalse(self.store.meta_path().exists())
        self.assertTrue(self.store.chat_path(CHAT).exists())

    def test_the_two_layers_reset_apart_because_they_undo_different_amounts_of_work(self):
        fill(self.store)
        one = compass.reset(self.store, "chat", CHAT)
        self.assertEqual(one["removed"], ["chat"])
        self.assertEqual(one["said"], "chat")
        self.assertTrue(self.store.project_path().exists())
        fill(self.store)
        both = compass.reset(self.store, "all", CHAT)
        self.assertEqual(sorted(both["removed"]), sorted(["chat", "project"]))
        self.assertEqual(both["said"], "both")
        self.assertFalse(self.store.chat_path(CHAT).exists())
        self.assertFalse(self.store.project_path().exists())
        # A second click on the same button clears nothing, and the answer says so instead of
        # promising a reset that took nothing away.
        self.assertEqual(compass.reset(self.store, "all", CHAT)["said"], "empty")

    def test_reset_names_the_three_targets_and_refuses_the_chat_one_without_a_chat(self):
        with self.assertRaises(Exception) as caught:
            compass.reset(self.store, "everything")
        self.assertIn("project", str(caught.exception))
        with self.assertRaises(Exception) as caught:
            compass.reset(self.store, "chat", "")
        self.assertIn("no chat", str(caught.exception))

    def test_the_sections_are_listed_with_the_room_left_in_each(self):
        fill(self.store)
        listed = {row["name"] + "/" + row["layer"]: row
                  for row in compass.editable_sections(self.store.read_project(),
                                                       self.store.read_chat(CHAT), CHAT)}
        self.assertEqual(listed["constraints/project"]["count"], 1)
        self.assertEqual(listed["constraints/project"]["cap"], 12)
        self.assertEqual(listed["task/chat"]["count"], 1)
        self.assertTrue(listed["task/chat"]["needs_chat"])
        self.assertFalse(listed["goal/project"]["needs_chat"])
        self.assertTrue(listed["goal/project"]["ready"])

    def test_a_panel_without_a_chat_is_told_which_lines_it_cannot_offer(self):
        fill(self.store)
        listed = {row["name"] + "/" + row["layer"]: row
                  for row in compass.editable_sections(self.store.read_project(),
                                                       MemoryDoc("chat"), "")}
        self.assertFalse(listed["steps/chat"]["ready"])
        self.assertTrue(listed["progress/project"]["ready"])

    def test_the_readout_carries_the_sections_a_panel_draws(self):
        fill(self.store)
        answer = compass.readout(self.store, CHAT)
        self.assertEqual([row["name"] for row in answer["sections"]][:2], ["goal", "constraints"])
        self.assertEqual(answer["sections"][-1]["layer"], "chat")


if __name__ == "__main__":
    unittest.main()
