"""The doubles several test modules were each carrying their own copy of.

Nothing here is new behaviour: every object below is the copy that was already in
`test_controller.py` / `test_gui.py` / `test_cli.py`, moved. The reason is the drift the main code
already got bitten by — `patched_catalog()` existed twice and differed the moment discovery changed
shape, and a test double that disagrees between two files is how a Web test passes over a Tk test
that should have failed.

Stubs whose *behaviour is the point of one test* deliberately stay in their own file:
`SteppingModel`, `DualModel`, `GatingModel`, `ScriptedModel` and the two `ScriptedProvider` classes
(the one in `test_repair.py` records the prompts it was given, the one in `test_agent.py` does not).
Merging those would be a rewrite with the safety net removed.

Imported as a top-level module, so each file adds its own directory to `sys.path` first — that is
what makes `python -m unittest discover -s tests` and `python -m unittest tests.test_gui` agree.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from ai_code_engineer import catalog

# ------------------------------------------------------------------ the fixtures

OLLAMA_ENTRY = {"id": "test-local", "name": "Test Local", "cloud": False,
                "description": "Synthetic local model for tests"}
FREE_ENTRY = {"id": "x:free", "name": "X Free", "free": True, "cloud": True,
              "description": "Synthetic free cloud model"}

CALCULATOR_BAD = "def add(a, b):\n    return a - b\n"
CALCULATOR_GOOD = "def add(a, b):\n    return a + b\n"

PROOF = {"tests": 4, "failures": 0, "errors": 0, "skipped": 0, "source": "JUnit XML"}

PLAN = ("# Build it\n\n## 1. Foundation\nCreate the package.\n\n"
        "## 2. Register\nReject duplicate emails.\n")


def run_result(status="passed", proof=None, observed=True, failures=None, target="."):
    """The shape `runner.run()` answers with, without running anything."""
    return {"recipe": "python-unittest", "label": "Python unittest", "command": "python -m unittest",
            "target": target,
            "status": status, "exit_code": 0 if status == "passed" else 1, "seconds": 1.4,
            "tests_observed": observed, "proof": proof, "truncated": False, "timed_out": False,
            "output": "Ran 4 tests\nOK\n", "tail": "Ran 4 tests\nOK\n",
            "failures": failures or []}


def patched_catalog(module: str):
    """Discovery for tests: one call, the real ``(entries, source)`` shape, no socket.

    `module` is the dotted name under `ai_code_engineer` that owns the import — `"gui"` for the Tk
    window, `"webapp.controller"` for the web one. Both windows pull `models_for` into their own
    namespace, so patching the package root would be invisible to both, which is exactly the mistake
    the two copies of this helper used to make in different ways.

    The entries are copies. The two originals returned `list(FREE_ENTRY)` for a free-only row, which
    is a dict's *keys* — a list of five strings that every caller then indexed as if it were an
    entry. Nothing noticed because no test read the ids back; the shape check at the bottom of this
    file is what caught it, and the copy is why a test may now sort a catalog in place.
    """
    def models_for(kind, endpoint="", api_key=None):
        entries = [dict(FREE_ENTRY)] if kind.free_only else [dict(OLLAMA_ENTRY)]
        return entries, catalog.LIVE
    return patch("ai_code_engineer.%s.models_for" % module, side_effect=models_for)


# ------------------------------------------------------------------ the model doubles

class ProposalModel:
    """Answers every turn with the same proposal, so a plan needs no real model."""

    model = "test-local"

    def __init__(self, content=CALCULATOR_GOOD):
        self.content = content
        self.prompts = []

    def generate(self, messages, json_mode=True):
        self.prompts.append(messages)
        return json.dumps({"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
                           "changes": [{"path": "calculator.py", "content": self.content}]})


class ChatModel:
    """The answering half of the loop: one canned sentence, and a record of what it was asked."""

    model = "test-local"

    def __init__(self):
        self.calls = []

    def generate(self, messages, json_mode=True):
        self.calls.append((messages, json_mode))
        return "A monotonic clock never moves backwards."
