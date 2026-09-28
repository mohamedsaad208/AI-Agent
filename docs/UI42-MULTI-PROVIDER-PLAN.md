# UI 4.2 — more providers, streaming, reasoning: measured before planned

The spec asked to "refactor and extend" the tool for generic OpenAI-compatible providers, profiles, model
discovery, SSE streaming, reasoning-model handling, an autonomous verify-then-self-correct loop, AST context
and a settings UI. Every item below was read in the code first. **About half of it exists, one third exists
but is not reachable from the windows, and two of its premises are factually wrong.**

Baseline: **832 offline tests green**, `node --check` clean. Nothing in this file is built yet; the decisions
at the bottom are open.

---

## Scorecard

| spec item | verdict | evidence |
| --- | --- | --- |
| Generic OpenAI-compatible provider | **PARTIAL** | `providers.py:147-155` already sends the OpenAI chat-completions shape (`messages`/`max_tokens`/`response_format:{"type":"json_object"}`), but the URL is a literal at `:154` `request_json("https://openrouter.ai/api/v1/chat/completions", …)` and `:150` adds an OpenRouter-only `"provider": {"allow_fallbacks": False, "require_parameters": True}` block |
| Expanded provider whitelist | **MISSING** | `config.py:66` `if settings.provider not in {"ollama", "openrouter"}` — a two-value set |
| TOML profiles | **EXISTS, CLI-only** | `config.py:38-62` `load_settings` with a key allowlist at `:43-52`; `profiles/local.toml`, `profiles/cloud-free.toml`. Only `cli.py:106` calls it — both windows build `replace(Settings(), …)` instead (`controller.py:1441`, `gui.py:1339`) |
| Model discovery from `/v1/models` | **MISSING** | `catalog.py:10` Ollama `/api/tags` and `catalog.py:40` OpenRouter `/api/v1/models`, both literals. No generic path, no offline fallback list |
| SSE token streaming | **MISSING end to end** | `"stream": False` at `providers.py:114` and `:148`; `engine.py:695` and `chat.py:159` are single blocking calls. The client stub `app.js:132` `case 'token'` → `appendToken` (`:666`) is **dead code: no Python file ever emits `kind:"token"`** |
| Reasoning / `…` separation | **MISSING (suppression only)** | `providers.py:109` detects the capability and `:124-125` sends `payload["think"] = False`. `message.reasoning` and Ollama's `message.thinking` are never read — `:130` and `:162` take `content` only |
| Autonomous verify → self-correct loop | **EXISTS, deliberately gated** | `repair.py` in full: `MAX_FIX_ROUNDS = 3` `:17`, `evidence()` `:121-131` (command, exit code, status, 12 failures, 2 500-char tail, redacted, capped 12 000), `fix_task()` `:134`, fed back as `extra_context` into `plan()` at `controller.py:2246`. The gate is `_offer_fix` `:2185-2214` → `confirm_choice("Keep going?")` |
| AST-based context extraction | **EXISTS** | `symbols.py:432-466` real `ast.parse` for Python plus bounded scanners for Java/Kotlin/Go/Rust/TS, `dependencies()` `:549`, `render(limit=12000)` `:575`; `engine.py:634-637` puts the map in the system turn and lets the model *ask* for file bodies |
| In-app provider/model/endpoint/key UI | **PARTIAL** | provider + model pickers and the key field exist (`app.js:1658-1705`, `controller.py:1172-1210`); **there is no endpoint field anywhere** — `grep endpoint` hits only `config.py` and `providers.py` |
| "No API keys leaked in error messages" | **MOSTLY, with two holes** | `redaction.py` is applied at 11 call sites. Holes below |

### Two premises in the spec that are wrong

1. **Anthropic and Gemini are not OpenAI-compatible endpoints.** Different auth (`x-api-key` +
   `anthropic-version` vs `Authorization: Bearer`), different bodies (`system` as a top-level array;
   `:generateContent` with `contents[].parts[]`), different responses. Groq, LM Studio, DeepSeek, vLLM,
   OpenAI and OpenRouter *are* — one class covers all six.
