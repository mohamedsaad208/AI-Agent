"""Settings, the provider table, and the one endpoint policy every caller shares.

The provider list lives here rather than in ``providers`` because both ``validate`` and the
windows need it, and ``providers`` imports this module — the other direction would be a cycle.

This module is also the only place in the codebase that names a model server's address. A URL resolves
in one order everywhere: what the person typed, a signed override row (``overrides.py``), what their
environment says for that provider (``OLLAMA_HOST``, ``GROQ_BASE_URL`` and the rest of
``Kind.url_env``), then the provider's own row.
"""
import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse
import re
import tomllib

from .errors import AgentError, PolicyError
from . import overrides

# The folder this tool keeps its own records in — the same one `cli.app_dir()` and `webapp/launch.py`
# compute. It is spelled here as well because a profile read from the terminal has no window to ask.
APP_DIR = Path(__file__).resolve().parents[2]
from . import overrides

# The request timeout is the one number a user can set in either window, and the saved registry,
# the browser's number input and Tk's spinbox all disagree about it by construction. The range
# lives here once; the widgets advertise these and every setter clamps through this function.
REQUEST_TIMEOUT_DEFAULT = 900
REQUEST_TIMEOUT_LOW = 30
REQUEST_TIMEOUT_HIGH = 900

# The model a window offers when it has nothing better to say, and the note it prints beside the ones
# worth picking. Both windows recommend the same two, so the recommendation is the provider table's
# business — it used to be typed out in `gui.py` and in `webapp/controller.py` separately, and the
# `Settings` default below was a third copy of the same string.
# Measured on the reference machine through the app's own request path (docs/MODEL-BENCHMARK.md):
# qwen2.5-coder:1.5b was the fastest model that produced valid, correct proposals.
DEFAULT_MODEL = "qwen2.5-coder:1.5b"
RECOMMENDED = {
    "qwen2.5-coder:1.5b": "fastest recommended choice here — valid proposals in ~25s; good for quick planning and iteration",
    "qwen3:4b": "more careful answers, roughly 2× slower on this machine",
    "gemini-2.0-flash": "Google Gemini (Google AI Studio) — generous free tier, fast response, excellent for coding",
    "llama-3.3-70b-versatile": "Groq — ultra-fast LPU inference (300+ tok/s) with flagship open-weights reasoning",
    "codestral-latest": "LLM7 (Free) — Mistral Codestral model for code and chat",
    "Meta-Llama-3_3-70B-Instruct": "OVH AI Endpoints — flagship Llama 3.3 70B on European cloud",
}


def clamp_request_timeout(value, fallback: int = REQUEST_TIMEOUT_DEFAULT) -> int:
    """One request timeout inside the advertised range, unreadable input included.

    A caller that must keep the previous value on garbage passes it as ``fallback``.
    """
    try:
        number = int(value)
    except (TypeError, ValueError, RuntimeError):   # RuntimeError catches tkinter's TclError
        return fallback
    return min(REQUEST_TIMEOUT_HIGH, max(REQUEST_TIMEOUT_LOW, number))


LOOPBACK = frozenset(("127.0.0.1", "localhost", "::1"))
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Kind:
    """One provider the tool can talk to, and the rules that come with it.

    ``cloud`` is not a description of the vendor: it is the consent switch. A cloud row refuses to
    run until the user has approved sending code off the device, because nothing here can prove
    where a remote server actually is.
    """
    key: str                      # the Settings.provider value
    label: str                    # what both windows show
    base: str                     # default endpoint; "" means the user must type one
    shape: str                    # "ollama" | "openai" — which wire protocol to speak
    cloud: bool                   # consent required before any request
    needs_key: bool               # an API key is required
    key_env: str                  # environment variable that may supply it
    routing: bool = False         # OpenRouter only: upstream routing block and model echo
    free_only: bool = False       # OpenRouter only: a paid model needs an explicit choice
    verified: tuple[str, ...] = ()  # names to offer when a live list cannot be fetched
    # The variable that may supply this provider's base URL, named the way that server already
    # documents it rather than the way this tool would invent. A row with "" has no such convention.
    url_env: str = ""


