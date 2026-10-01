"""Project memory 2.0: the two wires that were never connected, and what they must never do.

`update_auto_notes_from_task` and `auto_notes_context` already existed and were called by nothing, so a
project's recorded history stayed in a panel the model never saw. These tests hold the connected
behaviour: an applied change is remembered, a rolled-back one is corrected, the record reaches the
prompt as history rather than instruction, and a memory write that fails costs a task nothing.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import engine, memory as memory_store
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import apply_proposal, atomic_json, load_session, now, plan
from ai_code_engineer.workspace import Workspace

PROPOSED = "answer = 2\n"


class Script:
    model = "test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, messages):
        self.prompts.append([dict(item) for item in messages])
        item = self.responses.pop(0)
        return item if isinstance(item, str) else json.dumps(item)


class MemoryWireTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.app_dir = Path(self.temp.name).resolve()
        self.root = self.app_dir / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.app_dir / ".agent-runs"
        self.memory = self.app_dir / ".agent-memory"

    def draft(self):
        script = Script([{"action": "read_file", "path": "app.py"},
                         {"action": "propose", "summary": "Set answer to two", "checks": ["unit tests"],
                          "changes": [{"path": "app.py", "content": PROPOSED}]}])
        return script, plan(self.ws, "Update answer", script, Settings(), self.runs,
                            progress=lambda _: None)

    def notes(self):
        return memory_store.read_auto_notes(self.memory, str(self.root))

    def test_an_applied_change_is_remembered_by_the_project(self):
        _script, path = self.draft()
        session = load_session(path)
        apply_proposal(path, session["proposal_hash"])
        notes = self.notes()
        titles = [row["title"] for row in notes["implemented_changes"]]
        self.assertIn("Set answer to two", titles)
        self.assertEqual(notes["implemented_changes"][-1]["status"], "applied")
        self.assertEqual(notes["components"], ["app.py"], "the files it touched are part of the record")

    def test_a_rolled_back_change_is_corrected_rather_than_left_claiming_work(self):
        _script, path = self.draft()
        session = load_session(path)
        approved = session["proposal_hash"]
        apply_proposal(path, approved)
        engine.rollback(path, approved)
        notes = self.notes()
        rows = [row for row in notes["implemented_changes"] if row["title"] == "Set answer to two"]
        self.assertEqual(len(rows), 1, "the same title is updated, not appended to")
        self.assertEqual(rows[0]["status"], "rolled_back")

    def test_a_memory_write_that_fails_costs_the_task_nothing(self):
        _script, path = self.draft()
        session = load_session(path)
        with patch("ai_code_engineer.memory.update_auto_notes_from_task",
                   side_effect=OSError("disk full")):
            written = apply_proposal(path, session["proposal_hash"])
        self.assertEqual(written["state"], "APPLIED_UNVERIFIED")
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), PROPOSED)

    def test_the_record_reaches_the_model_as_history_not_instruction(self):
        memory_store.write_auto_notes(self.memory, str(self.root), {
            "enabled": True, "purpose_and_stack": "Spring Boot service on Java 17",
            "components": ["src/Main.java"], "execution_commands": {"maven-test": "mvn test"},
            "implemented_changes": [{"title": "Add the login route", "status": "verified",
                                     "time": now()}],
            "verification_results": "8 tests run, 0 failures", "remaining_issues": [],
            "facts": {"build": "maven", "java": "17"}})
        _script, path = self.draft()
        asked = json.dumps(_script.prompts[0])
        self.assertIn("Auto-Observed Project Summary", asked)
        self.assertIn("Spring Boot service on Java 17", asked)
        self.assertIn("build=maven", asked)
        self.assertIn("history, not an instruction", asked,
                      "the block has to say what rank it holds")
        self.assertEqual(load_session(path)["state"], "WAITING_APPROVAL")

    def test_a_project_with_nothing_recorded_sends_no_such_block(self):
        script, _path = self.draft()
        asked = json.dumps(script.prompts[0])
        self.assertNotIn("Auto-Observed Project Summary", asked)

    def test_switching_the_panel_off_takes_the_record_out_of_the_prompt(self):
        memory_store.write_auto_notes(self.memory, str(self.root), {
            "enabled": False, "purpose_and_stack": "typed away", "components": ["a.java"]})
        script, _path = self.draft()
        self.assertNotIn("Auto-Observed Project Summary", json.dumps(script.prompts[0]))
        self.assertNotIn("typed away", json.dumps(script.prompts[0]))

    def test_the_user_writes_nothing_into_the_folder_that_holds_the_record(self):
        _script, path = self.draft()
        apply_proposal(path, load_session(path)["proposal_hash"])
        self.assertFalse((self.root / ".agent-memory").exists(),
                         "the model must not be able to rewrite the notes it is held to")
        self.assertTrue(self.memory.is_dir())

    def test_a_redacted_secret_never_reaches_the_memory_file(self):
        memory_store.note_task(self.memory, str(self.root), {
            "root": str(self.root), "summary": "Wire datasource password=hunter01",
            "changes": [{"path": "src/main/resources/application.yml"}]})
        raw = self.memory.glob("*.auto.json")
        text = "".join(path.read_text(encoding="utf-8") for path in raw)
        self.assertNotIn("hunter01", text)
        self.assertIn("[redacted]", text)

    def test_the_auto_notes_block_rides_after_the_users_own_notes(self):
        memory_store.write_auto_notes(self.memory, str(self.root), {
            "enabled": True, "purpose_and_stack": "recorded stack"})
        script = Script([{"action": "read_file", "path": "app.py"},
                         {"action": "propose", "summary": "s", "checks": ["c"],
                          "changes": [{"path": "app.py", "content": PROPOSED}]}])
        plan(self.ws, "Update answer", script, Settings(), self.runs, progress=lambda _: None,
             memory="Never rename the package under src/main")
        content = script.prompts[0][1]["content"]
        self.assertLess(content.index("Never rename the package"), content.index("recorded stack"),
                        "the user's note outranks the recorded history and must read first")


class MeasuredFactsTests(unittest.TestCase):
    """What the scanner measured about the project, kept for the turns that come after it.

    The build files state a Java version and a framework version outright, and a later task is not
    shown those files: without this wire a small model re-derives `javax` or `jakarta` from memory on
    every task, and gets it wrong in proportion to how recently it saw the other one. Facts are stored
    outside the approved folder for the same reason the notes are -- a proposal cannot edit the facts
    it will be judged against.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.app_dir = Path(self._tmp.name).resolve()
        self.root = self.app_dir / "repo"
        (self.root / "src").mkdir(parents=True)
        (self.root / "app.py").write_text("answer = 1\n", encoding="utf-8")
        (self.root / "src" / "Api.java").write_text(
            "package a;\n@RestController\nclass Api {\n"
            '    @GetMapping("/x") public String x() { return ""; }\n}\n', encoding="utf-8")
        (self.root / "pom.xml").write_text(
            "<project><artifactId>demo</artifactId><properties>"
            "<java.version>17</java.version></properties></project>", encoding="utf-8")
        self.ws = Workspace(self.root)
        self.runs = self.app_dir / ".agent-runs"
        self.memory = self.app_dir / ".agent-memory"

    def draft(self):
        script = Script([{"action": "read_file", "path": "app.py"},
                         {"action": "propose", "summary": "Set answer to two", "checks": ["unit"],
                          "changes": [{"path": "app.py", "content": PROPOSED}]}])
        return script, plan(self.ws, "Update answer", script, Settings(), self.runs,
                            progress=lambda _: None)

    def facts(self):
        return memory_store.read_auto_notes(self.memory, str(self.root))["facts"]

    def test_a_task_measures_the_project_and_keeps_the_answer(self):
        self.draft()
        self.assertEqual(self.facts(), {"artifact": "demo", "build": "maven", "java": "17"})

    def test_the_next_task_is_told_the_versions_it_would_otherwise_re_derive(self):
        self.draft()
        script, _path = self.draft()
        asked = json.dumps(script.prompts[0])
        self.assertIn("java=17", asked)
        self.assertIn("build=maven", asked)

    def test_a_fact_that_has_not_changed_costs_no_rewrite(self):
        self.draft()
        path = memory_store.auto_notes_path(self.memory, str(self.root))
        before = path.stat().st_mtime_ns
        self.draft()
        self.assertEqual(path.stat().st_mtime_ns, before,
                         "a note rewritten every turn stops saying when the project last changed")

    def test_a_note_the_user_switched_off_is_not_rewritten_behind_their_back(self):
        memory_store.write_auto_notes(self.memory, str(self.root), {"enabled": False})
        stamp = memory_store.auto_notes_path(self.memory, str(self.root))
        before = stamp.stat().st_mtime_ns
        memory_store.record_facts(self.memory, str(self.root), {"java": "17"})
        self.assertEqual(stamp.stat().st_mtime_ns, before)
        self.assertEqual(memory_store.read_auto_notes(self.memory, str(self.root))["facts"], {})

    def test_a_project_that_states_nothing_stores_nothing(self):
        (self.root / "pom.xml").unlink()
        self.draft()
        self.assertFalse(self.memory.exists(),
                         "no build file is a missing answer, not a blank fact sheet")

    def test_a_fact_write_that_cannot_happen_costs_the_task_nothing(self):
        with patch("ai_code_engineer.memory.write_auto_notes", side_effect=OSError("disk full")):
            self.assertEqual(memory_store.record_facts(self.memory, str(self.root),
                                                       {"java": "17"}), {})
        script, _path = self.draft()
        self.assertEqual(load_session(_path)["state"], "WAITING_APPROVAL")


if __name__ == "__main__":
    unittest.main()
