"""The language rule, tested as a rule rather than as a pile of strings.

Nothing here renders a window: these are the sentences the two controllers show, and the one
decision that picks between them. The assertions go through code points on purpose — a source
file that lost its UTF-8 decodes to U+FFFD, which looks like Arabic in a terminal and is not.
"""
import inspect
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import labels
from ai_code_engineer.labels import (STATES, STATES_AR, STATUS_TEXTS, applied_note, artifact_card,
                                     branch_started, branch_switched, checkpoint_note, is_arabic,
                                     no_branch_note, no_checkpoint_note, restore_done,
                                     restore_offer, say, state_label, status_text, write_notice)

# Built from code points so this file stays ASCII and cannot itself arrive mangled.
ARABIC_TASK = "".join(map(chr, [0x0639, 0x0627, 0x064A, 0x0632])) + " " + \
              "".join(map(chr, [0x0627, 0x0636, 0x064A, 0x0641])) + " User"
HEBREW = "".join(map(chr, [0x05E9, 0x05DC, 0x05D5, 0x05DD]))
DIACRITICS = "".join(map(chr, [0x064E, 0x0652, 0x0670]))


class WhichLanguage(unittest.TestCase):
    def test_arabic_script_is_the_test_not_a_word_list(self):
        self.assertTrue(is_arabic(ARABIC_TASK))
        self.assertTrue(is_arabic("عدل src/main/java/App.java"))

    def test_english_and_nothing_are_not_arabic(self):
        self.assertFalse(is_arabic("Create a pom.xml for Spring Boot 3"))
        self.assertFalse(is_arabic(""))
        self.assertFalse(is_arabic(None))
        self.assertFalse(is_arabic("src/main/java/App.java"))

    def test_a_neighbouring_script_is_not_picked_up(self):
        self.assertFalse(is_arabic(HEBREW), "the range is Arabic, not right-to-left in general")

    def test_a_diacritic_only_string_still_counts(self):
        """Rare, but the block is the rule and the marks live inside it."""
        self.assertTrue(is_arabic(DIACRITICS))


class TheSentenceChoice(unittest.TestCase):
    def test_say_picks_one_of_two_and_nothing_in_between(self):
        self.assertEqual(say(False, en="saved", ar="حُفظ"), "saved")
        self.assertEqual(say(True, en="saved", ar="حُفظ"), "حُفظ")

    def test_every_state_has_an_arabic_twin(self):
        """A new engine state lands in STATES; forgetting the twin shows one English card in an
        Arabic session, which is the drift this file exists to prevent."""
        self.assertEqual(sorted(STATES), sorted(STATES_AR))

    def test_the_arabic_twins_are_actually_arabic_and_not_mangled(self):
        """U+FFFD is what a lost byte decodes to, and it renders as a box nobody notices in a
        terminal — so the check is by code point, not by eye."""
        for state, text in STATES_AR.items():
            with self.subTest(state=state):
                self.assertTrue(is_arabic(text), text)
                self.assertTrue(text.strip(), text)
                self.assertNotIn(chr(0xFFFD), text)

    def test_an_unknown_state_names_itself_in_either_language(self):
        self.assertEqual(state_label("WHAT"), "WHAT")
        self.assertEqual(state_label("WHAT", arabic=True), "WHAT")

    def test_no_state_reads_as_nothing_open(self):
        self.assertEqual(state_label(None), "No task open")
        self.assertEqual(state_label(""), "No task open")

    def test_english_is_the_default_so_the_tk_window_is_untouched(self):
        self.assertEqual(state_label("PARTIAL_APPLY"), STATES["PARTIAL_APPLY"])