KINDS = (
    Kind("ollama", "Ollama", "http://127.0.0.1:11434", "ollama", False, False, "",
         url_env="OLLAMA_HOST"),
    Kind("lmstudio", "LM Studio", "http://localhost:1234/v1", "openai", False, False, "",
         url_env="LMSTUDIO_HOST"),
    Kind("vllm", "vLLM", "http://localhost:8000/v1", "openai", False, False, "", url_env="VLLM_HOST"),
    Kind("openai", "OpenAI", "https://api.openai.com/v1", "openai", True, True, "OPENAI_API_KEY",
         verified=("gpt-4o-mini", "gpt-4o"), url_env="OPENAI_BASE_URL"),
    Kind("groq", "Groq", "https://api.groq.com/openai/v1", "openai", True, True, "GROQ_API_KEY",
         verified=("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen-qwq-32b", "deepseek-r1-distill-llama-70b"),
         url_env="GROQ_BASE_URL"),
    Kind("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "openai", True, True,
         "DEEPSEEK_API_KEY", verified=("deepseek-chat", "deepseek-reasoner"),
         url_env="DEEPSEEK_BASE_URL"),
    Kind("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "openai", True, True,
         "OPENROUTER_API_KEY", routing=True, free_only=True, url_env="OPENROUTER_BASE_URL"),
    Kind("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "openai", True, True,
         "GEMINI_API_KEY", verified=("gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-flash-lite-latest"),
         url_env="GEMINI_BASE_URL"),
    Kind("llm7", "LLM7 (Free)", "https://api.llm7.io/v1", "openai", True, False,
         "LLM7_API_KEY", verified=("codestral-latest", "mistral-Nemo-Instruct-2407", "nemotron-3-nano:30b"),
         url_env="LLM7_BASE_URL"),
    Kind("ovh", "OVH AI Endpoints", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "openai", True, True,
         "OVH_API_KEY", verified=("Meta-Llama-3_3-70B-Instruct", "Qwen2.5-Coder-32B-Instruct", "Mistral-7B-Instruct-v0.3"),
         url_env="OVH_BASE_URL"),
    # A user-typed base URL. The shape is OpenAI-compatible; the trust is decided by the host, so
    # loopback is local and anything else is treated exactly like a cloud row.
    Kind("generic", "Custom endpoint", "", "openai", False, False, "", url_env="AGENT_ENDPOINT"),
)
BY_KEY = {kind.key: kind for kind in KINDS}
OLLAMA = BY_KEY["ollama"]
OPENROUTER = BY_KEY["openrouter"]
GEMINI = BY_KEY["gemini"]
LLM7 = BY_KEY["llm7"]
OVH = BY_KEY["ovh"]
GENERIC = BY_KEY["generic"]
DEFAULT_KIND = OLLAMA


def kind_for(value) -> Kind:
    """The row for a key or a label, or None. Windows carry labels, settings carry keys."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return BY_KEY.get(text.casefold()) or next(
        (kind for kind in KINDS if kind.label.casefold() == text.casefold()), None)


def needs_consent(kind: Kind, endpoint: str) -> bool:
    """Whether this pair needs the cloud approval. A custom URL is judged by its host, not its name."""
    return kind.cloud or (kind.key == GENERIC.key and not is_loopback(endpoint))


def free_mode(kind: Kind) -> str:
    return f"{kind.label} \u00b7 Free"


def paid_mode(kind: Kind) -> str:
    return f"{kind.label} \u00b7 Paid"


def mode_rows() -> tuple[tuple[str, Kind], ...]:
    """The provider list as both windows show it: ``(label, kind)`` pairs.

    OpenRouter is two rows because its free list and its paid list differ in what they cost, which
    is a decision per task rather than a setting. Every other row is one provider. This lives here
    so the Tk window and the web controller cannot drift apart again the way ``MODES`` and the
    catalog URLs did.
    """
    rows = []
    for kind in KINDS:
        if kind.free_only:
            rows.append((free_mode(kind), kind))
            rows.append((paid_mode(kind), kind))
        else:
            rows.append((kind.label, kind))
    return tuple(rows)


MODES = tuple(label for label, _ in mode_rows())
MODE_KIND = dict(mode_rows())


def mode_for(provider: str, model: str = "") -> str:
    """The row a profile's provider (and the price class of its model) lands the window on."""
    kind = kind_for(provider)
    if kind is None:
        return ""
    if kind.free_only:
        name = str(model or "")
        if not name or name == "openrouter/free" or name.endswith(":free"):
            return free_mode(kind)
        return paid_mode(kind)
    return kind.label


