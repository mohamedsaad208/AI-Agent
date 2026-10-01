"""The folder's position: read or write, remembered per folder, and what each gate refuses."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way
from ai_code_engineer import intent, modes, runner
from ai_code_engineer.engine import atomic_json, project_key
from doubles import (
                     CALCULATOR_BAD, CALCULATOR_GOOD, ChatModel, OLLAMA_ENTRY, ProposalModel,
                     run_result)
from helpers import sandbox_repo
from controller_case import Scripted, patched_catalog


class IntentWordTests(unittest.TestCase):
    """The verb table is the whole heuristic, so it is tested as a table."""

    def setUp(self):
        from ai_code_engineer.webapp.controller import asks_for_a_change
        self.ask = asks_for_a_change

    def check(self, expected, *texts):
        for text in texts:
            self.assertEqual(self.ask(text), expected, text)

    def test_change_verbs_route(self):
        self.check(True, "add a test", "Fix the build", "Create a Flask app", "refactor: split it",
                   "build me a page", "make the sidebar wider", "delete the old handler",
                   "scaffold an api", "implement retry", "write a tokenizer")

    def test_a_verb_after_the_opening_word_still_routes(self):
        """The request rarely starts on its verb, so the opening five tokens are scanned."""
        self.check(True, "please add a test", "I want to create a flask app",
                   "I would like to generate a report", "setup a virtualenv for this",
                   "hey, can you make the sidebar wider")

    def test_questions_about_changes_do_not(self):
        self.check(False, "how do I add a logout button?", "what does build() do",
                   "explain this refactor", "is the test fixed?", "can you add a column?",
                   "why does add() fail", "An add-on?", "additionally, note that")

    def test_a_question_mark_is_a_question_whatever_it_opens_with(self):
        """The veto is about the shape of the sentence, not only its first word."""
        self.check(False, "can you fix this?", "please add a test?", "you want me to write it?")
        self.check(True, "can you fix this", "please add a test")

    def test_ordinary_chat_does_not(self):
        self.check(False, "HI", "thanks!", "", "   ", "good morning team")

    def test_arabic_change_verbs_route_in_every_spelling_a_person_types(self):
        self.check(True, "\u0623\u0646\u0634\u0626 \u0644\u064a \u0645\u0634\u0631\u0648\u0639",   # anshif ma with hamza
                   "\u0627\u0646\u0634\u0626 \u0644\u064a \u0645\u0634\u0631\u0648\u0639",   # same word, no hamza
                   "\u0637\u0648\u0651\u0631 \u0627\u0644\u0648\u0627\u062c\u0647\u0629",               # with shadda
                   "\u0639\u062f\u0644 \u0627\u0644\u062f\u0627\u0644\u0629", "\u0635\u0644\u062d \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631",
                   "\u0627\u0643\u062a\u0628 \u062f\u0627\u0644\u0629", "\u0636\u064a\u0641 \u0632\u0631", "\u0627\u0639\u0645\u0644 \u0627\u062e\u062a\u0628\u0627\u0631",
                   "\u0627\u062d\u0630\u0641 \u0627\u0644\u0645\u0644\u0641", "\u063a\u064a\u0631 \u0627\u0644\u0648\u0627\u0646\u0647",
                   "\u0633\u0648\u064a \u0644\u064a \u0645\u0644\u0641", "\u0631\u0643\u0628 \u0644\u064a \u0627\u0644\u062d\u0632\u0645")

    def test_arabic_requests_that_open_on_the_wanting_word(self):
        """"\u0639\u0627\u064a\u0632 \u0627\u0639\u0645\u0644 \u0645\u0634\u0631\u0648\u0639" opens on neither a verb nor a question word."""
        self.check(True, "\u0639\u0627\u064a\u0632 \u0627\u0639\u0645\u0644 \u0645\u0634\u0631\u0648\u0639",
                   "\u0645\u062d\u062a\u0627\u062c \u0627\u0639\u062f\u0644 \u0627\u0644\u062f\u0627\u0644\u0629",
                   "\u064a\u0627\u0631\u064a\u062a \u0627\u0644\u0648\u0627\u062c\u0647\u0629 \u062a\u0628\u0642\u0649 \u0623\u0633\u0647\u0644",
                   "\u0627\u0631\u064a\u062f \u0627\u0628\u0646\u064a \u0635\u0641\u062d\u0629",
                   "\u0628\u062f\u064a \u0632\u0631 \u062d\u0641\u0638",
                   "\u062d\u0627\u0628\u0628 \u0627\u0636\u064a\u0641 \u0627\u062e\u062a\u0628\u0627\u0631",
                   "\u0644\u0648 \u0633\u0645\u062d\u062a \u0636\u064a\u0641 \u0632\u0631")

    def test_the_wanting_word_need_not_be_first(self):
        """A polite address or an adverb can come before it: "ya sahbi ayez taamal login"."""
        self.check(True, "\u064a\u0627 \u0635\u0627\u062d\u0628\u064a \u0639\u0627\u064a\u0632 \u062a\u0639\u0645\u0644 login",
                   "\u0628\u0631\u0636\u0648 \u0639\u0627\u064a\u0632 \u0627\u0636\u064a\u0641 \u0632\u0631",
                   "\u0648\u0643\u0645\u0627\u0646 \u0645\u062d\u062a\u0627\u062c \u0627\u0639\u0645\u0644 \u0627\u062e\u062a\u0628\u0627\u0631")
        # The question veto still runs first, so an ask-after-a-question-word stays a question.
        self.check(False, "\u0647\u0644 \u0639\u0627\u064a\u0632 \u062a\u0639\u0645\u0644 login\u061f")

    def test_arabic_questions_do_not(self):
        self.check(False, "\u0643\u064a\u0641 \u0623\u0636\u064a\u0641 \u0632\u0631\u061f", "\u0645\u0627 \u0648\u0638\u064a\u0641\u0629 build\u061f",
                   "\u0627\u0634\u0631\u062d \u0627\u0644\u0643\u0648\u062f", "\u0647\u0644 \u064a\u0645\u0643\u0646 \u0625\u0635\u0644\u0627\u062d\u0647",
                   "\u0644\u064a\u0647 \u0627\u0644\u0643\u0648\u062f \u0645\u0634 \u0634\u063a\u0627\u0644", "\u0634\u0643\u0631\u0627")
        # A request word that opens a question is still a question: the mark vetoes it.
        self.check(False, "\u0645\u0645\u0643\u0646 \u062a\u0635\u0644\u062d \u0627\u0644\u0627\u062e\u062a\u0628\u0627\u0631\u061f")

    def test_the_window_is_five_words_and_no_further(self):
        self.check(False, "I was wondering if you could fix this")     # "fix" is the sixth token
        self.check(True, "Fix this, please")
class RememberedFolderModeTests(unittest.TestCase):
    """Granting a folder is a decision about *that folder*, so it has to outlive the window.

    Found by the ecommerce run: the folder was granted, landed on Change mode exactly as it should,
    Auto-Apply was remembered for it — and after a restart the same folder was back in Chat mode,
    because only `set_composer` wrote the preference map and the paths that select a branch with an
    explicit composer wrote nothing.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = self.app_dir / "svc"
        (self.repo / "src").mkdir(parents=True)

    def window(self):
        made = Scripted(self.app_dir)
        made.catalogs["Ollama"] = [OLLAMA_ENTRY]
        made.model = "test-local"
        self.addCleanup(made.close)
        return made

    def test_a_folder_granted_in_change_mode_restarts_in_change_mode(self):
        first = self.window()
        first.set_repo(str(self.repo))
        self.assertEqual(first.composer, "change")
        first.close()
        second = self.window()
        second.set_repo(str(self.repo))
        self.assertEqual(second.composer, "change", "the mode was set and then forgotten")

    def test_a_mode_the_person_picked_by_hand_is_remembered_the_same_way(self):
        """The fix must not turn into the tool overruling the person: a hand-picked Chat mode has to
        survive the restart exactly as a granted Change mode does."""
        first = self.window()
        first.set_repo(str(self.repo))
        first.set_composer("chat")
        first.close()
        second = self.window()
        second.set_repo(str(self.repo))
        self.assertEqual(second.composer, "chat")

    def test_a_plain_chat_branch_still_defaults_to_prose(self):
        window = self.window()
        self.assertEqual(window.composer, "chat", "no folder, nothing to propose against")
