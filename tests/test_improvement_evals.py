"""Stage 6 — local eval cases for the improvement plan.

Every case here is a fact true on any machine, offline, in-process: a direct assertion on a
deterministic module (the structured task state, the log extractor, the state-machine tables)
or a scripted run through `plan()` with a provider that captures the prompts it was asked.
Nothing reaches the network, and the only model involved is the canned turn list below.

The optional promptfoo config under `tools/eval/` points at the operator's own Ollama daemon;
it is dev tooling only and no test here loads it.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import core, repair, taskstate
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import load_session, plan
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.workspace import Workspace


class ScriptedProvider:
    """Answers each turn with the next canned reply and records the prompt it was asked.

    Kept local to this suite for the same reason the two scripted providers in
    `test_agent.py` and `test_repair.py` are: the recorded prompt IS the assertion, and a
    shared double would drift the moment one suite needed a new field.
    """

    model = "test"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts: list = []

    def generate(self, messages):
        self.prompts.append([dict(m) for m in messages])
        response = next(self.responses)
        return response if isinstance(response, str) else json.dumps(response)


class TaskStateContinuity(unittest.TestCase):
    """Stage 1: a mid-task constraint survives trimming and a tight prompt budget."""

    def test_constraint_survives_a_budget_too_small_for_evidence(self):
        # The block's contract on a tight budget is "cut, not vanish" (see taskstate.block's
        # docstring): the *line* the constraint lives on is written high in SECTION_ORDER and
        # arrives truncated with an explicit ellipsis, while the low-priority evidence line
        # never makes it in at all. Asserting the truncated prefix (not the full phrase) is
        # what actually proves the priority rule.
        state = taskstate.new_state("Fix the login form so it rejects duplicate emails.")
        taskstate.merge(state, {"constraints": ["do not rename the public API"]})
        state["evidence"] = ["x" * 200]                     # low priority, long
        block = taskstate.block(state, 500)
        self.assertIn("user_constraints", block)
        self.assertIn("do not rename", block)
        self.assertNotIn("evidence: ", block)

    def test_constraint_survives_in_full_at_a_comfortable_budget(self):
        # Companion case: with room to spare, the whole phrase arrives, still without the
        # evidence tail — priority + truncation are the two halves of the same guarantee.
        state = taskstate.new_state("Fix the login form so it rejects duplicate emails.")
        taskstate.merge(state, {"constraints": ["do not rename the public API"]})
        state["evidence"] = ["x" * 200]
        block = taskstate.block(state, 550)
        self.assertIn("do not rename the public API", block)

    def test_oldest_constraints_survive_the_cap(self):
        state = taskstate.new_state("t")
        for i in range(taskstate.CAPS["constraints"] + 4):
            taskstate.merge(state, {"constraints": [f"rule-{i}"]})
        self.assertEqual(len(state["constraints"]), taskstate.CAPS["constraints"])
        self.assertEqual(state["constraints"][0], "rule-0",
                         "the earliest stated rule is the one the cap preserves")
        self.assertNotIn("rule-12", state["constraints"],
                         "the newest over-cap rule is what is dropped, not an older one")

    def test_fold_turn_captures_both_sides_of_a_dropped_exchange(self):
        state = taskstate.new_state("t")
        taskstate.fold_turn(state, {"role": "user", "content": "keep the pom untouched"})
        taskstate.fold_turn(state, {"role": "assistant", "content": "read_file app.py"})
        self.assertTrue(any("keep the pom" in row for row in state["folded"]))
        self.assertTrue(any(row.startswith("assistant:") for row in state["folded"]))

    def test_note_files_replaces_wholesale_and_dedupes(self):
        state = taskstate.new_state("t")
        taskstate.note_files(state, ["b.py", "a.py", "b.py"])
        self.assertEqual(state["files_examined"], ["a.py", "b.py"])

    def test_merge_ignores_malformed_updates(self):
        state = taskstate.new_state("t")
        taskstate.merge(state, {"status": 12, "constraints": "not-a-list-just-a-string"})
        taskstate.merge(state, "not-a-dict")
        self.assertEqual(state["status"], "understanding",
                         "a non-string scalar never replaces the previous value")

    def test_constraint_text_is_redacted_in_the_block(self):
        # Stage 7: whatever reaches the prompt has to go through the same redactor the rest of
        # the app uses, or a rule that quotes a secret leaks it to the model's log.
        state = taskstate.new_state("t")
        taskstate.merge(state, {"constraints": ["the DB is password=hunter2"]})
        block = taskstate.block(state, 900)
        self.assertNotIn("hunter2", block)
        self.assertIn("[redacted]", block)

    def test_fold_turn_redacts_the_dropped_message(self):
        state = taskstate.new_state("t")
        taskstate.fold_turn(state, {"role": "user", "content": "the AWS key is AKIAIOSFODNN7EXAMPLE"})
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", state["folded"][0])


class EngineContinuityThroughTrim(unittest.TestCase):
    """Stage 1, end-to-end: a constraint stated on turn 0 is still in the prompt on
    turn 1 even after the loop trimmed that very turn's messages out of the history."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        # Sized so a single `read_file` of it, plus the fixed base prompt, pushes past a
        # 12 000-character context and forces the trim. Under the per-file
        # `context_chars // 2` guard so the read is not refused outright.
        filler = ("# " + "a" * 17 + "\n") * 280
        (self.root / "app.py").write_text("def handler():\n    return 1\n" + filler,
                                          encoding="utf-8")
        (self.root / "api.py").write_text("def ping():\n    return 2\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_state_envelope_survives_history_trim(self):
        phrase = "do not rename the public API surface"
        propose = {"action": "propose", "summary": "nudge ping", "checks": ["run unittest"],
                   "changes": [{"path": "api.py", "content": "def ping():\n    return 3\n"}]}
        provider = ScriptedProvider([
            {"action": "read_file", "path": "app.py", "state": {"constraints": [phrase]}},
            propose,       # refused: api.py reached the prompt as a snapshot, not as a read
            dict(propose),  # honoured after the run opened the file itself
        ])
        settings = Settings(context_chars=12000)
        path = plan(self.ws(), "Read app.py then update api.py", provider,
                    settings, self.base / "runs", progress=lambda _: None)
        session = load_session(path)
        # 1. The constraint lives on the session, not just in the messages.
        self.assertIn(phrase, session["task_state"]["constraints"])
        # 2. Trim actually fired — turn 0's assistant+user pair was folded, not dropped.
        self.assertTrue(session["task_state"]["folded"],
                        "expected turn 0's messages to be folded before dropping them")
        # 3. The second prompt still names the constraint under the binding header.
        joined = "\n".join(str(m.get("content", "")) for m in provider.prompts[-1])
        self.assertIn("user_constraints", joined)
        self.assertIn(phrase, joined)

    def ws(self):
        return Workspace(self.root)


class LogDiagnosis(unittest.TestCase):
    """Stage 2: extract the failure's facts once, keep observation apart from hypothesis."""

    PYTEST_LOG = (
        "FAILED tests/test_auth.py::test_rejects_duplicate_email - "
        "AssertionError: expected 409 got 200\n"
        '  File "/repo/app/auth.py", line 42, in signup\n'
        "    raise ValidationError('Email is already registered')\n"
        "ValidationError: Email is already registered\n"
    )

    def test_extract_pulls_named_facts_from_a_pytest_failure(self):
        run = {"command": "python -m pytest", "exit_code": 1, "status": "failed",
               "tail": self.PYTEST_LOG, "failures": []}
        facts = repair.extract(run)
        self.assertTrue(any("test_rejects_duplicate_email" in row
                            for row in facts.get("failed_tests", [])))
        self.assertTrue(any("app/auth.py:42" in row for row in facts.get("error_sites", [])),
                        "the file:line the log names must be quoted, not paraphrased")
        self.assertTrue(any(row.startswith("ValidationError:")
                            for row in facts.get("exceptions", [])))
        self.assertTrue(facts.get("frames"),
                        "a stack frame is kept here on purpose; relevant() drops them")

    def test_evidence_labels_hypothesis_and_names_gaps_when_the_site_is_missing(self):
        # Only a failing test id, no file:line, no exception in its own words. The gap
        # sentence is what tells the model to gather evidence before proposing.
        run = {"command": "pytest", "exit_code": 1, "status": "failed",
               "tail": "FAILED tests/test_x.py::test_y - AssertionError: nope\n",
               "failures": []}
        text = repair.evidence(run)
        self.assertIn("Observed", text)
        self.assertIn("Hypothesis", text,
                       "the category is presented as a hypothesis, never as a confirmed cause")
        self.assertIn("Not established", text)
        self.assertIn("no file and line of the cause", text)

    def test_evidence_reuses_stored_extract_without_reparsing(self):
        # Stage 2: a repair round must not pass the whole log again. When the run already
        # carries `extract`, `evidence()` reads the saved facts, so re-shipping the tail is
        # not required to keep the diagnosis honest.
        run = {"command": "pytest", "exit_code": 1, "status": "failed",
               "failures": [], "tail": "",
               "extract": {"failed_tests": ["saved::test_one"]}}
        text = repair.evidence(run)
        self.assertIn("saved::test_one", text)

    def test_extract_redacts_a_secret_shaped_line(self):
        # Stage 7: a build log can quote a connection string, and every fact that reaches
        # the next prompt goes through `redact()` on the way in.
        leaky = ('  File "/repo/app/db.py", line 9, in connect\n'
                 "password=hunter2 rejected by server\n")
        run = {"command": "pytest", "exit_code": 1, "status": "failed",
               "failures": [], "tail": leaky}
        text = repair.evidence(run)
        self.assertNotIn("hunter2", text)
        self.assertIn("[redacted]", text)


class FileSelection(unittest.TestCase):
    """Stage 3: retrieval should name the file the operator's task actually points at."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        (self.root / "login_forms.py").write_text(
            "def signup_handler(request):\n    return 200\n", encoding="utf-8")
        (self.root / "calculator.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("unrelated notes\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_named_file_lands_in_retrieval_events(self):
        provider = ScriptedProvider([
            {"action": "read_file", "path": "login_forms.py"},
            {"action": "propose", "summary": "flag dupes", "checks": ["run unittest"],
             "changes": [{"path": "login_forms.py",
                          "content": "def signup_handler(request):\n    return 409\n"}]},
        ])
        runs = Path(self.temp.name) / "runs"
        path = plan(Workspace(self.root),
                    "Duplicate emails must be rejected in login_forms.py",
                    provider, Settings(), runs, progress=lambda _: None)
        session = load_session(path)
        loaded = [e.get("path") for e in session["events"]
                  if e.get("kind") in ("context_file", "context_excerpt")]
        self.assertIn("login_forms.py", loaded,
                       "the file the operator named must be pulled in as context")


class StateTransition(unittest.TestCase):
    """Stage 3: the agent's status and stage axes stay legal; a proposal moves the task state."""

    def test_legal_and_illegal_status_transitions(self):
        state = core.AgentState(task=core.Task(description="x"))
        self.assertEqual(state.status, core.AgentStatus.ANALYZING)
        state.transition_to(core.AgentStatus.PLANNING, "plan")
        state.transition_to(core.AgentStatus.WAITING_APPROVAL, "review")
        with self.assertRaises(PolicyError):
            state.transition_to(core.AgentStatus.DONE, "nope")
        self.assertEqual(state.status, core.AgentStatus.WAITING_APPROVAL,
                         "the illegal move left the state where it was")

    def test_failed_can_restart_the_lifecycle(self):
        state = core.AgentState(task=core.Task(description="x"),
                                status=core.AgentStatus.FAILED)
        state.transition_to(core.AgentStatus.ANALYZING, "retry")
        self.assertEqual(state.status, core.AgentStatus.ANALYZING)

    def test_stage_moves_table(self):
        self.assertTrue(core.stage_allowed("plan", "review"))
        self.assertFalse(core.stage_allowed("understand", "verify"))
        self.assertTrue(core.stage_allowed("", "review"),
                         "a session with no recorded stage accepts any stage as its opening move")

    def test_proposal_marks_state_status(self):
        temp = tempfile.TemporaryDirectory()
        try:
            base = Path(temp.name)
            root = base / "repo"
            root.mkdir()
            (root / "app.py").write_text("answer = 1\n", encoding="utf-8")
            # The turn pattern `test_agent.py` uses: read the target file so it lands in
            # `observed`, then propose against it. A one-response provider whose only turn
            # is a propose would be rejected by `prepare_changes` (line 602) and the loop
            # would fall through to a second turn the canned list does not cover.
            provider = ScriptedProvider([
                {"action": "read_file", "path": "app.py"},
                {"action": "propose", "summary": "one", "checks": ["tests"],
                 "changes": [{"path": "app.py", "content": "answer = 2\n"}]},
            ])
            path = plan(Workspace(root), "Change answer", provider, Settings(),
                        base / "runs", progress=lambda _: None)
            session = load_session(path)
            self.assertEqual(session["state"], "WAITING_APPROVAL")
            self.assertEqual(session["task_state"]["status"], "proposal waiting for approval")
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
