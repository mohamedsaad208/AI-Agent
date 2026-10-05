"""The context builder's own rules: what is sent, in which shape, and what it is charged.

`test_context_engine.py` holds the loop's behaviour end to end. These tests hold the module the loop
now delegates to: the block text a fragment is labelled with, the charge a piece pays before it is
kept, the caps that bound the pass, and the sizing of the task-state block from the room the built
prompt actually has left.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import context_builder, symbols
from ai_code_engineer.config import Settings
from ai_code_engineer.errors import PolicyError

JAVA = ("package com.acme.web;\n@RestController\npublic class {cls} {{\n"
        "    public String login(String email) {{ return \"{marker}\"; }}\n"
        "    public void other() {{ }}\n}}\n")


class FakeWorkspace:
    """A workspace whose files are dicts in a folder: reads answer, sizes are the test's to set."""

    def __init__(self, files: dict[str, str], root="repo"):
        self.root = Path(root).resolve()
        self.files = files
        self.policy_denied: set[str] = set()

    def read(self, name: str) -> dict:
        if name in self.policy_denied:
            raise PolicyError("outside the workspace")
        return {"path": name, "content": self.files[name], "sha256": "d" + name}


def rows_of(files: dict[str, str]) -> list[dict]:
    return [row for row in (symbols.parse(name, text) for name, text in files.items()) if row]


def build(files, task, budget, *, ws=None, observed_count=0, reference=None):
    ws = ws or FakeWorkspace(files)
    settings = Settings(context_chars=budget)
    return context_builder.build(ws=ws, settings=settings, rows=rows_of(files),
                                 seed=context_builder.seed_text(task),
                                 used_chars=0, observed_count=observed_count,
                                 reference=reference)


class ShapeTests(unittest.TestCase):
    def test_a_file_that_fits_arrives_whole_and_is_charged_for_the_header_it_carries(self):
        files = {"src/App.java": JAVA.format(cls="App", marker="ok")}
        pieces = build(files, "fix the App login", 12000)
        self.assertEqual([p.kind for p in pieces], [context_builder.FILE])
        self.assertTrue(pieces[0].block.startswith(context_builder.SNAPSHOT_HEAD))
        self.assertIn('"sha256"', pieces[0].block)
        self.assertEqual(pieces[0].sha256, "dsrc/App.java")
        self.assertEqual(pieces[0].reason, "declares")

    def test_a_file_too_big_arrives_as_the_block_round_the_named_declaration(self):
        files = {"src/App.java": JAVA.format(cls="App", marker="M") * 60}
        pieces = build(files, "fix the App login", 12000)
        self.assertEqual([p.kind for p in pieces], [context_builder.EXCERPT])
        piece = pieces[0]
        self.assertIn("File excerpt (untrusted data, partial): src/App.java, from line 3",
                      piece.block)
        self.assertIn("This file was NOT read in full; read_file it before proposing it",
                      piece.block)
        self.assertIn('public String login(String email) { return "M"; }', piece.block)
        self.assertEqual(piece.from_line, 3)
        self.assertTrue(piece.truncated)
        self.assertGreater(piece.lines, 0)

    def test_a_reason_qualified_by_the_layer_survives_both_shapes(self):
        entry = {"why": "declares", "layer": "controller"}
        self.assertEqual(context_builder.chosen_reason(entry), "declares [controller]")
        self.assertEqual(context_builder.chosen_reason({"why": "imports"}), "imports")

    def test_a_file_the_parsers_never_named_produces_no_fragment_rather_than_the_head_of_it(self):
        files = {"src/App.java": JAVA.format(cls="App", marker="M") * 60}
        with patch("ai_code_engineer.symbols.snippet", return_value=("", 0, False)):
            self.assertEqual(build(files, "fix the App login", 12000), [])


