"""One-Key Approvals (Task 2.4): [y] approve / [n] reject / [d] details.

Three surfaces decide on a proposal, and they must agree on what "yes", "no" and "let me look" mean:
the command line, the web approval card, and the `engine` primitive underneath both. This file drives
the CLI exactly the way an operator does (argv in, exit code and stdout out, keystrokes fed through
`input`) and checks the shared reject primitive and the card state the keyboard shortcuts key off.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import engine
from ai_code_engineer.cli import main, one_key_approval
from ai_code_engineer.errors import PolicyError
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
context_chars = 6400
output_tokens = 1000
"""


class ScriptedProvider:
    model = "test-local"

    def __init__(self, responses):
        self.responses = iter(responses)

    def generate(self, messages, json_mode=True):
        value = next(self.responses)
        return value if isinstance(value, str) else json.dumps(value)


class ApprovalCase(unittest.TestCase):
    """A real pending proposal, planned end to end so apply/reject drive the same code a person hits."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = sandbox_repo(self.temp.name)
        self.runs = Path(self.temp.name) / "runs"
        self.config = Path(self.temp.name) / "groq.toml"
        self.config.write_text(GROQ, encoding="utf-8")
        env = patch.dict("os.environ", {"GROQ_API_KEY": "synthetic-key-for-tests"})
        env.start()
        self.addCleanup(env.stop)
        where = patch("ai_code_engineer.cli.app_dir", return_value=Path(self.temp.name))
        where.start()
        self.addCleanup(where.stop)

    def run_command(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def planned_session(self):
        provider = patch("ai_code_engineer.cli.make_provider",
                         return_value=ScriptedProvider([CALCULATOR_READ, PROPOSE]))
        provider.start()
        self.addCleanup(provider.stop)
        code, text = self.run_command(["plan", "Fix add", "--repo", str(self.root),
                                       "--runs", str(self.runs), "--config", str(self.config)])
        self.assertEqual(code, 0, text)
        line = [row for row in text.splitlines() if row.startswith("Session: ")]
        self.assertTrue(line, text)
        return Path(line[0].split("Session: ", 1)[1].strip())


class CliOneKeyTests(ApprovalCase):
    def test_y_approves_and_writes_the_proposal(self):
        path = self.planned_session()
        with patch("sys.stdin") as stdin, patch("builtins.input", return_value="y"):
            stdin.isatty.return_value = True
            code, text = self.run_command(["apply", str(path)])
        self.assertEqual(code, 0, text)
        self.assertIn("APPLIED", text.upper())
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)

    def test_n_rejects_without_writing_anything(self):
        path = self.planned_session()
        with patch("sys.stdin") as stdin, patch("builtins.input", return_value="n"):
            stdin.isatty.return_value = True
            code, text = self.run_command(["apply", str(path)])
        self.assertEqual(code, 0, text)
        self.assertIn("declined", text.lower())
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD,
                         "rejecting must leave the workspace untouched")
        self.assertTrue(engine.proposal_rejected(engine.load_session(path)),
                        "the refusal belongs in the task's own record")

    def test_d_reprints_the_review_then_accepts_a_later_key(self):
        path = self.planned_session()
        answers = iter(["d", "nonsense", "y"])
        with patch("sys.stdin") as stdin, patch("builtins.input", side_effect=lambda *_: next(answers)):
            stdin.isatty.return_value = True
            code, text = self.run_command(["apply", str(path)])
        self.assertEqual(code, 0, text)
        # The review is printed once up front, then again when `d` asked for it; the unrecognised key
        # earns a "please answer y, n or d" line; and `y` at the end still writes the proposal.
        self.assertGreaterEqual(text.count("calculator.py"), 2)
        self.assertIn("Please answer y, n or d", text)
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_GOOD)

    def test_a_pipe_still_demands_the_hash(self):
        """One-key approvals only make sense with someone typing; automation keeps the explicit path."""
        path = self.planned_session()
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            code, text = self.run_command(["apply", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("--approve", text)
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)

    def test_one_key_approval_recognises_yes_and_no_in_both_languages(self):
        for answer in ("y", "Y", "yes", "نعم"):
            self.assertEqual(one_key_approval("review text", ask=lambda *_: answer), "approve", answer)
        for answer in ("n", "NO", "لا"):
            self.assertEqual(one_key_approval("review text", ask=lambda *_: answer), "reject", answer)


class EngineRejectTests(ApprovalCase):
    def test_reject_records_the_refusal_and_persists(self):
        path = self.planned_session()
        pending = engine.load_session(path)
        result = engine.reject_proposal(path, pending["proposal_hash"])
        self.assertEqual(result["state"], "WAITING_APPROVAL")
        self.assertTrue(engine.proposal_rejected(engine.load_session(path)))
        self.assertEqual((self.root / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)

    def test_reject_demands_the_matching_pending_hash(self):
        path = self.planned_session()
        with self.assertRaises(PolicyError):
            engine.reject_proposal(path, "not-the-hash")

    def test_rejecting_twice_is_refused(self):
        path = self.planned_session()
        pending = engine.load_session(path)
        engine.reject_proposal(path, pending["proposal_hash"])
        with self.assertRaises(PolicyError):
            engine.reject_proposal(path, pending["proposal_hash"])


class WebApprovalCardTests(ApprovalCase):
    """The web card is driven by the same approval state the CLI reads; the one-key layer is the
    keyboard binding on top of it, so the deliverable to pin here is that binding."""

    def wiring(self):
        js = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer" / "webapp" / "static" / "ui-wiring.js"
        return js.read_text(encoding="utf-8")

    def test_global_shortcuts_map_y_n_d_onto_the_approval_actions(self):
        source = self.wiring()
        self.assertIn("DATA.review.canApply", source)
        self.assertIn("send('apply')", source)
        self.assertIn("send('reject')", source)
        self.assertIn("openFile(", source)

    def test_shortcuts_fire_only_over_a_live_approval_and_never_over_typing(self):
        source = self.wiring()
        # The y/n/d block is guarded by both the no-modifier flag and the card's own canApply, and it
        # sits behind the handsBusy guard that every global shortcut shares.
        self.assertIn("if (!mod && !e.shiftKey && DATA.review && DATA.review.canApply) {", source)
        self.assertIn("handsBusy()", source)


if __name__ == "__main__":
    unittest.main()
