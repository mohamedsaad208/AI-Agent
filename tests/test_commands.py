"""Tests for the unified commands handler (Release 4 - Task 4.3).

Every one of the twelve answers is driven twice over where it can be: through the handler on a
stored session, and through a fake host standing for a window with a job in flight — because the
whole claim of this task is that one handler answers both surfaces. The undo tests run against
real files on disk: an undo that only moved a state string did not put anything back.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import commands as cmd
from ai_code_engineer import memory, report
from ai_code_engineer.engine import SteeringInbox, atomic_json, load_session, proposal_hash
from ai_code_engineer.errors import AgentError, PolicyError
from ai_code_engineer.workspace import digest

BEFORE = "def add(a, b):\n    return a-b\n"
AFTER = "def add(a, b):\n    return a+b\n"


def change(path="app/calc.py", before=BEFORE, after=AFTER, delete=False):
    return {"path": path,
            "before": before, "before_hash": digest((before or "").encode("utf-8")),
            "after": None if delete else after,
            "after_hash": digest((after or "").encode("utf-8")),
            **({"delete": True} if delete else {})}


def session(over=None):
    """A stored session shaped the way engine.load_session hands one back."""
    data = {"schema": 1, "id": "abc123def", "root": "C:/demo", "model": "test-model",
            "task": "fix add so it adds", "state": "APPLIED_UNVERIFIED",
            "created": "2026-01-01T09:00:00+00:00", "stage": "verify",
            "summary": "Turns the minus in add() into a plus", "checks": ["run the tests"],
            "changes": [change(), change("app/new.py", before=None, after="count = 0\n")],
            "events": [{"at": "2026-01-01T09:00:01+00:00", "kind": "written", "path": "app/calc.py"}],
            "runs": [{"label": "unit tests", "recipe": "pytest", "command": "pytest -q",
                      "status": "failed", "exit_code": 1, "seconds": 2.5,
                      "proof": {"tests": 12, "failures": 1, "errors": 0, "source": "summary"},
                      "failures": ["app/test_calc.py::test_add — assert 1 == 2"], "tail": "FAILED"}],
            "verification": {"status": "failed", "static": ["manifest: requirements.txt lists pytest"]},
            "steering": [{"id": "s1", "at": "t", "text": "also cover negatives", "turn": 1,
                          "read": False}],
            "memory": "Use the existing fixtures."}
    data["proposal_hash"] = proposal_hash(data)
    data.update(over or {})
    return data


class Host:
    """A window with a job in flight: busy, stoppable, and holding this run's steering box."""

    def __init__(self, busy=True, cancellable=True, current=None):
        self.busy = busy
        self.cancellable = cancellable
        self.stops = 0
        self._steering = SteeringInbox()
        self.session = current if current is not None else session()
        self.repo = "C:/demo"

    def stop(self):
        self.stops += 1


def plain(renderable) -> str:
    from rich.console import Console
    buffer = io.StringIO()
    Console(file=buffer, force_terminal=False, no_color=True, width=100).print(renderable)
    return buffer.getvalue()


class DispatchTests(unittest.TestCase):
    def test_all_twelve_are_reachable_and_named_in_help(self):
        self.assertEqual(len(cmd.COMMANDS), 12)
        result = cmd.Commands(session()).run("help")
        for name in cmd.COMMANDS:
            self.assertIn(name, result.text)

    def test_a_slip_is_answered_with_the_list_not_a_traceback(self):
        with self.assertRaises(AgentError) as caught:
            cmd.Commands(session()).run("whatever")
        self.assertIn("status", str(caught.exception))

    def test_session_commands_refuse_when_no_session_is_open(self):
        for name in cmd.NEEDS_SESSION - {"memory"}:
            with self.assertRaises(AgentError):
                cmd.Commands().run(name)

    def test_the_leading_slash_is_optional(self):
        self.assertEqual(cmd.Commands(session()).run("status").command,
                         cmd.Commands(session()).run("/status").command)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.result = cmd.Commands(session()).run("status")

    def test_the_state_and_the_stage_are_said_in_words(self):
        from ai_code_engineer.labels import state_label
        self.assertIn("State:", self.result.text)
        self.assertIn(state_label("APPLIED_UNVERIFIED"), self.result.text)
        self.assertIn("Stage:", self.result.text)

    def test_the_answer_carries_the_counts_a_person_asks_for(self):
        self.assertEqual(self.result.data["files"], 2)
        self.assertEqual(self.result.data["runs"], 1)
        self.assertEqual(self.result.data["model"], "test-model")

    def test_the_tail_of_the_event_log_rides_the_answer(self):
        self.assertIn("Last events:", self.result.text)
        self.assertIn("written", self.result.text)

    def test_a_steering_instruction_that_was_never_read_shows_as_waiting(self):
        self.assertEqual(self.result.data["steering_waiting"], 1)
        self.assertIn("Steering waiting", self.result.text)


