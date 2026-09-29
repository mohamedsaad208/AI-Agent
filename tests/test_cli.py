"""The command line, driven the way a person drives it: argv in, exit code and stdout out.

`cli.py` is the oldest surface in this project and had no test of its wiring: every one of its nine
commands was exercised through `engine` directly, so a renamed flag, an argparse subcommand that fell
through to the wrong branch, or an error that escaped as a traceback instead of a line were all
invisible. The behaviour under test here is the *command*, not the engine behind it — so the engine
keeps its own tests and this file only ever calls `main([...])`.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
from contextlib import redirect_stderr, redirect_stdout
import unittest
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way

from ai_code_engineer.cli import doctor, main
from ai_code_engineer.errors import AgentError
from doubles import CALCULATOR_BAD, CALCULATOR_GOOD
from helpers import sandbox_repo

CALCULATOR_READ = {"action": "read_file", "path": "calculator.py"}
PROPOSE = {"action": "propose", "summary": "Fix addition", "checks": ["Run the addition tests"],
           "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]}

GROQ = """[model]
provider = "groq"
name = "llama-3.3-70b-versatile"
endpoint = "https://api.groq.com/openai/v1"
api_key_env = "GROQ_API_KEY"

[limits]
max_turns = 4
timeout_seconds = 30
context_chars = 4000
output_tokens = 1000
"""


class ScriptedProvider:
    """The provider the CLI is handed instead of a model: a queue of turns."""

    model = "test-local"

    def __init__(self, responses):
        self.responses = iter(responses)

    def generate(self, messages, json_mode=True):
        value = next(self.responses)
        return value if isinstance(value, str) else json.dumps(value)


def completion(action):
    """One OpenAI-shaped answer, so a real provider class can be used unpatched."""
    return {"choices": [{"finish_reason": "stop",
                         "message": {"content": json.dumps(action)}}]}


class Commands(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = sandbox_repo(self.temp.name)
        self.runs = Path(self.temp.name) / "runs"
        self.config = Path(self.temp.name) / "groq.toml"
        self.config.write_text(GROQ, encoding="utf-8")
        os_env = patch.dict("os.environ", {"GROQ_API_KEY": "synthetic-key-for-tests"})
        os_env.start()
        self.addCleanup(os_env.stop)

    def run_command(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue()

    def usage_error(self, argv):
        """argparse prints its usage block to stderr and exits 2 — the shell's signal for "you typed
        it wrong". Captured here so the suite stays readable and the message still gets asserted."""
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            main(argv)
        return caught.exception.code, err.getvalue()

    def planned_session(self, argv=None, responses=None):
        """`plan` end to end, returning the session path it printed."""
        started = [patch("ai_code_engineer.cli.make_provider",
                         return_value=ScriptedProvider(responses or [CALCULATOR_READ, PROPOSE]))]
        for patcher in started:
            patcher.start()
            self.addCleanup(patcher.stop)
        code, text = self.run_command(argv or ["plan", "Fix add", "--repo", str(self.root),
                                               "--runs", str(self.runs)])
        self.assertEqual(code, 0, text)
        line = [row for row in text.splitlines() if row.startswith("Session: ")]
        self.assertTrue(line, text)
        return Path(line[0].split("Session: ", 1)[1].strip())

    # ------------------------------ argv ------------------------------
    def test_no_command_at_all_is_a_usage_error(self):
        code, text = self.usage_error([])
        self.assertEqual(code, 2)
        self.assertIn("required: command", text)

    def test_an_unknown_command_is_a_usage_error_not_a_silent_zero(self):
        code, text = self.usage_error(["teleport"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", text)

    def test_map_demands_a_repository(self):
        code, text = self.usage_error(["map"])
        self.assertEqual(code, 2)
        self.assertIn("--repo", text)

    def test_a_missing_folder_is_a_line_not_a_traceback(self):
        code, text = self.run_command(["map", "--repo", str(Path(self.temp.name) / "nowhere")])
        self.assertEqual(code, 1)
        self.assertIn("Error:", text)
        self.assertNotIn("Traceback", text)

    def test_a_session_that_does_not_exist_is_the_same_kind_of_answer(self):
        code, text = self.run_command(["review", str(Path(self.temp.name) / "none.json")])
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("Error:"), text)
        self.assertNotIn("Traceback", text)

    # ------------------------------ doctor ------------------------------
    def test_doctor_reports_the_environment_it_found(self):
        models = [{"id": "qwen2.5-coder:1.5b", "cloud": False}, {"id": "deepseek-r1:70b", "cloud": True}]
        with patch("ai_code_engineer.cli.ollama_models", return_value=models):
            result = doctor()
        self.assertEqual(result["ollama"], "reachable")
        self.assertEqual(result["local_models"], ["qwen2.5-coder:1.5b"],
                         "a cloud model behind Ollama is not a local one")
        self.assertEqual(result["runtime_dependencies"], "standard library only")

    def test_doctor_survives_an_unreachable_ollama(self):
        with patch("ai_code_engineer.cli.ollama_models", side_effect=AgentError("no service")):
            code, text = self.run_command(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn('"ollama": "unreachable"', text)

    def test_doctor_never_prints_a_key_it_found(self):
        with patch("ai_code_engineer.cli.ollama_models", side_effect=AgentError("no service")), \
             patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-proj-a-real-looking-key-0123456789"}):
            _code, text = self.run_command(["doctor"])
        self.assertIn("openrouter_key_present", text)
        self.assertNotIn("sk-proj", text)

    # ------------------------------ the demo ------------------------------
    def test_the_demo_command_runs_and_reports_zero(self):
        code, text = self.run_command(["demo"])
        self.assertEqual(code, 0)
        self.assertIn('"proposal_apply_rollback": "passed"', text)
        self.assertIn("No project code executed", text)

    # ------------------------------ the review gate, as typed ------------------------------
    def test_plan_leaves_the_repository_untouched(self):
        session = self.planned_session()
        self.assertTrue(session.is_file())
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD,
                         "plan must never write project code")

    def test_apply_without_a_hash_refuses_a_non_interactive_run(self):
        session = self.planned_session()
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            code, text = self.run_command(["apply", str(session)])
        self.assertEqual(code, 1)
        self.assertIn("--approve", text)
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)

    def test_apply_with_the_wrong_hash_is_refused(self):
        session = self.planned_session()
        code, text = self.run_command(["apply", str(session), "--approve", "0" * 64])
        self.assertEqual(code, 1)
        self.assertIn("Error:", text)
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)

    def test_apply_with_the_proposal_hash_is_what_writes(self):
        session = self.planned_session()
        pending = json.loads(session.read_text(encoding="utf-8"))
        code, text = self.run_command(["apply", str(session), "--approve", pending["proposal_hash"]])
        self.assertEqual(code, 0, text)
        self.assertIn("APPLIED", text.upper())
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)

    def test_rollback_is_refused_before_anything_was_applied(self):
        session = self.planned_session()
        pending = json.loads(session.read_text(encoding="utf-8"))
        code, text = self.run_command(["rollback", str(session), "--approve", pending["proposal_hash"]])
        self.assertEqual(code, 1)
        self.assertIn("Error:", text)

    def test_rollback_after_an_apply_puts_the_file_back(self):
        session = self.planned_session()
        pending = json.loads(session.read_text(encoding="utf-8"))
        self.run_command(["apply", str(session), "--approve", pending["proposal_hash"]])
        code, text = self.run_command(["rollback", str(session), "--approve", pending["proposal_hash"]])
        self.assertEqual(code, 0, text)
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)

    def test_status_answers_from_the_session_file_alone(self):
        session = self.planned_session()
        code, text = self.run_command(["status", str(session)])
        self.assertEqual(code, 0)
        report = json.loads(text)
        self.assertEqual(report["state"], "WAITING_APPROVAL")
        self.assertIsInstance(report["events"], list)

    # ------------------------------ the consent switch ------------------------------
    def test_a_cloud_row_is_refused_before_anything_is_sent(self):
        """The request must never be built, so a patched transport that records calls is the proof."""
        sent = []
        with patch("ai_code_engineer.providers.request_json", side_effect=lambda *a, **k: sent.append(a)):
            code, text = self.run_command(["plan", "Fix add", "--repo", str(self.root),
                                           "--runs", str(self.runs), "--config", str(self.config)])
        self.assertEqual(code, 1, text)
        self.assertIn("--allow-cloud", text)
        self.assertEqual(sent, [], "code left the device without approval")

    def test_the_data_class_is_part_of_the_gate_not_a_label(self):
        sent = []
        with patch("ai_code_engineer.providers.request_json", side_effect=lambda *a, **k: sent.append(a)):
            code, text = self.run_command(["plan", "Fix add", "--repo", str(self.root),
                                           "--runs", str(self.runs), "--config", str(self.config),
                                           "--allow-cloud", "--data-class", "restricted"])
        self.assertEqual(code, 1, text)
        self.assertEqual(sent, [], "public or synthetic only; --allow-cloud alone is not a licence")

    def test_an_approved_cloud_row_sends_the_one_request_it_needs(self):
        answers = iter([completion({"action": "read_file", "path": "calculator.py"}), completion(PROPOSE)])
        with patch("ai_code_engineer.providers.request_json", side_effect=lambda *a, **k: next(answers)):
            code, text = self.run_command(["plan", "Fix add", "--repo", str(self.root),
                                           "--runs", str(self.runs), "--config", str(self.config),
                                           "--allow-cloud", "--data-class", "synthetic"])
        self.assertEqual(code, 0, text)
        self.assertIn("No project files changed", text)
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)


    # ------------------------------ the report ------------------------------
    def test_export_session_writes_the_report_to_a_file(self):
        session = self.planned_session()
        out = Path(self.temp.name) / "report.md"
        code, text = self.run_command(["export-session", str(session.parent), "--format", "markdown",
                                       "--out", str(out)])
        self.assertEqual(code, 0, text)
        page = out.read_text(encoding="utf-8")
        self.assertIn("## The request, verbatim", page)
        self.assertIn("No project command was run", page, "a planned-only task must not read as tested")

    def test_export_session_answers_json_on_stdout(self):
        session = self.planned_session()
        code, text = self.run_command(["export-session", session.parent.name, "--runs", str(self.runs),
                                       "--format", "json"])
        self.assertEqual(code, 0, text)
        report = json.loads(text[text.index("{"):])
        self.assertEqual(report["id"], session.parent.name)

    def test_a_run_that_never_happened_is_not_reported_as_a_format_error(self):
        code, text = self.run_command(["export-session", "ffffffff", "--runs", str(self.runs)])
        self.assertEqual(code, 1)
        self.assertIn("No session starts with", text)

    def test_an_unknown_format_is_a_usage_error_not_a_default(self):
        code, text = self.usage_error(["export-session", "abc", "--format", "pdf"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
