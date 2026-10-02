"""One verdict per acceptance criterion, computed from the ledger the steps already keep.

The claim these tests hold is narrow and is the whole of roadmap item #12: a criterion is `verified` only
when a step that answers it was closed by a command run that proved it, and a step a person closed by hand
does not become proof by being clicked. Nothing here asks the model how it did — the sentence that grades a
plan has to come out of the record, or it is an opinion with a colour on it.

The ledger is the only input, which is also why the function is safe to call on every snapshot: a step row
already carries the status, the session that proved it, when, and the mark that says a person closed it
instead.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_code_engineer import labels, planbook
from ai_code_engineer.webapp.fake import FakeController

CRITERIA = ["The endpoint answers", "The tests still pass", "No secret is committed"]


def book(steps: list, criteria=None) -> dict:
    """A ledger with the given step rows, as `author_goal` would leave it. Pass `criteria=[]` for a plan
    that was authored without any, which is a different record from one that has the default three."""
    given = CRITERIA if criteria is None else criteria
    return {"schema": 2, "root": "/repo", "plan_path": "plan.md", "plan_sha256": "0" * 64,
            "goal": "Ship the endpoint", "criteria": list(given), "sub_goals": [],
            "steps": steps}


def row(ident: int, accepts: list, status: str = "pending", **extra) -> dict:
    built = {"id": ident, "title": "Step %d" % ident, "body": "", "status": status,
             "session_id": None, "verified_at": None, "accepts": accepts, "sub_goal": None}
    built.update(extra)
    return built


def verdict_of(rows: list, number: int) -> dict:
    found = {item["number"]: item for item in planbook.criterion_verdicts(book(rows))}
    return found[number]


class TheFourAnswers(unittest.TestCase):
    """What a criterion can be told, and the record shape that earns each answer."""

    def test_a_step_that_a_run_proved_verifies_the_criteria_it_answers(self):
        rows = [row(1, [1, 2], "verified", session_id="a1", verified_at="2026-10-01 09:00")]
        for number in (1, 2):
            verdict = verdict_of(rows, number)
            self.assertEqual(verdict["verdict"], "verified", number)
            self.assertEqual(verdict["why"], "proved", number)
            self.assertEqual(verdict["at"], "2026-10-01 09:00", number)

    def test_a_step_closed_by_a_click_leaves_its_criterion_unproven(self):
        """The case the whole feature is for: the row says `verified`, and the truth is beside it."""
        rows = [row(1, [1], "verified", session_id="a1", verified_at="2026-10-01 09:00",
                    unproven="the operator said so")]
        verdict = verdict_of(rows, 1)
        self.assertEqual(verdict["verdict"], "unproven")
        self.assertEqual(verdict["why"], "clicked")
        self.assertEqual(verdict["detail"], "the operator said so")

    def test_a_step_whose_run_did_not_prove_it_answers_failed(self):
        rows = [row(1, [1], "failed", failure_reason="2 tests still fail")]
        verdict = verdict_of(rows, 1)
        self.assertEqual(verdict["verdict"], "failed")
        self.assertEqual(verdict["detail"], "2 tests still fail")

    def test_a_step_nobody_has_run_yet_is_not_a_failure(self):
        verdict = verdict_of([row(1, [1], "in_progress")], 1)
        self.assertEqual((verdict["verdict"], verdict["why"]), ("unproven", "not_run"))

    def test_a_criterion_no_step_claims_says_so_rather_than_waiting(self):
        verdict = verdict_of([row(1, [1])], 3)
        self.assertEqual((verdict["verdict"], verdict["why"]), ("unproven", "uncovered"))
        self.assertTrue(verdict["uncovered"])
        self.assertEqual(verdict["steps"], [])

    def test_proof_wins_over_a_sibling_that_failed(self):
        """Two steps answer one criterion; a run proved one of them. The criterion is proved, and the
        other step's failure stays a fact about that step rather than erasing the proof."""
        rows = [row(1, [1], "failed", failure_reason="not yet"),
                row(2, [1], "verified", session_id="a2", verified_at="2026-10-01 09:30")]
        self.assertEqual(verdict_of(rows, 1)["verdict"], "verified")

    def test_a_failure_outranks_the_not_yet_of_a_second_step(self):
        rows = [row(1, [2], "pending"), row(2, [2], "failed", failure_reason="compile error")]
        self.assertEqual(verdict_of(rows, 2)["verdict"], "failed")

    def test_a_plan_with_no_criteria_answers_nothing(self):
        self.assertEqual(planbook.criterion_verdicts(book([row(1, [])], criteria=[])), [])

    def test_an_accepts_number_out_of_range_claims_no_criterion(self):
        """`planbook` refuses such a tree when it is authored, so a stored one that carries it is an old
        or hand-edited record — and inventing a verdict for criterion 9 of 3 would be a lie."""
        rows = [row(1, [9], "verified", verified_at="now")]
        self.assertEqual(verdict_of(rows, 1)["verdict"], "unproven")

    def test_the_verdict_words_are_only_the_three_the_function_can_answer(self):
        for rows in ([row(1, [1])], [row(1, [1], "verified", verified_at="x")],
                     [row(1, [1], "failed", failure_reason="y")]):
            for item in planbook.criterion_verdicts(book(rows)):
                self.assertIn(item["verdict"], planbook.VERDICTS)
                self.assertIn(item["why"], ("proved", "run_failed", "clicked", "not_run", "uncovered"))


