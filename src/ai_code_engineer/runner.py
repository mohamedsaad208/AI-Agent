"""Run one allowlisted build/test recipe inside the approved project folder.

Commands are constant argv lists resolved with shutil.which; no shell, no model or
user text reaches the command line. This reduces blast radius but is not a sandbox:
the sandbox is here too (`sandbox_argv`, the `sandbox=` argument), and it is the only
place in the codebase that starts a container — `verification.docker_check` asks it for
its flags rather than keeping a second copy that can drift.
"""
from __future__ import annotations

import io
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque

from . import ignore
from .errors import Cancelled, PolicyError

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
        # Cargo writes no report file, so its own summary line is the only count there is. Every
        # target prints one (`lib`, each bin, each integration test, the doc tests), and the run is
        # the sum of them: reading only the first proved 20 of 24 tests and said nothing about the
        # four that had not run.
        "console_proof": {"line": r"(?m)^test result: \w+\.\s+(?P<tests>\d+) passed;\s+"
                                  r"(?P<failures>\d+) failed;\s+(?P<skipped>\d+) ignored",
                          "source": "cargo's own summary"},
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


def display_command(recipe: str) -> str:
    """The recipe as one line a person can read before agreeing to run it.

    The interpreter is named, not pathed: an absolute `python.exe` under a user profile is noise in a
    confirmation, and the argv the child really gets is recorded in the log. Both windows ask the same
    question about the same command, so both get it from here rather than spelling out the
    substitution again.
    """
    return " ".join("python" if part == sys.executable else str(part)
                    for part in RECIPES[recipe]["command"])


def timeout_for(recipe: str) -> int:
    """JVM builds need minutes even warm; script suites usually do not.

    Cargo joins them: `cargo test` is a *compile* the first time a crate or one of its dependencies
    changes, and a cold Rust build on this class of machine runs past the 600 seconds the script
    recipes get. A killed compile reads as "the command timed out", which sends a fix round at a
    problem that was only ever slow.
    """
    return LONG_TIMEOUT if recipe.startswith(("maven", "gradle", "cargo")) else DEFAULT_TIMEOUT


def available() -> list[str]:
    """Recipes whose executable exists on this machine."""
    found = []
    for name, recipe in RECIPES.items():
        program = recipe["command"][0]
        if program == sys.executable or shutil.which(program):
            found.append(name)
    return found


def sandbox_available() -> bool:
    """Whether this machine can start the container at all — the one question the switch asks.

    Both windows grey the choice out from here rather than calling `which` themselves, so there is one
    seam for a test to answer and one answer on a machine with and without a daemon.
    """
    return bool(shutil.which("docker"))


def sandbox_state(on, image: str, available: bool) -> str:
    """Which of the four sentences a sandbox choice deserves.

    A name, not a sentence: the words belong to `labels`, and the preview window has to be able to say
    the same four things without a daemon to ask.
    """
    if not available:
        return "sandbox_missing"
    if not on:
        return "sandbox_off"
    return "sandbox_on" if SANDBOX_IMAGE.fullmatch(str(image or "").strip()) else "sandbox_unpinned"


def detect(repo: Path, installed: list[str] | None = None) -> list[str]:
    """Recipes matching the project's build files, in RECIPES order.

    `installed` lets a caller that scans many folders ask `available()` once: it resolves each
    executable against PATH, and nine modules of one reactor cost half a second when every `detect()`
    re-did it.
    """
    files = {path.name.lower() for path in repo.glob("*")}
    folders = {path.name.lower() for path in repo.iterdir() if path.is_dir()}
    result = []
    for name in (available() if installed is None else installed):
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


# ---------------------------------------------------------------- the sandbox

