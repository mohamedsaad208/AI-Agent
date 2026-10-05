"""Tests for the friendly error formatter (Release 4 - Task 4.2).

Three things are checked here, in the order a person meets them: that every known failure
arrives as all three parts — Problem, Root cause, Agent action — with the Problem line being
the very sentence ``labels.friendly_error`` already says in the other windows; that a traceback
exists only when the exception was really raised, and stays hidden until ``details`` asks; and
that what reaches the terminal is that structure, intact under ``NO_COLOR`` and neutered of
control characters, with the ``agent`` CLI honouring both.
"""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import unittest

from rich.console import Console

from ai_code_engineer import error_fmt as ef
from ai_code_engineer import labels
from ai_code_engineer.errors import AgentError, Cancelled, PolicyError, ProviderError
from ai_code_engineer.events import AgentProgressError, Event, EventKind

COLOR_CODE = re.compile(r"\x1b\[([0-9;]*)m")


def has_color(out: str) -> bool:
    """Whether any SGR sequence names a foreground or background colour (bold alone is not one)."""
    for code in COLOR_CODE.findall(out):
        for segment in code.split(";"):
            if segment.isdigit() and (30 <= int(segment) <= 47 or 90 <= int(segment) <= 107):
                return True
    return False

# One sample message per category row, written to hit that row and no earlier one.
SAMPLES = {
    "timeout": "the model request timeout was exceeded",
    "connection": "connection failed: cannot reach ollama at 11434",
    "later_edit": "calculator.py changed since the proposal was reviewed",
    "repetition": "the model repeated the same action three times",
    "unchanged_file": "refused: the proposal is an unchanged file",
    "no_proposal": "the model could not produce a proposal: fenced block unreadable",
    "budget": "the run stopped without progress within budget",
    "truncated": "the reply hit output truncated at the token ceiling",
    "missing_key": "Set GROQ_API_KEY in your environment first",
    "denied": "provider returned HTTP 401 for this key",
    "rate_limit": "provider returned HTTP 429, slow down",
    "unknown": "a failure nobody has classified yet",
}


def plain(renderable, **over) -> str:
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, no_color=True, width=100, **over).print(renderable)
    return buffer.getvalue()


def raised(exc: Exception) -> Exception:
    """The exception, having actually been raised — the only way it gains a traceback."""
    try:
        raise exc
    except BaseException as caught:
        return caught


class ClassificationTests(unittest.TestCase):
    def test_every_known_failure_arrives_as_all_three_parts(self):
        for key, message in SAMPLES.items():
            err = ef.format_error(AgentError(message))
            # The unclassified keeps the class the failure actually arrived in — that name is
            # the triage information — so "unknown" is the row, not the printed type.
            self.assertEqual(err.error_type, "AgentError" if key == "unknown" else key, message)
            self.assertTrue(err.problem, message)
            self.assertTrue(err.root_cause, message)
            self.assertTrue(err.agent_action, message)
        cancelled = ef.format_error(Cancelled("the operator stopped the run"))
        self.assertEqual(cancelled.error_type, "cancelled")
        self.assertTrue(cancelled.root_cause and cancelled.agent_action)

    def test_the_problem_line_is_the_shared_voice_not_a_second_one(self):
        """Same sentence the web window and the status strip already say — one home, no drift."""
        for message in SAMPLES.values():
            exc = AgentError(message)
            self.assertEqual(ef.format_error(exc).problem, labels.friendly_error(exc))

    def test_an_agent_error_keeps_its_own_class_as_the_named_type(self):
        err = ef.format_error(PolicyError("a refusal no marker claimed"))
        self.assertEqual(err.error_type, "PolicyError")
        self.assertIn("PolicyError", err.root_cause)

    def test_a_provider_failure_is_classified_like_any_other(self):
        self.assertEqual(ef.format_error(ProviderError("connection failed")).error_type,
                         "connection")

    def test_the_new_sayings_follow_the_language_of_the_task(self):
        english = ef.format_error("the model request timeout was exceeded")
        arabic = ef.format_error("the model request timeout was exceeded", arabic=True)
        self.assertNotEqual(english.root_cause, arabic.root_cause)
        self.assertTrue(labels.is_arabic(arabic.root_cause))
        self.assertTrue(labels.is_arabic(arabic.agent_action))

    def test_an_arabic_message_writes_the_new_halves_in_arabic_by_itself(self):
        err = ef.format_error(AgentError("هذا المجلد أُمر ألا يعدّل الملف"))
        self.assertTrue(err.arabic)
        self.assertTrue(labels.is_arabic(err.root_cause))
        # The Problem line stays the shared sentence, which is English whatever the task's language.
        self.assertIn("هذا المجلد", err.problem)


