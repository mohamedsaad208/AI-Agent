"""Unit tests for PolicyEngine, RiskLevel classification, and ApprovalManager."""
import time
import unittest

from ai_code_engineer.errors import PolicyError
from ai_code_engineer.policy import ALLOW, ASK, DENY
from ai_code_engineer.policy_engine import (
    ApprovalManager,
    ApprovalStatus,
    PolicyEngine,
    RiskLevel,
)


class PolicyEngineTests(unittest.TestCase):
    def setUp(self):
        self.approval_mgr = ApprovalManager()
        self.engine = PolicyEngine(self.approval_mgr)

    def test_risk_classification(self):
        self.assertEqual(self.engine.classify_risk("read_file", "app.py"), RiskLevel.SAFE_READ)
        self.assertEqual(self.engine.classify_risk("write_file", "src/auth.py"), RiskLevel.LOCAL_WRITE)
        self.assertEqual(self.engine.classify_risk("write_file", "pom.xml"), RiskLevel.SECURITY_SENSITIVE)
        self.assertEqual(self.engine.classify_risk("run_tests", "pytest"), RiskLevel.LOCAL_EXECUTION)
        self.assertEqual(self.engine.classify_risk("delete_file", "old.log"), RiskLevel.DESTRUCTIVE)
        self.assertEqual(self.engine.classify_risk("run_command", "git push origin main"), RiskLevel.EXTERNAL_SIDE_EFFECT)
        self.assertEqual(self.engine.classify_risk("fetch_url", "https://api.github.com"), RiskLevel.NETWORK_ACCESS)

    def test_read_mode_denies_all_non_reads(self):
        verdict, _ = self.engine.evaluate("write_file", "src/auth.py", autonomy_mode="read")
        self.assertEqual(verdict, DENY)

        verdict, _ = self.engine.evaluate("run_tests", "pytest", autonomy_mode="read")
        self.assertEqual(verdict, DENY)

        verdict, _ = self.engine.evaluate("read_file", "src/auth.py", autonomy_mode="read")
        self.assertEqual(verdict, ALLOW)

    def test_change_mode_allows_safe_writes_and_tests(self):
        verdict, appr = self.engine.evaluate("write_file", "src/service.py", autonomy_mode="change")
        self.assertEqual(verdict, ALLOW)
        self.assertIsNone(appr)

        verdict, appr = self.engine.evaluate("run_tests", "unittest", autonomy_mode="change")
        self.assertEqual(verdict, ALLOW)
        self.assertIsNone(appr)

    def test_sensitive_write_requires_approval(self):
        verdict, appr = self.engine.evaluate("write_file", "package.json", autonomy_mode="change")
        self.assertEqual(verdict, ASK)
        self.assertIsNotNone(appr)
        self.assertEqual(appr.risk_level, RiskLevel.SECURITY_SENSITIVE)
        self.assertIn("package.json", appr.target)

    def test_approval_lifecycle_and_single_use_consumption(self):
        verdict, appr = self.engine.evaluate("delete_file", "data.db", autonomy_mode="change")
        self.assertEqual(verdict, ASK)
        self.assertIsNotNone(appr)
        token = appr.request_id

        # Grant approval
        self.approval_mgr.grant(token)
        self.assertEqual(appr.status, ApprovalStatus.APPROVED)

        # First consumption succeeds
        verdict2, _ = self.engine.evaluate("delete_file", "data.db", autonomy_mode="change", approval_id=token)
        self.assertEqual(verdict2, ALLOW)
        self.assertEqual(appr.status, ApprovalStatus.CONSUMED)

        # Reusing the token must fail (anti-replay)
        verdict3, _ = self.engine.evaluate("delete_file", "data.db", autonomy_mode="change", approval_id=token)
        self.assertEqual(verdict3, DENY)

    def test_expired_approval_rejected(self):
        req = self.approval_mgr.request_approval(
            action="run_command",
            risk_level=RiskLevel.DESTRUCTIVE,
            target="rm -rf /tmp",
            rationale="cleanup",
            ttl_seconds=0.01,
        )
        time.sleep(0.02)
        self.assertTrue(req.is_expired)

        with self.assertRaises(PolicyError):
            self.approval_mgr.grant(req.request_id)