class TheApplyCard(unittest.TestCase):
    def test_the_card_says_proposed_before_the_write_and_saved_after(self):
        """The old card read "N proposed file(s)" after Auto-Apply had written, which is the one
        moment that sentence is untrue. `written` is what separates them."""
        waiting = artifact_card("WAITING_APPROVAL", arabic=False, count=2, project="demo2",
                                summary="s", written=False)
        written = artifact_card("APPLIED_UNVERIFIED", arabic=False, count=2, project="demo2",
                                summary="s", written=True)
        self.assertIn("proposed", waiting["title"])
        self.assertNotIn("proposed", written["title"])
        self.assertIn("saved to disk", written["title"])
        self.assertTrue(written["written"])
        self.assertFalse(waiting["written"])

    def test_the_card_comes_out_in_the_language_it_was_asked_in(self):
        english = artifact_card("CHECKS_PASSED", arabic=False, count=1, project="demo2")
        arabic = artifact_card("CHECKS_PASSED", arabic=True, count=1, project="demo2")
        self.assertTrue(is_arabic(arabic["state"]))
        self.assertTrue(is_arabic(arabic["title"]))
        self.assertFalse(is_arabic(english["state"]))
        self.assertEqual(arabic["tone"], english["tone"], "tone is not a language question")

    def test_a_project_name_and_a_path_stay_in_their_own_script(self):
        card = artifact_card("APPLIED_UNVERIFIED", arabic=True, count=1, project="demo2",
                             summary=" adds User.name", written=True)
        self.assertIn("demo2", card["title"])

    def test_no_session_still_says_what_to_do(self):
        with_project = artifact_card(None, arabic=False, count=0, project="", has_project=True)
        without = artifact_card(None, arabic=False, count=0, project="", has_project=False)
        self.assertEqual(with_project["title"], "No proposal yet")
        self.assertIn("Choose a project", with_project["detail"])
        self.assertIn("no project attached", without["detail"])
        self.assertFalse(with_project["written"])

    def test_an_empty_summary_does_not_leave_the_card_blank(self):
        card = artifact_card("APPLIED_UNVERIFIED", arabic=False, count=1, project="demo2", summary="  ")
        self.assertEqual(card["detail"], "Nothing to apply for this task.")

    def test_a_long_summary_is_cut_the_same_way_in_both_languages(self):
        card = artifact_card("CHECKS_PASSED", arabic=True, count=1, project="demo2",
                             summary="x" * 900)
        self.assertEqual(len(card["detail"]), 240)


class TheQueueStrip(unittest.TestCase):
    def test_the_strip_gets_its_sentences_from_the_server_and_its_glyphs_from_the_client(self):
        notes = labels.queue_notes(False, 2, False)
        self.assertEqual(sorted(notes), ["elsewhere_note", "held_note", "when", "when_detached",
                                         "when_restored"])
        self.assertIn("2", notes["elsewhere_note"], "the count belongs in the sentence")

    def test_the_same_four_sentences_exist_in_both_languages(self):
        english, arabic = labels.queue_notes(False, 1, False), labels.queue_notes(True, 1, False)
        self.assertEqual(sorted(english), sorted(arabic))
        for key, text in arabic.items():
            with self.subTest(key=key):
                self.assertTrue(is_arabic(text), text)
                self.assertNotIn(chr(0xFFFD), text)

    def test_the_strip_says_when_the_real_reason_is_an_unanswered_question(self):
        """Two rows sat in the strip with held=False and busy=False for half an hour, and the sentence
        beside them promised they ran when the task ended. What they were really waiting on was the
        fix-round dialog nobody had answered."""
        plain = labels.queue_notes(False, 0, False)
        asked = labels.queue_notes(False, 0, True)
        self.assertIn("current task ends", plain["when"])
        self.assertNotEqual(plain["when"], asked["when"])
        self.assertIn("answer the question", asked["when"])
        self.assertEqual(sorted(asked), sorted(plain))
        arabic = labels.queue_notes(True, 0, True)
        self.assertTrue(is_arabic(arabic["when"]))
        self.assertNotEqual(arabic["when"], plain["when"])
        self.assertNotIn(chr(0xFFFD), arabic["when"])


