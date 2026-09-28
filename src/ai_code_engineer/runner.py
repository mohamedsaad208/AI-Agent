"""Run one allowlisted build/test recipe inside the approved project folder.

Commands are constant argv lists resolved with shutil.which; no shell, no model or
user text reaches the command line. This reduces blast radius but is not a sandbox:
OS-level isolation (verification.docker_check) stays the stronger boundary.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque

from .errors import PolicyError

MAX_OUTPUT_CHARS = 200_000
MODEL_OUTPUT_CHARS = 12_000
# A JUnit report is evidence, not an archive. Anything bigger is not read as proof.
MAX_REPORT_BYTES = 2_000_000
DEFAULT_TIMEOUT = 600
LONG_TIMEOUT = 1500
# A build can print a hundred thousand lines; a person reads a few hundred.
STREAM_BUDGET = 400
# After a tree kill the child still has buffered output to hand over.
POST_KILL_GRACE = 5.0

# Env entries a compiler toolchain needs. Anything else (tokens, cloud keys, proxies)
# stays out of the child process.
ENV_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "JAVA_HOME", "MAVEN_HOME",
            "GRADLE_HOME", "M2_HOME", "NODE_PATH", "LANG", "LC_ALL", "PYTHONIOENCODING",
            "GOPATH", "GOROOT", "CARGO_HOME", "RUSTUP_HOME")

RECIPES: dict[str, dict] = {
    "maven-test": {
        "command": ["mvn", "-B", "test"],
        "markers": ["pom.xml"],
        "label": "Maven test",
        "test_counts": r"Tests run:\s*(\d+)",
        "reports": ("target/surefire-reports/TEST-*.xml", "*/target/surefire-reports/TEST-*.xml"),
        "proof_source": "surefire XML",
    },
    "maven-compile": {
        "command": ["mvn", "-B", "-DskipTests", "compile"],
        "markers": ["pom.xml"],
        "label": "Maven compile",
        "test_counts": None,
    },
    "gradle-test": {
        "command": ["gradle", "--no-daemon", "test"],
        "markers": ["build.gradle", "build.gradle.kts"],
        "label": "Gradle test",
        "test_counts": None,
        "reports": ("build/test-results/**/TEST-*.xml", "*/build/test-results/**/TEST-*.xml"),
        "proof_source": "Gradle JUnit XML",
    },
    "python-unittest": {
        "command": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        "markers": [],
        "test_folder": True,
        "label": "Python unittest",
        "test_counts": r"Ran (\d+) tests?",
    },
    "uv-pytest": {
        "command": ["uv", "run", "pytest", "-q"],
        "markers": ["pyproject.toml", "uv.lock"],
        "label": "uv pytest",
        "test_counts": r"(\d+) passed",
        "junit_arg": "--junitxml",
        "proof_source": "pytest JUnit XML",
    },
    "python-pytest": {
        "command": [sys.executable, "-m", "pytest", "-q"],
        "markers": ["pytest.ini", "pyproject.toml"],
        "label": "pytest",
        "test_counts": r"(\d+) passed",
        "junit_arg": "--junitxml",
        "proof_source": "pytest JUnit XML",
    },
    "node-test": {
        "command": ["node", "--test"],
        "markers": ["package.json"],
        "label": "Node test runner",
        "test_counts": r"pass\s+(\d+)",
    },
    "npm-test": {
        "command": ["npm", "test"],
        "markers": ["package.json"],
        "label": "npm test",
        "test_counts": r"pass\s+(\d+)",
    },
    "pnpm-test": {
        "command": ["pnpm", "test"],
        "markers": ["pnpm-lock.yaml"],
        "label": "pnpm test",
        # pnpm prints vitest's "Tests  12 passed" or the node runner's "pass 12".
        "test_counts": r"(?i)(?:\bpass\b|\btests\b)\s+(\d+)",
    },
    "cargo-test": {
        "command": ["cargo", "test"],
        "markers": ["cargo.toml"],
        "label": "Cargo test",
        "test_counts": r"test result: \w+\.\s+(\d+) passed",
    },
    "go-test": {
        "command": ["go", "test", "./..."],
        "markers": ["go.mod"],
        "label": "Go test",
        # go test prints no total: one "ok  <package>" line per package that ran.
        "test_counts": None,
        "test_ran": r"(?m)^ok\s+\S+",
    },
}


def timeout_for(recipe: str) -> int:
    """JVM builds need minutes even warm; script suites usually do not."""
    return LONG_TIMEOUT if recipe.startswith(("maven", "gradle")) else DEFAULT_TIMEOUT


def available() -> list[str]:
    """Recipes whose executable exists on this machine."""
    found = []
    for name, recipe in RECIPES.items():
        program = recipe["command"][0]
        if program == sys.executable or shutil.which(program):
            found.append(name)
    return found


def detect(repo: Path) -> list[str]:
    """Recipes matching the project's build files, in RECIPES order."""
    files = {path.name.lower() for path in repo.glob("*")}
    folders = {path.name.lower() for path in repo.iterdir() if path.is_dir()}
    result = []
    for name in available():
        recipe = RECIPES[name]
        markers = set(recipe["markers"])
        if recipe.get("test_folder") and "tests" in folders:
            markers.add("tests")
        if markers & (files | folders):
            result.append(name)
    return result


