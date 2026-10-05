"""Tests for the colored diff view and its shared painter (Release 4 - Task 4.1).

Three things are checked here, in the order a reviewer would notice them: that one file's diff is
painted the same way for the CLI and the web rail (three context lines, `a/` and `b/`, A/M/D badge);
that the per-file summary says what the record says and nothing more; and that the colour the
specification asks for actually arrives — while `NO_COLOR` and a piped stdout still get every glyph
and no escape sequence for a colour.
"""
import io
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest

from rich.console import Console

from ai_code_engineer import diff_view as dv
from ai_code_engineer import diff_parse as painter
from ai_code_engineer.events import Event, FileChanged
from ai_code_engineer.webapp import uistate

COLOR_SGR = re.compile(r"\x1b\[(?:[1-3][0-8];\d+;\d+|3[0-7]|4[0-7]|9[0-7]|10[0-7])m")


def plain(renderable, **over) -> str:
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, no_color=True, width=100, **over).print(renderable)
    return buffer.getvalue()


def calc():
    """One modified file: a minus that should have been a plus."""
    return {"path": "app/calc.py",
            "before": "def add(a, b):\n    return a-b\n",
            "after": "def add(a, b):\n    return a+b\n"}


def created():
    return {"path": "app/counters.py", "before": None, "after": "count = 0\n",
            "summary": "Adds the counters module"}


def deleted():
    return {"path": "app/old.py", "before": "gone = True\n", "after": "", "delete": True}


def session(over=None):
    """A session record, shaped the way engine.load_session hands one back."""
    data = {"schema": 1, "id": "abc123", "task": "fix add", "state": "PROPOSED",
            "root": "C:/demo", "model": "test", "events": [],
            "changes": [calc(), created(), deleted()]}
    data.update(over or {})
    return data


class PainterTests(unittest.TestCase):
    def test_three_context_lines_by_default(self):
        before = "".join(f"line {n}\n" for n in range(20))
        after = before.replace("line 10\n", "CHANGED\n")
        lines = painter.diff_lines(before, after, "a.py")
        self.assertEqual(lines[0], "--- a/a.py")
        self.assertEqual(lines[1], "+++ b/a.py")
        self.assertEqual(len([line for line in lines if line.startswith(" line")]), 6)

    def test_context_is_askable_wider_and_narrower(self):
        before, after = "a\nb\nc\nd\ne\nf\n", "a\nb\nc\nd\ne\nX\n"
        # Two headers, one hunk line, and the pair that moved — no context at all.
        self.assertEqual(painter.diff_lines(before, after, "f.py", context=0),
                         ["--- a/f.py", "+++ b/f.py", "@@ -6 +6 @@", "-f", "+X"])
        self.assertGreater(len(painter.diff_lines(before, after, "f.py", context=5)),
                           len(painter.diff_lines(before, after, "f.py", context=1)))

    def test_counts_and_hunks_read_the_painted_lines(self):
        lines = painter.diff_lines(calc()["before"], calc()["after"], calc()["path"])
        self.assertEqual(painter.counts(lines), (1, 1))
        self.assertEqual(painter.hunks(lines), 1)
        self.assertEqual(painter.added(lines), ["    return a+b"])
        self.assertEqual(painter.removed(lines), ["    return a-b"])

    def test_the_badge_letters_the_rail_already_uses(self):
        self.assertEqual(painter.kind_of(calc()), "M")
        self.assertEqual(painter.kind_of(created()), "A")
        self.assertEqual(painter.kind_of(deleted()), "D")
        self.assertEqual(painter.kind_of({"path": "n.py", "change_type": "created"}), "A")
        self.assertEqual(painter.kind_of({"path": "n.py", "before": "", "after": "x"}), "M")

    def test_a_recorded_summary_is_the_caption(self):
        lines = painter.diff_lines("", "count = 0\n", "app/counters.py")
        self.assertEqual(painter.describe(created(), lines), "Adds the counters module")

    def test_without_one_the_names_that_moved_are_captioned(self):
        change = {"path": "app/svc.py", "before": None, "after": "def pay(user):\n    return 1\n"}
        lines = painter.diff_lines("", change["after"], "p")
        self.assertEqual(painter.describe(change, lines), "Added pay")

    def test_nothing_legible_moved_so_the_line_count_speaks(self):
        change = {"path": "notes.txt", "before": "one\n", "after": "two\nthree\n"}
        self.assertEqual(painter.describe(change, painter.diff_lines("one\n", "two\nthree\n", "n")),
                         "Updated file content: 2 lines added, 1 removed")

    def test_the_rail_and_this_view_read_one_painter(self):
        """Not two copies that can drift: the web card and the CLI call the same function."""
        self.assertIs(uistate.diff_lines, painter.diff_lines)
        self.assertIs(uistate.review_files, painter.review_files)
        self.assertEqual(list(uistate.file_view(calc())["diff"]), list(dv.collect([calc()]).files[0].lines))