def is_loopback(endpoint: str) -> bool:
    try:
        return urlparse(endpoint or "").hostname in LOOPBACK
    except ValueError:
        return False


def env_url(kind: Kind) -> str:
    """The base URL this provider's own environment variable supplies, or "".

    Normalising is the whole job here, because `OLLAMA_HOST` is documented as `host:port` with no
    scheme: a value copied out of a shell profile has to become a URL the same policy can judge. A
    variable set to nothing is not a value — the table's own base answers then.
    """
    if not kind.url_env:
        return ""
    raw = str(os.environ.get(kind.url_env) or "").strip()
    if not raw:
        return ""
    return raw if "://" in raw else "http://" + raw


def endpoint_override(app_dir, kind: Kind) -> str:
    """The address this provider's signed row supplies, or "" when the store has nothing to say.

    ``app_dir`` is a caller's, never a guess here: a resolution that read whichever folder the process
    happened to start in is how two surfaces end up disagreeing about where their traffic goes.
    """
    if app_dir is None:
        return ""
    value = overrides.values(app_dir, kind.key).get("endpoint")
    return str(value or "").strip()


def default_endpoint(kind: Kind, app_dir=None) -> str:
    """What the endpoint would be if nobody typed one, in the same order the gates resolve it.

    A window that showed ``kind.base`` here could be advertising a URL its own policy then refuses,
    which is the difference between a default and a hint that lies.
    """
    return endpoint_override(app_dir, kind) or env_url(kind) or kind.base


def check_endpoint(kind: Kind, endpoint) -> str:
    """The base URL for a provider, validated and normalised, or a PolicyError saying why not.

    The order is one rule with three sources: what the person typed in this window, what their
    environment says for that provider, and what the provider's own row in ``KINDS`` carries. Nothing
    else in the codebase names a model URL, which is what keeps a fourth provider from being a code
    change rather than a table entry and a profile.

    Paths are allowed — they are the whole shape of an OpenAI-compatible base
    (``http://localhost:1234/v1``) — which is what the old loopback rule got wrong. What stays
    refused: any scheme but http/https, credentials in the URL, a query or a fragment, and a
    provider that is not supposed to leave the machine doing exactly that.
    """
    typed = str(endpoint or "").strip()
    from_env = not typed and bool(env_url(kind))
    where = f" (set by {kind.url_env})" if from_env else ""
    raw = typed or env_url(kind) or kind.base
    if not raw:
        raise PolicyError("This provider needs an endpoint before it can be used.")
    try:
        url = urlparse(raw)
    except ValueError:
        raise PolicyError("The endpoint is not a valid URL.") from None
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise PolicyError("The endpoint must be an http or https URL." + where)
    if url.username or url.password:
        raise PolicyError("The endpoint must not carry credentials." + where)
    if url.query or url.fragment:
        raise PolicyError("The endpoint must not carry a query or a fragment." + where)
    local = url.hostname in LOOPBACK
    if not kind.cloud and kind.key != GENERIC.key and not local:
        raise PolicyError(f"{kind.label} is a local provider: the endpoint must be this device "
                          "(127.0.0.1, localhost or ::1)." + where)
    # Cleartext to another machine is the one shape that leaks a key, so it is refused for everyone.
    # A cloud row aimed at loopback is allowed: that is a local proxy on the user's own device, and
    # ``make_provider`` still gates it behind the cloud consent switch before it can be reached.
    if needs_consent(kind, raw) and not local and url.scheme != "https":
        raise PolicyError(f"{kind.label} at a remote address sends your code and your key over the "
                          "internet, so the endpoint must be https." + where)
    return raw.rstrip("/")


# The smallest budget a task can start inside. The instruction block, the task line and a repository
# map are spent before any of the project's own files are read, so a number under this one cannot be
# fixed by choosing a smaller repository -- every task fails on the first turn. It is refused where it
# is set, with a range in the message, rather than failing later with a sentence that blames the
# project. `test_the_smallest_budget_still_starts` keeps the two numbers from drifting apart.
MIN_CONTEXT_CHARS = 6400