def child_env() -> dict:
    return {key: value for key in ENV_KEYS
            if (value := os.environ.get(key)) is not None}


def kill_tree(process: subprocess.Popen) -> None:
    """Stop the child and its tree. This never raises: it runs on the way out of a timeout, and an
    exception here would take a build that already finished with it."""
    if os.name == "nt":
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
                           creationflags=flags)
            return
        except (OSError, subprocess.SubprocessError):
            pass                       # taskkill absent or wedged: fall back to the direct child
    else:
        import signal
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, OSError):
            pass
    try:
        process.kill()
    except (ProcessLookupError, OSError):
        pass                           # already gone; there is nothing left for us to stop


# Deliberately bounded alternatives: an open-ended "[a-z]+error" pattern backtracks
# catastrophically on the minified single-line output real build tools produce.
FAILURE_PATTERN = re.compile(
    r"(?i)(\berrors?\b|\bfail(ed|ure|s)?\b|exception|traceback|cannot find symbol|unresolved|"
    r"build failure|(syntax|name|type|value|key|import|attribute|index|assertion|runtime|null|"
    r"conversion|parsing|resolution)error)")


# Surefire and friends print "Tests run: 8, Failures: 0, Errors: 0" on every green run.
# Those lines carry the failure words but are not failures, and feeding them to a model
# as evidence sends it looking for a problem that is not there.
CLEAN_COUNTS = re.compile(r"(?i)(?:failures?|errors?)\s*[:=]\s*0\b")
DIRTY_COUNTS = re.compile(r"(?i)(?:failures?|errors?)\s*[:=]\s*[1-9]")
# Cargo prints "test result: ok. 5 passed; 0 failed" and Go prints "ok <package>", where
# the package path can itself contain the word "error" — so those lines are read by prefix.
CLEAN_SUMMARY = re.compile(r"(?i)^(?:test result: ok\.|ok\s+\S)")


def _clean(line: str) -> bool:
    if CLEAN_SUMMARY.match(line):
        return True
    if CLEAN_COUNTS.search(line) and not DIRTY_COUNTS.search(line):
        return True
    return line.endswith((": OK", "... ok", "...OK", "skipped"))


def _failures(text: str) -> list[str]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip()[:500]
        if not stripped or _clean(stripped):
            continue
        if FAILURE_PATTERN.search(stripped):
            rows.append(stripped)
    return rows[:40]


# A console line is something the project can print; a report file is something the test
# framework wrote after running the tests. Where a machine-readable report exists it is
# the authority, and only a report fresh enough to belong to this run counts.
MTIME_GRACE_SECONDS = 2


def _suite_counts(suite: ET.Element) -> tuple[int, int, int, int]:
    """(tests, failures, errors, skipped) for one <testsuite>, attributes or children."""
    def number(name: str, default: int = 0) -> int:
        raw = suite.get(name)
        try:
            return int(float(raw)) if raw not in (None, "") else default
        except ValueError:
            return default
    cases = list(suite.iter("testcase"))
    tests = number("tests", len(cases))
    failures = number("failures", sum(1 for c in cases if c.find("failure") is not None))
    errors = number("errors", sum(1 for c in cases if c.find("error") is not None))
    skipped = number("skipped", sum(1 for c in cases if c.find("skipped") is not None))
    return tests, failures, errors, skipped


