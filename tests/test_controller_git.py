"""Git folders: the branch that owns a conversation, the commit behind an apply, the restore."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way
from ai_code_engineer import git_integration, memory, runner
from ai_code_engineer.engine import project_key
from ai_code_engineer.errors import PolicyError
from doubles import CALCULATOR_BAD, CALCULATOR_GOOD, OLLAMA_ENTRY, ProposalModel, run_result
from helpers import sandbox_repo
from controller_case import DualModel, Scripted, patched_catalog


class BranchTests(unittest.TestCase):
    """UI 2.8: the branch selected in the sidebar owns the folder, the mode and the context."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        started = (patched_catalog(),)
        for patcher in started:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = DualModel()
        provider = patch("ai_code_engineer.webapp.controller.make_provider", return_value=self.model)
        provider.start()
        self.addCleanup(provider.stop)

    def fresh(self, answers=None):
        """A controller built the way a launch builds one: no folder until one is chosen."""
        controller = Scripted(self.app_dir, answers)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"

        def drain(c=controller):        # a stray background job must not outlive the temp folder
            c.cancel_event.set()
            c.join(10)
        self.addCleanup(drain)
        return controller

    def ask(self, controller, text="HI"):
        controller.start_plan(text)
        controller.join()
        return controller.snapshot()

    def open_folder(self, folder=None):
        """The sidebar gesture: grant a folder. Granting one is a decision to build in it."""
        controller = self.fresh({"directory": str(folder or self.repo)})
        controller.browse()
        controller.join()
        return controller

    def chat_in_folder(self):
        """A project branch switched to prose, which is what a bound chat answers with."""
        controller = self.open_folder()
        controller.set_composer("chat")
        return controller

    def chats(self):
        return sorted((self.app_dir / ".agent-chats").glob("*/chat.json"))

    # ------------------------------ starting apart ------------------------------
    def test_a_new_chat_carries_no_folder_and_answers_a_greeting(self):
        controller = self.open_folder()
        controller.new_chat()
        self.assertEqual(controller.repo, "")
        self.assertEqual(controller.composer, "chat")
        self.ask(controller)
        self.assertTrue(self.model.prose)
        self.assertEqual(self.model.actions, [])
        self.assertIsNone(controller.session)

    def test_a_new_chat_leaves_the_project_in_the_sidebar(self):
        controller = self.open_folder()
        group = controller.snapshot()["projects"][0]["key"]
        controller.new_chat()
        state = controller.snapshot()
        self.assertEqual([g["key"] for g in state["projects"]], [group])

    def test_granting_a_folder_starts_in_change_mode_and_says_so(self):
        controller = self.open_folder()
        self.assertEqual(controller.composer, "change")
        self.assertIn("Working in repo", controller.messages[-1]["text"])
        self.assertIn("until you click Apply", controller.messages[-1]["text"])
        self.assertIn("badge", controller.messages[-1]["text"])

    def test_a_restored_project_keeps_the_mode_it_was_left_in(self):
        first = self.open_folder()
        first.set_composer("chat")
        self.ask(first, "What does add do?")
        reopened = self.fresh()
        self.assertEqual(reopened.repo, str(self.repo))     # the branch comes back
        self.assertEqual(reopened.composer, "chat")         # with the mode the user chose for it
        self.ask(reopened)
        self.assertEqual(self.model.actions, [])

    def test_launch_after_a_standalone_chat_opens_on_nothing(self):
        first = self.open_folder()
        first.new_chat()
        self.assertEqual(self.fresh().repo, "")

    # ------------------------------ a chat in a project -----------------------------
    def test_a_chat_started_inside_a_project_records_that_project(self):
        controller = self.chat_in_folder()
        self.ask(controller, "What does add do?")
        stored = json.loads(self.chats()[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["project"]["path"], str(self.repo))
        leaves = controller.snapshot()["projects"][0]["chats"]
        self.assertEqual([leaf["kind"] for leaf in leaves], ["chat"])
        self.assertEqual(controller.snapshot()["chats"], [])

    def test_a_bound_chat_reads_the_repository_map_and_still_cannot_propose(self):
        controller = self.chat_in_folder()
        self.ask(controller, "What does add do?")
        system = self.model.prose[-1][1][0]["content"]
        self.assertIn("Repository context", system)
        self.assertIn("calculator.py", system)
        self.assertIn("Never emit JSON tool actions", system)
        self.assertIsNone(controller.session)

    def test_a_standalone_chat_sees_no_folder_at_all(self):
        controller = self.fresh()
        self.ask(controller, "What does add do?")
        system = self.model.prose[-1][1][0]["content"]
        self.assertNotIn("Repository context", system)
        self.assertNotIn("calculator.py", system)

    def test_two_chats_in_one_folder_do_not_share_their_history(self):
        controller = self.chat_in_folder()
        self.ask(controller, "Why does subtract show up here?")
        first = controller.chat_id
        controller.new_task()
        self.assertNotEqual(controller.chat_id, first)
        self.ask(controller, "And now?")
        system_and_turns = json.dumps(self.model.prose[-1][1])
        self.assertNotIn("Why does subtract show up here?", system_and_turns)

    # --------------------------------- dropping onto a node ---------------------------------
    def test_dropping_a_chat_on_a_project_binds_it_by_key(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        chat_id = controller.chat_id
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        controller.bind_chat(chat_id, key)
        controller.join()
        stored = json.loads((self.app_dir / ".agent-chats" / chat_id / "chat.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["project"]["key"], key)
        leaves = controller.snapshot()["projects"][0]["chats"]
        self.assertEqual([leaf["id"] for leaf in leaves], [chat_id])
        self.ask(controller, "So what is wrong with add?")
        self.assertEqual(self.model.actions, [], "a bound chat is still a chat")
        self.assertIsNone(controller.session)

    def test_dropping_onto_an_unknown_key_is_refused(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        for forged in ("not-a-key", str(self.repo), "..\\..\\Windows"):
            controller.bind_chat(controller.chat_id, forged)
            self.assertIn("granted", controller.status)
            stored = json.loads(self.chats()[0].read_text(encoding="utf-8"))
            self.assertIsNone(stored["project"])

    def test_a_forged_chat_id_cannot_walk_out_of_the_chat_store(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        controller.bind_chat("../../evil", project_key(str(self.repo)))
        self.assertIn("Only a chat", controller.status)
        self.assertFalse((self.app_dir.parent / "evil").exists())

    def test_detaching_a_chat_keeps_its_turns(self):
        controller = self.fresh()
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        self.ask(controller, "HI")
        controller.bind_chat(controller.chat_id, key)
        controller.join()
        before = json.loads(self.chats()[0].read_text(encoding="utf-8"))["turns"]
        controller.bind_chat(controller.chat_id, "")
        controller.join()
        after = json.loads(self.chats()[0].read_text(encoding="utf-8"))
        self.assertIsNone(after["project"])
        self.assertEqual([turn["content"] for turn in after["turns"]],
                         [turn["content"] for turn in before])
        self.assertIn(controller.chat_id, [chat["id"] for chat in controller.snapshot()["chats"]])

    def test_reopening_a_bound_chat_comes_back_in_chat_mode(self):
        controller = self.fresh()
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        self.ask(controller, "HI")
        chat_id = controller.chat_id
        controller.bind_chat(chat_id, key)
        controller.join()
        controller.set_composer("change")
        controller.open_chat(self.app_dir / ".agent-chats" / chat_id / "chat.json")
        self.assertEqual(controller.composer, "chat")
        self.assertEqual(controller.repo, str(self.repo))

    def armed_project_chat(self, controller):
        """Leave the project in Change with its switch on, then drop the open chat onto it."""
        key = project_key(str(self.repo))
        controller.projects[key] = str(self.repo.resolve())
        controller._select_branch("project", key, composer="change")
        controller.join()
        controller.set_composer("change")
        controller.set_auto_apply(True)
        controller.join()
        controller.new_chat()
        self.ask(controller, "HI")
        chat_id = controller.chat_id
        controller.bind_chat(chat_id, key)
        controller.join()
        return key, chat_id

    def test_a_chat_dropped_on_a_project_left_in_change_cannot_write(self):
        """The drop used to inherit the folder's mode and arm its Auto-Apply switch."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        self.assertTrue(controller.branch.get("bound"), "the branch is still a chat")
        self.assertEqual(controller.composer, "chat")
        self.assertFalse(controller.auto_apply)
        before = (self.repo / "calculator.py").read_text(encoding="utf-8")
        self.ask(controller, "add a logout button to the profile page")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), before,
                         "a bound chat that writes files breaks the one promise it makes")
        self.assertEqual([row for row in controller.asked if row.get("title") == "Apply changes"], [],
                         "and it must not have been offered a write to agree to")
        # Routing a build verb to a proposal is intended for a bound chat; what is not intended is
        # that proposal reaching the disk on its own, so the switch must still be off here.
        self.assertFalse(controller.auto_apply)

    def test_unbinding_and_rebinding_a_project_cannot_arm_the_chat_again(self):
        """The refusal is derived from the branch, not from a flag set once at bind time."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        controller.bind_chat(controller.chat_id, "")      # detach
        controller.join()
        controller.bind_chat(controller.chat_id, key)    # and drop it back on
        controller.join()
        self.assertEqual(controller.composer, "chat")
        self.assertFalse(controller.auto_apply)
        self.assertTrue(controller._auto_pref.get(key), "the project still keeps its own switch")
        controller.new_chat()
        controller.projects[key] = str(self.repo.resolve())
        self.ask(controller, "HI")
        controller.bind_chat(controller.chat_id, key)
        controller.join()
        self.assertEqual(controller.composer, "chat")
        self.assertFalse(controller.auto_apply)
        before = (self.repo / "calculator.py").read_text(encoding="utf-8")
        self.ask(controller, "add a logout button to the profile page")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), before)

    def test_a_bound_chat_leaves_the_projects_preferences_alone(self):
        """Switching mode or the switch here used to rewrite what the project branch remembers."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        controller.set_composer("change")
        self.assertEqual(controller.composer, "chat", "Change is refused while the chat is bound")
        self.assertIn("reads that folder only", controller.status)
        controller.set_auto_apply(False)
        self.assertFalse(controller.auto_apply)
        again = self.fresh()
        again.projects[key] = str(self.repo.resolve())
        again._select_branch("project", key)
        again.join()
        self.assertEqual(again.composer, "change", "the project still remembers Change")
        again.set_auto_apply(True)
        self.assertTrue(again.auto_apply, "and still remembers the switch being on")

    def test_a_bound_chat_never_writes_without_the_click(self):
        """The folder's switch stays off here, so nothing can write itself; the click still works."""
        controller = self.fresh()
        self.ask(controller, "HI")
        key, _ = self.armed_project_chat(controller)
        state = controller.action("apply_block", {"path": "calculator.py", "content": CALCULATOR_GOOD},
                                  lambda event: None)
        controller.join()
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD,
                         "a block click opens a proposal, as it always did")
        self.assertFalse(controller.auto_apply, "and this folder's switch does not reach this chat")
        controller.apply()
        controller.join()
        self.assertEqual([row["title"] for row in controller.asked], ["Apply changes"],
                         "the write is asked for, never assumed")
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD,
                         "and one explicit yes is enough — a bound chat is not a read-only lock")

    # ---------------------------------- Change mode ----------------------------------
    def test_the_badge_switches_one_branch_to_the_reviewed_path(self):
        controller = self.fresh()
        controller.set_repo(str(self.repo))
        controller.set_composer("change")
        controller.start_plan("Fix add in calculator.py")
        controller.join()
        state = controller.snapshot()
        self.assertEqual(state["review"]["state"], "Changes ready for review")
        self.assertEqual(len(self.model.actions), 1)
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD,
                         "a proposal writes nothing until Apply")

    def test_the_chosen_mode_is_remembered_for_that_project_only(self):
        controller = self.fresh()
        controller.set_repo(str(self.repo))
        controller.set_composer("change")
        reopened = self.fresh()
        self.assertEqual(reopened.composer, "change")
        other = self.app_dir / "second"
        other.mkdir()
        reopened.set_repo(str(other))
        reopened.set_composer("chat")
        reopened.projects[project_key(str(self.repo))] = str(self.repo.resolve())
        reopened._select_branch("project", project_key(str(self.repo)))
        self.assertEqual(reopened.composer, "change")

    def test_change_mode_without_a_folder_is_refused(self):
        controller = self.fresh()
        controller.set_composer("change")
        self.assertIn("Choose a project", controller.status)
        self.assertEqual(controller.composer, "chat")

    def test_an_editless_message_in_change_mode_points_at_the_badge(self):
        controller = self.fresh()
        controller.set_repo(str(self.repo))
        controller.set_composer("change")
        blocked = patch("ai_code_engineer.webapp.controller.plan",
                        side_effect=PolicyError("Proposal contains an unchanged file: calculator.py"))
        with blocked:
            controller.start_plan("HI")
            controller.join()
        self.assertIn("Chat mode", controller.status)
        self.assertNotIn("unchanged file", controller.status)

    # --------------------------- reaching another disk ---------------------------
    def test_the_folder_picker_lists_the_mounted_roots_at_a_drive_root(self):
        # `anchor` is the filesystem root whatever the OS calls it: `D:\` here, `/` on Linux, where
        # `drive_roots()` already answers `["/"]`. Hardcoding a drive letter made this test Windows
        # -only and let the POSIX branch go unproven.
        anchor = Path(self.repo).anchor
        listing = self.fresh().list_dir(anchor)
        self.assertIsNone(listing["parent"], "a root folder has no parent to walk up to")
        self.assertIn(anchor, listing["roots"])

    def test_a_typed_path_can_name_a_project_that_does_not_exist_yet(self):
        target = self.app_dir / "spring-project"
        controller = self.fresh({"directory": str(target)})
        controller.new_project()
        self.assertTrue(target.is_dir())
        self.assertEqual(controller.repo, str(target))
        self.assertEqual(controller.composer, "change")

    def test_opening_a_folder_that_is_not_there_is_refused_rather_than_adopted(self):
        controller = self.fresh({"directory": str(self.app_dir / "missing")})
        controller.browse()
        self.assertEqual(controller.repo, "")
        self.assertIn("not available", controller.status)

    # --------------------------------- window prefs ---------------------------------
    def test_the_folded_sidebar_survives_a_restart(self):
        quiet = lambda _event: None
        self.fresh().action("set_collapsed", {"value": True}, quiet)
        self.assertTrue(self.fresh().snapshot()["prefs"]["collapsed"])
        self.fresh().action("set_collapsed", {"value": False}, quiet)
        self.assertFalse(self.fresh().snapshot()["prefs"]["collapsed"])

    # ---------------------------------- project marks ----------------------------------
    def test_a_project_mark_survives_a_restart_and_renders_in_the_tree(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        controller.set_icon(key, "\U0001F680")
        reopened = self.fresh()
        group = reopened.snapshot()["projects"][0]
        self.assertEqual(group["icon"], "\U0001F680")

    def test_the_default_mark_is_not_written_out_as_a_difference(self):
        controller = self.open_folder()
        controller.set_icon(controller.branch["key"], "\U0001F4C1")
        self.assertEqual(controller.icons[controller.branch["key"]], "")

    def test_a_mark_outside_the_palette_is_refused(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        for forged in ("<img src=x onerror=alert(1)>", "📁📁📁📁", ""):
            controller.set_icon(key, forged)
            self.assertIn("offered", controller.status)
            self.assertNotIn(key, controller.icons)
        controller.set_icon("unknown-key", "\U0001F680")
        self.assertIn("granted", controller.status)

    def test_a_registry_of_bare_paths_still_loads_after_the_format_change(self):
        (self.app_dir / ".agent-projects.json").write_text(
            json.dumps({"projects": [str(self.repo)], "ui": {}}), encoding="utf-8")
        reopened = self.fresh()
        self.assertEqual(list(reopened.projects.values()), [str(self.repo.resolve())])
        self.assertEqual(reopened.icons, {})

    def test_the_plus_on_a_project_row_starts_a_chat_bound_to_that_project(self):
        controller = self.open_folder()
        controller.set_composer("change")
        key = controller.branch["key"]
        self.ask(controller, "Fix add in calculator.py")          # a task, not a chat
        controller.new_chat_in(key)
        self.assertEqual(controller.composer, "chat")
        self.assertEqual(controller.branch["key"], key)
        self.assertIsNone(controller.session, "the earlier task is untouched, not replaced")
        self.ask(controller, "What does the filter chain do?")
        stored = json.loads(self.chats()[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["project"]["key"], key)
        kinds = [leaf.get("kind") for leaf in controller.snapshot()["projects"][0]["chats"]]
        self.assertIn("session", kinds, "the project still lists its tasks")
        self.assertIn("chat", kinds, "and now lists its chat")

    def test_a_chat_cannot_be_moved_into_a_project_that_was_never_granted(self):
        controller = self.fresh()
        self.ask(controller, "HI")
        controller.new_chat_in("C:\\Windows")
        self.assertIn("granted", controller.status)

    # ---------------------------- §1 project drawer ----------------------------
    def test_the_drawer_reports_the_root_notes_budget_and_toolchain(self):
        controller = self.chat_in_folder()
        key = controller.branch["key"]
        controller.save_memory("Run the tests before proposing.")
        self.ask(controller, "What does calculator.py do?")
        info = controller.project_info(key)
        self.assertEqual(info["path"], str(self.repo.resolve()))
        self.assertTrue(info["exists"])
        self.assertEqual(info["notes"], "Run the tests before proposing.")
        self.assertTrue(info["notes_file"].endswith(".md"))
        # The notes live beside the app, never inside the folder a proposal can rewrite.
        self.assertNotIn(".agent-memory", str(self.repo))
        self.assertTrue(str(info["notes_dir"]).endswith(".agent-memory"))
        self.assertEqual(info["toolchain"]["detected"][0]["name"], "python-unittest")
        self.assertEqual(info["toolchain"]["timeout"], 600)
        self.assertIn("summary line", info["toolchain"]["proof"])
        budget = info["context"]
        self.assertGreater(budget["map"], 0, "the repository map is measured, not guessed")
        self.assertGreaterEqual(budget["files"], 1, "the index counted the folder")
        self.assertEqual(budget["used"], budget["system"] + budget["context"] + budget["turns"])
        self.assertEqual(budget["remaining"], budget["budget"] - budget["used"])
        self.assertEqual(budget["est_tokens"], budget["used"] // 4)
        self.assertTrue(budget["bound"], "a chat that reads this folder spends its budget")
        self.assertGreater(budget["turns"], 0)

    def test_the_drawer_payload_matches_the_one_the_preview_scripts(self):
        """--fake is the window the interface is reviewed in, so its drawer has to send the same
        fields the real controller does. A missing key reads as a blank there, never as an error."""
        from ai_code_engineer.webapp.fake import PROJECT_INFO
        controller = self.open_folder()
        info = controller.project_info(project_key(self.repo))
        scripted = PROJECT_INFO["demo2"]
        self.assertEqual(set(info), set(scripted), "the preview drawer and the real one differ")
        self.assertEqual(set(info["context"]), set(scripted["context"]))
        self.assertEqual(set(info["toolchain"]), set(scripted["toolchain"]))
        self.assertEqual({frozenset(entry) for entry in info["toolchain"]["detected"]},
                         {frozenset(entry) for entry in scripted["toolchain"]["detected"]})

    def test_the_budget_of_another_project_does_not_count_this_conversation(self):
        controller = self.open_folder()
        other = self.app_dir / "other"
        (other / "src").mkdir(parents=True)
        (other / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
        controller.projects[project_key(str(other))] = str(other.resolve())
        self.ask(controller, "What does calculator.py do?")
        key = project_key(str(other))
        self.assertFalse(controller.project_info(key)["context"]["bound"])
        self.assertEqual(controller.project_info(key)["context"]["turns"], 0)
        self.assertEqual(controller.branch["key"], project_key(str(self.repo)),
                         "opening a drawer must not repoint the window")

    def test_a_missing_folder_is_reported_rather_than_raised(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        (self.repo / "calculator.py").unlink()
        gone = self.app_dir / "gone"
        controller.projects[key] = str(gone)
        info = controller.project_info(key)
        self.assertFalse(info["exists"])
        self.assertEqual(info["context"]["map"], 0)
        self.assertEqual(info["toolchain"]["detected"], [])

    def test_an_unknown_project_key_is_refused(self):
        controller = self.fresh()
        with self.assertRaises(PolicyError):
            controller.project_info("c:\\windows")

    def test_reveal_opens_only_a_granted_folder_with_a_fixed_argv(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        seen = []
        with patch("ai_code_engineer.webapp.controller.subprocess.Popen",
                   side_effect=lambda command, **kwargs: seen.append((command, kwargs))):
            controller.reveal(key)
        self.assertEqual(len(seen), 1)
        command, kwargs = seen[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], "explorer.exe" if os.name == "nt" else command[0])
        self.assertEqual(command[-1], str(self.repo.resolve()))
        self.assertNotIn("shell", kwargs)

    def test_reveal_refuses_a_folder_the_browser_only_named(self):
        controller = self.open_folder()
        with patch("ai_code_engineer.webapp.controller.subprocess.Popen") as popen:
            controller.reveal(str(self.app_dir))          # a real path, never granted
            controller.reveal("../../Windows")
            controller.reveal("")
        popen.assert_not_called()

    def test_reveal_of_a_granted_folder_that_vanished_starts_nothing(self):
        controller = self.open_folder()
        key = controller.branch["key"]
        controller.projects[key] = str(self.app_dir / "not-here")
        with patch("ai_code_engineer.webapp.controller.subprocess.Popen") as popen:
            controller.reveal(key)
        popen.assert_not_called()
        self.assertIn("not on this disk", controller.status)

    # ------------------- UI 3.0 §1: a change verb in a chat branch -------------------
    FIX_TEST_AR = "\u0635\u0644\u062d \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631 \u0627\u0644\u0641\u0627\u0634\u0644"
    HOW_TO_ADD_AR = "\u0643\u064a\u0641 \u0623\u0636\u064a\u0641 \u0632\u0631 \u062e\u0631\u0648\u062c\u061f"

    def test_a_change_verb_in_a_bound_chat_plans_a_proposal_instead_of_an_answer(self):
        controller = self.chat_in_folder()
        controller.start_plan("add a logout button to the profile page")
        controller.join()
        self.assertEqual(self.model.prose, [], "the prose path must not have run")
        self.assertTrue(self.model.actions, "the plan path should have asked for an action")
        self.assertEqual(controller.session["state"], "WAITING_APPROVAL")
        self.assertTrue(any("Switched to Change mode" in entry["text"] for entry in controller.log),
                        "the routing has to be visible somewhere that outlives the job")

    def test_routing_explains_itself_in_the_conversation(self):
        controller = self.chat_in_folder()
        controller.start_plan("fix the failing test")
        controller.join()
        notes = [m["text"] for m in controller.snapshot()["messages"] if m["role"] == "tool"]
        self.assertTrue(any("planned as a proposal instead" in line for line in notes),
                        "a mode that appears to change on its own has to say why")

    def test_the_route_applies_to_that_message_and_is_not_remembered(self):
        controller = self.chat_in_folder()
        controller.start_plan("add a logout button")
        controller.join()
        self.assertEqual(controller.composer, "chat", "the branch is still a chat")
        self.assertEqual(controller._composer_pref[controller.branch["key"]], "chat")
        planned = len(self.model.actions)
        controller.start_plan("thanks")
        controller.join()
        self.assertEqual(len(self.model.actions), planned, "a thank-you must not plan a change")
        self.assertTrue(self.model.prose, "and it should have been answered in prose")
        self.assertIsNone(controller.session)

    def test_an_arabic_change_verb_routes_and_an_arabic_question_does_not(self):
        controller = self.chat_in_folder()
        controller.start_plan(self.FIX_TEST_AR)
        controller.join()
        self.assertTrue(self.model.actions, "slah al-ikhtibar is a request to change files")
        self.assertEqual(self.model.prose, [])
        other = self.chat_in_folder()
        planned = len(self.model.actions)
        other.start_plan(self.HOW_TO_ADD_AR)
        other.join()
        self.assertEqual(len(self.model.actions), planned, "a question about a change is a question")
        self.assertTrue(self.model.prose)

    def test_a_change_verb_in_a_chat_with_no_project_stays_prose(self):
        controller = self.open_folder()
        controller.new_chat()
        self.assertEqual(controller.repo, "")
        controller.start_plan("create a flask app")
        controller.join()
        self.assertTrue(self.model.prose)
        self.assertEqual(self.model.actions, [], "there is no folder to write into")

    def test_a_greeting_in_a_bound_chat_is_still_answered_as_a_greeting(self):
        controller = self.chat_in_folder()
        controller.start_plan("HI")
        controller.join()
        self.assertEqual(len(self.model.prose), 1)
        self.assertEqual(self.model.actions, [])
class CheckpointTests(unittest.TestCase):
    """UI 3.0 §4: an apply inside a git folder leaves a commit the developer can find."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        (self.repo / "notes.md").write_text("work in progress\n", encoding="utf-8", newline="\n")
        self.git("init", "-q")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "add", "--",
                 "calculator.py")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "commit",
                 "-q", "--no-verify", "-m", "first")
        for patcher in (patched_catalog(),
                        patch("ai_code_engineer.webapp.controller.make_provider",
                              return_value=ProposalModel())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)

    def git(self, *args):
        finished = subprocess.run([git_integration.git_program(), *args], cwd=str(self.repo),
                                  env=git_integration.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def apply_a_fix(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()

    def test_an_apply_in_a_repository_is_committed_under_its_own_subject(self):
        before = self.git("rev-parse", "HEAD")
        self.apply_a_fix()
        self.assertEqual(self.git("log", "-1", "--pretty=%s"),
                         "agent: Fix add in calculator.py [session-"
                         + self.controller.session["id"][:12] + "]")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), before)
        self.assertIn("Git checkpoint", str(self.controller.snapshot()["messages"]))

    def test_the_checkpoint_carrys_only_the_files_the_task_wrote(self):
        self.apply_a_fix()
        status = self.git("status", "--porcelain")
        self.assertIn("notes.md", status, "an unrelated file must stay uncommitted")
        self.assertNotIn("calculator.py", status)

    def test_a_plain_folder_gets_no_checkpoint_and_no_message_about_one(self):
        plain = self.app_dir / "plain"
        (plain / "tests").mkdir(parents=True)
        (plain / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        other = Scripted(self.app_dir)
        other.catalogs["Ollama"] = [OLLAMA_ENTRY]
        other.model = "test-local"
        other.set_repo(str(plain))
        self.addCleanup(other.close)
        other.start_plan("Fix add in calculator.py")
        other.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            other.apply()
            other.join()
        self.assertEqual((plain / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)
        self.assertNotIn("checkpoint", str(other.snapshot()["messages"]).lower())
        self.assertFalse((plain / ".git").exists())

    def test_a_checkpoint_that_cannot_be_made_does_not_undo_the_apply(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.webapp.controller.git_integration.checkpoint",
                   return_value={"ok": False, "hash": "", "before": "",
                                 "reason": "git has no user.name configured"}):
            self.controller.apply()
            self.controller.join()
        self.assertEqual((self.repo / "calculator.py").read_text(encoding="utf-8"),
                         CALCULATOR_GOOD, "the write happened, whatever git thinks about it")
        self.assertIn("No git checkpoint: git has no user.name configured",
                      str(self.controller.snapshot()["messages"]))
class TaskBranchTests(unittest.TestCase):
    """The branch chip is the one control that moves HEAD, so it is tested against a real git."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        self.git("init", "-q")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "add", "--",
                 "calculator.py")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "commit",
                 "-q", "--no-verify", "-m", "first")
        self.base = self.head()
        for patcher in (patched_catalog(),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def controller(self, answers=None):
        made = Scripted(self.app_dir, answers)
        made.catalogs["Ollama"] = [OLLAMA_ENTRY]
        made.model = "test-local"
        made.set_repo(str(self.repo))
        self.addCleanup(made.close)
        return made

    def git(self, *args):
        finished = subprocess.run([git_integration.git_program(), *args], cwd=str(self.repo),
                                  env=git_integration.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def head(self):
        return self.git("symbolic-ref", "--short", "HEAD")

    def test_the_chip_starts_a_branch_named_for_the_task_and_records_the_way_back(self):
        controller = self.controller()
        controller._draft = "Add the login endpoint"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        moved = self.head()
        self.assertTrue(moved.startswith("agent/task-add-the-login-endpoint-"), moved)
        self.assertNotEqual(moved, self.base)
        shown = controller.snapshot()["git"]
        self.assertEqual(shown["branch"], moved)
        self.assertEqual(shown["base"], self.base, "the chip has to know what it can go back to")
        self.assertIn("Started git branch " + moved, str(controller.snapshot()["messages"]))

    def test_the_confirmation_names_the_branch_before_anything_moves(self):
        controller = self.controller({"confirm": False})
        controller._draft = "Add the login endpoint"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        self.assertEqual(self.head(), self.base, "a refusal must not move HEAD")
        asked = controller.asked[-1]
        self.assertIn("agent/task-add-the-login-endpoint", asked["title"] + asked["message"])
        self.assertIn(self.base, asked["message"], "it has to say which branch is being left")
        self.assertEqual(controller.snapshot()["git"].get("base", ""), "",
                         "nothing was left, so there is nothing to return to")

    def test_a_second_click_goes_back_to_the_branch_it_left(self):
        controller = self.controller()
        controller._draft = "add b"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        self.assertNotEqual(self.head(), self.base)
        controller.action("git_branch", {"type": "git_branch", "back": self.base},
                          lambda _event: None)
        controller.join()
        self.assertEqual(self.head(), self.base)
        shown = controller.snapshot()["git"]
        self.assertNotIn("base", shown, "back where we started, so the ↩ has nothing to offer")
        self.assertIn("Back on git branch " + self.base, str(controller.snapshot()["messages"]))

    def test_a_folder_that_is_not_a_repository_says_so_and_asks_for_no_confirmation(self):
        plain = self.app_dir / "plain"
        (plain / "tests").mkdir(parents=True)
        controller = self.controller()
        controller.set_repo(str(plain))
        controller._draft = "add b"
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        self.assertEqual(controller.asked, [], "there is nothing to confirm about a plain folder")
        self.assertIn("not a git repository", controller.status)
        self.assertEqual(controller.snapshot()["git"]["branch"], "")

    def test_git_refusing_is_reported_in_gits_words_and_leaves_the_chip_alone(self):
        controller = self.controller()
        controller._draft = "add b"
        with patch("ai_code_engineer.webapp.controller.git_integration.start_task_branch",
                   return_value={"ok": False, "created": False, "branch": "", "from": "",
                                 "reason": "fatal: cannot lock ref 'HEAD'"}):
            controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
            controller.join()
        self.assertEqual(self.head(), self.base)
        self.assertIn("No branch change: fatal: cannot lock ref",
                      str(controller.snapshot()["messages"]))

    def test_an_arabic_task_gets_an_arabic_sentence_about_a_latin_branch_name(self):
        controller = self.controller()
        asked_in = "أضف نقطة تسجيل الدخول"
        # The language rule reads what the user asked in, so the sentence needs a real message from
        # them; the draft is what the branch name is built from when no session exists yet.
        controller._add("user", "You", asked_in)
        controller._draft = asked_in
        controller.action("git_branch", {"type": "git_branch"}, lambda _event: None)
        controller.join()
        moved = self.head()
        self.assertTrue(moved.startswith("agent/task-"), moved)
        self.assertNotIn("أ", moved, "a branch name stays Latin even in an Arabic sentence")
        row = str(controller.snapshot()["messages"])
        self.assertIn("تم إنشاء فرع git باسم " + moved, row)
        self.assertNotIn("Started git branch", row)
class GitRestoreTests(unittest.TestCase):
    """The escalation path: a rollback the session cannot do, git can, for those files only.

    These are live-repository tests on purpose. The whole claim is about bytes on disk — which copy
    survives, and whose does not — and a scripted git would only re-state the code's own assumption.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        (self.repo / "notes.md").write_text("work in progress\n", encoding="utf-8", newline="\n")
        self.git("init", "-q")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "add", "--",
                 "calculator.py", "notes.md")
        self.git("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent", "commit",
                 "-q", "--no-verify", "-m", "first")
        for patcher in (patched_catalog(),
                        patch("ai_code_engineer.webapp.controller.make_provider",
                              return_value=ProposalModel())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = Scripted(self.app_dir)
        self.controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        self.controller.model = "test-local"
        self.controller.set_repo(str(self.repo))
        self.addCleanup(self.controller.close)

    def git(self, *args):
        finished = subprocess.run([git_integration.git_program(), *args], cwd=str(self.repo),
                                  env=git_integration.env(), stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)
        self.assertEqual(finished.returncode, 0, finished.stdout.decode("utf-8", "replace"))
        return finished.stdout.decode("utf-8", "replace").strip()

    def read(self, name="calculator.py"):
        return (self.repo / name).read_text(encoding="utf-8")

    def apply_a_fix(self):
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            self.controller.apply()
            self.controller.join()

    def edit_afterwards(self, body="def add(a, b):\n    return a * b\n"):
        """Someone — the user, another tool — touched the file the task wrote."""
        (self.repo / "calculator.py").write_text(body, encoding="utf-8", newline="\n")
        git_integration.forget()

    def test_a_rollback_blocked_by_a_later_edit_offers_the_git_copy(self):
        self.apply_a_fix()
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        offer = self.controller.snapshot()["git"].get("restore")
        self.assertTrue(offer, "the refusal used to be a dead end with no way out")
        self.assertEqual(offer["paths"], 1)
        self.assertRegex(offer["commit"], r"^[0-9a-f]{4,40}$")
        self.assertIn("Rollback refused", self.controller.status)
        rows = str(self.controller.snapshot()["messages"])
        self.assertIn("git can still put 1 file(s) back to commit " + offer["commit"], rows)

    def test_the_offer_names_the_commit_before_anything_is_replaced(self):
        self.apply_a_fix()
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        commit = self.controller.snapshot()["git"]["restore"]["commit"]
        self.controller.answers["confirm"] = False
        self.controller.git_restore()
        self.controller.join()
        asked = self.controller.asked[-1]
        self.assertIn(commit, asked["message"])
        self.assertIn("1 file(s)", asked["ok"])
        self.assertEqual(self.read(), "def add(a, b):\n    return a * b\n",
                         "a refusal must leave the later edit exactly where it is")
        self.assertIn("restore", self.controller.snapshot()["git"],
                      "the offer stands until it is taken or a new task starts")

    def test_accepting_restores_the_tasks_own_file_and_leaves_the_rest_alone(self):
        self.apply_a_fix()
        (self.repo / "notes.md").write_text("someone else's newer note\n", encoding="utf-8")
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        self.controller.git_restore()
        self.controller.join()
        self.assertEqual(self.read(), CALCULATOR_BAD, "back to the copy from before the task")
        self.assertEqual(self.read("notes.md"), "someone else's newer note\n")
        self.assertNotIn("restore", self.controller.snapshot()["git"], "the offer was taken")
        rows = str(self.controller.snapshot()["messages"])
        self.assertIn("Restored 1 file(s) from commit", rows)

    def test_a_new_task_clears_an_offer_left_by_the_last_one(self):
        self.apply_a_fix()
        self.edit_afterwards()
        self.controller.undo()
        self.controller.join()
        self.assertIn("restore", self.controller.snapshot()["git"])
        self.controller.start_plan("Something else entirely")
        self.controller.join()
        self.assertNotIn("restore", self.controller.snapshot()["git"],
                         "the previous task's commit is the wrong answer for this one")

    def test_a_folder_with_no_checkpoint_keeps_the_old_refusal(self):
        """No commit means no honest "before", so the escalation must not appear at all."""
        plain = self.app_dir / "plain"
        (plain / "tests").mkdir(parents=True)
        (plain / "calculator.py").write_text(CALCULATOR_BAD, encoding="utf-8", newline="\n")
        other = Scripted(self.app_dir)
        other.catalogs["Ollama"] = [OLLAMA_ENTRY]
        other.model = "test-local"
        other.set_repo(str(plain))
        self.addCleanup(other.close)
        other.start_plan("Fix add in calculator.py")
        other.join()
        with patch("ai_code_engineer.runner.run", return_value=run_result()):
            other.apply()
            other.join()
        (plain / "calculator.py").write_text("def add(a, b):\n    return a * b\n",
                                             encoding="utf-8", newline="\n")
        git_integration.forget()
        other.undo()
        other.join()
        self.assertEqual(other.snapshot()["git"].get("restore"), None)
        self.assertIn("changed after review", str(other.snapshot()["messages"]).lower())


if __name__ == "__main__":
    unittest.main()
