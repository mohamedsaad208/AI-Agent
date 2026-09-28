"""Run fixed test recipes inside an explicit, preloaded Docker image."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import uuid

from .engine import atomic_json, event, load_session
from .errors import PolicyError
from . import runner
from .workspace import Workspace, digest

RECIPES = {
    "python-unittest": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
    "maven-test": ["mvn", "-o", "-B", "test"],
    "gradle-test": ["gradle", "--offline", "--no-daemon", "test"],
}


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
    docker = shutil.which("docker")
    if not docker:
        return {"status": "blocked", "reason": "Docker is not installed. Host execution is disabled."}
    if recipe not in RECIPES or not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", image):
        raise PolicyError("Choose a built-in recipe and a preloaded image pinned by sha256 digest.")
    name = "ai-agent-" + uuid.uuid4().hex
    # Only constant shell text; no model or user command interpolation.
    args = [docker, "run", "--name", name, "--rm", "--pull=never", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user=65534:65534", "--pids-limit=128", "--memory=1g", "--cpus=1",
            "--mount", f"type=bind,source={source},target=/input,readonly",
            "--tmpfs", "/tmp:rw,nosuid,size=128m",
            "--tmpfs", "/work:rw,exec,nosuid,size=512m,mode=1777",
            "--workdir=/work", "--env", "HOME=/tmp", "--entrypoint=/bin/sh", image,
            "-c", 'cp -R /input/. /work/ && exec "$@"', "agent", *RECIPES[recipe]]
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
