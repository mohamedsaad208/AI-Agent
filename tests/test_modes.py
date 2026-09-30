"""The per-folder declaration: who wrote it, what survives, and what a gate reads back.

`modes` exists because the position that decides whether a folder gets written was kept inside a
window's own preference block — a block each window rebuilds from a list of named keys, so the other
window's save deleted it, and a folder with no row left opens on Change. These tests are the proof that
the fact now lives somewhere a second process can be refused by.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import intent, modes
from ai_code_engineer.engine import atomic_json, project_key


class TheDeclaration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name)
        self.folder = self.app / "proj"
        self.folder.mkdir()
        self.other = self.app / "second"
        self.other.mkdir()

    def test_nothing_is_declared_until_somebody_says_so(self):
        self.assertEqual(modes.mode_for(self.app, self.folder), "")
        self.assertFalse(modes.sealed(self.app, self.folder))
        self.assertEqual(modes.listed(self.app), [])

    def test_a_seal_is_written_to_disk_and_read_back_by_a_process_that_never_asked(self):
        modes.declare(self.app, self.folder, intent.READ, by=modes.TERMINAL)
        raw = json.loads((self.app / modes.FILE).read_text(encoding="utf-8"))
        self.assertEqual(raw["modes"][project_key(self.folder)]["mode"], "read")
        self.assertEqual(modes.mode_for(self.app, self.folder), "read")
        self.assertTrue(modes.sealed(self.app, self.folder))

    def test_the_who_and_the_when_are_kept_because_a_refusal_needs_both(self):
        row = modes.declare(self.app, self.folder, intent.READ, by=modes.WEB)
        self.assertEqual(row["by"], "web")
        self.assertRegex(row["at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d$")
        self.assertEqual(modes.row_for(self.app, self.folder), row)

    def test_choosing_change_again_is_how_it_comes_off(self):
        modes.declare(self.app, self.folder, intent.READ, by=modes.DESKTOP)
        modes.declare(self.app, self.folder, intent.CHANGE, by=modes.WEB)
        self.assertFalse(modes.sealed(self.app, self.folder))
        self.assertEqual(modes.mode_for(self.app, self.folder), "change")

    def test_forgetting_leaves_the_folder_like_any_folder_nobody_told(self):
        modes.declare(self.app, self.folder, intent.READ, by=modes.TERMINAL)
        modes.forget(self.app, self.folder)
        self.assertEqual(modes.mode_for(self.app, self.folder), "")
        self.assertEqual(modes.listed(self.app), [])

    def test_each_folder_keeps_its_own_answer(self):
        modes.declare(self.app, self.folder, intent.READ, by=modes.TERMINAL)
        modes.declare(self.app, self.other, intent.CHANGE, by=modes.TERMINAL)
        self.assertTrue(modes.sealed(self.app, self.folder))
        self.assertFalse(modes.sealed(self.app, self.other))
        self.assertEqual(len(modes.listed(self.app)), 2)

    def test_a_folder_naming_nothing_declares_nothing(self):
        """`project_key("")` resolves to the current working directory, and a declaration written there
        would seal whatever folder the process happened to be started in."""
        for empty in ("", None, "   "):
            self.assertEqual(modes.folder_key(empty), "")
            self.assertEqual(modes.declare(self.app, empty, intent.READ, by=modes.TERMINAL), {})
            self.assertFalse(modes.sealed(self.app, empty))

    def test_a_row_nobody_recognises_stays_sealed_rather_than_open(self):
        """The asymmetry the store is built on: a value from a version that used other words costs one
        deliberate click to lift, while answering "nothing was declared" would have let a write through
        into a folder somebody had told this tool to leave alone."""
        for junk in ("ReadOnly", "READ ONLY", 7, None, ""):
            modes.declare(self.app, self.folder, junk, by=modes.TERMINAL)
            self.assertTrue(modes.sealed(self.app, self.folder), junk)

    def test_the_listing_spells_the_folder_the_way_the_file_system_does(self):
        """The key is normalised for matching, which on Windows means lower-cased. A person reading a
        list of their own folders should not be shown a path that no longer looks like theirs."""
        modes.declare(self.app, self.folder, intent.READ, by=modes.TERMINAL)
        row = modes.listed(self.app)[0]
        self.assertEqual(Path(row["path"]).name, "proj")
        self.assertEqual(modes.row_for(self.app, self.folder)["path"], row["path"])

    def test_a_file_nobody_wrote_is_read_as_nothing_being_declared(self):
        (self.app / modes.FILE).write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(modes.mode_for(self.app, self.folder), "")
        self.assertFalse(modes.sealed(self.app, self.folder))

    def test_a_row_that_is_not_a_row_is_read_as_sealed_not_as_absent(self):
        atomic_json(self.app / modes.FILE, {"modes": {project_key(self.folder): "read",
                                                      project_key(self.other): {"mode": 7}}})
        self.assertEqual(modes.mode_for(self.app, self.folder), intent.READ)
        self.assertEqual(modes.mode_for(self.app, self.other), intent.READ)
        self.assertTrue(modes.sealed(self.app, self.folder))
        self.assertTrue(modes.sealed(self.app, self.other))

    def test_the_reader_does_not_reparse_the_file_on_every_gate(self):
        """A write gate asks this on every snapshot and a snapshot goes out on every streamed log line,
        so the cached stat is what keeps the answer cheap — and it still notices a change."""
        modes.declare(self.app, self.folder, intent.CHANGE, by=modes.WEB)
        modes.sealed(self.app, self.folder)      # warm it: one read per changed file is the contract
        reads = []
        real = Path.read_text

        def counted(self_path, *args, **kwargs):
            reads.append(str(self_path))
            return real(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", counted):
            for _ in range(20):
                modes.sealed(self.app, self.folder)
        self.assertEqual(reads, [], "the cache re-read the file on a gate that runs per snapshot")
        modes.declare(self.app, self.folder, intent.READ, by=modes.TERMINAL)
        self.assertTrue(modes.sealed(self.app, self.folder))


class TheRefusalSentence(unittest.TestCase):
    """Which of the two refusals a gate prints, and the one rule that picks it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name)
        self.folder = self.app / "auth-service"
        self.folder.mkdir()

    def test_a_window_holding_the_position_gets_the_badge_s_own_words(self):
        modes.declare(self.app, self.folder, intent.READ, by=modes.WEB)
        text = modes.refusal(self.app, self.folder, "Apply", badge=intent.READ)
        self.assertEqual(text, intent.no_write("Apply"))

    def test_a_seal_that_came_from_elsewhere_names_the_surface_that_wrote_it(self):
        modes.declare(self.app, self.folder, intent.READ, by=modes.TERMINAL)
        text = modes.refusal(self.app, self.folder, "Apply", badge=intent.CHANGE)
        self.assertIn("the command line", text)
        self.assertIn("auth-service", text)
        self.assertNotEqual(text, intent.no_write("Apply"),
                            "an advice to switch the badge would be a lie about a seal it cannot lift")

    def test_a_folder_with_no_declaration_gets_the_badge_words_whatever_the_caller_asked(self):
        self.assertEqual(modes.refusal(self.app, self.folder, "Roll back"), intent.no_write("Roll back"))


if __name__ == "__main__":
    unittest.main()
