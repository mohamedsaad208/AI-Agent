"""Non-TTY and NO_COLOR handling (Release 5 - Task 5.2).

The renderer must know when it is talking to a screen, a pipe, or a CI log, and what
that stream can carry: colour, in-place spinners, or plain lines only. These tests pin
each detection rule in :mod:`ai_code_engineer.terminal` and the two consumers that act
on the answer — the live-view decision in ``cli_view`` and the compact banner shape.
"""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console

from ai_code_engineer import terminal
from ai_code_engineer.cli_view import CliView, startup_banner
from ai_code_engineer.terminal import Capabilities


class FakeStream:
    def __init__(self, tty=True, width=None):
        self._tty = tty
        if width is not None:
            self.width = width

    def isatty(self):
        return self._tty


class NoColorTests(unittest.TestCase):
    def test_any_nonempty_no_color_value_drops_colour(self):
        for value in ("1", "true", "please"):
            self.assertTrue(terminal.is_no_color({"NO_COLOR": value}), value)

    def test_an_empty_no_color_is_not_a_request(self):
        self.assertFalse(terminal.is_no_color({"NO_COLOR": ""}))

    def test_force_color_wins_over_no_color(self):
        self.assertFalse(terminal.is_no_color({"NO_COLOR": "1", "FORCE_COLOR": "1"}))

    def test_force_color_zero_does_not_win(self):
        self.assertTrue(terminal.is_no_color({"NO_COLOR": "1", "FORCE_COLOR": "0"}))

    def test_a_dumb_terminal_never_gets_colour(self):
        self.assertTrue(terminal.is_no_color({"TERM": "dumb"}))
        self.assertTrue(terminal.is_no_color({"TERM": "DUMB"}))


class CiDetectionTests(unittest.TestCase):
    def test_the_well_known_ci_variables_are_recognised(self):
        for name in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "TF_BUILD",
                     "CIRCLECI", "TRAVIS", "DRONE"):
            self.assertTrue(terminal.is_ci({name: "true"}), name)

    def test_a_falsy_ci_value_is_not_a_pipeline(self):
        self.assertFalse(terminal.is_ci({"CI": "false"}))
        self.assertFalse(terminal.is_ci({"CI": "0"}))

    def test_an_empty_environment_is_a_desk_not_a_pipeline(self):
        self.assertFalse(terminal.is_ci({}))


class TtyTests(unittest.TestCase):
    def test_both_ends_must_be_a_terminal(self):
        self.assertTrue(terminal.is_tty(FakeStream(True), FakeStream(True)))
        self.assertFalse(terminal.is_tty(FakeStream(False), FakeStream(True)))
        self.assertFalse(terminal.is_tty(FakeStream(True), FakeStream(False)))

    def test_a_stream_that_refuses_the_question_is_not_a_terminal(self):
        class Grumpy:
            def isatty(self):
                raise OSError("closed")
        self.assertFalse(terminal.is_tty(Grumpy(), FakeStream(True)))


class WidthTests(unittest.TestCase):
    def test_columns_env_wins(self):
        self.assertEqual(terminal.terminal_width({"COLUMNS": "42"}, FakeStream()), 42)

    def test_a_garbage_columns_falls_through_to_the_device(self):
        self.assertEqual(terminal.terminal_width({"COLUMNS": "wide"},
                                                 FakeStream(width=120)), 120)

    def test_an_unreachable_device_defaults_to_80(self):
        with patch("shutil.get_terminal_size", side_effect=OSError):
            self.assertEqual(terminal.terminal_width({}), 80)

    def test_narrow_is_under_80(self):
        self.assertLess(terminal.terminal_width({"COLUMNS": "79"}), terminal.NARROW_BELOW)


class CapabilitySnapshotTests(unittest.TestCase):
    def test_a_ci_pty_may_have_colour_but_never_a_spinner(self):
        caps = terminal.capabilities(env={"CI": "true"}, stdin=FakeStream(True),
                                     stdout=FakeStream(True))
        self.assertTrue(caps.interactive)
        self.assertFalse(caps.spinners)

    def test_a_pipe_can_be_colourless_and_spinnerless_at_once(self):
        caps = terminal.capabilities(env={"NO_COLOR": "1"}, stdin=FakeStream(True),
                                     stdout=io.StringIO())
        self.assertFalse(caps.interactive)
        self.assertFalse(caps.color)
        self.assertFalse(caps.spinners)

    def test_a_real_terminal_keeps_everything(self):
        caps = terminal.capabilities(env={}, stdin=FakeStream(True),
                                     stdout=FakeStream(True))
        self.assertEqual(caps, Capabilities(interactive=True, color=True,
                                            spinners=True, width=caps.width,
                                            narrow=caps.width < terminal.NARROW_BELOW))

    def test_a_small_screen_marks_itself_narrow(self):
        caps = terminal.capabilities(env={"COLUMNS": "60"}, stdin=FakeStream(True),
                                     stdout=FakeStream(True))
        self.assertTrue(caps.narrow)
        self.assertEqual(caps.width, 60)


class LiveFollowsCapabilitiesTests(unittest.TestCase):
    """The CliView's in-place redraw is exactly the spinner permission Task 5.2 guards."""

    def test_a_console_terminal_in_ci_streams_lines_instead_of_running_live(self):
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        with patch("ai_code_engineer.cli_view.terminal.capabilities",
                   return_value=Capabilities(interactive=True, color=True,
                                             spinners=False, width=100, narrow=False)):
            view = CliView(console=console)
        self.assertFalse(view.live)

    def test_an_explicit_live_flag_still_overrides_the_detected_answer(self):
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, no_color=True)
        view = CliView(console=console, live=True)
        self.assertTrue(view.live)


class BannerFollowsCapabilitiesTests(unittest.TestCase):
    def test_a_narrow_screen_gets_the_one_line_shape(self):
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, no_color=True, width=60)
        startup_banner(console=console, project="shop", model="llama", mode="PLAN",
                       caps=Capabilities(interactive=False, color=False, spinners=False,
                                         width=60, narrow=True), env={})
        out = buffer.getvalue()
        self.assertIn("shop", out)
        self.assertIn("PLAN", out)
        self.assertEqual(len([line for line in out.splitlines() if line.strip()]), 1, out)

    def test_no_color_reaches_the_banner_as_no_escape_sequences(self):
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=True, width=100)
        startup_banner(console=console, project="shop", model="llama", mode="EXECUTE",
                       caps=Capabilities(interactive=True, color=False, spinners=True,
                                         width=100, narrow=False), env={"NO_COLOR": "1"})
        out = buffer.getvalue()
        self.assertIn("EXECUTE", out)
        self.assertNotIn("\x1b[3", out)


if __name__ == "__main__":
    unittest.main()