SANDBOX_IMAGE = re.compile(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}")
# Where the copied project lives inside the container. A POSIX path either way: the string is passed
# to Docker, not to this OS, and a Windows source is normalised to forward slashes below.
WORKDIR = "/work"
# The interpreter a recipe names once it is inside an image. `sys.executable` is this machine's
# `python.exe` under a user profile, and no image has that path.
IMAGE_PYTHON = "python3"
SANDBOX_FILES = 2000
SANDBOX_BYTES = 20_000_000
# Not part of what a clean build needs, and each one is bigger than the sources it sits beside. A
# project whose build genuinely depends on `node_modules` needs an image that carries it: the
# container has no network, so nothing can be installed into it.
SANDBOX_SKIP = (".git", ".svn", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
                ".gradle", ".venv", "venv")


def copy_for_sandbox(root: Path, target: Path) -> dict:
    """The project, on the host side of the mount, minus what a clean build does not need.

    The copy is the isolation: the user's tree is never mounted, so nothing the build writes can land
    in it, and the reports the build *does* write are readable here afterwards. Size is capped because
    a container that cannot start in reasonable time is not a safer answer than a host run — it is just
    a slower refusal, so it is refused as a policy instead.
    """
    files = total = 0
    for path in root.rglob("*"):
        if any(part in SANDBOX_SKIP for part in path.relative_to(root).parts[:-1]):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files += 1
        total += size
        if files > SANDBOX_FILES:
            raise PolicyError("The project is too large to copy into a sandbox (%d files)." % files)
        if total > SANDBOX_BYTES:
            raise PolicyError("The project is too large to copy into a sandbox (20 MB).")
    shutil.copytree(root, target, ignore=shutil.ignore_patterns(*SANDBOX_SKIP), symlinks=False,
                    dirs_exist_ok=True)
    # The container runs as an unprivileged uid; on a POSIX host a temp directory owned by this user
    # is not writable by it. Windows maps its own permissions and ignores the mode.
    os.chmod(target, 0o1777)
    return {"files": files, "bytes": total}


def sandbox_argv(docker: str, source: Path, image: str, name: str, command: list[str],
                 workdir: str = WORKDIR) -> list[str]:
    """The one container this tool knows how to start, and the only place its flags are written.

    No network, no capabilities, no new privileges, an unprivileged uid, a read-only root filesystem
    with the copied project as the single writable mount, and a pinned image — a tag can be retagged
    at any time, a digest cannot. `--rm` cleans the container up; the caller's `docker rm -f` covers
    the case where it never got to exit.

    `command` is argv inside the container: no shell, and nothing here interpolates model or user text.
    """
    if not SANDBOX_IMAGE.fullmatch(image):
        raise PolicyError("Choose a preloaded image pinned by sha256 digest.")
    return [docker, "run", "--name", name, "--rm", "--pull=never", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user=65534:65534", "--pids-limit=128", "--memory=1g", "--cpus=1",
            "--mount", "type=bind,source=%s,target=%s" % (source.as_posix(), workdir),
            "--tmpfs", "/tmp:rw,nosuid,size=128m",
            "--workdir", workdir, "--env", "HOME=/tmp", image, *command]


# Folders that hold a build of their own. This is the whole of what a "project" is here: the tool
# does not guess from the file tree, it reads the file a build tool would have to have. Lowercased,
# because the comparison is by folded name on a case-insensitive filesystem.
PROJECT_FILES = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
                 "package.json", "pyproject.toml", "go.mod", "cargo.toml", "makefile")
# Walked past, never into: a vendored dependency and a build output both contain build files that
# belong to somebody else, and `mvn` would be happy to answer to any of them. The names are
# `ignore.project_dir` holds those names: the read gate's list plus the output folders that only
# disqualify a directory as a project root, never as a file somebody asked to read.
PROJECT_DEPTH = 3
PROJECT_LIMIT = 40