class TheNoticeThatFollowsAnAutomaticWrite(unittest.TestCase):
    def test_it_names_the_disk_carries_the_summary_and_offers_the_rollback(self):
        text = write_notice(arabic=False, count=3, summary="Adds a duplicate guard")
        self.assertIn("3 file(s)", text)
        self.assertIn("saved", text.lower())
        self.assertIn("Adds a duplicate guard", text)
        self.assertIn("Roll back", text)

    def test_no_summary_does_not_leave_a_dangling_label(self):
        self.assertNotIn("summary", write_notice(arabic=False, count=1).lower())
        self.assertNotIn("ملخص", write_notice(arabic=True, count=1))

    def test_it_is_plain_text_because_the_thread_escapes_tool_rows(self):
        """mdToHtml never runs on a tool row, so markdown here would print its own markers."""
        for text in (write_notice(arabic=False, count=1, summary="s"),
                     applied_note(arabic=False, count=1),
                     checkpoint_note(arabic=False, count=1, sha="abc1234")):
            self.assertNotIn("**", text)
            self.assertNotIn("`", text)

    def test_the_git_vocabulary_survives_the_translation(self):
        ok = checkpoint_note(arabic=True, count=2, sha="9f3c21a")
        self.assertIn("9f3c21a", ok)
        self.assertIn("--no-verify", ok)
        self.assertTrue(is_arabic(ok))
        self.assertIn("git has no user.name", no_checkpoint_note(arabic=True,
                                                                reason="git has no user.name configured"))

    def test_the_line_count_says_whether_the_file_was_rewritten(self):
        """D24 and D26 were the same accident: a one-line task that replaced the whole file, silently.
        The count is the part of that which fits in the row a user reads."""
        one = write_notice(arabic=False, count=1, lines=2, total=33, rewrote=False)
        self.assertIn("2 line(s) changed in a file of 33", one)
        swept = write_notice(arabic=False, count=1, lines=35, total=33, rewrote=True)
        self.assertIn("every one of the 33 lines", swept)
        fresh = write_notice(arabic=False, count=1, lines=9, total=0, rewrote=False)
        self.assertIn("9 line(s) written", fresh)
        self.assertNotIn("line(s) changed", write_notice(arabic=False, count=1))

    def test_the_line_count_exists_in_both_languages(self):
        for kwargs in ({"lines": 2, "total": 33, "rewrote": False},
                       {"lines": 35, "total": 33, "rewrote": True},
                       {"lines": 9, "total": 0, "rewrote": False}):
            with self.subTest(**kwargs):
                text = write_notice(arabic=True, count=1, **kwargs)
                self.assertTrue(is_arabic(text), text)
                self.assertNotIn(chr(0xFFFD), text)

    def test_a_branch_sentence_keeps_the_refname_latin_in_both_languages(self):
        """A branch name is a code name like a path: it must survive the translation intact."""
        started = branch_started(arabic=True, branch="agent/task-add-login-ab12cd34",
                                 back="main")
        self.assertTrue(is_arabic(started), started)
        self.assertIn("agent/task-add-login-ab12cd34", started)
        self.assertIn("main", started)
        self.assertNotIn("Started git branch", started)
        self.assertTrue(is_arabic(branch_switched(arabic=True, branch="main")))
        self.assertIn("cannot lock ref", no_branch_note(arabic=True,
                                                        reason="fatal: cannot lock ref 'HEAD'"))

    def test_the_way_back_is_only_offered_when_a_branch_was_actually_left(self):
        plain = branch_started(arabic=False, branch="agent/task-x", back="")
        self.assertNotIn("back to", plain)
        self.assertIn("agent/task-x", plain)
        self.assertIn("back to main",
                      branch_started(arabic=False, branch="agent/task-x", back="main"))

    def test_the_branch_sentences_are_plain_text_like_every_other_tool_row(self):
        for text in (branch_started(arabic=False, branch="agent/task-x", back="main"),
                     branch_switched(arabic=False, branch="main"),
                     no_branch_note(arabic=False, reason="fatal: nope")):
            self.assertNotIn("**", text)
            self.assertNotIn("`", text)

    def test_the_banner_sentence_arrives_ready_to_print(self):
        """The card used to build this sentence from a count client-side, which is how it stayed
        English under an Arabic task."""
        english = applied_note(arabic=False, count=2)
        self.assertIn("2 file(s)", english)
        self.assertIn("saved to disk", english)
        self.assertTrue(is_arabic(applied_note(arabic=True, count=2)))


    def test_the_restore_offer_is_one_sentence_for_the_refusal_and_its_remedy(self):
        """`friendly_error` already tells this user to create a new proposal. Where a git copy
        exists, the row has to say the refusal and the better route in one breath, not arrive as a
        second opinion beside the first."""
        offer = restore_offer(arabic=False, commit="9f3c21a", count=2)
        self.assertIn("Rollback refused", offer)
        self.assertIn("2 file(s)", offer)
        self.assertIn("9f3c21a", offer)
        self.assertNotIn("new proposal", offer, "the two remedies must not meet in one row")
        arabic = restore_offer(arabic=True, commit="9f3c21a", count=2)
        self.assertTrue(is_arabic(arabic), arabic)
        self.assertIn("9f3c21a", arabic, "a commit hash is a code name and stays Latin")
        for text in (offer, arabic, restore_done(arabic=False, commit="9f3c21a",
                                                  restored=["a.py"], skipped=[])):
            self.assertNotIn("**", text)
            self.assertNotIn("`", text)

    def test_a_partial_restore_names_what_git_could_not_find(self):
        """`checkout <hash> -- <path>` fails per file, so a row that only counts the successes would
        leave a file silently untouched."""
        done = restore_done(arabic=False, commit="9f3c21a", restored=["src/a.py"],
                            skipped=["src/new.py"])
        self.assertIn("Restored 1 file(s)", done)
        self.assertIn("src/new.py", done)
        self.assertIn("unchanged", done)
        self.assertNotIn("unchanged", restore_done(arabic=False, commit="9f3c21a",
                                                    restored=["src/a.py"], skipped=[]))


