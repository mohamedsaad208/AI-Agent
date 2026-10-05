"""Startup banner (Release 5 - Task 5.3).

The first thing an operator should see is who launched, which model answers, and which
position the run takes — plan (read-only) or execute (will write). The banner must carry
exactly those three things, keep the shared glyph vocabulary (○ pending/plan, ● running/
execute), collapse to one line on a narrow screen, and never smuggle colour into a stream
that asked for none. Everything is rendered through ``rich``, so the degradation rules
are the same ones the rest of the CLI already obeys.
"""
import io
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from ai_code_engineer import terminal
from ai_code_engineer.cli_view import (MODE_EXECUTE, MODE_PLAN, PROJECT_NAME,
                                       banner_renderable, startup_banner)


def render(project, model, mode, width=100, **kwargs) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, no_color=True, width=width)
    caps = kwargs.pop("caps", None) or terminal.Capabilities(
        interactive=False, color=False, spinners=False, width=width,
        narrow=width < terminal.NARROW_BELOW)
    startup_banner(project=project, model=model, mode=mode, console=console, caps=caps,
                   env=kwargs.get("env", {}))
    return buffer.getvalue()


class BannerContentTests(unittest.TestCase):
    def test_the_three_named_facts_appear(self):
        out = render("calculator-app", "llama3", MODE_EXECUTE)
        self.assertIn(PROJECT_NAME, out)
        self.assertIn("calculator-app", out)
        self.assertIn("llama3", out)
        self.assertIn(MODE_EXECUTE, out)

    def test_plan_mode_is_the_pending_ring_and_execute_is_the_running_dot(self):
        plan = banner_renderable("app", "m", MODE_PLAN)
        execute = banner_renderable("app", "m", MODE_EXECUTE)
        self.assertIn("\u25cb", plan.plain)   # ○ — nothing will be written
        self.assertIn("\u25cf", execute.plain)  # ● — the run acts

    def test_plan_mode_says_it_writes_nothing_and_execute_says_it_will(self):
        self.assertIn("nothing is written", banner_renderable("a", "m", MODE_PLAN).plain)
        self.assertIn("once approved", banner_renderable("a", "m", MODE_EXECUTE).plain)

    def test_an_unknown_mode_falls_back_to_execute_instead_of_blanking_the_line(self):
        self.assertIn(MODE_EXECUTE, banner_renderable("a", "m", "REVOLUTE").plain)

    def test_missing_facts_still_print_a_readable_placeholder(self):
        out = banner_renderable("", "", MODE_PLAN).plain
        self.assertIn("?", out)


class BannerShapeTests(unittest.TestCase):
    def test_a_wide_screen_gets_the_three_row_shape(self):
        out = render("shop", "qwen", MODE_EXECUTE, width=100)
        rows = [line for line in out.splitlines() if line.strip()]
        self.assertGreaterEqual(len(rows), 4)   # title + project + model + mode

    def test_a_narrow_screen_collapses_to_one_line(self):
        out = render("shop", "qwen", MODE_PLAN, width=60)
        rows = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(rows), 1, out)
        self.assertIn("shop", out)
        self.assertIn("qwen", out)
        self.assertIn(MODE_PLAN, out)


class BannerColourTests(unittest.TestCase):
    def test_a_colour_console_may_paint_the_banner(self):
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        startup_banner(console=console, project="shop", model="qwen", mode=MODE_PLAN,
                       caps=terminal.Capabilities(interactive=True, color=True,
                                                  spinners=True, width=100, narrow=False),
                       env={})
        self.assertIn("\x1b[", buffer.getvalue())

    def test_no_color_strips_the_paint_but_keeps_the_words_and_glyphs(self):
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        startup_banner(console=console, project="shop", model="qwen", mode=MODE_EXECUTE,
                       caps=terminal.Capabilities(interactive=True, color=False,
                                                  spinners=True, width=100, narrow=False),
                       env={"NO_COLOR": "1"})
        out = buffer.getvalue()
        self.assertIn(MODE_EXECUTE, out)
        self.assertIn("\u25cf", out)          # the symbol still says the state
        self.assertNotIn("\x1b[3", out)       # no foreground colours
        self.assertNotIn("\x1b[1;", out)      # no bold either


if __name__ == "__main__":
    unittest.main()