def projects(repo: Path, max_depth: int = PROJECT_DEPTH,
             limit: int = PROJECT_LIMIT) -> list[str]:
    """Every folder under `repo` that holds its own build file, the opened folder first.

    A monorepo opened at the top is one folder to the model and five commands to run: the reactor
    root, and a module each. `detect()` looks at one folder's own children, so `backend/pom.xml` was
    simply invisible and the window said "no command was detected" about a project that builds
    perfectly. Depth is capped and vendor folders skipped because a real Spring reactor nests
    `node_modules` under a starter module, and every one of those has a `package.json` that would be
    offered as this project's build. The opened folder is always first: it is what the user chose, and
    a Python project with only a `tests/` folder answers to no marker file at all.
    """
    root = Path(repo)
    if not root.is_dir():
        return []
    found = ["."]
    level, depth = [root], 0
    while level and len(found) < limit and depth < max_depth:
        children: list[Path] = []
        for folder in level:
            try:
                entries = sorted((child for child in folder.iterdir() if child.is_dir()),
                                 key=lambda child: child.name.lower())
            except OSError:
                continue
            for child in entries:
                if ignore.project_dir(child.name):
                    continue
                if _holds_build_file(child):
                    relative = child.relative_to(root).as_posix()
                    if relative not in found:
                        found.append(relative)
                children.append(child)
        level, depth = children, depth + 1
    return found[:limit]


def _holds_build_file(folder: Path) -> bool:
    """Whether this folder is a build of its own, read by folded name rather than by spelling."""
    try:
        names = {child.name.lower() for child in folder.iterdir()}
    except OSError:
        return False
    return any(marker in names for marker in PROJECT_FILES)


def targets(repo: Path, max_depth: int = PROJECT_DEPTH, limit: int = PROJECT_LIMIT) -> list[dict]:
    """Where a command can be run in this folder: each project that answers to an installed tool.

    Both windows draw their picker from here, so the list of folders and the commands each one has are
    decided once. A folder with a build file whose tool is not installed is left out rather than
    offered as a button that can only answer "Maven is not on PATH" — `detect()` already filters by
    `available()`, and an empty list means the window hides the whole Checks card.
    """
    root = Path(repo)
    rows: list[dict] = []
    installed = available()
    for relative in projects(root, max_depth=max_depth, limit=limit):
        try:
            folder = project_folder(root, relative)
        except (PolicyError, OSError):
            continue
        found = detect(folder, installed)
        if not found:
            continue
        rows.append({"path": relative,
                     # The path is the label, because `api` in two places of one monorepo is two
                     # modules and the window cannot show one button that means both.
                     "label": root.name if relative == "." else relative,
                     "recipes": found})
    if len(rows) > 1 and rows[0]["path"] == ".":
        # The opened folder of a monorepo is also the reactor root, and the difference matters when
        # the choice is between "everything" and "one module".
        rows[0]["label"] = rows[0]["label"] + " (whole project)"
    return rows


def project_folder(repo: Path, target: str = "") -> Path:
    """The folder a command runs in: a chosen module of this project, never anything outside it.

    `target` arrives from a window, which means from a click on a label — so it is resolved and then
    checked against the root rather than trusted. A path that escapes the opened folder is refused
    here, in the one place both windows go through, because the alternative is a build tool pointed
    at a tree the user never approved.
    """
    root = Path(repo).resolve(strict=True)
    wanted = str(target or ".").strip().replace("\\", "/")
    if wanted in ("", ".", "./"):
        return root
    path = PurePosixPath(wanted)
    parts = path.parts
    if (path.is_absolute() or not parts or any(":" in part for part in parts)
            or any(part in (".", "..") for part in parts)):
        raise PolicyError("That folder is not inside the project: " + wanted[:120])
    try:
        candidate = (root / Path(*parts)).resolve(strict=True)
    except OSError:
        raise PolicyError("That folder is not in the project any more: " + wanted[:120]) from None
    if root not in candidate.parents:
        raise PolicyError("That folder is not inside the project: " + wanted[:120])
    return candidate


def kill_tree(process: subprocess.Popen) -> None:
    """Stop the child and its tree. This never raises: it runs on the way out of a timeout, and an
    exception here would take a build that already finished with it."""
    if os.name == "nt":
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            result = subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
                           creationflags=flags)
            if result.returncode == 0:
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


