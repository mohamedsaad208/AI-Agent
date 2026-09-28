"""Verification is where this program is allowed to say a build ran: what is pinned here is
the suffix gate that has to read file names the way the workspace does, the scrubbed child it
spawns, and the rule that nothing counts as a pass without proof."""
from pathlib import Path
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import engine, runner, verification
from ai_code_engineer.errors import MissingFileError, PolicyError
from ai_code_engineer.labels import friendly_error
from ai_code_engineer.workspace import Workspace, digest


IMAGE = "python@sha256:" + "a" * 64
DOCKER = "/usr/local/bin/docker"
# The one text the container is started with; it is a constant, so changing it is a decision.
PROLOGUE = 'cp -R /input/. /work/ && exec "$@"'


class Process:
    """A child docker_check only ever talks to through Popen: fixed output, already finished."""

    def __init__(self, output=b"", exit_code=0):
        self.stdout = io.BytesIO(output)
        self.returncode = exit_code
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.killed = True
        if not self.returncode:
            self.returncode = -9          # a killed build must never look like a green one


class HangingProcess(Process):
    """The container that never answers, which is the case every cleanup rule has to survive."""

    def __init__(self):
        super().__init__(b"starting the image\n")
        self.returncode = None
        self.asks = 0

    def wait(self, timeout=None):
        self.asks += 1
        if self.asks == 1:
            raise subprocess.TimeoutExpired("docker", timeout)
        return self.returncode


class StubWorkspace:
    """snapshot() only ever touches files() and read(), so a stub keeps its two caps testable
    without creating 2001 files or twenty megabytes of real project on disk."""

    def __init__(self, names, content="x"):
        self.names = names
        self.content = content
        self.limits = []

    def files(self, limit=2000):
        self.limits.append(limit)
        return list(self.names)

    def read(self, name):
        return {"path": name, "sha256": digest(self.content.encode("utf-8")),
                "content": self.content}


class StaticCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "repo"
        self.root.mkdir()
        self.ws = Workspace(self.root)

    def change(self, name, content):
        """One change row as apply_proposal leaves it: bytes on disk, hash of those bytes."""
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
        return {"path": name, "after_hash": self.ws.read(name)["sha256"]}

    def statuses(self, *rows):
        session = {"schema": 1, "id": "s", "root": str(self.root), "task": "t", "summary": "x",
                   "checks": [], "events": [], "state": "APPLIED_UNVERIFIED",
                   "changes": list(rows)}
        return {row["path"]: row["status"] for row in verification.static_check(session)}

    def test_a_removal_is_checked_by_its_absence(self):
        """D31: a delete row has no content to parse, so its pass condition is that the file is gone.
        Reading it would raise for the one reason the row exists."""
        (self.root / "gone.py").write_text("answer = 1\n", encoding="utf-8")
        row = {"path": "gone.py", "before": "answer = 1\n", "after": None,
               "before_hash": self.ws.read("gone.py")["sha256"], "after_hash": None,
               "delete": True}
        (self.root / "gone.py").unlink()
        self.assertEqual(self.statuses(row), {"gone.py": "not_applicable"})
        (self.root / "gone.py").write_text("answer = 1\n", encoding="utf-8")
        with self.assertRaises(PolicyError):
            self.statuses(row)

    def test_a_python_change_is_parsed_before_it_can_be_called_a_check(self):
        rows = [self.change("good.py", "answer = 1\n"), self.change("broken.py", "def broken(:\n")]
        self.assertEqual(self.statuses(*rows), {"good.py": "passed", "broken.py": "failed"})

    def test_an_uppercase_python_suffix_is_still_parsed(self):
        # Punch-list 12: the workspace admits a suffix in any case, so a gate comparing the raw
        # suffix let APP.PY reach disk unchecked. Reverting the fold turns "failed" into
        # "not_applicable" here, which reads to the user as a clean run.
        rows = [self.change("APP.PY", "def broken(:\n"), self.change("Runner2.PY", "ok = 1\n")]
        self.assertEqual(self.statuses(*rows), {"APP.PY": "failed", "Runner2.PY": "passed"})

    def test_the_json_gate_reads_an_uppercase_suffix_too(self):
        rows = [self.change("data.JSON", "{not json"), self.change("config.json", '{"a": 1}')]
        self.assertEqual(self.statuses(*rows), {"data.JSON": "failed", "config.json": "passed"})

    def test_a_suffix_no_parser_covers_is_not_applicable_rather_than_passed(self):
        # "not_applicable" is the honest word: a session of nothing but .java changes has had
        # exactly one thing done to it — it was opened — and a pass would claim more.
        rows = [self.change("App.java", "class App {"), self.change("notes.md", "# hi\n"),
                self.change("build.gradle", "apply plugin:")]
        self.assertEqual(self.statuses(*rows),
                         {"App.java": "not_applicable", "notes.md": "not_applicable",
                          "build.gradle": "not_applicable"})

    def test_a_file_that_no_longer_matches_the_approved_hash_aborts_the_check(self):
        row = self.change("app.py", "answer = 1\n")
        (self.root / "app.py").write_text("answer = 999\n", encoding="utf-8")
        with self.assertRaises(PolicyError) as caught:
            self.statuses(row)
        self.assertIn("no longer match the approved proposal", str(caught.exception))
        # A plain ValueError would be replaced by "Could not complete the operation." in both
        # windows, which is how a hash mismatch stops being an actionable message.
        self.assertIn("no longer match", friendly_error(caught.exception))

    def test_a_deleted_applied_file_aborts_the_check_instead_of_looking_clean(self):
        row = self.change("temp.py", "x = 1\n")
        (self.root / "temp.py").unlink()
        with self.assertRaises(MissingFileError):
            self.statuses(row)


class RecipePreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sandbox = Path(temp.name)

    def test_only_the_built_in_recipes_are_ever_considered(self):
        self.assertEqual(set(verification.RECIPES),
                         {"python-unittest", "maven-test", "gradle-test"})
        for argv in verification.RECIPES.values():
            self.assertIsInstance(argv, list, "argv only: one string would be a shell")

    def test_an_unknown_recipe_is_refused_and_the_reason_survives(self):
        with patch("ai_code_engineer.verification.shutil.which", return_value=DOCKER), \
                patch("ai_code_engineer.verification.subprocess.Popen") as popen:
            with self.assertRaises(PolicyError) as caught:
                verification.docker_check(self.sandbox, "rm -rf /", IMAGE)
        popen.assert_not_called()
        self.assertIn("built-in recipe", friendly_error(caught.exception))

    def test_an_image_that_is_not_pinned_by_digest_is_refused(self):
        # A tag is a moving pointer; a digest is the only thing that names the image that was
        # actually built, which is what "preloaded and approved" has to mean.
        for image in ("python:3.11", "latest", "python@sha256:" + "z" * 64,
                      "python@sha256:" + "a" * 63, "python@sha256:" + "a" * 64 + ":3.11", ""):
            with self.subTest(image=repr(image)), \
                    patch("ai_code_engineer.verification.shutil.which", return_value=DOCKER), \
                    patch("ai_code_engineer.verification.subprocess.Popen") as popen:
                with self.assertRaises(PolicyError):
                    verification.docker_check(self.sandbox, "python-unittest", image)
                popen.assert_not_called()

    def test_a_machine_without_docker_reports_blocked_and_never_spawns(self):
        with patch("ai_code_engineer.verification.shutil.which", return_value=None), \
                patch("ai_code_engineer.verification.subprocess.Popen") as popen:
            result = verification.docker_check(self.sandbox, "python-unittest", IMAGE)
        popen.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Host execution is disabled", result["reason"])


class DockerSpawnTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sandbox = Path(temp.name)

    def spawn(self, recipe, process, image=IMAGE, timeout=5, cleanup_error=None):
        """The whole docker call with the binary replaced by a recording, so nothing here can
        reach a daemon: the suite stays offline on a machine with or without Docker."""
        with patch("ai_code_engineer.verification.shutil.which", return_value=DOCKER), \
                patch("ai_code_engineer.verification.subprocess.Popen",
                      return_value=process) as popen, \
                patch("ai_code_engineer.verification.subprocess.run",
                      side_effect=cleanup_error) as cleanup:
            result = verification.docker_check(self.sandbox, recipe, image, timeout=timeout)
        return result, popen.call_args, cleanup

    def test_the_container_is_sealed_and_the_project_is_only_ever_mounted_read_only(self):
        _result, call, _cleanup = self.spawn("python-unittest", Process(b"Ran 3 tests\n"))
        argv = call[0][0]
        self.assertEqual(argv[0], DOCKER)
        for flag in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--user=65534:65534",
                     "--pids-limit=128", "--entrypoint=/bin/sh"):
            self.assertIn(flag, argv)
        mount = [part for part in argv if part.startswith("type=bind")]
        self.assertEqual(mount, [f"type=bind,source={self.sandbox},target=/input,readonly"])
        self.assertIn("/tmp:rw,nosuid,size=128m", argv)
        self.assertTrue(any(part.startswith("/work:rw,exec,nosuid") for part in argv),
                        "the tests need a writable place that is not the mounted project")

    def test_the_recipe_is_handed_over_as_argv_behind_a_fixed_shell_prologue(self):
        for recipe in sorted(verification.RECIPES):
            with self.subTest(recipe=recipe):
                _result, call, _cleanup = self.spawn(recipe, Process(b"Ran 3 tests\n"))
                argv = call[0][0]
                self.assertEqual(argv[argv.index("-c") + 1], PROLOGUE)
                self.assertEqual(argv[argv.index("agent") + 1:], verification.RECIPES[recipe])

    def test_the_child_is_given_a_scrubbed_environment_and_not_the_applications_own(self):
        os.environ["OPENROUTER_API_KEY"] = "synthetic-secret"
        self.addCleanup(os.environ.pop, "OPENROUTER_API_KEY", None)
        _result, call, _cleanup = self.spawn("python-unittest", Process(b"Ran 3 tests\n"))
        env = call[1]["env"]
        self.assertIsNot(env, os.environ, "an inherited map is the leak this is here to stop")
        self.assertTrue(set(env) <= set(runner.ENV_KEYS), sorted(set(env) - set(runner.ENV_KEYS)))
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertFalse([name for name in env
                          if any(word in name.upper() for word in
                                 ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))],
                        "no name a provider key could be hiding under")

    def test_the_container_cleanup_call_keeps_the_same_scrubbed_environment(self):
        # Every subprocess in this tree gets the scrubbed environment and no console handle, the
        # cleanup included: `docker rm -f` would otherwise inherit the provider keys the run was
        # kept away from.
        _result, call, cleanup = self.spawn("python-unittest", Process(b"Ran 3 tests\n"))
        argv, kwargs = cleanup.call_args
        self.assertIsInstance(kwargs.get("env"), dict, argv)
        self.assertTrue(set(kwargs["env"]) <= set(runner.ENV_KEYS), argv)
        self.assertIs(kwargs.get("stdin"), subprocess.DEVNULL)

    def test_a_cleanup_that_fails_still_reports_the_run(self):
        """The container is gone with the process; a refused `rm` must not eat the result."""
        result, _call, _cleanup = self.spawn("python-unittest", Process(b"Ran 3 tests\n"),
                                             cleanup_error=OSError("docker refused"))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["tests_observed"], True)

    def test_the_child_cannot_be_left_reading_the_console(self):
        # Without DEVNULL a child that reads stdin hangs on a Windows console in QuickEdit
        # select mode, and docker_check's own timeout is never reached.
        _result, call, _cleanup = self.spawn("python-unittest", Process(b"Ran 3 tests\n"))
        self.assertIs(call[1]["stdin"], subprocess.DEVNULL)
        self.assertIs(call[1]["stdout"], subprocess.PIPE)
        self.assertIs(call[1]["stderr"], subprocess.STDOUT)

    def test_the_container_is_removed_even_after_a_clean_run(self):
        _result, call, cleanup = self.spawn("python-unittest", Process(b"Ran 3 tests\n"))
        name = call[0][0][call[0][0].index("--name") + 1]
        self.assertTrue(name.startswith("ai-agent-"), "one uuid per run: no colliding names")
        self.assertEqual(cleanup.call_args[0][0], [DOCKER, "rm", "-f", name])


class DockerOutcomeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sandbox = Path(temp.name)

    def outcome(self, recipe, child):
        with patch("ai_code_engineer.verification.shutil.which", return_value=DOCKER), \
                patch("ai_code_engineer.verification.subprocess.Popen", return_value=child), \
                patch("ai_code_engineer.verification.subprocess.run"):
            return verification.docker_check(self.sandbox, recipe, IMAGE, timeout=5)

    def test_a_counted_test_and_a_zero_exit_is_the_only_way_to_pass(self):
        result = self.outcome("python-unittest", Process(b"Ran 3 tests in 0.112s\n\nOK\n"))
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["tests_observed"])

    def test_a_green_command_that_counted_no_test_is_unverified(self):
        result = self.outcome("python-unittest", Process(b"BUILD SUCCESSFUL in 2s\n"))
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(result["tests_observed"])

    def test_a_nonzero_exit_is_a_failure_even_with_tests_counted(self):
        result = self.outcome("python-unittest",
                              Process(b"Ran 3 tests\nFAILED (failures=1)\n", exit_code=1))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 1)

    def test_the_maven_console_count_is_proof_only_for_the_maven_recipe(self):
        maven = self.outcome("maven-test", Process(b"Tests run: 8, Failures: 0\n"))
        self.assertEqual(maven["status"], "passed")
        # A count printed in the shape another tool uses is not evidence about this recipe.
        wrong = self.outcome("python-unittest", Process(b"Tests run: 8, Failures: 0\n"))
        self.assertEqual(wrong["status"], "unverified")

    def test_a_gradle_run_cannot_currently_be_proven_from_its_console(self):
        # CODE-REVIEW-2026-09-26 P1, still open: the Gradle branch parses no console counts at
        # all, so a green Gradle build stays "unverified" for ever and README's
        # --recipe gradle-test path can never reach CHECKS_PASSED. Pinned as it stands so the
        # dead end cannot quietly turn into a "passed" before someone decides Gradle's proof.
        result = self.outcome("gradle-test", Process(b"Tests run: 8, Failures: 0\n"
                                                     b"BUILD SUCCESSFUL\n"))
        self.assertFalse(result["tests_observed"])
        self.assertEqual(result["status"], "unverified")

    def test_a_timed_out_container_is_killed_removed_and_reported_as_blocked(self):
        hung = HangingProcess()
        result = self.outcome("python-unittest", hung)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["timeout"])
        self.assertTrue(hung.killed, "a container we gave up on is still running until we stop it")
        self.assertEqual(result["exit_code"], -9)

    def test_an_output_flood_is_capped_and_the_verdict_becomes_blocked(self):
        # The reader kills the child the moment the buffer is full: a build that shouts is not
        # evidence, and truncated text must never be allowed to say "passed".
        result = self.outcome("python-unittest", Process(b"Ran 5 tests\n" + b"y" * 70000))
        self.assertLessEqual(len(result["output"]), 64000)
        self.assertTrue(result["output_limited"])
        self.assertEqual(result["status"], "blocked")


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.target = self.base / "sandbox"
        self.target.mkdir()

    def test_the_snapshot_copies_the_policy_visible_set_and_hashes_what_it_copied(self):
        # The hash map is the only thing verify() compares afterwards, so a file that landed
        # somewhere else or in a different form has to show up as stale rather than as clean.
        (self.root / "app.py").write_bytes(b"answer = 1\n")
        (self.root / "src").mkdir()
        (self.root / "src/util.py").write_bytes(b"def helper():\n    return 1\n")
        (self.root / "image.png").write_bytes(b"\x89PNG\r\n")
        (self.root / "token.json").write_text('{"aws": "SHOULD_NOT_APPEAR"}', encoding="utf-8")
        hashes = verification.snapshot(Workspace(self.root), self.target)
        self.assertEqual(sorted(hashes), ["app.py", "src/util.py"])
        self.assertEqual((self.target / "app.py").read_bytes(), b"answer = 1\n")
        self.assertEqual((self.target / "src/util.py").read_bytes(),
                         (self.root / "src/util.py").read_bytes())
        self.assertEqual(hashes["app.py"], digest(b"answer = 1\n"))
        self.assertEqual([p.name for p in self.target.iterdir()], ["app.py", "src"])

    def test_one_file_too_big_for_a_prompt_does_not_veto_the_whole_snapshot(self):
        # 128 KiB is what may reach a model's context, not what a sandbox may be given. A repo
        # with a generated lock file over that ceiling — or one that is not UTF-8 — is ordinary,
        # and refusing to verify it at all was the sandbox calling itself unusable.
        (self.root / "app.py").write_bytes(b"answer = 1\n")
        (self.root / "package-lock.json").write_bytes(b'{"packages": ' + b"x" * 200_000 + b"}")
        (self.root / "legacy.properties").write_bytes("greeting=caf\xe9\n".encode("latin-1"))
        hashes = verification.snapshot(Workspace(self.root), self.target)
        self.assertEqual(sorted(hashes), ["app.py"])
        self.assertEqual([p.name for p in self.target.iterdir()], ["app.py"])
        self.assertEqual(verification._fingerprints(Workspace(self.root)), hashes,
                         "the drift check must skip exactly what the snapshot skipped")

    def test_a_snapshot_stops_at_its_file_cap_before_copying_anything(self):
        # Asking for one more file than the cap is what makes the guard cheap; a container
        # handed half a tree would still be asked for a verdict about all of it.
        ws = StubWorkspace([f"f{n}.py" for n in range(2001)])
        with self.assertRaises(PolicyError) as caught:
            verification.snapshot(ws, self.target)
        self.assertIn("2000 files", str(caught.exception))
        self.assertEqual(ws.limits, [2001], "the cap is only real if it asks for one more")
        self.assertEqual(list(self.target.iterdir()), [])

    def test_a_snapshot_stops_before_copying_more_than_its_byte_budget(self):
        ws = StubWorkspace(["big.py"], content="x" * 20_000_001)
        with self.assertRaises(PolicyError) as caught:
            verification.snapshot(ws, self.target)
        self.assertIn("20 MB", str(caught.exception))
        self.assertEqual(list(self.target.iterdir()), [])


class VerifySessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "app.py").write_bytes(b"answer = 2\n")
        self.ws = Workspace(self.root)

    def session(self, state="APPLIED_UNVERIFIED", content=None):
        """A session file the way the engine writes one, holding the hash of what is on disk."""
        if content is not None:
            (self.root / "app.py").write_text(content, encoding="utf-8")
        body = (self.root / "app.py").read_text(encoding="utf-8")
        row = {"path": "app.py", "before": None, "before_hash": None, "after": body,
               "after_hash": self.ws.read("app.py")["sha256"]}
        record = {"schema": 1, "id": "abc", "root": str(self.root), "task": "Make answer 2",
                  "summary": "Update answer", "checks": ["one"], "changes": [row],
                  "events": [], "state": state}
        record["proposal_hash"] = engine.proposal_hash(record)
        path = self.base / (state + "-" + str(len(list(self.base.glob('*.json')))) + ".json")
        engine.atomic_json(path, record)
        return path

    def test_verification_never_runs_on_a_proposal_that_was_not_applied(self):
        with self.assertRaises(PolicyError) as caught:
            verification.verify(self.session("WAITING_APPROVAL"), "python-unittest", IMAGE)
        self.assertIn("Apply the reviewed proposal", str(caught.exception))

    def test_static_checks_without_a_recipe_are_reported_as_unverified(self):
        path = self.session()
        result = verification.verify(path)
        self.assertEqual(result["status"], "unverified")
        self.assertEqual(result["static"], [{"path": "app.py", "status": "passed"}])
        self.assertIn("build/tests have not run", result["reason"])
        self.assertEqual(engine.load_session(path)["state"], "VERIFICATION_BLOCKED",
                         "a static-only answer must not leave the session looking checked")

    def test_a_recipe_without_an_image_digest_is_blocked(self):
        result = verification.verify(self.session(), "python-unittest")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("digest", result["reason"])

    def test_a_failing_static_check_stops_before_the_sandbox_is_consulted(self):
        # No point paying for a container to run code that does not parse, and running it would
        # hand the user a verdict about a file other than the one they approved.
        path = self.session(content="def broken(:\n")
        with patch("ai_code_engineer.verification.docker_check") as sandbox:
            result = verification.verify(path, "python-unittest", IMAGE)
        sandbox.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["static"], [{"path": "app.py", "status": "failed"}])
        self.assertEqual(engine.load_session(path)["state"], "VERIFICATION_FAILED")

    def test_the_sandbox_is_handed_a_snapshot_and_never_the_project(self):
        # The temp folder is gone by the time verify() returns, so the only way to see what the
        # container was really given is to look while it is still open.
        path = self.session()
        seen = {}

        def inspect(source, recipe, image, timeout=180):
            folder = Path(source)
            seen["name"] = folder.name
            seen["in_project"] = self.ws.root in folder.parents
            seen["copy"] = (folder / "app.py").read_bytes()
            return {"status": "passed"}

        with patch("ai_code_engineer.verification.docker_check", side_effect=inspect):
            verification.verify(path, "python-unittest", IMAGE)
        self.assertTrue(seen["name"].startswith("agent-verify-"))
        self.assertFalse(seen["in_project"], "the real tree is never mounted")
        self.assertEqual(seen["copy"], b"answer = 2\n")

    def test_a_passing_sandbox_run_over_an_untouched_tree_is_recorded_as_checks_passed(self):
        path = self.session()
        with patch("ai_code_engineer.verification.docker_check",
                   return_value={"status": "passed", "output": "Ran 3 tests"}):
            result = verification.verify(path, "python-unittest", IMAGE)
        self.assertEqual(result["status"], "passed")
        stored = engine.load_session(path)
        self.assertEqual(stored["state"], "CHECKS_PASSED")
        self.assertEqual(stored["verification"]["sandbox"]["status"], "passed")
        self.assertIn("verification", [entry["kind"] for entry in stored["events"]],
                      "the event log is what a later turn reads back")

    def test_a_file_the_run_changed_invalidates_the_verdict(self):
        # The before/after hashes are the only thing standing between a container that rewrote
        # the project and a green tick describing files nobody reviewed.
        path = self.session()

        def tamper(source, recipe, image, timeout=180):
            (self.root / "app.py").write_text("answer = 999\n", encoding="utf-8")
            return {"status": "passed"}

        with patch("ai_code_engineer.verification.docker_check", side_effect=tamper):
            result = verification.verify(path, "python-unittest", IMAGE)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(engine.load_session(path)["state"], "VERIFICATION_BLOCKED")


if __name__ == "__main__":
    unittest.main()