class ReadOnlyModeTests(unittest.TestCase):
    """Phase-3 item 4: a position that reads the folder and refuses to write it.

    Each gate is a sentence the operator only hears by asking for the thing it stops, so every test
    here enters through the method the UI calls — `apply`, `run_tests`, `start_plan` — rather than by
    asserting on a predicate.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._made = []
        self.addCleanup(self._drain)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        patcher = patched_catalog()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.model = ProposalModel()
        starter = patch("ai_code_engineer.webapp.controller.make_provider",
                        side_effect=lambda *a, **k: self.model)
        starter.start()
        self.addCleanup(starter.stop)
        self.controller = self.build()

    def build(self, answers=None):
        controller = Scripted(self.app_dir, answers)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        controller.set_repo(str(self.repo))
        self._made.append(controller)
        return controller

    def _drain(self):
        for controller in self._made:
            controller.cancel_event.set()
            controller.join(timeout=10)

    def read(self, controller=None):
        controller = controller or self.controller
        controller.set_composer("read")
        self.assertEqual(controller.composer, "read")
        return controller

    def rows(self, controller=None):
        return " \n".join(m["text"] for m in (controller or self.controller).snapshot()["messages"])

    # ------------------------------- choosing it -------------------------------
    def test_reading_a_folder_has_to_be_a_folder_first(self):
        window = self.build()
        window.set_repo("")
        window.set_composer("read")
        self.assertEqual(window.composer, "chat", "an empty window cannot be in Read-only")
        self.assertIn("Choose a project", window.snapshot()["status"])

    def test_the_header_says_which_promise_is_in_force(self):
        self.read()
        self.assertIn("read-only · writes nothing", self.controller.snapshot()["header"]["subtitle"])

    # ------------------------------- the proposal -------------------------------
    def test_an_imperative_gets_an_explanation_and_no_proposal(self):
        self.model = ChatModel()        # this message is answered in prose, so the provider must be one
        self.read()
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        state = self.controller.snapshot()
        self.assertNotEqual(state["review"]["state"], "Changes ready for review")
        self.assertIn(intent.no_proposal(), self.rows())
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD,
                         "the whole point is that nothing is written")
        self.assertEqual(state["status"], intent.answered(),
                         "and the answer closes on its own promise, not on advice to switch modes")

    def test_the_folder_is_read_while_answering(self):
        """The spec's four verbs: read it, search it, map it, explain it."""
        self.model = ChatModel()
        self.read()
        self.controller.start_plan("why is add() wrong here")
        self.controller.join()
        messages = self.model.calls[-1][0]
        text = json.dumps(messages)
        self.assertIn("calculator.py", text, "the repository map reached the model with the question")

    # ------------------------------- the writes -------------------------------
    def test_a_proposal_left_on_screen_cannot_be_applied_or_rolled_back(self):
        self.controller.set_composer("change")
        self.controller.start_plan("Fix add in calculator.py")
        self.controller.join()
        self.assertEqual(self.controller.snapshot()["review"]["state"], "Changes ready for review")
        self.read()
        state = self.controller.snapshot()
        self.assertFalse(state["review"]["canApply"], "the button is not offered either")
        self.assertFalse(state["review"]["canRollback"])
        self.controller.apply()
        self.controller.join()
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)
        self.assertIn(intent.no_write("Apply"), self.controller.snapshot()["status"])
        self.controller.undo()
        self.assertIn(intent.no_write("Roll back"), self.controller.snapshot()["status"])

    def test_a_code_block_cannot_be_turned_into_a_proposal(self):
        self.read()
        self.controller.offer_block({"path": "calculator.py", "content": CALCULATOR_GOOD})
        self.controller.join()
        self.assertIn(intent.no_proposal(), self.controller.snapshot()["status"])
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)

    def test_the_switch_that_writes_without_a_click_cannot_be_armed(self):
        self.read()
        self.controller.set_auto_apply(True)
        self.assertIn(intent.no_auto_apply(), self.controller.snapshot()["status"])
        self.assertFalse(self.controller.auto_apply)
        # Turning it back to Change must not silently arm what the refusal prevented.
        self.controller.set_composer("change")
        self.assertFalse(self.controller.auto_apply)

    # ------------------------------- the command -------------------------------
    def test_nothing_runs_until_the_operator_answers_for_that_command(self):
        self.read()
        self.controller.answers["confirm"] = False        # the answer to *this* command is no
        with patch("ai_code_engineer.runner.run", return_value=run_result()) as ran:
            self.controller.run_tests(False)
            self.controller.join()
        self.assertEqual(ran.call_count, 0)
        asked = self.controller.asked[-1]
        self.assertEqual(asked["title"], "Run this command?")
        self.assertEqual(asked["message"],
                         intent.run_ask(runner.display_command(self.controller.selected_recipe()),
                                        project=self.repo.name),
                         "the question names the exact command it is asking about")
        self.assertIn(intent.run_declined(), self.controller.snapshot()["status"])

    def test_the_question_about_a_command_says_it_will_run_in_the_container(self):
        """The read-only mode asks once per command and names it. Naming the command but not its
        environment would be answering a different question than the one being asked."""
        self.read()
        with patch("ai_code_engineer.runner.sandbox_available", return_value=True):
            self.controller.action("sandbox", {"on": True, "image": "python@sha256:" + "a" * 64},
                                   lambda _event: None)
        self.controller.answers["confirm"] = False
        with patch("ai_code_engineer.runner.run", return_value=run_result()) as ran:
            self.controller.run_tests(False)
            self.controller.join()
        self.assertEqual(ran.call_count, 0)
        self.assertIn("in Docker", self.controller.asked[-1]["message"])

    def test_a_command_the_operator_approved_runs_and_writes_nothing(self):
        window = Scripted(self.app_dir, {"confirm": True})
        window.catalogs["Ollama"] = [OLLAMA_ENTRY]
        window.model = "test-local"
        window.set_repo(str(self.repo))
        window.set_composer("read")
        self._made.append(window)
        failure = run_result(status="failed", failures=["AssertionError: 1 != 2"])
        with patch("ai_code_engineer.runner.run", return_value=failure) as ran:
            window.run_tests(True)          # "Run & fix" — the fix half is refused, the run is not
            window.join()
        self.assertEqual(ran.call_count, 1)
        self.assertIn(intent.no_fix_round(), self.rows(window))
        self.assertFalse(window._auto_fix)
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)

    # ------------------------------- remembering it -------------------------------
    def test_a_folder_left_in_read_only_comes_back_in_read_only(self):
        self.read()
        self.controller.set_auto_apply(False)
        self.controller.close()
        second = self.build()
        second.set_repo(str(self.repo))
        self.assertEqual(second.composer, "read")
        self.assertFalse(second.auto_apply, "and the write switch is still off with it")