def report_counts(files: list[Path]) -> dict | None:
    """Aggregate JUnit-family report files; None when none of them is readable."""
    tests = failures = errors = skipped = parsed = 0
    for path in files:
        try:
            # A report is evidence, not an archive: a test that dumps its stack into a testcase
            # element can produce hundreds of megabytes, and this reads it on the UI's thread.
            if path.stat().st_size > MAX_REPORT_BYTES:
                continue
            root = ET.fromstring(path.read_bytes())
        except (OSError, ET.ParseError):
            continue
        parsed += 1
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        if not suites:
            suites = [root]
        for suite in suites:
            count = _suite_counts(suite)
            tests += count[0]
            failures += count[1]
            errors += count[2]
            skipped += count[3]
    if not parsed:
        return None
    return {"tests": tests, "failures": failures, "errors": errors, "skipped": skipped,
            "reports": parsed}


def fresh_reports(root: Path, patterns: tuple, started_wall: float,
                  junit: Path | None) -> list[Path]:
    """Report files this run can be held responsible for, not last build's leftovers."""
    candidates = [junit] if junit is not None else [
        found for pattern in patterns for found in root.glob(pattern)]
    kept = []
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_mtime >= started_wall - MTIME_GRACE_SECONDS:
                kept.append(path)
        except OSError:
            continue
    return kept


def decide_status(timed_out: bool, exit_code: int, proof: dict | None,
                  console_tests: bool, expects_tests: bool) -> str:
    """A green command that proved no test ran is not a pass."""
    if timed_out:
        return "timeout"
    if proof is not None:
        # The report outranks the exit code: a build that swallows failures still failed.
        if proof["failures"] or proof["errors"]:
            return "failed"
        return "passed" if proof["tests"] - proof["skipped"] > 0 else "unverified"
    if exit_code != 0:
        return "failed"
    if expects_tests and not console_tests:
        return "unverified"
    return "passed"


def tests_ran(recipe_entry: dict, output: str, counts: list) -> bool:
    """Did a test actually execute? Some tools print a total, others only one line each."""
    watcher = recipe_entry.get("test_ran")
    if watcher:
        return bool(re.search(watcher, output))
    return any(int(value) > 0 for value in counts)


def expects_proof(recipe_entry: dict) -> bool:
    """True for recipes whose whole purpose is running tests, so a silent green run is suspect."""
    return bool(recipe_entry["test_counts"]) or bool(recipe_entry.get("test_ran"))


def _visible(chunk: str) -> str:
    """One line as the terminal would show it: a spinner's carriage returns collapse.

    `npm` and `cargo` redraw the same line dozens of a second, and the pipe is opened
    without newline translation precisely so a `\r` stays inside its line — one entry per
    redraw would bury the lines that matter. A trailing `\r` is a CRLF ending, not a redraw.
    """
    text = chunk.rstrip("\n").rstrip("\r")
    return text.rsplit("\r", 1)[-1].rstrip()


