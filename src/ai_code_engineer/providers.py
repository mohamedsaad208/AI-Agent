"""Provider boundaries: no redirects, no proxies, no automatic cloud fallback."""
from __future__ import annotations

import json
import math
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .config import Settings, validate
from .errors import PolicyError, ProviderError
from .redaction import redact


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, payload: dict | None = None, *, key: str | None = None,
                 timeout: int = 120, max_bytes: int = 2_000_000) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ProviderError("Provider response exceeds size limit.")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ProviderError("Provider returned an invalid response.")
        return result
    except HTTPError as exc:
        # Never show the request, the bearer token or the URL. The response body is a different case:
        # it is the only place Ollama writes *why* it refused, and "model requires more system memory
        # (9.2 GiB) than is available (6.1 GiB)" is the difference between a dead task and a fixable
        # one. So a short, collapsed, redacted excerpt is allowed — capped, not trusted.
        detail = ""
        try:
            detail = redact(" ".join(exc.read(4096).decode("utf-8", "replace").split()))[:180]
        except Exception:                                      # noqa: BLE001 - a body that will not
            detail = ""                                        # read is not worth losing the code for
        raise ProviderError(f"Provider HTTP {exc.code}"
                            + (f": {detail}" if detail else "")
                            + "; no automatic retry or fallback.") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderError("Provider connection failed or timed out. A local model needs a longer "
                       "request timeout for a reply this large.") from None
    except (ValueError, UnicodeError) as exc:
        raise ProviderError("Provider returned invalid JSON.") from None


# Ollama's default context is small enough to cut a real coding prompt in half without saying so.
# Measured on this machine with `qwen2.5-coder:3b`: a 20 000-character prompt and a 60 000-character
# prompt both answered with `prompt_eval_count=2050`. The model replied from roughly the first eight
# thousand characters and nothing reported the loss — and `num_predict` above the window was capped the
# same quiet way. So the window is asked for explicitly, sized to the request that is being sent.
MIN_CTX = 2048
MAX_CTX = 16384
TEMPLATE_MARKERS = 64


def estimate_tokens(chars: int) -> int:
    """The same ÷4 the interface labels as an estimate. It is an estimate: it fits English prose and
    not code or Arabic, which is why it sizes a request rather than reporting one."""
    return max(1, math.ceil(int(chars or 0) / 4))


def context_window(prompt_chars: int, output_tokens: int) -> int:
    """A power-of-two `num_ctx` with room for this prompt *and* its reply, clamped to what a CPU
    box can hold — prompt processing, not generation, is what costs minutes here."""
    need = estimate_tokens(prompt_chars) + int(output_tokens or 0) + TEMPLATE_MARKERS
    window = MIN_CTX
    while window < need and window < MAX_CTX:
        window *= 2
    return min(window, MAX_CTX)


class ModelProvider(Protocol):
    model: str

    def generate(self, messages: list[dict], json_mode: bool = True) -> str: ...


class OllamaProvider:
    def __init__(self, settings: Settings, *, allow_cloud: bool = False):
        self.settings = settings
        self.model = settings.model
        self.supports_thinking = False
        self.allow_cloud = allow_cloud
        url = urlparse(settings.endpoint)
        if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment):
            raise PolicyError("Local mode accepts a loopback HTTP Ollama endpoint only.")
        self.endpoint = settings.endpoint.rstrip("/")

    def preflight(self) -> None:
        info = request_json(self.endpoint + "/api/show", {"model": self.model}, timeout=10)
        cloud_backed = bool("cloud" in self.model.casefold() or info.get("remote_host") or info.get("remote_model"))
        if cloud_backed and not self.allow_cloud:
            raise PolicyError("This Ollama model runs in the cloud. Approve cloud processing for public/synthetic code or choose a local model.")
        if not cloud_backed and not info.get("model_info"):
            raise PolicyError("Cannot establish that this is an installed local model.")
        self.supports_thinking = "thinking" in (info.get("capabilities") or [])

    def generate(self, messages: list[dict], json_mode: bool = True) -> str:
        prompt_chars = sum(len(str(message.get("content", ""))) for message in messages or [])
        payload = {
            "model": self.model, "messages": messages, "stream": False,
            # temperature 0 is greedy decoding, and a weak model that is poor at Arabic
            # tokenisation can sit on one token forever; 1.1 is the smallest penalty that
            # breaks the loop without bending the distribution on English or code.
            "options": {"temperature": 0, "num_predict": self.settings.output_tokens,
                        "repeat_penalty": 1.1,
                        "num_ctx": context_window(prompt_chars, self.settings.output_tokens)},
        }
        if json_mode:
            payload["format"] = "json"
        if self.supports_thinking:
            payload["think"] = False
        result = request_json(self.endpoint + "/api/chat", payload, timeout=self.settings.timeout_seconds)
        if result.get("done_reason") == "length":
            raise ProviderError("Model output truncated; reduce the change size.")
        message = result.get("message")
        value = message.get("content") if isinstance(message, dict) else None
        if not isinstance(value, str) or not value:
            raise ProviderError("No model content returned.")
        return value


class OpenRouterProvider:
    def __init__(self, settings: Settings, api_key: str | None = None, *, allow_paid: bool = False):
        if settings.model != "openrouter/free" and not settings.model.endswith(":free") and not allow_paid:
            raise PolicyError("Select the Paid cloud option explicitly to use a paid OpenRouter model.")
        self.settings = settings
        self.model = settings.model
        self.key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self.key:
            raise ProviderError("Set OPENROUTER_API_KEY in your environment (never in a file).")

    def generate(self, messages: list[dict], json_mode: bool = True) -> str:
        body = {
            "model": self.settings.model, "messages": messages, "stream": False,
            "temperature": 0, "max_tokens": self.settings.output_tokens,
            "provider": {"allow_fallbacks": False, "require_parameters": True},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        result = request_json("https://openrouter.ai/api/v1/chat/completions", body,
                              key=self.key, timeout=self.settings.timeout_seconds)
        try:
            choice = result["choices"][0]
            # Proposals must be complete; chat tolerates a missing finish_reason.
            finished = choice.get("finish_reason") == "stop" or (not json_mode and choice.get("finish_reason") is None)
            if not finished:
                raise ProviderError("Model did not finish normally; output discarded.")
            value = choice["message"]["content"]
            self.model = result.get("model", self.model)
            if not isinstance(value, str) or not value:
                raise KeyError("content")
            return value
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("Invalid OpenRouter response.") from None


def make_provider(settings: Settings, *, allow_cloud: bool, data_class: str,
                  api_key: str | None = None, allow_paid: bool = False) -> ModelProvider:
    validate(settings)
    if settings.provider == "openrouter":
        if not allow_cloud or data_class not in {"public", "synthetic"}:
            raise PolicyError("Cloud requires --allow-cloud and --data-class public or synthetic.")
        return OpenRouterProvider(settings, api_key=api_key, allow_paid=allow_paid)
    provider = OllamaProvider(settings, allow_cloud=allow_cloud and data_class in {"public", "synthetic"})
    provider.preflight()
    return provider
