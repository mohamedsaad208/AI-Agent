"""Project notes: stored outside the folder the model can write, and sent every task."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import memory
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.workspace import Workspace


class MemoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.store = self.base / ".agent-memory"

    def test_notes_round_trip_and_live_outside_the_project(self):
        path = memory.write(self.store, str(self.repo), "Java 17. Package com.acme.auth.")
        self.assertEqual(memory.read(self.store, str(self.repo)),
                         "Java 17. Package com.acme.auth.")
        self.assertTrue(path.is_relative_to(self.store))
        self.assertFalse(path.is_relative_to(self.repo))

    def test_the_same_folder_written_differently_shares_one_note(self):
        first = memory.key_for(str(self.repo))
        self.assertEqual(first, memory.key_for(str(self.repo) + "\\"))
        self.assertEqual(first, memory.key_for(str(self.repo).upper()))
        self.assertNotEqual(first, memory.key_for(str(self.base / "other")))

    def test_a_note_too_long_is_refused_rather_than_cut(self):
        with self.assertRaises(PolicyError):
            memory.write(self.store, str(self.repo), "x" * (memory.MAX_MEMORY + 1))
        self.assertEqual(memory.read(self.store, str(self.repo)), "")

    def test_emptying_the_box_removes_the_file(self):
        memory.write(self.store, str(self.repo), "keep the endpoint paths")
        memory.write(self.store, str(self.repo), "   \n")
        self.assertFalse(memory.path_for(self.store, str(self.repo)).exists())
        self.assertEqual(list(self.store.glob("*")), [])

    def test_a_saved_note_is_labelled_as_the_users_own_instruction(self):
        block = memory.block("Use records, not dataclasses.")
        self.assertIn("the user's own standing instructions", block)
        self.assertIn("Use records, not dataclasses.", block)
        self.assertEqual(memory.block("   "), "")

    def test_the_model_cannot_read_the_notes_through_the_workspace(self):
        memory.write(self.store, str(self.repo), "secret decision about the schema")
        ws = Workspace(self.repo)
        with self.assertRaises(PolicyError):
            ws.read("../.agent-memory/" + memory.key_for(str(self.repo)) + ".md")

    def test_a_project_named_like_the_store_still_cannot_reach_it(self):
        # Blocking the directory name is what stops a project that happens to sit inside
        # the app folder from being used to edit its own instructions.
        inside = self.store / "repo"
        inside.mkdir(parents=True)
        (inside / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        ws = Workspace(inside)
        with self.assertRaises(PolicyError):
            ws.read("../notes.md")
        with self.assertRaises(PolicyError):
            Workspace(self.store.parent).path(".agent-memory/repo/app.py")

    def test_saving_twice_leaves_no_temporary_files(self):
        memory.write(self.store, str(self.repo), "first")
        memory.write(self.store, str(self.repo), "second")
        self.assertEqual([p.name for p in self.store.glob("*")],
                         [memory.key_for(str(self.repo)) + ".md"])

    def test_no_project_means_no_note_file(self):
        with self.assertRaises(PolicyError):
            memory.path_for(self.store, "  ")

    def test_record_keeps_the_note_and_its_digest_on_the_session(self):
        session = {}
        memory.record(session, "  Java 17.  ")
        self.assertEqual(session["memory"], "Java 17.")
        self.assertEqual(len(session["memory_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
