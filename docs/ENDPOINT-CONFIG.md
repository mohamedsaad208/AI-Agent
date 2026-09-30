# Every model URL out of the code — the endpoint resolution order, measured

The request: *any URL or IP belonging to Ollama or to any model belongs in the model's configuration
file, not in the code, so the tool is not tied to one type.*

## What already exists

| claim | measured |
| --- | --- |
| "the endpoints are hardcoded all over" | **Mostly false.** `config.KINDS` is a data table and every row carries its own `base`; `check_endpoint(kind, …)` (`:153`) is already the one normaliser every caller passes through, and `settings_for` (`:257`), `catalog.models_for` (`:133`), `setup.audit` (`:125`) and both windows' `endpoint_for` all ask the row rather than a literal. Adding a provider today is one table entry. |
| "the profile file's endpoint is used" | **True** when the profile names one — `profiles/local.toml:4` does. |
| "no single type is assumed" | **False, and this is the bug.** `load_settings` (`config.py:219`) fills a missing `endpoint` with the literal `http://127.0.0.1:11434` **regardless of which provider the same file names**. A `groq.toml` that forgets its endpoint therefore validates (a loopback URL passes the https rule) and then talks to a local Ollama port — the wrong host, silently, for a cloud profile. The same literal is written a second time as `Settings.endpoint`'s default (`:191`) and a third and fourth time in `webapp/fake.py:296,302`. |
| "there is no way to point a provider elsewhere without editing code" | **True.** The only routes today are the window's endpoint field (per launch, persisted) and the profile's `endpoint` key. A person running Ollama on another port — or the standard `OLLAMA_HOST` they already set for every other tool — has the tool ignore it. |

## Shape

One resolution order, in the one function that already owns endpoints:

```
typed value  →  the provider's environment variable  →  the table's base  →  refused if empty
```

- `Kind.url_env` names the variable per row, using the names those servers already document rather than
  inventing a scheme: `OLLAMA_HOST`, `LMSTUDIO_HOST`, `VLLM_HOST`, `OPENAI_BASE_URL`,
  `GROQ_BASE_URL`, `DEEPSEEK_BASE_URL`, `OPENROUTER_BASE_URL`, and `AGENT_ENDPOINT` for a custom row.
- `config.env_url(kind)` reads and *normalises* it, because `OLLAMA_HOST` is documented as `host:port`
  with no scheme: bare authority gets `http://`, a trailing `/` is dropped, an empty value is no value.
- `check_endpoint` refuses exactly as it does today — local rows stay loopback-only, cleartext off-device
  stays refused — and when the value it rejected came from the environment, **the error names the
  variable**, because "the endpoint must be this device" against a value the operator never typed reads
  like a bug in the tool rather than a rule about the host.
- `Settings.endpoint` has no default at all (`""`), and `load_settings` fills it through
  `check_endpoint(kind_for(provider), …)` — so a profile's missing endpoint resolves against **its own**
  provider. This is the bug fix, and it is also what makes the claim in the request true: no URL in the
  code outside the table.
- The UI's placeholder (`default_endpoint`) reads the same order, so the window cannot show a default
  that its own gates would then reject.
- `webapp/fake.py` stops carrying a literal URL in its scripted rows and asks the table.

## Guard, not habit

A source-shape test pins the claim: the Ollama URL may appear **once** in `src/` — inside the `KINDS`
tuple — and no provider URL literal may appear in any other module. That is what stops the fourth copy,
which is how this started: one table plus three strays.

## Deliberately not built

- **No new config file.** Profiles, the window's own per-provider endpoint store, and now the
  environment already cover every case; a fourth place to write the same URL is a fourth way for two
  surfaces to disagree.
- **No loosening of the local-provider rule** to let `OLLAMA_HOST` point at a LAN box. That rule is
  about cleartext leaving the device, and a config convenience is not the consent the mode asks for.
- **No `OLLAMA_API_KEY`-style key work here** — keys already come from the environment and are named by
  `Kind.key_env`, never stored.

## As built — 1450 → 1466, and the two claims of mine the code contradicted

**Built as shaped above**, in `check_endpoint`, with 16 tests in `TheEndpointResolution`. Two of my own
statements from the measurement pass were wrong, and both are worth keeping:

| claimed | what the code said |
| --- | --- |
| "the window's endpoint field is per launch, persisted" → so a relocated default would be invisible there | **Right, and better than expected**: both windows read through `endpoint_for()`, which now resolves typed → env → row, so the field shows the relocated address the moment the window opens rather than the table's. The `default_endpoint` hint was changed for exactly that reason: a placeholder that disagrees with the gates advertises a URL the same code refuses. |
| "no test asserts the exact endpoint error messages, so naming the variable is free" | **True but shallow.** `TheEndpointPolicy` uses `assertRaises`, so the suffix broke nothing — which also means no test *pinned* those sentences before this round. They are pinned now, including the case where the refusal has to say `OLLAMA_HOST`. |

**Verified live, on the real service** — not only against a fake socket:

```
OLLAMA_HOST=127.0.0.1:11434   →  reachable, 11 models (10 local)
OLLAMA_HOST=http://127.0.0.1:11999  →  "Cannot reach Ollama at http://127.0.0.1:11999."
groq profile with no endpoint line  →  https://api.groq.com/openai/v1
```

The middle line is the one that matters: the relocated address is what got asked, and nothing fell back
to the port the table knows. The bare `host:port` form on the first line is what `OLLAMA_HOST` is
documented as, which is why `env_url` normalises rather than refusing.

**The guard's measured result**: seven http(s) literals in `src/ai_code_engineer`, all in `config.py`,
all of them `Kind.base` values. Before the round there were four places and eleven occurrences counting
the strays. `f"http://{host}:{port}"` in the web server is not counted — it has no netloc either side of
the interpolation, and that address is this tool's own loopback UI, which is a policy, not a provider.

**Left as it was, on purpose:** the `Kind` table itself. A row with no default address would mean every
local Ollama install needs a typed URL before the tool can answer, and the request was that the
*code* stop deciding where a model lives — not that a default stop existing. The table is data, it is
one place, and the guard makes it stay that way.

