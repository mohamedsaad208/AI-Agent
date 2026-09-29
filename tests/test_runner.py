"""The runner must stay on argv, bounded in time and output, and honest about proof."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import runner
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.labels import friendly_error


PASSING = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(2, 1 + 1)\n"
FAILING = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self)\n        self.assertEqual(3, 1 + 1)\n"


class MoreThanOneProjectTests(unittest.TestCase):
    """A folder can be one project, a reactor of nine, or a monorepo with no shared build at all.

    The scan used to look only at the opened folder's own children, so `backend/pom.xml` was
    invisible and the window said "no command was detected" about a project that builds perfectly.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "monorepo"
        self.addCleanup(self.temp.cleanup)
        self.module("backend/auth-service", PASSING)
        self.module("backend/product-service", FAILING)
        (self.root / "node_modules" / "left-pad").mkdir(parents=True)
        (self.root / "node_modules" / "left-pad" / "package.json").write_text("{}", encoding="utf-8")
        (self.root / "backend" / "auth-service" / "target" / "classes").mkdir(parents=True)
        (self.root / "backend" / "auth-service" / "target" / "classes"
         / "package.json").write_text("{}", encoding="utf-8")

    def module(self, relative, test_body):
        folder = self.root.joinpath(*relative.split("/"))
        (folder / "tests").mkdir(parents=True)
        (folder / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (folder / "tests" / "test_one.py").write_text(test_body, encoding="utf-8")
        return folder

    def test_each_module_is_found_and_the_opened_folder_stays_first(self):
        self.assertEqual(runner.projects(self.root),
                         [".", "backend/auth-service", "backend/product-service"])
        # `backend/` is a container, not a project: it holds no build file of its own, and offering
        # it would be a button that can only answer "no command here".
        self.assertNotIn("backend", runner.projects(self.root))

    def test_a_vendored_dependency_and_a_build_output_are_not_offerings(self):
        paths = runner.projects(self.root)
        self.assertFalse(any("node_modules" in path for path in paths), paths)
        self.assertFalse(any(path.endswith("target") for path in paths), paths)

    def test_a_target_is_a_folder_of_this_project_and_nothing_else(self):
        self.assertEqual(runner.project_folder(self.root, "backend/auth-service"),
                         (self.root / "backend" / "auth-service").resolve())
        self.assertEqual(runner.project_folder(self.root, "."), self.root.resolve())
        for wanted in ("../elsewhere", "/etc", "C:/Windows", "backend/../..", ""):
            with self.subTest(wanted=wanted):
                if wanted == "":
                    self.assertEqual(runner.project_folder(self.root, ""), self.root.resolve())
                    continue
                with self.assertRaises(PolicyError):
                    runner.project_folder(self.root, wanted)

    def test_a_folder_that_vanished_is_refused_rather_than_raising_a_bare_filenotfound(self):
        with self.assertRaises(PolicyError):
            runner.project_folder(self.root, "backend/not-here")

    def test_targets_are_the_modules_with_a_command_and_are_labelled_by_path(self):
        with patch("ai_code_engineer.runner.available", return_value=["python-unittest"]):
            rows = runner.targets(self.root)
        self.assertEqual([row["path"] for row in rows],
                         ["backend/auth-service", "backend/product-service"])
        self.assertEqual([row["label"] for row in rows],
                         ["backend/auth-service", "backend/product-service"])
        self.assertEqual(rows[0]["recipes"], ["python-unittest"])

    def test_two_modules_of_one_project_are_two_different_answers(self):
        """The proof of the whole feature: the same recipe in two folders of one tree, and each run
        reports the folder it ran in rather than the tree it was pointed at."""
        with patch("ai_code_engineer.runner.available", return_value=["python-unittest"]):
            rows = runner.targets(self.root)
        for row in rows:
            result = runner.run(self.root, "python-unittest", timeout=120, target=row["path"])
            self.assertEqual(result["target"], row["path"])
            expected = "passed" if "auth" in row["path"] else "failed"
            self.assertEqual(result["status"], expected, row["path"])
            self.assertIn("in auth-service" if "auth" in row["path"] else "in product-service",
                          runner.summarize(result))

    def test_the_root_of_a_folder_that_is_only_a_container_has_no_command(self):
        with patch("ai_code_engineer.runner.available", return_value=["python-unittest"]):
            self.assertEqual(runner.detect(self.root), [])

    def test_scanning_ten_modules_asks_the_path_once_per_tool(self):
        """`available()` resolves each executable against PATH; nine modules used to pay that nine
        times, which was half a second on the reactor this was written for."""
        calls = []
        real = runner.available

        def counted():
            calls.append(1)
            return real()

        with patch("ai_code_engineer.runner.available", side_effect=counted):
            runner.targets(self.root)
        self.assertEqual(len(calls), 1)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        (self.root / "tests").mkdir(parents=True)
        self.addCleanup(self.temp.cleanup)

    def recipe_test(self, body):
        (self.root / "tests" / "test_sample.py").write_text(body, encoding="utf-8")

    def test_detect_matches_project_markers(self):
        self.recipe_test(PASSING)
        self.assertIn("python-unittest", runner.detect(self.root))
        (self.root / "pom.xml").write_text("<project/>", encoding="utf-8")
        with patch("ai_code_engineer.runner.available",
                   return_value=list(runner.RECIPES)):
            detected = runner.detect(self.root)
        self.assertIn("maven-test", detected)
        self.assertIn("python-unittest", detected)

    def test_passing_suite_is_reported_as_passed_with_proof(self):
        self.recipe_test(PASSING)
        result = runner.run(self.root, "python-unittest", timeout=120)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["tests_observed"])
        self.assertEqual(result["exit_code"], 0)

    def test_failing_suite_keeps_output_for_the_model(self):
        self.recipe_test(FAILING)
        result = runner.run(self.root, "python-unittest", timeout=120)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 1)
        self.assertTrue(any("SyntaxError" in row for row in result["failures"]),
                        msg=result["output"][-400:])
        self.assertIn("SyntaxError", result["tail"])

    def test_green_summary_lines_are_not_reported_as_failures(self):
        # Surefire prints "Failures: 0, Errors: 0" on every green run. Those lines match
        # the failure words, and feeding them to a model sends it hunting a real problem.
        text = ("[INFO] Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 7.7 s"
                " -- in com.example.auth.AuthApplicationTests\n"
                "[INFO] Tests run: 8, Failures: 0, Errors: 0, Skipped: 0\n"
                "[INFO] BUILD SUCCESS\n")
        self.assertEqual(runner._failures(text), [])
        broken = text.replace("Failures: 0", "Failures: 2")
        self.assertTrue(any("Failures: 2" in row for row in runner._failures(broken)),
                        msg=runner._failures(broken))

    def test_green_command_without_tests_is_unverified(self):
        result = runner.run(self.root, "python-unittest", timeout=120)
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["tests_observed"])

    def test_timeout_is_bounded_and_reported(self):
        sleeper = {"command": [sys.executable, "-c", "import time; time.sleep(60)"],
                   "markers": [], "label": "Sleeper", "test_counts": None}
        with patch.dict(runner.RECIPES, {"sleep": sleeper}):
            result = runner.run(self.root, "sleep", timeout=2)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["timed_out"])

    def test_unknown_recipe_is_refused_and_says_why(self):
        with self.assertRaises(PolicyError):
            runner.run(self.root, "rm -rf /", timeout=5)
        # The reason has to survive the trip to the status line; a plain ValueError would not.
        self.assertIn("rm -rf", friendly_error(PolicyError("Unknown recipe: rm -rf /")))

    def test_missing_tool_reports_unavailable_without_spawning(self):
        with patch("ai_code_engineer.runner.shutil.which", return_value=None), \
             patch("ai_code_engineer.runner.subprocess.Popen") as popen:
            result = runner.run(self.root, "maven-test", timeout=5)
        self.assertEqual(result["status"], "unavailable")
        popen.assert_not_called()

    def test_children_never_receive_a_shell_or_secrets(self):
        self.recipe_test(PASSING)
        real_popen = runner.subprocess.Popen
        seen = []

        def spy(*args, **kwargs):
            seen.append((args, kwargs))
            return real_popen(*args, **kwargs)

        os.environ["OPENROUTER_API_KEY"] = "synthetic-secret"
        self.addCleanup(os.environ.pop, "OPENROUTER_API_KEY", None)
        with patch("ai_code_engineer.runner.subprocess.Popen", side_effect=spy):
            runner.run(self.root, "python-unittest", timeout=120)
        self.assertEqual(len(seen), 1)
        args, kwargs = seen[0]
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)
        self.assertFalse(any(character in part for part in args[0]
                             for character in "&|<>^%"))
        self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
        self.assertNotIn("PYTHONPATH", kwargs["env"])

    def test_output_is_capped(self):
        loud = {"command": [sys.executable, "-c", "print('x' * 300000)"],
                "markers": [], "label": "Loud", "test_counts": None}
        with patch.dict(runner.RECIPES, {"loud": loud}):
            result = runner.run(self.root, "loud", timeout=60)
        self.assertLessEqual(len(result["output"]), runner.MAX_OUTPUT_CHARS)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["tail"]), runner.MODEL_OUTPUT_CHARS)

    def test_machine_report_decides_even_when_the_build_lies(self):
        """A console line is printable; a report file is written by the test framework."""
        writer = (
            "import pathlib;"
            "pathlib.Path('target/surefire-reports').mkdir(parents=True, exist_ok=True);"
            "pathlib.Path('target/surefire-reports/TEST-com.example.AuthTest.xml').write_text("
            "'<testsuite tests=\"8\" failures=\"1\" errors=\"0\" skipped=\"0\"/>')"
        )
        recipe = {"command": [sys.executable, "-c", writer], "markers": [], "label": "Fake maven",
                  "test_counts": None, "reports": ("target/surefire-reports/TEST-*.xml",),
                  "proof_source": "surefire XML"}
        with patch.dict(runner.RECIPES, {"fake-maven": recipe}):
            result = runner.run(self.root, "fake-maven", timeout=60)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 0)  # the command itself was happy
        self.assertEqual(result["proof"]["tests"], 8)
        self.assertEqual(result["proof"]["failures"], 1)
        self.assertEqual(result["proof"]["source"], "surefire XML")
        self.assertIn("8 tests, 1 failed", runner.summarize(result))

    def test_green_report_with_no_test_inside_is_not_a_pass(self):
        writer = ("import pathlib;"
                  "pathlib.Path('target/surefire-reports').mkdir(parents=True, exist_ok=True);"
                  "pathlib.Path('target/surefire-reports/TEST-empty.xml').write_text("
                  "'<testsuite tests=\"0\" failures=\"0\" errors=\"0\" skipped=\"0\"/>')")
        recipe = {"command": [sys.executable, "-c", writer], "markers": [], "label": "Empty maven",
                  "test_counts": None, "reports": ("target/surefire-reports/TEST-*.xml",)}
        with patch.dict(runner.RECIPES, {"empty-maven": recipe}):
            result = runner.run(self.root, "empty-maven", timeout=60)
        self.assertEqual(result["status"], "unverified")

    def test_report_left_by_an_earlier_build_is_ignored(self):
        stale = self.root / "target" / "surefire-reports"
        stale.mkdir(parents=True)
        report = stale / "TEST-old.xml"
        report.write_text('<testsuite tests="99" failures="0" errors="0" skipped="0"/>',
                          encoding="utf-8")
        past = time.time() - 3600
        os.utime(report, (past, past))
        self.assertEqual(runner.fresh_reports(self.root, ("target/surefire-reports/TEST-*.xml",),
                                              time.time(), None), [])
        # Without a fresh report the console text is the only evidence left.
        self.assertEqual(runner.decide_status(False, 0, None, True, True), "passed")

    def test_counts_come_from_testcase_children_when_attributes_are_missing(self):
        folder = self.root / "build" / "test-results" / "test"
        folder.mkdir(parents=True)
        (folder / "TEST-a.xml").write_text(
            '<testsuite><testcase name="one"/><testcase name="two"><failure message="x"/>'
            '</testcase><testcase name="three"><skipped/></testcase></testsuite>',
            encoding="utf-8")
        found = runner.fresh_reports(self.root, ("build/test-results/**/TEST-*.xml",),
                                     time.time(), None)
        self.assertEqual(runner.report_counts(found),
                         {"tests": 3, "failures": 1, "errors": 0, "skipped": 1, "reports": 1})

    def test_unreadable_report_falls_back_to_console_rules(self):
        self.assertIsNone(runner.report_counts([]))
        broken = self.root / "TEST-broken.xml"
        broken.write_text("<testsuite tests=", encoding="utf-8")
        self.assertIsNone(runner.report_counts([broken]))
        self.assertEqual(runner.decide_status(False, 0, None, False, True), "unverified")

    def test_summary_is_one_short_line(self):
        self.recipe_test(PASSING)
        line = runner.summarize(runner.run(self.root, "python-unittest", timeout=120))
        self.assertTrue(line.startswith("Python unittest: passed"))
        self.assertLess(len(line), 90)


