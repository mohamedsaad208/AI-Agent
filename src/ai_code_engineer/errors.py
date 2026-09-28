class AgentError(Exception):
    """An actionable failure that can be displayed without a traceback."""


class PolicyError(AgentError):
    pass


class MissingFileError(PolicyError):
    """A policy-accessible path does not exist; creation may still be allowed."""


class ProviderError(AgentError):
    pass


class Cancelled(AgentError):
    """A user requested cancellation at a safe planning boundary."""