2. **"After code generation/application, trigger verification … attempt automated fixes up to `max_turns`"
   describes what already ships**, except that it asks for no human in the loop. Today the loop is bounded
   (`MAX_FIX_ROUNDS = 3`), fed real evidence, and — by decision, not omission — produces a *proposal* that
   still needs Apply, and `repair.must_ask` `:67-88` refuses to auto-apply anything containing a deletion.
   Removing the gate is a red-line move, not a missing feature. It is decision 2 below.

### Two real bugs the measurement turned up

- **`controller.py:460`** `log_msg = f"{failure} [Detail: {raw_error[:300]}]"` puts a raw exception string into
  the Activity log and every later snapshot **without `redact()`** — while `engine.py:884` redacts the same
  exception before storing it. `labels.friendly_error`'s fallthrough `return value[:600]` (`labels.py:624`) is
  reached by model-authored text (`engine.py:606` interpolates the model's own blocked `reason`). That is
  exactly acceptance criterion 2, and it is fixable now regardless of the rest of this plan.
- **`catalog.py:10`** discovers Ollama models at a literal `http://127.0.0.1:11434` while generation uses
  `settings.endpoint`. Discovery and generation already read different sources, so a relocated Ollama lists
  zero models. Any configurable base URL has to reconcile them.

---

## Build

### Phase 1 — the connection spine (no new behaviour, removes the literals)

1. **One provider table, one class.** A module-level `ENDPOINTS` tuple of small frozen records
   (`key`, `label`, `default_base`, `needs_key`, `cloud`, `models_path`, `auth`), and
   `OpenAICompatibleProvider(settings, endpoint, key)` replacing today's `OpenRouterProvider`, which becomes
   one row of that table plus its routing block. Ollama keeps its own class: `/api/chat`, `format:"json"`,
   `num_ctx` sizing (`providers.py:74`) and `think:false` are not OpenAI-shaped, and dropping them would
   regress the CPU-only loop this tool exists for.
2. **`config.py`**: whitelist from `ENDPOINTS`; validate `endpoint` per kind (loopback for local rows, https
   for cloud rows), and accept `api_key_env` as the *name* of an environment variable. `Settings` stays
   frozen and `validate` keeps its strict `type(value) is not int` bounds.
3. **`providers.py:96-99`** currently refuses `url.path not in {"", "/"}` — which rejects precisely the shape
   an OpenAI-compatible base takes (`http://localhost:1234/v1`). The rule becomes: loopback host + any path
   allowed; non-loopback requires the existing cloud consent switch. Keep `NoRedirect`, the dead proxy handler
   and the size cap as they are.
4. **`catalog.py`**: `models(endpoint, key)` for `/v1/models` and `/api/tags`, both reading the *configured*
   endpoint, plus a `VERIFIED` static list per row used when the request fails, with the answer saying which
   one it drew from (a server-built sentence in `labels.py`, because the model list is read in Arabic too).
5. **Reach the windows**: `ui["profile"]` in `.agent-projects.json` (already the single atomic store for UI
   state, `controller.py:2731-2754`), loaded through the existing `load_settings`, and a Settings-drawer
   "Connection" tab: provider select, endpoint input, key input, model count and where it came from. Tk gets
   the same three widgets beside the composer where mode/model already live (`gui.py:653`).
6. **Profiles** `groq.toml`, `lmstudio.toml`, `openai.toml`, `deepseek.toml` in the existing schema
   (`[model] provider/name/endpoint` + `[limits]`), each naming `api_key_env` rather than carrying a value.
7. **Fix the two bugs** above, each with a test that plants a `sk-…` string and asserts it never reaches
   `snapshot()["log"]`.

### Phase 2 — reasoning without breaking the envelope

- Read `message.reasoning` / `message.thinking` instead of dropping them, keep them **out of the JSON
  envelope path entirely**, and show them as a bounded, redacted, collapsed row — UI 4.1's step row is already
  that shape, so this is a new `action` and a detail section, not a new surface.
- Strip inline `…<…>` from `content` **before** `parse_action` (`engine.py:106`): today a model that
  reasons inside its reply produces invalid JSON and the refusal is "Return one JSON object", which
  blames the model for something the transport can fix.

### Phase 3 — streaming

- `ModelProvider.generate(messages, json_mode=True, on_token=None)` plus a `supports_stream` attribute, so the
  ~10 scripted providers in `tests/test_agent.py` keep their current signature and nothing silently changes
  shape under them.
- Two readers, not one: SSE `data:` lines for the OpenAI-shaped providers, NDJSON for Ollama, both under the
  same size cap, the same `from None` discipline and the same `redact()` as `_build_line`.
- **Chat answers stream** into the bubble `appendToken` already expects (making the dead stub live is the
  cheapest visible win in this plan). **Proposals stream into the live step row** — the JSON envelope is not
  prose, and UI 4.1 gave the running row a body that already accumulates lines.
- Stop has to mean stop mid-generation: `cancel_event` checked in the read loop, killing the connection rather
  than waiting for the body.

### Phase 4 — only if decision 2 says so

- A batch-scoped "run the fix rounds without asking" as a fourth answer on the existing sheet, symmetric with
  D30's "don't ask again for this batch". Bounded by `MAX_FIX_ROUNDS`, still producing a proposal, still
  refusing Auto-Apply for deletions via `must_ask`.

## What does NOT change

- **Review before write.** Apply stays a click; `must_ask` keeps refusing auto-apply for removals; a fix round
  still ends in a proposal, not a write.
- **The API key never touches disk** — `controller.py:1206` `self.key = str(value or "")  # memory only` and
  `test_the_api_key_is_never_written_to_disk` (`test_controller.py:809`) both stay true. Profiles carry an env
  var *name*.
- **Cloud stays consented**: `make_provider`'s `allow_cloud` / `data_class` gate (`providers.py:175-176`) is
  what the new rows plug into, not what they replace.
- **Stdlib only.** `urllib`, `json`, `tomllib`, `ast` — no `httpx`, no `requests`, no SDK.
- **`snapshot()["review"]`, the step rows, the side viewer** are consumers, not targets.

## Tests

- Backward compatibility is the acceptance bar: the 832 must stay green, and `test_local_remote_endpoint_denied`
  (`test_agent.py:344`), `test_a_provider_refusal_carries_its_reason_and_not_its_request` (`:426`) and
  `test_the_api_key_is_never_written_to_disk` are the three that must not be weakened to pass.
- New: one request-shape test per provider row (URL, auth header, body keys, JSON mode); endpoint validation
  per kind; `/v1/models` parsing plus its offline fallback and the sentence that names which was used;
  `redact()` on the two holes; reasoning-field extraction that leaves `content` clean; `parse_action` surviving
  an inline `…` block; SSE and NDJSON readers each yielding tokens in order, honouring the cap,
  and stopping on cancel.
- Expect roughly **832 → 880**.

---

## Open decisions

1. **Anthropic and Gemini**: the spec lists them as OpenAI-compatible, and they are not. Recommended: ship the
   OpenAI-shaped set (OpenAI, Groq, DeepSeek, LM Studio, vLLM, OpenRouter) plus a `generic` row now, and add an
   Anthropic adapter only if you will actually use it — it is a second auth header, a second body shape and a
   second response parser, not a config line.
2. **Autonomy**: keep the "Keep going?" gate as it is (recommended), or add the batch-scoped
   "run fix rounds without asking" of Phase 4? Fully unattended fixing is the one change that would write to a
   folder without a decision in it, and the tool already has a per-folder Auto-Apply switch for that.
3. **Streaming scope**: chat answers only (recommended first step), or chat + proposals into the live step row,
   or also make Stop abort mid-generation? The last is real work in the read loop, and today Stop waits for the
   response body.
4. **The `generic` row's reach**: loopback freely, any other host only behind the existing cloud-consent switch
   (recommended), or free-form URLs? This tool refuses redirects and proxies on purpose; a user-typed
   `http://` host to a cloud key is the one path that could leak it.
5. **Where a profile is chosen**: the Settings drawer (recommended — provider, model, endpoint and key already
   live in that area) or the sidebar next to the project, where the tool's other per-folder choices are?

---

## As built — decisions answered

**"نفذ على التوصيات الخمس"** (2026-09-28): all five recommendations stand. No Anthropic or Gemini
adapter; the "Keep going?" gate stays and Phase 4 adds one batch-scoped answer to it; streaming
covers chat answers *and* proposals into the live step row, with Stop-mid-generation deferred;
`generic` is free on loopback and behind the consent switch everywhere else; the profile picker is
in the Settings drawer.

## As built — Phase 1, the connection spine

**832 → 881 tests green.** `tests/test_connection.py` is new: 49 tests over the provider table, the
endpoint policy, one request shape per row, discovery following the configured endpoint, the
built-in fallback and its sentence, the profiles, and the web window's connection surface.

- **`config.py` owns the table**, not `providers.py` as planned: `validate` needs it and
  `providers` imports `config`, so the other direction is a cycle. `Kind` carries
  `key/label/base/shape/cloud/needs_key/key_env/routing/free_only/verified`; `mode_rows()` is the
  list both windows show, so the two `MODES` copies are gone with it.
- **`check_endpoint` replaces the loopback rule** at `providers.py:96-99`. A URL path is allowed —
  that is what `/v1` is, and refusing it made every OpenAI-compatible base unusable. Still refused:
  any scheme but http/https, credentials in the URL, a query or a fragment, a remote host for a
  local row, and cleartext to a remote address.
- **One class for seven rows.** `OpenAICompatibleProvider` replaced `OpenRouterProvider`; the three
  things that really are OpenRouter-only became flags (the upstream routing block, the echoed
  upstream model, the free/paid rule). `OllamaProvider` keeps `/api/chat`, `format:"json"`, the
  `num_ctx` sizing and `think:false` — those are not OpenAI-shaped and dropping them would regress
  the CPU-only loop this tool exists for.
- **Discovery and generation read one endpoint.** `ollama_models(endpoint)`,
  `openrouter_models(api_key, endpoint)`, a new `openai_models(base, key)` that accepts both
  `/models` payload shapes, and `models_for(kind, endpoint, key) -> (entries, source)`. Only rows
  with genuinely-known names fall back, and `labels.catalog_status_line` says which list the user
  is looking at — a shipped name and a server's live list are different promises.
- **The windows.** The drawer's fourth tab is **Connection** (profile, provider, endpoint, key, and
  the consent switch only for a row that can leave the device). Tk got the same controls in its
  Settings page. New actions `set_endpoint` and `set_profile`; `snapshot()["connection"]` is the row.
