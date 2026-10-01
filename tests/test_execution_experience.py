from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from ai_code_engineer.webapp import controller
from ai_code_engineer import service_runner


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


if __name__ == "__main__":
    unittest.main()