class PlanTests(unittest.TestCase):
    def test_the_five_sections_of_plan_mode_are_the_answer(self):
        result = cmd.Commands(session()).run("plan")
        for heading in ("Goal:", "Affected files", "Steps", "Risks", "Test strategy"):
            self.assertIn(heading, result.text)
        self.assertIn("app/calc.py", result.text)
        self.assertEqual(result.data["view"]["goal"], "fix add so it adds")

    def test_the_proposal_hash_the_apply_will_check_is_printed(self):
        self.assertIn(session()["proposal_hash"], cmd.Commands(session()).run("plan").text)


class ChangesTests(unittest.TestCase):
    def test_one_row_per_file_with_counts(self):
        result = cmd.Commands(session()).run("changes")
        self.assertIn("2 file(s)", result.text)
        self.assertIn("app/calc.py", result.text)
        self.assertEqual([row["path"] for row in result.data["files"]],
                         ["app/calc.py", "app/new.py"])

    def test_the_renderables_are_the_same_table_the_diff_view_paints(self):
        from ai_code_engineer import diff_view
        built = diff_view.collect(session())
        result = cmd.Commands(session()).run("changes")
        self.assertEqual(plain(result.renderables[1]), plain(diff_view.summary_table(built)))


class DiffTests(unittest.TestCase):
    def test_the_hunks_are_there_as_plain_text_for_any_surface(self):
        result = cmd.Commands(session()).run("diff")
        self.assertIn("-    return a-b", result.text)
        self.assertIn("+    return a+b", result.text)

    def test_one_file_can_be_asked_for_by_path(self):
        result = cmd.Commands(session()).run("diff app/new.py")
        self.assertEqual(result.data["files"], ["app/new.py"])
        self.assertNotIn("calc", result.text)

    def test_a_file_the_change_set_never_touches_is_said_not_left_blank(self):
        result = cmd.Commands(session()).run("diff app/other.py")
        self.assertIn("Nothing in this change set touches app/other.py", result.text)

    def test_context_lines_are_askable_and_an_argument_needs_a_number(self):
        self.assertEqual(cmd.Commands(session()).run("diff --context 5").data["context"], 5)
        with self.assertRaises(AgentError):
            cmd.Commands(session()).run("diff --context many")


class TestsCommandTests(unittest.TestCase):
    def test_every_run_is_answered_with_what_it_proved(self):
        result = cmd.Commands(session()).run("tests")
        self.assertIn("unit tests", result.text)
        self.assertIn("12 tests, 1 failed", result.text)
        self.assertIn("app/test_calc.py", result.text)
        self.assertIn("failed", result.text)

    def test_a_session_that_never_ran_a_command_says_so(self):
        result = cmd.Commands(session({"runs": [], "verification": None})).run("tests")
        self.assertIn("No command has run", result.text)

    def test_the_machine_reading_matches_the_export_layer(self):
        data = cmd.Commands(session()).run("tests").data
        self.assertEqual(data["runs"], report.runs_of(session()))


class RisksTests(unittest.TestCase):
    def test_the_band_and_the_reasons_come_from_the_same_view_plan_shows(self):
        from ai_code_engineer.engine import plan_mode_view
        result = cmd.Commands(session()).run("risks")
        self.assertIn("Risk level:", result.text)
        self.assertEqual(result.data["risks"], plan_mode_view(session())["risks"])


class StopTests(unittest.TestCase):
    def test_a_live_job_is_asked_to_stop_through_its_own_host(self):
        host = Host()
        result = cmd.Commands(host=host).run("stop")
        self.assertEqual(host.stops, 1)
        self.assertIn("Stop requested", result.text)

    def test_with_nothing_running_the_answer_is_plain_and_not_an_error(self):
        host = Host(busy=False)
        result = cmd.Commands(host=host).run("stop")
        self.assertEqual(host.stops, 0)
        self.assertIn("Nothing is running to stop", result.text)

    def test_a_step_that_cannot_be_interrupted_is_not_promised_a_stop(self):
        host = Host(cancellable=False)
        result = cmd.Commands(host=host).run("stop")
        self.assertEqual(host.stops, 0)
        self.assertIn("cannot be interrupted", result.text)


