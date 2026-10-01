"""The web window's test doubles, in one file so the suites that use them cannot disagree.

Everything here was a copy living at the top of `test_controller.py`. They stay out of `doubles.py`
because that file's rule is *shared by more than one window's tests*: these are read only by the
controller suites, and their behaviour is what those tests are about — a queue needs a model that
holds its answer until the test lets it go, a reasoning row needs a model that emits deliberation
beside its action. Moving them next to the tests that assert on them would make the assertion travel
with the definition, which is how a double gets quietly changed to make a test pass.

Imported as a top-level module, so each file adds its own directory to `sys.path` first — that is what
makes `python -m unittest discover -s tests` and `python -m unittest tests.test_controller` agree.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way

from ai_code_engineer.webapp.controller import AgentController
from doubles import CALCULATOR_GOOD, patched_catalog as shared_patched_catalog


def patched_catalog():
    """This window's discovery patch: the shared double, aimed at the controller's own import."""
    return shared_patched_catalog("webapp.controller")


class Scripted(AgentController):
    """The two places the window opened a native dialog now answer from the test."""

    def __init__(self, app_dir, answers=None):
        self.answers = {"confirm": True, **(answers or {})}
        self.asked = []
        super().__init__(app_dir)

    def confirm_choice(self, title, message, warning="", ok_label="Continue", alt_label=""):
        """The one dialog seam. `confirm` is a thin reader of this, so overriding the wrapper left
        every test that reaches the fix offer waiting on a real `_ask` for half an hour."""
        self.asked.append({"title": title, "message": message, "warning": warning, "ok": ok_label,
                           "alt": alt_label})
        answer = self.answers["confirm"]
        return answer if isinstance(answer, dict) else {"ok": bool(answer)}

    def ask_directory(self, title, hint="", mustexist=True):
        self.asked.append({"title": title, "directory": True})
        chosen = self.answers.get("directory")
        return Path(chosen) if chosen else None

    def ask_plan_file(self):
        self.asked.append({"title": "plan", "plan": True})
        chosen = self.answers.get("plan")
        return Path(chosen) if chosen else None


class DualModel:
    """Answers in prose when asked for prose, and proposes only when handed the action envelope."""

    model = "test-local"

    def __init__(self, content=CALCULATOR_GOOD):
        self.content = content
        self.calls = []

    def generate(self, messages, json_mode=True):
        self.calls.append((json_mode, messages))
        if not json_mode:
            return "A monotonic clock never moves backwards."
        return json.dumps({"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
                           "changes": [{"path": "calculator.py", "content": self.content}]})

    @property
    def prose(self):
        return [call for call in self.calls if not call[0]]

    @property
    def actions(self):
        return [call for call in self.calls if call[0]]


class GatingModel:
    """A model that holds its first answer until the test lets it go.

    "While a task is running" has to be a real running task, not a flag set by hand: the whole
    point of the queue is that it drains from inside a job finishing.
    """

    model = "test-local"

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.prompts = []

    def generate(self, messages, json_mode=True):
        self.prompts.append(messages)
        if len(self.prompts) == 1:
            self.gate.wait(20)
        return json.dumps({"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
                           "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]})


class SteppingModel:
    """Reads a file, reads it again, searches, then proposes — the shape of a real turn loop.

    The repeat is the point: a small model re-reading the same file is common enough that the
    thread has to say it once, not twice.
    """

    model = "test-local"
    SCRIPT = ({"action": "read_file", "path": "calculator.py"},
              {"action": "read_file", "path": "calculator.py"},
              {"action": "search_code", "query": "def add"},
              {"action": "propose", "summary": "Fix addition", "checks": ["Run unit tests"],
               "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]})

    def __init__(self):
        self.turn = 0
        self.prompts = []

    def generate(self, messages, json_mode=True):
        self.prompts.append(messages)
        action = self.SCRIPT[min(self.turn, len(self.SCRIPT) - 1)]
        self.turn += 1
        return json.dumps(action)


class ThinkingModel(SteppingModel):
    """The same four turns, plus the deliberation a reasoning model returns beside its answer."""

    THOUGHTS = ("", "It adds two numbers, so the bug is in the operator.\nNot in the return type.",
                "", "A search before the second read would have saved a turn.")

    def __init__(self):
        super().__init__()
        self.reasoning = ""

    def generate(self, messages, json_mode=True):
        self.reasoning = self.THOUGHTS[min(self.turn, len(self.THOUGHTS) - 1)]
        return super().generate(messages, json_mode=json_mode)


class StreamingModel:
    """A model that answers in pieces and says so, the way both real providers do."""

    model = "test-local"
    supports_stream = True

    def __init__(self, pieces=("The guard ", "lives in create().", ""), envelope=None):
        self.pieces, self.envelope = list(pieces), envelope or {
            "action": "propose", "summary": "Fix add", "checks": ["unit tests"],
            "changes": [{"path": "calculator.py", "content": CALCULATOR_GOOD}]}
        self.asked = []

    def generate(self, messages, json_mode=True, on_token=None):
        self.asked.append(on_token is not None)
        for piece in self.pieces:
            if on_token is not None:
                on_token(piece)
        if not json_mode:
            return "".join(self.pieces)
        return json.dumps(self.envelope)