class TheCount(unittest.TestCase):
    def test_the_tally_counts_each_answer_once(self):
        rows = [row(1, [1], "verified", verified_at="x"),
                row(2, [2], "failed", failure_reason="y")]
        tally = planbook.verdict_tally(planbook.criterion_verdicts(book(rows)))
        self.assertEqual(tally, {"total": 3, "verified": 1, "failed": 1, "unproven": 1})

    def test_the_sentence_names_the_number_that_is_proved(self):
        rows = [row(1, [1], "verified", verified_at="x")]
        tally = planbook.verdict_tally(planbook.criterion_verdicts(book(rows)))
        line = labels.note("plan_verdicts_line", proved=tally["verified"], total=tally["total"])
        self.assertIn("1 of 3", line)
        arabic = labels.note("plan_verdicts_line", arabic=True, proved=2, total=3)
        self.assertTrue(labels.is_arabic(arabic))
        self.assertIn("2", arabic)
        self.assertIn("3", arabic)


class TheWords(unittest.TestCase):
    """Every answer has a word in both languages, and neither window writes its own."""

    def test_each_verdict_has_a_word_in_both_languages(self):
        for verdict in planbook.VERDICTS:
            english, arabic_text = labels.NOTE_TEMPLATES["verdict_" + verdict]
            self.assertTrue(labels.is_arabic(arabic_text), verdict)
            self.assertNotEqual(english, arabic_text, verdict)

    def test_each_reason_has_a_word_in_both_languages(self):
        for key in ("verdict_clicked", "verdict_not_run"):
            english, arabic_text = labels.NOTE_TEMPLATES[key]
            self.assertTrue(labels.is_arabic(arabic_text), key)
            self.assertNotEqual(english, arabic_text, key)

    def test_a_proved_criterion_is_not_given_a_reason_to_explain_itself(self):
        self.assertEqual(labels.note("verdict_verified"), "proved")


class NothingAsksTheModel(unittest.TestCase):
    """The grading is arithmetic over the record; the prompt is not part of it."""

    def test_the_prompt_files_never_mention_a_criterion(self):
        src = Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
        for name in ("prompts.py", "refusals.py"):
            text = (src / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("criteri", text, name + " asks the model about acceptance")

    def test_the_verdict_function_opens_nothing_but_the_book_it_is_handed(self):
        src = (Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer" / "planbook.py")
        start = src.read_text(encoding="utf-8").index("def criterion_verdicts")
        body = src.read_text(encoding="utf-8")[start:start + 2600]
        for call in ("atomic_json(", "open(", "load_session(", "runner.run("):
            self.assertNotIn(call, body, "a verdict must be computed from the ledger alone: " + call)


class TheTwoWindowsAnswerIdentically(unittest.TestCase):
    """The scripted preview is the window a design is reviewed in, so it has to answer in the same words."""

    def fake_rows(self) -> list:
        return FakeController().snapshot()["plan"]["verdicts"]

    def test_the_preview_says_which_criteria_a_run_has_not_proved(self):
        rows = self.fake_rows()
        self.assertEqual([row["number"] for row in rows], [1, 2, 3, 4])
        self.assertEqual({row["verdict"] for row in rows}, {"verified", "unproven"})
        self.assertIn("no step answers this yet", [row["why"] for row in rows][-1])

    def test_the_preview_keeps_the_click_and_the_run_apart(self):
        """Step 2 of the scripted plan is closed by a click, so its criterion must not read as proved."""
        controller = FakeController()
        controller.step = 3
        rows = {row["number"]: row for row in controller.snapshot()["plan"]["verdicts"]}
        self.assertEqual(rows[1]["verdict"], "verified", "step 1 proved it with a run")
        self.assertEqual(rows[2]["why"], labels.note("verdict_clicked"))
        self.assertNotEqual(rows[2]["verdict"], "verified")

    def test_the_preview_counts_in_the_same_sentence_the_window_does(self):
        plan = FakeController().snapshot()["plan"]
        tally = planbook.verdict_tally([{"verdict": row["verdict"]} for row in plan["verdicts"]])
        self.assertEqual(plan["verdictNote"],
                         labels.note("plan_verdicts_line", proved=tally["verified"], total=tally["total"]))


class TheClientDrawsWhatTheServerSaid(unittest.TestCase):
    """The rows are arithmetic over the ledger; the client adds a shape and no wording of its own."""

    def setUp(self):
        self.js = (Path(__file__).resolve().parents[1] / "src" / "ai_code_engineer"
                   / "webapp" / "static" / "ui-run.js").read_text(encoding="utf-8")

    def rows(self) -> str:
        start = self.js.index("crit.forEach((row) =>")
        return self.js[start:self.js.index("goal.appendChild(shown)", start)]

    def test_the_row_words_come_from_the_payload(self):
        body = self.rows()
        self.assertIn("esc(row.word)", body)
        self.assertIn("esc(row.why)", body)
        for word in ("proved", "failed", "unproven"):
            self.assertNotIn("'" + word + "'", body, "the client invented a verdict word: " + word)

    def test_a_server_sentence_takes_its_direction_from_itself(self):
        """An Arabic criterion row opens with its number, and a digit is a weak character that cannot
        resolve a paragraph direction — `dir=auto` is how every other server sentence here is drawn."""
        self.assertIn("line.dir = 'auto'", self.rows())
        self.assertIn("note.dir = 'auto'", self.js)


if __name__ == "__main__":
    unittest.main()