class SchemaTests(unittest.TestCase):
    def test_what_the_core_named_wins_over_the_table(self):
        event = AgentProgressError(error_type="disk_full", message="no space left writing app.py",
                                   root_cause="The volume holding the project is full",
                                   suggested_action="Free space and apply again",
                                   recoverable=False)
        err = ef.format_error(event)
        self.assertEqual((err.root_cause, err.agent_action),
                         ("The volume holding the project is full", "Free space and apply again"))
        self.assertEqual((err.error_type, err.recoverable), ("disk_full", False))

    def test_the_blanks_the_core_left_are_filled_from_the_markers(self):
        err = ef.format_error(AgentProgressError(error_type="error",
                                                 message="connection failed: refused"))
        self.assertEqual(err.root_cause,
                         ef.format_error(AgentError("connection failed: refused")).root_cause)

    def test_the_wire_event_is_formatted(self):
        wire = AgentProgressError(error_type="error", message="HTTP 429 slow down").to_dict()
        self.assertEqual(ef.format_error(Event.from_dict(wire)).error_type, "rate_limit")
        self.assertEqual(ef.format_error(wire).error_type, "rate_limit")
        self.assertEqual(ef.format_error({"message": "HTTP 429 slow down"}).error_type,
                         "rate_limit")

    def test_a_formatted_error_is_already_an_error_event(self):
        err = ef.format_error("the reply hit output truncated at the token ceiling")
        event = err.to_event()
        self.assertIsInstance(event, AgentProgressError)
        self.assertEqual(event.to_event().kind, EventKind.ERROR.value)
        self.assertEqual(event.message, err.problem)
        self.assertEqual(event.suggested_action, err.agent_action)

    def test_raw_text_is_an_error_too_because_the_core_sends_plaintext(self):
        err = ef.format_error("the run stopped without progress within budget")
        self.assertEqual(err.error_type, "budget")
        self.assertFalse(err.has_details)   # text was never raised; inventing frames would lie

    def test_an_event_that_is_not_an_error_is_refused(self):
        with self.assertRaises(TypeError):
            ef.format_error(Event(kind=EventKind.STAGE_CHANGED.value, data={"stage": "plan"}))
        for wrong in (3, None, {"kind": "status"}):
            with self.assertRaises(TypeError):
                ef.format_error(wrong)


class TracebackTests(unittest.TestCase):
    def test_a_raised_failure_keeps_every_frame_behind_details(self):
        err = ef.format_error(raised(AgentError("an unclassified stop")))
        self.assertTrue(err.has_details)
        self.assertIn("Traceback (most recent call last)", err.trace)
        self.assertIn("raise exc", err.trace)   # the frame that raised it is named

    def test_a_constructed_error_hides_nothing_because_it_has_nothing_to_hide(self):
        self.assertFalse(ef.format_error(AgentError("request timeout")).has_details)

    def test_a_secret_in_a_traceback_never_leaves_the_page_it_was_redacted_on(self):
        secret = "sk-abcdefghijklmnopqrstuvwx"
        err = ef.format_error(raised(AgentError("connection failed after sending key=" + secret)))
        self.assertNotIn(secret, err.trace)
        # `[redacted]` can only be there if the raw text passed through the scrubber on its way in.
        self.assertIn("[redacted]", err.trace)

    def test_a_traceback_carried_by_an_event_is_redacted_as_the_same_rule(self):
        event = Event(kind=EventKind.ERROR.value, data={
            "message": "boom", "traceback": "Traceback...\ntoken=abcdefghijkl123456"})
        err = ef.format_error(event)
        self.assertNotIn("abcdefghijkl123456", err.trace)
        self.assertIn("[redacted]", err.trace)

    def test_with_traceback_off_no_frames_are_captured_at_all(self):
        err = ef.format_error(raised(AgentError("request timeout")), with_traceback=False)
        self.assertEqual(err.trace, "")


