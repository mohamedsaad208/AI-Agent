"""Scratch: serve the real controller with a scripted model so the new sidebar can be driven.

Not a test file — it exists so the browser pass exercises controller.py, server.py and app.js
exactly as Run-Agent.bat does, with only the model replaced. Flags: ``--inside`` boots already
inside the granted folder in Change mode (so a run can be started without driving the picker),
``--chunk`` proposes the same edit in the §6 search/replace shape, and ``--fail`` adds a suite
that fails so the §5 "Keep going?" question appears after a run.
"""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer.webapp import server as server_module
from ai_code_engineer.webapp.controller import AgentController

CALC_GOOD = "def add(a, b):\n    return a + b\n"
PROPOSAL = json.dumps({"action": "propose", "summary": "add() now adds the two numbers.",
                       "checks": ["Run the project tests"],
                       "changes": [{"path": "calculator.py", "content": CALC_GOOD}]})
# The §6 shape: the same change as one exact hunk, so the review shows two lines, not a file.
CHUNK = json.dumps({"action": "propose", "summary": "add() now adds the two numbers.",
                    "checks": ["Run the project tests"],
                    "changes": [{"path": "calculator.py", "edits": [
                        {"search": "    return a - b", "replace": "    return a + b"}]}]})


FENCE = chr(96) * 3
# An answer with two blocks: one that names a file, where the Apply-to-File button belongs, and
# one that does not, where it must not appear. One page checks both halves of §3.
BLOCK_PROSE = ("Here is the fixed function, and a fragment to keep as a note.\n\n"
               + FENCE + "python\n# path: calculator.py\ndef add(a, b):\n    return a + b\n"
               + FENCE + "\n\nA scratch fragment with no file of its own:\n\n"
               + FENCE + "python\nprint(add(2, 3))\n" + FENCE + "\n")


class Scripted:
    model = "verify-local"
    proposal = PROPOSAL
    prose = BLOCK_PROSE

    def generate(self, messages, json_mode=True):
        if json_mode:
            return self.proposal
        if "--block" in sys.argv:
            return Scripted.prose
        # Arabic prose with English code: the RTL check is that the paragraph flows right-to-left
        # while the block below it does not mirror.
        return ("أكيد — أحتاج أولًا إضافة اعتماديات Spring Security إلى `pom.xml`، ثم تعريف سلسلة "
                "المرشحات التي تتحقق من الرمز.\n\n"
                + "```xml\n<dependencies>\n    <dependency>\n        <groupId>org.springframework.boot</groupId>\n"
                  "        <artifactId>spring-boot-starter-security</artifactId>\n    </dependency>\n</dependencies>\n```\n\n"
                "اسألني عن القطعة التالية عندما تريد.")


ENTRY = {"id": "verify-local", "name": "Verify Local", "cloud": False,
         "description": "Scripted model used for the browser pass"}


class ScriptedChunk(Scripted):
    proposal = CHUNK


SLOW_TEST = '''"""A suite that talks while it runs, so the browser pass can see the stream arrive."""
import time
import unittest


class Chatty(unittest.TestCase):
    def test_one(self):
        print("compiling module one", flush=True)
        time.sleep(0.4)
        print("linking module one", flush=True)
        time.sleep(0.4)
        print('password = "hunter2-secret-value"', flush=True)
        time.sleep(0.4)

    def test_two(self):
        print("done", flush=True)
'''


FAILING_TEST = '''"""A suite that fails, so the §5 "Keep going?" question can be driven in the browser."""
import unittest


class Broken(unittest.TestCase):
    def test_add(self):
        self.assertEqual(5, 2 + 2)
'''


def main():
    app_dir = Path(tempfile.mkdtemp(prefix="ui28-"))
    repo = app_dir / "sandbox-repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "tests" / "test_chatty.py").write_text(SLOW_TEST, encoding="utf-8")
    granted = app_dir / "granted-repo"
    (granted / "tests").mkdir(parents=True)
    (granted / "calculator.py").write_text("def add(a, b):\n    return a - b\n")
    (granted / "tests" / "test_chatty.py").write_text(SLOW_TEST, encoding="utf-8")
    if "--fail" in sys.argv:
        (granted / "tests" / "test_broken.py").write_text(FAILING_TEST, encoding="utf-8")
    if "--git" in sys.argv:
        # The branch chip needs a real repository; --git without it is the negative case.
        import subprocess
        from ai_code_engineer import git_integration
        def sh(*args):
            subprocess.run([git_integration.git_program(), *args], cwd=str(granted),
                           env=git_integration.env(), stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
        sh("init", "-q")                      # 2.15 has no `init -b`; the name does not matter
        sh("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent",
           "-c", "commit.gpgsign=false", "add", "-A")
        sh("-c", "user.email=agent@example.invalid", "-c", "user.name=Agent",
           "-c", "commit.gpgsign=false", "commit", "-q", "-m", "first commit")
        if "--dirty" in sys.argv:
            (granted / "notes.md").write_text("work in progress" + chr(10), encoding="utf-8")

    patches = [patch("ai_code_engineer.webapp.controller.ollama_models", return_value=[ENTRY]),
               patch("ai_code_engineer.webapp.controller.openrouter_models", return_value=[]),
               patch("ai_code_engineer.webapp.controller.make_provider",
                     return_value=ScriptedChunk() if "--chunk" in sys.argv else Scripted())]
    for patcher in patches:
        patcher.start()

    controller = AgentController(app_dir)
    controller.catalogs["Ollama"] = [ENTRY]
    controller.model = "verify-local"
    from ai_code_engineer.engine import project_key
    key = project_key(str(granted))
    controller.projects[key] = str(granted)          # a folder granted in an earlier session
    controller._save_state()
    if "--inside" in sys.argv:                       # boot already in the project, Change mode
        controller._select_branch("project", key, composer="change")
    if "--chatbranch" in sys.argv:                   # the same folder as a bound chat
        controller._select_branch("project", key, composer="chat")
    if "--headed" in sys.argv:                       # an answer already holding both kinds of block
        controller._add("assistant", "Verify Local", Scripted.prose)
    if "--routed" in sys.argv:
        # §1: a bound chat, then a message that opens with a build verb. The route has to fire
        # for this one message and say so in the thread, without the branch leaving Chat.
        controller._select_branch("project", key, composer="chat")
        controller.start_plan("add a logout button to the profile page")
        controller.join()
    if "--auto" in sys.argv:
        # One whole turn before the page opens: the proposal arrives, writes itself, is
        # committed to git and checked, so the banner and the chip can be looked at.
        controller._select_branch("project", key, composer="change")
        controller.set_auto_apply(True)
        controller.start_plan("Fix add() so it adds the two numbers together")
        controller.join()
    server, url, token = server_module.serve(controller)
    print("URL " + url, flush=True)
    print("APPDIR " + str(app_dir), flush=True)
    print("GRANTED " + str(granted) + " key " + key, flush=True)
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
