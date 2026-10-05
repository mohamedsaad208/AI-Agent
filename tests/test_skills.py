"""Unit tests for Skill, SkillRegistry, and Signal Matching."""
import unittest

from ai_code_engineer.skills import (
    Skill,
    SkillRegistry,
)


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.registry = SkillRegistry()

    def test_default_skills_registered(self):
        self.assertIn("bug_fix_sop", self.registry.skills)
        self.assertIn("refactoring_sop", self.registry.skills)
        self.assertIn("api_compatibility_sop", self.registry.skills)
        self.assertIn("security_review_sop", self.registry.skills)

    def test_matching_bug_fix_skill(self):
        matches = self.registry.match_skills(
            task_text="Please fix the timeout defect in database connector",
            max_skills=1,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].skill_id, "bug_fix_sop")

    def test_matching_api_compatibility_via_file_context(self):
        matches = self.registry.match_skills(
            task_text="Add new status field to payload",
            context_files=["src/controllers/payment_controller.py"],
            max_skills=1,
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].skill_id, "api_compatibility_sop")

    def test_irrelevant_task_matches_no_skills(self):
        matches = self.registry.match_skills(
            task_text="Count the words in poem.txt",
            max_skills=2,
        )
        self.assertEqual(len(matches), 0)

    def test_max_skills_bound_prevents_prompt_inflation(self):
        # A task that might match refactor, api, and security
        matches = self.registry.match_skills(
            task_text="Refactor the auth controller endpoint to improve security and clean code",
            max_skills=2,
        )
        self.assertLessEqual(len(matches), 2)

    def test_prompt_block_rendering(self):
        skill = self.registry.skills["bug_fix_sop"]
        block = skill.to_prompt_block()
        self.assertIn("### SOP / Skill: Defect Remediation", block)
        self.assertIn("Verification Checklist", block)
        self.assertIn("Forbidden Practices", block)
