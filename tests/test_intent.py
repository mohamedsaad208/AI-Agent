"""The write-intent axis: three positions, both windows, and one copy of every sentence.

Read-only mode is a promise, and a promise two surfaces word separately is two promises. An earlier
round shipped a refusal that meant different things in the two windows because each had its own
sentence for it; `intent` exists so that cannot recur, and these tests are what makes the rule real:
one of them fails if either window writes the words out again, and one fails if a stray character from
another alphabet lands inside a translation.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import intent

SRC = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"

# Every sentence the module owns, callable both ways. The two that take an argument are given one.
SENTENCES = {
    "switched(read)": lambda a: intent.switched(intent.READ, arabic=a),
    "switched(change)": lambda a: intent.switched(intent.CHANGE, arabic=a),
    "switched(chat)": lambda a: intent.switched(intent.CHAT, arabic=a),
    "needs_folder(change)": lambda a: intent.needs_folder(intent.CHANGE, arabic=a),
    "needs_folder(read)": lambda a: intent.needs_folder(intent.READ, arabic=a),
    "no_proposal": lambda a: intent.no_proposal(arabic=a),
    "no_write": lambda a: intent.no_write("Apply", arabic=a),
    "no_fix_round": lambda a: intent.no_fix_round(arabic=a),
    "no_auto_apply": lambda a: intent.no_auto_apply(arabic=a),
    "run_ask": lambda a: intent.run_ask("mvn -B test", arabic=a, project="auth-service"),
    "run_declined": lambda a: intent.run_declined(arabic=a),
    "unchecked": lambda a: intent.unchecked(arabic=a),
    "answered": lambda a: intent.answered(arabic=a),
    # The durable declaration: a folder told to stay read-only by a surface this window never saw.
    "declared": lambda a: intent.declared(by="cli", at="2026-09-30 07:12", arabic=a,
                                          project="auth-service"),
    "declared with no who": lambda a: intent.declared(arabic=a),
    "lift": lambda a: intent.lift("D:\\repo\\auth-service", arabic=a),
    "followed": lambda a: intent.followed(by="web", at="2026-09-30 07:12", arabic=a),
    "followed with no who": lambda a: intent.followed(arabic=a),
}


def arabic(text: str) -> bool:
    return any(0x600 <= ord(char) <= 0x6ff for char in text)


class TheThreePositions(unittest.TestCase):
    def test_the_three_positions_are_the_ones_the_ladder_describes(self):
        self.assertEqual(intent.MODES, ("chat", "read", "change"))
        self.assertEqual([intent.label(mode) for mode in intent.MODES],
                         ["Chat", "Read-only", "Change"])

    def test_only_read_only_is_the_position_that_refuses_the_proposal(self):
        self.assertTrue(intent.read_only("read"))
        self.assertFalse(intent.read_only("chat"))
        self.assertFalse(intent.read_only("change"))

    def test_a_value_nobody_sent_still_lands_on_the_promise_that_writes_nothing(self):
        """A preference written by an older build, or a payload with a typo in it, cannot be allowed
        to leave a window in a state where neither promise holds."""
        for junk in ["", None, "READ ONLY", "ReadOnly", "writes", 3]:
            self.assertEqual(intent.normalise(junk), intent.CHAT, junk)

    def test_a_spelling_of_read_only_that_is_not_the_constant_is_not_silently_change(self):
        self.assertEqual(intent.normalise(" read "), intent.READ)
        self.assertTrue(intent.read_only(" read "))

    def test_the_header_line_names_the_position_then_its_promise(self):
        # Pinned to the strings the web header has always printed, so the third one joins them in the
        # same shape rather than restyling the two that were already there.
        self.assertEqual(intent.subtitle("chat"), "chat · reads as context")
        self.assertEqual(intent.subtitle("change"), "change · reviewed diff")
        self.assertEqual(intent.subtitle("read"), "read-only · writes nothing")

    def test_an_unknown_position_still_gets_a_header_line(self):
        self.assertEqual(intent.subtitle("nonsense"), intent.subtitle("chat"))


class EverySentenceSpeaksBothLanguages(unittest.TestCase):
    def test_both_halves_of_the_table_are_filled_in(self):
        for name, build in SENTENCES.items():
            english, arabic_text = build(False), build(True)
            self.assertTrue(english.strip(), name)
            self.assertTrue(arabic(arabic_text), f"{name} has no Arabic in it")
            self.assertFalse(arabic(english), f"{name} leaked Arabic into the English half")

    def test_no_sentence_carries_a_character_from_a_third_alphabet(self):
        """A stray CJK ideograph once reached a translated line and nobody noticed until it printed.

        Everything allowed here is ASCII, Arabic script, or the three punctuation marks the sentences
        genuinely use inside Latin text — an em dash, a middot and a curly apostrophe.
        """
        strays = {"\u2014", "\u00b7", "\u2019"}
        for name, build in SENTENCES.items():
            for arabic_flag in (False, True):
                text = build(arabic_flag)
                for char in text:
                    if ord(char) < 128 or (0x600 <= ord(char) <= 0x6ff) or char in strays:
                        continue
                    self.fail(f"{name} carries U+{ord(char):04X}: {text[:60]!r}")

    def test_the_arabic_half_is_written_right_to_left_end_to_end(self):
        """The whole sentence flips, so a sentence assembled from an English frame with Arabic words
        dropped into it would read backwards in the window."""
        for name, build in SENTENCES.items():
            text = build(True)
            first = next((char for char in text if ord(char) >= 128), "")
            self.assertTrue(arabic(text.strip()[:12]), name)
            self.assertNotIn("Read-only", text, name)


class TheRunPermission(unittest.TestCase):
    def test_the_question_names_the_command_and_where_it_will_run(self):
        text = intent.run_ask("mvn -B test", project="auth-service")
        self.assertIn("mvn -B test", text)
        self.assertIn("auth-service", text)
        self.assertIn("cannot write to your files", text,
                      "the promise the rest of the mode is built on is restated at the one yes")

    def test_the_question_without_a_project_names_no_project(self):
        """The clause is interpolated, so an empty project must drop it rather than leave the sentence
        saying the command runs somewhere unnamed."""
        self.assertIn("with your permissions:", intent.run_ask("npm test"))
        self.assertNotIn("npm test in", intent.run_ask("npm test"))
        self.assertIn("with your permissions in auth-service:",
                      intent.run_ask("npm test", project="auth-service"))

    def test_declining_says_that_nothing_ran(self):
        self.assertIn("Nothing ran", intent.run_declined())


class TheDurableDeclaration(unittest.TestCase):
    """The sentences a folder sealed by another surface gets. Each one has to name the way out."""

    def test_the_declaration_names_the_surface_and_the_minute(self):
        text = intent.declared(by="cli", at="2026-09-30 07:12", project="auth-service")
        self.assertIn("the command line", text)
        self.assertIn("2026-09-30 07:12", text)
        self.assertIn("auth-service", text)
        self.assertIn("git branch", text, "the two writes this mode stopped late are named with the rest")

    def test_a_declaration_anybody_can_see_needs_no_blame(self):
        text = intent.declared()
        self.assertIn("declared Read-only", text)
        self.assertNotIn("()", text, "no empty parenthesis where a who would have been")

    def test_the_lift_line_carries_the_command_to_type(self):
        text = intent.lift("D:\\repo\\auth-service")
        self.assertIn("agent read-only --off", text)
        self.assertIn("D:\\repo\\auth-service", text)

    def test_the_badge_follows_the_fact_and_says_which_one_won(self):
        text = intent.followed(by="web", at="2026-09-30 07:12")
        self.assertIn("The badge follows the declaration", text)
        self.assertIn("the web window", text)

    def test_a_surface_nobody_listed_keeps_the_name_it_was_written_with(self):
        self.assertEqual(intent.source("cli"), "the command line")
        self.assertEqual(intent.source("cli", arabic=True), "سطر الأوامر")
        self.assertEqual(intent.source("hand-edited"), "hand-edited")
        self.assertEqual(intent.source(""), "")


class NeitherWindowWordsARefusal(unittest.TestCase):
    """The drift guard. `intent` is the only place these sentences may be written."""

    WINDOWS = {"gui.py": SRC / "gui.py", "controller.py": SRC / "webapp" / "controller.py"}

    def test_both_windows_import_the_module(self):
        for name, path in self.WINDOWS.items():
            text = path.read_text(encoding="utf-8")
            self.assertRegex(text, r"from \. import .*intent|from \.\. import .*intent", name)
            self.assertIn("intent.", text, f"{name} imports the axis and never uses it")

    def test_no_window_carries_a_copy_of_a_sentence(self):
        for name, path in self.WINDOWS.items():
            text = path.read_text(encoding="utf-8")
            for sentence, build in SENTENCES.items():
                probe = build(False)[:48]
                self.assertNotIn(probe, text, f"{name} words {sentence} itself")

    def test_the_two_windows_ask_the_same_question_of_the_same_switch(self):
        """`reading_only` is the gate every write and run checks; if one window stops exposing it, the
        other is refusing something the first one no longer even knows about."""
        for name, path in self.WINDOWS.items():
            text = path.read_text(encoding="utf-8")
            self.assertIn("def reading_only", text, name)