# The number ranges, written once. `overrides.py` prints these into the file it creates and judges a
# row against them through ``validate``, so a limit cannot be restated in the module that stores them —
# a second table is what eventually disagrees with the first.
LIMITS = {"max_turns": (1, 30), "timeout_seconds": (1, 900),
          "context_chars": (MIN_CONTEXT_CHARS, 100000), "output_tokens": (256, 8192)}


@dataclass(frozen=True)
class Settings:
    provider: str = "ollama"
    model: str = DEFAULT_MODEL
    # No address of its own. "" means "ask the order `check_endpoint` implements": the profile, then
    # the provider's environment variable, then its row in the table. A default written here would be
    # one more place a URL is hardcoded, and the one that got read for a provider it never named.
    endpoint: str = ""
    max_turns: int = 12
    timeout_seconds: int = 120
    context_chars: int = 64000
    output_tokens: int = 4096
    # The *name* of the variable that holds a key, never a key. A profile that carried a value would
    # put a credential in a file that is meant to be committed.
    api_key_env: str = ""
    # Two optional local models for the fast/strong split (see `providers.py` and the loop). Empty
    # means "not chosen": the loop keeps using `model` for every request, so a profile written before
    # this field existed behaves exactly as it did. When both are set, small mechanical turns go to the
    # fast model and planning/diagnosis turns go to the strong one — all still the same local endpoint.
    fast_model: str = ""
    strong_model: str = ""
    # Optional folder of local FastEmbed model files for semantic retrieval. Empty, a missing folder,
    # or an uninstalled FastEmbed all mean the same thing: retrieval is lexical, exactly as before.
    # Nothing here downloads a model; the operator places the files and points this at them.
    semantic_model_dir: str = ""


def apply_overrides(settings: Settings, app_dir=None, *, keep=()) -> Settings:
    """Put this provider's signed rows under the values the caller is actually looking at.

    A row never carries an address or a provider, and that is the whole safety of the layer: where the
    code and the key go stays decided by the profile or the field on screen, and swapping which vendor
    answers a task from a side file is the failure ``providers.py`` already refuses upstream fallbacks
    for. Everything else a row states outranks a profile line and a table default — that is the point of
    a file the program writes rather than the operator. ``keep`` names the exceptions a caller states:
    a window passes `model`, because the model its list has selected is the one being reviewed.

    ``app_dir=None`` keeps the store out of the resolution entirely, which is what a caller with no
    records directory means, and what every test that has never heard of overrides relies on.
    """
    if app_dir is None:
        return settings
    kind = kind_for(settings.provider)
    if kind is None:
        return settings
    changes = {key: value for key, value in overrides.values(app_dir, kind.key).items()
               if key != "endpoint" and key not in keep}
    if not changes:
        return settings
    merged = replace(settings, **changes)
    try:
        validate(merged)
    except AgentError:
        # Individually valid rows can still stop fitting the rules this version has. Refusing the set
        # silently would be the exact thing the signature exists to prevent, and raising would fail a
        # task that has nothing to do with the row, so: named, dropped, and the profile is used.
        for key in changes:
            overrides.note(app_dir, key, "no-longer-valid")
        return settings
    return merged


