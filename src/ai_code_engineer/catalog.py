"""Read-only model discovery. Catalog requests do not submit source code or generate tokens."""
from decimal import Decimal, InvalidOperation
import os

from .errors import ProviderError
from .providers import request_json


def ollama_models() -> list[dict]:
    data = request_json("http://127.0.0.1:11434/api/tags", timeout=10)
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


def openrouter_models(api_key: str | None = None) -> list[dict]:
    data = request_json("https://openrouter.ai/api/v1/models", key=api_key or os.environ.get("OPENROUTER_API_KEY"),
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
