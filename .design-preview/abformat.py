"""A/B the Ollama request shape: same loop, with and without constrained JSON output.

Run:  python .design-preview/abformat.py
"""
import json
import sys
from pathlib import Path
import shutil
import tempfile
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace

from ai_code_engineer import providers
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import plan
from ai_code_engineer.providers import OllamaProvider
from ai_code_engineer.workspace import Workspace

REPO = Path(__file__).resolve().parents[1] / "examples" / "demo_repo"
TASK = "Fix the add function in calculator.py so it adds the two numbers instead of subtracting them."
MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-coder:1.5b"

with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=8) as response:
    print("ollama version:", json.loads(response.read()).get("version"))

real_generate = OllamaProvider.generate


def without_json_format(self, messages, json_mode=True):
    """Keep everything identical except dropping the grammar constraint."""
    original = self.settings
    try:
        return real_generate(self, messages, json_mode=False)
    finally:
        self.settings = original


def run(label, patch_on):
    work = Path(tempfile.mkdtemp(prefix="agent-ab-"))
    try:
        repo = work / "repo"
        shutil.copytree(REPO, repo)
        settings = replace(Settings(), provider="ollama", model=MODEL, max_turns=6, timeout_seconds=300)
        seen = []

        def progress(line):
            seen.append(line)
            print("   ", line)

        if patch_on:
            OllamaProvider.generate = without_json_format
        try:
            path = plan(Workspace(repo), TASK, OllamaProvider(settings), settings, work / "runs",
                        progress=progress)
            from ai_code_engineer.engine import load_session
            session = load_session(path)
            print(f"{label}: state={session['state']} changes={[c['path'] for c in session.get('changes', [])]}")
        except Exception as exc:                        # noqa: BLE001
            print(f"{label}: FAILED {type(exc).__name__}: {str(exc)[:140]}")
        finally:
            if patch_on:
                OllamaProvider.generate = real_generate
    finally:
        shutil.rmtree(work, ignore_errors=True)


run("A  format=json (current code)", patch_on=False)
print()
run("B  no format constraint", patch_on=True)