class BlockApplyTests(unittest.TestCase):
    """UI 3.0 §3: the ⚡ Apply to File button opens a proposal, and only Apply writes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = self.app_dir / "repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "main.py").write_text("def add(a, b):\n    return a - b\n",
                                                   encoding="utf-8", newline="\n")
        (self.repo / ".env").write_text("TOKEN=1\n", encoding="utf-8")
        for patcher in (patched_catalog(),):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.events = []

    def project(self):
        controller = Scripted(self.app_dir)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        controller.projects["repo"] = str(self.repo)
        controller._select_branch("project", "repo", composer="change")

        def drain(c=controller):
            c.cancel_event.set()
            c.join(10)
        self.addCleanup(drain)
        return controller

    def offer(self, controller, path, content):
        controller.action("apply_block", {"path": path, "content": content}, self.events.append)
        controller.join()
        return controller.snapshot()

    def test_the_block_becomes_a_proposal_and_the_file_stays_as_it_was(self):
        controller = self.project()
        state = self.offer(controller, "src/main.py", "def add(a, b):\n    return a + b\n")
        self.assertEqual((self.repo / "src" / "main.py").read_text(encoding="utf-8"),
                         "def add(a, b):\n    return a - b\n", "the button must not write")
        self.assertEqual(controller.session["state"], "WAITING_APPROVAL")
        self.assertTrue(state["review"]["canApply"])
        self.assertIn("Review the diff", controller.status)
        self.assertIn({"kind": "view", "value": "preview"}, self.events,
                      "the side viewer has to open on its own, or the proposal is invisible")

    def test_the_reviewed_diff_shows_the_line_the_block_replaces(self):
        controller = self.project()
        self.offer(controller, "src/main.py", "def add(a, b):\n    return a + b\n")
        change = controller.session["changes"][0]
        self.assertEqual(change["before"], "def add(a, b):\n    return a - b\n")
        self.assertEqual(change["after"], "def add(a, b):\n    return a + b\n")

    def test_a_block_addressed_at_a_protected_path_writes_nothing(self):
        controller = self.project()
        for name in (".env", "../outside.py", "C:\\Windows\\win.ini"):
            self.offer(controller, name, "x = 1\n")
            self.assertIsNone(controller.session, name)
            self.assertTrue((self.repo / ".env").exists())
        self.assertEqual(list((self.app_dir / ".agent-runs").glob("*/session.json")), [])

    def test_a_block_offer_needs_a_project_and_says_so(self):
        controller = Scripted(self.app_dir)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        self.addCleanup(controller.close)
        self.offer(controller, "src/main.py", "x = 1\n")
        self.assertEqual(controller.session, None)
        self.assertIn("Choose a project", controller.status)

    def test_a_half_written_block_is_refused_rather_than_offered_empty(self):
        controller = self.project()
        self.offer(controller, "src/main.py", "def add(a, b):\n")
        self.assertIsNone(controller.session)
        self.assertIn("syntax", controller.status.lower())

    def test_an_unchanged_block_is_refused_as_nothing_to_write(self):
        controller = self.project()
        self.offer(controller, "src/main.py", "def add(a, b):\n    return a - b\n")
        self.assertIsNone(controller.session)
        self.assertIn("same text the file already holds", controller.status.lower())
        self.assertNotIn("Switch the badge", controller.status,
                         "the chat-mode advice is for a message, not for a block that was clicked")
class TheDeclarationOutlivesTheWindow(unittest.TestCase):
    """Item #58: the folder's position is a fact on disk, not a badge state inside one process.

    Every test here writes the declaration through one surface and reads the consequence from another,
    which is the half Read-only could not do while the position lived in a window's own preference
    block — a block each window rebuilds from a list of named keys, so the other window's save deleted
    it and a folder with no row left opened on Change.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._made = []
        self.addCleanup(self._drain)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = sandbox_repo(self.app_dir)
        patcher = patched_catalog()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.model = ProposalModel()
        starter = patch("ai_code_engineer.webapp.controller.make_provider",
                        side_effect=lambda *a, **k: self.model)
        starter.start()
        self.addCleanup(starter.stop)

    def _drain(self):
        for controller in self._made:
            controller.cancel_event.set()
            controller.join(timeout=10)

    def window(self, folder=None):
        controller = Scripted(self.app_dir)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        controller.set_repo(str(folder or self.repo))
        self._made.append(controller)
        return controller

    def status(self, controller):
        return controller.snapshot()["status"]

    # ---------------------------- written here, obeyed there ----------------------------
    def test_choosing_the_badge_writes_the_folder_s_position_outside_the_window(self):
        controller = self.window()
        controller.set_composer("read")
        row = modes.row_for(self.app_dir, self.repo)
        self.assertEqual(row["mode"], "read", "the promise has to outlive the process that made it")
        self.assertEqual(row["by"], "web")

    def test_a_folder_sealed_by_the_command_line_is_sealed_when_the_window_opens_it(self):
        modes.declare(self.app_dir, self.repo, intent.READ, by=modes.TERMINAL)
        controller = self.window()
        self.assertEqual(controller.composer, "read")
        self.assertIn("read-only · writes nothing", controller.snapshot()["header"]["subtitle"])

    def test_a_seal_that_arrives_while_the_window_is_open_refuses_the_write_and_says_who(self):
        """The window opened on Change and got a proposal, then something else sealed the folder."""
        controller = self.window()
        controller.set_composer("change")
        controller.start_plan("Fix add in calculator.py")
        controller.join()
        self.assertEqual(controller.snapshot()["review"]["state"], "Changes ready for review")
        modes.declare(self.app_dir, self.repo, intent.READ, by=modes.TERMINAL)
        controller.apply()
        controller.join()
        self.assertIn("the command line", self.status(controller),
                      "a refusal that names no surface reads like a bug in this window")
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)

    def test_choosing_change_is_what_lifts_a_seal_and_says_that_it_did(self):
        modes.declare(self.app_dir, self.repo, intent.READ, by=modes.TERMINAL)
        controller = self.window()
        self.assertEqual(controller.composer, "read")
        controller.set_composer("change")
        self.assertFalse(modes.sealed(self.app_dir, self.repo))
        self.assertIn(intent.unchecked(), self.status(controller),
                      "ending a promise somebody else made is not a silent event")

    def test_choosing_chat_over_a_seal_leaves_the_seal_standing(self):
        """Chat is not a promise about files — a message that asks for one is still planned as a
        proposal — so picking it must not be a way to lift a seal without naming it."""
        modes.declare(self.app_dir, self.repo, intent.READ, by=modes.TERMINAL)
        controller = self.window()
        controller.set_composer("chat")
        self.assertTrue(modes.sealed(self.app_dir, self.repo))
        self.assertTrue(controller.reading_only())

    def test_the_desktop_window_saving_its_own_preferences_cannot_unseal_a_folder(self):
        """The regression this whole change exists for.

        Both windows rebuild the `ui` block of `.agent-projects.json` from a list of named keys, so a
        save by one deleted the other's map — and a folder with no stored row is granted on Change.
        Choosing Read-only for a folder and then opening the other window once used to put that folder
        back on the writable path without a word.
        """
        controller = self.window()
        controller.set_composer("read")
        controller.close()
        atomic_json(self.app_dir / ".agent-projects.json", {
            "projects": [{"path": str(self.repo), "key": project_key(self.repo), "icon": ""}],
            # Exactly what the desktop window writes: its own keys, and no `composer` block.
            "ui": {"mode": "Ollama", "last_project": str(self.repo), "read_only": False}})
        second = self.window()
        self.assertEqual(second.composer, "read",
                         "the position a person chose is not the other window s to forget")
        second.apply()
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_BAD)

    # ------------------------------- the two git writes -------------------------------
    def test_a_branch_switch_is_refused_because_git_rewrites_the_tracked_files(self):
        controller = self.window()
        controller.set_composer("read")
        controller.git_branch()
        self.assertIn(intent.no_write("Switching branches"), self.status(controller))
        self.assertEqual(controller.asked, [], "and it does not even reach the confirm dialog")

    def test_the_git_escalation_is_refused_with_the_rest(self):
        """The restore appears only after a rollback refused, so it was the door left open: the same
        bytes, put back by git instead of by the session file."""
        controller = self.window()
        controller.set_composer("read")
        controller._git_restore = {"commit": "9f3c21a", "paths": ["calculator.py"]}
        with patch("ai_code_engineer.git_integration.restore_paths") as restored:
            controller.git_restore()
        self.assertEqual(restored.call_count, 0)
        self.assertIn(intent.no_write("Restoring files from git"), self.status(controller))

    # ---------------------------------- what it looks like ----------------------------------
    def test_the_snapshot_carries_the_declaration_and_only_explains_a_disagreement(self):
        controller = self.window()
        controller.set_composer("change")
        self.assertFalse(controller.snapshot()["declared"]["sealed"])
        row = modes.declare(self.app_dir, self.repo, intent.READ, by=modes.TERMINAL)
        state = controller.snapshot()
        self.assertTrue(state["declared"]["sealed"])
        self.assertEqual(state["declared"]["by"], "the command line")
        self.assertEqual(state["declared"]["note"],
                         intent.followed(row["by"], row["at"]),
                         "the badge says Change while the gates refuse: that has to be said out loud")
        controller.set_composer("read")
        self.assertEqual(controller.snapshot()["declared"]["note"], "",
                         "badge and fact agreeing is not a thing to explain every snapshot")

    def test_a_static_check_stays_allowed_on_a_sealed_folder(self):
        """`canMutate` drives the Check syntax button, and a check that executes nothing from the
        project is one of the four verbs Read-only promises it will still do. The rollback next to it
        is a write with a friendly name, so that one goes."""
        controller = self.window()
        controller.set_composer("change")
        controller.start_plan("Fix add in calculator.py")
        controller.join()
        controller.apply()
        controller.join()
        self.assertEqual((self.repo / "calculator.py").read_text(), CALCULATOR_GOOD)
        modes.declare(self.app_dir, self.repo, intent.READ, by=modes.TERMINAL)
        state = controller.snapshot()
        self.assertTrue(state["review"]["canMutate"])
        self.assertFalse(state["review"]["canRollback"], "restoring bytes is still a write")


if __name__ == "__main__":
    unittest.main()