class RenderTests(unittest.TestCase):
    def test_the_three_parts_arrive_in_order(self):
        out = plain(ef.renderable(ef.format_error(AgentError(SAMPLES["timeout"]))))
        self.assertIn("Error:", out)
        self.assertIn("Root cause:", out)
        self.assertIn("Agent action:", out)
        self.assertLess(out.index("Error:"), out.index("Root cause:"))
        self.assertLess(out.index("Root cause:"), out.index("Agent action:"))

    def test_the_hidden_traceback_says_where_it_is_without_saying_its_name(self):
        """The old CLI promise — a refusal is a line, no `Traceback` unless asked — still holds."""
        out = plain(ef.renderable(ef.format_error(raised(AgentError("an unclassified stop")))))
        self.assertIn("details", out)
        self.assertNotIn("Traceback", out)

    def test_details_asks_and_the_evidence_arrives(self):
        err = ef.format_error(raised(AgentError("an unclassified stop")))
        out = plain(ef.renderable(err, details=True))
        self.assertIn("Full traceback", out)
        self.assertIn("Traceback (most recent call last):\n", out)
        self.assertIn('  File "', out)      # every frame on its own line, not folded into one
        self.assertNotIn("?\n  File", out)

    def test_a_multiline_failure_keeps_its_line_breaks(self):
        """The sanitizer answers for one line; a refusal written across lines stays readable."""
        err = ef.format_error(AgentError("a refusal with\ntwo lines\ninside"))
        out = plain(ef.renderable(err))
        self.assertIn("a refusal with\ntwo lines\ninside", out)
        self.assertNotIn("with?two", out)

    def test_a_row_that_opens_onto_nothing_promises_nothing(self):
        out = plain(ef.renderable(ef.format_error(AgentError("request timeout"))))
        self.assertNotIn("details", out)

    def test_arabic_headings_for_an_arabic_failure(self):
        err = ef.format_error(AgentError(SAMPLES["truncated"]), arabic=True)
        out = plain(ef.renderable(err))
        self.assertIn("السبب الجذري", out)
        self.assertIn("إجراء الوكيل", out)

    def test_no_color_keeps_every_word_and_drops_the_colour(self):
        buffer = io.StringIO()
        ef.render(AgentError(SAMPLES["timeout"]), console=Console(file=buffer, width=100,
                                                                  force_terminal=True,
                                                                  no_color=True))
        out = buffer.getvalue()
        self.assertIn("Error:", out)
        self.assertIn("The model needed longer", out)
        self.assertFalse(has_color(out))

    def test_a_terminal_that_takes_colour_is_given_it(self):
        buffer = io.StringIO()
        ef.render(AgentError(SAMPLES["timeout"]), console=Console(file=buffer, width=100,
                                                                  force_terminal=True))
        self.assertTrue(has_color(buffer.getvalue()))

    def test_control_characters_never_drive_the_terminal_themselves(self):
        err = ef.format_error(AgentError("a refusal with \x1b[31mHIDDEN\x1b[0m inside"))
        out = plain(ef.renderable(err))
        self.assertNotIn("\x1b", out)
        self.assertIn("?[31mHIDDEN", out)


class CommandTests(unittest.TestCase):
    """The `agent` CLI, driven the way an operator drives it into a wall."""

    def run_agent(self, *argv) -> str:
        from ai_code_engineer.cli import main
        buffer = io.StringIO()
        stdout, sys.stdout = sys.stdout, buffer
        try:
            code = main(list(argv))
        finally:
            sys.stdout = stdout
        self.assertEqual(code, 1)
        return buffer.getvalue()

    def test_a_failed_command_says_the_three_parts_not_the_raw_line(self):
        out = self.run_agent("review", str(Path("no-such-session.json")))
        self.assertTrue(out.startswith("Error:"), out)
        self.assertIn("unreadable or invalid", out)
        self.assertIn("Root cause:", out)
        self.assertIn("Agent action:", out)

    def test_the_traceback_stays_hidden_until_details_is_asked_for(self):
        self.assertNotIn("Traceback (most recent call last)",
                         self.run_agent("review", str(Path("no-such-session.json"))))
        self.assertIn("Traceback (most recent call last)",
                      self.run_agent("--details", "review", str(Path("no-such-session.json"))))


if __name__ == "__main__":
    unittest.main()