def _check_maven_pom_modules(folder: Path) -> str | None:
    pom_file = folder / "pom.xml"
    if not pom_file.exists():
        return None
    try:
        content = pom_file.read_text(encoding="utf-8", errors="ignore")
        if "<packaging>pom</packaging>" in content:
            if "<modules>" not in content or not re.search(r"<module>\s*[^<]+\s*</module>", content):
                return "Root pom.xml specifies <packaging>pom</packaging> but contains no child <modules>. Submodules were not tested."
    except Exception:
        pass
    return None



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


def console_proof(recipe_entry: dict, output: str) -> dict | None:
    """A count the tool printed itself, for the runners that write no report file.

    Only a recipe that declares one is read this way, and only its own summary lines: a proof built
    from stdout is the runner's claim about its run, not this program guessing from the word "ok"
    appearing somewhere. Returns None when the run printed no summary at all, which leaves the exit
    code and `expects_proof` to decide — the honest answer then is "unverified", not zero.
    """
    spec = recipe_entry.get("console_proof")
    if not spec:
        return None
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    found = 0
    for match in re.finditer(spec["line"], output):
        found += 1
        for name, value in match.groupdict().items():
            if name in totals:
                totals[name] += int(value or 0)
    if not found:
        return None
    totals["source"] = spec["source"]
    return totals


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


def _collect(process, progress, deadline: float, cancelled=None) -> tuple[str, bool, int]:
    """Read the child until it closes its output, streaming each line as it lands.

    Returns the kept text, whether the run was killed for taking too long, and how many
    characters were dropped: the tail is what the model reads, so the front goes first.
    """
    chunks: queue.Queue = queue.Queue(maxsize=2000)
    # Read as text, split only on "\n": `Popen` cannot be told this, and both the default
    # universal-newlines mode and `newline=""` end a line at every carriage return, which
    # would turn each spinner redraw into its own log entry.
    stream = io.TextIOWrapper(process.stdout, encoding="utf-8", errors="replace", newline="\n")

    stopped = threading.Event()

    def put_chunk(chunk):
        # A failed display consumer must not leave this reader blocked on a full queue.
        while not stopped.is_set():
            try:
                chunks.put(chunk, timeout=0.1)
                return
            except queue.Full:
                continue

    def reader():
        try:
            for chunk in iter(stream.readline, ""):
                if stopped.is_set():
                    break
                put_chunk(chunk)
        except (OSError, ValueError):
            pass                       # the pipe closes when the process dies
        finally:
            # This thread owns the buffered reader's lock. Closing it in the collector
            # can block forever if the OS refuses to stop a child still holding the pipe.
            try:
                stream.close()
            except OSError:
                pass
            put_chunk(None)

    threading.Thread(target=reader, daemon=True, name="agent-output").start()
    try:
        kept: deque[str] = deque()
        stored = dropped = streamed = hidden = 0
        timed_out = False
        while True:
            if cancelled is not None and cancelled():
                kill_tree(process)
                raise Cancelled("Build command cancelled.")
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
        stopped.set()
        if process.returncode is None:
            kill_tree(process)
    return text, timed_out, dropped


