"""Unit tests for TestSelector, SecurityReviewer, PullRequestReviewer, and IDELocalProtocol."""
from pathlib import Path
import unittest

from ai_code_engineer.smart_review import (
    IDELocalProtocol,
    PRVerdict,
    PullRequestReviewer,
    ReviewSeverity,
    SecurityConfidence,
    SecurityFinding,
    SecurityReviewer,
    TestSelector,
)


class SmartReviewTests(unittest.TestCase):
    def test_test_selector_mapping(self):
        all_tests = ["tests/test_auth.py", "tests/test_payment.py", "tests/test_cart.py"]
        selected = TestSelector.select_tests(["src/auth_service.py"], all_tests)
        self.assertEqual(selected, ["tests/test_auth.py"])

    def test_detect_test_tampering(self):
        diff_tamper = (
            "- def test_security_check():\n"
            "-     assert user.is_authenticated\n"
            "+ @pytest.mark.skip\n"
            "+ def test_bypassed():\n"
        )
        warnings = TestSelector.detect_test_tampering(diff_tamper)
        self.assertEqual(len(warnings), 3)
        self.assertTrue(any("Deleted test assertion" in w for w in warnings))
        self.assertTrue(any("Added skip annotation" in w for w in warnings))

    def test_security_reviewer_detects_sqli_and_secrets(self):
        vuln_diff = (
            "+ def get_user(uid):\n"
            "+     cursor.execute(f'SELECT * FROM users WHERE id = {uid}')\n"
            "+     api_key = 'sk-proj-12345678901234567890abcdef'\n"
        )
        findings = SecurityReviewer.scan_diff("src/db.py", vuln_diff)
        self.assertEqual(len(findings), 2)
        cats = [f.category for f in findings]
        self.assertIn("sql_injection", cats)
        self.assertIn("hardcoded_secret", cats)
        self.assertEqual(findings[0].severity, ReviewSeverity.BLOCKER)

    def test_pr_reviewer_blocks_on_security_vulnerabilities(self):
        findings = [
            SecurityFinding(
                category="command_injection",
                severity=ReviewSeverity.BLOCKER,
                confidence=SecurityConfidence.CONFIRMED,
                file_path="src/exec.py",
                line_number=10,
                evidence="shell=True",
                risk_description="Injection",
                remediation="Avoid shell=True",
            )
        ]
        report = PullRequestReviewer.review(["src/exec.py"], "diff", findings, [])
        self.assertEqual(report.verdict, PRVerdict.REQUEST_CHANGES)
        self.assertTrue(report.has_blockers)
        md = report.to_markdown()
        self.assertIn("REQUEST CHANGES", md)
        self.assertIn("COMMAND_INJECTION", md)

    def test_pr_reviewer_approves_clean_code(self):
        report = PullRequestReviewer.review(["src/math.py"], "diff", [], [])
        self.assertEqual(report.verdict, PRVerdict.APPROVE)
        self.assertFalse(report.has_blockers)

    def test_ide_local_protocol(self):
        protocol = IDELocalProtocol()
        pong = protocol.handle_request({"id": 1, "method": "ping"})
        self.assertEqual(pong["result"], "pong")

        caps = protocol.handle_request({"id": 2, "method": "get_capabilities"})
        self.assertTrue(caps["result"]["can_plan"])

        task = protocol.handle_request({"id": 3, "method": "submit_task", "params": {"task": "fix bug"}})
        self.assertEqual(task["result"]["status"], "accepted")