class TheSharedStatusSentences(unittest.TestCase):
    """The twenty sentences both windows used to write themselves.

    The English is pinned byte-for-byte on purpose: this sweep is only allowed to move a sentence,
    never to reword it. A test that asserts the new wording would let the refactor change what the
    user reads and call it progress.
    """

    def test_the_english_is_exactly_what_both_windows_said_before(self):
        self.assertEqual(status_text("applied_rerun"),
                         "Changes applied. Running the command again\u2026")
        self.assertEqual(status_text("applied_idle"),
                         "Changes applied. You can check syntax or run the project's own command.")
        self.assertEqual(status_text("granted"), "Project folder granted: ")
        self.assertEqual(status_text("prior_unverified"),
                         "Previous task is still unverified \u2014 see the conversation.")
        self.assertEqual(status_text("too_long"),
                         "Type your message in up to 4,000 characters and select a model.")

    def test_every_sentence_has_an_arabic_twin_written_in_arabic_script(self):
        for key, (english, arabic) in STATUS_TEXTS.items():
            self.assertTrue(is_arabic(arabic), key + " has no Arabic in it")
            self.assertNotEqual(english, arabic, key + " was copied, not translated")
            if english.endswith(" "):
                self.assertTrue(arabic.endswith(" "), key + " lost the room its tail fills")

    def test_a_tail_stays_outside_the_translation(self):
        """Paths, commands and git's own lines are Latin inside an Arabic sentence, as everywhere
        else in this file — so they arrive as a tail rather than being interpolated into it."""
        granted = status_text("granted", arabic=True, tail="D:/work/demo2")
        self.assertTrue(is_arabic(granted), granted)
        self.assertTrue(granted.endswith("D:/work/demo2"), granted)

    def test_an_unknown_key_raises_rather_than_answering_in_the_wrong_language(self):
        with self.assertRaises(KeyError):
            status_text("no_such_sentence")

    def test_the_two_windows_now_share_every_one_of_them(self):
        """The count is the claim: sentences that were written twice are now asked for by name in
        both windows. A key may stand outside that only by being named here as single-window."""
        from ai_code_engineer import gui
        from ai_code_engineer.webapp import controller
        gui_source = Path(gui.__file__).read_text(encoding="utf-8")
        web_source = Path(controller.__file__).read_text(encoding="utf-8")
        for key in STATUS_TEXTS:
            needle = 'status_text("' + key + '"'
            self.assertIn(needle, web_source, key + " is still written by hand in the web window")
            if key in labels.SINGLE_WINDOW_STATUS:
                self.assertNotIn(needle, gui_source,
                                 key + " is exempt from Tk but Tk asks for it anyway")
                continue
            self.assertIn(needle, gui_source, key + " is still written by hand in Tk")

    def test_only_a_real_key_is_exempt_from_the_other_window(self):
        for key in labels.SINGLE_WINDOW_STATUS:
            self.assertIn(key, STATUS_TEXTS, key + " is exempt but never defined")


class TheBatchAndRemovalLines(unittest.TestCase):
    """The rows UI 3.9 added: a finished batch, a silenced offer, an unrecorded run, a removal."""

    def test_the_batch_line_says_once_what_the_batch_landed(self):
        text = labels.batch_summary_line(arabic=False, tasks=3, files=4,
                                         paths=["src/a.py", "src/b.py"],
                                         short=["Create exactly five new files"])
        self.assertIn("3 task(s), 4 file(s)", text)
        self.assertIn("a.py", text, "the row names what is in the folder, basename first")
        self.assertIn("did not deliver: Create exactly five new files", text)

    def test_each_line_says_the_same_thing_in_the_language_it_was_asked_in(self):
        calls = [lambda ar: labels.batch_summary_line(arabic=ar, tasks=1, files=0, paths=[], short=[]),
                 lambda ar: labels.fix_offers_off_line(arabic=ar),
                 lambda ar: labels.run_unrecorded_line(arabic=ar, project="demo2")]
        for build in calls:
            english, arabic = build(False), build(True)
            self.assertFalse(labels.is_arabic(english), english)
            self.assertTrue(labels.is_arabic(arabic), arabic)

    def test_a_removal_is_named_in_the_row_about_the_click(self):
        self.assertIn("removing 1 of them", labels.applied_line(arabic=False, count=2, removed=1))
        self.assertNotIn("removing", labels.applied_line(arabic=False, count=2),
                         "an ordinary apply keeps the sentence it always had")