class ToolchainRecipeTests(unittest.TestCase):
    """uv / pnpm / cargo / go: same argv, scrubbed env and proof rules as the rest."""

    CARGO_GREEN = ("running 5 tests\ntest auth::tests::token ... ok\n"
                   "test result: ok. 5 passed; 0 failed; 0 ignored; 0 measured; "
                   "0 filtered out; finished in 0.12s\n")
    GO_GREEN = ("ok  \tgithub.com/acme/auth\t0.412s\n"
                "ok  \tgithub.com/acme/errors\t0.088s\n")
    GO_NO_TESTS = "?   \tgithub.com/acme/cmd\t[no test files]\n"
    GO_FAILED = ("--- FAIL: TestToken (0.00s)\n    auth_test.go:31: want 200, got 401\n"
                 "FAIL\tgithub.com/acme/auth\t0.204s\nFAIL\n")
    VITEST_GREEN = " Test Files  2 passed (2)\n      Tests  12 passed (12)\n"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.addCleanup(self.temp.cleanup)

    def pretend(self, recipe, output, exit_code=0):
        class Process:
            def __init__(self):
                self.returncode = exit_code
                self.stdout = io.BytesIO(output.encode("utf-8"))

            def wait(self, timeout=None):
                return self.returncode

        os.environ["OPENROUTER_API_KEY"] = "synthetic-secret"
        self.addCleanup(os.environ.pop, "OPENROUTER_API_KEY", None)
        with patch("ai_code_engineer.runner.shutil.which", return_value="/usr/local/bin/tool"), \
             patch("ai_code_engineer.runner.subprocess.Popen", return_value=Process()) as popen:
            return runner.run(self.root, recipe, timeout=30), popen.call_args

    def test_markers_select_the_recipe(self):
        cases = {"uv.lock": "uv-pytest", "pnpm-lock.yaml": "pnpm-test",
                 "Cargo.toml": "cargo-test", "go.mod": "go-test"}
        for marker, expected in cases.items():
            with self.subTest(marker=marker):
                (self.root / marker).write_text("x", encoding="utf-8")
                with patch("ai_code_engineer.runner.available",
                           return_value=list(runner.RECIPES)):
                    detected = runner.detect(self.root)
                self.assertIn(expected, detected)
                (self.root / marker).unlink()

    def test_uv_wins_over_the_interpreter_pytest(self):
        """A locked uv project must not be tested with whatever Python starts the app."""
        (self.root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (self.root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        with patch("ai_code_engineer.runner.available", return_value=list(runner.RECIPES)):
            detected = runner.detect(self.root)
        self.assertLess(detected.index("uv-pytest"), detected.index("python-pytest"))

    def test_commands_are_fixed_argv_without_the_project_text(self):
        expected = {"uv-pytest": ["uv", "run", "pytest", "-q"],
                    "pnpm-test": ["pnpm", "test"],
                    "cargo-test": ["cargo", "test"],
                    "go-test": ["go", "test", "./..."]}
        for recipe, argv in expected.items():
            self.assertEqual(runner.RECIPES[recipe]["command"], argv)

    def test_env_scrubbing_still_applies_to_the_new_recipes(self):
        result, call = self.pretend("go-test", self.GO_GREEN)
        args, kwargs = call
        self.assertEqual(args[0][:2], ["/usr/local/bin/tool", "test"])
        self.assertNotIn("shell", kwargs)
        self.assertTrue(set(kwargs["env"]) <= set(runner.ENV_KEYS))
        self.assertNotIn("OPENROUTER_API_KEY", kwargs["env"])
        self.assertNotIn("PYTHONPATH", kwargs["env"])
        self.assertEqual(result["status"], "passed")

    def test_pytest_under_uv_still_writes_a_machine_report(self):
        _result, call = self.pretend("uv-pytest", "5 passed in 0.41s\n")
        argv = call[0][0]
        self.assertEqual(argv[1:4], ["run", "pytest", "-q"])
        self.assertIn("--junitxml", argv)

    def test_cargo_counts_its_own_summary(self):
        result = self.pretend("cargo-test", self.CARGO_GREEN)[0]
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["tests_observed"])
        self.assertEqual(runner._failures(self.CARGO_GREEN), [])

    def test_cargo_failures_reach_the_model(self):
        output = self.CARGO_GREEN.replace("test result: ok. 5 passed; 0 failed",
                                          "test result: FAILED. 4 passed; 1 failed")
        result = self.pretend("cargo-test", output, exit_code=101)[0]
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("1 failed" in row for row in result["failures"]),
                        msg=result["failures"])

    def test_crate_without_tests_is_unverified_not_passed(self):
        result = self.pretend("cargo-test", "running 0 tests\ntest result: ok. 0 passed; "
                                            "0 failed; 0 ignored\n", exit_code=0)[0]
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["tests_observed"])

    # ---------------- the count a workspace prints more than once ----------------
    def test_every_summary_line_in_a_workspace_counts(self):
        """One crate, one summary. A workspace of two printed 20 and 4, and the old reader took
        the first match and reported 20 — a number that quietly disagreed with the run."""
        output = ("test result: ok. 20 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
                  "test result: ok. 4 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out\n")
        proof = runner.console_proof(runner.RECIPES["cargo-test"], output)
        self.assertEqual((proof["tests"], proof["failures"], proof["skipped"]), (24, 0, 1))
        self.assertEqual(proof["source"], "cargo's own summary")
        self.assertEqual(runner.summarize({"recipe": "cargo-test", "label": "Cargo test", "status": "passed",
                                           "exit_code": 0, "seconds": 1.0, "tests_observed": True,
                                           "proof": proof, "truncated": False, "timed_out": False}),
                         "Cargo test: passed (24 tests, 0 failed, 0 errors (cargo's own summary), 1.0s)")

    def test_a_run_that_printed_no_summary_proves_nothing(self):
        # Returning 0 here would read as "green, and there were no tests" — the honest answer is
        # that there is no count, which leaves the exit code and expects_proof to decide.
        self.assertIsNone(runner.console_proof(runner.RECIPES["cargo-test"], "Compiling\ncollecting\n"))
        # A recipe that declares no console proof is never read for one, whatever its output says.
        self.assertIsNone(runner.console_proof(runner.RECIPES["go-test"], self.CARGO_GREEN))

    def test_the_console_count_survives_into_the_result(self):
        result = self.pretend("cargo-test", self.CARGO_GREEN)[0]
        self.assertEqual(result["proof"]["tests"], 5)
        self.assertEqual(result["proof"]["source"], "cargo's own summary")

    def test_go_counts_packages_because_it_prints_no_total(self):
        result = self.pretend("go-test", self.GO_GREEN)[0]
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["tests_observed"])
        # A package path that happens to contain "errors" is not a failure.
        self.assertEqual(runner._failures(self.GO_GREEN), [])

    def test_go_green_exit_with_no_test_file_is_unverified(self):
        result = self.pretend("go-test", self.GO_NO_TESTS)[0]
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["tests_observed"])

    def test_go_failure_lines_survive(self):
        result = self.pretend("go-test", self.GO_FAILED, exit_code=1)[0]
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("FAIL: TestToken" in row for row in result["failures"]),
                        msg=result["failures"])

    def test_node_and_pnpm_output_both_count_tests(self):
        self.assertEqual(self.pretend("pnpm-test", self.VITEST_GREEN)[0]["status"], "passed")
        self.assertEqual(self.pretend("pnpm-test", "pass 12\nfail 0\ncancelled 0\n")[0]["status"],
                         "passed")
        self.assertEqual(self.pretend("pnpm-test", "# tests 0\n")[0]["status"], "unverified")

    def test_the_compilers_keep_the_long_timeout(self):
        """Cargo is not a script suite: `cargo test` is a compile the first time anything changes.

        It used to sit with the fast recipes at 600 s because the long timeout was written as a JVM
        rule, so a cold Rust build got killed for being slow and the window reported a timeout where
        the truth was ten minutes of codegen.
        """
        for recipe in ("maven-test", "gradle-test", "cargo-test"):
            self.assertEqual(runner.timeout_for(recipe), runner.LONG_TIMEOUT, recipe)
        for recipe in ("uv-pytest", "pnpm-test", "node-test", "go-test", "python-unittest"):
            self.assertEqual(runner.timeout_for(recipe), runner.DEFAULT_TIMEOUT, recipe)

    def test_recipes_without_an_installed_tool_never_spawn(self):
        for recipe in ("uv-pytest", "pnpm-test", "cargo-test", "go-test"):
            with self.subTest(recipe=recipe):
                with patch("ai_code_engineer.runner.shutil.which", return_value=None), \
                     patch("ai_code_engineer.runner.subprocess.Popen") as popen:
                    result = runner.run(self.root, recipe, timeout=5)
                self.assertEqual(result["status"], "unavailable")
                popen.assert_not_called()

    def test_available_hides_a_tool_that_is_not_installed(self):
        real_which = runner.shutil.which

        def which(program):
            return None if program in ("uv", "pnpm", "cargo", "go") else real_which(program)

        with patch("ai_code_engineer.runner.shutil.which", side_effect=which):
            found = runner.available()
        for recipe in ("uv-pytest", "pnpm-test", "cargo-test", "go-test"):
            self.assertNotIn(recipe, found)


