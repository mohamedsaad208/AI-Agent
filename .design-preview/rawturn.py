"""Prints exactly what the model returns on each turn, through the real prompt path.

Run:  python .design-preview/rawturn.py [model]
"""
import sys
from pathlib import Path
import shutil
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dataclasses import replace

from ai_code_engineer import engine
from ai_code_engineer.config import Settings
from ai_code_engineer.engine import plan
from ai_code_engineer.providers import OllamaProvider
from ai_code_engineer.workspace import Workspace

REPO = Path(__file__).resolve().parents[1] / "examples" / "demo_repo"
TASK = "Fix the add function in calculator.py so it adds the two numbers instead of subtracting them."
MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5-coder:1.5b"

real_parse = engine.parse_action


def logged(raw):
    print("\n--- MODEL RAW (" + str(len(raw)) + " chars) ---")
    print(raw[:1200])
    print("--- parsed ---")
    try:
        print(real_parse(raw))
    except Exception as exc:                            # noqa: BLE001
        print("REJECTED:", exc)
        raise
    return real_parse(raw)


engine.parse_action = logged

work = Path(tempfile.mkdtemp(prefix="agent-raw-"))
try:
    repo = work / "repo"
    shutil.copytree(REPO, repo)
    settings = replace(Settings(), provider="ollama", model=MODEL, max_turns=4, timeout_seconds=300)
    plan(Workspace(repo), TASK, OllamaProvider(settings), settings, work / "runs",
         progress=lambda line: print("\n>>>", line))
except Exception as exc:                                # noqa: BLE001
    print("\nENDED:", type(exc).__name__, str(exc)[:200])
finally:
    shutil.rmtree(work, ignore_errors=True)
