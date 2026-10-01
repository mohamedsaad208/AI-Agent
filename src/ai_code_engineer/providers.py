"""Provider boundaries: no redirects, no proxies, no automatic cloud fallback."""
from __future__ import annotations

import json
import math
import os
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .config import (Kind, OLLAMA, OPENROUTER, Settings, check_endpoint, kind_for, needs_consent,
                     validate)
from .errors import Cancelled, PolicyError, ProviderError, ProviderUnavailable
from .redaction import redact


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _refuse(exc: HTTPError) -> ProviderError:
    """The one translation of a provider that answered with an error code.

    Never the request, the bearer token or the URL. The response body is a different case: it is the
    only place Ollama writes *why* it refused, and "model requires more system memory (9.2 GiB) than
    is available (6.1 GiB)" is the difference between a dead task and a fixable one. So a short,
    collapsed, redacted excerpt is allowed — capped, not trusted.
    """
    detail = ""
    try:
        detail = redact(" ".join(exc.read(4096).decode("utf-8", "replace").split()))[:180]
    except Exception:                                      # noqa: BLE001 - a body that will not
        detail = ""                                        # read is not worth losing the code for
    return ProviderError(f"Provider HTTP {exc.code}"
                         + (f": {detail}" if detail else "")
                         + "; no automatic retry or fallback.")


def _opener():
    """One opener for both reads, so a streaming body cannot inherit different rules than a buffered one."""
    return build_opener(ProxyHandler({}), NoRedirect())


