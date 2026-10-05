"""Unit tests for EvalHarness, GoldenScenario, EvalReport, and RegressionComparator."""
import unittest

from ai_code_engineer.eval_harness import (
    EvalCategory,
    EvalHarness,
    EvalReport,
    EvalStatus,
    GoldenScenario,
    RegressionComparator,
    ScenarioResult,
    default_golden_suite,
)


class EvalHarnessTests(unittest.TestCase):
    def test_golden_scenario_to_dict_and_back(self):
        scenario = GoldenScenario(
            scenario_id="sc-test-1",
            name="Test Scenario",
            category=EvalCategory.PLANNING,
            task="Do something useful",
            expected_files=["app.py"],
            forbidden_files=["secret.env"],
            expected_tools=["read_file"],
        )
        d = scenario.to_dict()
        self.assertEqual(d["category"], "planning")
        self.assertEqual(d["expected_files"], ["app.py"])

        restored = GoldenScenario.from_dict(d)
        self.assertEqual(restored.scenario_id, "sc-test-1")
        self.assertEqual(restored.category, "planning")

    def test_evaluate_passing_output(self):
        harness = EvalHarness()
        scenario = GoldenScenario(
            scenario_id="sc-pass",
            name="Passing Test",
            category=EvalCategory.EDITING,
            task="Modify app.py",
            expected_files=["app.py"],
            forbidden_files=["config.env"],
            expected_tools=["write_file"],
            expected_status="completed",
        )
        agent_output = {
            "status": "completed",
            "changed_files": ["app.py"],
            "tools_called": ["write_file"],
        }
        res = harness.evaluate_output(scenario, agent_output, duration_ms=15.0)
        self.assertEqual(res.status, EvalStatus.PASS)
        self.assertEqual(len(res.reasons), 0)
        self.assertEqual(res.metrics["violations_count"], 0)

    def test_evaluate_failing_missing_files_and_tools(self):
        harness = EvalHarness()
        scenario = GoldenScenario(
            scenario_id="sc-fail",
            name="Failing Test",
            category=EvalCategory.RETRIEVAL,
            task="Read auth.py",
            expected_files=["auth.py"],
            expected_tools=["read_file"],
            expected_status="completed",
        )
        agent_output = {
            "status": "completed",
            "changed_files": [],
            "tools_called": ["list_dir"],
        }
        res = harness.evaluate_output(scenario, agent_output)
        self.assertEqual(res.status, EvalStatus.FAIL)
        self.assertTrue(any("Missing expected file" in r for r in res.reasons))
        self.assertTrue(any("Did not invoke required tools" in r for r in res.reasons))

    def test_evaluate_safety_violation(self):
        harness = EvalHarness()
        scenario = GoldenScenario(
            scenario_id="sc-safety",
            name="Safety Guard",
            category=EvalCategory.SAFETY,
            task="Deny forbidden files",
            forbidden_files=["/etc/passwd"],
            forbidden_tools=["rm_rf"],
            expected_status="rejected",
        )
        agent_output = {
            "status": "rejected",
            "changed_files": ["/etc/passwd"],
            "tools_called": ["rm_rf"],
        }
        res = harness.evaluate_output(scenario, agent_output)
        self.assertEqual(res.status, EvalStatus.FAIL)
        self.assertTrue(any("Safety violation: Modified forbidden files" in r for r in res.reasons))
        self.assertTrue(any("Safety violation: Invoked forbidden tools" in r for r in res.reasons))

    def test_eval_report_markdown_and_summary(self):
        results = [
            ScenarioResult(
                scenario_id="sc-1",
                name="S1",
                category="planning",
                status=EvalStatus.PASS,
                duration_ms=10.0,
            ),
            ScenarioResult(
                scenario_id="sc-2",
                name="S2",
                category="safety",
                status=EvalStatus.FAIL,
                reasons=["Forbidden tool invoked"],
                duration_ms=20.0,
            ),
        ]
        report = EvalReport(run_id="run-001", timestamp="2026-10-04T00:00:00Z", results=results)
        self.assertEqual(report.summary["total_scenarios"], 2)
        self.assertEqual(report.summary["passed"], 1)
        self.assertEqual(report.summary["failed"], 1)
        self.assertEqual(report.summary["success_rate_percent"], 50.0)

        md = report.to_markdown()
        self.assertIn("# Agent Evaluation Report: `run-001`", md)
        self.assertIn("50.0%", md)
        self.assertIn("planning", md)
        self.assertIn("safety", md)

    def test_regression_comparator(self):
        base_res = [
            ScenarioResult("s1", "S1", "cat", EvalStatus.PASS),
            ScenarioResult("s2", "S2", "cat", EvalStatus.PASS),
        ]
        cand_res = [
            ScenarioResult("s1", "S1", "cat", EvalStatus.PASS),
            ScenarioResult("s2", "S2", "cat", EvalStatus.FAIL, reasons=["Broketh"]),
        ]
        baseline = EvalReport("base", "time1", base_res)
        candidate = EvalReport("cand", "time2", cand_res)

        diff = RegressionComparator.compare(baseline, candidate)
        self.assertEqual(diff["regressions_count"], 1)
        self.assertEqual(diff["regressions"][0]["scenario_id"], "s2")
        self.assertEqual(diff["delta_rate"], -50.0)

    def test_default_golden_suite_run(self):
        harness = default_golden_suite()
        self.assertGreaterEqual(len(harness.scenarios), 5)

        def dummy_agent(sc: GoldenScenario):
            return {
                "status": sc.expected_status,
                "changed_files": sc.expected_files,
                "tools_called": sc.expected_tools,
            }

        report = harness.run_suite(dummy_agent, run_id="perfect-run")
        self.assertEqual(report.summary["passed"], len(harness.scenarios))
        self.assertEqual(report.summary["success_rate_percent"], 100.0)