class SteerTests(unittest.TestCase):
    def test_an_instruction_lands_in_the_running_job_box(self):
        host = Host()
        result = cmd.Commands(host=host).run("steer use the existing fixture")
        self.assertIn("Steering received", result.text)
        self.assertEqual([row["text"] for row in host._steering.waiting()],
                         ["use the existing fixture"])

    def test_urgent_is_a_flag_not_part_of_the_sentence(self):
        host = Host()
        cmd.Commands(host=host).run("steer --urgent stop touching the migrations")
        row = host._steering.waiting()[0]
        self.assertTrue(row["urgent"])
        self.assertEqual(row["text"], "stop touching the migrations")

    def test_an_empty_instruction_is_refused_the_way_the_box_refuses_it(self):
        with self.assertRaises(AgentError):
            cmd.Commands(host=Host()).run("steer")

    def test_nothing_running_means_nothing_to_steer(self):
        result = cmd.Commands(host=Host(busy=False)).run("steer hurry up")
        self.assertIn("Nothing is running to steer", result.text)


class ReportTests(unittest.TestCase):
    def test_markdown_is_the_readable_answer_and_tables_the_painted_one(self):
        result = cmd.Commands(session()).run("report")
        self.assertIn("abc123def", result.text + json.dumps(result.data))
        self.assertTrue(result.renderables)

    def test_json_is_the_machine_answer(self):
        result = cmd.Commands(session()).run("report json")
        json.loads(result.text)
        self.assertEqual(result.data["state"], "APPLIED_UNVERIFIED")

    def test_only_the_two_formats_are_offered(self):
        with self.assertRaises(AgentError):
            cmd.Commands(session()).run("report pdf")