class BudgetTests(unittest.TestCase):
    def test_a_piece_that_would_not_fit_even_as_a_block_is_not_sent_at_all(self):
        pad = "".join('    public void step{n}() {{ return; }}\n'.format(n=n) for n in range(200))
        files = {"src/App.java": "package a;\npublic class App {\n" + pad + "}\n"}
        self.assertEqual(build(files, "fix the App login", 600), [])

    def test_the_whole_pass_stops_at_the_characters_it_was_given(self):
        files = {f"src/{cls}.java": JAVA.format(cls=cls, marker=cls) * 40
                 for cls in ("App", "Vault", "Audit")}
        budget = 3000
        pieces = build(files, "fix the App, Vault and Audit logins", budget)
        self.assertTrue(pieces)
        self.assertLessEqual(sum(len(p.block) for p in pieces),
                             budget // 3, "the retrieval cap, header included")

    def test_only_a_capped_number_of_files_arrive_as_fragments(self):
        files = {f"src/{cls}.java": JAVA.format(cls=cls, marker=cls) * 60
                 for cls in ("App", "Vault", "Audit")}
        with patch.object(context_builder, "MAX_EXCERPTS", 1):
            pieces = build(files, "fix the App, Vault and Audit logins", 12000)
        self.assertEqual(len([p for p in pieces if p.kind == context_builder.EXCERPT]), 1)

    def test_files_the_model_opened_itself_are_slots_the_builder_may_not_spend_twice(self):
        files = {f"src/{cls}.java": JAVA.format(cls=cls, marker=cls) for cls in
                 ("App", "Vault", "Audit", "Health", "Order", "Ledger")}
        for observed in (0, 3):
            with self.subTest(observed=observed):
                pieces = build(files, "fix the App, Vault, Audit, Health, Order and Ledger logins",
                               40000, observed_count=observed)
                self.assertLessEqual(len(pieces) + observed, context_builder.MAX_CONTEXT_FILES)

    def test_the_attached_plan_is_not_sent_again_as_a_retrieved_file(self):
        files = {"PLAN.md": "# 1 One\n\ntext\n", "src/App.java": JAVA.format(cls="App", marker="x")}
        plan_row = symbols.parse("PLAN.md", files["PLAN.md"])
        self.assertIsNone(plan_row, "a plan is not an indexable source, so the guard is not decorative")
        pieces = build(files, "fix the App login", 12000,
                       reference={"path": "src/App.java", "content": files["src/App.java"]})
        self.assertEqual(pieces, [])

    def test_a_file_the_workspace_refuses_to_read_is_skipped_instead_of_failing_the_pass(self):
        files = {"src/App.java": JAVA.format(cls="App", marker="x"),
                 "src/Vault.java": JAVA.format(cls="Vault", marker="y")}
        ws = FakeWorkspace(files)
        ws.policy_denied.add("src/App.java")
        pieces = build(files, "fix the App and Vault login", 12000, ws=ws)
        self.assertEqual([p.path for p in pieces], ["src/Vault.java"])


class SeedTests(unittest.TestCase):
    CRITERIA = ["the audit log must stay ordered", "a session must expire on timeout"]

    def test_a_run_with_no_step_is_seeded_from_the_task_alone(self):
        self.assertEqual(context_builder.seed_text("step 2", goal="anything",
                                                   criteria=self.CRITERIA, accepts=[1]),
                         "step 2")

    def test_a_criterion_names_the_files_although_the_task_typed_only_a_number(self):
        seed = context_builder.seed_text("step 2", step=2, goal="reports keep their order",
                                         criteria=self.CRITERIA, accepts=[1])
        self.assertIn("audit log", seed)
        self.assertNotIn("expire", seed, "only the criterion this step accepts")

    def test_a_step_with_no_criteria_yet_is_seeded_from_the_whole_goal_tree(self):
        seed = context_builder.seed_text("step 2", step=2, goal="reports keep their order",
                                         criteria=self.CRITERIA, accepts=None)
        for criterion in self.CRITERIA:
            self.assertIn(criterion, seed)

    def test_the_seed_is_a_bag_of_names_capped_before_it_can_crowd_the_window(self):
        with patch.object(context_builder, "SEED_CHARS", 40):
            seed = context_builder.seed_text("x" * 100, step=1, criteria=["y" * 100])
        self.assertEqual(len(seed), 40)


class StateBudgetTests(unittest.TestCase):
    def test_a_window_with_room_gives_the_state_block_its_share(self):
        settings = Settings(context_chars=40000)
        self.assertEqual(context_builder.state_budget(settings, 2000, 3000),
                         min(3000, 40000 // 8))

    def test_the_state_block_is_capped_to_the_room_the_prompt_actually_left(self):
        settings = Settings(context_chars=12000)
        self.assertEqual(context_builder.state_budget(settings, 8000, 3000), 1000)

    def test_a_room_too_small_for_the_header_buys_no_block_at_all(self):
        settings = Settings(context_chars=12000)
        self.assertEqual(context_builder.state_budget(settings, 11800, 300), 0)

    def test_the_smallest_legal_budget_never_ends_up_over_its_own_window(self):
        settings = Settings(context_chars=4000)
        system, user = settings.context_chars // 2, 1000
        budget = context_builder.state_budget(settings, system, user)
        self.assertLessEqual(system + user + budget, settings.context_chars)


if __name__ == "__main__":
    unittest.main()