class TheStepLines(unittest.TestCase):
    """One line per thing the agent did, in whichever language the task was written in.

    These are the sentences the engine announces through `progress`/`step`, so they arrive in the
    chat as tool rows — escaped and never rendered — which makes the plain-text rule a rendering
    rule here, not a style preference.
    """

    ACTIONS = ("read_file", "search_code", "list_files", "propose", "blocked")

    def test_every_action_has_a_line_in_both_languages(self):
        for action in self.ACTIONS:
            english = labels.step_line(False, action, path="calculator.py", query="def add",
                                       count=2, names=["a.py", "b.py"], reason="no build file")
            arabic = labels.step_line(True, action, path="calculator.py", query="def add",
                                      count=2, names=["a.py", "b.py"], reason="no build file")
            self.assertTrue(english.strip() and arabic.strip(), action)
            self.assertNotEqual(english, arabic, action)
            self.assertTrue(is_arabic(arabic), f"{action} did not come out in Arabic: {arabic}")
            self.assertFalse(is_arabic(english), action)

    def test_paths_commands_and_names_stay_latin_inside_the_arabic_line(self):
        line = labels.step_line(True, "read_file", path="src/main/java/App.java")
        self.assertIn("src/main/java/App.java", line)
        self.assertTrue(is_arabic(line))
        listed = labels.step_line(True, "propose", count=2, names=["App.java", "AppTest.java"])
        self.assertIn("App.java", listed)
        self.assertIn("AppTest.java", listed)

    def test_an_action_this_helper_has_never_met_still_says_something(self):
        """A new tool silently vanishing from the thread is the failure worth designing against."""
        line = labels.step_line(False, "run_tests", detail="mvn test")
        self.assertIn("run_tests", line)
        self.assertIn("mvn test", line)
        self.assertTrue(labels.step_line(True, "run_tests", detail="mvn test").strip())

    def test_no_step_line_carries_markdown(self):
        for arabic in (False, True):
            for action in self.ACTIONS + ("unknown",):
                line = labels.step_line(arabic, action, path="a.py", query="q", count=3,
                                        names=["a.py", "b.py", "c.py"], reason="because")
                self.assertNotIn("**", line, action)
                self.assertNotIn("`", line, action)

    def test_a_long_file_list_is_cut_and_says_so_in_the_same_language(self):
        names = [f"file{i}.java" for i in range(12)]
        english = labels.step_line(False, "propose", count=len(names), names=names)
        arabic = labels.step_line(True, "propose", count=len(names), names=names)
        self.assertIn("+6 more", english)
        self.assertNotIn("file6.java", english)
        self.assertTrue(is_arabic(arabic))
        self.assertIn("+6", arabic)

    def test_a_proposal_with_no_names_does_not_end_on_a_colon(self):
        self.assertFalse(labels.step_line(False, "propose", count=0, names=[]).endswith(":"))

    def test_the_apply_and_executing_lines_read_as_one_sentence_each(self):
        applied = labels.applied_line(arabic=False, count=3)
        self.assertIn("3 file(s)", applied)
        self.assertIn("\U0001f4be", applied)
        self.assertTrue(is_arabic(labels.applied_line(arabic=True, count=3)))
        running = labels.executing_line(arabic=False, command="mvn -B test")
        self.assertIn("mvn -B test", running)
        self.assertIn("\u2699\ufe0f", running)
        arabic_running = labels.executing_line(arabic=True, command="mvn -B test")
        self.assertIn("mvn -B test", arabic_running)
        self.assertTrue(is_arabic(arabic_running))


