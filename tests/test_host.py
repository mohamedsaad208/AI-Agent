"""The seam both windows share: one Apply question, four verbs, and no window writing its own.

Code-review item 16 counted the drift in the two presentation layers — 52 `self.status.set()` calls
against 72 `self.status =`, 15 `chat_message` against 19 `_add`, 6 `messagebox.*` against 6
`self.confirm`, 7 `events.put` against 13 `_emit` — and recommended four primitives rather than a
rewrite of both windows. The rewrite was refused at the time and stays refused; what is tested here
is the part that had actually diverged: the Apply dialog, which the web window built with
`repair.must_ask()`'s reason and the Tk window built without it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import host

SRC = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
SESSION = {"root": "D:/work/demo2", "state": "WAITING_APPROVAL",
           "changes": [{"path": "a.py", "content": ""}, {"path": "b.py", "content": ""}]}


class TheApplyQuestion(unittest.TestCase):
    def test_it_names_the_files_the_folder_and_the_way_back(self):
        prompt = host.apply_prompt(SESSION)
        self.assertEqual(prompt["title"], "Apply changes")
        self.assertEqual(prompt["ok_label"], "Apply changes")
        self.assertIn("Write 2 file(s) to", prompt["message"])
        self.assertIn("D:/work/demo2", prompt["message"])
        self.assertIn("as long as the files are not edited later", prompt["message"])

    def test_a_removal_is_named_as_one_rather_than_as_a_write(self):
        """D31: "Write 2 file(s)" about a proposal that deletes one of them is the card lying about
        the irreversible half, so the two verbs are counted apart."""
        session = {"root": "D:/work/demo2",
                   "changes": [{"path": "a.py", "before": "1\n", "after": "2\n"},
                               {"path": "b.py", "before": "3\n", "after": None, "delete": True}]}
        self.assertIn("Write 1 and remove 1 file(s) to", host.apply_prompt(session)["message"])

    def test_the_removal_notice_is_the_head_of_the_message_not_an_appended_afterthought(self):
        prompt = host.apply_prompt(SESSION, notice="Removes most of an existing file:\n• a.py\n\n")
        self.assertTrue(prompt["message"].startswith("Removes most of an existing file:"))
        self.assertIn("Write 2 file(s)", prompt["message"])
        self.assertNotIn("\n\n\n", prompt["message"], "the notice already ended in a break")

    def test_the_reason_is_added_only_where_it_would_otherwise_be_the_whole_warning(self):
        """An emptying proposal is both a removal notice and a must-ask case. Saying it twice in one
        dialog reads as two different problems."""
        notice = "Removes most of an existing file:\n• a.py\n"
        once = host.apply_prompt(SESSION, notice=notice, reason=notice.strip())
        self.assertEqual(once["message"].count("Removes most of an existing file"), 1)
        only = host.apply_prompt(SESSION, notice="", reason="The last task stopped part way through.")
        self.assertIn("The last task stopped part way through.", only["message"])
        self.assertEqual(only["warning"], "The last task stopped part way through.",
                         "the reason still reaches the surface that draws warnings")

    def test_the_next_command_is_disclosed_inside_the_same_question(self):
        prompt = host.apply_prompt(SESSION, again="\nIt will then run Maven test again.")
        self.assertIn("run Maven test again", prompt["message"])

    def test_a_session_with_nothing_in_it_still_asks_a_sensible_question(self):
        prompt = host.apply_prompt({})
        self.assertIn("Write 0 file(s)", prompt["message"])
        self.assertIn("to\n", prompt["message"])


class TheSeamHolds(unittest.TestCase):
    """Both windows have to ask the question this module builds, and neither may keep its own copy."""

    def windows(self):
        return {"Tk": (SRC / "gui.py").read_text(encoding="utf-8"),
                "web": (SRC / "webapp" / "controller.py").read_text(encoding="utf-8")}

    def test_both_windows_build_their_apply_dialog_from_the_shared_builder(self):
        for name, source in self.windows().items():
            self.assertIn("host.apply_prompt(", source, name + " writes its own Apply question")

    def test_the_apply_sentence_is_written_once_in_the_whole_package(self):
        """The count is the point: a second copy is how the two dialogs drifted, and the only
        durable guard is that there is nowhere else to say it."""
        found = [name for name, source in self.windows().items() if "file(s) to\\n" in source]
        found += ["fake" if "file(s) to\\n" in (SRC / "webapp" / "fake.py").read_text(encoding="utf-8")
                  else None]
        self.assertEqual([item for item in found if item], [],
                         "a window is assembling the Apply message itself again")
        self.assertIn("file(s) to\\n", (SRC / "host.py").read_text(encoding="utf-8"))

    def test_the_four_verbs_are_named_and_documented(self):
        for verb in ("say", "line", "ask", "stream"):
            method = getattr(host.Host, verb)
            self.assertTrue(callable(method), verb)
            self.assertTrue((method.__doc__ or "").strip(), verb + " has no contract to hold to")

    def test_the_window_contract_is_the_seven_methods_the_server_calls(self):
        """`Host` is presentation, `contract.Controller` is orchestration; the two lists must not be
        allowed to blur into each other, because that is how a window ends up owning a decision."""
        from ai_code_engineer.webapp import contract
        declared = {name for name in dir(contract.Controller) if not name.startswith("_")}
        host_only = {name for name in dir(host.Host) if not name.startswith("_")}
        self.assertEqual(declared & host_only, set())


if __name__ == "__main__":
    unittest.main()
