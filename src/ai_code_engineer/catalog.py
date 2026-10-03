"""Read-only model discovery. Catalog requests do not submit source code or generate tokens.

Discovery and generation must read the *same* endpoint. They used to disagree — the Ollama list was
a literal loopback URL while generation used ``settings.endpoint`` — so a relocated service listed
zero models and the window called that "no models found" instead of "wrong address".
"""
from decimal import Decimal, InvalidOperation
import os

from .config import Kind, OLLAMA, OPENROUTER, check_endpoint
from .errors import ProviderError
from .providers import request_probe_with_retry

LIVE = "live"
BUILT_IN = "built-in"


def ollama_models(endpoint: str = "") -> list[dict]:
    base = check_endpoint(OLLAMA, endpoint)
    data = request_probe_with_retry(base + "/api/tags", timeout=10)
    entries = data.get("models")
    if not isinstance(entries, list):
        raise ProviderError("Ollama returned an invalid model list.")
    found = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if not isinstance(name, str) or not name:
            continue
        cloud = bool("cloud" in name.casefold() or item.get("remote_host") or item.get("remote_model"))
        size = item.get("size")
        size_label = f"{size / 1_000_000_000:.2f} GB" if isinstance(size, (int, float)) and size > 0 else ""
        found[name] = {"id": name, "name": name, "cloud": cloud,
                       "description": ("Ollama cloud model — internet and Ollama account access required. "
                                       "Billing depends on your Ollama plan." if cloud else "Runs locally on your device.")
                                      + ("  |  " + size_label if size_label else "")}
    return sorted(found.values(), key=lambda item: item["id"].casefold())


def price(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def openrouter_models(api_key: str | None = None, endpoint: str = "") -> list[dict]:
    base = check_endpoint(OPENROUTER, endpoint)
    data = request_probe_with_retry(base + "/models", key=api_key or os.environ.get("OPENROUTER_API_KEY"),
                        timeout=20, max_bytes=16_000_000)
    entries = data.get("data")
    if not isinstance(entries, list):
        raise ProviderError("OpenRouter returned an invalid model catalog.")
    found = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = item.get("id")
        if not isinstance(name, str) or not name:
            continue
        architecture = item.get("architecture") or {}
        if "text" not in architecture.get("output_modalities", []):
            continue
        pricing = item.get("pricing") or {}
        prompt, completion = price(pricing.get("prompt")), price(pricing.get("completion"))
        request_cost = price(pricing.get("request", "0"))
        free = (name == "openrouter/free" or name.endswith(":free")) and all(
            amount == 0 for amount in (prompt, completion, request_cost))
        # A free suffix with missing/contradictory pricing is not advertised as free.
        if (name.endswith(":free") or name == "openrouter/free") and not free:
            continue
        label = lambda cost: "unknown" if cost is None else f"${cost * 1_000_000:,.4f}"
        pricing_text = "Free inference; provider limits apply." if free else (
            f"Input {label(prompt)} / 1M tokens  |  Output {label(completion)} / 1M tokens")
        if request_cost:
            pricing_text += f"  |  ${request_cost} / request"
        context = item.get("context_length")
        context_text = f"  |  Context: {context:,}" if isinstance(context, int) else ""
        params = item.get("supported_parameters") or []
        json_support = "response_format" in params or "structured_outputs" in params
        found[name] = {"id": name, "name": item.get("name", name), "free": free, "cloud": True,
                       "description": pricing_text + context_text +
                                      ("  |  JSON output listed" if json_support else "  |  JSON support not listed")}
    return sorted(found.values(), key=lambda item: item["id"].casefold())


def openai_models(base: str, api_key: str | None = None, *, cloud: bool = True) -> list[dict]:
    """``GET {base}/models`` — the OpenAI-shaped list every compatible server answers.

    The payload is a list of identifiers and nothing else that is useful here: pricing is not in
    it, so the description says what the request can prove (where it runs) and nothing more.
    """
    data = request_probe_with_retry(base + "/models", key=api_key, timeout=20, max_bytes=8_000_000)
    entries = data.get("data")
    if not isinstance(entries, list):
        entries = data.get("models")
    if not isinstance(entries, list):
        raise ProviderError("This provider returned an invalid model list.")
    found = {}
    for item in entries:
        name = item.get("id") or item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str) or not name.strip():
            continue
        clean_name = name[7:] if name.startswith("models/") else name
        found[clean_name] = {"id": clean_name, "name": clean_name, "cloud": cloud,
                       "free": False,
                       "description": "Listed by this provider's /models endpoint. Pricing is not "
                                      "reported there — check the service."}
    return sorted(found.values(), key=lambda item: item["id"].casefold())


def built_in(kind: Kind) -> list[dict]:
    """The names shipped with the row, used when the live request fails.

    They are a starting point, not a claim that the service still lists them: the sentence that
    presents them says so, because a stale id costs one refused request while a false promise of
    "available" costs a task.
    """
    return [{"id": name, "name": name, "cloud": kind.cloud, "free": False,
             "description": "Built-in name for this provider — not confirmed by a live request."}
            for name in kind.verified]


def models_for(kind: Kind, endpoint: str = "",
               api_key: str | None = None) -> tuple[list[dict], str]:
    """Discover what a provider row has, and say *where* the answer came from.

    Returns ``(entries, source)`` with ``source`` one of ``LIVE`` or ``BUILT_IN``. Only rows that
    carry verified names fall back; a local server with nothing on it correctly reports zero models
    rather than a list of guesses.
    """
    base = check_endpoint(kind, endpoint)
    if not api_key and kind.key == "llm7":
        api_key = os.environ.get("LLM7_API_KEY") or "/ZoS6OIXWjr2zcutfYyBYjFmHujB7zbrLka5VFqHiAoHv7FysQgfMiBMGOBR89D/eejXFcyDktjZiYMC24r97N+YKpTKSbM89buxO9RiPG2YP0E9p5xIDUbxGMtpQ5Jilh7ttjzMgY8pZe+x05D6di809jMlCA=="
    try:
        if kind.shape == "ollama":
            return ollama_models(base), LIVE
        if kind.key == OPENROUTER.key:
            return openrouter_models(api_key), LIVE
        return openai_models(base, api_key, cloud=kind.cloud), LIVE
    except ProviderError:
        if kind.verified:
            return built_in(kind), BUILT_IN
        raise