class CoercionTests(unittest.TestCase):
    def test_a_session_record_is_diffed(self):
        data = dv.to_diff(session())
        self.assertEqual([entry.path for entry in data.files],
                         ["app/calc.py", "app/counters.py", "app/old.py"])
        self.assertEqual((data.added, data.removed), (2, 2))

    def test_a_bare_change_list_is_diffed(self):
        self.assertEqual([entry.kind for entry in dv.to_diff([calc(), created()]).files], ["M", "A"])

    def test_one_file_s_change_on_its_own_is_diffed(self):
        entry = dv.to_diff(calc()).files[0]
        self.assertEqual((entry.path, entry.kind), ("app/calc.py", "M"))
        self.assertTrue(entry.body().startswith("--- a/app/calc.py"))

    def test_a_session_that_changed_nothing_says_so_without_inventing_rows(self):
        self.assertEqual(dv.to_diff({"state": "COMPLETED", "task": "read the file"}).files, [])

    def test_a_file_changed_dataclass_is_diffed(self):
        event = FileChanged(path="app/x.py", change_type="created",
                            diff_summary="Adds the retry counter", lines_added=4, lines_removed=0)
        entry = dv.to_diff(event).files[0]
        self.assertEqual((entry.path, entry.kind, entry.summary), ("app/x.py", "A",
                                                                   "Adds the retry counter"))
        self.assertEqual((entry.added, entry.removed, entry.hunks), (4, 0, 0))

    def test_hunks_in_the_event_are_painted_as_hunks_not_as_a_caption(self):
        text = "--- a/app/x.py\n+++ b/app/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        entry = dv.to_diff(FileChanged(path="app/x.py", diff_summary=text)).files[0]
        self.assertEqual(entry.lines[0], "--- a/app/x.py")
        self.assertEqual((entry.added, entry.removed, entry.hunks), (1, 1, 1))
        self.assertEqual(entry.summary, "")

    def test_the_wire_event_is_diffed(self):
        wire = FileChanged(path="app/y.py", change_type="deleted").to_dict()
        self.assertEqual(dv.to_diff(Event.from_dict(wire)).files[0].kind, "D")
        self.assertEqual(dv.to_diff(wire).files[0].path, "app/y.py")

    def test_an_event_that_carries_no_change_is_refused(self):
        with self.assertRaises(TypeError):
            dv.to_diff(Event(kind="stage_changed", data={"stage": "build_test"}))

    def test_anything_that_is_not_a_change_set_is_refused(self):
        for wrong in (3, "app/calc.py", None):
            with self.assertRaises(TypeError):
                dv.to_diff(wrong)

    def test_a_named_file_narrows_the_view(self):
        data = dv.collect(session(), path="app/old.py")
        self.assertEqual([entry.path for entry in data.files], ["app/old.py"])
        self.assertEqual(dv.collect(session(), path="nope.py").files, [])


class SummaryTests(unittest.TestCase):
    def test_one_row_per_file_with_kind_counts_and_caption(self):
        table = plain(dv.summary_table(dv.collect(session())))
        self.assertIn("Changes", table)
        self.assertIn("app/calc.py", table)
        self.assertIn("M 1h", table)
        self.assertIn("+1", table)
        self.assertIn("-1", table)
        self.assertIn("Adds the counters module", table)

    def test_totals_count_files_and_lines_both_ways(self):
        totals = plain(dv.totals(dv.collect(session())))
        self.assertIn("3 files", totals)
        self.assertIn("+2", totals)
        self.assertIn("-2", totals)

    def test_an_empty_change_set_is_said_not_blanked(self):
        self.assertIn("No files were changed", plain(dv.summary_table(dv.collect({"changes": []}))))


