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
from .repair import (
    execute_verification,
    handle_fix_evaluation,
    verification_from_run,
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
    "execute_verification",
    "get_or_create_index",
    "handle_fix_evaluation",
    "index_boost",
    "load_index",
    "verification_from_run",
]