def run(repo: Path, recipe: str, timeout: int = DEFAULT_TIMEOUT,
        progress=lambda _text: None, target: str = "", sandbox: str = "",
        cancelled=None) -> dict:
    """Execute a fixed recipe in the project folder — or in one module of it, when a target is named.

    `target` is a relative folder of `repo`, never a path the caller invented: `project_folder`
    resolves and checks it, and the child's `cwd` is the result. A reactor build runs at the root and
    Maven walks the modules itself; pointing one module at a time is for the monorepo where the
    modules do not share a build file.

    `sandbox` is the pinned image to run inside, or "" for this machine. With one named, the project is
    copied to the host side of the only writable mount the container sees: the tree the user opened is
    never mounted, so nothing the build writes can reach it, and the reports the build writes are still
    readable afterwards — which is the whole reason the copy is a directory and not a tmpfs.
    """
    if recipe not in RECIPES:
        # A PolicyError carries its reason through friendly_error; a bare ValueError would be
        # replaced by "Could not complete the operation." in both windows.
        raise PolicyError("Unknown recipe: " + str(recipe)[:80])
    recipe_entry = RECIPES[recipe]
    command = list(recipe_entry["command"])
    sandbox = str(sandbox or "").strip()
    if sandbox and not SANDBOX_IMAGE.fullmatch(sandbox):
        # Refused before the copy: a tag can be retagged while the build is running, and a project
        # tree has already been walked and written to a temp folder by the time argv is assembled.
        raise PolicyError("Choose a preloaded image pinned by sha256 digest.")
    docker = ""
    if sandbox:
        docker = shutil.which("docker") or ""
        if not docker:
            # The rule the request asked for twice: a missing sandbox is a refusal, not a hint to run
            # the build on the machine the sandbox was chosen to keep the build away from.
            return {"recipe": recipe, "label": recipe_entry["label"],
                    "command": display_command(recipe), "status": "blocked",
                    "reason": "Docker is not installed. The command did not run on the host either.",
                    "sandbox": {"image": sandbox, "container": ""}}
    else:
        program = shutil.which(command[0])
        if program is None and command[0] != sys.executable:
            return {"recipe": recipe, "command": shlex.join(command), "status": "unavailable",
                    "reason": command[0] + " is not installed or not on PATH."}
        command[0] = program or command[0]
    root = Path(repo).resolve(strict=True)
    where = project_folder(root, target)
    started = time.monotonic()
    started_wall = time.time()
    reports = (tempfile.TemporaryDirectory(prefix="agent-report-")
               if recipe_entry.get("junit_arg") else None)
    sandbox_dir = None
    name = ""
    process = None
    try:
        if sandbox:
            sandbox_dir = tempfile.TemporaryDirectory(prefix="agent-sandbox-")
            copy = Path(sandbox_dir.name)
            copy_for_sandbox(root, copy)
            relative = "" if where == root else where.relative_to(root).as_posix()
            inside = WORKDIR + ("/" + relative if relative else "")
            read_from = copy if not relative else copy / where.relative_to(root)
            # The image resolves its own interpreter; this machine's absolute path means nothing in there.
            argv = [IMAGE_PYTHON if part == sys.executable else str(part) for part in command]
            junit = None
            if reports is not None:
                junit = read_from / "junit.xml"
                argv += [recipe_entry["junit_arg"], inside + "/junit.xml"]
            name = "ai-agent-" + uuid.uuid4().hex
            argv = sandbox_argv(docker, copy, sandbox, name, argv, workdir=inside)
            cwd = None
        else:
            argv = command
            read_from, cwd = where, str(where)
            junit = None
            if reports is not None:
                junit = Path(reports.name) / "junit.xml"
                argv = command + [recipe_entry["junit_arg"], str(junit)]
        progress("Running " + shlex.join(recipe_entry["command"])
                 + (" in Docker (" + sandbox.split("@")[0] + ")" if sandbox else ""))
        options = {"cwd": cwd, "env": child_env(), "stdin": subprocess.DEVNULL,
                   "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
        if os.name == "nt":
            no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            options["creationflags"] = new_group | no_window
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(argv, **options)
        try:
            output, timed_out, dropped = _collect(process, progress, started + timeout,
                                                  cancelled=cancelled)
        except TypeError:
            output, timed_out, dropped = _collect(process, progress, started + timeout)
        seconds = round(time.monotonic() - started, 1)
        truncated = dropped > 0
        exit_code = process.returncode
        pattern = recipe_entry["test_counts"]
        counts = re.findall(pattern, output) if pattern else []
        ran = tests_ran(recipe_entry, output, counts)
        proof = report_counts(fresh_reports(read_from, recipe_entry.get("reports", ()),
                                            started_wall, junit))
        if proof is not None:
            proof["source"] = recipe_entry.get("proof_source", "JUnit XML")
        else:
            # A runner that writes no report file still gets read: cargo prints its own summary and
            # nothing else, and "exit 0" alone hides how many tests that green actually was.
            proof = console_proof(recipe_entry, output)
        status = decide_status(timed_out, exit_code, proof, ran, expects_proof(recipe_entry))
    finally:
        if process is not None and process.returncode is None:
            # Anything that raises between the spawn and the answer — a progress sink that throws,
            # a cancelled job — would otherwise leave a build running with its pipe held open.
            kill_tree(process)
            try:
                process.wait(timeout=POST_KILL_GRACE)
            except subprocess.TimeoutExpired:
                # Finish the other cleanup below before reporting that this process survived.
                pass
        if name:
            # `--rm` removes a container that exits; one that was killed does not get the chance, and a
            # leftover container holds its mount for the rest of the daemon's life. A failure here is
            # not the result's business: the run it is cleaning up after is already answered.
            try:
                subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=20,
                               stdin=subprocess.DEVNULL, env=child_env(),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               if os.name == "nt" else 0)
            except (OSError, subprocess.SubprocessError):
                pass
        if sandbox_dir is not None:
            sandbox_dir.cleanup()
        if reports is not None:
            reports.cleanup()
        if process is not None and process.returncode is None:
            raise PolicyError(f"Build process {process.pid} could not be stopped. "
                              "It may still be running; stop it before starting another build.")
    failures = _failures(output)
    if recipe == "maven-test" and status == "unverified":
        pom_err = _check_maven_pom_modules(where)
        if pom_err:
            status = "failed"
            failures.append(pom_err)
    return {"recipe": recipe, "label": recipe_entry["label"],
            # Which folder of this project the command actually ran in, recorded rather than implied:
            # a reactor's root and one module of it answer to the same recipe name, and a fix round
            # sent to "the build failed" with no folder in it is a guess about which build failed.
            "target": "." if where == root else where.relative_to(root).as_posix(),
            # Where the command ran is part of what a green means: a passing Maven run inside
            # `eclipse-temurin@sha256:…` is a claim about that image's JDK, not about this machine's.
            "sandbox": {"image": sandbox, "container": name} if sandbox else None,
            "command": shlex.join(recipe_entry["command"]), "status": status,
            "exit_code": exit_code, "seconds": seconds,
            "tests_observed": bool(proof and proof["tests"]) or ran,
            "proof": proof, "truncated": truncated, "timed_out": timed_out,
            "output": output, "tail": output[-MODEL_OUTPUT_CHARS:], "failures": failures}


