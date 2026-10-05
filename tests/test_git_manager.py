"""Unit tests for Git-Aware Change Management, Change Ownership, and Policy Gate."""
import unittest

from ai_code_engineer.git_manager import (
    DiffAnalysis,
    GitChangeManager,
    GitPolicyGate,
    WorktreeState,
)
from ai_code_engineer.policy import ALLOW, ASK, DENY


class GitManagerTests(unittest.TestCase):
    def test_worktree_state_snapshot(self):
        state = WorktreeState(
            branch="feature/login",
            head_commit="abc1234",
            is_dirty=True,
            user_modified_files=["src/user.py"],
            untracked_files=["scratch.txt"],
        )
        data = state.to_dict()
        self.assertEqual(data["branch"], "feature/login")
        self.assertTrue(data["is_dirty"])
        self.assertIn("src/user.py", data["user_modified_files"])

    def test_change_ownership_and_rollback_isolation(self):
        initial = WorktreeState(
            branch="main",
            head_commit="commit1",
            is_dirty=True,
            user_modified_files=["unrelated/user_edit.py"],
        )
        manager = GitChangeManager(initial)

        # Agent modifies its own target files
        manager.record_modification("src/auth.py")
        manager.record_modification("tests/test_auth.py")

        self.assertTrue(manager.is_agent_owned("src/auth.py"))
        self.assertFalse(manager.is_agent_owned("unrelated/user_edit.py"))

        # Rollback only targets agent-modified files
        rollback_targets = manager.plan_rollback()
        self.assertEqual(rollback_targets, ["src/auth.py", "tests/test_auth.py"])
        self.assertNotIn("unrelated/user_edit.py", rollback_targets)

    def test_diff_analysis_categorization(self):
        manager = GitChangeManager()
        analysis = manager.analyze_changes(
            changed_files=[
                "src/service.py",
                "tests/test_service.py",
                "package.json",
                "README.md",
            ],
            additions=45,
            deletions=10,
        )
        self.assertEqual(len(analysis.categories["source"]), 1)
        self.assertEqual(len(analysis.categories["test"]), 1)
        self.assertEqual(len(analysis.categories["config"]), 1)
        self.assertEqual(len(analysis.categories["doc"]), 1)
        self.assertFalse(analysis.is_suspiciously_large)

    def test_large_diff_flagged(self):
        manager = GitChangeManager()
        analysis = manager.analyze_changes(
            changed_files=["file.py"],
            additions=1200,
            deletions=500,
        )
        self.assertTrue(analysis.is_suspiciously_large)
        self.assertIn("Suspiciously large diff", analysis.warning)

    def test_git_policy_gate_denies_destructive_operations(self):
        verdict, msg = GitPolicyGate.evaluate("git push origin main --force")
        self.assertEqual(verdict, DENY)
        self.assertIn("Destructive git operation", msg)

        verdict_reset, _ = GitPolicyGate.evaluate("git reset --hard HEAD~1")
        self.assertEqual(verdict_reset, DENY)

        verdict_clean, _ = GitPolicyGate.evaluate("git clean -fd")
        self.assertEqual(verdict_clean, DENY)

    def test_git_policy_gate_permits_reads_and_asks_on_write(self):
        verdict_diff, _ = GitPolicyGate.evaluate("git diff HEAD")
        self.assertEqual(verdict_diff, ALLOW)

        verdict_status, _ = GitPolicyGate.evaluate("git status --porcelain")
        self.assertEqual(verdict_status, ALLOW)

        verdict_push, _ = GitPolicyGate.evaluate("git push origin feature")
        self.assertEqual(verdict_push, ASK)

        verdict_commit, _ = GitPolicyGate.evaluate("git commit -m 'feat: update'")
        self.assertEqual(verdict_commit, ASK)