class TheInsideOfAStepRow(unittest.TestCase):
    """UI 4.1: what a row opens to, and the one rule that decides whether it opens at all.

    `step_has_detail` is the load-bearing piece. The client draws its chevron from the answer, so a row
    that says it has details and opens onto an empty box costs the reader the habit of opening rows —
    and a row that hides a real record costs them the thing they came to look at.
    """

    def test_a_running_command_has_nothing_behind_it_yet(self):
        self.assertFalse(labels.step_has_detail("executing", {"command": "mvn -B test"}))
        self.assertTrue(labels.step_has_detail("executed", {"command": "mvn -B test"}))
        self.assertFalse(labels.step_has_detail("executed", {"command": ""}),
                         "a row that cannot name its command cannot show its output")

    def test_a_file_row_opens_only_when_there_are_files(self):
        self.assertTrue(labels.step_has_detail("propose", {"names": ["a.py"]}))
        self.assertFalse(labels.step_has_detail("propose", {"names": []}))
        self.assertTrue(labels.step_has_detail("applied", {"names": ["a.py"]}))

    def test_a_read_opens_only_when_it_knows_which_version_it_saw(self):
        self.assertFalse(labels.step_has_detail("read_file", {"path": "a.py"}))
        self.assertTrue(labels.step_has_detail("read_file", {"path": "a.py", "digest": "8f2a5c31"}))

    def test_counts_are_details_and_an_unknown_action_is_not(self):
        self.assertTrue(labels.step_has_detail("search_code", {"count": 0}))
        self.assertTrue(labels.step_has_detail("list_files", {"count": 42}))
        self.assertFalse(labels.step_has_detail("blocked", {"reason": "no build file"}))
        self.assertFalse(labels.step_has_detail("brand_new_tool", {}))

    def test_the_record_fields_a_step_event_may_carry_are_the_ones_the_sentence_reads(self):
        """A stored event also holds `at`, `kind` and `id`; rebuilding a row filters them out through
        this list, so a field added to the sentence has to be added here or it never comes back."""
        for name in labels.STEP_FIELDS:
            self.assertIn(name, inspect.signature(labels.step_line).parameters, name)

    def test_the_verdict_says_the_same_thing_in_both_languages(self):
        run = {"status": "failed", "exit_code": 1, "seconds": 41.2, "truncated": True,
               "proof": {"tests": 14, "failures": 2, "errors": 0, "source": "JUnit XML"}}
        english = labels.run_verdict(False, run)
        arabic = labels.run_verdict(True, run)
        for needle in ("failed", "exit 1", "41.2s", "14 tests", "output trimmed"):
            self.assertIn(needle, english)
        self.assertTrue(is_arabic(arabic))
        self.assertIn("exit 1", arabic, "the numbers and the exit code stay latin")
        self.assertIn("41.2s", arabic)

    def test_a_row_that_settles_says_so_in_the_past_tense(self):
        line = labels.executed_line(arabic=False, command="mvn -B test",
                                    verdict=labels.run_verdict(False, {"status": "passed",
                                                                       "exit_code": 0,
                                                                       "seconds": 1.4}))
        self.assertTrue(line.startswith("\u2699\ufe0f Ran mvn -B test"), line)
        self.assertIn("passed", line)
        self.assertTrue(is_arabic(labels.executed_line(arabic=True, command="mvn -B test",
                                                       verdict="ok")))

    def test_every_section_a_row_can_show_is_named_in_both_languages(self):
        for key in labels.DETAIL_SECTIONS:
            english = labels.detail_section(False, key)
            arabic = labels.detail_section(True, key)
            self.assertTrue(english and arabic, key)
            self.assertTrue(is_arabic(arabic), key)
            self.assertFalse(is_arabic(english), key)

    def test_the_two_admissions_are_sentences_not_counts(self):
        """The cap and the missing row both have to be said in the task's language, because both are
        drawn where a rebuilt English string would sit in an Arabic thread."""
        trimmed = labels.log_dropped_line(arabic=True, count=7)
        missing = labels.step_missing_line(arabic=True)
        self.assertTrue(is_arabic(trimmed) and is_arabic(missing))
        self.assertIn("7", trimmed)
        self.assertFalse(is_arabic(labels.log_dropped_line(arabic=False, count=7)))


