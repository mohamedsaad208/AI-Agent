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

from ai_code_engineer import catalog, modes, setup
from ai_code_engineer.cli import doctor, main, parser, run_setup
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
context_chars = 6000
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
        # The terminal resolves its own record folder the way the launcher does, which in a test run is
        # this repository — and the folder declarations are a real file. Point it at the temp folder,
        # or a test that seals a folder would seal the one the suite is running in.
        where = patch("ai_code_engineer.cli.app_dir", return_value=Path(self.temp.name))
        where.start()
        self.addCleanup(where.stop)
        self.app = Path(self.temp.name)

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
        with patch("ai_code_engineer.catalog.models_for", return_value=(models, "live")):
            result = doctor()
        self.assertEqual(result["ollama"], "reachable")
        self.assertEqual(result["local_models"], ["qwen2.5-coder:1.5b"],
                         "a cloud model behind Ollama is not a local one")
        self.assertEqual(result["runtime_dependencies"], "standard library only")

    def test_doctor_survives_an_unreachable_ollama(self):
        with patch("ai_code_engineer.catalog.models_for", side_effect=AgentError("no service")):
            code, text = self.run_command(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn('"ollama": "unreachable"', text)

    def test_doctor_never_prints_a_key_it_found(self):
        with patch("ai_code_engineer.catalog.models_for", side_effect=AgentError("no service")), \
             patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-proj-a-real-looking-key-0123456789"}):
            _code, text = self.run_command(["doctor"])
        self.assertIn("openrouter_key_present", text)
        self.assertNotIn("sk-proj", text)

    def test_doctor_and_the_first_run_audit_ask_the_provider_once_each(self):
        """They read the same probe. Two reachability checks that can disagree is the whole bug."""
        from ai_code_engineer import setup
        calls = []

        def counted(kind, endpoint="", api_key=None):
            calls.append(kind.key)
            return [{"id": "qwen2.5-coder:1.5b", "cloud": False}], "live"

        with patch("ai_code_engineer.catalog.models_for", side_effect=counted):
            result = doctor()
            rows = setup.audit(provider="ollama", probe=True)
        self.assertEqual(result["ollama"], "reachable")
        self.assertEqual(setup.counts(rows)["bad"], 0)
        self.assertEqual(calls, ["ollama", "ollama"], "each entry point asked, once")

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


class FirstRunWizard(unittest.TestCase):
    """`agent setup` driven from a script instead of a keyboard.

    The wizard is the one command whose product is a set of questions, so the behaviour worth pinning
    is what it does when nobody answers: a pipe gets a no and a line saying so, Ctrl-D is an empty
    answer rather than a traceback, and `--yes` cannot invent a folder path. The provider is patched in
    `setUp` because an audit that reached a real Ollama would make the exit code depend on whether this
    machine happens to be running one.
    """

    LOCAL = {"id": "qwen2.5-coder:1.5b", "cloud": False}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.repo = sandbox_repo(self.temp.name)
        self.reach = patch("ai_code_engineer.setup.reach",
                           return_value=([self.LOCAL], catalog.LIVE, ""))
        self.reach.start()
        self.addCleanup(self.reach.stop)

    def wizard(self, argv, answers=(), interactive=True):
        args = parser().parse_args(["setup"] + argv)
        left = list(answers)
        asked = []

        def ask(prompt):
            asked.append(prompt)
            if not left:
                return ""
            value = left.pop(0)
            if isinstance(value, BaseException):
                raise value
            return value

        out = io.StringIO()
        with redirect_stdout(out):
            code = run_setup(args, ask=ask, interactive=interactive)
        return code, out.getvalue(), asked

    def patch_proof(self, result=None):
        patcher = patch("ai_code_engineer.setup.run_demo",
                        return_value=result or {"proposal_apply_rollback": "passed", "note": ""})
        proof = patcher.start()
        self.addCleanup(patcher.stop)
        return proof

    def test_the_wizard_is_a_command_and_not_a_fallthrough(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["setup", "--repo", str(self.repo), "--yes", "--no-demo"])
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("blocking.", text)

    def test_a_pipe_gets_a_no_and_a_line_saying_why(self):
        """A wizard that waited on `input()` in a CI job is `apply` blocking on a pipe with a friendlier
        name, so the refusal is printed and the run finishes."""
        self.patch_proof()
        code, text, asked = self.wizard(["--repo", str(self.repo)], interactive=False)
        self.assertIsInstance(code, int)
        self.assertIn("not an interactive terminal", text)
        self.assertEqual(asked, [], "nothing was asked of a machine that cannot answer")

    def test_yes_answers_every_offer_but_cannot_name_a_folder(self):
        """The folder is the one question a yes is meaningless for, so the run says it checked nothing
        against one rather than reporting a green project row it never read."""
        self.patch_proof()
        code, text, _ = self.wizard(["--yes"])
        self.assertEqual(code, 0, text)
        self.assertIn("No folder named with --repo", text)
        self.assertNotIn(f"{self.repo.name}:", text)

    def test_the_proof_is_run_for_a_yes_and_never_for_no_demo(self):
        proof = self.patch_proof()
        self.wizard(["--repo", str(self.repo), "--yes"])
        proof.assert_called_once()
        proof.reset_mock()
        self.wizard(["--repo", str(self.repo), "--yes", "--no-demo"])
        proof.assert_not_called()

    def test_declining_the_proof_leaves_the_demo_row_unrun(self):
        proof = self.patch_proof()
        code, text, _ = self.wizard(["--repo", str(self.repo)], answers=["n"])
        proof.assert_not_called()
        self.assertIn("The offline proof has not been run yet", text)
        self.assertIsInstance(code, int)

    def test_a_folder_typed_at_the_prompt_is_checked_against_the_command_it_answers_to(self):
        _code, text, asked = self.wizard([], answers=["y", str(self.repo), "n", "n"])
        self.assertIn("Project folder: ", asked)
        self.assertIn(f"{self.repo.name}:", text, "the project row reprinted after the folder was named")

    def test_an_empty_answer_to_the_folder_question_checks_nothing(self):
        _code, text, asked = self.wizard([], answers=["y", "", "n", "n"])
        self.assertIn("Project folder: ", asked)
        self.assertIn("No folder named, so nothing was checked", text)

    def test_a_folder_that_does_not_exist_blocks_the_run(self):
        _code, text, _ = self.wizard([], answers=["y", str(Path(self.temp.name) / "gone"), "n", "n"])
        self.assertIn("[x] ", text)

    def test_the_promises_print_before_the_question_about_them(self):
        """Asking someone to accept five lines they cannot see is a rubber stamp, so the order is the
        contract. Checked by capturing what stdout held at the moment each question was asked."""
        out = io.StringIO()
        seen = []

        def ask(prompt):
            seen.append((prompt, out.getvalue()))
            return "n"

        args = parser().parse_args(["setup", "--repo", str(self.repo), "--no-demo"])
        with redirect_stdout(out):
            run_setup(args, ask=ask, interactive=True)
        accept = [text for prompt, text in seen if prompt.startswith("Accept")]
        self.assertTrue(accept, [prompt for prompt, _ in seen])
        self.assertIn("(5)", accept[0], "the fifth promise had not printed when the run asked")
        self.assertIn("(1) Nothing is written until you approve", out.getvalue())
        self.assertLess(out.getvalue().index("(1)"), out.getvalue().index("Next, in the window"))

    def test_declining_the_policy_still_runs_but_says_the_write_is_not_blessed(self):
        self.patch_proof()
        _code, text, _ = self.wizard(["--repo", str(self.repo), "--no-demo"], answers=["n"])
        self.assertIn("The policy was not accepted", text)

    def test_accepting_the_policy_prints_where_the_three_positions_are_set(self):
        _code, text, _ = self.wizard(["--repo", str(self.repo), "--yes", "--no-demo"])
        self.assertIn("Read-only", text)
        self.assertIn("Auto-Apply is the switch on top of Change", text)
        self.assertNotIn("The policy was not accepted", text)

    def test_a_control_d_is_an_empty_answer_not_a_traceback(self):
        self.patch_proof()
        code, text, _ = self.wizard([], answers=[EOFError(), EOFError()])
        self.assertIsInstance(code, int)
        self.assertIn("No folder named", text)

    def test_a_provider_that_does_not_answer_exits_nonzero(self):
        """The exit code is the point a script can read: a red line is not a success that printed
        nicely. `ollama serve` is the advice that has to reach the same stdout."""
        with patch("ai_code_engineer.setup.reach", return_value=([], "", "connection refused")):
            code, text, _ = self.wizard(["--repo", str(self.repo), "--yes", "--no-demo"])
        self.assertEqual(code, 1)
        self.assertIn("ollama serve", text)
        self.assertIn("[x] ", text)

    def test_arabic_rows_reach_the_terminal_as_arabic(self):
        """Verified by code point, not by eye: a mangled literal looks like correct Arabic in a
        terminal, and cp1252 replacement turns the whole run into question marks."""
        self.patch_proof()
        _code, text, _ = self.wizard(["--repo", str(self.repo), "--arabic", "--yes", "--no-demo"])
        self.assertTrue(any(0x0600 <= ord(char) <= 0x06ff for char in text), text[:200])
        self.assertNotIn("\ufffd", text)
        self.assertNotIn("The offline proof", text)
        for char in text:
            if ord(char) >= 128:
                self.assertFalse(0x3040 <= ord(char) <= 0x30ff or 0x4e00 <= ord(char) <= 0x9fff,
                                 f"U+{ord(char):04X} leaked into the Arabic run")


class TheFolderSOwnPosition(unittest.TestCase):
    """`agent read-only`, and the three commands a declaration stops.

    The terminal used to be the surface the promise did not reach: a folder set Read-only in a window
    could be written by `agent apply` from any directory, with nothing to read back the choice. These
    go through `main([...])` like the rest of this file, because the point is the command a person
    types, not the store behind it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name)
        self.root = sandbox_repo(self.temp.name)
        self.runs = self.app / "runs"
        where = patch("ai_code_engineer.cli.app_dir", return_value=self.app)
        where.start()
        self.addCleanup(where.stop)

    def run_command(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue()

    def declare(self, folder=None):
        code, text = self.run_command(["read-only", "--repo", str(folder or self.root)])
        self.assertEqual(code, 0, text)
        return text

    # ------------------------------- declaring it -------------------------------
    def test_a_folder_is_declared_for_every_surface_and_says_how_to_undo_it(self):
        text = self.declare()
        self.assertTrue(modes.sealed(self.app, self.root))
        self.assertIn("the command line", text)
        self.assertIn("agent read-only --off", text,
                      "a promise with no named way out sends the operator into a config file")

    def test_a_folder_that_does_not_exist_is_an_error_not_a_declaration(self):
        code, text = self.run_command(["read-only", "--repo", str(self.app / "nowhere")])
        self.assertEqual(code, 1)
        self.assertTrue(text.startswith("Error:"), text)
        self.assertEqual(modes.listed(self.app), [])

    def test_listing_answers_for_a_machine_that_has_never_declared_anything(self):
        code, text = self.run_command(["read-only"])
        self.assertEqual(code, 0, text)
        self.assertIn("Nothing is declared", text)
        self.declare()
        code, text = self.run_command(["read-only"])
        self.assertIn("Read-only", text)
        self.assertIn("command line", text)

    def test_lifting_is_a_separate_line_and_leaves_no_row_behind(self):
        self.declare()
        code, text = self.run_command(["read-only", "--repo", str(self.root), "--off"])
        self.assertEqual(code, 0, text)
        self.assertFalse(modes.sealed(self.app, self.root))
        self.assertEqual(modes.listed(self.app), [])

    def test_the_arabic_run_of_the_same_command_answers_in_arabic(self):
        """The console used to print `??????` for this: `run_setup` had the encoding fix and the new
        command did not, so the flag existed and the answer nobody could read shipped anyway."""
        code, text = self.run_command(["read-only", "--repo", str(self.root), "--arabic"])
        self.assertEqual(code, 0, text)
        self.assertTrue(any(0x0600 <= ord(char) <= 0x06ff for char in text), text[:120])
        self.assertNotIn("\ufffd", text)
        self.assertIn("agent read-only --off", text, "the way out is part of the refusal, in either language")
        self.assertNotIn("declared Read-only", text)

    # ------------------------------- what it stops -------------------------------
    def test_a_declared_folder_is_not_planned_against(self):
        """Refused before a provider is built: a job that was never going to happen cannot cost a
        request to a model, sealed or not."""
        self.declare()
        started = patch("ai_code_engineer.cli.make_provider",
                        side_effect=AssertionError("asked a model for a sealed folder"))
        started.start()
        self.addCleanup(started.stop)
        code, text = self.run_command(["plan", "Fix add", "--repo", str(self.root),
                                       "--runs", str(self.runs)])
        self.assertEqual(code, 1, text)
        self.assertIn("declared Read-only", text)
        self.assertIn("agent read-only --off", text)
        self.assertEqual((self.root / "calculator.py").read_text(), CALCULATOR_BAD)

    def test_apply_refuses_before_it_asks_for_the_hash(self):
        session = self.planned_session()
        self.declare()
        code, text = self.run_command(["apply", str(session)])
        self.assertEqual(code, 1, text)
        self.assertIn("the command line", text)
        self.assertNotIn("Type the full proposal", text,
                         "asking for a hash is promising the write follows it")
        self.assertEqual((self.root / "calculator.py").read_text(), CALCULATOR_BAD)

    def test_rollback_is_a_write_too(self):
        session = self.planned_session()
        self.declare()
        code, text = self.run_command(["rollback", str(session)])
        self.assertEqual(code, 1, text)
        self.assertIn("declared Read-only", text)

    def planned_session(self):
        started = patch("ai_code_engineer.cli.make_provider",
                        return_value=ScriptedProvider([CALCULATOR_READ, PROPOSE]))
        started.start()
        self.addCleanup(started.stop)
        code, text = self.run_command(["plan", "Fix add", "--repo", str(self.root),
                                       "--runs", str(self.runs)])
        self.assertEqual(code, 0, text)
        line = [row for row in text.splitlines() if row.startswith("Session: ")]
        return Path(line[0].split("Session: ", 1)[1].strip())

    # ------------------------------- what it does not stop -------------------------------
    def test_reading_a_declared_folder_still_works(self):
        """The four verbs the mode promises are all reads, and a declaration that also stopped them
        would be a way to lock a person out of their own project."""
        self.declare()
        code, text = self.run_command(["map", "--repo", str(self.root)])
        self.assertEqual(code, 0, text)
        self.assertIn("calculator.py", text)

    def test_a_session_of_a_sealed_folder_can_still_be_read_back(self):
        session = self.planned_session()
        self.declare()
        code, text = self.run_command(["review", str(session)])
        self.assertEqual(code, 0, text)
        self.assertIn("calculator.py", text)


if __name__ == "__main__":
    unittest.main()
