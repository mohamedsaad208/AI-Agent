from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doubles import Sentinel
from ai_code_engineer.webapp import controller
from ai_code_engineer import labels
from ai_code_engineer import permissions
from ai_code_engineer import policy
from ai_code_engineer import service_runner
from ai_code_engineer.labels import policy_line
from ai_code_engineer.errors import PolicyError


class TestExecutionExperience(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.app_dir = Path(self.temp_dir.name)
        self.repo = self.app_dir / "my_project"
        self.repo.mkdir()
        (self.repo / "package.json").write_text(
            json.dumps({"scripts": {"start": "node index.js", "test": "jest", "build": "webpack"}}),
            encoding="utf-8"
        )
        self.c = controller.AgentController(self.app_dir)
        self.c.repo = str(self.repo)
        # The policy table answers ASK for any command that is not one of the project's own recipes, and
        # every command this suite runs is one of those. The dialog is not what these tests are about, so
        # it is answered the way `test_controller.py` answers it; `TestPolicyAtTheButton` is where the
        # ask itself is asserted, both ways.
        self.c.confirm = lambda *args, **kwargs: True

    def tearDown(self):
        service_runner.GLOBAL_SERVICES.stop_all()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_run_status_explanations(self):
        # 1. Valid project
        status = self.c.run_status_info()
        self.assertTrue(status["canRun"])
        self.assertTrue(status["canRunApp"])
        self.assertEqual(status["reasons"], [])

        # 2. No project selected
        self.c.repo = ""
        no_proj_status = self.c.run_status_info()
        self.assertFalse(no_proj_status["canRun"])
        self.assertTrue(any(r["code"] == "no_project" for r in no_proj_status["reasons"]))
        self.assertIn("No project selected", no_proj_status["disabledMessage"])

        # 3. Agent is busy
        self.c.repo = str(self.repo)
        self.c.busy = True
        busy_status = self.c.run_status_info()
        self.assertFalse(busy_status["canRun"])
        self.assertTrue(any(r["code"] == "agent_busy" for r in busy_status["reasons"]))
        self.assertIn("Agent is busy", busy_status["disabledMessage"])
        self.c.busy = False

        # 4. Waiting approval
        self.c.session = {"state": "WAITING_APPROVAL"}
        approval_status = self.c.run_status_info()
        self.assertFalse(approval_status["canRun"])
        self.assertTrue(any(r["code"] == "waiting_approval" for r in approval_status["reasons"]))
        self.assertIn("Changes waiting for approval", approval_status["disabledMessage"])

    def test_run_app_and_stop_lifecycle(self):
        cmd = f'"{sys.executable}" -u -c "import time; print(\'Service online on port 8088\', flush=True); time.sleep(10)"'
        res = self.c.run_app_service({"name": "web", "command": cmd, "port": 8088})
        self.assertEqual(res["name"], "web")
        time.sleep(0.5)

        # Snapshot should list running service
        snaps = service_runner.GLOBAL_SERVICES.all_snapshots()
        self.assertTrue(any(s["name"] == "web" for s in snaps))

        # Stop service
        stop_res = self.c.stop_app_service({"name": "web"})
        self.assertTrue(stop_res["stopped"])

    def test_run_build_action(self):
        cmd = f'"{sys.executable}" -u -c "import sys; print(\'Build step done\', flush=True); sys.exit(0)"'
        # Save command in runConfig
        service_runner.save_project_config(self.repo, {
            "version": 1,
            "build": {"command": cmd, "cwd": "."}
        })

        build_res = self.c.run_build_action()
        self.assertTrue(build_res["success"])
        self.assertEqual(build_res["exit_code"], 0)
        self.assertIn("Build step done", build_res["output"])
        self.assertEqual(self.c._last_job["type"], "build")

    def test_run_config_persistence_and_readiness(self):
        # 1. Readiness check
        readiness = self.c.get_project_readiness()
        self.assertIn("package.json", readiness["configs"])
        self.assertTrue(any(t["name"] == "Python" and t["available"] for t in readiness["tools"]))

        # 2. Save and retrieve config
        new_cfg = {
            "version": 1,
            "app": {"command": "npm start", "cwd": ".", "port": 4000},
            "test": {"command": "npm test", "cwd": "."},
            "build": {"command": "npm run build", "cwd": "."},
            "api_key_secret": "my-secret-key-123",
        }
        self.c.save_project_run_config({"config": new_cfg})

        saved = self.c.get_project_run_config()
        self.assertEqual(saved["app"]["port"], 4000)
        self.assertEqual(saved["api_key_secret"], "[redacted]")

    def test_fix_run_failure_loop_guard(self):
        diag = {
            "kind": "missing_dependency",
            "summary": "Missing dependency: 'pytest' not installed",
            "suggestion": "Run pip install pytest"
        }
        self.c._last_job = {"type": "tests", "diagnosis": diag, "output": "ModuleNotFoundError: pytest"}

        # First attempt initiates plan
        self.c.fix_run_failure({"diagnosis": diag})
        self.assertEqual(self.c._fix_round, 1)

        # Repeated identical attempt halts loop
        self.c.fix_run_failure({"diagnosis": diag})
        self.assertIn("Identical failure repeated", self.c.status)

    def test_custom_command_and_security(self):
        # 1. Directory traversal rejected (controller catches PolicyError and returns error dict)
        bad_res = self.c.run_custom_cmd({"command": "echo test", "subdir": "../../"})
        self.assertFalse(bad_res["success"])
        self.assertIn("stay within the project root", bad_res["output"])

        # 2. Execution success and output
        cmd = f'"{sys.executable}" -u -c "print(\'custom command ran\', flush=True)"'
        good_res = service_runner.run_custom_command(self.repo, cmd)
        self.assertTrue(good_res["success"])
        self.assertEqual(good_res["exit_code"], 0)
        self.assertIn("custom command ran", good_res["output"])

        # 3. Secret redaction in history recording
        secret_cmd = "curl -H 'Authorization: Bearer ghp_topsecrettoken1234567890123456' https://api.example.com"
        service_runner.record_custom_command(self.repo, secret_cmd)
        history_data = service_runner.get_custom_command_history(self.repo)
        self.assertTrue(len(history_data["history"]) > 0)
        self.assertNotIn("ghp_topsecrettoken1234567890123456", history_data["history"][0]["command"])
        self.assertIn("[redacted]", history_data["history"][0]["command"])

    def test_analyze_terminal_output(self):
        # Python ModuleNotFoundError
        output = "Traceback (most recent call last):\n  File 'app.py', line 2\nModuleNotFoundError: No module named 'requests'"
        diag = service_runner.analyze_terminal_output(output, command="python app.py", cwd=str(self.repo), exit_code=1)
        self.assertEqual(diag["kind"], "missing_dependency")
        self.assertTrue(any("requests" in f for f in diag["facts"]) or any("dependency" in f for f in diag["facts"]))
        self.assertTrue(len(diag["solutions"]) > 0)
        self.assertIn("pip install requests", diag["solutions"][0]["suggestion"])

        # Secret redaction in diagnosis
        secret_output = "Error connecting with token: ghp_1234567890abcdef1234567890abcdef12345678 to https://github.com"
        secret_diag = service_runner.analyze_terminal_output(secret_output, command="git fetch", exit_code=1)
        self.assertNotIn("ghp_1234567890abcdef1234567890abcdef12345678", secret_diag["evidence"])
        self.assertIn("[redacted]", secret_diag["evidence"])

    def test_auto_notes_lifecycle(self):
        from ai_code_engineer import memory as memory_store

        # 1. Initial state
        notes = memory_store.read_auto_notes(self.c.memory_dir, self.repo)
        self.assertTrue(notes["enabled"])

        # 2. Update from task with secrets and files
        task_info = {
            "title": "Build user login feature with password=secret_pwd_9999",
            "stack": "Python / Flask",
            "files": ["src/login.py"],
            "verification": "pytest tests/test_login.py passed (1 passed)",
        }
        updated = memory_store.update_auto_notes_from_task(
            self.c.memory_dir,
            self.repo,
            task_info,
        )
        self.assertIn("src/login.py", updated["components"])
        self.assertNotIn("secret_pwd_9999", json.dumps(updated))
        self.assertIn("pytest tests/test_login.py passed", updated["verification_results"])

        # 3. Controller toggle
        self.c.toggle_auto_notes({"enabled": False})
        re_read = memory_store.read_auto_notes(self.c.memory_dir, self.repo)
        self.assertFalse(re_read["enabled"])


class TestPolicyAtTheButton(unittest.TestCase):
    """#4, wired: the paths with no recipe list ask before they run, and a folder's DENY is not advice.

    Three things are asserted for each button, and all three matter. ASK reaches the operator *before*
    anything is executed or recorded; a refused ask leaves nothing behind — no process, no history row;
    and a folder that answered ALLOW stops the dialog appearing at all, because a guard that interrupts
    for what it permits is the guard people learn to click through.
    """

    COMMAND = f'"{sys.executable}" -u -c "print(\'policy probe ran\', flush=True)"'

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.app_dir = Path(self.temp_dir.name)
        self.repo = self.app_dir / "my_project"
        self.repo.mkdir()
        self.c = controller.AgentController(self.app_dir)
        self.c.repo = str(self.repo)
        self.asked = []
        self.c.confirm = lambda *args, **kwargs: (self.asked.append(args), True)[1]

    def tearDown(self):
        service_runner.GLOBAL_SERVICES.stop_all()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def history(self):
        return service_runner.get_custom_command_history(self.repo).get("history") or []

    def test_a_custom_command_is_asked_about_before_it_runs(self):
        res = self.c.run_custom_cmd({"command": self.COMMAND})
        self.assertEqual(len(self.asked), 1, "the operator was asked once")
        self.assertIn("policy probe ran", res["output"])
        self.assertTrue(res["success"])

    def test_a_refused_ask_runs_nothing_and_records_nothing(self):
        self.c.confirm = lambda *args, **kwargs: False
        res = self.c.run_custom_cmd({"command": self.COMMAND})
        self.assertFalse(res["success"])
        self.assertIn("recipe", res["output"], "the refusal says why, in the words the table speaks")
        self.assertEqual(self.history(), [], "a command that never ran is not history")

    def test_a_folder_that_denied_never_shows_the_dialog(self):
        permissions.declare(self.app_dir, self.repo, policy.EXECUTE_CUSTOM, policy.DENY)
        res = self.c.run_custom_cmd({"command": self.COMMAND})
        self.assertEqual(self.asked, [], "a DENY is an answer, not a question")
        self.assertFalse(res["success"])

    def test_a_folder_that_allowed_runs_without_asking(self):
        permissions.declare(self.app_dir, self.repo, policy.EXECUTE_CUSTOM, policy.ALLOW)
        res = self.c.run_custom_cmd({"command": self.COMMAND})
        self.assertEqual(self.asked, [])
        self.assertTrue(res["success"])

    def test_starting_a_service_from_the_projects_own_file_is_asked_about(self):
        (self.repo / ".ai_project.json").write_text(
            json.dumps({"app": {"command": self.COMMAND}}), encoding="utf-8")
        self.c.run_app_service({})
        self.assertEqual(len(self.asked), 1, "a command that comes from the repository is not pre-approved")
        self.c.confirm = lambda *args, **kwargs: False
        with self.assertRaises(PolicyError):
            self.c.run_app_service({})
        service_runner.GLOBAL_SERVICES.stop_all()

    def test_the_api_test_button_asks_before_it_opens_a_socket(self):
        # Answered "no", because a test that reaches the internet is a test that fails on a plane.
        self.c.confirm = lambda *args, **kwargs: (self.asked.append(args), False)[1]
        with self.assertRaises(PolicyError):
            self.c.run_api_test({"url": "https://8.8.8.8/x"})
        self.assertEqual(len(self.asked), 1, "the ask is the last thing before the request")

    def test_a_folder_that_denied_network_refuses_without_asking(self):
        permissions.declare(self.app_dir, self.repo, policy.NETWORK, policy.DENY)
        with self.assertRaises(PolicyError):
            self.c.run_api_test({"url": "https://api.example.com/x"})
        self.assertEqual(self.asked, [])

    def test_the_api_test_button_reaches_its_request_at_all(self):
        # It did not: the handler named `urllib` and the module never imported it, so every press of
        # that button died on a NameError. No test called it, which is how a shipped feature stays
        # broken. The port below is closed on purpose — the answer must be the tool's own report of a
        # failed request, not a crash from the middle of the standard library.
        permissions.declare(self.app_dir, self.repo, policy.NETWORK, policy.ALLOW)
        res = self.c.run_api_test({"url": "http://127.0.0.1:9/nothing-here"})
        self.assertFalse(res["ok"])
        self.assertEqual(self.asked, [], "the folder already answered this class")
        self.assertNotIn("NameError", json.dumps(res))


    def apply_session(self):
        return {"state": "WAITING_APPROVAL", "id": "a" * 32, "root": str(self.repo),
                "task": "fix the start script", "changes": [{"path": "package.json",
                                                             "content": "{}", "delete": False}],
                "proposal_hash": "deadbeef", "events": []}

    def test_a_proposal_that_edits_the_command_file_is_asked_about(self):
        self.c.session = self.apply_session()
        self.c.confirm = lambda *args, **kwargs: (self.asked.append(args), False)[1]
        self.c.apply()
        self.assertEqual(len(self.asked), 1, "an ASK put a question in front of the write")
        self.assertIn("package.json", self.asked[0][1])
        self.assertEqual(list(self.c._jobs), [], "the write did not happen behind the question")

    def test_a_folder_that_denied_that_write_never_reaches_the_dialog(self):
        permissions.declare(self.app_dir, self.repo, policy.WRITE_THAT_RUNS, policy.DENY)
        self.c.session = self.apply_session()
        self.c.apply()
        self.assertEqual(self.asked, [], "a DENY is an answer, not a question")
        self.assertIn("package.json", self.c.status)

    def test_a_write_that_runs_cannot_be_routed_round_auto_apply(self):
        # Auto-Apply takes a click out of a task, and this class is the one case where the click is the
        # whole point: the file says what the next run will execute. So the switch alone is not enough —
        # the write still has to be asked about, and it still has not happened behind that question.
        self.c.auto_apply = True
        self.c.session = self.apply_session()
        self.c.confirm = lambda *args, **kwargs: (self.asked.append(args), False)[1]
        self.c.apply()
        self.assertEqual(len(self.asked), 1, "the switch took the click out of the task, not out of this")
        self.assertIn("package.json", self.asked[0][1])
        self.assertEqual(list(self.c._jobs), [], "and nothing was written")

    def test_a_folder_that_allowed_this_class_does_not_hear_the_policy_sentence(self):
        permissions.declare(self.app_dir, self.repo, policy.WRITE_THAT_RUNS, policy.ALLOW)
        self.c.session = self.apply_session()
        self.c.confirm = lambda *args, **kwargs: (self.asked.append(args), False)[1]
        self.c.apply()
        said = policy_line(self.c.arabic, policy.WRITE_THAT_RUNS, policy.ASK, names="package.json")
        self.assertEqual(self.asked[0][1].count(said), 0,
                         "the ordinary apply dialog stays ordinary once the folder has answered")

    def test_a_row_answered_once_stops_being_asked(self):
        self.c.run_custom_cmd({"command": self.COMMAND})
        self.assertEqual(len(self.asked), 1, "asked once, as the table says")
        self.c.set_policy({"action": policy.EXECUTE_CUSTOM, "verdict": policy.ALLOW})
        self.asked.clear()
        res = self.c.run_custom_cmd({"command": self.COMMAND})
        self.assertEqual(self.asked, [], "the folder answered, so the question is spent")
        self.assertTrue(res["success"])
        block = self.c.policy_block()
        row = [r for r in block["rows"] if r["action"] == policy.EXECUTE_CUSTOM][0]
        self.assertEqual((row["verdict"], row["declared"]), (policy.ALLOW, True))

    def test_taking_the_answer_back_puts_the_question_back(self):
        self.c.set_policy({"action": policy.NETWORK, "verdict": policy.DENY})
        self.c.set_policy({"action": policy.NETWORK})
        block = self.c.policy_block()
        row = [r for r in block["rows"] if r["action"] == policy.NETWORK][0]
        self.assertEqual((row["verdict"], row["declared"]), (policy.ASK, False),
                         "a cleared row is the table speaking again, not a row missing")

    def test_the_block_names_every_class_and_speaks_the_tasks_language(self):
        block = self.c.policy_block()
        self.assertEqual([r["action"] for r in block["rows"]], list(policy.ACTIONS))
        self.assertEqual(sorted(block["words"]), sorted(policy.VERDICTS))
        self.assertTrue(block["heading"] and block["note"] and block["lift"])
        self.assertIn("What this folder answers", block["heading"],
                      "the words arrive written by the server, not drawn by the client")

    def test_an_action_the_table_does_not_know_is_not_a_row(self):
        with self.assertRaises(PolicyError):
            self.c.set_policy({"action": policy.READ, "verdict": policy.ALLOW})
        self.assertEqual(permissions.overrides(self.app_dir, self.repo), {})


class TheAddressAtTheButton(unittest.TestCase):
    """#14: one yes on the API-test button used to reach the cloud metadata address exactly as easily as
    it reached the dev server. The destination is now asked on its own, and the only answer that opens it
    is a row the folder wrote — a yes pressed on a dialog is not a rule.

    Nothing may aim a socket here, and nothing resolves a name either: the limit is asserted, because a
    gate that resolved `localhost` to find out where it goes would be sending the request it exists to
    think about first.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.app_dir = Path(self.temp_dir.name)
        self.repo = self.app_dir / "my_project"
        self.repo.mkdir()
        self.c = controller.AgentController(self.app_dir)
        self.c.repo = str(self.repo)
        self.asked = []
        self.c.confirm = lambda *args, **kwargs: (self.asked.append(args), True)[1]
        self.sentinel = Sentinel()
        stopper = patch("urllib.request.urlopen", self.sentinel)
        stopper.start()
        self.addCleanup(stopper.stop)

    def tearDown(self):
        service_runner.GLOBAL_SERVICES.stop_all()
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_the_metadata_address_is_refused_before_anything_is_aimed(self):
        with self.assertRaises(PolicyError) as caught:
            self.c.run_api_test({"url": "http://169.254.169.254/latest/meta-data/iam"})
        self.assertEqual(self.sentinel.aimed, [], "the refusal comes before the request is built")
        self.assertEqual(self.asked, [], "a dialog that cannot open the address is not asked about it")
        said = str(caught.exception)
        self.assertIn("169.254.169.254", said, "the refusal names the address it refused")
        self.assertIn(labels.address_words(False)[policy.LINK_LOCAL], said,
                      "and says what kind of place it is, in words the operator reads")

    def test_a_yes_on_the_ask_does_not_open_a_private_network(self):
        with self.assertRaises(PolicyError):
            self.c.run_api_test({"url": "http://10.0.0.9:3000/x"})
        self.assertEqual(self.asked, [])
        self.assertEqual(self.sentinel.aimed, [])

    def test_a_row_the_folder_wrote_does_open_it(self):
        permissions.declare(self.app_dir, self.repo, policy.NETWORK, policy.ALLOW)
        res = self.c.run_api_test({"url": "http://10.0.0.9:3000/x"})
        self.assertEqual(self.sentinel.aimed, ["http://10.0.0.9:3000/x"])
        self.assertEqual(self.asked, [], "allow is silence, at both gates")
        self.assertTrue(res["ok"])

    def test_an_address_form_the_gate_cannot_read_is_refused_with_its_form(self):
        for url in ("http://127.1/", "http://0177.0.0.1/", "http://2130706433/"):
            with self.assertRaises(PolicyError) as caught:
                self.c.run_api_test({"url": url})
            said = str(caught.exception)
            self.assertIn(url.split("//", 1)[1].rstrip("/"), said, url)
            self.assertIn(labels.address_words(False)[policy.UNKNOWN], said, url)
        self.assertEqual(self.sentinel.aimed, [], "none of them was tried")

    def test_loopback_and_a_public_numeric_address_keep_the_one_time_ask(self):
        for url in ("http://127.0.0.1:8080/", "http://localhost:8080/", "http://8.8.8.8/"):
            self.c.run_api_test({"url": url})
        self.assertEqual(len(self.asked), 3, "the escalation added no dialog to an address at home")
        self.assertEqual(len(self.sentinel.aimed), 3)

    def test_an_unresolved_hostname_needs_a_folder_rule_before_any_request(self):
        resolver = MagicMock(side_effect=AssertionError("the gate looked a name up"))
        self.addCleanup(setattr, socket, "getaddrinfo", socket.getaddrinfo)
        socket.getaddrinfo = resolver
        with self.assertRaises(PolicyError) as caught:
            self.c.run_api_test({"url": "http://db.internal:5432/"})
        resolver.assert_not_called()
        self.assertEqual(self.sentinel.aimed, [])
        self.assertIn("db.internal", str(caught.exception))
        permissions.declare(self.app_dir, self.repo, policy.NETWORK, policy.ALLOW)
        self.c.run_api_test({"url": "http://db.internal:5432/"})
        self.assertEqual(self.sentinel.aimed, ["http://db.internal:5432/"])

    def test_the_refusal_reads_the_same_in_the_scripted_preview(self):
        """`--fake` is where this design gets reviewed, so it answers the destination question with the
        same function and the same sentence — and a declared row opens it there too."""
        from ai_code_engineer.webapp.fake import FakeController
        fake = FakeController()
        with self.assertRaises(PolicyError) as caught:
            fake.action("api_test", {"url": "http://db.internal/"}, lambda event: None)
        self.assertIn("db.internal", str(caught.exception))
        fake.policy_over[policy.NETWORK] = policy.ALLOW
        self.assertEqual(fake.action("api_test", {"url": "http://db.internal/"},
                                     lambda event: None),
                         {"status": 200, "headers": {}, "body": "OK"})


if __name__ == "__main__":
    unittest.main()
