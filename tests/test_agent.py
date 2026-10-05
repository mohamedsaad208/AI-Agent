from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import engine, policy, repair, tools
from ai_code_engineer.cli import demo
from ai_code_engineer.config import MIN_CONTEXT_CHARS, Settings, load_settings
from ai_code_engineer.engine import (apply_proposal, atomic_json, chat_sessions, DEFAULT_CHECKS, diff_size,
                                     MAX_TASK_CHARS, NO_SUMMARY, load_session,
                                     parse_action, plan, prepare_changes, project_key,
                                     propose_block, proposal_hash, review, rollback, shrink_warning)
from ai_code_engineer.prompts import SYSTEM
from ai_code_engineer.errors import AgentError, PolicyError, ProviderError
from ai_code_engineer.redaction import redact
from ai_code_engineer.providers import OllamaProvider, OpenAICompatibleProvider, make_provider
from ai_code_engineer.verification import verify
from ai_code_engineer.workspace import Workspace, digest, ensure_project_dir


class ScriptedProvider:
    model = "test"

    def __init__(self, responses):
        self.responses = iter(responses)

    def generate(self, messages):
        response = next(self.responses)
        return response if isinstance(response, str) else json.dumps(response)


class RecordingProvider(ScriptedProvider):
    """Keeps each prompt so a test can see what the model was actually told."""

    def __init__(self, responses):
        super().__init__(responses)
        self.prompts = []

    def generate(self, messages):
        self.prompts.append([dict(message) for message in messages])
        return super().generate(messages)