def run_folder(run: dict) -> str:
    """The module a run happened in, normalised: "" when it was the opened folder itself.

    A target comes from a click, from a record written before this existed, or from a call that never
    named one, and ".", "./" and "" all mean the same place — the root of the project.
    """
    parts = [part for part in str(run.get("target") or ".").replace("\\", "/").split("/")
             if part not in ("", ".")]
    return "/".join(parts)


def summarize(result: dict) -> str:
    """Short human line for the status area.

    The module is named when the command ran in one: in a nine-module reactor "Maven test: passed"
    does not say whether the build that passed was the one the user was looking at.
    """
    folder = run_folder(result)
    named = result["label"] + (" in " + PurePosixPath(folder).name if folder else "")
    # Where a green happened is part of what it claims: a passing build inside an image says nothing
    # about this machine's JDK, and a reader comparing two runs has to be able to tell them apart.
    if result.get("sandbox"):
        named += " in Docker"
    if result["status"] in ("unavailable", "blocked"):
        return f"{named}: {result['reason']}"
    state = {"passed": "passed", "failed": "FAILED", "timeout": "timed out",
             "unverified": "no tests ran"}[result["status"]]
    proof = result.get("proof")
    counted = (f"{proof['tests']} tests, {proof['failures']} failed, {proof['errors']} errors "
               f"({proof['source']})" if proof else f"exit {result['exit_code']}")
    return f"{named}: {state} ({counted}, {result['seconds']}s)"
