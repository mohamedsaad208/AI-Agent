"""A task that stopped is not a task that finished: what is stored, and what resuming trusts.

`session.json` says where a run got to; `turns.json` is what lets it carry on. These tests hold the two
rules that make that safe — nothing the run could not re-verify is trusted after the fact, and a stored
turn is capped and scrubbed before it reaches the disk.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import engine
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import (LOCK_NAME, LOCK_STALE_SECONDS, TURNS_NAME, TURN_CHARS,
                                     TURNS_TOTAL_CHARS, load_session, load_turns, plan, save_turns,
                                     turns_file)
from ai_code_engineer.errors import AgentError, PolicyError
from ai_code_engineer.workspace import Workspace


class Script:
    """A provider that can die mid-turn, which is the whole reason this feature exists."""

    model = "test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, messages):
        self.prompts.append([dict(item) for item in messages])
        item = self.responses.pop(0) if self.responses else {"action": "blocked", "reason": "empty"}
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.base / "runs"

    def proposal(self, content="answer = 2\n"):
        return {"action": "propose", "summary": "Update answer", "checks": ["unit tests"],
                "changes": [{"path": "app.py", "content": content}]}

    def crash_mid_turn(self, responses=None):
        """Run until the script dies, and hand back the run identity it left behind."""
        script = Script(responses or [{"action": "read_file", "path": "app.py"},
                                      RuntimeError("process killed")])
        with self.assertRaises(RuntimeError):
            plan(self.ws, "Update answer", script, Settings(), self.runs, progress=lambda _: None)
        run_id = next(path.name for path in self.runs.iterdir() if path.is_dir())
        return run_id, script

    def test_a_run_that_died_leaves_what_it_needed_to_continue(self):
        run_id, _script = self.crash_mid_turn()
        session = load_session(self.runs / run_id / "session.json")
        self.assertEqual(session["state"], "DISCOVERING")
        turns = load_turns(self.runs / run_id / TURNS_NAME)
        self.assertEqual(turns["turn"], 1, "one turn is done; the next one is turn 2")
        self.assertEqual([item["role"] for item in turns["turns"]], ["assistant", "user"])
        self.assertEqual(turns["observed"], {"app.py": turns["observed"]["app.py"]})
        self.assertEqual(turns["run_id"], run_id)

    def test_resuming_continues_the_turn_counter_instead_of_restarting(self):
        run_id, _before = self.crash_mid_turn()
        lines = []
        script = Script([self.proposal()])
        path = plan(self.ws, "Update answer", script, Settings(), self.runs,
                    progress=lines.append, resume_run=run_id)
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL")
        self.assertIn("Turn 2/", "\n".join(lines), "the resumed run is on its second turn, not its first")
        asked = script.prompts[0]
        self.assertEqual(asked[0]["role"], "system")
        self.assertIn('"action": "read_file"', asked[2]["content"],
                      "the turn the run already spent is still in front of the model")

    def test_a_resumed_run_reverifies_the_files_it_thinks_it_read(self):
        run_id, _before = self.crash_mid_turn()
        (self.root / "app.py").write_text("answer = 99\n", encoding="utf-8")
        script = Script([self.proposal(), self.proposal("answer = 100\n")])
        path = plan(self.ws, "Update answer", script, Settings(), self.runs,
                    progress=lambda _: None, resume_run=run_id)
        self.assertEqual(len(script.prompts), 2,
                         "the stale proposal came back refused, with the current file attached")
        self.assertIn("answer = 99", json.dumps(script.prompts[1]))
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL")

    def test_a_run_that_reached_a_result_is_not_resumable(self):
        script = Script([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Update answer", script, Settings(), self.runs, progress=lambda _: None)
        run_id = path.parent.name
        with self.assertRaisesRegex(PolicyError, "already reached a result"):
            plan(self.ws, "Update answer", Script([self.proposal()]), Settings(), self.runs,
                 progress=lambda _: None, resume_run=run_id)

    def test_an_identity_that_is_not_a_run_is_refused_before_anything_is_read(self):
        with self.assertRaisesRegex(PolicyError, "Invalid run identity"):
            plan(self.ws, "Update answer", Script([self.proposal()]), Settings(), self.runs,
                 progress=lambda _: None, resume_run="not-a-run")

    def test_a_run_belonging_to_another_folder_is_not_this_folders_run(self):
        run_id, _before = self.crash_mid_turn()
        elsewhere_root = self.base / "other"
        shutil.copytree(self.root, elsewhere_root)
        session_path = self.runs / run_id / "session.json"
        stored = json.loads(session_path.read_text(encoding="utf-8"))
        stored["root"] = str(elsewhere_root)
        session_path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaisesRegex(PolicyError, "different project folder"):
            plan(self.ws, "Update answer", Script([self.proposal()]), Settings(), self.runs,
                 progress=lambda _: None, resume_run=run_id)

    def test_the_failure_counters_carry_over_so_budget_is_not_renewed(self):
        # Each reply is different on purpose: three identical ones trip the repeat guard first, and
        # this test is about the invalid-action budget the resumed run must not get back.
        run_id, _before = self.crash_mid_turn([{"action": "nonsense", "query": "0"},
                                               RuntimeError("killed")])
        script = Script([{"action": "nonsense", "query": "1"}, {"action": "nonsense", "query": "2"},
                         {"action": "nonsense", "query": "3"}])
        with self.assertRaisesRegex(AgentError, "invalid-action budget"):
            plan(self.ws, "Update answer", script, Settings(), self.runs,
                 progress=lambda _: None, resume_run=run_id)
        self.assertEqual(len(script.prompts), 3, "the resumed run got three tries, not four")

    def test_a_turn_being_written_holds_the_run_against_a_second_writer(self):
        run_id, _before = self.crash_mid_turn()
        (self.runs / run_id / LOCK_NAME).write_text(str(os.getpid()), encoding="utf-8")
        with self.assertRaisesRegex(PolicyError, "Another window"):
            plan(self.ws, "Update answer", Script([self.proposal()]), Settings(), self.runs,
                 progress=lambda _: None, resume_run=run_id)
        stale = time.time() - LOCK_STALE_SECONDS - 5
        os.utime(self.runs / run_id / LOCK_NAME, (stale, stale))
        path = plan(self.ws, "Update answer", Script([self.proposal()]), Settings(), self.runs,
                    progress=lambda _: None, resume_run=run_id)
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL")
        self.assertFalse((self.runs / run_id / LOCK_NAME).exists(),
                         "a finished run leaves no lock behind")

    def test_a_fresh_run_clears_its_lock_even_when_it_dies(self):
        script = Script([RuntimeError("killed")])
        with self.assertRaises(RuntimeError):
            plan(self.ws, "Update answer", script, Settings(), self.runs, progress=lambda _: None)
        run_dir = next(path for path in self.runs.iterdir() if path.is_dir())
        self.assertFalse((run_dir / LOCK_NAME).exists())

    def test_the_continuation_is_capped_and_scrubbed_before_it_reaches_disk(self):
        turns = [{"role": "assistant", "content": "x" * (TURN_CHARS * 3)},
                 {"role": "user", "content": "password=hunter01 " + "y" * 500}]
        for index in range(40):
            turns.append({"role": "assistant", "content": f"turn {index} " + "z" * 3000})
            turns.append({"role": "user", "content": f"seen {index} " + "w" * 3000})
        path = turns_file(self.runs / ("f" * 32) / "session.json")
        save_turns(path, run_id="f" * 32, turn=41, history=turns,
                   observed={"app.py": "sha"}, counters={"failures": 1}, elapsed=12.0)
        raw = path.read_text(encoding="utf-8")
        stored = json.loads(raw)
        self.assertNotIn("hunter01", raw, "a stored turn is a redacted turn")
        self.assertLessEqual(sum(len(item["content"]) for item in stored["turns"]),
                             TURNS_TOTAL_CHARS + 4000, "the oldest pairs are dropped first")
        self.assertEqual(stored["turns"][-1]["content"][:7], "seen 39")
        self.assertEqual(stored["turns"][0]["role"], "assistant")
        self.assertFalse(len(stored["turns"]) % 2, "trimming keeps question and answer pairs")
        self.assertEqual(stored["elapsed"], 12.0)
        self.assertTrue((path.parent / LOCK_NAME).exists(), "writing the turn is the heartbeat")

    def test_an_unreadable_continuation_is_no_continuation(self):
        path = self.runs / ("a" * 32) / TURNS_NAME
        path.parent.mkdir(parents=True)
        self.assertIsNone(load_turns(path), "absent")
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(load_turns(path))
        path.write_text(json.dumps({"schema": 2, "turns": [], "observed": {}}), encoding="utf-8")
        self.assertIsNone(load_turns(path), "a future shape is not understood as this one")
        path.write_text(json.dumps({"schema": 1, "turns": "no", "observed": {}}), encoding="utf-8")
        self.assertIsNone(load_turns(path))

    def test_a_turn_file_that_cannot_be_read_starts_the_loop_without_history(self):
        run_id, _before = self.crash_mid_turn()
        (self.runs / run_id / TURNS_NAME).write_text("{broken", encoding="utf-8")
        script = Script([{"action": "read_file", "path": "app.py"}, self.proposal()])
        path = plan(self.ws, "Update answer", script, Settings(), self.runs,
                    progress=lambda _: None, resume_run=run_id)
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL")
        self.assertEqual(len(script.prompts), 2, "it read the file again because nothing was remembered")


class ResumeThroughTheWindowTests(unittest.TestCase):
    """The web window reads what stopped, says it, and waits for a press."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.repo = self.app_dir / "repo"
        self.repo.mkdir()
        (self.repo / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.runs = self.app_dir / ".agent-runs"

    def stop_a_task(self):
        script = Script([{"action": "read_file", "path": "app.py"}, RuntimeError("killed")])
        with self.assertRaises(RuntimeError):
            plan(Workspace(self.repo), "Update answer", script, Settings(), self.runs,
                 progress=lambda _: None)
        return next(path.name for path in self.runs.iterdir() if path.is_dir())

    def controller(self):
        from controller_case import Scripted
        from doubles import OLLAMA_ENTRY
        controller = Scripted(self.app_dir)
        controller.catalogs["Ollama"] = [OLLAMA_ENTRY]
        controller.model = "test-local"
        controller.set_repo(str(self.repo))
        self.addCleanup(controller.close)
        return controller

    def test_opening_the_folder_does_not_resume_the_task_by_itself(self):
        run_id = self.stop_a_task()
        controller = self.controller()
        controller.refresh_resumable()
        listed = [row["run_id"] for row in controller.snapshot()["resume"]]
        self.assertEqual(listed, [run_id])
        self.assertFalse(controller.busy)
        self.assertIsNone(controller.session, "a stopped task is shown, not started")

    def test_pressing_resume_continues_the_same_run_id(self):
        run_id = self.stop_a_task()
        controller = self.controller()
        controller.refresh_resumable()
        model = Script([self.proposal()])
        with patch("ai_code_engineer.webapp.controller.make_provider", return_value=model):
            controller.action("resume_task", {"run_id": run_id}, lambda event: None)
            controller.join()
        session = load_session(self.runs / run_id / "session.json")
        self.assertEqual(session["state"], "WAITING_APPROVAL")
        self.assertEqual(session["id"], run_id, "the continuation is the same task, not a new one")
        self.assertEqual(len(model.prompts[0]) > 2, True, "the stored turns came back into the prompt")
        controller.refresh_resumable()
        self.assertEqual(controller.snapshot()["resume"], [], "a finished task leaves the strip")

    def test_a_run_that_is_not_on_the_list_is_refused_without_starting_anything(self):
        self.stop_a_task()
        controller = self.controller()
        controller.refresh_resumable()
        controller.action("resume_task", {"run_id": "z" * 32}, lambda event: None)
        self.assertIn("no longer here", controller.status)
        self.assertFalse(controller.busy)

    def proposal(self):
        return {"action": "propose", "summary": "Update answer", "checks": ["unit tests"],
                "changes": [{"path": "app.py", "content": "answer = 2\n"}]}


class ResumableLookupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.runs = self.base / "runs"
        self.root = self.base / "repo"
        self.root.mkdir()

    def write(self, ident, state, root=None, turns=None):
        folder = self.runs / ident
        folder.mkdir(parents=True)
        (folder / "session.json").write_text(json.dumps(
            {"schema": 1, "id": ident, "root": str(root or self.root), "task": "t " + ident,
             "state": state, "created": "2026-10-01T00:00:00+00:00", "events": [], "model": "test"}),
            encoding="utf-8")
        if turns is not None:
            (folder / TURNS_NAME).write_text(json.dumps(turns), encoding="utf-8")

    def listed(self):
        from ai_code_engineer import session_flow
        return session_flow.resumable_runs(self.runs, str(self.root), {})

    def test_only_an_unfinished_run_of_this_folder_is_listed(self):
        write = self.write
        write("a" * 32, "DISCOVERING", turns={"schema": 1, "turn": 3, "turns": [], "observed": {}})
        write("b" * 32, "WAITING_APPROVAL")
        write("c" * 32, "BLOCKED")
        write("d" * 32, "DISCOVERING", root=self.base / "elsewhere")
        rows = self.listed()
        self.assertEqual([row["run_id"] for row in rows], ["a" * 32])
        self.assertEqual(rows[0]["turn"], 3)

    def test_a_stopped_run_with_no_continuation_is_still_shown(self):
        self.write("e" * 32, "DISCOVERING")
        rows = self.listed()
        self.assertEqual([row["run_id"] for row in rows], ["e" * 32])
        self.assertEqual(rows[0]["turn"], 0, "there is nothing to carry on from, and it says so")

    def test_an_unreadable_session_is_skipped_not_raised(self):
        self.write("f" * 32, "DISCOVERING")
        (self.runs / ("f" * 32) / "session.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.listed(), [])

    def test_a_folder_that_does_not_exist_lists_nothing(self):
        self.write("a" * 32, "DISCOVERING")
        from ai_code_engineer import session_flow
        self.assertEqual(session_flow.resumable_runs(self.runs, str(self.base / "gone"), {}), [])


if __name__ == "__main__":
    unittest.main()