class ListingProvider:
    """Counts model calls while asking for the same harmless action each turn."""

    model = "test"

    def __init__(self):
        self.calls = 0

    def generate(self, messages):
        self.calls += 1
        return json.dumps({"action": "list_files"})


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def proposal(self, content="answer = 2\n"):
        return {"action": "propose", "summary": "Update answer", "checks": ["unit tests"],
                "changes": [{"path": "app.py", "content": content}]}

    def draft(self, content="answer = 2\n"):
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"}, self.proposal(content)])
        return plan(self.ws, "Update answer", provider, Settings(), self.base / "runs", progress=lambda _: None)

    def test_plan_does_not_write(self):
        session = load_session(self.draft())
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")

    def test_complete_ends_the_run_without_writing_anything(self):
        provider = ScriptedProvider([{"action": "complete",
                                      "summary": "The answer is already 1."}])
        path = plan(self.ws, "Make the answer 1", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "COMPLETED")
        self.assertEqual(session["summary"], "The answer is already 1.")
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")
        self.assertNotIn("proposal_hash", session)

    def test_complete_without_a_summary_is_still_a_result(self):
        provider = ScriptedProvider([{"action": "complete"}])
        path = plan(self.ws, "Make the answer 1", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "COMPLETED")
        self.assertTrue(session["summary"])

    def test_a_complete_with_a_non_sentence_summary_is_refused_and_the_loop_recovers(self):
        provider = ScriptedProvider([{"action": "complete", "summary": 42},
                                     {"action": "complete", "summary": "ok now"}])
        path = plan(self.ws, "Make the answer 1", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "COMPLETED")
        self.assertEqual(session["summary"], "ok now")

    def test_a_completed_task_cannot_be_resumed(self):
        provider = ScriptedProvider([{"action": "complete", "summary": "done"}])
        path = plan(self.ws, "Make the answer 1", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        with self.assertRaises(PolicyError):
            plan(self.ws, "Make the answer 1", ScriptedProvider([{"action": "complete"}]),
                 Settings(), self.base / "runs", progress=lambda _: None,
                 resume_run=load_session(path)["id"])

    def test_declined_proposal_requires_explicit_reopening(self):
        path = self.draft()
        session = load_session(path)
        approved = session["proposal_hash"]
        engine.event(session, "proposal_rejected", hash=approved)
        atomic_json(path, session)
        with self.assertRaisesRegex(PolicyError, "declined"):
            apply_proposal(path, approved)
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")
        with self.assertRaises(PolicyError):
            engine.reopen_proposal(path, "wrong")
        reopened = engine.reopen_proposal(path, approved)
        self.assertFalse(engine.proposal_rejected(reopened))
        self.assertEqual(reopened["state"], "WAITING_APPROVAL")
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")
        self.assertEqual(apply_proposal(path, approved)["state"], "APPLIED_UNVERIFIED")

    def test_reopening_does_not_bypass_stale_file_checks(self):
        path = self.draft()
        session = load_session(path)
        approved = session["proposal_hash"]
        engine.event(session, "proposal_rejected", hash=approved)
        atomic_json(path, session)
        (self.root / "app.py").write_text("answer = 99\n")
        engine.reopen_proposal(path, approved)
        with self.assertRaisesRegex(PolicyError, "changed since planning"):
            apply_proposal(path, approved)
        self.assertEqual((self.root / "app.py").read_text(), "answer = 99\n")

    def test_latest_decision_is_scoped_to_the_proposal_hash(self):
        session = {"proposal_hash": "new", "events": []}
        engine.event(session, "proposal_rejected", hash="old")
        self.assertFalse(engine.proposal_rejected(session))
        engine.event(session, "proposal_rejected", hash="new")
        engine.event(session, "proposal_reopened", hash="new")
        self.assertFalse(engine.proposal_rejected(session))
        engine.event(session, "proposal_rejected", hash="new")
        self.assertTrue(engine.proposal_rejected(session))

    def test_a_delete_lands_and_rollback_brings_the_file_back(self):
        """D31: the workspace had no delete verb, so the only way a file left the project was a
        rollback of a create. The undo has to key off the entry's own flag, because for a removal
        `before is None` means the opposite of what it means for a create."""
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"},
                                     {"action": "propose", "summary": "Retire app.py",
                                      "checks": ["unit tests"],
                                      "changes": [{"path": "app.py", "delete": True}]}])
        path = plan(self.ws, "Delete app.py", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        approved = load_session(path)["proposal_hash"]
        self.assertEqual(apply_proposal(path, approved)["state"], "APPLIED_UNVERIFIED")
        self.assertFalse((self.root / "app.py").exists())
        rollback(path, approved)
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n",
                         "undoing a removal restores the bytes it took away")

    def test_approval_apply_and_guarded_rollback(self):
        path = self.draft()
        approved = load_session(path)["proposal_hash"]
        with self.assertRaises(PolicyError):
            apply_proposal(path, "wrong")
        self.assertEqual(apply_proposal(path, approved)["state"], "APPLIED_UNVERIFIED")
        rollback(path, approved)
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")

    def test_an_interrupted_apply_says_what_it_already_wrote(self):
        """The ecommerce run left a task in `APPLYING` forever: the write succeeded, the note about
        it hit a Windows sharing violation, and the note's own failure escaped as the user's status
        line — so a second Apply answered "Approval must match the pending proposal hash". Three
        true sentences that together told nobody the file was on disk."""
        path = self.draft()
        approved = load_session(path)["proposal_hash"]
        locked, notes = OSError(5, "Access is denied"), []
        def flaky(session_path, record):
            """The note that opens the apply lands; every note after it hits the sharing violation —
            the order the real run produced, and the reason the session read as `APPLYING` after."""
            notes.append(record["state"])
            if len(notes) > 1:
                raise locked
            return atomic_json(session_path, record)
        with patch("ai_code_engineer.engine.atomic_json", side_effect=flaky):
            with self.assertRaises(AgentError) as caught:
                apply_proposal(path, approved)
        message = str(caught.exception)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "answer = 2\n")
        self.assertIn("app.py", message, "the file is on disk; the sentence has to name it")
        self.assertIn("Access is denied", message, "and say why it stopped")
        self.assertIn("Rollback", message)
        self.assertEqual(load_session(path)["state"], "APPLYING",
                         "the record never caught up with the disk — what a user then sees is a stuck task")
        self.assertEqual(notes[0], "APPLYING")
        self.assertIn("PARTIAL_APPLY", notes, "it did try to record the truth before giving up")

    def test_changed_file_blocks_apply(self):
        path = self.draft()
        (self.root / "app.py").write_text("human = True\n")
        with self.assertRaises(PolicyError):
            apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertEqual((self.root / "app.py").read_text(), "human = True\n")

    def test_later_edit_blocks_rollback(self):
        path = self.draft()
        approved = load_session(path)["proposal_hash"]
        apply_proposal(path, approved)
        (self.root / "app.py").write_text("human = True\n")
        with self.assertRaises(PolicyError):
            rollback(path, approved)

    def test_diff_size_says_how_much_of_the_file_a_change_replaced(self):
        """The count behind the Auto-Apply row. It is the only number that tells a reader that a task
        which named one line replaced thirty — the shape that lost a `package` line and a `public`
        modifier in this run, twice, with a green build each time it was written."""
        kept = {"path": "A.java", "before": "line 1\nline 2\nline 3\n",
                "after": "line 1\nline 2\nline 3\n"}
        self.assertEqual(diff_size([kept]), (0, 3, False))
        one_line = {"path": "A.java", "before": "line 1\nline 2\nline 3\n",
                    "after": "line 1\npublic line 2\nline 3\n"}
        self.assertEqual(diff_size([one_line]), (2, 3, False))
        swept = {"path": "A.java", "before": "line 1\nline 2\nline 3\n",
                 "after": "a\nb\nc\n"}
        self.assertEqual(diff_size([swept]), (6, 3, True))
        fresh = {"path": "A.java", "before": None, "after": "a\nb\n"}
        self.assertEqual(diff_size([fresh]), (2, 0, False))
        self.assertEqual(diff_size([]), (0, 0, False))
        self.assertEqual(diff_size([one_line, fresh]), (4, 3, False))
        reindented = {"path": "A.java", "before": "line 1\n  line 2\n",
                      "after": "line 1\nline 2\n"}
        self.assertEqual(diff_size([reindented]), (0, 2, False),
                         "whitespace is not a rewrite, or every file Maven touches looks damaged")

    def test_static_success_does_not_mean_test_success(self):
        path = self.draft()
        apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertEqual(verify(path)["status"], "unverified")
        self.assertEqual(load_session(path)["state"], "VERIFICATION_BLOCKED")

    def test_invalid_python_cannot_reach_approval(self):
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"}] +
                                     [self.proposal("def broken(:\n")] * 4)
        with self.assertRaises(AgentError):
            plan(self.ws, "change", provider, Settings(), self.base / "runs", progress=lambda _: None)
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")

    def test_no_docker_fails_closed(self):
        path = self.draft()
        apply_proposal(path, load_session(path)["proposal_hash"])
        with patch("ai_code_engineer.verification.shutil.which", return_value=None):
            result = verify(path, "python-unittest", "python@sha256:" + "a" * 64)
        self.assertEqual(result["status"], "blocked")

    def test_path_policy(self):
        for name in ("../app.py", "/app.py", "C:/app.py", "a\\b.py", ".git/config.txt",
                     ".env", "my-secrets.json", "app.py:stream", "NUL.txt", "a./b.py"):
            with self.subTest(name=name), self.assertRaises(PolicyError):
                self.ws.path(name)
        with self.assertRaises(PolicyError):
            self.ws.path("AGENTS.md", writable=True)

    def test_short_name_alias_cannot_reach_a_protected_file(self):
        """NTFS opens an 8.3 alias for the same bytes, so the alias must be refused too."""
        (self.root / "token.json").write_text('{"aws": "SHOULD_NOT_APPEAR"}')
        for name in ("TOKEN~1.JSON", "token~1.json", "NODE_M~1/lib.js", "GIT~1/config.txt",
                     "src/App~2.java"):
            with self.subTest(name=name), self.assertRaises(PolicyError):
                self.ws.path(name)
        self.assertEqual(self.ws.search("SHOULD_NOT_APPEAR"), [])

    def test_agent_rule_files_stay_read_only(self):
        """A proposal must not be able to write the instructions another tool reads back."""
        for name in (".vscode/tasks.json", ".github/workflows/ci.yml", "mcp.json", ".mcp.json",
                     "QODER.md", "CLAUDE.md", "GEMINI.md", ".qoder/repowiki/plan.yaml",
                     "src/main/java/Codex.md"):
            with self.subTest(name=name), self.assertRaises(PolicyError):
                self.ws.path(name, writable=True)
            self.ws.path(name)  # reading the project's own config stays allowed
        self.ws.path("src/main/java/AuthController.java", writable=True)
        self.ws.path("docs/rules.md", writable=True)

    def test_a_plan_step_is_recorded_outside_the_proposal_itself(self):
        """The step number sequences work; it must not become part of what is approved."""
        (self.root / "plan.md").write_text("# Steps\n\n## Phase 1: one\nbody\n\n## Phase 2: two\nbody\n",
                                           encoding="utf-8")
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Implement step 2", provider, Settings(), self.base / "runs",
                    progress=lambda _: None, plan_file="plan.md", plan_step=2)
        session = load_session(path)
        self.assertEqual(session["plan_step"], 2)
        self.assertEqual(session["plan_reference"]["path"], "plan.md")
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL")
        self.assertEqual(proposal_hash(session), session["proposal_hash"])
        del session["plan_step"]
        self.assertEqual(proposal_hash(session), session["proposal_hash"])

    def test_goal_and_criteria_recorded_on_session_outside_proposal_hash(self):
        (self.root / "plan.md").write_text("## Phase 1: one\nbody\n", encoding="utf-8")
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Implement step 1", provider, Settings(), self.base / "runs",
                    progress=lambda _: None, plan_file="plan.md", plan_step=1,
                    goal="Test goal statement", criteria=["Criterion A", "Criterion B"],
                    accepts=[1, 2])
        session = load_session(path)
        self.assertEqual(session["goal"], "Test goal statement")
        self.assertEqual(session["criteria"], ["Criterion A", "Criterion B"])
        self.assertEqual(session["accepts"], [1, 2])
        self.assertEqual(proposal_hash(session), session["proposal_hash"])
        del session["goal"]
        del session["criteria"]
        del session["accepts"]
        self.assertEqual(proposal_hash(session), session["proposal_hash"])

    def test_a_step_number_needs_a_plan_and_stays_small(self):
        provider = ScriptedProvider([self.proposal()])
        with self.assertRaises(PolicyError):
            plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                 progress=lambda _: None, plan_step=1)
        (self.root / "plan.md").write_text("## Phase 1: one\n\n## Phase 2: two\n", encoding="utf-8")
        with self.assertRaises(PolicyError):
            plan(self.ws, "Update answer", ScriptedProvider([self.proposal()]), Settings(),
                 self.base / "runs", progress=lambda _: None, plan_file="plan.md", plan_step=0)
        self.assertEqual(list((self.base / "runs").glob("*/session.json")), [])

    def test_project_notes_reach_the_model_and_are_recorded_for_audit(self):
        provider = RecordingProvider([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None, memory="Java 17. Package com.acme.auth.")
        prompt = provider.prompts[0][1]["content"]
        self.assertIn("the user's own standing instructions", prompt)
        self.assertIn("Java 17. Package com.acme.auth.", prompt)
        session = load_session(path)
        self.assertEqual(session["memory"], "Java 17. Package com.acme.auth.")
        self.assertEqual(proposal_hash(session), session["proposal_hash"])
        # The note is context, not something the approval authorises.
        del session["memory"], session["memory_sha256"]
        self.assertEqual(proposal_hash(session), session["proposal_hash"])

    def test_oversized_notes_are_refused_before_a_session_exists(self):
        with self.assertRaises(PolicyError):
            plan(self.ws, "Update answer", RecordingProvider([self.proposal()]), Settings(),
                 self.base / "runs", progress=lambda _: None, memory="x" * 4001)
        self.assertEqual(list((self.base / "runs").glob("*/session.json")), [])

    def test_a_project_without_notes_is_sent_unchanged(self):
        plain = RecordingProvider([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Update answer", plain, Settings(), self.base / "runs",
                    progress=lambda _: None)
        self.assertNotIn("standing instructions", plain.prompts[0][1]["content"])
        self.assertNotIn("memory", load_session(path))

    def test_build_evidence_is_scrubbed_before_it_is_kept_or_sent(self):
        """The repair turn stores and re-sends command output; secrets must not ride along."""
        calls = []

        class Recording(ScriptedProvider):
            def generate(self, messages):
                calls.append(messages)
                return super().generate(messages)

        dummy_gh = "gh" + "p_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        evidence = ("password='hunter22hunter' refused\n"
                    f"token {dummy_gh} leaked by the setup script")
        provider = Recording([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None, extra_context=evidence)
        self.assertNotIn("hunter22hunter", json.dumps(load_session(path)))
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef", json.dumps(load_session(path)))
        sent = json.dumps(calls[-1])
        self.assertNotIn("hunter22hunter", sent)
        self.assertNotIn(dummy_gh, sent)
        self.assertIn("refused", sent)  # the failure the model needs is still there

    def test_secret_excluded_from_map_and_search(self):
        (self.root / "secrets.json").write_text('{"x":"SHOULD_NOT_APPEAR"}')
        self.assertNotIn("secrets.json", self.ws.repo_map())
        self.assertEqual(self.ws.search("SHOULD_NOT_APPEAR"), [])

    def test_link_escape(self):
        outside = self.base / "outside.py"
        outside.write_text("private = 1")
        try:
            (self.root / "link.py").symlink_to(outside)
        except OSError:
            self.skipTest("Symlink creation unavailable on this Windows account")
        with self.assertRaises(PolicyError):
            self.ws.read("link.py")

    def test_unknown_actions_never_execute(self):
        provider = ScriptedProvider([{"action": "shell", "command": "whoami"}] * 4)
        with self.assertRaises(AgentError), patch("subprocess.Popen") as execute:
            try:
                plan(self.ws, "change", provider, Settings(), self.base / "runs", progress=lambda _: None)
            finally:
                execute.assert_not_called()

    def test_tampered_proposal_is_rejected(self):
        path = self.draft()
        session = json.loads(path.read_text())
        session["changes"][0]["after"] = "malicious = 1"
        path.write_text(json.dumps(session))
        with self.assertRaises(AgentError):
            load_session(path)

    def test_turn_budget(self):
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"}])
        with self.assertRaises(AgentError):
            plan(self.ws, "change", provider, replace(Settings(), max_turns=1), self.base / "runs", progress=lambda _: None)

    def test_cloud_denied_before_request(self):
        settings = replace(Settings(), provider="openrouter", model="openrouter/free",
                           endpoint="https://openrouter.ai/api/v1")
        with patch("ai_code_engineer.providers.request_json") as request:
            with self.assertRaises(PolicyError):
                make_provider(settings, allow_cloud=False, data_class="public")
            with self.assertRaises(PolicyError):
                make_provider(settings, allow_cloud=True, data_class="restricted")
            request.assert_not_called()

    def test_local_remote_endpoint_denied(self):
        for endpoint in ("https://evil.example", "http://127.0.0.1.evil.example", "http://user@localhost:11434"):
            with self.assertRaises(PolicyError):
                OllamaProvider(replace(Settings(), endpoint=endpoint))

    def test_cloud_backed_ollama_denied(self):
        with patch("ai_code_engineer.providers.request_json", return_value={"remote_host": "cloud", "model_info": {"x": 1}}):
            with self.assertRaises(PolicyError):
                OllamaProvider(Settings()).preflight()

    def test_ollama_contract(self):
        with patch("ai_code_engineer.providers.request_json", return_value={"message": {"content": '{"action":"blocked","reason":"test"}'}}) as request:
            value = OllamaProvider(Settings()).generate([{"role": "user", "content": "test"}])
            self.assertEqual(json.loads(value)["action"], "blocked")
            self.assertEqual(request.call_args.args[1]["format"], "json")

    def test_openrouter_contract_and_truncation(self):
        settings = replace(Settings(), provider="openrouter", model="openrouter/free",
                           endpoint="https://openrouter.ai/api/v1")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-test-key"}):
            provider = OpenAICompatibleProvider(settings)
            with patch("ai_code_engineer.providers.request_json", return_value={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}], "model": "actual:free"}) as request:
                self.assertEqual(provider.generate([]), "{}")
                self.assertEqual(provider.model, "actual:free")
                self.assertEqual(request.call_args.args[0],
                                 "https://openrouter.ai/api/v1/chat/completions")
                self.assertFalse(request.call_args.args[1]["provider"]["allow_fallbacks"])
            with patch("ai_code_engineer.providers.request_json", return_value={"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}):
                with self.assertRaises(ProviderError):
                    provider.generate([])

    def test_invalid_config_fails(self):
        path = self.base / "config.toml"
        path.write_text('[limits]\nmax_turns = "infinite"\n')
        with self.assertRaises(AgentError):
            load_settings(path)

    def test_demo(self):
        self.assertEqual(demo()["proposal_apply_rollback"], "passed")

    def test_new_file_rollback(self):
        proposal = self.proposal()
        proposal["changes"] = [{"path": "new.py", "content": "value = 3\n"}]
        path = plan(self.ws, "Create file", ScriptedProvider([proposal]), Settings(),
                    self.base / "runs", progress=lambda _: None)
        approved = load_session(path)["proposal_hash"]
        apply_proposal(path, approved)
        self.assertTrue((self.root / "new.py").exists())
        rollback(path, approved)
        self.assertFalse((self.root / "new.py").exists())

    def test_second_apply_is_not_replayed(self):
        path = self.draft()
        approved = load_session(path)["proposal_hash"]
        apply_proposal(path, approved)
        with self.assertRaises(PolicyError):
            apply_proposal(path, approved)

    def test_all_files_preflight_before_write(self):
        (self.root / "second.py").write_text("old = 1\n")
        proposal = self.proposal()
        proposal["changes"].append({"path": "second.py", "content": "new = 2\n"})
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"},
                                     {"action": "read_file", "path": "second.py"}, proposal])
        path = plan(self.ws, "Update", provider, Settings(), self.base / "runs", progress=lambda _: None)
        (self.root / "second.py").write_text("human = 1\n")
        with self.assertRaises(PolicyError):
            apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")

    def test_paid_openrouter_model_denied(self):
        with self.assertRaises(PolicyError):
            OpenAICompatibleProvider(replace(Settings(), provider="openrouter", model="paid-model"))

    def test_repeated_read_stops_without_writes(self):
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"}] * 3)
        with self.assertRaisesRegex(AgentError, "without progress"):
            plan(self.ws, "change", provider, Settings(), self.base / "runs", progress=lambda _: None)
        self.assertEqual((self.root / "app.py").read_text(), "answer = 1\n")

    def test_malformed_ollama_response(self):
        with patch("ai_code_engineer.providers.request_json", return_value={"message": None}):
            with self.assertRaises(ProviderError):
                OllamaProvider(Settings()).generate([])

    def test_a_provider_refusal_carries_its_reason_and_not_its_request(self):
        """Ollama writes *why* it refused into the error body, and a bare "Provider HTTP 500" left a
        whole milestone undiagnosable. The body is now read — capped, collapsed to one line and
        redacted — while the URL, the payload and any key still never reach the user."""
        import io
        from urllib.error import HTTPError
        from ai_code_engineer import providers as providers_module
        body = b'{"error":"model requires more system memory (9.2 GiB) than is available (6.1 GiB)"}'

        class RefusingOpener:
            def open(self, *args, **kwargs):
                raise HTTPError("http://127.0.0.1:11434/api/chat", 500, "Internal Server Error",
                                {}, io.BytesIO(body))
            def close(self):
                pass

        with patch("ai_code_engineer.providers.build_opener", return_value=RefusingOpener()):
            with self.assertRaises(ProviderError) as caught:
                providers_module.request_json("http://127.0.0.1:11434/api/chat", {"model": "x"})
        text = str(caught.exception)
        self.assertIn("HTTP 500", text)
        self.assertIn("more system memory", text, "the reason is the whole point of reading the body")
        self.assertNotIn("127.0.0.1", text, "the endpoint is not part of the user's problem")
        self.assertNotIn('"model"', text, "the request is never echoed back")

    def test_a_provider_body_cannot_turn_into_a_second_log(self):
        """A 200 KiB error body must not become the message the strip has to render."""
        import io
        from urllib.error import HTTPError
        from ai_code_engineer import providers as providers_module
        huge = b'{"error":"' + b"x" * 90_000 + b'"}'

        class RefusingOpener:
            def open(self, *args, **kwargs):
                raise HTTPError("http://127.0.0.1:11434/api/chat", 502, "Bad Gateway", {},
                                io.BytesIO(huge))
            def close(self):
                pass

        with patch("ai_code_engineer.providers.build_opener", return_value=RefusingOpener()):
            with self.assertRaises(ProviderError) as caught:
                providers_module.request_json("http://127.0.0.1:11434/api/chat", {})
        self.assertLess(len(str(caught.exception)), 260, str(caught.exception)[:60])

    def test_a_body_that_will_not_read_still_reports_the_status_code(self):
        import io
        from urllib.error import HTTPError
        from ai_code_engineer import providers as providers_module

        class EmptyOpener:
            def open(self, *args, **kwargs):
                raise HTTPError("http://127.0.0.1:11434/api/chat", 503, "Unavailable", {}, None)
            def close(self):
                pass

        with patch("ai_code_engineer.providers.build_opener", return_value=EmptyOpener()):
            with self.assertRaises(ProviderError) as caught:
                providers_module.request_json("http://127.0.0.1:11434/api/chat", {})
        self.assertIn("HTTP 503", str(caught.exception))

    def test_the_context_window_is_asked_for_rather_than_left_to_a_silent_default(self):
        """Measured on this machine: with no `num_ctx`, Ollama answered a 20 000-character prompt and
        a 60 000-character one with the same `prompt_eval_count=2050`. It had truncated both to its own
        default and reported nothing, so a proposal could be written from a third of the files."""
        from ai_code_engineer import providers as providers_module
        self.assertEqual(providers_module.context_window(0, 4096), 8192,
                         "the reply needs room too, and output_tokens already promises 4096")
        self.assertEqual(providers_module.context_window(2_000, 1024), 2048)
        self.assertEqual(providers_module.context_window(20_000, 4096), 16384)
        self.assertEqual(providers_module.context_window(4_000_000, 4096),
                         providers_module.MAX_CTX, "a CPU box is clamped, not given a blank cheque")

    def test_every_window_is_a_power_of_two_at_least_the_default(self):
        from ai_code_engineer import providers as providers_module
        for chars in (0, 1, 4, 100, 5_000, 40_000, 500_000):
            for out in (256, 1024, 4096, 8192):
                window = providers_module.context_window(chars, out)
                self.assertGreaterEqual(window, providers_module.MIN_CTX)
                self.assertLessEqual(window, providers_module.MAX_CTX)
                self.assertEqual(window & (window - 1), 0, f"{chars}/{out} gave {window}")

    def test_the_ollama_request_carries_the_window_it_sized(self):
        from ai_code_engineer import providers as providers_module
        seen = {}

        def fake_request(url, payload, **kwargs):
            seen.update(payload)
            return {"message": {"content": '{"action":"blocked","reason":"test"}'}}

        with patch("ai_code_engineer.providers.request_json", side_effect=fake_request):
            provider = OllamaProvider(Settings())
            provider.supports_thinking = False
            provider.generate([{"role": "user", "content": "x" * 30_000}])
        self.assertEqual(seen["options"]["num_ctx"],
                         providers_module.context_window(30_000, Settings().output_tokens))

    def test_non_table_config_fails_cleanly(self):
        path = self.base / "invalid.toml"
        path.write_text('model = []\n')
        with self.assertRaises(AgentError):
            load_settings(path)

    def test_fenced_or_prefixed_json_still_parses(self):
        payload = '{"action": "propose", "summary": "s", "checks": ["c"], "changes": []}'
        for raw in (payload, "```json\n" + payload + "\n```", "Here you go:\n" + payload + "\nDone.",
                    "noise {not json} then " + payload):
            with self.subTest(raw=raw[:20]):
                self.assertEqual(parse_action(raw)["action"], "propose")

    def test_unparseable_output_is_still_rejected(self):
        for raw in ("", "no json here", '{"action": "propose"', "[1, 2, 3]"):
            with self.subTest(raw=raw), self.assertRaises(PolicyError):
                parse_action(raw)

    def test_unread_edit_is_read_for_the_model_instead_of_dead_ending(self):
        # Turn 1 proposes a blind replacement of app.py; the runtime should fetch the
        # current content itself so turn 2 can propose against it, not end the run.
        provider = ScriptedProvider([self.proposal("answer = 2\n"), self.proposal("answer = 3\n")])
        path = plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        reads = [item for item in session["events"] if item["kind"] == "auto_read"]
        self.assertEqual([item["path"] for item in reads], ["app.py"])
        self.assertEqual(reads[0]["sha256"], digest((self.root / "app.py").read_bytes()))
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual(session["changes"][0]["before"].strip(), "answer = 1")
        self.assertNotIn("answer = 2", (self.root / "app.py").read_text(encoding="utf-8"))
        self.assertNotIn("answer = 3", (self.root / "app.py").read_text(encoding="utf-8"))

    def test_task_budget_follows_a_patient_request_timeout(self):
        # Each turn lands 1300s later than the last: the default budget stops the run
        # before the first model call, a 900s request timeout buys patience instead.
        def clock():
            moment = [0.0]

            def tick():
                value = moment[0]
                moment[0] += 1300
                return value
            return tick

        results = []
        for timeout, expected_calls in ((120, 0), (900, 2)):
            provider = ListingProvider()
            with patch("time.monotonic", clock()):
                with self.assertRaises(AgentError) as caught:
                    plan(self.ws, "change", provider,
                         replace(Settings(), timeout_seconds=timeout), self.base / "runs",
                         progress=lambda _: None)
            self.assertIn("Task time budget exhausted", str(caught.exception))
            results.append(provider.calls)
        self.assertEqual(results, [0, 2])

    def test_mass_removal_is_surfaced_in_review(self):
        (self.root / "big.py").write_text("".join(f"value_{i} = {i}\n" for i in range(12)),
                                          encoding="utf-8")
        provider = ScriptedProvider([
            {"action": "read_file", "path": "big.py"},
            {"action": "propose", "summary": "Trim the module", "checks": ["import works"],
             "changes": [{"path": "big.py", "content": "value_0 = 0\n"}]}])
        path = plan(self.ws, "trim big.py", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        text = review(load_session(path))
        self.assertIn("Check these removals before approving", text)
        self.assertIn("removes 11 of 12 existing lines", text)
        self.assertIsNone(shrink_warning({"path": "x", "before": "a\n" * 12,
                                          "after": "a\n" * 11 + "b\n"}))
        self.assertIsNone(shrink_warning({"path": "x", "before": None, "after": "brand new\n"}))

    UNKNOWN = {"action": "shell", "command": "whoami"}
    ECHO = ("Unknown action or invalid fields. Allowed: list_files, read_file, search_code, "
            "find_symbol, find_references, propose, blocked.")

    def blocked(self, reason):
        return {"action": "blocked", "reason": reason}

    def new_java(self):
        return "src/main/java/com/example/auth/JwtTokenProvider.java"

    def test_an_edit_to_a_file_the_task_never_named_is_called_out_to_the_model(self):
        """M3 measured this: asked for `ApiResponse.java`, the model spent its turns editing the file the
        *previous* task had touched. The engine refused the edit; nothing told the model it had drifted."""
        provider = RecordingProvider([
            {"action": "read_file", "path": "app.py"},
            {"action": "propose", "summary": "Adjust the filter", "checks": ["mvn test"],
             "changes": [{"path": "app.py",
                          "edits": [{"search": "text that is not in the file", "replace": "x"}]}]},
            {"action": "propose", "summary": "Create the envelope", "checks": ["mvn test"],
             "changes": [{"path": "api/ApiResponse.java",
                          "content": "package com.example.api;\npublic class ApiResponse {}\n"}]}])
        path = plan(self.ws, "Create exactly one new file: api/ApiResponse.java", provider,
                    Settings(), self.base / "runs", progress=lambda _: None)
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL")
        told = " ".join(message["content"] for message in provider.prompts[-1])
        self.assertIn("a file this task never named", told)
        self.assertIn("api/ApiResponse.java", told, "the remedy has to point back at the asked-for file")

    def test_a_rejection_also_says_what_to_do_next(self):
        """D15: the remedy sentence was computed and then thrown away on every path but the stale-read
        one, so a rejected proposal reached the model as a bare error — and it answered by proposing the
        same bytes again, three times, until the loop guard stopped it."""
        unchanged = (self.root / "app.py").read_bytes().decode("utf-8")
        provider = RecordingProvider([{"action": "read_file", "path": "app.py"},
                                      self.proposal(unchanged),
                                      self.proposal("answer = 2\n")])
        path = plan(self.ws, "change", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertIn("unchanged file", " ".join(str(item.get("reason", ""))
                                                 for item in session["events"]),
                      "the test premise is a rejection; without one it proves nothing")
        told = " ".join(message["content"] for message in provider.prompts[-1])
        self.assertIn("identical to the file already on disk", told)
        self.assertIn("next_action", told, "the advice has to arrive as advice, not as more error text")

    def test_a_rejected_action_records_which_rejection_it_was(self):
        """D12, measured on the ecommerce run: three `rejected_action` rows in a BLOCKED session, all
        of them blank, because the reason was in scope one line above and never written."""
        provider = ScriptedProvider([self.UNKNOWN, self.blocked(self.ECHO),
                                     {"action": "read_file", "path": "app.py"},
                                     self.proposal("answer = 2\n")])
        path = plan(self.ws, "change", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        rejected = [item for item in load_session(path)["events"] if item["kind"] == "rejected_action"]
        self.assertEqual(len(rejected), 1)
        self.assertIn("Unknown action", rejected[0]["reason"])

    def test_an_empty_anchor_is_told_what_to_anchor_on(self):
        """The JwtService repair round: asked to fix one import line, the model answered with
        {"search": "", "replace": "import …"} twice running. The refusal was right — an empty block
        matches anywhere — and it is the only hole in the edit contract, so it has to say how a line
        is actually added."""
        (self.root / "app.py").write_bytes(b"answer = 1\n")     # write_text would add CR on Windows
        provider = RecordingProvider([
            {"action": "read_file", "path": "app.py"},
            {"action": "propose", "summary": "Add a line", "checks": ["python -m unittest"],
             "changes": [{"path": "app.py", "edits": [{"search": "", "replace": "extra = 3\n"}]}]},
            {"action": "propose", "summary": "Add a line", "checks": ["python -m unittest"],
             "changes": [{"path": "app.py",
                          "edits": [{"search": "answer = 1\n", "replace": "answer = 1\nextra = 3\n"}]}]}])
        path = plan(self.ws, "add a line to app.py", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertIn("extra = 3", session["changes"][0]["after"])
        told = " ".join(message["content"] for message in provider.prompts[-1])
        self.assertIn("quote text that is already in the file", told)
        self.assertIn("plus yours", told)

    def test_a_refused_file_name_says_which_name_and_says_the_right_remedy(self):
        """Measured on the ecommerce run: two 5-minute turns ended BLOCKED on `Only supported text
        files are accessible.` — the sentence named nothing and the advice said *choose an action
        again*, so the model chose the same action, the same name, until the loop guard fired."""
        provider = RecordingProvider([
            {"action": "propose", "summary": "Add the ignore list", "checks": ["git status"],
             "changes": [{"path": "ignore-list", "content": "target/\n"}]},
            {"action": "propose", "summary": "Add the ignore list", "checks": ["git status"],
             "changes": [{"path": ".gitignore", "content": "target/\n"}]}])
        path = plan(self.ws, "create the ignore list", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual([item["path"] for item in session["changes"]], [".gitignore"])
        refused = [item for item in session["events"] if item["kind"] == "rejected_action"]
        self.assertIn("ignore-list", refused[-1]["reason"])
        told = " ".join(message["content"] for message in provider.prompts[-1])
        self.assertIn("cannot write", told)
        self.assertNotIn("Choose exactly one action again", told,
                         "shape advice for a name problem is what made the model repeat the name")

    def test_a_refused_shape_shows_the_model_its_own_field_names(self):
        """The JwtService fix round died on three copies of one reply, and the history says only that
        they were copies: the refusal was "invalid fields", which is true of eight different mistakes
        and fixes none of them. Field names are the half of a reply that carries no project content,
        so they can be echoed — and the stop line can finally say what was being refused."""
        same = {"action": "propose", "summary": "Fix the import", "checks": ["mvn test"],
                "path": "ecommerce-common-lib/src/main/java/a/JwtService.java",
                "changes": [{"path": "app.py", "content": "answer = 2\n"}]}
        runs = self.base / "runs"
        provider = ScriptedProvider([same, same, same])
        with self.assertRaises(AgentError) as caught:
            plan(self.ws, "change", provider, Settings(), runs, progress=lambda _: None)
        session = load_session(next(runs.glob("*/session.json")))
        refused = [item["reason"] for item in session["events"]
                   if item["kind"] == "rejected_action"]
        self.assertIn("with fields action, changes, checks, path, summary", refused[0])
        self.assertIn("was refused with", session["error"])
        self.assertIn("invalid fields", str(caught.exception))

    def test_a_proposal_that_forgets_its_prose_is_still_a_proposal(self):
        """Measured twice in the ecommerce run: a model copying a 3 k-character file loses `summary`
        and `checks` before it loses the file, and refusing the write cost a six-minute turn each time
        for two sentences nobody executes."""
        provider = RecordingProvider([
            {"action": "propose", "changes": [{"path": "app.py", "content": "answer = 42\n"}]},
            {"action": "propose", "changes": [{"path": "app.py", "content": "answer = 42\n"}]},
        ])
        path = plan(self.ws, "make app.py answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual([item["path"] for item in session["changes"]], ["app.py"])
        self.assertEqual(session["summary"], NO_SUMMARY)
        self.assertEqual(session["checks"], DEFAULT_CHECKS)

    def test_a_summary_of_the_wrong_kind_is_still_refused(self):
        """Filling what is absent is not the same as accepting what is wrong: the field has a type,
        and a summary that is not text cannot be shown to anyone."""
        bad = {"action": "propose", "summary": 42, "checks": ["mvn test"],
               "changes": [{"path": "app.py", "content": "answer = 42\n"}]}
        good = {"action": "propose", "summary": "Answer", "checks": ["mvn test"],
                "changes": [{"path": "app.py", "content": "answer = 42\n"}]}
        provider = RecordingProvider([bad, good, good])   # refused for its type, then for the unread file
        path = plan(self.ws, "make app.py answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        refused = [item for item in session["events"] if item["kind"] == "rejected_action"]
        self.assertIn("short summary", refused[0]["reason"])
        self.assertIn("short summary", " ".join(m["content"] for m in provider.prompts[1]))

    def test_an_asking_nested_under_args_is_obeyed_rather_than_charged_as_a_mistake(self):
        """The function-calling dialect every model is tuned on: `read_file` with its path inside
        `args` is the asking the prompt made, and refusing it on the envelope used to cost three
        refusals and the run — the invalid-action budget — for a model that never got it wrong."""
        provider = ScriptedProvider([
            {"action": "read_file", "args": {"path": "app.py"}},
            {"action": "propose", "parameters": {
                "summary": "Update answer", "checks": ["unit tests"],
                "changes": [{"path": "app.py", "content": "answer = 2\n"}]}}])
        path = plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual(session["summary"], "Update answer")
        self.assertEqual([item["path"] for item in session["changes"]], ["app.py"])
        self.assertFalse([item for item in session["events"]
                          if item["kind"] == "rejected_action"], session["events"])

    def test_a_decision_that_explains_itself_is_judged_on_the_asking_not_the_prose(self):
        """A reasoning model writes its deliberation into the envelope it was asked for, under
        `thought` where the prompt said `reason`. The prose is no part of the contract, so it comes
        out before validation — and, being a raw reply, is recorded nowhere."""
        provider = ScriptedProvider([
            {"action": "read_file", "path": "app.py", "thought": "checking the current value"},
            {"action": "propose", "summary": "Update answer", "checks": ["unit tests"],
             "reason": "one line changes",
             "changes": [{"path": "app.py", "content": "answer = 2\n"}]}])
        path = plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual(session["summary"], "Update answer")
        recorded = json.dumps(session["events"])
        self.assertNotIn("checking the current value", recorded)
        self.assertNotIn("one line changes", recorded)

    def test_a_check_the_policy_denies_is_an_observation_and_the_model_can_still_propose(self):
        """The registry asks the loop's verdict before any handler runs, so a refused class costs
        the model one turn and a sentence rather than a command — and a proposal without the check
        is still a result. The verdict is the module's own function, peeked at here by answering it
        differently: exactly the seam the boundary was built as."""
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text(
            "import unittest\n\n\nclass One(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertTrue(True)\n", encoding="utf-8")
        provider = RecordingProvider([{"action": "run_tests"},
                                      {"action": "read_file", "path": "app.py"},
                                      self.proposal()])
        answers = {policy.EXECUTE_RECIPE: policy.DENY, policy.READ: policy.ALLOW}

        def decide(name, override=""):
            return answers.get(name, policy.ALLOW)

        with patch.object(policy, "decide", new=decide):
            path = plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                        progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertFalse([item for item in session["events"]
                          if item["kind"] == "tool" and item["name"] == "run_tests"],
                         "the denied class never reached its handler")
        rejection = [item for item in session["events"] if item["kind"] == "rejected_action"]
        self.assertTrue(rejection, session["events"])
        self.assertIn("policy denies", rejection[0]["reason"])
        told = " ".join(message["content"] for message in provider.prompts[1])
        self.assertIn("policy denies", told)
        self.assertIn("run_tests", told)

    def test_a_failed_check_becomes_continuity_evidence_the_next_prompt_carries(self):
        """The tool's own verdict is folded into the task state by the loop, not left to the model
        to restate, so the turn after the failure reads it from the state block the way it reads
        its own entries — the record keeps what the trimmed history would have dropped."""
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_app.py").write_text(
            "import unittest\n\n\nclass One(unittest.TestCase):\n"
            "    def test_bad(self):\n        self.assertTrue(False, \"boom\")\n", encoding="utf-8")
        provider = RecordingProvider([{"action": "run_tests"},
                                      {"action": "read_file", "path": "app.py"},
                                      self.proposal()])
        path = plan(self.ws, "Update answer", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        evidence = session["task_state"]["evidence"]
        self.assertTrue(any(item.startswith("run_tests (python-unittest): failed")
                            for item in evidence), evidence)
        told = " ".join(message["content"] for message in provider.prompts[1])
        self.assertIn("run_tests (python-unittest): failed", told)

    def test_a_blocked_task_keeps_the_reason_that_blocked_it(self):
        provider = ScriptedProvider([self.UNKNOWN] * 5)
        runs = self.base / "runs"
        with self.assertRaises(AgentError) as caught:
            plan(self.ws, "change", provider, replace(Settings(), max_turns=8), runs,
                 progress=lambda _: None)
        session = load_session(next(runs.glob("*/session.json")))
        self.assertEqual(session["state"], "BLOCKED")
        self.assertEqual(session["error"], redact(str(caught.exception))[:300],
                         "the history replay reads this field, and it used to be empty for every failure")
        stopped = [item for item in session["events"] if item["kind"] == "stopped"]
        self.assertEqual(stopped[-1]["reason"], session["error"][:180])

    def test_blocked_after_a_rejected_action_gets_one_more_turn(self):
        provider = ScriptedProvider([self.UNKNOWN, self.blocked(self.ECHO),
                                     {"action": "read_file", "path": "app.py"},
                                     self.proposal("answer = 2\n")])
        path = plan(self.ws, "change", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual([item["attempt"] for item in session["events"]
                          if item["kind"] == "blocked_retried"], [1])
        self.assertEqual(session["state"], "WAITING_APPROVAL")

    def test_blocked_on_a_missing_file_bounces_into_creating_it(self):
        # Observed live: the model reads a file the task asks it to create, is told a
        # new-file proposal is allowed, and answers by blocking on that same message.
        provider = ScriptedProvider([
            {"action": "read_file", "path": self.new_java()},
            self.blocked("The file " + self.new_java() + " does not exist."),
            {"action": "propose", "summary": "Add the token provider", "checks": ["mvn test"],
             "changes": [{"path": self.new_java(),
                          "content": "package com.example.auth;\npublic class JwtTokenProvider {}\n"}]}])
        path = plan(self.ws, "Create the JWT provider", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual([item["attempt"] for item in session["events"]
                          if item["kind"] == "blocked_retried"], [1])
        self.assertIsNone(session["changes"][0]["before"])
        self.assertFalse((self.root / self.new_java()).exists())

    def test_a_blocked_reason_that_is_not_runtime_echoing_ends_at_once(self):
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"},
                                     self.blocked("The task requires a database this project cannot reach.")])
        with self.assertRaises(AgentError) as caught:
            plan(self.ws, "change", provider, Settings(), self.base / "runs",
                 progress=lambda _: None)
        self.assertIn("Model could not produce a proposal", str(caught.exception))

    def test_a_block_that_explains_itself_in_thought_blocks_on_that_explanation(self):
        """The reason is what a blocked run is read by afterwards, so the sentence has to survive
        being sent under the other name — and the loop's judgement of it does not change with the key
        it arrived in."""
        provider = ScriptedProvider([
            {"action": "read_file", "path": "app.py"},
            {"action": "blocked", "thought": "The task needs a database this project cannot reach."}])
        with self.assertRaises(AgentError) as caught:
            plan(self.ws, "change", provider, Settings(), self.base / "runs",
                 progress=lambda _: None)
        self.assertIn("a database this project cannot reach", str(caught.exception))

    def test_a_block_with_nothing_to_say_is_still_asked_to_say_something(self):
        """Tolerating the second name is not tolerating the absence of one: an explanation that is
        not a sentence leaves the loop with nothing to record, which is the refusal it always was."""
        said = "The task needs a database this project cannot reach."
        provider = RecordingProvider([{"action": "blocked", "reason": 42},
                                      {"action": "blocked"},
                                      self.blocked(said), self.blocked(said + " Now."),
                                      self.blocked(said + " Please.")])
        with self.assertRaises(AgentError) as caught:
            plan(self.ws, "change", provider, Settings(), self.base / "runs",
                 progress=lambda _: None)
        self.assertIn("Model could not produce a proposal", str(caught.exception))
        told = " ".join(message["content"] for message in provider.prompts[1])
        self.assertIn("A blocked action requires a short reason", told)

    def test_persistent_blocked_still_ends_the_run(self):
        provider = ScriptedProvider([self.UNKNOWN]
                                    + [self.blocked(self.ECHO + suffix)
                                       for suffix in ("", " now", " please")])
        with self.assertRaises(AgentError) as caught:
            plan(self.ws, "change", provider, Settings(), self.base / "runs",
                 progress=lambda _: None)
        self.assertIn("Model could not produce a proposal", str(caught.exception))
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "answer = 1\n")

    def test_source_files_that_mention_secrets_stay_visible(self):
        # Renamed policy: only credential *files* are blocked, not any identifier.
        for name in ("JwtTokenProvider.java", "PasswordHasher.java", "tokenStore.ts",
                     "src/main/java/com/example/SecretsFactory.java", "keygen.py"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("class Sample {}\n", encoding="utf-8")
            with self.subTest(name=name):
                self.ws.path(name)
        for name in ("token.json", "aws-secret.txt", "my_password.py", "credentials.yml",
                    ".env.production", "token-service.ts"):
            with self.subTest(blocked=name), self.assertRaises(PolicyError):
                self.ws.path(name)

    def test_new_project_folder_must_be_absent_or_empty(self):
        fresh = self.base / "new" / "project"
        self.assertEqual(ensure_project_dir(fresh), fresh.resolve())
        self.assertTrue(fresh.is_dir())
        with self.assertRaises(PolicyError):
            ensure_project_dir(self.root)
        with self.assertRaises(PolicyError):
            ensure_project_dir(self.root / "app.py")
        with self.assertRaises(PolicyError):
            ensure_project_dir("relative/path")
        with self.assertRaises(PolicyError):
            ensure_project_dir("C:/")


class PromptDirectiveTests(unittest.TestCase):
    """The prompt is a contract. A directive nobody can grep for is one that quietly
    disappears at the next rewrite, so the sentence the user asked for is asserted here."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        root = self.base / "repo"
        root.mkdir()
        (root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(root)

    def system_prompt(self, task):
        """list_files is a no-op the loop keeps asking for, so one provider answers every turn."""

        class Recorder:
            model = "test"

            def __init__(self):
                self.prompts = []

            def generate(self, messages):
                self.prompts.append([dict(message) for message in messages])
                return json.dumps({"action": "list_files"})

        provider = Recorder()
        with self.assertRaises(AgentError):     # the loop gives up on a model that never proposes
            plan(self.ws, task, provider, Settings(), self.base / "runs", progress=lambda _: None)
        self.assertEqual(provider.prompts[0][0]["role"], "system")
        return provider.prompts[0][0]["content"]

    def test_a_build_task_is_answered_with_a_proposal_not_a_tutorial(self):
        system = self.system_prompt("Scaffold a Flask API")
        self.assertIn("never with instructions for the user to create those files by hand", system)
        self.assertIn("not the same as proposing one", system)

    def test_the_directive_does_not_claim_a_write_the_user_has_not_approved(self):
        system = self.system_prompt("Scaffold a Flask API")
        self.assertIn("write access through proposals", system)
        self.assertIn("The user reviews the diff before writing", system)


class ChunkProposalTests(unittest.TestCase):
    """§6: a small change can be quoted as exact search/replace hunks instead of a whole file."""

    SOURCE = ("def add(a, b):\n"
              "    return a - b\n"
              "\n"
              "def sub(a, b):\n"
              "    return a - b\n")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.file = self.root / "app.py"
        # LF on purpose: a model quotes back exactly the bytes it read, and the resolver
        # matches against them without forgiving anything — including line endings.
        self.file.write_text(self.SOURCE, encoding="utf-8", newline="\n")
        self.ws = Workspace(self.root)

    def draft(self, changes):
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"},
                                     {"action": "propose", "summary": "fix add",
                                      "checks": ["unit tests"], "changes": changes}])
        return plan(self.ws, "fix add", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)

    def refused(self, changes):
        """A bad shape is a PolicyError from the resolver, before any session exists.

        `plan()` deliberately feeds such an error back to the model as an observation, so
        testing the refusal itself means calling the resolver the loop calls.
        """
        observed = {name: self.ws.read(name)["sha256"]
                    for name in ("app.py", "other.py") if self.ws.path(name).exists()}
        with self.assertRaises(PolicyError) as caught:
            prepare_changes(self.ws, changes, observed)
        return str(caught.exception)

    def test_a_unique_block_is_replaced_and_the_rest_survives(self):
        path = self.draft([{"path": "app.py", "edits": [
            {"search": "def add(a, b):\n    return a - b", "replace": "def add(a, b):\n    return a + b"}]}])
        session = load_session(path)
        self.assertEqual(session["changes"][0]["after"],
                         self.SOURCE.replace("def add(a, b):\n    return a - b",
                                             "def add(a, b):\n    return a + b"))
        self.assertEqual(apply_proposal(path, session["proposal_hash"])["state"],
                         "APPLIED_UNVERIFIED")
        self.assertEqual(self.file.read_text(encoding="utf-8"), session["changes"][0]["after"])
        self.assertIn("def sub(a, b):", self.file.read_text(encoding="utf-8"),
                      "a chunk must not touch a line it never quoted")

    def test_rollback_of_a_chunk_applied_file_restores_the_original_bytes(self):
        path = self.draft([{"path": "app.py", "edits": [
            {"search": "def add(a, b):", "replace": "def plus(a, b):"}]}])
        approved = load_session(path)["proposal_hash"]
        apply_proposal(path, approved)
        rollback(path, approved)
        self.assertEqual(self.file.read_text(encoding="utf-8"), self.SOURCE)

    def test_a_block_that_matches_nothing_names_the_line_that_does_not_exist(self):
        message = self.refused([{"path": "app.py", "edits": [
            {"search": "def mul(a, b):\n    return a * b", "replace": "pass"}]}])
        self.assertIn("matches nothing", message)
        self.assertIn("def mul(a, b):", message)
        self.assertIn("app.py", message)

    def test_a_block_that_matches_twice_lists_every_line_number(self):
        message = self.refused([{"path": "app.py", "edits": [
            {"search": "    return a - b", "replace": "    return 0"}]}])
        self.assertIn("matches 2 places", message)
        self.assertIn("lines 2, 5", message)
        self.assertIn("Widen", message)

    def test_two_hunks_cannot_both_claim_the_same_bytes(self):
        message = self.refused([{"path": "app.py", "edits": [
            {"search": "def add(a, b):", "replace": "def plus(a, b):"},
            {"search": "def plus(a, b):\n    return a - b", "replace": "def other(a, b):"}]}])
        self.assertIn("overlaps", message)

    def test_an_empty_replacement_deletes_the_block(self):
        path = self.draft([{"path": "app.py", "edits": [
            {"search": "\ndef sub(a, b):\n    return a - b\n", "replace": ""}]}])
        apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertEqual(self.file.read_text(encoding="utf-8"),
                         "def add(a, b):\n    return a - b\n")

    def test_hunks_apply_in_order_so_the_second_sees_the_first(self):
        """`return a - b` appears twice, so only the second hunk can be unique — after the first."""
        path = self.draft([{"path": "app.py", "edits": [
            {"search": "\ndef sub(a, b):\n    return a - b\n", "replace": ""},
            {"search": "    return a - b", "replace": "    return a + b"}]}])
        apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertEqual(self.file.read_text(encoding="utf-8"),
                         "def add(a, b):\n    return a + b\n")

    def test_the_shorthand_one_hunk_shape_is_accepted(self):
        path = self.draft([{"path": "app.py", "search": "return a - b\n\ndef sub",
                             "replace": "return a + b\n\ndef sub"}])
        apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertIn("return a + b", self.file.read_text(encoding="utf-8"))

    def test_a_chunk_and_a_whole_file_can_share_one_proposal(self):
        (self.root / "other.py").write_text("x = 1\n", encoding="utf-8")
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"},
                                     {"action": "read_file", "path": "other.py"},
                                     {"action": "propose", "summary": "two shapes",
                                      "checks": ["add is renamed", "other is updated"],
                                      "changes": [{"path": "app.py", "edits": [
                                          {"search": "def add", "replace": "def plus"}]},
                                          {"path": "other.py", "content": "x = 2\n"}]}])
        path = plan(self.ws, "two shapes", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        session = load_session(path)
        self.assertEqual(len(session["changes"]), 2)
        apply_proposal(path, session["proposal_hash"])
        self.assertIn("def plus", self.file.read_text(encoding="utf-8"))
        self.assertEqual((self.root / "other.py").read_text(encoding="utf-8"), "x = 2\n")

    def test_a_file_that_moved_after_the_chunk_was_resolved_blocks_apply(self):
        path = self.draft([{"path": "app.py", "edits": [
            {"search": "def add(a, b):", "replace": "def plus(a, b):"}]}])
        approved = load_session(path)["proposal_hash"]
        self.file.write_text("human = True\n", encoding="utf-8")
        with self.assertRaises(PolicyError):
            apply_proposal(path, approved)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "human = True\n")

    def test_the_resolved_content_is_what_the_hash_covers(self):
        path = self.draft([{"path": "app.py", "edits": [
            {"search": "def add(a, b):", "replace": "def plus(a, b):"}]}])
        session = load_session(path)
        self.assertEqual(session["proposal_hash"], proposal_hash(session))
        session["changes"][0]["after"] = session["changes"][0]["after"].replace("plus", "minus")
        self.assertNotEqual(session["proposal_hash"], proposal_hash(session),
                            "editing the resolved text must break the approval")

    def test_a_session_write_waits_out_a_momentary_reader(self):
        """Windows denies the rename while anything holds the destination open — measured live, where a
        session write failed in the middle of an apply because a second process was reading that file."""
        target = self.base / "state" / "session.json"
        real, attempts = os.replace, []

        def flaky(src, dst):
            attempts.append(dst)
            if len(attempts) < 3:
                raise PermissionError(5, "Access is denied", dst)
            real(src, dst)

        with patch("ai_code_engineer.engine.os.replace", side_effect=flaky):
            atomic_json(target, {"state": "APPLIED"})
        self.assertEqual(len(attempts), 3, "the write waited rather than failing the task")
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["state"], "APPLIED")

    def test_the_wait_gives_up_rather_than_hanging_a_job(self):
        target = self.base / "state" / "session.json"

        with patch("ai_code_engineer.engine.os.replace",
                   side_effect=PermissionError(5, "Access is denied")), \
                patch("ai_code_engineer.engine.time.sleep"):
            with self.assertRaises(PermissionError):
                atomic_json(target, {"state": "APPLIED"})

    def test_an_edit_needs_an_existing_file(self):
        message = self.refused([{"path": "brand-new.py", "edits": [
            {"search": "x", "replace": "y"}]}])
        self.assertIn("new file", message)

    def test_an_empty_search_on_a_missing_file_gets_the_new_file_answer(self):
        """Not "it would match anywhere" — there is no anywhere. The model meant "write the file"."""
        message = self.refused([{"path": "Api.java", "edits": [{"search": "", "replace": "x"}]}])
        self.assertIn("new file needs complete content", message)
        self.assertNotIn("match anywhere", message)

    def test_shape_rules(self):
        for change, hint in (
            ([{"path": "app.py", "edits": []}], "1 and 10"),
            ([{"path": "app.py", "edits": [{"search": "", "replace": "x"}]}], "empty search"),
            ([{"path": "app.py", "edits": [{"search": "def add", "replace": None}]}], "as a string"),
            ([{"path": "app.py", "edits": [{"search": "def add"}]}], "only search and replace"),
            ([{"path": "app.py", "edits": [{"search": "def add", "replace": "x"}],
               "content": "junk"}], "complete content"),
        ):
            with self.subTest(hint=hint):
                self.assertIn(hint, self.refused(change))

    def test_a_chunk_that_produces_invalid_python_is_refused(self):
        message = self.refused([{"path": "app.py", "edits": [
            {"search": "def add(a, b):\n    return a - b", "replace": "def add(a, b):"}]}])
        self.assertIn("Invalid syntax", message)

    # ---------------- the same gate for the languages the run actually writes ----------------
    JAVA_UNIT = ("package com.ecommerce.eureka;\n\n"
                 "import org.springframework.boot.SpringApplication;\n\n"
                 "public class EurekaServerApplication {\n"
                 "    public static void main(String[] args) {\n"
                 "        SpringApplication.run(EurekaServerApplication.class, args);\n"
                 "    }\n}\n")
    POM_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n<project><modelVersion>4.0.0</modelVersion>'
               '<artifactId>eureka-server</artifactId></project>\n')

    def accepted(self, changes):
        return prepare_changes(self.ws, changes, {})

    def accepts(self, changes):
        """A gate that refuses legitimate files is worse than one that misses a bad one, so every
        refusal test here names the shape that must still get through."""
        return [item["path"] for item in self.accepted(changes)]

    BAD_POM = ('<?xml version="1.0" encoding="UTF-8"?>\n<project>\n'
               '<modelVersion>4.0.0</modelVersion>\n'
               '<dependency><groupId>org.springframework.boot</groupId>'
               '<artifactId>spring-boot-starter-aop</artifactId></dependency>\n</project>\n')
    GOOD_POM = ('<?xml version="1.0" encoding="UTF-8"?>\n<project>\n'
                '<modelVersion>4.0.0</modelVersion>\n<dependencies>\n'
                '<dependency><groupId>org.springframework.boot</groupId>'
                '<artifactId>spring-boot-starter-aop</artifactId></dependency>\n'
                '</dependencies></project>\n')

    def test_a_dependency_hanging_off_project_is_refused_though_it_parses(self):
        """D34, and the premise the spec got wrong: this file is well-formed XML, so a before/after
        AST comparison passes it — `git show` of the pom that broke the build parses fine today.
        What Maven rejected was the model, so the check is about where the element sits."""
        import xml.etree.ElementTree as ElementTree
        ElementTree.fromstring(self.BAD_POM)                 # the premise, asserted
        message = self.refused([{"path": "pom.xml", "content": self.BAD_POM}])
        self.assertIn("Unrecognised tag", message)
        self.assertIn("<dependencies>", message, "the refusal has to name where it belongs")

    def test_the_same_dependency_under_dependencies_is_accepted(self):
        self.assertEqual(self.accepts([{"path": "pom.xml", "content": self.GOOD_POM}]), ["pom.xml"])

    def test_a_managed_dependency_is_accepted_where_maven_accepts_it(self):
        managed = self.GOOD_POM.replace("<dependencies>", "<dependencyManagement><dependencies>", 1)
        managed = managed.replace("</dependencies>", "</dependencies></dependencyManagement>", 1)
        self.assertEqual(self.accepts([{"path": "pom.xml", "content": managed}]), ["pom.xml"])

    def test_a_non_pom_document_is_untouched_by_the_check(self):
        changelog = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                     '<databaseChangeLog xmlns="http://www.liquibase.org/xml/ns/dbchangelog">'
                     '<changeSet author="agent" id="1"><createTable tableName="orders"/>'
                     '</changeSet></databaseChangeLog>\n')
        self.assertEqual(self.accepts([{"path": "db.changelog-master.xml", "content": changelog}]),
                         ["db.changelog-master.xml"])

    def test_a_namespaced_pom_is_checked_like_every_pom_that_exists(self):
        """D43's real defect, and not the one the spec named: `xmlns` makes ElementTree report the tag
        as `{http://…}dependency`, so the raw comparison returned at the root and the gate was a no-op
        on every Maven file in the wild — including the one this project dogfoods on."""
        import xml.etree.ElementTree as ElementTree
        namespace = ' xmlns="http://maven.apache.org/POM/4.0.0"'
        bad = self.BAD_POM.replace("<project>", "<project" + namespace + ">", 1)
        ElementTree.fromstring(bad)                            # well-formed, as always
        message = self.refused([{"path": "pom.xml", "content": bad}])
        self.assertIn("Unrecognised tag", message)
        good = self.GOOD_POM.replace("<project>", "<project" + namespace + ">", 1)
        self.assertEqual(self.accepts([{"path": "pom.xml", "content": good}]), ["pom.xml"])

    def resources_case(self, opening: str, closing: str) -> str:
        """A pom with `<resources>` in position `opening`/`closing` — empty for the top level."""
        return ('<?xml version="1.0" encoding="UTF-8"?>\n<project'
                ' xmlns="http://maven.apache.org/POM/4.0.0">\n<modelVersion>4.0.0</modelVersion>'
                + opening + "<resources><directory>src/main/resources/custom</directory></resources>"
                + closing + "</project>\n")

    def test_resources_at_the_top_of_the_pom_is_refused_for_what_it_actually_does(self):
        """Maven does not reject a misplaced `<resources>`; it ignores it. The build passes and the
        changelog is not on the classpath, so the failure arrives turns later as a missing file — which
        is a worse bug to read than a refusal that says where the element belongs."""
        message = self.refused([{"path": "pom.xml", "content": self.resources_case("", "")}])
        self.assertIn("<build>", message)

    def test_resources_under_build_and_under_a_profile_build_are_accepted(self):
        for opening, closing in (("<build>", "</build>"),
                                 ("<profiles><profile><build>", "</build></profile></profiles>")):
            self.assertEqual(self.accepts([{"path": "pom.xml",
                                            "content": self.resources_case(opening, closing)}]),
                             ["pom.xml"], opening)

    def test_resources_inside_a_plugins_configuration_are_accepted(self):
        content = self.resources_case("<build><plugins><plugin><configuration>",
                                      "</configuration></plugin></plugins></build>")
        self.assertEqual(self.accepts([{"path": "pom.xml", "content": content}]), ["pom.xml"])

    # ------------------------------ the delete entry (D31) ------------------------------
    def test_a_delete_prepares_with_the_bytes_it_takes(self):
        prepared = prepare_changes(self.ws, [{"path": "app.py", "delete": True}],
                                   {"app.py": self.ws.read("app.py")["sha256"]})
        self.assertEqual([row["path"] for row in prepared], ["app.py"])
        self.assertTrue(prepared[0]["delete"])
        self.assertIsNone(prepared[0]["after"])
        self.assertEqual(prepared[0]["before"], self.SOURCE, "rollback restores from this")

    def test_a_delete_of_a_file_nobody_read_is_refused(self):
        with self.assertRaises(PolicyError) as caught:
            prepare_changes(self.ws, [{"path": "app.py", "delete": True}], {})
        self.assertIn("Read the current file", str(caught.exception))

    def test_a_delete_of_a_missing_file_is_refused(self):
        self.assertIn("does not exist", self.refused([{"path": "ghost.py", "delete": True}]))

    def test_a_delete_shaped_like_a_flag_instead_of_true_is_refused(self):
        message = self.refused([{"path": "app.py", "delete": "yes"}])
        self.assertIn("delete", message.lower())

    # ------------------------------ one task limit (D35) ------------------------------
    def test_the_two_task_channels_refuse_the_same_length_in_the_same_words(self):
        """D35: the limit was one number written in six places and two spellings of one sentence
        ("1-4000" and "1–4000"), which is how a task came to be refused by the very channel the
        refusal told it to use."""
        long_task = "x" * (MAX_TASK_CHARS + 1)
        with self.assertRaises(AgentError) as by_plan:
            plan(self.ws, long_task, ScriptedProvider([]), Settings(), self.base / "runs",
                 progress=lambda _: None)
        with self.assertRaises(AgentError) as by_block:
            propose_block(self.ws, long_task, "app.py", "answer = 3\n", self.base / "runs")
        self.assertEqual(str(by_plan.exception), str(by_block.exception))

    def test_the_task_limit_is_one_number_wherever_it_is_spoken(self):
        from ai_code_engineer import gui
        from ai_code_engineer.labels import STATUS_TEXTS
        from ai_code_engineer.webapp import controller
        self.assertIn(f"{MAX_TASK_CHARS:,}", STATUS_TEXTS["too_long"][0])
        for module in (gui, controller):
            source = Path(module.__file__).read_text(encoding="utf-8")
            self.assertNotIn("> 4000", source, module.__name__ + " still carries its own copy")
            self.assertNotIn("[:4000]", source, module.__name__ + " still clips to a literal")

    def test_the_xml_refusal_names_a_route_that_works_when_the_file_is_too_big(self):
        """The sentence used to send the model back to whole-file content — the one channel a large
        file cannot use. This is what product-service's pom hit twice in the dogfood run."""
        message = self.refused([{"path": "broken.xml", "content": "<a><b></a>\n"}])
        self.assertIn("anchored edit", message)

    def test_a_pom_written_into_a_java_path_is_refused(self):
        """What the ecommerce run did for real: the model answered the entry point with its pom."""
        message = self.refused([{"path": "EurekaServerApplication.java", "content": self.POM_XML}])
        self.assertIn("XML where Java was asked for", message)

    def test_java_with_no_declaration_is_refused(self):
        """The loose half of the rule: a file with not one declaration keyword in it is not Java.

        It is deliberately a keyword test rather than a parser, so a comment that happens to mention
        a class does not trip it — the markup case is caught by the `<` rule above.
        """
        message = self.refused([{"path": "Loose.java", "content": '{"config": "not java"}\n'}])
        self.assertIn("no Java declaration", message)

    def test_a_java_rewrite_that_drops_the_package_line_is_refused(self):
        """D24, measured on the ecommerce run: "make the class public, every other line stays as it
        is" came back as the same file minus its package and import lines. Still legal Java, so
        `shape_mismatch` passed it and the build found out six minutes later."""
        name = "EurekaServerApplication.java"
        (self.root / name).write_text(self.JAVA_UNIT, encoding="utf-8", newline="\n")
        headless = self.JAVA_UNIT.replace("package com.ecommerce.eureka;\n\n", "")
        observed = {name: self.ws.read(name)["sha256"]}
        with self.assertRaises(PolicyError) as caught:
            prepare_changes(self.ws, [{"path": name, "content": headless}], observed)
        self.assertIn("lost its package line", str(caught.exception))
        self.assertNotEqual((self.root / name).read_text(encoding="utf-8"), headless,
                            "the refusal happens before anything is written")
        moved = self.JAVA_UNIT.replace("package com.ecommerce.eureka;", "package com.ecommerce.app;")
        self.assertEqual([item["path"] for item in
                          prepare_changes(self.ws, [{"path": name, "content": moved}], observed)],
                        [name])
        fresh = prepare_changes(self.ws, [{"path": "Fresh.java", "content": "public class Fresh {}\n"}], {})
        self.assertEqual([item["path"] for item in fresh], ["Fresh.java"])

    def test_a_java_file_that_declares_no_type_is_refused(self):
        """`UserRepository.java` arrived as 167 characters of imports with the interface missing, the
        model's own summary still said "Create UserRepository interface", and the reactor answered with
        nine errors in the file that referenced it. Truncation is the failure this names."""
        imports = ("package com.ecommerce.auth.repository;\n\n"
                   "import com.ecommerce.auth.entity.User;\n"
                   "import java.util.Optional;\n"
                   "import org.springframework.data.jpa.repository.JpaRepository;")
        message = self.refused([{"path": "UserRepository.java", "content": imports + "\n"}])
        self.assertIn("declares no Java type", message)
        whole = imports + ("\n\npublic interface UserRepository extends JpaRepository<User, Long> {\n"
                           "    Optional<User> findByUsername(String username);\n}\n")
        self.assertEqual(self.accepts([{"path": "UserRepository.java", "content": whole}]),
                         ["UserRepository.java"])
        self.assertEqual(self.accepts([{"path": "package-info.java",
                                        "content": "package com.ecommerce.auth;\n"}]),
                         ["package-info.java"])

    def test_a_real_compilation_unit_passes_the_gate(self):
        self.assertEqual(len(self.accepted([{"path": "EurekaServerApplication.java",
                                             "content": self.JAVA_UNIT}])), 1)

    def test_unbalanced_xml_is_refused_before_it_reaches_the_disk(self):
        message = self.refused([{"path": "pom.xml",
                                 "content": "<project><build><plugin><groupId>x</plugin>"}])
        self.assertIn("Invalid XML", message)

    def test_a_well_formed_pom_passes_the_gate(self):
        self.assertEqual(len(self.accepted([{"path": "pom.xml", "content": self.POM_XML}])), 1)

    def test_a_dtd_is_refused_rather_than_handed_to_the_parser(self):
        """The content is a model's output, so it is parsed like untrusted input: a DTD is the only
        thing that lets an XML parser reach beyond what the proposal itself wrote."""
        message = self.refused([{"path": "settings.xml", "content":
                                 '<!DOCTYPE p [ <!ENTITY x SYSTEM "file:///etc/passwd"> ]>'
                                 '<p>&x;</p>\n'}])
        self.assertIn("DTD", message)
        self.assertNotIn("Entity", message, "the refusal is about the DTD, not a parse detail")

    def test_the_java_rule_is_folded_like_the_suffix_gate_above_it(self):
        """workspace.py admits a suffix in any case, so APP.JAVA gets the same check as app.java."""
        message = self.refused([{"path": "Application.JAVA", "content": self.POM_XML}])
        self.assertIn("XML where Java was asked for", message)

    def test_two_changes_for_one_file_are_still_refused(self):
        """Order between two whole-file entries is ambiguous; edits[] is the ordered form."""
        message = self.refused([{"path": "app.py", "content": "a = 1\n"},
                                {"path": "app.py", "content": "a = 2\n"}])
        self.assertIn("Duplicate change target", message)


class BlockProposalTests(unittest.TestCase):
    """A code block the person chose to keep becomes a proposal, never a write.

    propose_block is the one place a session is opened without the model being asked, so
    the tests are about what does NOT change: the state, the hash, the policy and the fact
    that the file on disk is still the old one until apply_proposal says otherwise.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "main.py").write_text("def add(a, b):\n    return a - b\n",
                                                   encoding="utf-8", newline="\n")
        (self.root / ".env").write_text("TOKEN=1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.base / "runs"
        self.runs.mkdir()

    def session(self, name="src/main.py", content="def add(a, b):\n    return a + b\n"):
        return load_session(propose_block(self.ws, "keep this block", name, content, self.runs))

    def test_a_new_file_is_offered_without_touching_the_disk(self):
        path = propose_block(self.ws, "scaffold it", "src/util.py", "CONSTANT = 1\n", self.runs)
        session = load_session(path)
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertIsNone(session["changes"][0]["before"])
        self.assertFalse((self.root / "src" / "util.py").exists())

    def test_an_existing_file_keeps_its_current_bytes_as_the_diff(self):
        session = self.session()
        self.assertEqual(session["changes"][0]["before"], "def add(a, b):\n    return a - b\n")
        self.assertEqual(session["changes"][0]["after"], "def add(a, b):\n    return a + b\n")

    def test_the_hash_covers_the_block_so_apply_verifies_the_same_bytes(self):
        session = self.session()
        self.assertEqual(session["proposal_hash"], proposal_hash(session))
        self.assertEqual(session["model"], "user")

    def test_applying_a_block_proposal_writes_the_file(self):
        path = propose_block(self.ws, "keep it", "src/main.py",
                             "def add(a, b):\n    return a + b\n", self.runs)
        apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertEqual((self.root / "src" / "main.py").read_text(encoding="utf-8"),
                         "def add(a, b):\n    return a + b\n")

    def test_a_wrong_hash_is_refused_at_apply_time_like_any_other_proposal(self):
        path = propose_block(self.ws, "keep it", "src/main.py",
                             "def add(a, b):\n    return a + b\n", self.runs)
        with self.assertRaises(AgentError):
            apply_proposal(path, "0" * 64)

    def test_a_path_outside_the_project_is_refused_before_anything_is_read(self):
        for name in ("../elsewhere.py", "/etc/passwd", "C:\\Windows\\win.ini", ".env",
                     "src/../src/main.py"):
            with self.assertRaises((PolicyError, AgentError), msg=name):
                propose_block(self.ws, "keep it", name, "x = 1\n", self.runs)
        self.assertEqual(list(self.runs.glob("*/session.json")), [], "refused before a session")

    def test_a_block_that_is_not_valid_python_is_refused(self):
        with self.assertRaises(PolicyError):
            propose_block(self.ws, "keep it", "src/broken.py", "def broken(:\n", self.runs)

    def test_a_block_identical_to_the_file_is_refused_as_no_change(self):
        with self.assertRaises(PolicyError):
            propose_block(self.ws, "keep it", "src/main.py",
                          "def add(a, b):\n    return a - b\n", self.runs)

    def test_an_empty_block_is_refused(self):
        for blank in ("", "   \n  \n"):
            with self.assertRaises(PolicyError):
                propose_block(self.ws, "keep it", "src/main.py", blank, self.runs)

    def test_the_task_text_keeps_the_usual_limit(self):
        with self.assertRaises(AgentError):
            propose_block(self.ws, "x" * 4001, "src/main.py", "def add(a, b):\n    return 1\n",
                          self.runs)

    def test_a_chat_identity_is_recorded_so_the_proposal_belongs_to_this_thread(self):
        digest_id = "0123456789abcdef0123456789abcdef"
        path = propose_block(self.ws, "keep it", "src/main.py",
                             "def add(a, b):\n    return a * b\n", self.runs, chat_id=digest_id)
        session = load_session(path)
        self.assertEqual(session["chat_id"], digest_id)
        self.assertEqual(session["proposal_hash"], proposal_hash(session))
        with self.assertRaises(PolicyError):
            propose_block(self.ws, "keep it", "src/main.py", "def add(a, b):\n    return 1\n",
                          self.runs, chat_id="../elsewhere")


class TheSymbolVerbs(unittest.TestCase):
    """`find_symbol` and `find_references` — the questions the index could already answer.

    Measured in the ecommerce run: a 3 B model shown a type name it had to edit spent its turns
    `read_file`-ing paths it invented from that name, because the only lookup offered was a substring
    search over text. These two verbs are the difference between guessing a filename and being told it,
    and the tests are written as a conversation: a scripted model asks, and the next prompt is what it
    was told.
    """

    MAIN = "def add(a, b):\n    return a + b\n"
    CALLER = "from main import add\n\n\ndef run():\n    return add(1, 2)\n"
    CONFIG = "eureka:\n  client:\n    register-with-eureka: false\n    register: true\n"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        root = self.base / "repo"
        (root / "src").mkdir(parents=True)
        for name, text in {"main.py": self.MAIN, "caller.py": self.CALLER,
                           "application.yml": self.CONFIG}.items():
            (root / "src" / name).write_text(text, encoding="utf-8", newline="\n")
        self.ws = Workspace(root)

    def proposal(self, content="def add(a, b):\n    return a - b\n"):
        return {"action": "propose", "summary": "Fix add", "checks": ["unit tests"],
                "changes": [{"path": "src/main.py", "content": content}]}

    def ask(self, *actions):
        """One scripted conversation: the lookups under test, then a real read, then a proposal.

        The read is not padding. A proposal that replaces a file the model never opened is refused, so
        without it the loop asks again and the test measures a rejection rather than the answer it is
        looking at.
        """
        provider = RecordingProvider(list(actions)
                                     + [{"action": "read_file", "path": "src/main.py"},
                                        self.proposal()])
        path = plan(self.ws, "fix add", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        return load_session(path), " ".join(message["content"] for message in provider.prompts[-1])

    def test_a_name_answers_with_the_file_that_declares_it(self):
        session, told = self.ask({"action": "find_symbol", "query": "add"})
        self.assertIn('"declarations"', told)
        self.assertIn("src/main.py", told)
        self.assertIn("add(a, b)", told, "the signature is what tells a model this is the one it wants")
        rows = [item for item in session["events"] if item["kind"] == "tool"]
        self.assertEqual([(row["name"], row["count"]) for row in rows if row["name"] == "find_symbol"],
                         [("find_symbol", 1)], "the lookup ran once, over the index, and said what it found")

    def test_a_declared_type_answers_with_the_line_it_sits_on(self):
        """Python's index keeps a file-level function as a signature, so a line for it would be
        invented; a declared type is recorded with one, and that is the case worth the verb's cost."""
        (self.ws.root / "Api.java").write_text("package web;\n\npublic class Api {\n}\n",
                                               encoding="utf-8", newline="\n")
        _session, told = self.ask({"action": "find_symbol", "query": "Api"})
        self.assertIn("Api.java", told)
        self.assertIn('"line": 3', told)

    def test_a_use_answers_with_every_site_and_its_role(self):
        _session, told = self.ask({"action": "find_references", "query": "add"})
        self.assertIn('"declaration"', told)
        self.assertIn('"import"', told)
        self.assertIn('"call"', told)
        self.assertIn("src/caller.py", told)

    def test_an_answer_cut_at_the_cap_says_it_was_cut(self):
        """40 sites returned is either the whole truth or the ceiling, and the model cannot tell which.

        `list_files` already answers with a `truncated` field; these two verbs used to answer without
        one, which is how a small model concludes "this name is used 40 times" and stops looking.
        """
        for number in range(8):
            body = "\n".join(f"add({number}, {slot})" for slot in range(8))
            (self.ws.root / f"many{number}.py").write_text(body + "\n", encoding="utf-8", newline="\n")
        session, told = self.ask({"action": "find_references", "query": "add"})
        self.assertIn('"truncated": true', told)
        self.assertIn("truncated at 40 matches; more references exist in the repository", told)
        # The per-file ceiling is a rule the answer always obeys, so it is stated rather than inferred:
        # one file with twenty uses reports six and would otherwise look complete.
        self.assertIn('"per_file": 6', told)
        rows = [row for row in session["events"]
                if row["kind"] == "tool" and row["name"] == "find_references"]
        self.assertEqual([row["truncated"] for row in rows], [True], "the record says it too")
        self.assertEqual(rows[0]["count"], 40, "what was shown is 40, what existed was more")

    def test_a_complete_answer_says_it_is_complete(self):
        """`truncated: false` is the half that makes the flag worth reading — absence is not a no."""
        _session, told = self.ask({"action": "find_references", "query": "add"})
        self.assertIn('"truncated": false', told)
        self.assertNotIn("truncated at", told)

    def test_every_row_a_run_writes_is_keyed_by_an_id_no_other_row_uses(self):
        """The id is the only handle a row clicked an hour later has for its details, and a tool's
        row now takes the id the registry minted for the call — so the one thing that must survive
        that handover is that no two rows share a key."""
        session, _told = self.ask({"action": "find_symbol", "query": "add"},
                                  {"action": "search_code", "query": "add"})
        ids = [row["id"] for row in session["events"] if row["kind"] == "step"]
        self.assertGreaterEqual(len(ids), 2, session["events"])
        self.assertEqual(len(ids), len(set(ids)))
        for one in ids:
            self.assertRegex(one, r"^[0-9a-f]{8}$")

    def test_a_declaration_list_cut_at_the_cap_says_it_was_cut(self):
        for number in range(45):
            (self.ws.root / f"d{number}.py").write_text("def thing():\n    return 1\n",
                                                        encoding="utf-8", newline="\n")
        _session, told = self.ask({"action": "find_symbol", "query": "thing"})
        self.assertIn('"truncated": true', told)
        self.assertIn('"declarations"', told)

    def test_a_name_the_project_does_not_declare_is_answered_as_an_answer(self):
        """Empty is information. The guidance that follows is what stops a small model searching for the
        same name again instead of proposing the file the plan asks for."""
        _session, told = self.ask({"action": "find_symbol", "query": "subtract"})
        self.assertIn('"declarations": []', told)
        self.assertIn("does not define it", told)
        self.assertIn('"next_step"', told, "an empty answer with no next step costs another turn")

    def test_a_configuration_key_is_not_reported_as_a_use_of_a_name(self):
        """`register-with-eureka` is a real occurrence of `register`, and reporting it would spend the
        hit budget on YAML. Text search is the verb for that; this one is about code, so the key must
        not reach the prompt at all — the map lists the file, never its contents."""
        _session, told = self.ask({"action": "find_references", "query": "register"})
        self.assertIn('"sites": []', told)
        self.assertNotIn("register-with-eureka", told)

    def test_the_counts_of_every_turn_land_on_the_record(self):
        """What one task cost, summed over its turns, from the numbers the server measured.

        The tool has always been able to guess a prompt's size with `estimate_tokens`; only the
        provider knows how many tokens it actually read, and that is the reading that shows a context
        was cut in half — which has looked, until now, like a model that could not code.
        """
        class Counting(RecordingProvider):
            def generate(self, messages):
                self.metrics = {"prompt_tokens": 100 * (len(self.prompts) + 1),
                                "completion_tokens": 5}
                return super().generate(messages)

        provider = Counting([{"action": "find_symbol", "query": "add"},
                             {"action": "read_file", "path": "src/main.py"}, self.proposal()])
        path = plan(self.ws, "fix add", provider, Settings(), self.base / "runs",
                    progress=lambda _: None)
        self.assertEqual(load_session(path)["metrics"],
                         {"prompt_tokens": 600, "completion_tokens": 15},
                         "three turns: 100 + 200 + 300 prompt tokens")

    def test_a_provider_that_measured_nothing_leaves_the_number_absent(self):
        """`RecordingProvider` owns no transport, so the record says nothing about cost — rather than
        a zero that reads as "this answer was free"."""
        session, _told = self.ask({"action": "find_symbol", "query": "add"})
        self.assertNotIn("metrics", session)

    def test_a_query_with_the_wrong_fields_is_refused_with_the_shape_named(self):
        _session, told = self.ask({"action": "find_symbol", "path": "src/main.py"},
                                  {"action": "find_symbol", "query": "add"})
        self.assertIn("Unknown action or invalid fields", told)
        self.assertIn("action find_symbol with fields action, path", told)

    def test_the_verbs_the_prompt_offers_are_the_verbs_the_loop_accepts(self):
        """The drift this guards is the expensive kind: a verb in the system prompt that the handler
        chain does not know costs a turn per attempt, and one the chain knows but the prompt never
        mentions is code no model will ever ask for."""
        root = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
        offered = set(re.findall(r'\n- action="(\w+)":',
                                 (root / "prompts.py").read_text(encoding="utf-8")))
        # The kitchen is asked rather than read out of a source file. It lives in two places now:
        # the table in `tools.py`, and the two control actions `engine.py` keeps for itself because
        # `propose` returns the session and `blocked` raises. A regex over either file goes blind
        # the day one of them moves, and `names()` is the very list the refusal for an unknown
        # action is built from — so it is the vocabulary the loop actually answers to.
        handled = set(tools.default_registry().names())
        self.assertEqual(offered, handled, "the menu and the kitchen disagree")
        self.assertIn("find_symbol", offered)
        self.assertIn("find_references", offered)


class TheRankedContext(unittest.TestCase):
    """Files chosen because the task is *about* them, and the thread saying which and why.

    The rule this replaces was a substring test on the filename, so a task that described the bug
    instead of naming the file arrived with no context at all. The other half of the change is the
    sentence: a context block nobody can explain is the thing the memory notes already record as
    complaints about injected text, so the reason travels with the file and reaches the thread.
    """

    CALLER = "def run():\n    return compute()\n"
    HELPER = "def compute():\n    return 41\n"
    # "why does run return the wrong number", from code points so this file stays ASCII.
    ARABIC_TASK = "".join(map(chr, [0x0644, 0x064a, 0x0647])) + " run " + \
                "".join(map(chr, [0x064a, 0x0631, 0x062c, 0x0639]))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        root = self.base / "repo"
        root.mkdir()
        (root / "caller.py").write_text(self.CALLER, encoding="utf-8", newline="\n")
        (root / "helper.py").write_text(self.HELPER, encoding="utf-8", newline="\n")
        self.ws = Workspace(root)

    def proposal(self, content="def run():\n    return 42\n"):
        return {"action": "propose", "summary": "Fix run", "checks": ["unit tests"],
                "changes": [{"path": "caller.py", "content": content}]}

    def send(self, task):
        """Two proposals, because the second case is the one that needs them.

        A file the engine did not inject cannot be proposed without being read first, so the loop
        refuses the first attempt, hands the model the content, and asks again. Scripting one reply
        would make that test measure a `StopIteration` instead of the absence of a line.
        """
        provider = RecordingProvider([self.proposal(), self.proposal()])
        lines = []
        plan(self.ws, task, provider, Settings(), self.base / "runs", progress=lines.append)
        return " ".join(message["content"] for message in provider.prompts[0]), lines

    def test_a_file_the_task_never_named_still_reaches_the_prompt(self):
        task = "why does run return the wrong number?"
        self.assertNotIn("caller.py", task, "the premise of this test is that no path was typed")
        prompt, _lines = self.send(task)
        self.assertIn("File snapshot", prompt)
        self.assertIn("def run():", prompt)
        self.assertIn("caller.py", prompt, "the ranked file is injected by the index, not by luck")

    def test_the_thread_says_which_files_were_chosen_and_why(self):
        _prompt, lines = self.send("why does run return the wrong number?")
        chosen = [line for line in lines if "Chose" in line]
        self.assertEqual(len(chosen), 1, chosen)
        self.assertIn("caller.py", chosen[0])
        self.assertIn("(defines run)", chosen[0], "a reason the operator can check, not a file count")

    def test_no_injection_means_no_line_about_it(self):
        """A task that matches nothing must not announce a choice of zero files — the thread is read
        for what happened, and "Chose 0 file(s)" is a sentence about nothing."""
        _prompt, lines = self.send("nothing in this project is called FrobnicateToday")
        self.assertFalse([line for line in lines if "Chose" in line], lines)

    def test_the_reason_arrives_in_the_language_the_task_was_asked_in(self):
        _prompt, lines = self.send(self.ARABIC_TASK)
        chosen = [line for line in lines if "run" in line and "Chose" not in line]
        self.assertTrue(any(0x0600 <= ord(ch) <= 0x06ff for line in chosen for ch in line), chosen)

    def test_the_smallest_legal_budget_still_carries_a_task_and_a_file(self):
        """`context_chars` has a floor because the instruction block and the repository map are spent
        before any of the project's own files are read. Retrieval was then allowed a fixed third of the
        window *on top* of that, so a task already near the limit was refused outright, with a message
        that blamed the repository for the tool's own prompt."""
        provider = RecordingProvider([self.proposal(), self.proposal()])
        plan(self.ws, "why does run return the wrong number?", provider,
             Settings(context_chars=MIN_CONTEXT_CHARS), self.base / "runs",
             progress=lambda _line: None)
        first = provider.prompts[0]
        self.assertLessEqual(sum(len(message["content"]) for message in first), MIN_CONTEXT_CHARS)
        self.assertIn("def run():", " ".join(message["content"] for message in first),
                      "the floor is meant to leave room for one real file, not just for the prompt")

    def test_the_instruction_block_fits_the_floor_it_is_given(self):
        """The floor is a claim about the size of this prompt, and the prompt changes. If it grows past
        the smallest number the config will accept, every legal budget refuses every task."""
        self.assertLess(len(SYSTEM) + 400, MIN_CONTEXT_CHARS)


class TheUnexpectedFileFlag(unittest.TestCase):
    """D41: a change to a file the task never spoke about is said before it is approved.

    The rule is a sentence, not a gate. A fix that spans two files is ordinary, and a refusal that fired
    on every one of them would be answered by re-typing the task until the gate stopped — so the operator
    sees which files came out of the map rather than the agent's own reading, and decides.
    """

    KEEP = "".join("import module%d\n" % i for i in range(12))

    def session(self, task="fix the login in auth.py", paths=(), events=(), created=()):
        return {"task": task, "state": "WAITING_APPROVAL", "root": "/tmp/repo", "summary": "s",
                "checks": [], "proposal_hash": "h",
                "events": [{"kind": kind, "path": path} for kind, path in events],
                "changes": [{"path": path, "before": self.KEEP, "after": self.KEEP + "x = 1\n",
                             "delete": False} for path in paths]
                           + [{"path": path, "before": None, "after": "new file\n",
                               "delete": False} for path in created]}

    def names(self, session):
        return engine.unrelated_files(session)

    def test_a_file_the_task_named_is_not_flagged(self):
        got = self.names(self.session(paths=("auth.py",)))
        self.assertEqual(got, [])

    def test_a_file_the_index_chose_for_the_task_is_not_flagged(self):
        got = self.names(self.session(paths=("auth/LoginService.java",),
                                      events=[("context_file", "auth/LoginService.java")]))
        self.assertEqual(got, [])

    def test_a_file_the_agent_read_during_the_turn_is_not_flagged(self):
        """Reading is how the agent earns the right to edit, and the thread already shows the read — a
        flag on every build-fix that touched a pom would teach the operator to ignore the sentence."""
        got = self.names(self.session(paths=("pom.xml",), events=[("auto_read", "pom.xml")]))
        self.assertEqual(got, [])

    def test_a_file_nobody_spoke_about_is_named_in_the_dialog(self):
        got = self.session(paths=("billing/Invoice.java",))
        self.assertEqual(engine.unrelated_files(got), ["billing/Invoice.java"])
        notice = engine.unexpected_notice(got)
        self.assertIn("billing/Invoice.java", notice)
        self.assertIn("repository map alone", notice)

    def test_a_new_file_is_not_a_surprise_of_the_same_kind(self):
        """A task that asks for a feature expects a file that has never existed; the artifact card says
        "Created" and there is no prior content to have been written over."""
        self.assertEqual(self.names(self.session(created=("auth/HealthController.java",))), [])

    def test_a_removal_is_left_to_the_notice_that_already_owns_it(self):
        self.assertEqual(self.names(self.session(paths=())), [])

    def test_a_folder_relative_name_still_matches_the_file_the_task_named(self):
        """The task says `pom.xml`; the change is `orders-service/pom.xml`. Same file as far as the
        operator is concerned, and a flag here would be noise on every Maven task."""
        got = self.session(task="bump the spring boot version in pom.xml",
                           paths=("orders-service/pom.xml",))
        self.assertEqual(engine.unrelated_files(got), [])

    def test_the_flag_is_a_window_sentence_not_an_approval_block(self):
        """Nothing here stops `apply_proposal`: the proposal stays approvable, exactly as it was."""
        session = self.session(paths=("billing/Invoice.java",))
        self.assertTrue(engine.unexpected_notice(session))
        self.assertEqual(session["state"], "WAITING_APPROVAL")

    def test_the_review_text_lists_the_stray_files_for_the_cli_too(self):
        text = engine.review(self.session(paths=("billing/Invoice.java",)))
        self.assertIn("Files this task never named or read:", text)
        self.assertIn("billing/Invoice.java", text)

    def test_the_dialog_carries_the_removal_warning_and_this_one_together(self):
        """One owner for both advisories: the two windows used to call the removal notice directly, and
        a sentence added to only one of them is the drift this project keeps paying for."""
        emptied = {"task": "t", "root": "/tmp/repo", "events": [], "checks": [], "changes": [
            {"path": "big.py", "before": "".join("line%d\n" % i for i in range(20)),
             "after": "line0\n", "delete": False},
            {"path": "other.py", "before": self.KEEP, "after": self.KEEP + "x = 1\n",
             "delete": False}]}
        text = repair.approval_advisories(emptied)
        self.assertIn("Removes most of an existing file", text)
        self.assertIn("other.py", text)
        self.assertIn("big.py", text, "the emptied file was never named either, and says so")

    def test_both_windows_read_the_one_aggregate(self):
        """Source-level, because no Python test opens a dialog."""
        import inspect
        from ai_code_engineer import gui
        from ai_code_engineer.webapp import controller as controller_module
        for module in (gui, controller_module):
            source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
            self.assertIn("approval_advisories(", source, module.__name__)
            self.assertNotIn("repair.removal_notice(", source,
                             "%s still shows half of the warning" % module.__name__)


class TheErrorThatCameBack(unittest.TestCase):
    """D42 on the loop's side: a failure another task already left open is said before it is found again.

    The record of every run is already on disk under `.agent-runs`, and the repair loop already reads it
    for the conversation in front of it. This is the same reading widened to the project, because the
    person who opens a second chat to finish what the first one could not is the case where the history
    is worth anything — and a model that is not told repeats the fix that already failed.
    """

    OTHER_CHAT = "b" * 32
    THIS_CHAT = "a" * 32

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("def total(items):\n    return sum(items)\n",
                                          encoding="utf-8", newline="\n")
        self.runs = self.base / "runs"
        self.ws = Workspace(self.root)

    def prior_session(self, *runs, chat=OTHER_CHAT, sid="old1"):
        atomic_json(self.runs / sid / "session.json",
                    {"schema": 1, "id": sid, "root": str(self.root), "task": "fix the totals",
                     "state": "BLOCKED", "created": "2026-09-01T10:00", "events": [],
                     "chat_id": chat, "runs": list(runs)})

    def failing(self, at="2026-09-01T10:05", line=None):
        return {"status": "failed", "label": "pytest", "at": at, "seconds": 3.0,
                "failures": [line or "FAILED tests/test_cart.py::test_total - AssertionError: 3 != 4"],
                "tail": "1 failed"}

    def passing(self, at="2026-09-04T10:05"):
        return {"status": "passed", "label": "pytest", "at": at, "seconds": 2.0,
                "failures": [], "tail": "12 passed"}

    def proposal(self):
        return {"action": "propose", "summary": "Fix total", "checks": ["unit tests"],
                "changes": [{"path": "app.py", "content": "def total(items):\n    return 0\n"}]}

    def run_plan(self, chat=THIS_CHAT):
        provider = RecordingProvider([self.proposal(), self.proposal()])
        lines = []
        plan(self.ws, "the sum in app.py is wrong", provider, Settings(), self.runs,
             progress=lines.append, chat_id=chat)
        return provider, " ".join(lines)

    def test_an_open_error_from_another_task_is_said_in_the_thread(self):
        self.prior_session(self.failing())
        _provider, lines = self.run_plan()
        self.assertIn("earlier task(s) left this build error open", lines)
        self.assertIn("test_total", lines, "the row names the failure, not just that there was one")

    def test_the_model_is_told_which_battle_was_already_lost(self):
        self.prior_session(self.failing())
        provider, _lines = self.run_plan()
        prompt = " ".join(message["content"] for message in provider.prompts[0])
        self.assertIn("already failed on", prompt)
        self.assertIn("do not repeat a change that already failed", prompt)

    def test_a_chat_is_not_quoted_back_at_its_own_rounds(self):
        """`stalled()` already says this every round, under its own name. Two sentences about one thing
        is how a thread starts sounding like a log."""
        self.prior_session(self.failing(), chat=self.THIS_CHAT)
        _provider, lines = self.run_plan()
        self.assertNotIn("left this build error open", lines)

    def test_an_error_a_later_pass_settled_stays_silent(self):
        self.prior_session(self.failing(), self.passing())
        _provider, lines = self.run_plan()
        self.assertNotIn("left this build error open", lines)

    def test_a_project_with_no_history_says_nothing_about_history(self):
        _provider, lines = self.run_plan()
        self.assertNotIn("left this build error open", lines)
        self.assertNotIn("already failed on", lines)

    def test_the_announce_row_carries_the_command_it_failed_in(self):
        self.prior_session(self.failing(), self.failing(at="2026-09-02T10:05"))
        _provider, lines = self.run_plan()
        self.assertIn("pytest", lines, "which command the error belongs to travels with it")

    def test_a_reopened_task_rebuilds_the_same_sentence(self):
        """The stored record is filtered through `STEP_FIELDS` on the way back, so a field that is not
        in that tuple makes history say less than the live row did — the drift class this keeps hitting."""
        from ai_code_engineer import labels
        self.prior_session(self.failing())
        provider = RecordingProvider([self.proposal(), self.proposal()])
        events = []

        def record(line, step_id, action, fields):
            events.append({"id": step_id, "action": action, **fields})

        plan(self.ws, "the sum in app.py is wrong", provider, Settings(), self.runs,
             progress=lambda _l: None, chat_id=self.THIS_CHAT, step=record)
        row = next(item for item in events if item["action"] == "unresolved_error")
        rebuilt = {key: row[key] for key in labels.STEP_FIELDS if key in row}
        whole = {key: value for key, value in row.items() if key not in ("id", "action")}
        self.assertEqual(labels.step_line(False, "unresolved_error", **rebuilt),
                         labels.step_line(False, "unresolved_error", **whole))


class ThoughtfulProvider(RecordingProvider):
    """The transport's shape: an answer, plus the deliberation that arrived beside it.

    With `pieces` it also advertises `supports_stream`, which is the only way a test can tell the
    engine's streaming ask apart from a call at a model that has never taken one.
    """

    def __init__(self, responses, thoughts, pieces=None):
        super().__init__(responses)
        self.thoughts = iter(thoughts)
        self.pieces = iter(pieces) if pieces is not None else None
        self.reasoning = ""
        if pieces is not None:
            self.supports_stream = True

    def generate(self, messages, on_token=None):
        self.reasoning = next(self.thoughts)
        if on_token is not None and self.pieces is not None:
            for piece in next(self.pieces):
                on_token(piece)
        return super().generate(messages)


class TheThoughtThatCameWithTheAnswer(unittest.TestCase):
    """UI 4.2 phase 2: a reasoning model's thinking is read, shown once, and never re-sent.

    Two separate losses were being paid here. The field the provider returns beside `content` was
    dropped, so the operator could not see why a weak model picked the file it picked; and a reply
    that put braces in prose lost its turn to "Return one JSON object", because the recovery took the
    *first* balanced object rather than the one with an action in it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.base / "runs"

    def proposal(self, content="answer = 2\n"):
        return {"action": "propose", "summary": "Update answer", "checks": ["unit tests"],
                "changes": [{"path": "app.py", "content": content}]}

    def read_then_propose(self, thoughts, task="Update answer"):
        provider = ThoughtfulProvider([{"action": "read_file", "path": "app.py"}, self.proposal()],
                                      thoughts)
        lines = []
        path = plan(self.ws, task, provider, Settings(), self.runs, progress=lines.append)
        return provider, lines, path

    def test_the_envelope_wins_over_an_object_quoted_in_prose(self):
        answer = json.dumps(self.proposal())
        raw = "Files I weighed: {\"candidates\": {\"app.py\": \"the one\"}} — my answer is " + answer
        self.assertEqual(parse_action(raw)["action"], "propose")
        self.assertNotIn("candidates", parse_action(raw))

    def test_a_whole_reply_is_still_taken_as_whole(self):
        value = parse_action(json.dumps({"action": "propose", "summary": "s", "checks": [],
                                         "notes": {"keep": True}, "changes": []}))
        self.assertEqual(value["action"], "propose")
        self.assertIn("notes", value, "the envelope comes back entire, not cut to its action")

    def test_a_reply_with_no_action_in_it_falls_back_to_the_first_object(self):
        # The family this project measured as `{"code": ...}`: right fix, wrong envelope. The loop has
        # to see the model's own object and say why it is not an action, not a recovery of nothing.
        value = parse_action('prose {"code": "answer = 2"} then {"other": 1}')
        self.assertEqual(value, {"code": "answer = 2"})

    def test_a_thought_is_said_once_per_reply_and_keeps_its_text_for_opening(self):
        first, second = "weighing the two files", "writing the fix"
        _provider, lines, path = self.read_then_propose([first, second])
        rows = [line for line in lines if "Thought for" in line]
        self.assertEqual(len(rows), 2, lines)
        self.assertIn(first, rows[0])
        self.assertIn("%d characters" % len(first), rows[0])
        stored = load_session(path)["events"]
        thought = [item for item in stored if item.get("action") == "model_reasoning"]
        self.assertEqual([item["detail"] for item in thought], [first, second])
        self.assertEqual([item["count"] for item in thought], [len(first), len(second)])

    def test_the_row_comes_before_the_action_it_preceded(self):
        """A thread read for what happened puts the deliberation where it happened: before the row."""
        _provider, lines, _path = self.read_then_propose(["thinking first", None])
        self.assertLess([index for index, line in enumerate(lines) if "Thought for" in line][0],
                        next(index for index, line in enumerate(lines) if "Proposed" in line))

    def test_a_model_that_shows_no_reasoning_announces_no_row(self):
        provider = ScriptedProvider([{"action": "read_file", "path": "app.py"}, self.proposal()])
        lines = []
        path = plan(self.ws, "Update answer", provider, Settings(), self.runs, progress=lines.append)
        self.assertFalse([line for line in lines if "Thought for" in line], lines)
        self.assertFalse([item for item in load_session(path)["events"]
                          if item.get("action") == "model_reasoning"])

    def test_the_thought_never_goes_back_to_the_model_as_history(self):
        provider, _lines, _path = self.read_then_propose(["a private aside about app.py", None])
        second = json.dumps(provider.prompts[1])
        self.assertNotIn("a private aside", second,
                         "the next turn does not need to re-read the deliberation it just made")

    def test_the_thought_is_announced_in_the_language_the_task_was_asked_in(self):
        _provider, lines, _path = self.read_then_propose(["looked at app.py first", None],
                                                        task="صلح القيمة في app.py")
        row = next(line for line in lines if "\U0001f9ed" in line)
        self.assertNotIn("Thought for", row)
        self.assertTrue(any(0x0600 <= ord(char) <= 0x06ff for char in row), row)

    def test_a_streaming_ask_only_goes_to_a_model_that_offers_one(self):
        """`plan` grows a third provider argument and must not hand it to a model that never took it."""
        heard = []
        provider = ThoughtfulProvider([{"action": "read_file", "path": "app.py"}, self.proposal()],
                                      [None, None],
                                      pieces=[('{"action": "rea', 'd_file"}',),
                                              ('{"action": "pro', 'pose"}',)])
        plan(self.ws, "Update answer", provider, Settings(), self.runs,
             progress=lambda _line: None, on_token=heard.append)
        self.assertEqual(heard, ['{"action": "rea', 'd_file"}', '{"action": "pro', 'pose"}'],
                         "every turn's pieces are heard, in the order the model wrote them")
        plain = ScriptedProvider([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Update answer", plain, Settings(), self.runs,
                    progress=lambda _line: None, on_token=heard.append)
        self.assertEqual(len(heard), 4, "a model without the flag was not asked to stream")
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL",
                         "and the loop still finished the way it always did")


if __name__ == "__main__":
    unittest.main()
