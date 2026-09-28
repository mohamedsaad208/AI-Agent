"""Chat storage and the read-only context a bound chat is answered with.

A chat is the one mode that must never reach a file, so these cover the two ways that
could break: the prompt drifting into an action envelope, and a project's context either
leaking into a standalone chat or crowding the conversation out of its own budget.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import memory
from ai_code_engineer.chat import (BOUND_CHAT_SYSTEM, _messages, context_block, context_use,
                                   create_chat, load_chat, path_for, project_of, respond)
from ai_code_engineer.config import Settings

MAP = "calculator.py\n  def add(a, b)"


class Recorder:
    model = "test-local"

    def __init__(self, reply="Two numbers, added."):
        self.reply = reply
        self.calls = []

    def generate(self, messages, json_mode=True):
        self.calls.append((messages, json_mode))
        return self.reply


class ChatModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.store = Path(self.temp.name)
        self.settings = Settings()

    def answer(self, chat, text="What does add do?", context=""):
        model = Recorder()
        reply = respond(chat, model, text, self.settings, self.store, context=context)
        return model.calls[0], reply

    # ------------------------------- prose only -------------------------------
    def test_a_chat_is_never_asked_for_an_action_envelope(self):
        calls, _ = self.answer(create_chat("test-local"))
        self.assertFalse(calls[1], "json_mode must be off or the model returns a proposal")

    def test_every_prompt_answers_in_the_language_it_was_asked(self):
        """One rule, three prompts: the prose follows the user, the machine-readable parts do not."""
        from ai_code_engineer.chat import BOUND_CHAT_SYSTEM, CHAT_SYSTEM
        from ai_code_engineer.engine import SYSTEM
        for prompt in (CHAT_SYSTEM, BOUND_CHAT_SYSTEM, SYSTEM):
            self.assertIn("same language", prompt)
            self.assertIn("JSON key", prompt)
            self.assertIn("English", prompt)

    def test_an_empty_or_failing_answer_stores_no_half_conversation(self):
        chat = create_chat("test-local")
        for reply in ("", "   ", None):
            with self.assertRaises(Exception):
                respond(chat, Recorder(reply), "HI", self.settings, self.store)
        self.assertEqual(chat["turns"], [])
        self.assertFalse(path_for(self.store, chat).exists())

    # ------------------------------ the project link ------------------------------
    def test_an_unbound_chat_records_no_project(self):
        chat = create_chat("test-local")
        self.assertIsNone(project_of(chat))
        _, reply = self.answer(chat)
        stored = load_chat(path_for(self.store, chat))
        self.assertIsNone(stored["project"])
        self.assertEqual(reply, "Two numbers, added.")

    def test_a_bound_chat_records_the_folder_it_reads(self):
        project = {"key": "abc", "path": "/tmp/repo"}
        chat = create_chat("test-local", project=project)
        self.assertEqual(project_of(chat), project)

    def test_a_standalone_prompt_carries_no_project_context(self):
        calls, _ = self.answer(create_chat("test-local"))
        system = calls[0][0]["content"]
        self.assertNotIn("Repository context", system)
        self.assertIn("NO access to any project", system)

    def test_a_bound_prompt_reads_the_map_but_cannot_write(self):
        chat = create_chat("test-local", project={"key": "abc", "path": "/tmp/repo"})
        context = context_block(MAP, "Java 17, never rename this artifact.")
        calls, _ = self.answer(chat, context=context)
        system = calls[0][0]["content"]
        self.assertIn("Repository context", system)
        self.assertIn("calculator.py", system)
        self.assertIn("Java 17", system)
        self.assertIn("switch the badge next to Send to Change", system)
        self.assertTrue(system.startswith(BOUND_CHAT_SYSTEM), "the bound prompt, not the plain one")

    def test_a_full_map_does_not_push_the_conversation_out_of_budget(self):
        """The largest map the index can emit (12 000 characters) plus a full note still leaves room."""
        chat = create_chat("test-local")
        for index in range(12):
            chat["turns"].append({"role": "user", "content": "question " + str(index) * 200})
            chat["turns"].append({"role": "assistant", "content": "answer " + str(index) * 200})
        context = context_block("m" * 12_000, "n" * memory.MAX_MEMORY)
        calls, _ = self.answer(chat, context=context)
        sent = calls[0]
        self.assertLessEqual(sum(len(message["content"]) for message in sent),
                             self.settings.context_chars)
        self.assertGreater(len(sent), 1, "the newest turn survives next to the context")
        self.assertEqual(sent[-1]["content"], "What does add do?")

    def test_a_chat_survives_a_restart_with_its_project(self):
        chat = create_chat("test-local", project={"key": "abc", "path": "/tmp/repo"})
        self.answer(chat, context=context_block(MAP))
        reopened = load_chat(path_for(self.store, chat))
        self.assertEqual(project_of(reopened)["key"], "abc")
        self.assertEqual([turn["role"] for turn in reopened["turns"]], ["user", "assistant"])
        self.assertEqual(reopened["title"], "What does add do?")

    def test_the_context_block_omits_what_was_not_collected(self):
        self.assertEqual(context_block("", ""), "")
        self.assertNotIn("Standing notes", context_block(MAP))
        self.assertNotIn("Repository context", context_block(notes="just a note"))

    def test_the_budget_the_drawer_shows_is_the_request_that_would_be_sent(self):
        chat = create_chat("test-local")
        chat["turns"].append({"role": "user", "content": "What does add do?"})
        chat["turns"].append({"role": "assistant", "content": "It adds two numbers."})
        context = context_block(MAP, notes="Run the tests first.")
        use = context_use(chat, self.settings, context)
        sent = _messages(chat, self.settings, context)
        self.assertEqual(use["turns"], sum(len(m["content"]) for m in sent[1:]))
        self.assertEqual(use["context"], len(context))
        self.assertEqual(use["system"], len(BOUND_CHAT_SYSTEM))
        self.assertEqual(use["kept"], 2)
        self.assertEqual(use["remaining"], self.settings.context_chars - use["used"])
        self.assertEqual(use["est_tokens"], use["used"] // 4)

    def test_turns_that_do_not_fit_are_not_counted_as_spent(self):
        """The drawer must not bill the user for history the builder already dropped."""
        chat = create_chat("test-local")
        for index in range(60):
            chat["turns"].append({"role": "user", "content": "x" * 900 + str(index)})
        use = context_use(chat, self.settings, context_block(MAP))
        sent = _messages(chat, self.settings, context_block(MAP))
        self.assertEqual(use["kept"], len(sent) - 1)
        self.assertLess(use["used"], self.settings.context_chars)
        self.assertLess(len(chat["turns"]), 200)

    def test_a_project_with_no_conversation_reports_zero_turns(self):
        use = context_use(None, self.settings, context_block(MAP))
        self.assertEqual(use["turns"], 0)
        self.assertEqual(use["kept"], 0)
        self.assertEqual(use["used"], use["system"] + use["context"])


if __name__ == "__main__":
    unittest.main()
