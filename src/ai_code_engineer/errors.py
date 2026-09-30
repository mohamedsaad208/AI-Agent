class AgentError(Exception):
    """An actionable failure that can be displayed without a traceback."""


class PolicyError(AgentError):
    pass


class MissingFileError(PolicyError):
    """A policy-accessible path does not exist; creation may still be allowed."""


class ProviderError(AgentError):
    pass


class ProviderUnavailable(ProviderError):
    """The provider could not be reached at all — no status, no body, nothing it said.

    A subclass rather than a new field because the choice it enables is per call site, not per error:
    asking a model list again one second later costs a second, asking a generation again costs a model
    load. A caller that can tell the two apart is the only thing that may retry anything.
    """


class Cancelled(AgentError):
    """A user requested cancellation at a safe planning boundary."""
