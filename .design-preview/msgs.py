"""Dumps the exact message array Ollama receives on each turn.

Run:  python .design-preview/msgs.py [model]
"""
import json
import sys
from pathlib import Path
import shutil
import tempfile

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

real = OllamaProvider.generate
turn = {"n": 0}


def logged(self, messages, json_mode=True):
    turn["n"] += 1
    print(f"\n########## REQUEST {turn['n']}  ({len(messages)} messages, "
          f"{sum(len(m['content']) for m in messages)} chars) ##########")
    for index, msg in enumerate(messages):
        body = msg["content"]
        print(f"[{index}] {msg['role']}  ({len(body)} chars)")
        if index >= 2:                      # the interesting tail: prior turns + observations
            print("      " + body.replace("\n", "\n      ")[:900])
    out = real(self, messages, json_mode)
    print("---------- REPLY ----------")
    print(out[:600])
    return out


OllamaProvider.generate = logged

work = Path(tempfile.mkdtemp(prefix="agent-msgs-"))
try:
    repo = work / "repo"
    shutil.copytree(REPO, repo)
    settings = replace(Settings(), provider="ollama", model=MODEL, max_turns=3, timeout_seconds=300)
    plan(Workspace(repo), TASK, OllamaProvider(settings), settings, work / "runs",
         progress=lambda line: print(">>>", line))
except Exception as exc:                    # noqa: BLE001
    print("\nENDED:", type(exc).__name__, str(exc)[:160])
finally:
    shutil.rmtree(work, ignore_errors=True)
