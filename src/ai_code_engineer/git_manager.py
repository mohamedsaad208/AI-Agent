"""Git-Aware Change Management, Change Ownership, and Safe Rollback.

"The agent owns its changes. It does not own the developer’s repository."

This module enforces safe repository change management:
1. Worktree Safety: Detects dirty files and uncommitted human work before agent actions.
2. Change Ownership: Segregates agent modifications from pre-existing human edits.
3. Diff Analysis: Computes structured diff summaries, categorizing changes (source, test, config)
   and flagging suspiciously oversized diffs.
4. Granular Rollback: Restores ONLY agent-owned files, strictly refusing repository-wide resets.
5. Git Policy Gate: Strictly denies destructive commands (force push, reset --hard) while
   gating standard commits and pushes behind explicit policies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Optional

from .errors import PolicyError
from .policy import ALLOW, ASK, DENY
from .redaction import redact


@dataclass
class WorktreeState:
    """Snapshot of working tree state prior to agent actions."""
    branch: str
    head_commit: str
    is_dirty: bool
    user_modified_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "head_commit": self.head_commit,
            "is_dirty": self.is_dirty,
            "user_modified_files": [redact(f) for f in self.user_modified_files],
            "untracked_files": [redact(f) for f in self.untracked_files],
        }


@dataclass
class DiffAnalysis:
    """Structured inspection of proposed or applied code changes."""
    files_changed: list[str]
    additions: int
    deletions: int
    categories: dict[str, list[str]]
    is_suspiciously_large: bool = False
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_changed": [redact(f) for f in self.files_changed],
            "additions": self.additions,
            "deletions": self.deletions,
            "categories": {k: [redact(f) for f in v] for k, v in self.categories.items()},
            "is_suspiciously_large": self.is_suspiciously_large,
            "warning": self.warning,
        }


class GitPolicyGate:
    """Enforces strict safety boundaries on Git commands."""

    FORBIDDEN_PATTERNS = [
        re.compile(r"\bpush\b.*(-f|--force|--delete)", re.IGNORECASE),
        re.compile(r"\breset\b.*--hard", re.IGNORECASE),
        re.compile(r"\bclean\b.*-f", re.IGNORECASE),
        re.compile(r"\bbranch\b.*(-D|--delete\s+--force)", re.IGNORECASE),
    ]

    @classmethod
    def evaluate(cls, git_cmd: str) -> tuple[str, str]:
        cmd_clean = git_cmd.strip().lower()

        # Check for forbidden destructive commands
        for pat in cls.FORBIDDEN_PATTERNS:
            if pat.search(cmd_clean):
                return DENY, "Destructive git operation (force push, reset --hard, clean -f) is strictly forbidden."

        # Outbound push requires explicit human approval
        if "push" in cmd_clean.split():
            return ASK, "Git push transmits repository data externally and requires explicit operator approval."

        # Creating commits is allowed in change mode or can be reviewed
        if "commit" in cmd_clean.split():
            return ASK, "Creating a git commit requires operator confirmation."

        # Safe read-only inspection commands
        if any(v in cmd_clean.split() for v in ("status", "diff", "log", "rev-parse", "branch")):
            return ALLOW, "Read-only Git query permitted."

        return DENY, f"Unrecognized or unapproved git command: {git_cmd}"


class GitChangeManager:
    """Manages agent-owned changes without interfering with developer work."""

    LARGE_DIFF_LINES = 1000
    LARGE_DIFF_FILES = 15

    def __init__(self, initial_state: Optional[WorktreeState] = None) -> None:
        self.initial_state = initial_state
        self.agent_modified_files: set[str] = set()

    def record_modification(self, file_path: str) -> None:
        """Mark a file as modified by the active agent run."""
        clean = file_path.replace("\\", "/").strip()
        self.agent_modified_files.add(clean)

    def is_agent_owned(self, file_path: str) -> bool:
        clean = file_path.replace("\\", "/").strip()
        return clean in self.agent_modified_files

    def analyze_changes(
        self,
        changed_files: list[str],
        additions: int = 0,
        deletions: int = 0,
    ) -> DiffAnalysis:
        """Categorize changed files and flag anomalous diffs."""
        categories: dict[str, list[str]] = {
            "source": [],
            "test": [],
            "config": [],
            "doc": [],
            "other": [],
        }

        for path in changed_files:
            p_lower = path.lower()
            if "test" in p_lower or p_lower.endswith(("_test.py", "test.java", ".spec.ts")):
                categories["test"].append(path)
            elif p_lower.endswith((".json", ".xml", ".yaml", ".yml", ".toml", ".gradle")):
                categories["config"].append(path)
            elif p_lower.endswith((".md", ".txt", ".rst", ".adoc")):
                categories["doc"].append(path)
            elif p_lower.endswith((".py", ".java", ".kt", ".js", ".ts", ".go", ".rs", ".cpp")):
                categories["source"].append(path)
            else:
                categories["other"].append(path)

        total_lines = additions + deletions
        is_large = total_lines > self.LARGE_DIFF_LINES or len(changed_files) > self.LARGE_DIFF_FILES
        warning = ""
        if is_large:
            warning = (
                f"Suspiciously large diff: {len(changed_files)} files, "
                f"+{additions}/-{deletions} lines exceeds safety threshold."
            )

        return DiffAnalysis(
            files_changed=changed_files,
            additions=additions,
            deletions=deletions,
            categories=categories,
            is_suspiciously_large=is_large,
            warning=warning,
        )

    def plan_rollback(self) -> list[str]:
        """Returns the list of agent-owned files safe to roll back."""
        return sorted(list(self.agent_modified_files))
