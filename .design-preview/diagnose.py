"""Isolates the failing proposal loop: same settings the web controller builds, but
plan() called directly with no UI, no thread and no chat history.

Run:  python .design-preview/diagnose.py
"""
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace

from ai_code_engineer.config import Settings
from ai_code_engineer.engine import load_session, plan
from ai_code_engineer.providers import OllamaProvider
from ai_code_engineer.workspace import Workspace

REPO = Path(__file__).resolve().parents[1] / "examples" / "demo_repo"
TASK = "Fix the add function in calculator.py so it adds the two numbers instead of subtracting them."
MODELS = sys.argv[1:] or ["qwen2.5-coder:1.5b"]


def attempt(model, timeout):
    work = Path(tempfile.mkdtemp(prefix="agent-diag-"))
    try:
        repo = work / "repo"
        shutil.copytree(REPO, repo)
        settings = replace(Settings(), provider="ollama", model=model, max_turns=12,
                           timeout_seconds=timeout)
        provider = OllamaProvider(settings)
        path = plan(Workspace(repo), TASK, provider, settings, work / "runs",
                    progress=lambda line: print("   ", line))
        session = load_session(path)
        return session["state"], [c["path"] for c in session.get("changes", [])], path
    except Exception as exc:                                # noqa: BLE001 - this is the report
        return type(exc).__name__ + ": " + str(exc)[:200], [], None
    finally:
        shutil.rmtree(work, ignore_errors=True)


for model in MODELS:
    for timeout in (300,):
        print("=" * 72)
        print("model:", model, "| timeout:", timeout)
        state, changes, path = attempt(model, timeout)
        print("  -> state:", state)
        print("  -> changes:", changes)
        if path:
            events = [event.get("kind") for event in load_session(path)["events"]]
            print("  -> events:", json.dumps(events))