class RenderTests(unittest.TestCase):
    def buffer(self, **over) -> Console:
        self.buffer_obj = io.StringIO()
        return Console(file=self.buffer_obj, width=100, **over)

    def test_the_summary_comes_before_the_hunks(self):
        dv.render(session(), console=self.buffer(force_terminal=False, no_color=True))
        out = self.buffer_obj.getvalue()
        self.assertIn("Changes", out)
        self.assertLess(out.index("Changes"), out.index("--- a/app/calc.py"))
        self.assertIn("+++ b/app/calc.py", out)

    def test_hunks_can_be_printed_without_the_summary(self):
        dv.render(session(), console=self.buffer(force_terminal=False, no_color=True),
                  with_summary=False)
        out = self.buffer_obj.getvalue()
        self.assertNotIn("Changes", out)
        self.assertEqual(len([line for line in out.splitlines() if line.startswith("--- a/")]), 3)

    def test_a_colored_terminal_gets_color(self):
        dv.render(session(), console=self.buffer(force_terminal=True))
        self.assertTrue(COLOR_SGR.search(self.buffer_obj.getvalue()))

    def test_no_color_keeps_every_glyph_and_drops_the_color(self):
        """The letters, signs and hunks survive; no escape sequence names a colour."""
        dv.render(session(), console=self.buffer(force_terminal=True, no_color=True))
        out = self.buffer_obj.getvalue()
        self.assertIn("M", out)
        self.assertIn("+1", out)
        self.assertIn("@@", out)
        self.assertIsNone(COLOR_SGR.search(out))

    def test_control_characters_never_reach_the_terminal(self):
        """Diff content is model and repository text; an escape sequence in it is data, not a command."""
        sneaky = {"path": "a.py", "before": "x\n", "after": "\x1b[31mBYPASSED\x1b[0m\n"}
        body = dv.collect(sneaky).files[0].body()
        self.assertNotIn("\x1b", body)
        self.assertIn("+?[31mBYPASSED?[0m", body)
        dv.render(sneaky, console=self.buffer(force_terminal=True, no_color=True))
        self.assertNotIn("\x1b[31m", self.buffer_obj.getvalue())

    def test_a_change_with_no_textual_difference_says_so(self):
        entry = dv.collect({"changes": [{"path": "a.py", "before": "same\n", "after": "same\n"}]})
        block = plain(dv.diff_block(entry.files[0]))
        self.assertIn("a.py", block)
        self.assertIn("nothing to show", block)

    def test_a_named_file_that_was_not_changed_is_answered_plainly(self):
        dv.render(session(), console=self.buffer(force_terminal=False, no_color=True),
                  path="app/never.py")
        self.assertIn("Nothing in this change set touches app/never.py", self.buffer_obj.getvalue())


class CommandTests(unittest.TestCase):
    """The `agent diff` command, driven the way an operator drives it."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "session.json"
        self.path.write_text(json.dumps(session()), encoding="utf-8")
        self.addCleanup(self.dir.cleanup)

    def run_diff(self, *extra) -> str:
        from ai_code_engineer.cli import main
        buffer = io.StringIO()
        stdout, sys.stdout = sys.stdout, buffer
        try:
            code = main(["diff", str(self.path), *extra])
        finally:
            sys.stdout = stdout
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_the_command_prints_the_summary_then_the_colored_hunks(self):
        out = self.run_diff()
        self.assertIn("Changes", out)
        self.assertIn("--- a/app/calc.py", out)
        self.assertIn("-    return a-b", out)

    def test_one_file_can_be_asked_for(self):
        out = self.run_diff("--file", "app/old.py")
        self.assertIn("app/old.py", out)
        self.assertNotIn("app/calc.py", out)

    def test_context_lines_are_askable(self):
        wide = {"schema": 1, "id": "x", "task": "t", "state": "PROPOSED", "root": "C:/demo",
                "events": [], "changes": [
                    {"path": "b.py", "before": "".join(f"n{i}\n" for i in range(11)),
                     "after": "".join(f"n{i}\n" for i in range(10)) + "CHANGED\n"}]}
        self.path.write_text(json.dumps(wide), encoding="utf-8")
        self.assertEqual(self.run_diff("--context", "1").count(" n"),
                         len([line for line in painter.diff_lines(wide["changes"][0]["before"],
                                                                  wide["changes"][0]["after"],
                                                                  "b.py", context=1)
                              if line.startswith(" n")]))


if __name__ == "__main__":
    unittest.main()
