"""Run fixed test recipes inside an explicit, preloaded Docker image."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid

from .engine import atomic_json, event, load_session
from .errors import PolicyError
from . import runner
from .workspace import Workspace, digest

RECIPES = {
    # The container has no network, so a build that would have downloaded a dependency on the host has
    # to be told not to try. These are `runner.RECIPES` with that one flag added, and nothing else: a
    # second table of hand-copied commands is how the two drifted apart on exactly that flag.
    "python-unittest": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
    "maven-test": ["mvn", "-o", "-B", "test"],
    "gradle-test": ["gradle", "--offline", "--no-daemon", "test"],
}

# Which flag each recipe needs to be honest about a machine with no network, keyed on the program so a
# recipe added to `runner.RECIPES` is not silently missing an offline mode here.
OFFLINE = {"mvn": "-o", "gradle": "--offline"}


def container_command(recipe: str) -> list[str]:
    """The runner's own argv for a recipe, with the offline flag a `--network=none` build needs.

    `sys.executable` becomes the image's interpreter: this machine's absolute path to a python under a
    user profile means nothing inside a container, and a recipe that names it would fail to start.
    """
    command = [runner.IMAGE_PYTHON if part == sys.executable else str(part)
               for part in runner.RECIPES[recipe]["command"]]
    if command[0] in OFFLINE:
        command.insert(1, OFFLINE[command[0]])
    return command


def static_check(session: dict) -> list[dict]:
    ws = Workspace(Path(session["root"]))
    results = []
    for change in session["changes"]:
        if change.get("delete"):
            # A removal's static check is that the file is gone: there is no content to parse, and
            # reading it would raise for the one reason this row is supposed to accept.
            if ws.path(change["path"]).exists():
                raise PolicyError("Changed files no longer match the approved proposal.")
            results.append({"path": change["path"], "status": "not_applicable"})
            continue
        current = ws.read(change["path"])
        if current["sha256"] != change["after_hash"]:
            raise PolicyError("Changed files no longer match the approved proposal.")
        # The workspace admits a suffix in any case, so the gate has to compare the same way —
        # otherwise APP.PY reaches disk unchecked, twice over.
        suffix = Path(change["path"]).suffix.lower()
        try:
            if suffix == ".py":
                ast.parse(current["content"], filename=change["path"])
            elif suffix == ".json":
                json.loads(current["content"])
            else:
                results.append({"path": change["path"], "status": "not_applicable"})
                continue
            results.append({"path": change["path"], "status": "passed"})
        except (SyntaxError, ValueError):
            results.append({"path": change["path"], "status": "failed"})
    return results


def _readable(ws: Workspace, name: str) -> dict | None:
    """A file's read record, or None when the read limits refuse it.

    The 128 KiB and UTF-8 ceilings bound what may reach a model's context; they are not a reason
    a repository with one generated lock file too big to quote cannot be verified at all. The
    snapshot carries what it can, and the same rule decides both sides of the drift check.
    """
    try:
        return ws.read(name)
    except PolicyError:
        return None


def _fingerprints(ws: Workspace) -> dict[str, str]:
    out = {}
    for name in ws.files(limit=2001):
        item = _readable(ws, name)
        if item is not None:
            out[name] = item["sha256"]
    return out


def snapshot(ws: Workspace, target: Path) -> dict:
    hashes = {}
    total = 0
    names = ws.files(limit=2001)
    if len(names) > 2000:
        raise PolicyError("Verification snapshot exceeds 2000 files.")
    for name in names:
        item = _readable(ws, name)
        if item is None:
            continue
        raw = item["content"].encode("utf-8")
        total += len(raw)
        if total > 20_000_000:
            raise PolicyError("Verification snapshot exceeds 20 MB.")
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
        hashes[name] = item["sha256"]
    return hashes


def docker_check(source: Path, recipe: str, image: str, timeout: int = 180) -> dict:
    """The session's own verification run: the snapshot copied to a temp folder, built inside a container.

    `source` is that copy, never the user's tree, and it is mounted as the only writable filesystem the
    build sees — which is also why the copy is a directory and not a tmpfs: the test reports have to be
    readable afterwards, and a green with no proof is the answer this tool refuses to give elsewhere.
    """
    docker = shutil.which("docker")
    if not docker:
        return {"status": "blocked", "reason": "Docker is not installed. Host execution is disabled."}
    if recipe not in RECIPES:
        raise PolicyError("Choose a built-in recipe and a preloaded image pinned by sha256 digest.")
    name = "ai-agent-" + uuid.uuid4().hex
    # The flags live in one place now, with the runner's; this call used to carry its own copy of them
    # and had already drifted from it on the one flag that matters without a network.
    args = runner.sandbox_argv(docker, Path(source), image, name, container_command(recipe))
    output = bytearray()
    exceeded = threading.Event()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, env=runner.child_env(),
                               creationflags=flags)

    def read_output():
        while chunk := process.stdout.read(4096):
            remaining = 64000 - len(output)
            output.extend(chunk[:max(0, remaining)])
            if len(chunk) > remaining:
                exceeded.set()
                process.kill()
                break

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    finally:
        # The cleanup is a docker call like the run: scrubbed environment, no console handle to
        # block on, and a failure here must not swallow the result it is trying to finish writing.
        try:
            subprocess.run([docker, "rm", "-f", name], capture_output=True, timeout=20,
                           stdin=subprocess.DEVNULL, env=runner.child_env(),
                           creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            pass
        reader.join(timeout=5)
        process.stdout.close()
    text = output.decode("utf-8", errors="replace")
    # A green process with no tests must not count as passing.
    if recipe == "python-unittest":
        counts = re.findall(r"Ran (\d+) tests?", text)
    elif recipe == "maven-test":
        counts = re.findall(r"Tests run:\s*(\d+)", text)
    else:
        counts = []  # Gradle requires XML report parsing, not console heuristics.
    proven_tests = any(int(n) > 0 for n in counts)
    status = "passed" if process.returncode == 0 and proven_tests else "failed"
    if process.returncode == 0 and not proven_tests:
        status = "unverified"
    if timed_out or exceeded.is_set():
        status = "blocked"
    return {"status": status, "exit_code": process.returncode, "recipe": recipe,
            "image": image, "timeout": timed_out, "output_limited": exceeded.is_set(),
            "output": text, "tests_observed": proven_tests}


def verify(path: Path, recipe: str | None = None, image: str | None = None) -> dict:
    session = load_session(path)
    if session["state"] not in {"APPLIED_UNVERIFIED", "CHECKS_PASSED", "VERIFICATION_FAILED", "VERIFICATION_BLOCKED"}:
        raise PolicyError("Apply the reviewed proposal before verification.")
    result = {"static": static_check(session)}
    if any(r["status"] == "failed" for r in result["static"]):
        result["status"] = "failed"
    elif recipe is None:
        result.update(status="unverified", reason="Static checks only; build/tests have not run.")
    elif not image:
        result.update(status="blocked", reason="An approved preloaded Docker image digest is required.")
    else:
        ws = Workspace(Path(session["root"]))
        with tempfile.TemporaryDirectory(prefix="agent-verify-") as temp:
            before = snapshot(ws, Path(temp))
            result["sandbox"] = docker_check(Path(temp), recipe, image)
            after = _fingerprints(ws)
            result["snapshot_hash"] = digest(json.dumps(before, sort_keys=True).encode())
            result["status"] = result["sandbox"]["status"] if before == after else "stale"
    # Passing a selected recipe is not proof of all natural-language acceptance criteria.
    session["state"] = {"passed": "CHECKS_PASSED", "failed": "VERIFICATION_FAILED"}.get(
        result["status"], "VERIFICATION_BLOCKED")
    session["verification"] = result
    event(session, "verification", status=result["status"])
    atomic_json(path, session)
    return result