class UndoTests(unittest.TestCase):
    """Undo against real files: what the session wrote is what must go back."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.repo = base / "repo"
        self.session_path = base / "runs" / "abc123def" / "session.json"
        self.addCleanup(self.dir.cleanup)

    def write_repo(self, files):
        for relative, content in files.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            (path if content is None else path).write_bytes((content or "").encode("utf-8"))

    def stored(self, over=None):
        data = session(over)
        data["root"] = str(self.repo)
        data["proposal_hash"] = proposal_hash(data)
        atomic_json(self.session_path, data)
        return data

    def test_an_applied_session_gives_its_files_back(self):
        self.write_repo({"app/calc.py": AFTER, "app/new.py": "count = 0\n"})
        self.stored()
        runner = cmd.Commands(load_session(self.session_path), session_path=self.session_path)
        answer = runner.run("undo")
        self.assertIn("rolled back", answer.text.lower())
        self.assertEqual((self.repo / "app/calc.py").read_bytes().decode("utf-8"), BEFORE)
        self.assertFalse((self.repo / "app/new.py").exists())
        self.assertEqual(answer.data["state"], "ROLLED_BACK")

    def test_a_session_that_never_wrote_nothing_has_nothing_to_undo(self):
        self.write_repo({"app/calc.py": BEFORE})
        self.stored({"state": "COMPLETED"})
        with self.assertRaises(AgentError):
            cmd.Commands(load_session(self.session_path),
                         session_path=self.session_path).run("undo")

    def test_an_edit_made_after_the_write_blocks_the_undo_instead_of_swallowing_it(self):
        self.write_repo({"app/calc.py": "somebody else's version\n", "app/new.py": ""})
        self.stored()
        with self.assertRaises(PolicyError) as caught:
            cmd.Commands(load_session(self.session_path),
                         session_path=self.session_path).run("undo")
        self.assertIn("later edit", str(caught.exception))

    def test_undo_needs_the_stored_file_not_just_a_memory_of_it(self):
        with self.assertRaises(AgentError):
            cmd.Commands(session()).run("undo")


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.memory_dir = Path(self.dir.name) / ".agent-memory"
        self.addCleanup(self.dir.cleanup)

    def test_the_notes_the_operator_wrote_and_the_session_recorded_are_both_said(self):
        memory.write(self.memory_dir, "C:/demo", "Never edit the vendored folder.")
        result = cmd.Commands(session(), memory_dir=self.memory_dir).run("memory")
        self.assertIn("Never edit the vendored folder.", result.text)
        self.assertIn("Use the existing fixtures.", result.text)

    def test_a_project_nobody_has_written_notes_about_says_so_rather_than_failing(self):
        result = cmd.Commands(session(), memory_dir=self.memory_dir).run("memory")
        self.assertIn("none written", result.text)


class MemoryCommandTests(unittest.TestCase):
    """`memory` and its sub-commands, run against a real project folder on disk.

    The class above reads the two legacy note files; this one reads `.agent/memory/`, the store the
    compass is built from. Both go through the same handler, because the claim of the task is that a
    terminal line and a window click reach one answer — so every assertion here is made on the text a
    person would actually see, or on the markdown file they would open afterwards to check it.
    """

    CHAT = "0123456789abcdef0123456789abcdef"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.memory_dir = self.root / ".agent-memory"
        self.store_dir = self.root / ".agent" / "memory"

    def commands(self, **over):
        return cmd.Commands(session({"root": str(self.root), **over}), memory_dir=self.memory_dir)

    def project_file(self) -> str:
        return (self.store_dir / "project.md").read_text(encoding="utf-8")

    def propose(self, text):
        """The way a run states a goal: through the guard, without an approval to spend."""
        from ai_code_engineer import compass
        return compass.brief.record_goal(compass.store_for(self.root), text)

    def test_an_empty_project_says_so_without_inventing_a_file(self):
        result = self.commands().run("memory")
        self.assertIn("none written", result.text)
        self.assertFalse(self.store_dir.exists())
        self.assertFalse(result.data["memory"]["has_memory"])

    def test_a_goal_typed_by_the_operator_is_written_to_the_file_and_read_back(self):
        result = self.commands().run("memory edit goal Ship the reporting dashboard")
        self.assertIn("## Goal\nShip the reporting dashboard", self.project_file())
        self.assertIn("Ship the reporting dashboard", result.text)
        self.assertEqual(result.data["reason"], "opened")
        self.assertIn("goal: Ship the reporting dashboard", self.commands().run("memory").text)

    def test_renaming_the_goal_needs_no_second_approval_because_a_person_typed_it(self):
        self.commands().run("memory edit goal Ship the reporting dashboard")
        result = self.commands().run("memory edit goal Ship the billing dashboard instead")
        self.assertEqual(result.data["reason"], "approved")
        self.assertIn("Ship the billing dashboard instead", self.project_file())

    def test_a_list_section_appends_and_leaves_the_rules_beside_it_alone(self):
        self.commands().run("memory edit constraints Never touch the vendored folder")
        self.commands().run("memory edit constraints Use the existing fixtures")
        text = self.project_file()
        self.assertIn("- Never touch the vendored folder", text)
        self.assertIn("- Use the existing fixtures", text)

    def test_a_full_section_refuses_the_newcomer_rather_than_dropping_an_old_rule(self):
        for number in range(12):
            self.commands().run("memory edit constraints rule number " + str(number))
        with self.assertRaises(AgentError) as caught:
            self.commands().run("memory edit constraints one rule too many")
        self.assertIn("full", str(caught.exception))

    def test_the_chat_sections_wait_for_a_chat_and_the_project_sections_do_not(self):
        with self.assertRaises(AgentError):
            self.commands().run("memory edit task Add the missing plus")
        with self.assertRaises(AgentError):
            self.commands().run("memory edit steps read the file first")
        self.commands(chat_id=self.CHAT).run("memory edit task Add the missing plus")
        chat_file = self.store_dir / "chats" / (self.CHAT + ".md")
        self.assertIn("## Current task\nAdd the missing plus",
                      chat_file.read_text(encoding="utf-8"))

    def test_only_the_sections_the_store_knows_can_be_written(self):
        with self.assertRaises(AgentError) as caught:
            self.commands().run("memory edit feelings great")
        self.assertIn("constraints", str(caught.exception))

    def test_a_secret_in_a_written_line_never_reaches_the_file(self):
        self.commands().run("memory edit goal use the key sk-abcdefghij1234567890WXYZ")
        text = self.project_file()
        self.assertNotIn("sk-abcdefghij1234567890WXYZ", text)
        self.assertIn("[redacted]", text)

    def test_reset_names_the_layer_and_echoes_what_it_removed(self):
        self.commands().run("memory edit goal Ship the reporting dashboard")
        self.commands().run("memory edit constraints Use the existing fixtures")
        result = self.commands().run("memory reset project")
        self.assertIn("Use the existing fixtures", result.text)
        self.assertFalse((self.store_dir / "project.md").exists())
        self.assertFalse((self.store_dir / "project.json").exists())

    def test_reset_of_an_empty_layer_is_said_rather_than_thrown(self):
        result = self.commands(chat_id=self.CHAT).run("memory reset chat")
        self.assertIn("already empty", result.text)

    def test_the_memory_answers_arrive_in_the_language_the_task_was_asked_in(self):
        """The same table the web window reads, in the terminal's voice.

        The sentences are shared now, so this is the guard that the two surfaces cannot drift into
        answering an Arabic project in English while the other one answers in Arabic.
        """
        from ai_code_engineer import labels
        spoke = self.commands(task="أصلح دالة الجمع في calculator.py", chat_id=self.CHAT)
        self.assertTrue(spoke.arabic, "the terminal reads the task it was handed")
        for line in ("memory reset chat", "memory approve"):
            result = spoke.run(line)
            self.assertTrue(labels.is_arabic(result.text), line + " -> " + result.text)
        with self.assertRaises(Exception) as caught:
            spoke.run("memory edit feelings add a rule")
        self.assertTrue(labels.is_arabic(str(caught.exception)),
                        "the refusal reached the terminal in English: " + str(caught.exception))

    def test_reset_without_a_target_clears_the_chat_because_that_is_the_working_layer(self):
        self.commands(chat_id=self.CHAT).run("memory edit task Add the missing plus")
        result = self.commands(chat_id=self.CHAT).run("memory reset")
        self.assertIn("the chat memory", result.text)

    def test_an_unknown_target_is_refused_with_the_three_that_exist(self):
        with self.assertRaises(AgentError) as caught:
            self.commands().run("memory reset everything")
        self.assertIn("project", str(caught.exception))

    def test_a_proposed_goal_waits_and_only_approve_moves_it(self):
        self.commands().run("memory edit goal Ship the reporting dashboard")
        self.propose("Abandon the dashboard")
        self.assertIn("waiting for you", self.commands().run("memory").text)
        self.assertEqual(self.commands().run("memory reject").data["reason"], "rejected")
        self.assertIn("Ship the reporting dashboard", self.project_file())
        self.propose("Abandon the dashboard")
        self.assertEqual(self.commands().run("memory approve").data["reason"], "approved")
        self.assertIn("Abandon the dashboard", self.project_file())

    def test_approving_when_nothing_waits_says_so(self):
        self.assertIn("Nothing is waiting", self.commands().run("memory approve").text)

    def test_edit_without_a_section_and_without_a_file_points_at_the_line_that_works(self):
        with self.assertRaises(AgentError) as caught:
            self.commands().run("memory edit")
        self.assertIn("memory edit goal", str(caught.exception))

    def test_a_slip_after_memory_lists_the_sub_commands(self):
        with self.assertRaises(AgentError) as caught:
            self.commands().run("memory forget")
        self.assertIn("memory reset", str(caught.exception))

    def test_memory_works_with_no_project_at_all_and_edit_says_where_to_point_it(self):
        self.assertIn("none written", cmd.Commands(session({"root": ""})).run("memory").text)
        with self.assertRaises(AgentError) as caught:
            cmd.Commands(session({"root": ""})).run("memory edit goal anything")
        self.assertIn("none is open", str(caught.exception))


class CliWiringTests(unittest.TestCase):
    """`agent command <session> <name> [args]` — one terminal line, one unified answer."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.runs = Path(self.dir.name) / ".agent-runs"
        self.path = self.runs / "abc123def" / "session.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(session()), encoding="utf-8")
        self.addCleanup(self.dir.cleanup)

    def run_command(self, *argv) -> str:
        from ai_code_engineer.cli import main
        buffer = io.StringIO()
        stdout, sys.stdout = sys.stdout, buffer
        try:
            code = main(["command", *argv])
        finally:
            sys.stdout = stdout
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_a_stored_session_answers_status_by_its_file(self):
        out = self.run_command(str(self.path), "status")
        self.assertIn("Applied", out)
        self.assertIn("abc123def", out)

    def test_a_session_id_is_resolved_against_the_runs_folder(self):
        out = self.run_command("--runs", str(self.runs), "abc123", "changes")
        self.assertIn("app/calc.py", out)

    def test_the_alias_carries_a_command_with_its_own_arguments(self):
        from ai_code_engineer.cli import main
        buffer = io.StringIO()
        stdout, sys.stdout = sys.stdout, buffer
        try:
            code = main(["cmd", str(self.path), "diff", "app/new.py"])
        finally:
            sys.stdout = stdout
        self.assertEqual(code, 0)
        self.assertIn("count = 0", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
