"""Keyboard shortcuts over the CLI prompts (Release 5 - Task 5.1).

Three keystrokes carry the whole shortcut layer: Esc decides against the pending proposal
(the stop the window's Stop button answers to), Tab flips the details between the plan view
and the diff, and `?` prints the shortcuts with the twelve unified commands. prompt_toolkit
owns the raw key reading; this file pins the binding table, the dispatch through the real
prompt_toolkit handlers, the token loop inside `one_key_approval`, and the degradation to
plain input when the library or the terminal is missing — the same actions, reachable from
both surfaces, one key each.
"""
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompt_toolkit.keys import Keys

from ai_code_engineer import commands as unified
from ai_code_engineer.cli import (HELP, STOP, TOGGLE, approval_prompt, build_key_bindings,
                                  one_key_approval, shortcut_help)


class FakeApp:
    def __init__(self):
        self.result = None
        self.exited = False

    def exit(self, result=None):
        self.exited = True
        self.result = result

    def invalidate(self):
        pass


def fire(kb, key):
    """Press one key through the real binding objects prompt_toolkit holds."""
    wanted = ("?",) if key == "?" else (key,)
    for binding in kb.bindings:
        if tuple(binding.keys) == wanted:
            app = FakeApp()
            binding.call(SimpleNamespace(app=app))
            return app
    raise AssertionError("no binding for " + repr(key))


class BindingTableTests(unittest.TestCase):
    def setUp(self):
        self.fired = []
        self.kb = build_key_bindings(on_stop=lambda: self.fired.append(STOP),
                                     on_toggle=lambda: self.fired.append(TOGGLE),
                                     on_help=lambda: self.fired.append(HELP))

    def test_the_three_shortcuts_are_bound_to_their_keys(self):
        keys = {tuple(b.keys) for b in self.kb.bindings}
        self.assertEqual(keys, {(Keys.Escape,), (Keys.Tab,), ("?",)}, keys)

    def test_escape_is_eager_so_a_lone_press_answers_at_once(self):
        for b in self.kb.bindings:
            if b.keys == (Keys.Escape,):
                self.assertTrue(b.eager() if callable(b.eager) else b.eager)
            else:
                self.assertFalse(b.eager() if callable(b.eager) else b.eager)

    def test_esc_stops_and_ends_the_prompt_with_the_stop_token(self):
        app = fire(self.kb, Keys.Escape)
        self.assertEqual((app.result, self.fired), (STOP, [STOP]))

    def test_tab_toggles_and_ends_the_prompt_with_the_toggle_token(self):
        app = fire(self.kb, Keys.Tab)
        self.assertEqual((app.result, self.fired), (TOGGLE, [TOGGLE]))

    def test_question_mark_helps_and_ends_the_prompt_with_the_help_token(self):
        app = fire(self.kb, "?")
        self.assertEqual((app.result, self.fired), (HELP, [HELP]))

    def test_a_shortcut_without_a_callback_still_ends_with_its_token(self):
        app = fire(build_key_bindings(), Keys.Escape)
        self.assertEqual(app.result, STOP)

    def test_without_prompt_toolkit_there_are_no_bindings_to_install(self):
        with patch.dict(sys.modules, {"prompt_toolkit": None,
                                      "prompt_toolkit.key_binding": None}):
            self.assertIsNone(build_key_bindings())


class WebParityTests(unittest.TestCase):
    """The keys must name decisions the shared command layer already answers."""

    def test_stop_and_help_are_unified_commands_with_the_same_names(self):
        self.assertIn(STOP, unified.COMMANDS)
        self.assertIn(HELP, unified.COMMANDS)

    def test_the_help_block_names_the_actions_the_window_also_offers(self):
        text = shortcut_help()
        self.assertIn("Esc", text)
        self.assertIn("Tab", text)
        self.assertIn("stop", text)
        self.assertIn("toggle", text)
        self.assertIn("window", text)


class ApprovalPromptTests(unittest.TestCase):
    def test_an_injected_reader_answers_verbatim(self):
        prompt = approval_prompt(ask=lambda text: "y")
        self.assertEqual(prompt("go? "), "y")

    def test_an_eof_reads_as_the_empty_answer_not_a_traceback(self):
        def dying(text):
            raise EOFError
        self.assertEqual(approval_prompt(ask=dying)("go? "), "")

    def test_a_pipe_gets_plain_input_and_no_prompt_session(self):
        with patch("ai_code_engineer.cli.terminal.is_tty", return_value=False), \
             patch("builtins.input", return_value="n") as typed:
            self.assertEqual(approval_prompt()("go? "), "n")
        typed.assert_called_once_with("go? ")


class ApprovalLoopTests(unittest.TestCase):
    """The token loop: every shortcut decides or displays, and none writes anything."""

    def run_answers(self, answers, **kwargs):
        out = io.StringIO()
        queue = iter(answers)
        with redirect_stdout(out):
            decision = one_key_approval("DIFF TEXT", ask=lambda *_: next(queue), **kwargs)
        return decision, out.getvalue()

    def test_escape_decides_exactly_as_n_does(self):
        self.assertEqual(self.run_answers([STOP])[0], "reject")

    def test_tab_shows_the_plan_then_the_diff_then_still_lets_y_approve(self):
        decision, text = self.run_answers([TOGGLE, TOGGLE, "y"], plan_text="PLAN TEXT")
        self.assertEqual(decision, "approve")
        self.assertEqual(text.count("PLAN TEXT"), 1)
        self.assertEqual(text.count("DIFF TEXT"), 1)   # back to the diff on the second press

    def test_toggle_without_a_plan_view_says_so_and_asks_again(self):
        decision, text = self.run_answers([TOGGLE, "y"])
        self.assertEqual(decision, "approve")
        self.assertIn("no plan view", text)

    def test_help_prints_the_shortcuts_and_the_twelve_then_asks_again(self):
        decision, text = self.run_answers([HELP, "n"])
        self.assertEqual(decision, "reject")
        self.assertIn("Keyboard shortcuts", text)
        for name in unified.COMMANDS:
            self.assertIn(name, text)

    def test_details_prints_the_view_last_shown(self):
        decision, text = self.run_answers([TOGGLE, "d", "y"], plan_text="PLAN TEXT")
        self.assertEqual(decision, "approve")
        self.assertEqual(text.count("PLAN TEXT"), 2)   # toggle showed it, d repeated it

    def test_an_unknown_key_still_answers_the_plain_way(self):
        decision, text = self.run_answers(["nonsense", "y"])
        self.assertEqual(decision, "approve")
        self.assertIn("Please answer y, n or d", text)

    def test_typed_words_keep_working_exactly_as_before(self):
        for answer in ("y", "YES", "نعم"):
            self.assertEqual(self.run_answers([answer])[0], "approve", answer)
        for answer in ("n", "لا"):
            self.assertEqual(self.run_answers([answer])[0], "reject", answer)


if __name__ == "__main__":
    unittest.main()
