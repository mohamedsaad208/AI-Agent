"""AI Code Engineer: bounded planning, review, and isolated verification."""
__version__ = "0.1.0"

from .core import (
    Action,
    AgentCore,
    AgentState,
    AgentStatus,
    Plan,
    Result,
    Task,
    Tool,
    VerificationResult,
)
from .repo_scanner import (
    ProjectIndex,
    RepoScanner,
    get_or_create_index,
    index_boost,
    load_index,
)

__all__ = [
    "Action",
    "AgentCore",
    "AgentState",
    "AgentStatus",
    "Plan",
    "ProjectIndex",
    "RepoScanner",
    "Result",
    "Task",
    "Tool",
    "VerificationResult",
    "get_or_create_index",
    "index_boost",
    "load_index",
]
