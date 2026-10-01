"""The ledger is the only thing that decides whether a plan step is finished."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer import planbook
from ai_code_engineer.errors import PolicyError
from ai_code_engineer.workspace import Workspace

PLAN = """# Delivery plan

## Phase 1: scaffold
Create the application and its manifest.

## Phase 2: login endpoint
Add the login route and its tests.

## Phase 3: token filter
Validate the token on every request.
"""


def session_with(runs, state="CHECKS_PASSED", run_id="a" * 32):
    return {"id": run_id, "state": state, "created": "2026-09-24T00:00:00+00:00", "runs": runs}


PASSED_PROOF = [{"recipe": "maven-test", "status": "passed", "exit_code": 0,
                 "proof": {"tests": 8, "failures": 0, "errors": 0, "skipped": 0,
                           "source": "surefire XML"}}]


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "plan.md").write_text(PLAN, encoding="utf-8")
        self.plans = root / "plans"
        self.ws = Workspace(self.repo)

    def open(self, plan_file="plan.md"):
        return planbook.open_book(self.plans, self.ws, plan_file)

    def test_numbered_headings_become_ordered_steps(self):
        steps = planbook.parse_steps(PLAN)
        self.assertEqual([row["title"] for row in steps],
                         ["scaffold", "login endpoint", "token filter"])
        self.assertIn("login route", planbook.parse_steps(PLAN)[1]["body"])

    def test_a_plan_without_headings_is_one_step(self):
        steps = planbook.parse_steps("Fix the rounding error in totals.\n")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["id"], 1)
        with self.assertRaises(PolicyError):
            planbook.parse_steps("   ")

    def test_the_ledger_survives_reopening_the_same_plan(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "s" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="s" * 32))
        _, again = self.open()
        self.assertEqual(again["steps"][0]["status"], "verified")
        self.assertEqual(planbook.current(again)["id"], 2)

    def test_editing_the_plan_starts_a_fresh_ledger(self):
        first, _ = self.open()
        (self.repo / "plan.md").write_text(PLAN + "\n## Phase 4: logout\nDrop the session.\n",
                                          encoding="utf-8")
        second, book = self.open()
        self.assertNotEqual(first, second)
        self.assertEqual(len(book["steps"]), 4)
        self.assertFalse(first.exists())

    def test_a_verified_step_does_not_cross_into_another_folder_of_the_same_name(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "s" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="s" * 32))
        twin = self.repo.parent / "elsewhere" / self.repo.name        # same name, other folder
        twin.mkdir(parents=True)
        (twin / "plan.md").write_text(PLAN, encoding="utf-8")
        twin_path, twin_book = planbook.open_book(self.plans, Workspace(twin), "plan.md")
        self.assertNotEqual(twin_path, path, "one ledger cannot serve two folders")
        self.assertEqual([row["status"] for row in twin_book["steps"]], ["pending"] * 3)

    def test_a_ledger_that_names_another_folder_is_not_read_as_this_one(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "s" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="s" * 32))
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["root"] = str(self.repo.parent / "moved-away")
        path.write_text(json.dumps(stored), encoding="utf-8")
        _, reread = self.open()
        self.assertEqual([row["status"] for row in reread["steps"]], ["pending"] * 3,
                         "a ledger whose root disagrees restarts instead of being trusted")

    def test_a_step_opens_only_for_the_session_recorded_against_it(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        with self.assertRaises(PolicyError):
            planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="2" * 32))
        self.assertEqual(planbook.step(book, 1)["status"], "in_progress")

    def test_a_fix_round_can_finish_the_step_it_was_rebound_to(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.record_session(path, book, 1, "2" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="2" * 32))
        self.assertEqual(planbook.step(book, 1)["status"], "verified")

    def test_green_exit_without_a_test_report_proves_nothing(self):
        cases = {
            "the session is not in CHECKS_PASSED": session_with(PASSED_PROOF, "APPLIED_UNVERIFIED"),
            "no command has been recorded for this session": session_with([]),
            "the last command did not pass": session_with([{"status": "failed", "recipe": "maven-test"}]),
            "the test report shows failures or errors": session_with(
                [{"status": "passed", "recipe": "maven-test",
                  "proof": {"tests": 8, "failures": 2, "errors": 0, "skipped": 0}}]),
            "the test report contains no executed test": session_with(
                [{"status": "passed", "recipe": "maven-test",
                  "proof": {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}}]),
        }
        for reason, session in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(planbook.proof_reason(session), reason)

    def test_a_compile_only_run_proves_the_build_without_tests(self):
        session = session_with([{"recipe": "maven-compile", "status": "passed", "tests_observed": False}])
        self.assertEqual(planbook.proof_reason(session), "")

    def test_a_refused_completion_leaves_the_step_open(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        with self.assertRaises(PolicyError):
            planbook.complete(path, book, 1, session_with([{"status": "unverified", "recipe": "maven-test"}],
                                                          run_id="1" * 32))
        self.assertEqual(planbook.step(book, 1)["status"], "in_progress")
        self.assertEqual(json.loads(path.read_text())["steps"][0]["status"], "in_progress")

    def test_the_next_task_names_finished_work_and_keeps_the_note(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="1" * 32))
        row = planbook.current(book)
        task = planbook.task_for(book, row, note="keep the endpoint path unchanged")
        self.assertIn("Implement step 2 of 3", task)
        self.assertIn("do not redo", task)
        self.assertIn("scaffold", task)
        self.assertIn("keep the endpoint path unchanged", task)
        self.assertNotIn("Create the application and its manifest", task)
        self.assertLessEqual(len(task), 4000)

    def test_progress_line_and_rollback_reopen(self):
        path, book = self.open()
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="1" * 32))
        self.assertIn("Plan step 2/3", planbook.progress_line(book))
        planbook.reopen(path, book, 1)
        self.assertEqual(planbook.step(book, 1)["status"], "in_progress")
        self.assertIsNone(planbook.step(book, 1)["verified_at"])
        self.assertIn("Plan step 1/3", planbook.progress_line(book))

    def test_the_ledger_is_keyed_by_the_plan_not_its_path_spelling(self):
        by_name, first = self.open("plan.md")
        by_path, second = self.open(str(self.repo / "plan.md"))
        self.assertEqual(by_name, by_path)
        self.assertEqual(first["steps"][1]["title"], second["steps"][1]["title"])
        with self.assertRaises(PolicyError):
            self.open(str(self.plans / "elsewhere.md"))

    def test_a_green_run_without_headings_is_one_verified_step(self):
        (self.repo / "flat.md").write_text("Fix the rounding error in totals.\n", encoding="utf-8")
        path, book = self.open("flat.md")
        planbook.record_session(path, book, 1, "1" * 32)
        planbook.complete(path, book, 1, session_with(PASSED_PROOF, run_id="1" * 32))
        self.assertIn("Plan complete", planbook.progress_line(book))

    def test_an_unknown_step_is_refused(self):
        path, book = self.open()
        with self.assertRaises(PolicyError):
            planbook.record_session(path, book, 99, "1" * 32)
        with self.assertRaises(PolicyError):
            planbook.complete(path, book, 99, session_with(PASSED_PROOF))

    def test_ledgers_are_outside_the_project_and_keyed_by_its_name(self):
        path, _ = self.open()
        planbook.atomic_json(path, {"schema": 1})
        self.assertTrue(path.is_relative_to(self.plans))
        self.assertEqual(path.name.split("-")[0], "repo")
        self.assertNotIn(self.repo, path.parents)

    def test_a_ledger_written_by_an_older_version_is_replaced(self):
        path, book = self.open()
        planbook.atomic_json(path, {"schema": 99, "steps": []})
        _, reloaded = self.open()
        self.assertEqual(len(reloaded["steps"]), 3)
        self.assertEqual(reloaded["schema"], 1)
        planbook.record_session(path, reloaded, 1, "1" * 32)
        self.assertEqual(json.loads(path.read_text())["steps"][0]["status"], "in_progress")


class StepLimitTests(unittest.TestCase):
    def test_a_long_plan_is_rejected_without_silent_truncation(self):
        text = "\n".join(f"## Phase {n}: step {n}\nbody {n}" for n in range(1, 60))
        with self.assertRaisesRegex(PolicyError, "59 steps; the limit is 50"):
            planbook.parse_steps(text)

    def test_lists_are_sequential_and_stop_at_sections(self):
        rows = planbook.parse_steps("# Plan\n## Tasks\n1. **Setup**\n   - Maven\n   1. Nested\n"
                                    "7) Login\n   - API\n## Details\n7. **JWT**\n   - Tokens\nAfterword")
        self.assertEqual([r['id'] for r in rows], [1, 2, 3])
        self.assertEqual([r['title'] for r in rows], ['Setup', 'Login', 'JWT'])
        self.assertIn('Nested', rows[0]['body'])
        self.assertEqual(rows[1]['body'], '- API')
        self.assertEqual(rows[2]['body'], '- Tokens')

    def test_heading_does_not_consume_next_line(self):
        text = "## Tasks\n1. **Setup**\n   - Maven\n"
        self.assertEqual(list(planbook.STEP_HEADING.finditer(text)), [])
        rows = planbook.parse_steps(text)
        self.assertEqual(rows[0]['title'], 'Setup')
        self.assertEqual(rows[0]['body'], '- Maven')
        rows = planbook.parse_steps("## 1.\nBody on next line")
        self.assertEqual(rows[0]['title'], 'step 1')
        self.assertEqual(rows[0]['body'], 'Body on next line')

    def test_one_heading_wins_over_lists_and_code_examples(self):
        rows = planbook.parse_steps("```md\n## 9. Example\n```\n## 1. Real\n1. Detail\n2. Detail two")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['title'], 'Real')
        self.assertIn('Detail two', rows[0]['body'])
        self.assertEqual(len(planbook.parse_steps("1) Real\n   ```\n   2. Example\n   ```\n")), 1)

    def test_solitary_year_sentence_is_plain_text(self):
        text = '2024. was a bad year'
        self.assertEqual(planbook.parse_steps(text)[0]['body'], text)

    def test_list_limit_and_windows_newlines(self):
        text = '\r\n'.join(f'{i}. **Task {i}**\r\n   - Work' for i in range(1, 55))
        with self.assertRaisesRegex(PolicyError, '54 steps; the limit is 50'):
            planbook.parse_steps(text)
        self.assertEqual(len(planbook.parse_steps('\n'.join(f'{i}) Task' for i in range(1, 51)))), 50)


class LegacyLedgerTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    open = LedgerTests.open

    def legacy(self, status='pending', session_id=None):
        text = '# Plan\n## Tasks\n1. **Setup**\n   - Maven\n2. Login\n   - API'
        (self.repo / 'plan.md').write_text(text, encoding='utf-8')
        text = self.ws.read('plan.md')['content'].strip()
        path, book = self.open()
        book['steps'] = [{'id': 1, 'title': '# Plan', 'body': text,
                          'status': status, 'session_id': session_id, 'verified_at': None}]
        planbook.atomic_json(path, book)
        return path, path.read_bytes()

    def test_untouched_fallback_is_rebuilt_without_overwriting_old_file(self):
        path, saved = self.legacy()
        _, book = self.open()
        self.assertEqual([r['title'] for r in book['steps']], ['Setup', 'Login'])
        self.assertEqual(path.read_bytes(), saved)
        planbook.record_session(path, book, 1, 'new-session')
        self.assertEqual(len(self.open()[1]['steps']), 2)

    def test_linked_or_verified_fallback_is_preserved_and_blocks_restart(self):
        for status, session in [('in_progress', None), ('pending', 'session'), ('verified', None)]:
            with self.subTest(status=status):
                # Remove only the test fixture so each case can seed its own legacy ledger.
                for path in self.plans.glob('*.json'):
                    path.unlink()
                path, saved = self.legacy(status, session)
                with self.assertRaisesRegex(PolicyError, 'progress was preserved'):
                    self.open()
                self.assertEqual(path.read_bytes(), saved)

    def test_cached_fallback_cannot_bypass_the_step_limit(self):
        text = '# Plan\n' + '\n'.join(f'{i}. Task {i}' for i in range(1, 55))
        (self.repo / 'plan.md').write_text(text, encoding='utf-8')
        reference = self.ws.read('plan.md')
        path = self.plans / (planbook.key_for(str(self.ws.root), reference['sha256']) + '.json')
        planbook.atomic_json(path, {'schema': 1, 'root': str(self.ws.root),
            'plan_sha256': reference['sha256'], 'steps': [{'id': 1, 'title': '# Plan',
                'body': reference['content'].strip(), 'status': 'in_progress', 'session_id': 'old'}]})
        saved = path.read_bytes()
        with self.assertRaisesRegex(PolicyError, '54 steps; the limit is 50'):
            self.open()
        self.assertEqual(path.read_bytes(), saved)


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, messages, cancelled=None):
        self.calls.append(list(messages))
        if not self.responses:
            return "{}"
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class GoalEngineTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    open = LedgerTests.open

    def test_validate_tree_valid(self):
        tree = {
            "goal": "Build authentication with JWT tokens.",
            "criteria": ["Login endpoint returns 200 with JWT", "Invalid credentials return 401"],
            "sub_goals": [{"id": 1, "title": "Auth API"}],
            "steps": [{"id": 1, "accepts": [1, 2], "sub_goal": 1},
                      {"id": 2, "accepts": [1], "sub_goal": 1},
                      {"id": 3, "accepts": [2], "sub_goal": None}],
        }
        res = planbook.validate_tree(tree, [1, 2, 3])
        self.assertEqual(res["goal"], "Build authentication with JWT tokens.")
        self.assertEqual(len(res["criteria"]), 2)
        self.assertEqual(len(res["sub_goals"]), 1)
        self.assertEqual(len(res["steps"]), 3)

    def test_validate_tree_rejections(self):
        with self.assertRaisesRegex(PolicyError, "one JSON object"):
            planbook.validate_tree(["not", "a", "dict"], [1])

        with self.assertRaisesRegex(PolicyError, "needs goal and criteria"):
            planbook.validate_tree({"goal": "Only goal"}, [1])

        with self.assertRaisesRegex(PolicyError, "goal must be one sentence"):
            planbook.validate_tree({"goal": "", "criteria": ["Works"]}, [1])

        with self.assertRaisesRegex(PolicyError, "goal must be one sentence"):
            planbook.validate_tree({"goal": "x" * 350, "criteria": ["Works"]}, [1])

        with self.assertRaisesRegex(PolicyError, "criteria must be 1-8"):
            planbook.validate_tree({"goal": "Valid", "criteria": []}, [1])

        with self.assertRaisesRegex(PolicyError, "criteria must be 1-8"):
            planbook.validate_tree({"goal": "Valid", "criteria": ["c"] * 9}, [1])

        with self.assertRaisesRegex(PolicyError, "sub_goals must be a list"):
            planbook.validate_tree({"goal": "Valid", "criteria": ["c"], "sub_goals": "bad"}, [1])

        with self.assertRaisesRegex(PolicyError, "sub_goal ids must be unique"):
            planbook.validate_tree({"goal": "Valid", "criteria": ["c"],
                                    "sub_goals": [{"id": 1, "title": "A"}, {"id": 1, "title": "B"}]}, [1])

        with self.assertRaisesRegex(PolicyError, "must name exactly the plan's step numbers"):
            planbook.validate_tree({"goal": "Valid", "criteria": ["c"],
                                    "steps": [{"id": 99, "accepts": [1]}]}, [1, 2])

    def test_author_goal_happy_path(self):
        path, book = self.open()
        payload = json.dumps({
            "goal": "Deliver scaffold and auth.",
            "criteria": ["App runs and passes unit tests", "Login endpoint returns JWT"],
            "sub_goals": [{"id": 1, "title": "Scaffolding"}, {"id": 2, "title": "Auth"}],
            "steps": [{"id": 1, "accepts": [1], "sub_goal": 1},
                      {"id": 2, "accepts": [2], "sub_goal": 2},
                      {"id": 3, "accepts": [1, 2], "sub_goal": 2}],
        })
        provider = FakeProvider([payload])
        authored, refusal = planbook.author_goal(path, book, provider, task="Add login")
        self.assertEqual(refusal, "")
        self.assertEqual(authored["schema"], 2)
        self.assertEqual(authored["goal"], "Deliver scaffold and auth.")
        self.assertEqual(authored["steps"][0]["accepts"], [1])
        self.assertEqual(authored["steps"][0]["sub_goal"], 1)

        # Re-authoring when goal is already present returns book untouched without calling provider
        again, ref = planbook.author_goal(path, authored, provider)
        self.assertEqual(ref, "")
        self.assertEqual(len(provider.calls), 1)

    def test_author_goal_retry_and_exhaustion(self):
        path, book = self.open()
        bad_payload = "not json at all"
        valid_payload = json.dumps({
            "goal": "Fixed on retry.",
            "criteria": ["Criterion 1"],
            "steps": [{"id": 1, "accepts": [1]}, {"id": 2, "accepts": [1]}, {"id": 3, "accepts": [1]}],
        })
        provider = FakeProvider([bad_payload, valid_payload])
        authored, refusal = planbook.author_goal(path, book, provider, attempts=2)
        self.assertEqual(refusal, "")
        self.assertEqual(authored["goal"], "Fixed on retry.")
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("Tool observation", provider.calls[1][-1]["content"])

        # Exhaustion test
        path2, book2 = self.open()
        book2["goal"] = ""
        failing_provider = FakeProvider(["bad1", "bad2"])
        _, refusal2 = planbook.author_goal(path2, book2, failing_provider, attempts=2)
        self.assertTrue(refusal2)

    def test_author_goal_cancelled(self):
        path, book = self.open()
        provider = FakeProvider(["{}"])
        _, refusal = planbook.author_goal(path, book, provider, cancelled=lambda: True)
        self.assertIn("cancelled", refusal)

    def test_criteria_and_queries(self):
        path, book = self.open()
        book.update({
            "goal": "Build robust auth.",
            "criteria": ["Pass tests", "Return JWT", "Block unauthorized"],
            "sub_goals": [{"id": 1, "title": "Core Auth"}],
            "steps": [
                {"id": 1, "title": "Scaffold", "accepts": [1], "sub_goal": 1, "status": "pending"},
                {"id": 2, "title": "Login", "accepts": [2], "sub_goal": 1, "status": "pending"},
                {"id": 3, "title": "Filter", "accepts": [], "sub_goal": None, "status": "pending"},
            ]
        })
        self.assertTrue(planbook.has_goal(book))
        self.assertEqual(planbook.goal_line(book), "Build robust auth.")
        self.assertEqual(planbook.criteria_of(book), ["Pass tests", "Return JWT", "Block unauthorized"])
        self.assertEqual(planbook.criteria_of(book, book["steps"][0]), ["Pass tests"])
        self.assertEqual(planbook.criteria_of(book, book["steps"][1]), ["Return JWT"])
        self.assertEqual(planbook.criteria_of(book, book["steps"][2]), [])
        self.assertEqual(planbook.uncovered_criteria(book), [3])
        self.assertEqual(planbook.sub_goal_of(book, book["steps"][0]), "Core Auth")
        self.assertEqual(planbook.sub_goal_of(book, book["steps"][2]), "")

        # Task for includes goal and criteria
        task_text = planbook.task_for(book, book["steps"][0])
        self.assertIn("The plan's goal is: Build robust auth.", task_text)
        self.assertIn("- Pass tests", task_text)

    def test_mark_failed_and_progress_line(self):
        path, book = self.open()
        self.assertEqual(planbook.failed_count(book), 0)
        self.assertIn("(0 verified)", planbook.progress_line(book))

        planbook.mark_failed(path, book, 1, "Build compilation failed with exit code 1")
        self.assertEqual(book["steps"][0]["status"], "failed")
        self.assertEqual(book["steps"][0]["failure_reason"], "Build compilation failed with exit code 1")
        self.assertEqual(planbook.failed_count(book), 1)
        self.assertIn("(0 verified, 1 failed)", planbook.progress_line(book))

        # Verified step cannot be overwritten with failed
        book["steps"][0]["status"] = "verified"
        planbook.mark_failed(path, book, 1, "Should not overwrite")
        self.assertEqual(book["steps"][0]["status"], "verified")

    def test_mark_unproven(self):
        path, book = self.open()
        planbook.mark_unproven(path, book, 1, "User manual verification without test run")
        self.assertEqual(book["steps"][0]["status"], "verified")
        self.assertEqual(book["steps"][0]["unproven"], "User manual verification without test run")
        self.assertIsNotNone(book["steps"][0]["verified_at"])
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["steps"][0]["unproven"], "User manual verification without test run")


if __name__ == "__main__":
    unittest.main()