def load_settings(path: Path | None, app_dir=None) -> Settings:
    if path is None:
        # No profile read, so nothing was stated for it: the rows fill in the defaults, and an address
        # they supply resolves through the same order a profile's own line would.
        settings = Settings()
        where = endpoint_override(app_dir, kind_for(settings.provider) or DEFAULT_KIND)
        return apply_overrides(replace(settings, endpoint=where) if where else settings, app_dir)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(data) - {"model", "limits"}:
            raise AgentError("Unknown configuration section.")
        model = data.get("model", {})
        limits = data.get("limits", {})
        if not isinstance(model, dict) or not isinstance(limits, dict):
            raise AgentError("Configuration model and limits must be TOML tables.")
        if set(model) - {"provider", "name", "endpoint", "api_key_env",
                         "fast_model", "strong_model", "semantic_model_dir"} or set(limits) - {
            "max_turns", "timeout_seconds", "context_chars", "output_tokens"
        }:
            raise AgentError("Unknown configuration field.")
        settings = Settings(
            provider=model.get("provider", "ollama"),
            model=model.get("name", DEFAULT_MODEL),
            api_key_env=model.get("api_key_env", ""),
            fast_model=model.get("fast_model", ""),
            strong_model=model.get("strong_model", ""),
            semantic_model_dir=model.get("semantic_model_dir", ""),
            **limits,
        )
        # The endpoint is resolved against the provider this same file names, through the one function
        # that owns the order. It used to fall back to a literal Ollama address here, which meant a
        # cloud profile that forgot its endpoint validated — loopback passes the https rule — and then
        # sent the task to a local port instead of to the vendor its own name.
        kind = kind_for(settings.provider)
        if kind is None:
            raise AgentError("Provider must be one of: " + ", ".join(item.key for item in KINDS) + ".")
        # An address the profile spells out is the one thing a row never replaces; when it names none,
        # the order is the row, then the provider's own environment variable, then its table entry.
        typed = str(model.get("endpoint", "") or "").strip()
        settings = replace(settings, endpoint=check_endpoint(
            kind, typed or endpoint_override(app_dir, kind)))
        settings = apply_overrides(settings, app_dir)
        validate(settings)
        return settings
    except (OSError, ValueError, TypeError) as exc:
        raise AgentError("Configuration is unreadable or invalid TOML/types.") from exc


def validate(settings: Settings) -> None:
    kind = kind_for(settings.provider)
    if kind is None:
        raise AgentError("Provider must be one of: " + ", ".join(item.key for item in KINDS) + ".")
    if not isinstance(settings.model, str) or not settings.model.strip():
        raise AgentError("A model name is required.")
    if not isinstance(settings.endpoint, str):
        raise AgentError("Endpoint must be a string.")
    if not isinstance(settings.api_key_env, str) or (
            settings.api_key_env and not ENV_NAME.fullmatch(settings.api_key_env)):
        raise AgentError("api_key_env must name an environment variable, not hold a key.")
    for name in ("fast_model", "strong_model", "semantic_model_dir"):
        if not isinstance(getattr(settings, name), str):
            raise AgentError(f"{name} must be a string.")
    check_endpoint(kind, settings.endpoint)
    for name, (low, high) in LIMITS.items():
        value = getattr(settings, name)
        if type(value) is not int or not low <= value <= high:
            raise AgentError(f"{name} must be between {low} and {high}.")


def settings_for(kind: Kind, endpoint: str = "", *, app_dir=None, keep=("model",),
                 **changes) -> Settings:
    """A Settings for one provider row, with its own default base when nothing was typed.

    Both windows used to hand every choice to ``replace(Settings(), …)`` with a literal provider
    name; this is the one place that pairs a kind with the endpoint that kind actually uses.

    ``keep`` defaults to `model` because that is the one value a window is showing as a decision: the
    model its list has selected is the one a person reviewed and clicked. The other numbers on the
    screen are remembered preferences rather than choices made for this run, so a row outranks them —
    and the Overrides section of both windows lists the rows currently in force, which is what keeps a
    number the field disagrees with from being a surprise.
    """
    settings = replace(Settings(), provider=kind.key,
                       endpoint=check_endpoint(kind, endpoint or endpoint_override(app_dir, kind)),
                       **changes)
    return apply_overrides(settings, app_dir, keep=keep)


# A profile label arrives from a browser dropdown, so it is a name from a closed shape rather than
# a path: "local" reads profiles/local.toml and "../../windows/win.ini" reads nothing at all.
PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
PROFILES_DIR = Path(__file__).resolve().parents[2] / "profiles"


def profile_names(directory: Path | None = None) -> list[str]:
    try:
        return sorted(path.stem for path in (directory or PROFILES_DIR).glob("*.toml")
                      if PROFILE_NAME.fullmatch(path.stem))
    except OSError:
        return []


def load_profile(label: str, directory: Path | None = None, app_dir=None) -> Settings:
    if not PROFILE_NAME.fullmatch(str(label or "")):
        raise AgentError("Unknown configuration profile.")
    return load_settings((directory or PROFILES_DIR) / f"{label}.toml", app_dir)