class TheWarningThatRunIsNotSandboxed(unittest.TestCase):
    """The one sentence in this program that must not be a code comment.

    Everything else about running the project's command is a mechanism — an argv allowlist, a
    stripped environment, a kill tree. This is the consequence, and it belongs in front of the
    person who presses the button, in whatever language they are reading the window in.
    """

    def test_it_says_what_runs_and_where(self):
        for text in (labels.run_warning(arabic=False), labels.run_warning(arabic=True)):
            self.assertIn("\u26a0\ufe0f", text)
        self.assertTrue(is_arabic(labels.run_warning(arabic=True)))
        self.assertFalse(is_arabic(labels.run_warning(arabic=False)))

    def test_english_names_the_two_buttons_and_the_limit(self):
        text = labels.run_warning(arabic=False)
        for word in ("Run", "Check syntax", "build code", "permissions", "sandbox"):
            self.assertIn(word.lower(), text.lower(), word)
        # "a limit, not a sandbox" is the whole point; a sentence that only warns is not it.
        self.assertIn("not a sandbox", text.lower())

    def test_the_arabic_says_the_same_thing_as_the_english(self):
        """Both are read by someone deciding whether to click, so neither may be softer."""
        english = labels.run_warning(arabic=False).lower()
        arabic = labels.run_warning(arabic=True)
        self.assertIn("\u0635\u0644\u0627\u062d\u064a\u0627\u062a\u0643", arabic)      # your permissions
        self.assertIn("\u0644\u064a\u0633 \u0635\u0646\u062f\u0648\u0642", arabic)        # not a sandbox
        self.assertGreater(len(arabic), 60)
        self.assertGreater(len(english), 60)


class TheRowThatThoughtOutLoud(unittest.TestCase):
    """UI 4.2 phase 2: the deliberation a reasoning model returns beside its answer is a row, not a reply.

    The shape is decided by what the thread is for: a reader scanning it wants to know *that* the model
    spent 1,200 characters thinking and roughly on what, and wants the whole text one click away. So the
    line carries one preview and the record carries the rest, and the words are said in the task's own
    language like every other row.
    """

    def test_the_line_previews_one_line_and_the_rest_lives_behind_the_row(self):
        thought = "the email check has to come first\nsecond line of the deliberation"
        line = labels.step_line(False, "model_reasoning", count=len(thought), detail=thought)
        self.assertIn("the email check has to come first", line)
        self.assertNotIn("second line", line, "opening the row is where the rest of it lives")
        self.assertIn(str(len(thought)), line)
        self.assertTrue(line.endswith("…"), line)

    def test_a_long_first_line_is_cut_so_the_thread_stays_scanable(self):
        line = labels.step_line(False, "model_reasoning", count=900, detail="x" * 900)
        self.assertLess(len(line), 200)
        self.assertEqual(line.count("…"), 1)

    def test_a_row_with_nothing_to_show_does_not_end_on_a_colon(self):
        self.assertFalse(labels.step_line(False, "model_reasoning", count=0, detail="").endswith(":"))

    def test_both_languages_say_the_same_thing_about_the_same_thought(self):
        english = labels.step_line(False, "model_reasoning", count=214, detail="the email check")
        arabic = labels.step_line(True, "model_reasoning", count=214, detail="the email check")
        self.assertIn("Thought for 214 characters", english)
        self.assertNotIn("Thought for", arabic)
        self.assertIn("\u0641\u0643\u0631", arabic)          # "thought"
        self.assertIn("\u062d\u0631\u0641", arabic)          # "characters"
        self.assertIn("214", arabic)
        self.assertIn("the email check", arabic)
        self.assertTrue(is_arabic(arabic))
        self.assertFalse(is_arabic(english))

    def test_a_thought_opens_only_when_there_is_a_thought(self):
        self.assertTrue(labels.step_has_detail("model_reasoning", {"detail": "a"}))
        self.assertFalse(labels.step_has_detail("model_reasoning", {"detail": ""}))
        self.assertFalse(labels.step_has_detail("model_reasoning", {}))

    def test_the_error_that_came_back_names_the_count_the_recipe_and_the_line(self):
        english = labels.step_line(False, "unresolved_error", count=3, label="mvn -B test",
                                   detail="[ERROR] Tests run: 14, Failures: 2")
        self.assertIn("3 earlier task(s)", english)
        self.assertIn("mvn -B test", english)
        self.assertIn("[ERROR] Tests run: 14", english)
        arabic = labels.step_line(True, "unresolved_error", count=3, label="mvn -B test",
                                  detail="[ERROR] x")
        self.assertTrue(is_arabic(arabic))
        self.assertIn("mvn -B test", arabic, "a command stays latin inside an Arabic thread")
        self.assertIn("3", arabic)
        self.assertFalse(labels.step_line(False, "unresolved_error", count=1).endswith(":"))

    def test_an_unresolved_error_says_everything_on_its_line_so_it_never_opens(self):
        """UI 4.1's rule: a row opens to say more than its line does. The count, the recipe and the
        error are all in the sentence already, so a chevron here would open an echo."""
        for fields in ({"detail": "[ERROR] x", "count": 3, "label": "mvn -B test"}, {"detail": ""}):
            self.assertFalse(labels.step_has_detail("unresolved_error", fields))