class StreamingTests(unittest.TestCase):
    """A long build has to talk while it runs, not only after it finishes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir(parents=True)
        self.addCleanup(self.temp.cleanup)

    def script_recipe(self, body, name="stream"):
        recipe = {"command": [sys.executable, "-c", body], "markers": [], "label": "Stream",
                  "test_counts": None}
        return patch.dict(runner.RECIPES, {name: recipe}), name

    def test_lines_arrive_while_the_child_is_still_running(self):
        gate = self.root / "gate.txt"
        body = ("import pathlib, sys, time\n"
                "print('one', flush=True)\n"
                "deadline = time.monotonic() + 30\n"
                "while not pathlib.Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
                "    time.sleep(0.05)\n"
                "print('two', flush=True)\n")
        recipe = {"command": [sys.executable, "-c", body, str(gate)], "markers": [],
                  "label": "Handshake", "test_counts": None}
        seen = []

        def progress(line):
            seen.append(line)
            if line == "one":
                gate.write_text("go", encoding="utf-8")

        started = time.monotonic()
        with patch.dict(runner.RECIPES, {"handshake": recipe}):
            runner.run(self.root, "handshake", timeout=90, progress=progress)
        self.assertEqual(seen[1:], ["one", "two"])
        # Blocked on the gate, so the child could only have finished after "one" was read;
        # a run that streamed nothing would have taken the full 30-second fallback.
        self.assertLess(time.monotonic() - started, 20)

    def test_a_carriage_return_redraw_is_one_line(self):
        body = ("import sys\n"
                "sys.stdout.write('  0% building\\r  92% building\\r  done\\n')\n"
                "sys.stdout.flush()\n")
        holder, name = self.script_recipe(body)
        seen = []
        with holder:
            result = runner.run(self.root, name, timeout=60, progress=seen.append)
        # The redraws collapse into what the terminal would finally show, so the Activity
        # view gets one entry per visible line instead of one per repaint. seen[0] is the
        # "Running …" header, which echoes the script text itself.
        self.assertEqual(seen[1:], ["  done"])
        self.assertEqual(len(result["output"].splitlines()), 1)

    def test_a_flood_is_streamed_for_a_while_then_counted(self):
        body = ("for i in range(5000):\n    print('line %d' % i)\n")
        holder, name = self.script_recipe(body)
        seen = []
        with holder:
            result = runner.run(self.root, name, timeout=120, progress=seen.append)
        self.assertLessEqual(len(seen), runner.STREAM_BUDGET + 2)   # header + budget + note
        self.assertIn("more lines", seen[-1])
        self.assertIn("4600", seen[-1])
        self.assertTrue(result["output"].startswith("line 0\n"))
        self.assertTrue(result["output"].rstrip().endswith("line 4999"))

    def test_a_killed_run_reports_the_timeout_and_keeps_what_it_printed(self):
        body = ("import time\n"
                "print('before', flush=True)\n"
                "time.sleep(30)\n")
        holder, name = self.script_recipe(body)
        with holder:
            result = runner.run(self.root, name, timeout=3)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["timed_out"])
        self.assertIn("before", result["output"])

    def test_one_gigantic_line_still_stays_inside_the_cap(self):
        body = "import sys\nsys.stdout.write('x' * 300000 + '\\n')\n"
        holder, name = self.script_recipe(body)
        seen = []
        with holder:
            result = runner.run(self.root, name, timeout=60, progress=seen.append)
        self.assertLessEqual(len(result["output"]), runner.MAX_OUTPUT_CHARS)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(max(len(line) for line in seen), 501)


class ProcessDisciplineTests(unittest.TestCase):
    """A build that goes wrong still has to leave this machine the way it found it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir(parents=True)
        self.addCleanup(self.temp.cleanup)

    def script_recipe(self, body, name="discipline"):
        recipe = {"command": [sys.executable, "-c", body], "markers": [], "label": "Discipline",
                  "test_counts": None}
        return patch.dict(runner.RECIPES, {name: recipe}), name

    def test_a_run_that_raises_with_a_child_alive_stops_that_child(self):
        holder, name = self.script_recipe("import time\nprint('up', flush=True)\ntime.sleep(60)\n")
        captured = {}
        real_collect = runner._collect

        def exploding_progress(line):
            raise RuntimeError("the status sink went away mid-build")

        def exploding_collect(process, progress, deadline):
            captured["process"] = process
            return real_collect(process, exploding_progress, deadline)

        with holder, patch.object(runner, "_collect", exploding_collect):
            with self.assertRaises(RuntimeError):
                runner.run(self.root, name, timeout=60)
        child = captured["process"]
        self.assertIsNotNone(child.returncode, "the build was left running with its pipe held")
        self.assertNotEqual(child.returncode, 0, "a killed child must not look like a green run")

    def test_the_kill_falls_back_to_the_child_when_taskkill_cannot_run(self):
        class Child:
            pid = 4321

            def __init__(self):
                self.killed = False

            def kill(self):
                self.killed = True

        child = Child()
        for error in (FileNotFoundError("taskkill"), subprocess.TimeoutExpired("taskkill", 30)):
            with self.subTest(error=type(error).__name__):
                child.killed = False
                with patch.object(runner.os, "name", "nt"), \
                        patch.object(runner.subprocess, "run", side_effect=error):
                    runner.kill_tree(child)
                self.assertTrue(child.killed, "the tree kill was skipped, so stop what we can")

    def test_a_kill_that_cannot_find_the_process_does_not_raise(self):
        """This runs on the way out of a timeout; an exception here loses a finished build."""
        import signal

        class Gone:
            pid = 4321

            def kill(self):
                raise ProcessLookupError

        for platform in ("nt", "posix"):
            # Both branches are walked here, so the signal constant is borrowed for the
            # platform that is not in front of us.
            with self.subTest(os=platform), patch.object(runner.os, "name", platform), \
                    patch.object(signal, "SIGKILL", create=True, new=9), \
                    patch.object(runner.subprocess, "run", side_effect=OSError), \
                    patch.object(runner.os, "getpgid", create=True, return_value=4321), \
                    patch.object(runner.os, "killpg", create=True, side_effect=ProcessLookupError):
                runner.kill_tree(Gone())

    def test_a_child_the_os_will_not_reap_still_hands_over_its_output(self):
        class Wedged:
            pid = 4321
            returncode = None

            def __init__(self):
                self.stdout = io.BytesIO(b"first line\n")

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("child", timeout)

            def kill(self):
                pass

        with patch.object(runner, "kill_tree") as killed:
            text, timed_out, dropped = runner._collect(Wedged(), lambda _line: None,
                                                       time.monotonic() + 5)
        self.assertEqual(text, "first line\n")
        self.assertTrue(timed_out)
        self.assertEqual(dropped, 0)
        self.assertGreaterEqual(killed.call_count, 1)

    def test_an_oversized_report_is_not_read_as_proof(self):
        report = self.root / "TEST-big.xml"
        report.write_text('<testsuite name="big" tests="3" failures="0" errors="0">' +
                          " " * 400 + "</testsuite>", encoding="utf-8")
        self.assertEqual(runner.report_counts([report])["tests"], 3)
        with patch.object(runner, "MAX_REPORT_BYTES", 100):
            self.assertIsNone(runner.report_counts([report]))


if __name__ == "__main__":
    unittest.main()
