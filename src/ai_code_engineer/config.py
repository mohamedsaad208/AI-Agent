from dataclasses import dataclass
from pathlib import Path
import tomllib

from .errors import AgentError

# The request timeout is the one number a user can set in either window, and the saved registry,
# the browser's number input and Tk's spinbox all disagree about it by construction. The range
# lives here once; the widgets advertise these and every setter clamps through this function.
REQUEST_TIMEOUT_DEFAULT = 300
REQUEST_TIMEOUT_LOW = 30
REQUEST_TIMEOUT_HIGH = 900


def clamp_request_timeout(value, fallback: int = REQUEST_TIMEOUT_DEFAULT) -> int:
    """One request timeout inside the advertised range, unreadable input included.

    A caller that must keep the previous value on garbage passes it as ``fallback``.
    """
    try:
        number = int(value)
    except (TypeError, ValueError, RuntimeError):   # RuntimeError catches tkinter's TclError
        return fallback
    return min(REQUEST_TIMEOUT_HIGH, max(REQUEST_TIMEOUT_LOW, number))


@dataclass(frozen=True)
class Settings:
    provider: str = "ollama"
    model: str = "qwen2.5-coder:1.5b"
    endpoint: str = "http://127.0.0.1:11434"
    max_turns: int = 12
    timeout_seconds: int = 120
    context_chars: int = 24000
    output_tokens: int = 4096


def load_settings(path: Path | None) -> Settings:
    if path is None:
        return Settings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data) - {"model", "limits"}:
            raise AgentError("Unknown configuration section.")
        model = data.get("model", {})
        limits = data.get("limits", {})
        if not isinstance(model, dict) or not isinstance(limits, dict):
            raise AgentError("Configuration model and limits must be TOML tables.")
        if set(model) - {"provider", "name", "endpoint"} or set(limits) - {
            "max_turns", "timeout_seconds", "context_chars", "output_tokens"
        }:
            raise AgentError("Unknown configuration field.")
        settings = Settings(
            provider=model.get("provider", "ollama"),
            model=model.get("name", "qwen2.5-coder:1.5b"),
            endpoint=model.get("endpoint", "http://127.0.0.1:11434"),
            **limits,
        )
        validate(settings)
        return settings
    except (OSError, ValueError, TypeError) as exc:
        raise AgentError("Configuration is unreadable or invalid TOML/types.") from exc


def validate(settings: Settings) -> None:
    if settings.provider not in {"ollama", "openrouter"}:
        raise AgentError("Provider must be ollama or openrouter.")
    if not isinstance(settings.model, str) or not settings.model.strip():
        raise AgentError("A model name is required.")
    if not isinstance(settings.endpoint, str):
        raise AgentError("Endpoint must be a string.")
    for name, low, high in (
        ("max_turns", 1, 30), ("timeout_seconds", 1, 900),
        ("context_chars", 2000, 100000), ("output_tokens", 256, 8192),
    ):
        value = getattr(settings, name)
        if type(value) is not int or not low <= value <= high:
            raise AgentError(f"{name} must be between {low} and {high}.")