class TheRefusalSentences(unittest.TestCase):
    """UI 4.6: every sentence a declined proposal can print exists in both languages.

    Reject is a new answer to an old question, and the two halves of it that must not be confused are
    that the engine's state never changes — the operator answered it — and that the refusal is a fact
    about the run worth exporting afterwards.
    """

    def test_the_thread_note_is_written_in_arabic_too(self):
        english = labels.rejected_note(arabic=False, count=2)
        arabic = labels.rejected_note(arabic=True, count=2)
        self.assertTrue(is_arabic(arabic))
        self.assertNotEqual(english, arabic, "copied, not translated")
        self.assertIn("2", arabic)
        self.assertFalse(is_arabic(english), "and the English twin stayed English")

    def test_the_two_refusals_in_the_notes_table_have_arabic_twins(self):
        for key in ("proposal_reject_nothing", "proposal_reject_twice"):
            english, arabic_text = labels.NOTE_TEMPLATES[key]
            self.assertTrue(is_arabic(arabic_text), key)
            self.assertNotEqual(english, arabic_text, key)

    def test_a_declined_proposal_is_said_in_the_replayed_log(self):
        english = labels.log_line(False, {"kind": "proposal_rejected"})
        self.assertIn("Proposal declined", english)
        self.assertTrue(is_arabic(labels.log_line(True, {"kind": "proposal_rejected"})))

    def test_the_card_answers_the_refusal_without_the_engine_changing_state(self):
        card = artifact_card("WAITING_APPROVAL", arabic=False, count=3, project="demo2",
                             rejected=True)
        self.assertEqual(card["state"], "Rejected")
        self.assertEqual(card["tone"], "idle")
        self.assertFalse(card["written"], "nothing went to disk, and the card says so")
        self.assertIn("3 file(s)", card["title"])
        self.assertEqual(artifact_card("WAITING_APPROVAL", arabic=False, count=3,
                                       project="demo2")["state"], STATES["WAITING_APPROVAL"],
                       "an unanswered proposal still reads as one")


class TheReferenceLine(unittest.TestCase):
    """UI 4.6: the line that puts somebody else's words in front of a message.

    It is a quotation, it says whose words they are, and it is short. The first two are the honesty
    half — a sentence the agent wrote must not arrive in the prompt looking like an instruction from
    the operator — and the third is because 160 characters of quoted answer would eat the message that
    needs reading.
    """

    def test_the_block_quotes_and_attributes(self):
        line = labels.quote_reference(False, "assistant", "The guard lives in create().")
        self.assertEqual(line, '> [In reference to the agent\'s earlier reply: '
                               '"The guard lives in create()."]\n\n')

    def test_every_role_in_the_thread_has_a_phrase_in_both_languages(self):
        for role in ("user", "assistant", "tool"):
            english = labels.quote_reference(False, role, "x")
            arabic = labels.quote_reference(True, role, "x")
            self.assertTrue(is_arabic(arabic), role)
            self.assertNotEqual(english, arabic, role)
            self.assertTrue(english.startswith("> [In reference to "), role)
        # A role this table has never met is still said, as the agent's words rather than as nothing.
        self.assertIn("the agent", labels.quote_reference(False, "unknown", "x"))

    def test_the_quotation_is_cut_at_the_length_the_window_says(self):
        words = "w" * (labels.QUOTE_CHARS + 60)
        line = labels.quote_reference(False, "user", words)
        self.assertIn("w" * labels.QUOTE_CHARS + '…"', line)
        self.assertNotIn("w" * (labels.QUOTE_CHARS + 1), line)
        self.assertNotIn("…", labels.quote_reference(False, "user", "short enough"))

    def test_the_block_comes_back_off_for_a_display_question_only(self):
        text = labels.quote_reference(False, "assistant", "a long answer nobody asked for") + "why?"
        self.assertEqual(labels.asked_of(text), "why?")
        self.assertEqual(labels.asked_of("why?"), "why?")
        self.assertEqual(labels.asked_of("> [In reference"), "> [In reference",
                         "a message that merely starts with the marker is not a block")


if __name__ == "__main__":
    unittest.main()
