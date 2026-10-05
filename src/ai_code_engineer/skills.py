"""Reusable Agent Skills, Standard Operating Procedures (SOPs), and Knowledge Matching.

"Skills should encode repeatable engineering practice, not inflate prompts."

This module coordinates reusable engineering guidance:
1. Skill Specification: Explicit SOPs defining steps, verification checklists, and forbidden practices.
2. Signal-Based Matching: Activates ONLY relevant skills matching task intent, language, or frameworks,
   preventing prompt inflation.
3. Precedence Hierarchy: Enforces that Skill guidance can never override System Security Policies
   or User Constraints (Security Policy > User Constraints > Skill Guidance).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Optional

from .errors import PolicyError
from .redaction import redact


@dataclass
class Skill:
    """A standardized engineering procedure for a specific class of tasks."""
    skill_id: str
    name: str
    version: str
    description: str
    triggers: list[str] = field(default_factory=list)
    guidelines: list[str] = field(default_factory=list)
    verification_checklist: list[str] = field(default_factory=list)
    forbidden_practices: list[str] = field(default_factory=list)

    def to_prompt_block(self, max_chars: int = 1200) -> str:
        """Render a concise markdown block for prompt context."""
        lines = [
            f"### SOP / Skill: {self.name} (v{self.version})",
            f"**Objective:** {self.description}",
            "**Guidelines:**",
            *[f"- {g}" for g in self.guidelines[:5]],
            "**Verification Checklist:**",
            *[f"- [ ] {v}" for v in self.verification_checklist[:4]],
        ]
        if self.forbidden_practices:
            lines.extend([
                "**Forbidden Practices:**",
                *[f"- ⚠️ {f}" for f in self.forbidden_practices[:3]],
            ])
        return "\n".join(lines)[:max_chars]


class SkillRegistry:
    """Registry and signal matcher for engineering skills."""

    def __init__(self) -> None:
        self.skills: dict[str, Skill] = {}
        self._register_default_skills()

    def register(self, skill: Skill) -> None:
        self.skills[skill.skill_id] = skill

    def match_skills(
        self,
        task_text: str,
        context_files: Optional[list[str]] = None,
        max_skills: int = 2,
    ) -> list[Skill]:
        """Match relevant skills based on task keywords and file context."""
        text_tokens = set(re.findall(r"\w+", (task_text or "").lower()))
        file_tokens = set(re.findall(r"\w+", " ".join(context_files or []).lower()))
        all_tokens = text_tokens.union(file_tokens)

        scored: list[tuple[int, Skill]] = []
        for skill in self.skills.values():
            score = 0
            for trigger in skill.triggers:
                trig_clean = trigger.lower().strip()
                if trig_clean in all_tokens:
                    score += 2
                elif any(trig_clean in token for token in all_tokens):
                    score += 1

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [skill for _, skill in scored[:max_skills]]

    def _register_default_skills(self) -> None:
        self.register(Skill(
            skill_id="bug_fix_sop",
            name="Defect Remediation & Bug Fixing",
            version="1.0.0",
            description="Systematic root-cause diagnosis and minimal-diff defect remediation.",
            triggers=["fix", "bug", "defect", "error", "failure", "crash", "issue"],
            guidelines=[
                "Isolate the failing site before proposing modifications.",
                "Implement the smallest possible targeted patch.",
                "Preserve existing API contracts and behavior outside the defect.",
            ],
            verification_checklist=[
                "Confirm the defect is reproduced or traced.",
                "Ensure new or existing unit tests prove the fix.",
                "Verify no regressions were introduced in related modules.",
            ],
            forbidden_practices=[
                "Do not delete or disable assertions in failing tests.",
                "Do not make sweeping refactors during a bug fix.",
            ],
        ))

        self.register(Skill(
            skill_id="refactoring_sop",
            name="Behavior-Preserving Code Refactoring",
            version="1.0.0",
            description="Restructuring code structure without altering external runtime behavior.",
            triggers=["refactor", "clean", "extract", "modularize", "reorganize", "simplify"],
            guidelines=[
                "Preserve all public method signatures and behaviors.",
                "Extract cohesive helper modules to reduce cyclomatic complexity.",
                "Maintain strict type annotations and docstrings.",
            ],
            verification_checklist=[
                "Ensure all pre-existing tests pass without modification.",
                "Verify no new dependencies were added.",
            ],
            forbidden_practices=[
                "Do not alter business logic or change API response formats.",
            ],
        ))

        self.register(Skill(
            skill_id="api_compatibility_sop",
            name="Backward-Compatible API Evolution",
            version="1.0.0",
            description="Safely extending APIs and DTOs without breaking downstream clients.",
            triggers=["api", "endpoint", "dto", "controller", "route", "contract", "schema"],
            guidelines=[
                "Make all new fields optional or provide backward-compatible defaults.",
                "Never remove or rename existing fields without deprecation cycles.",
                "Validate input payloads deterministically at the boundary.",
            ],
            verification_checklist=[
                "Verify existing client payloads continue to parse cleanly.",
                "Confirm serialization/deserialization tests cover edge cases.",
            ],
            forbidden_practices=[
                "Do not change existing status codes or response field names.",
            ],
        ))

        self.register(Skill(
            skill_id="security_review_sop",
            name="Security & Injection Hardening",
            version="1.0.0",
            description="Safeguarding against command injection, path traversal, and secret leakage.",
            triggers=["security", "auth", "secret", "token", "sanitize", "vulnerability", "permission"],
            guidelines=[
                "Never pass unvalidated user input directly into shell execution.",
                "Always resolve paths against project root to prevent path traversal.",
                "Scrub all secrets, tokens, and keys from logs and payloads.",
            ],
            verification_checklist=[
                "Confirm input sanitization is deterministic and offline.",
                "Verify no secrets are hardcoded in test fixtures.",
            ],
            forbidden_practices=[
                "Never use shell=True or raw string formatting in subprocess calls.",
            ],
        ))