- **Profiles**: `groq`, `openai`, `deepseek`, `lmstudio`, `vllm` added, and `cloud-free.toml`
  corrected — it pointed an OpenRouter provider at the Ollama loopback endpoint, which the old code
  silently ignored and the new code would have honoured. Each names `api_key_env` and holds no value.

Three deviations worth keeping in mind, all measured rather than guessed:

1. **A cloud row aimed at loopback is allowed.** The plan implied refusing it; the leak that has to
   be refused is *cleartext to a remote address*, and a local proxy on the user's own machine is
   theirs to run. `make_provider`'s consent switch still gates every cloud row, so nothing reaches
   that path without approval.
2. **A refused endpoint leaves the typed text in the field** and toasts why. The server keeps its
   own value. Snapping the field back would hide the typo the user has to fix.
3. **An open drawer repaints only the Connection tab, and only when the row actually changed**
   (a signature of kind/endpoint/profile/mode/model). Repainting on every snapshot would clear the
   model filter and the key field mid-typing — and the key is never echoed by the server, so it is
   carried across a repaint by hand.

`check_endpoint`'s refusals stay English, like every other sentence at the provider layer: a
PolicyError raised in `config` has no language to ask about, and `friendly_error` has no Arabic
parameter to answer with. Recorded rather than half-fixed.