def request_json(url: str, payload: dict | None = None, *, key: str | None = None,
                 timeout: int = 120, max_bytes: int = 2_000_000) -> dict:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                      headers=headers)
    try:
        with _opener().open(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ProviderError("Provider response exceeds size limit.")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ProviderError("Provider returned an invalid response.")
        return result
    except HTTPError as exc:
        raise _refuse(exc) from None
    except (URLError, TimeoutError, OSError):
        raise ProviderUnavailable("Provider connection failed or timed out. A local model needs a "
                       "longer request timeout for a reply this large.") from None
    except (ValueError, UnicodeError):
        raise ProviderError("Provider returned invalid JSON.") from None


# A cold Ollama daemon answers nothing for a few seconds while it loads, which is the one failure a
# second ask genuinely fixes. Two attempts and one second is the whole policy; more would only be a
# slower way of saying the service is not running.
PROBE_ATTEMPTS = 2
PROBE_PAUSE = 1.0


def request_probe_with_retry(url: str, payload: dict | None = None, *, key: str | None = None,
                             timeout: int = 10, max_bytes: int = 2_000_000,
                             attempts: int = PROBE_ATTEMPTS, pause: float = PROBE_PAUSE) -> dict:
    """One metadata read, asked twice if the first attempt never reached anything.

    Only ever for metadata: `/api/tags` and `/api/show` are pure reads, so a repeat costs a second and
    changes nothing. A generation call is deliberately not in here. Re-running `/api/chat` or a stream
    starts another model load — minutes of CPU on a machine that may have just said it has no room —
    and restarting a stream mid-answer breaks the row the user is already watching, which is what
    `tests/test_transport.py` holds the refusal sentence for. So those keep one attempt, and the
    distinction is typed (`ProviderUnavailable`) rather than read back out of an English message.
    """
    for attempt in range(max(1, attempts)):
        try:
            return request_json(url, payload, key=key, timeout=timeout, max_bytes=max_bytes)
        except ProviderUnavailable:
            if attempt + 1 >= attempts:
                raise
            time.sleep(pause)


# A streaming body announces no length to trust, so the cap moves to what has been read. The same
# figure as the buffered read, because a reply that grows past it is not an answer that arrived late.
MAX_STREAM_BYTES = 2_000_000


def ollama_chunk(line: bytes) -> dict | None:
    """One NDJSON line from `/api/chat`: what was added, to which field, and how it ended."""
    try:
        chunk = json.loads(line)
    except ValueError:
        return None
    if not isinstance(chunk, dict):
        return None
    message = chunk.get("message") if isinstance(chunk.get("message"), dict) else {}
    return {"content": str(message.get("content") or ""),
            "thinking": str(message.get("thinking") or message.get("reasoning") or ""),
            "finish": str(chunk.get("done_reason") or "") or None,
            "model": "", "done": bool(chunk.get("done")),
            # Ollama writes these two only on the last chunk, and they are the measured counterpart of
            # `estimate_tokens` below: when `prompt_eval_count` comes back smaller than what was sent,
            # the server cut the prompt in half — which is a plumbing failure that has always looked
            # like a model that could not code.
            "prompt_tokens": chunk.get("prompt_eval_count"),
            "completion_tokens": chunk.get("eval_count")}


def openai_chunk(line: bytes) -> dict | None:
    """One SSE frame from `/chat/completions`. Blank frames and comment lines carry no text.

    DeepSeek's R1 line and OpenRouter's reasoning models put the deliberation in the delta beside the
    content, under the same two names they use in the buffered answer, so the same fields are read
    here — a streaming reply is not a licence to drop half of it.
    """
    text = line.decode("utf-8", "replace").strip()
    if not text.startswith("data:"):
        return None
    body = text[5:].strip()
    if body == "[DONE]":
        return {"content": "", "thinking": "", "finish": None, "model": "", "done": True}
    try:
        chunk = json.loads(body)
    except ValueError:
        return None
    choices = chunk.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
    usage = chunk.get("usage") if isinstance(chunk.get("usage"), dict) else {}
    return {"content": str(delta.get("content") or ""),
            "thinking": str(delta.get("reasoning_content") or delta.get("reasoning") or ""),
            "finish": first.get("finish_reason") or None,
            "model": str(chunk.get("model") or ""), "done": False,
            # Only present on the servers that send a usage frame, which is why these stay optional
            # rather than defaulting to zero: 0 tokens would be a measurement nobody made.
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens")}


def read_stream(url: str, payload: dict, *, key: str | None = None, timeout: int = 120,
                on_token=None, chunk=ollama_chunk, max_bytes: int = MAX_STREAM_BYTES,
                cancelled=None) -> dict:
    """Read a streamed reply in pieces, saying each one as it lands, and return the whole of it.

    The text is assembled here rather than trusted from the client's copy: the caller has to parse an
    envelope, store a record and hash a proposal out of what arrives, and a browser that lost a frame
    would otherwise change what the tool decided. `on_token` is a *display* consumer.
    """
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = Request(url, data=json.dumps(payload).encode(), headers=headers)
    content, thought, size, model, finish = [], [], 0, "", None
    seen: dict = {}
    try:
        with _opener().open(request, timeout=timeout) as response:
            for line in response:
                if cancelled is not None and cancelled():
                    raise Cancelled("Streaming cancelled.")
                size += len(line)
                if size > max_bytes:
                    raise ProviderError("Provider response exceeds size limit.")
                part = chunk(line)
                if not part:
                    continue
                if part.get("model"):
                    model = part["model"]
                if part.get("finish"):
                    finish = part["finish"]
                for field in ("prompt_tokens", "completion_tokens"):
                    # Last one wins rather than a sum: a server that repeats the counts on every frame
                    # would otherwise turn one answer into forty.
                    if isinstance(part.get(field), int):
                        seen[field] = part[field]
                if part["content"]:
                    content.append(part["content"])
                    if on_token is not None:
                        on_token(part["content"])
                thought.append(part["thinking"])
                if part.get("done"):
                    break
    except HTTPError as exc:
        raise _refuse(exc) from None
    except (URLError, TimeoutError, OSError):
        raise ProviderUnavailable("Provider connection failed or timed out. A local model needs a "
                       "longer request timeout for a reply this large.") from None
    except ValueError:
        raise ProviderError("Provider returned invalid JSON.") from None
    return {"content": "".join(content), "thinking": "".join(thought),
            "finish": finish, "model": model, **seen}


def counts(answer: dict) -> dict:
    """One provider answer's token counts, in one shape — or `{}` when it reported none.

    Absence is kept as absence. Writing 0 for a server that measured nothing would be a number in the
    audit that says "this cost nothing", which is the one reading nobody corrects.
    """
    found = {}
    for name, keys in (("prompt_tokens", ("prompt_tokens", "prompt_eval_count")),
                       ("completion_tokens", ("completion_tokens", "eval_count"))):
        for key in keys:
            if isinstance(answer.get(key), int):
                found[name] = answer[key]
                break
    return found


def metrics_of(provider) -> dict:
    """What the provider measured on its last answer, if a provider measures anything.

    Optional by construction, exactly as `reasoning` and `supports_stream` are: the scripted models this
    suite runs answer a string and own no transport, so a provider without a `metrics` attribute has
    reported nothing — which is not the same claim as having reported zero.
    """
    return counts(getattr(provider, "metrics", None) or {})


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
    """The seam between the loop and a model.

    `supports_stream` is declared rather than defaulted: a caller asks it before it passes
    `on_token`, which is what lets the scripted providers in the tests keep the two-argument
    signature they have always had.
    """

    model: str
    supports_stream: bool

    def generate(self, messages: list[dict], json_mode: bool = True,
                 on_token=None, cancelled=None) -> str: ...


# A reasoning model answers twice: once in a field nobody asked for and once in `content`. The names
# differ per provider — `reasoning` on Ollama and OpenRouter, `reasoning_content` on DeepSeek's R1 line,
# `thinking` on Qwen — and all three have been seen carrying the words while `content` carried the JSON.
REASONING_KEYS = ("reasoning", "reasoning_content", "thinking")
REASONING_CHARS = 1200


def read_reasoning(message) -> str:
    """What the model thought out loud, capped and redacted — or "" when it kept that to itself.

    This never enters the envelope path: `parse_action` must only ever see what the model was asked to
    return, and reasoning is precisely the field that contains prose, braces and half-finished thoughts.
    It is redacted here rather than at the surface because the raw words are also what a report exports,
    and a chain of thought quoting a connection string is still quoting a connection string.
    """
    if not isinstance(message, dict):
        return ""
    for key in REASONING_KEYS:
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return redact(value.strip())[:REASONING_CHARS]
    return ""


class OllamaProvider:
    supports_stream = True

    def __init__(self, settings: Settings, *, allow_cloud: bool = False):
        self.settings = settings
        self.model = settings.model
        self.supports_thinking = False
        self.reasoning = ""
        self.allow_cloud = allow_cloud
        # One rule for every provider, in ``config``: this device only, no credentials in the URL.
        # It used to refuse a URL *path* as well, which is how a relocated Ollama became unusable.
        self.endpoint = check_endpoint(OLLAMA, settings.endpoint)

    def preflight(self) -> None:
        info = request_probe_with_retry(self.endpoint + "/api/show", {"model": self.model},
                                        timeout=10)
        cloud_backed = bool("cloud" in self.model.casefold() or info.get("remote_host") or info.get("remote_model"))
        if cloud_backed and not self.allow_cloud:
            raise PolicyError("This Ollama model runs in the cloud. Approve cloud processing for public/synthetic code or choose a local model.")
        if not cloud_backed and not info.get("model_info"):
            raise PolicyError("Cannot establish that this is an installed local model.")
        self.supports_thinking = "thinking" in (info.get("capabilities") or [])

    def generate(self, messages: list[dict], json_mode: bool = True, on_token=None,
                 cancelled=None) -> str:
        if cancelled is not None and cancelled():
            raise Cancelled("Operation cancelled.")
        self.reasoning = ""
        self.metrics = {}
        prompt_chars = sum(len(str(message.get("content", ""))) for message in messages or [])
        payload = {
            "model": self.model, "messages": messages,
            # Asked for piece by piece only when somebody is listening. A turn that parses an envelope
            # gains nothing from a stream, and an SSE reader is one more way for a reply to go wrong.
            "stream": on_token is not None,
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
            # Measured on the local endpoint (2026-09-29, qwen3:4b): a prose turn with the switch off
            # puts the deliberation at the *front of `content`* — its closing marker sits inside the
            # text the reader is meant to call the answer — while the same 136 tokens with it on split
            # into a 180-character reply and a 522-character field. An envelope keeps it off: under
            # `format: json` the same request spent its whole budget thinking and answered nothing.
            payload["think"] = not json_mode
        if on_token is None:
            result = request_json(self.endpoint + "/api/chat", payload,
                                  timeout=self.settings.timeout_seconds)
            message = result.get("message") if isinstance(result.get("message"), dict) else {}
            value, thought = message.get("content"), message
            truncated = result.get("done_reason") == "length"
            self.metrics = counts(result)
        else:
            streamed = read_stream(self.endpoint + "/api/chat", payload,
                                   timeout=self.settings.timeout_seconds, on_token=on_token,
                                   chunk=ollama_chunk, cancelled=cancelled)
            value, thought = streamed["content"], {"thinking": streamed["thinking"]}
            truncated = streamed["finish"] == "length"
            self.metrics = counts(streamed)
        self.reasoning = read_reasoning(thought)
        if truncated:
            raise ProviderError("Model output truncated; reduce the change size.")
        if not isinstance(value, str) or not value:
            raise ProviderError("No model content returned.")
        return value


class OpenAICompatibleProvider:
    """Every provider that answers ``POST {base}/chat/completions`` with the OpenAI shape.

    That is OpenAI, Groq, DeepSeek, OpenRouter, LM Studio, vLLM and any custom base a user types:
    one body, one response, a different URL and a different key. Anthropic and Gemini are *not* in
    this class — different auth header, different body, different response — and are not offered.
    The three things that really are OpenRouter-only (the upstream routing block, the echoed
    upstream model, the free/paid rule) hang off ``Kind`` flags rather than a subclass.
    """

    supports_stream = True

    def __init__(self, settings: Settings, api_key: str | None = None, *,
                 allow_paid: bool = False, kind: Kind | None = None):
        self.kind = kind or kind_for(settings.provider) or OPENROUTER
        self.reasoning = ""
        self.settings = settings
        self.model = settings.model
        if self.kind.free_only and not allow_paid and settings.model != "openrouter/free" \
                and not str(settings.model).endswith(":free"):
            raise PolicyError("Select the Paid cloud option explicitly to use a paid OpenRouter model.")
        self.endpoint = check_endpoint(self.kind, settings.endpoint)
        env_name = settings.api_key_env or self.kind.key_env
        self.key = api_key or (os.environ.get(env_name) if env_name else "")
        # A custom endpoint may or may not want a key — that is the user's server to decide — so only
        # the rows that are known to require one refuse without it.
        if self.kind.needs_key and not self.key:
            raise ProviderError(f"Set {env_name or 'an API key'} in your environment (never in a file).")

    def generate(self, messages: list[dict], json_mode: bool = True, on_token=None,
                 cancelled=None) -> str:
        if cancelled is not None and cancelled():
            raise Cancelled("Operation cancelled.")
        self.reasoning = ""
        body = {
            "model": self.settings.model, "messages": messages, "stream": on_token is not None,
            "temperature": 0, "max_tokens": self.settings.output_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.kind.routing:
            # Refuse OpenRouter's own failovers: a silent hop to another upstream means the model
            # that answered is not the one that was reviewed.
            body["provider"] = {"allow_fallbacks": False, "require_parameters": True}
        if on_token is None:
            try:
                result = request_json(self.endpoint + "/chat/completions", body,
                                       key=self.key or None, timeout=self.settings.timeout_seconds)
                choice = result["choices"][0]
                message = choice["message"] if isinstance(choice.get("message"), dict) else {}
                value, thought = message.get("content"), message
                finish, echoed = choice.get("finish_reason"), str(result.get("model") or "")
                self.metrics = counts(result.get("usage") if isinstance(result.get("usage"),
                                                dict) else {})
            except (KeyError, IndexError, TypeError, AttributeError):
                raise ProviderError(f"Invalid {self.kind.label} response.") from None
        else:
            streamed = read_stream(self.endpoint + "/chat/completions", body, key=self.key or None,
                                   timeout=self.settings.timeout_seconds, on_token=on_token,
                                   chunk=openai_chunk, cancelled=cancelled)
            value, thought = streamed["content"], {"thinking": streamed["thinking"]}
            finish, echoed = streamed["finish"], streamed["model"]
            self.metrics = counts(streamed)
        self.reasoning = read_reasoning(thought)
        # Proposals must be complete; chat tolerates a missing finish_reason.
        if not (finish == "stop" or (not json_mode and finish is None)):
            raise ProviderError("Model did not finish normally; output discarded.")
        if self.kind.routing and echoed:
            self.model = echoed
        if not isinstance(value, str) or not value:
            raise ProviderError(f"Invalid {self.kind.label} response.")
        return value


def make_provider(settings: Settings, *, allow_cloud: bool, data_class: str,
                  api_key: str | None = None, allow_paid: bool = False) -> ModelProvider:
    validate(settings)
    kind = kind_for(settings.provider) or OLLAMA
    # Judged on the address that will actually be used, not on the string the settings happened to
    # carry: an empty endpoint resolves through the profile, the environment and the table, and a
    # consent decision made before that question is answered is made about a host nobody chose.
    endpoint = check_endpoint(kind, settings.endpoint)
    if kind.shape == "ollama":
        # A cloud-backed Ollama model is discovered in preflight, not assumed from the endpoint.
        provider = OllamaProvider(settings, allow_cloud=allow_cloud and data_class in {"public", "synthetic"})
        provider.preflight()
        return provider
    if needs_consent(kind, endpoint) and (
            not allow_cloud or data_class not in {"public", "synthetic"}):
        raise PolicyError("Cloud requires --allow-cloud and --data-class public or synthetic.")
    return OpenAICompatibleProvider(settings, api_key=api_key, allow_paid=allow_paid, kind=kind)
