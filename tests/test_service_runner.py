import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from ai_code_engineer import service_runner


class TestServiceRunnerUtils(unittest.TestCase):
    def test_safe_split_command(self):
        parts = service_runner.safe_split_command("python -m unittest discover")
        self.assertEqual(parts, ["python", "-m", "unittest", "discover"])

        # Windows paths with spaces
        parts2 = service_runner.safe_split_command('python "C:\\My Project\\app.py" --port 8080')
        self.assertIn("C:\\My Project\\app.py", parts2[1])

    def test_diagnose_failure(self):
        # Port in use
        diag1 = service_runner.diagnose_failure("Error: listen EADDRINUSE: address already in use :::3000", exit_code=1)
        self.assertEqual(diag1["kind"], "port_in_use")
        self.assertIn("3000", diag1["summary"])

        # Missing module
        diag2 = service_runner.diagnose_failure("ModuleNotFoundError: No module named 'fastapi'", exit_code=1)
        self.assertEqual(diag2["kind"], "missing_dependency")
        self.assertIn("fastapi", diag2["summary"])

        # Missing command
        diag3 = service_runner.diagnose_failure("'mvn' is not recognized as an internal or external command", exit_code=1)
        self.assertEqual(diag3["kind"], "missing_tool")
        self.assertIn("mvn", diag3["summary"])

        # Syntax error
        diag4 = service_runner.diagnose_failure("SyntaxError: invalid syntax (line 42)", exit_code=1)
        self.assertEqual(diag4["kind"], "syntax_error")


class TestProjectConfigAndReadiness(unittest.TestCase):
    def test_infer_and_save_config(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            repo = Path(td)
            # Create a mock package.json
            (repo / "package.json").write_text('{"scripts": {"start": "node server.js", "test": "jest", "build": "webpack"}}', encoding="utf-8")
            
            config = service_runner.read_project_config(repo)
            self.assertEqual(config["app"]["command"], "npm start")
            self.assertEqual(config["test"]["command"], "npm test")
            self.assertEqual(config["build"]["command"], "npm run build")

            # Save modified config with secret
            config["app"]["port"] = 5000
            config["secret_token"] = "super-secret-key-12345"
            service_runner.save_project_config(repo, config)

            # Read back - secret should be masked
            loaded = service_runner.read_project_config(repo)
            self.assertEqual(loaded["app"]["port"], 5000)
            self.assertEqual(loaded["secret_token"], "[redacted]")

    def test_check_project_readiness(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            repo = Path(td)
            (repo / ".env.example").write_text("DB_HOST=localhost\nAPI_KEY=xyz\n", encoding="utf-8")
            (repo / ".env").write_text("DB_HOST=localhost\n", encoding="utf-8")

            res = service_runner.check_project_readiness(repo)
            self.assertIn("API_KEY", res["env"]["missing_keys"])
            self.assertTrue(any("API_KEY" in rec for rec in res["recommendations"]))


class TestServiceProcessAndBoundedJob(unittest.TestCase):
    def test_execute_bounded_job(self):
        repo = Path.cwd()
        cmd = [sys.executable, "-u", "-c", "import sys; print('Hello test output', flush=True); sys.exit(0)"]
        res = service_runner.execute_bounded_job(cmd, repo, timeout=10)
        self.assertTrue(res["success"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("Hello test output", res["output"])

    def test_service_process_lifecycle(self):
        repo = Path.cwd()
        cmd = [sys.executable, "-u", "-c", "import time; print('Ready and waiting', flush=True); time.sleep(10)"]
        lines = []
        svc = service_runner.ServiceProcess("svc-1", "test-srv", cmd, repo, on_output=lines.append)
        svc.start()
        for _ in range(30):
            if any("Ready and waiting" in l for l in svc.get_lines()):
                break
            time.sleep(0.1)

        snap = svc.snapshot()
        self.assertIn(snap["status"], ("starting", "running"))
        self.assertTrue(any("Ready and waiting" in l for l in svc.get_lines()))

        # Stop cleanly
        svc.stop()
        time.sleep(0.3)
        snap_after = svc.snapshot()
        self.assertEqual(snap_after["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