def _collect(process, progress, deadline: float) -> tuple[str, bool, int]:
    """Read the child until it closes its output, streaming each line as it lands.

    Returns the kept text, whether the run was killed for taking too long, and how many
    characters were dropped: the tail is what the model reads, so the front goes first.
    """
    chunks: queue.Queue = queue.Queue(maxsize=2000)
    # Read as text, split only on "\n": `Popen` cannot be told this, and both the default
    # universal-newlines mode and `newline=""` end a line at every carriage return, which
    # would turn each spinner redraw into its own log entry.
    stream = io.TextIOWrapper(process.stdout, encoding="utf-8", errors="replace", newline="\n")

    def reader():
        try:
            for chunk in iter(stream.readline, ""):
                chunks.put(chunk)
        except (OSError, ValueError):
            pass                       # the pipe closes when the process dies
        finally:
            chunks.put(None)

    threading.Thread(target=reader, daemon=True, name="agent-output").start()
    try:
        kept: deque[str] = deque()
        stored = dropped = streamed = hidden = 0
        timed_out = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if timed_out:
                    break
                timed_out = True           # kill it, then read whatever it flushes on the way
                kill_tree(process)
                deadline = time.monotonic() + POST_KILL_GRACE
            try:
                chunk = chunks.get(timeout=min(max(remaining, 0.05), 0.5))
            except queue.Empty:
                continue
            if chunk is None:
                break
            pieces = chunk.split("\n")
            if pieces[-1] == "":
                pieces.pop()               # that newline ended a line; it is not a new one
            for part in pieces:
                line = _visible(part)
                kept.append(line + "\n")
                stored += len(line) + 1
                while stored > MAX_OUTPUT_CHARS and len(kept) > 1:
                    gone = kept.popleft()
                    stored -= len(gone)
                    dropped += len(gone)
                if not line:
                    continue
                if streamed < STREAM_BUDGET:
                    streamed += 1
                    # One minified bundle is a line too; the caller's channel is a status feed.
                    progress(line if len(line) <= 500 else line[:500] + "…")
                else:
                    hidden += 1
        if hidden:
            progress(f"… {hidden} more lines; only the first {STREAM_BUDGET} are streamed.")
        text = "".join(kept)
        if len(text) > MAX_OUTPUT_CHARS:      # one gigantic line is still only a tail
            dropped += len(text) - MAX_OUTPUT_CHARS
            text = text[-MAX_OUTPUT_CHARS:]
        try:
            process.wait(timeout=5.0 if timed_out else max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            kill_tree(process)
            timed_out = True
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                # A child the OS will not reap is not a reason to lose the output we already read.
                pass
    finally:
        # A progress sink that raises escapes from the middle of the loop above with the child
        # still running. Closing first would block here: the reader thread holds the buffer's lock
        # until the pipe closes, so the child has to die before the stream can.
        if process.returncode is None:
            kill_tree(process)
        try:
            stream.close()
        except OSError:
            pass
    return text, timed_out, dropped


def run(repo: Path, recipe: str, timeout: int = DEFAULT_TIMEOUT,
        progress=lambda _text: None) -> dict:
    """Execute a fixed recipe with the project folder as the working directory."""
    if recipe not in RECIPES:
        # A PolicyError carries its reason through friendly_error; a bare ValueError would be
        # replaced by "Could not complete the operation." in both windows.
        raise PolicyError("Unknown recipe: " + str(recipe)[:80])
    command = list(RECIPES[recipe]["command"])
    program = shutil.which(command[0])
    if program is None and command[0] != sys.executable:
        return {"recipe": recipe, "command": shlex.join(command), "status": "unavailable",
                "reason": command[0] + " is not installed or not on PATH."}
    command[0] = program or command[0]
    root = repo.resolve(strict=True)
    started = time.monotonic()
    started_wall = time.time()
    recipe_entry = RECIPES[recipe]
    reports = (tempfile.TemporaryDirectory(prefix="agent-report-")
               if recipe_entry.get("junit_arg") else None)
    junit = None
    if reports is not None:
        junit = Path(reports.name) / "junit.xml"
        command += [recipe_entry["junit_arg"], str(junit)]
    progress("Running " + shlex.join(recipe_entry["command"]))
    options = {"cwd": str(root), "env": child_env(), "stdin": subprocess.DEVNULL,
               "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        options["creationflags"] = new_group | no_window
    else:
        options["start_new_session"] = True
    process = None
    try:
        process = subprocess.Popen(command, **options)
        output, timed_out, dropped = _collect(process, progress, started + timeout)
        seconds = round(time.monotonic() - started, 1)
        truncated = dropped > 0
        exit_code = process.returncode
        pattern = recipe_entry["test_counts"]
        counts = re.findall(pattern, output) if pattern else []
        ran = tests_ran(recipe_entry, output, counts)
        proof = report_counts(fresh_reports(root, recipe_entry.get("reports", ()),
                                            started_wall, junit))
        if proof is not None:
            proof["source"] = recipe_entry.get("proof_source", "JUnit XML")
        status = decide_status(timed_out, exit_code, proof, ran, expects_proof(recipe_entry))
    finally:
        if process is not None and process.returncode is None:
            # Anything that raises between the spawn and the answer — a progress sink that throws,
            # a cancelled job — would otherwise leave a build running with its pipe held open.
            kill_tree(process)
            try:
                process.wait(timeout=POST_KILL_GRACE)
            except subprocess.TimeoutExpired:
                pass
        if reports is not None:
            reports.cleanup()
    return {"recipe": recipe, "label": recipe_entry["label"],
            "command": shlex.join(recipe_entry["command"]), "status": status,
            "exit_code": exit_code, "seconds": seconds,
            "tests_observed": bool(proof and proof["tests"]) or ran,
            "proof": proof, "truncated": truncated, "timed_out": timed_out,
            "output": output, "tail": output[-MODEL_OUTPUT_CHARS:], "failures": _failures(output)}


def summarize(result: dict) -> str:
    """Short human line for the status area."""
    if result["status"] == "unavailable":
        return f"{result['label']}: {result['reason']}"
    state = {"passed": "passed", "failed": "FAILED", "timeout": "timed out",
             "unverified": "no tests ran"}[result["status"]]
    proof = result.get("proof")
    counted = (f"{proof['tests']} tests, {proof['failures']} failed, {proof['errors']} errors "
               f"({proof['source']})" if proof else f"exit {result['exit_code']}")
    return f"{result['label']}: {state} ({counted}, {result['seconds']}s)"
