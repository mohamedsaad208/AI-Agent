"""The web window's Memory tab: the readout it paints, and the four writes it is allowed to make.

The tab is the third surface for the same memory, after the terminal's `memory` command and the model
that reads the compass, so what is tested here is the promise the release makes: a folder remembers one
thing, in one place, and no surface can say it differently. Two of those promises are the reason the
suite exists at all. A line written from the panel lands in the layer that owns its section — and never
in the goal, which is the one field a run cannot move and a click can, because the click is a person.
The other is quieter: the panel reads three files and rebuilds a block, and it is asked for on the
click, so a window with no folder open, or a chat id that is not a chat id, answers with the reason
rather than an exception the browser would render as a blank tab.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from controller_case import ControllerCase, Scripted
from ai_code_engineer import compass
from ai_code_engineer.memory_store import MemoryStore

CHAT = "c" * 32


def bare_window(case):
    """A window over the same app folder with no project granted to it.

    Built by switching to the chat branch rather than by pointing a window at a path that does not
    exist: a second window opened over one app folder restores the first one's last folder, which is
    the remembered-session behaviour and not something the tests should be fighting.
    """
    controller = Scripted(case.app_dir)
    case.addCleanup(controller.close)
    controller.set_repo("")
    return controller


class PanelReadTests(ControllerCase):
    """What the tab shows, before anybody clicks anything."""

    def test_the_panel_reads_the_folder_the_window_is_standing_in(self):
        store = MemoryStore(self.repo)
        store.set_goal("Ship the reporting dashboard")
        store.add_items("constraints", ["Never rename the package under src"], "project")
        panel = self.controller.memory_panel()
        self.assertTrue(panel["available"])
        self.assertEqual(panel["goal"], "Ship the reporting dashboard")
        self.assertEqual(panel["constraints"], ["Never rename the package under src"])
        self.assertTrue(panel["has_memory"])
        self.assertIn("Project compass", panel["compass"])

    def test_the_panel_names_the_two_files_it_read(self):
        MemoryStore(self.repo).set_goal("Ship the reporting dashboard")
        paths = self.controller.memory_panel()["paths"]
        self.assertTrue(paths["project"].endswith(str(Path(".agent") / "memory" / "project.md")))
        self.assertTrue(paths["chats"].endswith(str(Path(".agent") / "memory" / "chats")))

    def test_a_window_with_no_folder_answers_the_reason_rather_than_a_stack_trace(self):
        panel = bare_window(self).memory_panel()
        self.assertFalse(panel["available"])
        self.assertTrue(panel["why"])

    def test_a_chat_id_that_is_not_one_is_answered_and_not_raised(self):
        """The id arrives from a URL query, so anything can be in it."""
        MemoryStore(self.repo).set_goal("Ship the reporting dashboard")
        panel = self.controller.memory_panel("../../etc/passwd")
        self.assertFalse(panel["available"])
        self.assertTrue(panel["why"])

    def test_the_snapshot_carries_the_answer_that_is_waiting_for_the_operator(self):
        store = MemoryStore(self.repo)
        store.set_goal("Ship the reporting dashboard")
        self.assertEqual(self.controller.snapshot()["memory"]["goal_pending"], "")
        compass.state_goal(store, "Drop the dashboard and rewrite the importer")
        self.assertEqual(self.controller.snapshot()["memory"]["goal_pending"],
                         "Drop the dashboard and rewrite the importer")


class PanelWriteTests(ControllerCase):
    """The four posts the tab makes, through the same answers the terminal's command gives."""

    def events(self):
        rows = []
        return rows, rows.append

    def post(self, type_, payload):
        events, emit = self.events()
        return self.controller.action(type_, payload, emit), events

    def store(self):
        return MemoryStore(self.repo)

    def test_a_line_lands_in_the_layer_that_owns_the_section(self):
        result, _rows = self.post("memory_write", {"section": "constraints", "layer": "project",
                                                   "text": "Never rename the package under src"})
        self.assertEqual(result["written"]["layer"], "project")
        self.assertIn("Never rename the package under src",
                      self.store().read_project().items("constraints"))
        self.assertEqual(result["panel"]["constraints"], ["Never rename the package under src"])

    def test_the_generic_writer_cannot_move_the_goal_and_says_which_command_can(self):
        """The refusal is the whole of goal protection on this surface.

        The tab posts a section name for every line it writes, so `goal` is reachable from the same
        call as `constraints`. It is refused here — not silently routed — and the goal only moves
        through the field that carries a person's approval with it.
        """
        self.store().set_goal("Ship the reporting dashboard")
        result, _rows = self.post("memory_write", {"section": "goal", "layer": "project",
                                                   "text": "Drop the dashboard"})
        self.assertIn("memory edit goal", result["error"])
        self.assertEqual(self.store().goal(), "Ship the reporting dashboard")
        self.assertEqual(result["panel"]["goal"], "Ship the reporting dashboard")

    def test_a_goal_typed_in_the_panel_is_the_approval_the_record_asks_for(self):
        self.store().set_goal("Ship the reporting dashboard")
        result, _rows = self.post("memory_goal", {"text": "Ship the billing dashboard first"})
        self.assertEqual(result["reason"], "approved")
        self.assertEqual(self.store().goal(), "Ship the billing dashboard first")
        self.assertEqual(self.store().read_meta()["goal_source"], "user")
        self.assertEqual(result["panel"]["pending_goal"], "")

    def test_an_empty_goal_leaves_the_stored_one_standing(self):
        self.store().set_goal("Ship the reporting dashboard")
        result, _rows = self.post("memory_goal", {"text": "   "})
        self.assertIn("error", result)
        self.assertEqual(self.store().goal(), "Ship the reporting dashboard")

    def test_a_run_can_only_propose_and_the_panel_holds_the_answer(self):
        store = self.store()
        store.set_goal("Ship the reporting dashboard")
        compass.state_goal(store, "Drop the dashboard and rewrite the importer")
        waiting = self.controller.memory_panel()
        self.assertTrue(waiting["goal_needs_review"])
        self.assertEqual(waiting["pending_goal"], "Drop the dashboard and rewrite the importer")
        self.assertEqual(waiting["goal"], "Ship the reporting dashboard")
        result, _rows = self.post("memory_answer", {"answer": "approve"})
        self.assertEqual(result["reason"], "approved")
        self.assertEqual(self.store().goal(), "Drop the dashboard and rewrite the importer")

    def test_refusing_keeps_the_stored_goal_and_clears_the_ask(self):
        store = self.store()
        store.set_goal("Ship the reporting dashboard")
        compass.state_goal(store, "Drop the dashboard")
        result, _rows = self.post("memory_answer", {"answer": "reject"})
        self.assertEqual(result["reason"], "rejected")
        self.assertEqual(self.store().goal(), "Ship the reporting dashboard")
        self.assertEqual(result["panel"]["pending_goal"], "")

    def test_answering_when_nothing_waits_is_said_rather_than_changing_the_goal(self):
        self.store().set_goal("Ship the reporting dashboard")
        result, _rows = self.post("memory_answer", {"answer": "approve"})
        self.assertEqual(result["reason"], "none")
        self.assertEqual(self.store().goal(), "Ship the reporting dashboard")

    def test_the_two_layers_are_forgotten_apart(self):
        store = self.store()
        store.set_goal("Ship the reporting dashboard")
        store.add_items("constraints", ["Never rename the package"], "project")
        store.set_text("task", "Add the route", "chat", self.controller.chat_id)
        chat, _rows = self.post("memory_reset", {"layer": "chat"})
        self.assertEqual(chat["removed"], ["chat"])
        self.assertTrue(store.project_path().exists())
        self.assertFalse(store.chat_path(self.controller.chat_id).exists())
        project, _rows = self.post("memory_reset", {"layer": "project"})
        self.assertEqual(project["removed"], ["project"])
        self.assertFalse(store.project_path().exists())
        self.assertFalse(store.meta_path().exists())

    def test_the_memory_answers_arrive_in_the_language_the_task_was_asked_in(self):
        """A folder worked in Arabic gets its memory answers in Arabic, in both windows.

        The sentences are the shared table's, and the window names the language the same way it names
        it for every other notice — off the task in front of it — so a reset or a goal refused here is
        not a line the operator has to read in a second language.
        """
        from ai_code_engineer import labels
        self.controller.messages.append({"role": "user",
                                         "text": "أصلح دالة الجمع في calculator.py"})
        self.assertTrue(self.controller.arabic, "the window reads the last thing it was told")
        self.post("memory_reset", {"layer": "chat"})
        self.assertTrue(labels.is_arabic(self.controller.status), self.controller.status)
        self.post("memory_goal", {"text": "شحن لوحة التقارير قبل يوم الخميس"})
        self.assertTrue(labels.is_arabic(self.controller.status), self.controller.status)
        refused, _rows = self.post("memory_write", {"section": "feelings", "layer": "project",
                                                   "text": "a feeling about the code"})
        self.assertTrue(labels.is_arabic(refused["error"]), refused["error"])

    def test_forgetting_sends_back_what_the_memory_said(self):
        """Neither reset can be undone, so the answer carries the record it is about to lose."""
        store = self.store()
        store.add_items("progress", ["applied — Add the route (2 files) — tests passed"], "project")
        result, _rows = self.post("memory_reset", {"layer": "project"})
        self.assertIn("Add the route", str(result["was"]["progress"]))
        self.assertFalse(store.project_path().exists())

    def test_an_unknown_layer_and_a_full_section_are_both_answered_in_the_status_line(self):
        result, _rows = self.post("memory_reset", {"layer": "everything"})
        self.assertIn("project", result["error"])
        for number in range(12):
            self.post("memory_write", {"section": "constraints", "layer": "project",
                                       "text": "rule " + str(number)})
        full, _rows = self.post("memory_write", {"section": "constraints", "layer": "project",
                                                 "text": "one rule too many"})
        self.assertIn("full", full["error"])
        self.assertEqual(len(self.controller.snapshot()["memory"]["goal_pending"]), 0)

    def test_a_write_with_no_folder_open_answers_the_reason(self):
        controller = bare_window(self)
        for type_, payload in (("memory_write", {"section": "constraints", "text": "x"}),
                               ("memory_goal", {"text": "x"}),
                               ("memory_answer", {"answer": "approve"}),
                               ("memory_reset", {"layer": "project"})):
            result = controller.action(type_, payload, lambda row: None)
            self.assertIn("error", result, type_)
            self.assertFalse(result["panel"]["available"], type_)

    def test_the_panel_and_the_command_answer_the_same_file_the_same_way(self):
        """The invariant of the release, said once: two surfaces, one record.

        A click writes the line, the terminal reads it back; the terminal writes one, the panel shows
        it. Neither side is allowed a private idea of which section belongs to which layer.
        """
        from ai_code_engineer import commands as cmd_module
        session = {"id": "abc", "root": str(self.repo), "chat_id": CHAT, "task": "fix it",
                   "state": "APPLIED_VERIFIED", "changes": [], "events": [], "runs": [],
                   "memory": ""}
        self.post("memory_write", {"section": "decisions", "layer": "project",
                                   "text": "Reuse the session table"})
        shown = cmd_module.Commands(session).run("memory")
        self.assertIn("Reuse the session table", shown.text)
        cmd_module.Commands(session).run("memory edit constraints Use the existing fixtures")
        panel = self.controller.memory_panel()
        self.assertEqual(panel["constraints"], ["Use the existing fixtures"])
        self.assertEqual(panel["chat"]["id"], self.controller.chat_id)


if __name__ == "__main__":
    unittest.main()
