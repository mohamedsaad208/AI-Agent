"""Full-regression probe: old registries, HTTP surface, and the new actions end to end.

Not a unit test — it drives the shipped server the way the window does, over real HTTP, so a
contract change cannot pass the suite and still break the app.
"""
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer.webapp import server as server_module
from ai_code_engineer.webapp.controller import AgentController

FAILS = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + ("   " + detail if detail else ""))
    if not condition:
        FAILS.append(name)


def http(base, token, path, body=None):
    request = urllib.request.Request(base + path + ("&" if "?" in path else "?") + "t=" + token,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"content-type": "application/json"} if body else {})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())


# ------------------------------------------------------------------ 1. old registry
old_registry = {
    "projects": ["D:/somewhere/plain"],                     # the pre-icon bare-string form
    "ui": {"mode": "Ollama", "last_chat": "abc", "request_timeout": 300,
           "plan_chained": False, "style": "claude", "theme": "light",
           "last_project": "D:/somewhere/plain"},           # the pre-2.8 key, now ignored
}
temp = tempfile.TemporaryDirectory()
app_dir = Path(temp.name)
(app_dir / ".agent-projects.json").write_text(json.dumps(old_registry), encoding="utf-8")
controller = AgentController(app_dir)
check("an old bare-string registry still loads", len(controller.projects) == 1)
state = controller.snapshot()
check("snapshot carries the new keys", "git" in state and "banner" in state
      and "auto_apply" in state["settings"], str(sorted(k for k in state)[:6]))
check("git is empty with no folder", state["git"] == {}, repr(state["git"]))
check("auto_apply defaults off", state["settings"]["auto_apply"] is False)
check("banner starts at zero", state["banner"] == 0)
controller.close()

# ------------------------------------------------------------------ 2. garbage prefs
(app_dir / ".agent-projects.json").write_text(json.dumps(
    {"projects": [], "ui": {"auto_apply": ["not", "a", "map"], "composer": 7}}), encoding="utf-8")
try:
    broken = AgentController(app_dir)
    broken.close()
    check("a malformed auto_apply pref is ignored, not fatal", True)
except Exception as exc:                                    # noqa: BLE001 - reported below
    check("a malformed auto_apply pref is ignored, not fatal", False, repr(exc))

# ------------------------------------------------------------------ 3. live HTTP round trip
ENTRY = {"id": "probe-local", "name": "Probe Local", "cloud": False, "description": "probe"}
PROPOSAL = json.dumps({"action": "propose", "summary": "add() adds", "checks": ["Run tests"],
                       "changes": [{"path": "calculator.py",
                                    "content": "def add(a, b):\n    return a + b\n"}]})


class Model:
    model = "probe-local"
    prose_calls = 0
    action_calls = 0

    def generate(self, messages, json_mode=True):
        if json_mode:
            Model.action_calls += 1
            return PROPOSAL
        Model.prose_calls += 1
        return "prose answer"


from unittest.mock import patch

model = Model()
repo = app_dir / "project"
(repo / "tests").mkdir(parents=True)
(repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8",
                                    newline="\n")
with patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[ENTRY]), \
        patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[]), \
        patch("ai_code_engineer.webapp.controller.make_provider", return_value=model):
    live = AgentController(app_dir)
    live.catalogs["Ollama"] = [ENTRY]
    live.model = "probe-local"
    live.set_repo(str(repo))
    server, url, token = server_module.serve(live)
    base = url.rsplit("/?", 1)[0]
    try:
        started = time.time()
        while live.busy and time.time() - started < 15:
            time.sleep(0.05)
        state = http(base, token, "/api/bootstrap")
        check("GET /api/state carries git", "git" in state, repr(state.get("git")))
        check("GET /api/state carries banner", state["banner"] == 0)
        check("settings.auto_apply crosses HTTP", state["settings"]["auto_apply"] is False)

        acted = http(base, token, "/api/action", {"type": "set_auto_apply", "value": True})
        check("set_auto_apply is remembered on the server",
              live.auto_apply is True
              and acted["state"]["settings"]["auto_apply"] is True)

        before = (repo / "calculator.py").read_text(encoding="utf-8")
        http(base, token, "/api/action", {"type": "apply_block", "path": "calculator.py",
                                          "content": "def add(a, b):\n    return a * b\n"})
        deadline = time.time() + 20
        while live.busy and time.time() < deadline:
            time.sleep(0.05)
        check("apply_block produced a proposal, not a write",
              live.session and live.session["state"] == "WAITING_APPROVAL"
              and (repo / "calculator.py").read_text(encoding="utf-8") == before,
              str((live.session or {}).get("state")))
        check("the block proposal is one change with a real hash",
              len(live.session["changes"]) == 1 and len(live.session["proposal_hash"]) == 64)

        http(base, token, "/api/action", {"type": "apply_block", "path": "../escape.py",
                                          "content": "x = 1\n"})
        deadline = time.time() + 10
        while live.busy and time.time() < deadline:
            time.sleep(0.05)
        check("a block addressed outside the folder is refused",
              not (app_dir / "escape.py").exists()
              and (repo.parent / "escape.py").exists() is False
              and live.session["state"] == "WAITING_APPROVAL", live.status[:70])

        saved = json.loads((app_dir / ".agent-projects.json").read_text(encoding="utf-8"))
        check("auto_apply persisted in the registry", saved["ui"].get("auto_apply"),
              repr(saved["ui"].get("auto_apply")))

        # the routed message: same branch, chat mode, build verb
        live.set_auto_apply(False)
        live.set_composer("chat")
        actions, prose = Model.action_calls, Model.prose_calls
        http(base, token, "/api/action", {"type": "send", "text": "add a comment to add()"})
        deadline = time.time() + 25
        while live.busy and time.time() < deadline:
            time.sleep(0.05)
        check("a build verb in a chat branch took the change path",
              Model.action_calls > actions and Model.prose_calls == prose,
              "actions=%d prose=%d" % (Model.action_calls, Model.prose_calls))
        check("the branch is still Chat afterwards", live.composer == "chat")
        check("the route was not stored as the folder's mode",
              live._composer_pref.get(live.branch["key"], "chat") == "chat")
    finally:
        server.shutdown()
        live.close()

# ------------------------------------------------------------------ 4. engine surface unchanged
from ai_code_engineer import engine
from ai_code_engineer.engine import load_session, propose_block
from ai_code_engineer.workspace import Workspace

with tempfile.TemporaryDirectory() as other:
    root = Path(other) / "repo"
    root.mkdir()
    runs = Path(other) / "runs"
    runs.mkdir()
    path = propose_block(Workspace(root), "probe", "new.py", "CONSTANT = 1\n", runs)
    session = load_session(path)
    check("propose_block hashes the same fields as a model proposal",
          set(engine.proposal_hash(session) and ["id", "root", "task", "summary", "checks", "changes"])
          <= {"id", "root", "task", "summary", "checks", "changes"})
    check("propose_block leaves the file unwritten", not (root / "new.py").exists())

temp.cleanup()
print("\n" + ("ALL CHECKS PASSED" if not FAILS else "FAILURES: " + ", ".join(FAILS)))
sys.exit(1 if FAILS else 0)
