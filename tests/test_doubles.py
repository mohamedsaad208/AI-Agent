"""The shared doubles, and the rule that they stay shared.

Two halves. The first checks the doubles answer the way the copies they replaced did — a helper that
silently changed shape would make every suite pass while testing something else. The second is the
reason the file exists: a definition of the same name appearing again in a test module is how
`patched_catalog()` ended with two copies that disagreed about discovery's return shape.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # so `doubles` is importable either way

from ai_code_engineer import catalog, runner
from ai_code_engineer.config import kind_for
from doubles import (CALCULATOR_BAD, CALCULATOR_GOOD, ChatModel, PROOF, ProposalModel,
                     patched_catalog, run_result)
from helpers import sandbox_repo

# The two rows the discovery double answers differently for: one free-only, one not.
FREE_KIND = kind_for("openrouter")
LOCAL_KIND = kind_for("ollama")

TESTS = Path(__file__).resolve().parent
# `patched_catalog` is in this list for the delegation test below, not the no-redeclaration one:
# each window keeps a one-line wrapper that names its own import, which is the point.
SHARED = ("ChatModel", "ProposalModel", "run_result", "OLLAMA_ENTRY",
          "FREE_ENTRY", "CALCULATOR_BAD", "CALCULATOR_GOOD", "PROOF")
WRAPPERS = ("patched_catalog",)


def top_level_names(module: Path) -> set:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in SHARED:
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            found |= {target.id for target in node.targets
                      if isinstance(target, ast.Name) and target.id in SHARED}
    return found


class OneCopyEach(unittest.TestCase):
    def test_no_module_declares_a_shared_double_again(self):
        """No test module may declare one of these again. `patched_catalog` is deliberately not in
        the list: each window keeps a one-line wrapper for it, and the next test holds those to
        delegating rather than reimplementing.

        The walk covers every module in `tests/`, not only the `test_*` ones, because a shared name
        can be re-declared in a helper file just as quietly -- and a helper is the one place a second
        copy would be believed.
        """
        for module in sorted(TESTS.glob("*.py")):
            if module.name == "doubles.py":
                continue
            with self.subTest(module=module.name):
                self.assertEqual(top_level_names(module), set(),
                                 "%s re-declares a shared double" % module.name)

    def test_the_wrappers_point_at_the_window_they_belong_to(self):
        bodies = {}
        for name in ("test_gui.py", "controller_case.py"):
            tree = ast.parse((TESTS / name).read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name == "patched_catalog":
                    bodies[name] = ast.dump(node)
        self.assertEqual(sorted(bodies), ["controller_case.py", "test_gui.py"])
        for name, dump in bodies.items():
            self.assertIn("shared_patched_catalog", dump, name)
            self.assertNotIn("models_for", dump, "%s builds its own discovery stub again" % name)

    def test_the_shared_file_defines_everything_it_promises(self):
        self.assertEqual(top_level_names(TESTS / "doubles.py"), set(SHARED))


class WhatTheDoublesAnswer(unittest.TestCase):
    def test_chat_model_records_and_answers(self):
        model = ChatModel()
        self.assertEqual(model.generate([{"role": "user", "content": "x"}], json_mode=False),
                         "A monotonic clock never moves backwards.")
        self.assertEqual(model.calls, [([{"role": "user", "content": "x"}], False)])

    def test_proposal_model_proposes_the_code_it_was_given(self):
        import json
        answer = json.loads(ProposalModel(content="def add(a, b):\n    return a\n").generate([]))
        self.assertEqual(answer["action"], "propose")
        self.assertEqual(answer["changes"], [{"path": "calculator.py", "content": CALCULATOR_GOOD.replace(
            "return a + b", "return a")}])

    def test_the_runner_double_has_the_keys_the_real_one_returns(self):
        """If `runner.run()` grows a field, the double is lying and every test that reads it goes
        quiet. This is the one place that says so out loud."""
        self.assertEqual(set(run_result(proof=PROOF)),
                         {"recipe", "label", "command", "status", "exit_code", "seconds",
                          "tests_observed", "proof", "truncated", "timed_out", "output",
                          "tail", "failures", "target", "sandbox"})

    def test_the_status_of_a_failed_run_is_the_one_the_caller_branches_on(self):
        self.assertEqual(run_result(status="failed")["exit_code"], 1)
        self.assertTrue(run_result(status="failed", failures=["boom"])["failures"])

    def test_discovery_is_answered_at_the_import_each_window_actually_made(self):
        """Patching the package root would leave both windows calling the real function, and the suite
        would go to the network — which is what the two separate copies existed to avoid.

        The entries have to be the shapes the window reads, not merely a list: the copy this
        replaced answered a free-only row with `list(FREE_ENTRY)`, which is a dict's five keys, and
        no test looked.
        """
        for module in ("gui", "webapp.controller"):
            with self.subTest(module=module):
                owner = importlib.import_module("ai_code_engineer." + module)
                real = owner.models_for
                started = patched_catalog(module)
                started.start()
                self.addCleanup(started.stop)
                self.assertIsNot(owner.models_for, real,
                                 "%s was not the module that imports discovery" % module)
                entries, source = owner.models_for(FREE_KIND, "http://127.0.0.1:1", None)
                self.assertEqual([entry["id"] for entry in entries], ["x:free"])
                self.assertEqual(source, catalog.LIVE)
                local, _ = owner.models_for(LOCAL_KIND, "http://127.0.0.1:1", None)
                self.assertEqual([entry["id"] for entry in local], ["test-local"])
                entries[0]["id"] = "mutated-by-one-test"
                again, _ = owner.models_for(FREE_KIND, "", None)
                self.assertEqual(again[0]["id"], "x:free", "each answer is a copy")


class TheRepositoryFixture(unittest.TestCase):
    def test_a_fixture_repo_is_a_python_project_the_runner_can_read(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = sandbox_repo(temp)
            self.assertEqual((repo / "calculator.py").read_text(encoding="utf-8"), CALCULATOR_BAD)
            self.assertTrue((repo / "tests").is_dir())
            self.assertEqual(runner.detect(repo), runner.detect(repo))

    def test_the_tk_shape_stays_the_shape_it_had(self):
        """`with_tests=False` is not tidiness: a `tests/` directory changes which command
        `runner.detect()` believes is available, and the Tk window's tests were written against a
        folder that has never had one."""
        with tempfile.TemporaryDirectory() as temp:
            plain = sandbox_repo(temp, with_tests=False)
            self.assertFalse((plain / "tests").exists())
            with patch("ai_code_engineer.runner.available", return_value=list(runner.RECIPES)):
                self.assertNotIn("python-pytest", runner.detect(plain))


if __name__ == "__main__":
    unittest.main()
