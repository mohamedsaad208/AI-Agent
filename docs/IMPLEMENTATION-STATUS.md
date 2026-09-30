# Implementation status — 2026-09-30

## Endpoints out of the code — one resolution order, and a profile that can forget its URL

The request: *any URL or IP belonging to Ollama or to any model belongs in the model's configuration,
not in the code, so the tool isn't tied to one type.* Measured first in `docs/ENDPOINT-CONFIG.md`,
which records the two claims of mine that the code contradicted. **1450 → 1466 offline tests.**

| what changed | where |
| --- | --- |
| one order, in the one function every caller already passed through | typed value → `Kind.url_env` (the variable that server already documents: `OLLAMA_HOST`, `LMSTUDIO_HOST`, `VLLM_HOST`, `OPENAI_BASE_URL`, `GROQ_BASE_URL`, `DEEPSEEK_BASE_URL`, `OPENROUTER_BASE_URL`, `AGENT_ENDPOINT`) → the row's own base → refused if none. `default_endpoint()` is the same order, so a window cannot show a hint its gates then reject |
| a profile that forgets its endpoint no longer gets Ollama's address | the bug the request exposed: `load_settings` filled a missing `endpoint` with a **literal loopback URL regardless of the provider named in the same file**, and because a loopback URL passes the https rule it *validated* — a cloud profile then talked to a local port. It resolves through `check_endpoint(kind_for(provider), …)` now |
| the three strays are gone | `Settings.endpoint` carries no address at all, and `webapp/fake.py`'s two scripted rows ask the table |
| a bare `host:port` is a URL | `OLLAMA_HOST` is documented without a scheme, so the normaliser adds one — otherwise the variable exists and does nothing |
| the policy is untouched, and the error names its source | a local row aimed off-device by the environment is still refused, and the message says `(set by OLLAMA_HOST)`, because a rule about a value nobody typed reads like a bug otherwise |
| consent is judged on the address that will be used | `make_provider` resolves first; a custom endpoint arriving from the environment used to be consent-checked on the raw, empty settings value |
| a guard, not a habit | an AST walk over `src/ai_code_engineer` asserts that every http(s) URL literal in code is a `Kind.base` inside `config.py` — measured result today: seven literals, one module. That is what stops the fourth copy |

**Verified live, on the real service:** `OLLAMA_HOST=127.0.0.1:11434` (bare form) answers with all 11
models; `OLLAMA_HOST=http://127.0.0.1:11999` answers `unreachable` and the audit row prints
`Cannot reach Ollama at http://127.0.0.1:11999.` — the relocated address is the one being asked, with no
silent fallback to the default port. A `groq` profile with no `endpoint` line resolves to
`https://api.groq.com/openai/v1`.

**Refused, with the reason:** a fourth config file (profiles + the window's per-provider store + the
environment already cover every case, and a fourth place to write a URL is a fourth way to disagree);
loosening the local-provider rule so `OLLAMA_HOST` may point at a LAN box over cleartext (a config
convenience is not the consent that mode asks for); any change to where keys come from (environment
only, named by `Kind.key_env`, never stored).

## UI 4.4 — the folder's position became a fact: read-only as a declaration every surface reads

The request was "there is something called read-only mode, make it a flag and finish it". The reading
that got built is the one `docs/READ-ONLY-MODE.md` had refused on purpose — a declaration on disk a
second process cannot ignore — and the reading that got refused is the curtain: hiding a *protective*
mode behind a default-off switch makes the writable path the default. Measured first, in
`docs/READ-ONLY-FLAG-PLAN.md`. **1403 → 1450 offline tests**, `node --check` and
`tools/scan_asi_strings.py` clean, and every claim below driven through a live window.

| what changed | where |
| --- | --- |
| the bug this round exists for | both windows rebuilt the shared `ui` block of `.agent-projects.json` from a list of named keys, so the desktop window's next save deleted the web window's per-folder positions — and a granted folder with no stored row opens on **Change**. Choosing Read-only for a folder and then opening the other window once put it back on the writable path without a word |
| one file with one purpose | `modes.py`: `{project_key: {mode, by, at, path}}` in `.agent-modes.json`, read through a `(mtime_ns, size)` cache because a gate asks on every snapshot and a snapshot goes out on every streamed log line. Written by whichever surface got there first, read by all of them |
| the asymmetry a corrupt row would have opened | a row nobody can parse is read as **sealed**, not as absent: over-sealing costs one deliberate click, under-sealing costs a file. `project_key("")` is the current directory, so an empty folder declares nothing rather than sealing wherever the process was started |
| two writes the mode still allowed | `git_branch` (`checkout -b`, a switch — git rewrites the tracked files) and `git_restore` (replaces bytes) asked nothing of `reading_only()`. Both refuse now, and the preview refuses them the same way so the design stays reviewable |
| the terminal was outside the promise | `agent read-only [--repo] [--off] [--arabic]` declares, lifts and lists; `plan`, `apply` and `rollback` refuse a declared folder *before* a provider is built or a hash is asked for. `map`, `review`, `status` and `verify` keep working: reading is what the mode is for, and a command typed by hand is the explicit yes the window would otherwise have asked for |
| which row wins, in one direction only | a stored `read` outranks anything (it is the only row that promises *less*); a stored `change` does not outrank the window's memory of a folder left on Chat — two tests failed on exactly that overreach and they were right |
| what the refusal says when the badge is not the culprit | `modes.refusal()` picks between the badge's own sentence and the declaration's, so a seal that arrived from a terminal names the command line and the minute, and the line to type that lifts it |
| the badge can now be honest | `declared` in the snapshot, a lock glyph and a dashed border on the pill, and `note` only when badge and fact disagree — the case where every refusal below would otherwise look like a bug |
| the desktop switch changed meaning | it is this window's starting position now, and clicking it tells the folder. Opening a folder whose declaration says read moves the box without the write-trace reading that as a click |
| a flag nobody would have found | the new command's `--arabic` printed `??????` on a cp1252 console until the four-line fix `run_setup` already had was lifted into `cli.speak_arabic()` — and the listing printed a lower-cased path because the *key* is normalised, so the row carries the path as the file system spells it |

**Refused, with the reason written down:** the default-off curtain; a per-call `--allow-write` (a
refusal you can rename your way around is not a refusal); `canMutate` as a "hole" — measuring showed it
drives the **Check syntax** button, one of the four verbs Read-only promises to keep doing, so gating it
would have broken the mode rather than finished it. `auto_apply`, `style`, `theme` and `queue` still get
erased across surfaces; only the position that decides whether files are written was moved out.

**Open from the same measurements:** a seal is keyed to a path, so renaming or moving a folder on disk
leaves it behind at the old one; and the *other* window's badge catches up when the folder is next
selected, not by watcher — the gate refuses live in the meantime, which is the half that matters.

## UI 4.3 — the container the command runs in, the desktop's own viewer, and one provider line

Three queue items, each measured before it was written, each with its own record:
`docs/DOCKER-SANDBOX-PLAN.md`, `docs/UI40-CHANGES-PANE-REMOVAL-PLAN.md` (*As built — the Tk twin*),
`docs/UI42-MULTI-PROVIDER-PLAN.md` (*As built — #56*). **1380 → 1403 offline tests**, `node --check` clean.

| what changed | where |
| --- | --- |
| the desktop window lost its Changes tab | the proposal viewer is a withdrawn `Toplevel` now, raised by `open_review()` and hidden by `close_review()` (title-bar X and Escape). Two views left in the stack, and the approval line names the window in the web sheet's own words. `transient()` was tried and is wrong here: a transient child of a withdrawn master cannot be mapped at all |
| a thinking model's chat answer stopped carrying its working-out | `payload["think"] = not json_mode`. Measured: the same 136 tokens come back as `thinking:522 + content:180` with the field asked for and as 712 characters of `content` with it refused, the closing marker inside the sentence the user was handed. The envelope keeps `think:false`, where asking cost a whole budget and answered nothing |
| `runner.run` can run inside the container | `sandbox="image@sha256:…"`: the project is copied to a host temp folder, that copy is the only writable mount, the recipe runs as argv with the image's own entrypoint, and the JUnit reports are read back off the copy. No Docker → `blocked`, and the reason says the host was not used either |
| the second recipe table died | `verification.RECIPES` was a hand-copied list of three of the runner's eleven commands, and it had already drifted on the one flag a `--network=none` build needs. `container_command()` derives the container argv from `runner.RECIPES` and adds `OFFLINE = {"mvn": "-o", "gradle": "--offline"}` |
| the shell prologue died with it | the old call started `/bin/sh -c 'cp -R /input/. /work/ && exec "$@"'` — two copies of the project and a tmpfs the evidence could not leave. The tests now pin the *absence* of shell text in the argv |
| both windows ask the same four questions | `Run in Docker` plus one image field in the Checks card of each, `runner.sandbox_state()` deciding and `labels.NOTE_TEMPLATES["sandbox_*"]` saying, so a half-typed digest gets the same sentence on both screens |
| the record says where a green came from | `run["sandbox"] = {image, container}`, `summarize()` says `in Docker`, the read-only question about a command names the container, and `agent export-session` prints `Maven test inside maven@sha256:… · passed` |

**Unverified, on purpose and in words:** no container has run on this machine — there is no Docker here.
Every claim above is an argv recorded against a fake `Popen`, at the same standard
`tests/test_verification.py` has always held. Whether a Maven reactor builds with no network, whether a
Windows temp path mounts cleanly, and whether uid 65534 can write the copy on a Linux host are questions
for a Docker-capable machine, and `docs/DOCKER-SANDBOX-PLAN.md` says so rather than implying a run.

**Left open from the same measurements:** a thinking model's chat turn is silent for the whole
deliberation — five minutes on `qwen3:4b` through the app's own prompt — because `read_stream` hands
`on_token` content pieces only (task #57); and the CLI still has no `run` command, which is where the
sandbox would otherwise be reachable from a third surface.

## Phase 2 — cleanup: the boundary tested, the two windows made to share what they say

An eight-item list framed as "not new features, less chance a future change regresses something"
(`docs/PHASE-2-CLEANUP.md`), measured first. Three items already existed in a different shape, one
turned out to be a contract nobody had implemented, and the repository-restructuring item had to be
refused on its consequences. **903 → 951 offline tests**, `node --check` clean, and the new packaging
gate **run locally** rather than only written into CI.

| what changed | where |
| --- | --- |
| the second window is no longer a takeover of the first | `serve()` builds a handler **subclass per launch**: token, controller, hub and port were class attributes on the shared `Handler`, so a second call replaced the first server's authentication |
| the HTTP boundary's own gaps closed | the `X-Auth-Token` header path (existed in code, tested nowhere), a closed SSE stream removing its client from `Hub` — proved with an `SO_LINGER` RST plus a publish to force the failing write — and two servers in one process keeping separate sessions. Every fixture now calls `server_close()`; the suite was leaking a bound port per class |
| the wire under a provider, tested on a socket | `tests/test_transport.py`: a 302 refused *and its target never reached*, a proxy in the environment ignored, `sk-…` echoed by a 401 not repeated back, non-JSON, valid-JSON-that-is-a-list, oversized body, a real timeout, a dead port |
| the command line, which had no test of its wiring | `tests/test_cli.py`: argv in, exit code and stdout out — usage errors exit 2, `doctor` both Ollama branches and never a key, `plan` leaves the folder untouched, `apply` refusing a missing hash / a wrong hash / a non-interactive run, `rollback` refusing before an apply, and the cloud gate refusing **before the transport is called** |
| a safety rule defined once instead of twice | `labels.NOTE_TEMPLATES` (21 sentences, both languages, `{fields}` verified): seven pairs had already drifted in wording. `gui.py` also had an inline duplicate of a key it imports |
| the two windows now share four verbs, with a ceiling | `host.Host`'s `say`/`line`/`ask`/`stream` were stubs neither window implemented; both do now, and `tests/test_host.py` ratchets the raw primitives so the un-routed half cannot grow unnoticed |
| four Tk/web asymmetries, three of them safety-relevant | Tk dropped `apply_prompt`'s `warning`; Tk called `repair.must_ask(session)` without the prior task, leaving that branch dead there; Tk wrote `runner.summarize()` unredacted where the web window redacts every field; and `controller.py` put `str(exc)` into a status line the browser reads |
| the shared fixtures, with a guard | `tests/doubles.py` + `tests/helpers.py`, and a test that no module re-declares them. The extraction surfaced a latent bug in both copies of `patched_catalog()`: `list(FREE_ENTRY)` is a dict's keys, so a free-only row was handed five strings as a model catalog |
| a wheel that can be trusted to open a window | `tools/check_package.py` (the four static files must be in the zip) and `tools/package_smoke.py` (install into a venv, start the server, fetch the page, the CSS, both scripts). CI builds the wheel, checks it, installs it. `pyproject.toml` was already right — nothing held it |
| the README stopped promising things | the `620+` badge is the CI badge, "nothing reaches your disk without explicit approval" became what Auto-Apply actually is, and a *Three modes and the limits* section carries the four caveats: Run/Check executes the project's own build code (a limit, not a sandbox), Docker is the isolation, the repository map is context not a compiler, a cloud row means code leaves the device |

Refused, with the reason written down: `spring-rpoject/` and `project-2/` stay where they are because
the tool's own history — `.agent-projects.json` and four `.agent-chats/<id>/chat.json` — addresses them
by absolute path, and "open the window afterwards and read the whole history" outranks a typo in a
directory name (`archive/README.md`). `ecommerce` is a **gitlink** (`160000`, no `.gitmodules`) and
de-indexing it changes what the repository records, so it is reported, not run. `ruff` is not added:
the project is stdlib-only deliberately and a first lint of 5 100 never-linted lines would be a wall
nobody reads.

## Phase 1 hardening — the boundary, the lock, and one sentence the docstring was keeping

A seven-item priority list, measured against the code before any of it was written
(`docs/HARDENING-PHASE-1.md`): CI marked done and absent, cargo reported broken in the wrong place,
no lock anywhere in the web layer, `snapshot()` handing out live lists, a server that checked only its
token, 500s that echoed exception text, and a "this runs the project's own code" fact that existed only
in a module docstring. **881 → 903 offline tests**, `node --check` clean on both scripts, and the
workflow's every command run locally first.

| what changed | where |
| --- | --- |
| the server answers only the page it was opened for | `server.Handler._guard()` — `Host` must name a loopback name on this exact port (DNS rebinding), and `Origin`/`Referer`, when sent, must be `http://` + that same port. `serve()` refuses to bind a non-loopback host at all |
| every response carries the same headers, so a new route cannot forget them | `_safe()` + `CSP`/`SAFE_HEADERS`: `default-src 'none'`, `script-src 'self'`, `frame-ancestors 'none'`, `base-uri`/`form-action 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer` (the launch URL holds the token in its query) |
| the policy made a file of the one script that ran before paint | `static/boot.js`, extracted from `index.html`'s inline `<script>`. `style-src 'self' 'unsafe-inline'` stays, deliberately, and is the one loosening documented in the header itself |
| a failure says one fixed sentence to the browser | `Handler._fail()`: an `AgentError` keeps its own redacted, capped wording because this program wrote it to be read; anything else gets `(id 3f9a1c)` and the reason goes to `controller.note_failure()` — the Activity log, redacted at 600, because a provider's 401 body can echo a bearer token |
| controller state has one guard, and it is never held across a wait | `self._state = threading.RLock()`: the `busy` claim, `_add`/`_note`/`_step`, `set_reply` (its `Event.set()` deliberately outside), `_ask` (registers locked, waits unlocked), `open_step` (copies inside, searches outside), `snapshot()` |
| a snapshot cannot change under whoever holds it | `snapshot()` is `deepcopy` of `_snapshot()` inside the lock — a client sorting a list in place used to rewrite the server's state |
| cargo gets the timeout its own step needs, and the count it already printed | `timeout_for()` puts cargo with the compilers (cold Rust is a *compile*), and `runner.console_proof()` reads cargo's own `test result: …` lines — summed, so a two-crate workspace reporting 20 and 4 says 24 — instead of `summarize()` throwing the parsed number away and saying `exit 0` |
| the run button now says what it does, in both languages | `labels.run_warning(arabic=)`, drawn under Run in the drawer (`.warn`) and on Tk's Settings page, and carried in `snapshot()["runWarning"]`: project build code, your permissions, this folder — a limit, not a sandbox |
| the gate that did not exist | `.github/workflows/ci.yml`: 3.11 on `windows-latest` + `ubuntu-latest` (Tk's 41 tests would silently skip on Linux, so `python3-tk` + `Xvfb`), `compileall`, `node --check` on both scripts, then the same `python -m unittest discover -s tests` that `run-tests.cmd` runs. Pinned to one interpreter on purpose: 3.13 tallies subtests differently, and the test count is this project's acceptance measure |

Two premises died in the measurement, and both are worth keeping down: cargo's *marker* was never
broken — `detect()` lowercases filenames, so `markers: ["cargo.toml"]` has always matched a real
`Cargo.toml` — and go has no counting hole, since `go test` prints no total by design and the recipe
already counts `ok <package>` lines. Only cargo declares a console proof.

## UI 4.2 Phase 1 — the connection spine: eight providers, one endpoint, no literals

Asked for "Multi-Provider Integration": generic OpenAI-compatible providers, TOML profiles,
`/v1/models` discovery, SSE streaming, reasoning separation, an autonomous verify-then-fix loop and an
in-app provider UI. `docs/UI42-MULTI-PROVIDER-PLAN.md` measured it first and found about half of it
already shipping, a third reachable only from the command line, and two of its premises wrong
(Anthropic/Gemini are not OpenAI-compatible; the self-correct loop exists and is gated by decision).
**832 → 881 offline tests**, `node --check` clean, verified in the scripted window.

| what changed | where |
| --- | --- |
| the provider list is a table, not a two-value whitelist | `config.Kind` + `KINDS` (ollama, lmstudio, vllm, openai, groq, deepseek, openrouter, generic) and `mode_rows()`, which both windows read instead of their own copies |
| one class speaks for seven rows | `providers.OpenAICompatibleProvider` replaces `OpenRouterProvider`; what really is OpenRouter-only became flags — the upstream routing block, the echoed upstream model, the free/paid rule |
| the endpoint rule allows the shape it used to refuse | `config.check_endpoint` — a URL path is what `/v1` is. Still refused: credentials, query, fragment, a remote host for a local row, cleartext to a remote address |
| discovery and generation read the same address | `catalog.ollama_models(endpoint)`, `openrouter_models(api_key, endpoint)`, new `openai_models()` for `/models`, and `models_for(kind, endpoint, key) → (entries, source)` |
| a fallback list says what it is | `labels.catalog_status_line` names the provider and the source, because a shipped name and a live list are different promises. Only rows with verified names fall back |
| the windows can reach all of it | a **Connection** tab in the Settings drawer (profile, provider, endpoint, key, consent when the row can leave the device), the same three controls in Tk's Settings page, `set_endpoint` / `set_profile` actions, `snapshot()["connection"]` |
| profiles are no longer CLI-only | `groq/openai/deepseek/lmstudio/vllm` added; `cloud-free.toml` corrected (it pointed an OpenRouter provider at the Ollama loopback URL); each names `api_key_env` and holds no key |
| two redaction holes closed | `friendly_error` redacts once at its boundary, covering the `value[:600]` fallthrough and the model's own refusal reason; the controller's `[Detail: …]` line is redacted before it is stored |

Measured during the browser pass, and kept because they are not visible in the diff: a refused
endpoint leaves the mistyped text in the field and toasts the reason; an open drawer repaints *only*
the Connection tab and only when the row changed, since the key field is never echoed back by the
server; and a cloud row aimed at loopback is allowed — the leak worth refusing is cleartext to
somewhere else, and `make_provider`'s consent switch still gates it.

## UI 4.1 — step rows that say what they did, and open to show what they found


Asked with a screenshot of another tool's transcript: "عايز اعمل زي دي جوه التول يكتب كدا ايه شغال وبيعمل
ايه ولو في تفاصيل هيكون جميل افتحها ابص عليها او اعملها كولابس". `docs/UI41-STEP-ROWS-PLAN.md` measured the
existing parts first and found most of the job already done: `labels.step_line` already writes each row's
sentence and glyph in both languages, `engine.announce` already calls it for every action, and
`session["runs"]` already keeps the command, exit code, seconds, 12 failure lines and a 2 500-character tail.
**805 → 832 offline tests**, `node --check` clean, verified live in both windows.

| what changed | where |
| --- | --- |
| a step row is now a handle, not just a sentence | `engine.announce()` mints an id, records `event(session, "step", id, action, **fields)` and calls `step(line, id, action, fields)`; `_add` grows an optional `step` block on the message |
| the row draws with a chevron only when there is something behind it | `labels.step_has_detail(action, fields)` — one rule, both controllers, client draws the answer. A running command is the exception the client knows: its detail is the output streaming in |
| the detail is fetched, never shipped | new `step_detail` action → `controller.open_step(id)` → `snapshot()["step_detail"] = {id, sections, files}`. A run row gets Command / Result / Reported problems / End of output from the stored record; a proposal row gets its file list, and each path opens the side viewer |
| "Executing mvn -B test" ends in the past tense | `_settle_run_step` rewrites that same row into `⚙️ Ran … — failed · exit 1 · 7.1s` and flips its detail flag |
| reopening a task gives the steps back | `_history_steps` rebuilds rows from `step` events and turns each `run` event into its row, collapsing a repeated read exactly as the live thread does — and only for the task that was opened, so the chat is not buried in old tool calls |
| a click that can be answered always is | an unknown row, or a run no session could hold (D28), answers with a server-written sentence instead of silence |
| Activity admits what it dropped | `MAX_LOG_ENTRIES = 400` on the live list and on the rebuilt one, with `log_dropped` and `log_note` so the trim is stated. `snapshot()["log"]` had no ceiling at all before this |

Three findings worth keeping. **The proposal's own step event was being thrown away** — `plan()` saved the
session and *then* announced the proposal, so the most important row in the thread never reached disk and a
reopened task lost it. **`.step` was already the plan card's class** (`.step.done`, `.step.now`), so the first
version of the row wore the plan's layout and the plan wore the row's; it is `.steprow` now, with a test that
`.step {` appears exactly once. And **the record and the thread disagree on purpose** — the loop records every
read it took, the thread shows a repeat once — so the rebuild collapses the same way or history shows a step
nobody saw.

Verified live and read-only on the real folder: a session reopened from history carried
`⚙️ Ran mvn -B test — failed · exit 1 · 7.1s`, and opening it fetched the stored command, the verdict, 12
redacted `[ERROR]` lines and 15 lines of real Maven output, with `state.view` never leaving Chat. In the
scripted window: four named sections on the run row, a proposal row whose file list opens the viewer, a read
row with no digest drawn **without** a chevron, and a row marked `executing` under `busy` that opens without a
round trip and streams chunks into itself.


## UI 4.0 — the Changes pane is deleted, and the viewer lives beside the chat

Asked as a removal, not a refactor: "عندي مشكلة في صفحة ال changes / ملهاش لازمة وينفع نستغني عنها /
عايز أشيلها خالص / هنستبدلها ب viewer جانبي عشان ماخرجش برا الشات خالص". `docs/UI40-CHANGES-PANE-REMOVAL-PLAN.md`
measured the pane first and found it was already redundant: the rail preview rendered the same
`snapshot()["review"]` block with the same badge, path and `+add/−del`, and `openFile()` took the rail
branch at every width the rail is on screen. **796 → 805 offline tests**, `node --check` clean,
`tests.test_webapp` alone green, and the deletion verified in the live window.

| what the pane owned | where it is now |
| --- | --- |
| the `Changes` tab, `#view-review`, `renderReview()` | gone — `markTabs` toggles `task`/`details` only, and the pane's CSS wrappers went with it (`test_the_panes_own_css_rules_went_with_it`) |
| Apply changes / Check syntax / Roll back | `changeActions(r, host)`, one builder mounted on both rail surfaces: the artifact card and the preview. Same enablement as the banner had, with Roll back on `canRollback` rather than `canMutate`, so an interrupted apply keeps its escape. The palette keeps its copies |
| the file list | the rail's one-row-per-file list and the thread's chips, which already read the same block |
| the `before` / `after` / `checks` tabs | the preview is now `Diff / Now / Was / Checks`; `view.checks` draws as `.pv-checks` prose, because it is a promise about checks and the server's own run line, not a diff |
| below 1180px, where `.rail { display: none }` | the same card in a `sheet(…, …).wide` modal (`viewerSheet()`), titled with the server's task line and file count. Chat stays behind it; a new `refreshPreview()` repaints whichever surface holds it, so tab clicks work inside the sheet |
| the three `{"kind": "view", "value": "review"}` emits | `"preview"`, handled by `showView()` → the rail opens on the newest change set and `state.view` never moves. The emit arrives from the worker *before* the snapshot carrying the files, so the intent is recorded and taken up by the next `render()` |
| `?view=review` bookmarks | still accepted: `showView(value, asClick)` treats `review` and `preview` alike, and only an explicit URL forces the sheet. An arriving proposal never pops a dialog over typing |

Two findings the deletion surfaced. **The scripted preview window had no `canRollback` field at all**, so
in `--fake` the escape would have shipped permanently disabled — the new snapshot test caught it, and
`fake.py` now derives all three flags from `labels.MUTABLE_STATES`/`INTERRUPTED_STATES` instead of a
hand-copied set. And closing the sheet left the rail holding the dismissed preview (invisible at that
width, visible the moment the window widened) — now `onClose` repaints the rail, with a test.

Verified in the live window on the real project, read-only: an applied session opened from history, its
chip raised the sheet, the Checks tab drew the last `mvn -B test` verdict, and the buttons came up
`Apply off / Check syntax on / Roll back on` for a change already on disk. `state.view` stayed `task`
through all of it. The Tk window kept its Changes tab in that round — no side rail to receive the
controls, so the move there was a rebuild (decision 1, web only). **#28 has since shipped it**: the desktop
viewer is its own window now and the tab is gone (`docs/UI40-CHANGES-PANE-REMOVAL-PLAN.md`,
*As built — the Tk twin*).


## UI 3.9 — the seven open defects, plus the two the window itself reported

`docs/UI39-REMAINING-DEFECTS-PLAN.md` measured every premise first; two of the seven were restated
rather than built as written, and one more ("`git rm` is needed") died on a scratch repository.
**753 → 796 offline tests**, suite green, `node --check` clean, and each touched module also passes run
alone. The dogfood round that follows these fixes is documented in `DOGFOOD-ECOMMERCE-RUN.md`.

| id | what was wrong | what ships |
| --- | --- | --- |
| D33 | `_ask()` minted an id, emitted it once over SSE and waited 1 800 s. Nothing stored it, so a window that missed the event — or any window after a restart — could never answer the question, and the worker holding it read `busy=False` while the queue waited behind it | `snapshot()["asks"]` carries `{kind, id, **payload}` for every live question as a **list** (two were simultaneously live in the run); `set_reply` and the expiry path both retire it; the client draws an id once through `drawAsk`, whether it arrives by event or by snapshot — 3 controller cases + 1 webapp case |
| D30 | the fix offer was a yes/no dialog, so every red build in a batch cost the whole ask timeout. The operator's workaround (`set_recipe("")`) did that by destroying the folder's verification | a third answer, `ok: false` plus `alt: true`, labelled from `repair.FIX_OFFER_ALT`; it runs no round, silences the rest of **that** batch, and says so in a Tool row; cleared when the batch closes and on any hand-typed Send. The sentence belongs to one window, because Tk has no queue to be the rest of a batch — 3 cases |
| D28 | `_can_run` and `run_tests` gated on `MUTABLE_STATES`, so after `BLOCKED`, `CANCELLED`, `ROLLED_BACK` or an interrupted apply the project could not be built at all — the tree that most needed checking was the unreachable one | the gate is now "locked while work is pending": `{DISCOVERING, WAITING_APPROVAL}`. The folder comes from the session or from the window; the run is recorded only when a session can hold it, and when nothing can, `run_unrecorded_line` says so rather than letting the verdict vanish — 3 cases, and `test_commands_are_locked_until_a_proposal_is_applied` still passes untouched |
| D31 | no delete verb existed. `workspace.py` had `read`/`write`/`path`, and the only `unlink()` was inside `rollback()` keyed on `before is None` — which is why `ecommerce` shipped three near-duplicate security configs | `{"path": …, "delete": true}` through the whole contract: `Workspace.remove` (same guards as `write`, hash-checked), `prepare_changes` (must exist, must have been read, counts the removed text against the 100 KB budget, skips the content gates), `apply_proposal` (event `removed`), `rollback` (restores — the existing `before is None` rule already means the opposite of a delete, so no flag collision survives), `must_ask` (**a delete never auto-applies**), `static_check` (a removal passes by being absent), both review surfaces (kind `D`, "Removed"), `host.apply_prompt` ("Write 1 and remove 1 file(s)") and `applied_line` — 8 cases across four modules. **Measured, not assumed:** `git add -- <gone path>` already stages a deletion, so the checkpoint needed no `git rm`; `LiveRepositoryTests` pins that fact |
| D35 | 4 000 was written in six places and spelled two ways ("1-4000" / "1–4000"), and the XML refusal told the model to send complete file text — the one channel a file that large cannot use through a 4 000-character task | `MAX_TASK_CHARS` in `engine.py`, imported at every site (`plan`, `propose_block`, `gui`, `queue_add`, `queue_edit`, `start_plan`, block tasks); both refusals now name the route that works at any size: an anchored edit on a unique line — 3 cases, one of which greps the two window sources for a re-forked literal |
| D34 | the spec asked for a before/after XML AST integrity check. The gate already parses the **result**, and the pom that actually broke the build parses cleanly today — `<dependency>` hanging off `<project>` is well-formed XML and an invalid Maven model | a **POM model check** next to the existing `.xml` branch: under a `<project>` root, every `dependency` must sit in `<dependencies>` and every `plugin` in `<plugins>`, refusal naming the element and the parent it was found under. Non-pom XML (a Liquibase changelog) and `dependencyManagement/dependencies` are untouched. It cannot catch the other D34 incident — a deleted element whose comment survived — and the batch summary is what reports that — 4 cases |
| — | the queue was in memory on purpose, with a test asserting it: a change request that outlives a reboot can be applied to files that moved on | `.agent-projects.json`'s `ui["queue"]` (not the spec's new `.agent-runs/queue.json` — one store, already written atomically) keeps the batch, and every restored row comes back **held**, marked `restored`, with `when_restored` on the strip; `queue_add` beside a restored row does not release it, ▶ does. The old test is rewritten deliberately from "resurrects nothing" to "resurrects it held, and held means nothing runs" |
| D36 | a task that asked for two files proposed one, and nothing said so until another module failed to compile an hour later | `_batch` records each queue-started task and the paths it wrote; when the queue drains, one Tool row: tasks, files, paths. The disagreement flag fires only on the literal scaffold phrase ("Create exactly three new files"), because parsing prose for an implied count would refuse good work — 3 cases |
| D39 | the user's report was "the View button in history does nothing". The button is wired: a restart rotates the per-launch token, and an already-open page keeps every pixel of real history while refusing every click with a 403 and a 4.2 s toast. Reproduced by rotating the token on a live page | `api()` routes any 403 into `windowGoneDeaf`: one notice that does not auto-dismiss, carries Reload, and appears once however many clicks follow; the dead stream shares it; `openFile` no longer returns silently — 6 cases in `DeadWindowTests` |
| D40 | "Applied — project tests have not run" appeared on every card because the post-write run keyed off the branch's recipe selection, which the D30 workaround had cleared. The label was true; the gate was the bug | Auto-Apply now always runs a check the folder has: the selected recipe, else the first command `runner.detect()` listed for it, and when the folder has no command at all `applied_no_command` says that instead of leaving it implied — 2 cases |

One project note from the same round: the operator's per-message boilerplate was measured across the 90
prompts of the first run — **37 of them pasted file content**, and the most repeated clauses were
"do not create or change any other file" (36) and "send the whole file as complete content" (21). Both
the facts and the answering rules now live in the project's own notes file, which the tool appends to
every task, and the second run sends commands instead.



## UI 3.8 — two fixes, six named gaps, and the batch that wrote them

M5–M8 ran as a queue of nineteen scaffolding and repair tasks driven through `/api/action`, and the run's
evidence is in `docs/DOGFOOD-ECOMMERCE-RUN.md` and `docs/DOGFOOD-PROMPTS.md`. Two defects shipped, each
after the first attempt at it turned out to be wrong; six were measured and left open on purpose.
**750 → 753 offline tests**, suite green, `node --check` clean.

| id | what the run showed | state |
| --- | --- | --- |
| D29 | `engine.plan` announces every tool action on **both** callbacks with the same line, and the web controller logged it twice — once from `progress`, once from `step` — so the activity history held two rows for every tool call. The first patch fixed only the *repeat* case and the duplicates came back on the next task, in the window the patch had just been restarted into | **shipped**: `_step()` adds the conversation row and moves the status, and records no log row of its own — 1 case |
| D37 | a model copying a 3 019-character pom sent the file correctly and dropped `summary` and `checks`; the shape gate demanded all four keys, refused the whole write twice, and the loop guard ended the task twelve minutes later — for two sentences no code executes | **shipped**: absent prose is filled (`NO_SUMMARY`, `DEFAULT_CHECKS`, the second now shared with the block-chosen path), wrong-typed prose is still refused — 2 cases |
| D28 | after a task blocks, `canRun` is false because `MUTABLE_STATES` is about *this session*, while the unverified files belong to *this project* — the tree the tool wrote cannot be built until another task writes something | open; the gate needs a project-side question and a test that Run survives a blocked task |
| D30 | with the recipe selected, every scaffolding write ended in "Keep going? Fix round 1 of 3" and the queue waited for an answer that was always the same. `_applied` skips `run_tests` when no recipe is selected, so clearing the run command is the switch — 2.5 min/task instead of 5, and one build verifying the whole reactor at the end | open as UI: the offer needs a third answer ("not again for this batch"); the operator workaround is documented |
| D31 | the plan wanted one shared security config and `auth-service`'s own retired. A task can create and edit; only `rollback()` can `unlink`, and only for a file that session created — so the app ships three near-duplicate configs, a design cost paid from a tool gap. It also costs an `h2-console` that renders in one service and not in the other | open; the change form needs a `delete` entry (path + `before_hash`), reviewed as "removes this file", with `repair.removal_notice` already holding the wording |
| D33 | `_ask()` mints a random id, emits it once over SSE and waits 1 800 s. Nothing stores it and nothing replays it, so a window that was not attached when the question was raised can never answer it, and the queue behind it stalls the full timeout reading idle (`busy=False`). Restarting clears it but drops the queue, which is not persisted either | open; one `asks` field in the snapshot, and the client's existing sheet drawer |
| D35 | the XML refusal tells the model to "put actual complete file text directly in content" — impossible for any file that does not fit inside the 4 000-character task, which is the operator's only channel for that text. The three copies of that limit are in `engine.plan`, `queue_add` and this advice | open; the sentence needs the alternative it does not mention (a narrower edit on a unique leaf line), and the limit wants one constant |
| D36 | a task that asked for two files proposed one; the thread and the Auto-Apply card both said `1 file(s)` truthfully, and the missing entity surfaced as nine compile errors in another module an hour later | open as tool work (a batch summary, or a question when the counts disagree), closed as operator rule #16 |
| D38 | the run allowlist is build and test commands, so nothing in the tool can start a service, bind a port, or issue the one request that would prove `admin`/`admin` logs in; there is no `clean` recipe either. Verification ends at the compiler, and `VERIFICATION_BLOCKED` with "no tests ran" is the tool saying so | open by design; documented as the boundary in `DOGFOOD-ECOMMERCE-RUN.md` |

Two of these correct claims this repository had already written down. UI 3.6's note that Auto-Apply's
post-write run has "no switch for it" is wrong — `set_recipe("")` is the switch, and D30 is what found it.
And the run doc's header pointed at `.agent-chats` for the transcripts: that is where the Tk window keeps
prose, while the web thread is rebuilt from `.agent-runs` grouped by `chat_id` — which is exactly why 89
sessions of history stayed readable through three server restarts.

## UI 3.7 — the second half of the same run

The `ecommerce` session kept going past M1, and the defects changed character: fewer crashes, more
sentences that were true and useless. **738 → 747 offline tests**, suite green, `node --check` clean.

| Defect | What was wrong | What ships |
| --- | --- | --- |
| D20 | `.gitignore` could not be written at all — the text gate was a *suffix* gate, and a dotfile has no suffix; the repo carried three `target/` rows as uncommitted work for two milestones | `TEXT_NAMES`: a closed, case-folded allowlist of suffix-less text files (`Dockerfile`, `Makefile`, `.editorconfig`, …) checked after the credential and rule-file guards — 8 cases |
| D20b | the refusal that produced it named nothing, so the model repeated the same action until the loop guard cost two 5-minute turns | the sentence carries the offending path, and a name problem gets a name remedy instead of the shape example — 1 case |
| D21 | `apply_proposal` wrote the file, then lost the note about it to a Windows sharing violation *inside its own handler*: the user saw a bare `[WinError 5]`, the session stayed `APPLYING` forever, and clicking Apply again answered "Approval must match the pending proposal hash" | the recovery note is best-effort, and the sentence names what is already on disk — 1 case |
| D22 | a repair round was told only "an empty search block would match anywhere" and tried it twice more | the refusal now says how a line is inserted: anchor on the neighbouring line, replace it with itself plus yours — 1 case |
| D23 | "Unknown action or invalid fields" is true of eight different mistakes; the session recorded that a reply was refused, never which fields it had | `action_shape()` echoes the action name and its field names (values never recorded), and the repeat guard ends the task by quoting the refusal it hit — 1 case |
| D24 | "make this class public, keep every other line" came back as the file minus its `package` and `import` lines — legal Java, so every existing gate passed, and the reactor went red | a rewrite that drops a package declaration the file already had is refused; moving a class and creating a default-package file both still pass — 1 case |

| D25 | a `.java` file arrived as 167 characters of imports with the interface missing; Auto-Apply wrote it, and the reactor answered with nine errors in the file that referenced the absent type | a `.java` proposal that declares no `class`/`interface`/`enum`/`record` is refused (`package-info.java` excepted) — 1 case |
| D25b | two rows sat in the queue strip for half an hour behind an unanswered fix-round dialog, with a sentence promising they ran "when the current task ends" | the strip names the open question as the reason when one is open — 2 cases |

The pattern across the eight is the same one the first half of the run found, stated once more with numbers:
on a 3 B model, the quality of the tool's error sentences *is* the throughput. Every entry above is a
message change; only D20 and D24 change what is allowed, and both widen rather than tighten.

## UI 3.6 — what the `ecommerce` run broke, fixed while it ran

Nine defects came out of the first real multi-module session (`docs/DOGFOOD-ECOMMERCE-RUN.md` is the
evidence file, with every prompt pasted verbatim and every fix re-run against the live window).
**694 → 738 offline tests**, `node --check` clean.

| Defect | What was wrong | What ships |
| --- | --- | --- |
| D3 | a provider 500 left no reason anywhere; the milestone died silently | a capped, redacted excerpt of the error body joins the message — 3 cases |
| D4 | Ollama was answering from a third of the prompt, silently: `num_ctx` was never sent | `context_window()` sizes a power-of-two window per request — 3 cases |
| D5 | a granted folder returned to Chat mode after a restart | one `_grant_folder()` writer for all three grant paths — 3 cases |
| D9 | nine unanswered modals stayed stacked over the app, the topmost one eating every click | `_ask()` emits a `retract` when its wait expires and the sheet is removed by its `data-ask`; a late reply is dropped — 6 cases |
| D10 | the composer's Stop button shared Send's class, so anything naming "the send button" by class stopped a running turn | stable handles: `#stop`, `#send` — 2 cases |
| D11 | a Maven pom was Auto-Applied into a `.java` path, twice, and the build found out 11 s later | the `.py`/`.json` syntax gate extended to `.java` (markup or no declaration) and `.xml` (parsed, DTD refused before parsing) — 7 cases |
| D12 | a BLOCKED task recorded that it blocked, with blank events and no `session["error"]` | the rejection reason and the stop reason are stored, capped and redacted — 2 cases |
| D13 | a reconnected SSE stream never re-read the state, so the window could sit on a stale snapshot | `src.onopen` re-bootstraps and renders — 3 cases |
| D14 | a second Send during a running task vanished with no message, no queue row, no session | `start_plan` and `run_job` hand the text to the queue instead of returning — 6 cases |
| D15 | the engine computed a remedy for every rejection and delivered it on only one path | `next_action` always travels with the error, with an unchanged-file sentence — 1 case |
| D16 | two create tasks were refused with sentences that could not help: "matches nothing" for a file the task never named, "would match anywhere" for a file that does not exist | a drift-specific remedy naming the asked-for file; path existence resolved before hunk shape — 3 cases |
| D17 | a dead token retried `/api/events` forever (33 rejected requests, blank page) | a counted budget, `src.close()`, and one plain sentence — 3 cases |
| D18 | a session write failed mid-apply because another process was reading the file | `atomic_json` waits out a momentary reader (20 × 50 ms) — 2 cases |
| D19 | the queue spent two messages and produced nothing — popped before `start_plan` could refuse | a row is removed only when `run_job` claims it — covered by D14's class |

`labels.SINGLE_WINDOW_STATUS` is new: D9 needed a status sentence only the web window can say (Tk's
`messagebox` holds the mainloop until answered), and rather than weaken the "both windows share every
sentence" test the exception is now named and tested.

## UI 3.5 — the four items the earlier rounds left unbuilt

Opened 2026-09-27 as the precondition for the `ecommerce` dogfood run (`docs/SPRING-MICROSERVICES-PLAN.md`):
the run needs a tool that can commit a milestone-per-branch history, and the run is the first thing that
will exercise them. **624 → 694 offline tests**, `node --check` clean, contrast PASS at 126 pairs.

### 1. Git task branches — the deliberate rejection, reversed

`docs/AUTOAPPLY-GIT-PLAN.md` refused this because "moving HEAD is a larger claim on the working tree than
anything else earned". That is still true, so the claim is now something the user asks for by name: the
branch chip became a button, the server confirms the exact refname before anything moves, and
`git_integration` still has no force, no reset and no network verb — the guard test that used to read
"the only git verbs are add and commit" now reads "add, checkout and commit", and says why `checkout` is
admissible in exactly two forms.

- **Names are generated, never accepted.** `task_branch_name()` builds `agent/task-<task words>-<session>`
  from a `[a-z0-9]` whitelist, so git's refname rules hold by construction. A task written in Arabic slugs
  to nothing, which is why the session tail is not decoration: without it every Arabic request in this
  project's own history would have landed on one branch called `agent/task-task`.
- **Two refusals before any write.** A folder on a detached HEAD has no named branch to return to, and an
  existing branch is not joined — two sessions on one branch would mix their commits, after which neither
  rollback can tell its own files apart.
- **The way back is remembered, not derived.** `git` cannot answer "which branch was this window on", so
  `base` is the one snapshot field that comes from controller state rather than from a probe, and it is
  in-memory on purpose: after a restart the history is the record.
- **A name that reads like a verb stays a positional argument.** `checkout` is a legal branch name; the
  argv is `["checkout", "checkout"]` with the lookup scoped to `refs/heads/`, and a name beginning with
  `-`, containing `..`, or carrying a `refs/` prefix is refused before a process exists.

Two findings from building it, both now pinned:

- **The action-parity test greps source and never executes it.** `git_branch` was "answered" by the
  scripted preview in every regex the parity test runs, and raised `NameError` on the first click because
  `fake.py` used `git_integration` without importing it. `test_the_scripted_handlers_execute_and_not_only_exist`
  now calls every non-blocking client action against `FakeController` and fails on the raise; it is the
  only test in the file that executes the preview's dispatch rather than reading it.
- **An untrimmed refname is a layout defect.** A real task branch measured **375 px** in a 593 px composer
  row — wider than every other control on its line. The chip now shows the tail (`…eview-script-fakesess`)
  and keeps the full name in the title, measured at **220 px**, with both directions verified live in
  `--fake`: `.screens/ui35-git-chip-01-taskbranch.png`.

### 2. Git-shaped rollback — built as an escalation, not a second undo

The 2026-09-26 rejection was "the window already has one rollback and a second would only create a way
to disagree with it". That objection survives as the design constraint rather than as a refusal: there is
still exactly one undo, and git appears only where that undo *cannot* act.

- `engine.rollback()` refuses with "Rollback would overwrite a later edit" when a file's bytes match
  neither the saved before nor the saved after — the session's copy is simply older than the disk. That
  refusal used to be the end of the road. Now, **only when this program made a checkpoint commit for this
  task**, the same click leaves an offer: `git_restore` restores those paths from `checkpoint()["before"]`.
- `git_integration.restore_paths()` is the one call in the tree that discards what is on disk, and its
  shape is the argument: the commit must match `^[0-9a-f]{4,40}$` (a word like `HEAD~3` is a claim about
  history this program cannot see, and a branch name can move before the click lands), the paths come
  from the proposal, and each file is restored by itself so one git does not know cannot widen the blast
  radius. `reset`, `clean`, `checkout .` and `--force` are still absent, and a test asserts it.
- **The offer dies with the task.** `start_plan` clears it, because the previous task's "before" is the
  wrong answer for a path both tasks touched.
- **One remedy per fact.** `friendly_error` already answers this PolicyError with "create a new proposal
  to preserve your edits". Where a git copy exists that advice is wrong, so the row states the refusal
  and the restore in one sentence instead of adding a second opinion beside the first — the same rule
  UI 3.4 §C made about wording.
- **Measured, not assumed:** `checkout <hash> -- <path>` writes the index as well as the worktree, so a
  file restored to behind HEAD shows as `M  src/a.py` (staged) while a neighbour's uncommitted edit stays
  ` M`. The live test asserts both, because the honest statement is "the bytes are back", not "the tree
  is clean".

The pill is red — `--bad-ink` on `--bad-bg`, the pair the failed run already wears and the contrast audit
already grades — and it is the only control that appears in the composer row rather than on a card,
because it is the only one that replaces current work. Verified live in `--fake`:
`.screens/ui35-git-chip-02-restore-pill.png`, measured 110 px, and gone after the click that took it.

### 3. The Host seam — built as four named verbs plus the one decision that had drifted

Code-review item 16 recommended two things: extract the shared decision, then move the ten
≥80 %-identical methods of `gui.py` and `controller.py` into a base both windows subclass. **The first
is built; the second is not, and that is a deliberate deviation.**

- `src/ai_code_engineer/host.py` now owns the four verbs the review counted — `say`, `line`, `ask`,
  `stream` — as a documented contract, and `apply_prompt()` as the one Apply question.
- The drift it removes was real and measurable: the web window passed `repair.must_ask()`'s reason into
  its dialog and **Tk never called `must_ask` at all** (it has no Auto-Apply either), so the same
  proposal warned about different things depending on which window was open. `gui.apply()` now builds
  its `messagebox` text from the same builder and passes the reason.
- Three pins in `tests/test_host.py` are what keep it from coming back: both windows must call
  `host.apply_prompt(`, the sentence `"file(s) to\n"` may exist in **one** file in the package, and
  `Host`'s verbs may not overlap `contract.Controller`'s methods — presentation must not start owning
  orchestration decisions, which is how the two lists blurred before.

**What was not done, and why.** Moving ten methods under a shared base means rewriting both
presentation layers at once against a suite that cannot see presentation. The review itself ranked
that as the only item in the document that "rewrites both presentation layers", scheduled it after the
P0s, and the P0s have since fixed the two drifts it cited (#7 the Tk timeout, #8 the unredacted Tk
log). The seam is now the place those four verbs have to go through; a later round can move the methods
one at a time under a name that already exists. `docs/CODE-REVIEW-2026-09-26.md` item 16 should be read
as *half built*, not closed.

### 4. The Tk window reads Arabic — measured as twenty duplicated sentences, not as a wish

The claim in the old note was "`gui.py` reads `STATES` directly and owns its own English strings".
Measured before touching it: **20 status sentences are byte-identical between `gui.py` and
`controller.py`** (11 more are Tk-only, 25 web-only). That intersection is item 16's "52 against 72"
seen from the other side, and it is the part worth closing.

- `labels.STATUS_TEXTS` now holds those twenty as `(english, arabic)` pairs and both windows ask for
  them by key through `labels.status_text(key, arabic=…)`. **The English is byte-identical** — a test
  pins five of them verbatim, because a sweep that also reworded the UI would not be a sweep.
- `AgentWindow.arabic` is the same rule the web window already had: the session's task, then the last
  thing the user typed, then English. Tk had none, which is the whole reason the fallback window
  stayed English.
- `STATES.get(...)` is gone from `gui.py`; the headline and the reopened-history rows go through
  `state_label(state, arabic=…)`. History rows decide **per row**, because a reopened task list can
  hold an Arabic job and an English one.
- **Tk 8.6 has no bidi, and that limit is now written down where it was found.** Measured on this
  machine: a `tk.Text` rejects `-justify` as a widget option (the first attempt failed 36 tests that
  way) — `justify` is a *tag* option, so an Arabic bubble gets a `rtl` tag and the headline flips its
  `anchor`. Alignment is what the fallback window can honestly offer; the real RTL surface is the web
  window, where UI 3.1 gave every row `dir="auto"`. **Not visually verified**: Tk renders in a native
  window this round has no screenshot path for, so the claim is "the tag and the anchor are set",
  which is what the four new `test_gui` cases assert.

## UI 3.4 — what the agent did, said while it does it

A specification arrived in English with an emoji vocabulary: step messages in the chat for each action
(`📖 read_file`, `🔍 search_code`, `📁 list_files`, `✍️ propose`, `⛔ blocked`), a status bar kept
current in real time, and sentences for Apply / Executing / passed / failed with error snippets.
**603 → 624 tests**, offline; `node --check` clean; contrast PASS at 126 pairs (the strip reuses
`faint` on the page background, already graded). Plan and the measured premises:
`docs/UI34-ACTIVITY-PLAN.md`.

**The channel existed and the actions did not use it.** `engine.plan(..., progress=print)`
(`engine.py:384`) already streams a turn line, the file-not-found note and the auto-read note, and the
CLI and the Tk window consume that same stream (`gui.py:881` renders it into its status label). A
successful `read_file`, `search_code` or `list_files` recorded a *session event* and never said a word
(`:510, :538, :544`). So the work was in the call sites, not in plumbing: `labels.step_line()` builds
the sentence, `plan()` announces it through `progress` **and** a new optional `step` sink, and only the
web window passes `step` — which is how the chat gets the action lines without also getting the turn
counter, and how the CLI and the fallback window keep the single stream they always had. Consecutive
identical lines collapse, because a small model re-reading the same file is common enough to fill the
thread with one sentence.

**The density question was measured before it was answered.** Across the 39 sessions stored in
`.agent-runs`: 24 contain **no** tool event at all, median 0, maximum 5, 34 in total (small models
die on the protocol long before turn twelve — `rejected_action` appears 44 times). One row per step is
therefore 0-5 rows on a real task on this machine, not the wall of noise a per-turn design usually
means.

**The status line was being written into the wrong element and then erased.** Measured in the running
page before touching it: a `status` event assigned `$('title').nextElementSibling`, which is the header
*subtitle*, so "Connecting to the model…" replaced `demo2 · step 2/5 · qwen2.5-coder:1.5b`; the next
state push restored the subtitle and the live line vanished **while the job was still running**, even
though the client had kept it in `state.status` and never drew it. `#busybar`, the only real status
strip, measured `textContent: ""` at a computed `height: 2px` — a shimmer with no words. Now:
`status` is a snapshot field, `_progress` keeps it current, `renderHeader()` adopts it on every push,
and `paintStatus()` draws it in the strip — 26 px of `faint` text, RTL when the line is Arabic, the
shimmer kept as a 2px `::after` while a job runs, visible when there is a line **or** work in flight.
At the end of a job, if nobody replaced the running line it is dropped, so "Connecting to the
model…" cannot sit there after the answer arrived.

**Item 3 was half shipped, and the missing half was the phantom defect.** `runner.summarize` already
builds the "(<details>)" the spec asks for (`Maven test: passed (41 tests, 0 failed, 0 errors (JUnit
XML), 41.2s)`), and Auto-Apply already wrote a notice. A **manual** Apply left no trace in the
conversation at all except the git row — `_applied` ended in
`self.status = "Changes applied. You can check syntax…"`, which reached nobody. It now adds
`labels.applied_line`, and a run says `⚙️ Executing: python -m unittest discover -s tests -v`
**before** the child starts, because a project's own build executes code the repository defines.

**One security finding the request itself exposed.** `run_tests.done()` calls
`report_run(pair[1])` — the **raw** runner result. `runner.run` builds `tail`/`failures` straight from
the process output and never scrubs it; the two surfaces that already showed command output redacted
on their own paths (`repair.record_run` for storage and the model, `_build_line` for the live stream).
So the error snippet the spec asked to put in the chat was the one copy of the log that could carry a
printed `DATABASE_URL=…` into the thread — from where UI 3.3's copy button puts it on the clipboard.
`report_run` now redacts every field it interpolates, keeps `repair.FAILURES_KEPT` lines rather than
the ad-hoc 8, and ends with the pointer to Activity, where the full redacted stream already is. There
is a test that fails if the secret reappears.

**Three places this deliberately did not follow the spec's letter.**
- The strip is the existing bar under the header, not a new one above the composer: the dock already
  holds the queue strip and the composer, and the header bar was the element that owned this job.
- "formatted error log snippets" means ` · `-separated plain text, not markdown: tool rows are escaped
  and never rendered, and the pill collapses newlines — the same rule that made this morning's
  `**Checks**` line print literal stars.
- `⛔ Blocked` did not become a second chat row. The failure row from `friendly_error` already states
  the model's reason plus the remedy, so the row now carries the ⛔ instead of a duplicate sentence
  under it.

**Verification:** 624 tests. Seven new controller cases pin the step rows in the thread, the collapsed
repeat, the Arabic twin chosen by the task rather than the window, the manual apply row, the executing
line, the redacted failing build and the blocked row, plus one that the running line does not outlive
the job. Seven `test_labels` cases pin the sentence family
across both languages, including that paths, commands and file names stay Latin inside an Arabic line
and that an action `step_line` has never met still says something — a new tool vanishing silently is
the failure worth designing against. Six client cases pin that a status event no longer touches the
subtitle and that the strip is words, not pixels. Live, from the `--fake` window: the strip measured
26 px tall showing `Turn 3/12: asking qwen2.5-coder:1.5b...`, an injected `status` event landed in the
strip with the subtitle unchanged, an Arabic line came out `dir="rtl"`, a state push restored the
server's own line instead of erasing it, and the four scripted step rows rendered with `Steps` as
their author and a copy button each.

## UI 3.3 — a model list you can search, and a copy button on what the server said

Three items asked for in Arabic, planned in `docs/UI33-MODEL-FILTER-COPY-RESEND-PLAN.md`, and two of
the three arrived as corrections to work already on screen. **586 → 603 tests**, offline; `node
--check` clean; contrast still PASS at 126 pairs.

**The model search already existed, and it was destructive.** Settings → Models had a *"Filter models
by name"* box whose server side ended in `self.catalogs[self.mode] = kept` and then
`self.model = ""` — so a search threw away every model that did not match until the next **Refresh
models**, and a search that stopped matching your current model **deselected the model your task was
running**. It also reached `_save_state()`, writing the preferences file once per keystroke. Three
defects in one function, and the Tk window had none of them: `gui.py:1208-1217` filters a *computed
list* into the combobox and checks membership against the full catalog, so this was drift introduced
when the feature was ported to the web controller, not an original decision. `filter_models()` is now
`visible_models()` — a read — `set_filter` assigns a string and nothing else, and the snapshot serves
the view. A hidden model stays selected and still describes itself in the settings line.

**The filter was also on the wrong surface.** The list you actually pick from is the model pill's
sheet, which had no filter at all, while the Settings box drew no list — so typing there had its only
visible effect somewhere else. The sheet now filters client-side against the entries it already holds
(a keystroke costs no POST and no state push), matches name *and* description so `3b`, `7.37 GB` and
`cloud` all work, counts as *N of M models*, says so when nothing matches, and **Enter** picks the
first row. Settings keeps its box, now over the same non-destructive view.

**Copy went on the server's rows, and only those** — the assistant's reply and the tool notices — after
the user narrowed the request from "every message" to "رسائل السيستم، مش المستخدم". Two constraints
came from reading the file rather than from the request: the button sits in the body column and not in
the bubble, because `appendToken` rewrites a streaming reply's `innerHTML` on every chunk and would
take its handlers with it; and one delegated listener on `#thread` answers all of them, resolving the
row's index against `DATA.messages` at click time. It copies `message.text` — markdown source for a
reply, plain text for a notice — never `innerHTML`.

**The row-level resend was dropped by the person who asked for it** ("مكانش ليه لازمة"): `↻ Again`
stays the only repeat path. What the request did expose was its obsolete guard. `askAgain()` refused
while a task ran — *"Wait for the running task, then ask again"* — which was correct when the composer
was disabled during a run and became wrong the moment the queue made it typable: the refusal blocked
the path that works, and `tests/test_webapp.py` had that string pinned. That is the round's only edit
to an existing assertion, and `assertNotIn("DATA.busy", …)` now holds the new behaviour in its place.

**A Checks line was markdown in a plain-text row, found by running one module on its own.**
`tests.test_controller` failed an assertion the full suite had been passing for hours: `report_run`
wrapped its summary in `**…**` and fenced the failure sample in backticks, into a tool row that the
thread escapes and never renders — so the run result arrived with literal stars, and its `\n\n`
collapsed into one long line inside a rounded pill. It had been passing only because the chained run
had not finished by the time the assertion looked at the thread; a faster machine turned the flake
into a failure and the failure into a find. Those rows are one sentence now, joined with ` · `.

**`--fake` answered 22 of the 42 actions the page posts**, silently: its dispatch fell off the end and
returned `None`, which is why the model filter could sit dead in the window designs get reviewed in
while every test stayed green. The real controller answered all 42; the scripted one now does too —
the state it can hold holds it, the ten gestures whose interesting part is a dialog or a disk read
answer with a sentence naming that limit (`fake.PREVIEW_ONLY`), and anything unsaid falls to
*"Not scripted in this preview: <name>"* rather than to quiet. Action parity is a test now
(`tests/test_webapp.py`), reading the client's own list of `send('…')` names.

**What no test could catch, and the page caught immediately.** The new `matches()` was written with
`.casefold()` — a Python method, not a JavaScript one — so opening the model sheet threw and the pill
looked dead. `node --check` parses, it does not run. Driving the page found it on the first click.

**Verification:** 603 tests, and the live measurements behind them: the sheet opened on 7 models,
`cloud` → *1 of 7*, `13b` → the one 13b row, `zzz` → the empty line, clearing restored all seven, and
**Enter** after `gemma` set the pill to `codegemma:2b`; the Settings box narrowed the served list 7 → 1
while `provider.model` stayed `codegemma:2b` — the deselect bug measured fixed; copy rendered on rows
0, 2 and 3 (assistant, assistant, `Changes` tool row) and **not** on row 1, the user's own; the click
put the message's raw text on the clipboard and raised the existing toast; `↻ Again` with `DATA.busy`
true filled the box with 100 characters and gave the success line, not the refusal. The button measures
24–25 px in `--faint`, which the contrast audit already graded.

## UI 3.2 — a queue for messages sent while a task is running

Asked for in Arabic: send a message while a request is still running, see it as a line, and per line
take it into a separate chat, edit it, or delete it. Planned in `docs/QUEUE-PLAN.md` after measuring
the baseline, which turned out to be the real bug: `run_job` answered *busy* with a bare `return`
(`controller.py:317-319`) and `start_plan` did the same (`:1154-1156`), while the composer was
`disabled` to make the loss look designed. **565 → 586 tests**, offline, green at every step;
`node --check` clean; contrast still PASS at 126 pairs.

**Decision A came back against the recommendation.** The plan recommended that a queued line wait for
a click when the running task ends, on the project's own "no click, no work" rule; the user chose
**run it automatically**. So the queue is the one path in the app that starts work without a click at
that moment, and the rule now reads: **the click that queued the message is the approval.** Stated
without softening, because it is the second time this line has moved — in a folder with **⚡ Auto-Apply**
on, a request you queued five minutes ago can write to disk while you are looking at something else.
What is preserved is that the folder's own rules still run at that moment: Change still proposes and
stops at a proposal unless the switch is on, Chat still answers in prose, the two cases that always
ask (emptying an existing file, a folder whose previous apply was interrupted) still ask, and **Stop**
holds the queue — a stop followed instantly by the next message is not a stop.

**The drain is event-driven, at three call sites that are three different bugs.** `_drain_queue()`
runs at the end of `worker()` (`:352`), inside `queue_add()` (`:391`) and after a branch switch
(`:699`); there is no timer and nothing polls. The worker's last line is the only moment known to be
"this task has finished", and because a chained job (propose → apply → run under Auto-Apply) keeps
`busy` set until the chain is truly done, the strip waits for the last step instead of firing after
the first. `queue_add` drains itself because a click can land *after* the task it queued behind has
already ended, and a line that says "Queued" and never runs is worse than one that just sent. Going
back to a chat drains it, because items belong to the chat they were typed in. The recursion that
would otherwise exist — detach → `_drain_queue` → `new_chat_in` → `_select_branch` → `_drain_queue` —
is closed by a `self._draining` flag and an `if not self._draining` at the branch hook.

**Branch identity is the rule the queue had to follow**, so each item carries `chat`, `branch` and
`project`, and only `item["chat"] == self.chat_id` may fire. Items waiting for another conversation
stay in the list but are not drawn here; the strip says *N waiting in another chat — open it to see
them* instead, because an invisible line that fires later is exactly the surprise this strip exists to
avoid.

**Decision B: ↗ opens a new chat in the same project.** `_select_branch` refuses to move the branch
while a job runs — that gate is what stops a chained apply from finding itself pointed at a different
folder — so a detached item is re-queued at the front marked `detached` with no `chat`, and the switch
happens when the drain reaches it: `new_chat_in(key)`, then the text is asked there. It never runs in
the conversation it left. The new chat opens in Chat mode like any other, and the rule already in the
app decides the rest: a message that opens with a build verb is planned as a change, anything else is
answered in prose. The queue invents no routing of its own.

**Decision C: in memory only.** The queue is not in `chat.json` and not in the persisted preferences,
so a queued change request cannot survive a reboot and fire against files that have moved on since.

**The server owns the sentences.** `labels.queue_notes(arabic, elsewhere)` builds the strip's four
lines and `queue_add` emits the confirmation itself, as the `{"kind": "toast"}` event the client has
handled since UI 2.7 and the *real* controller had never once sent — only the scripted `--fake` window
had, which is why the surface survived unused for so long. So this feature put the first real traffic
through the surface the phantom `self.status` needs. The client supplies glyphs and buttons only. Two
things are refused where they stand rather than dropped quietly: over 4,000 characters says so in the
conversation with the reason, and the same text twice in one chat stays one line.

**The composer is no longer disabled.** `ta.disabled` stays false while busy, **Send** reads
`Queue ↑`, and its placeholder says *"Ask the next thing — it queues until this task ends."*
`.composer.busy` lost `opacity: .72` for a 2 px accent border, because a box you can type in must not
look dead. The `--fake` controller carries two scripted rows (one plain, one detached) so the strip
can be reviewed without a model, and `fake.py` and `controller.py` shipped in the same commit because
`DATA.queue` would otherwise break the snapshot-key contract test. `contract.py` still declares five
methods: the queue is payload, not surface.

**Five of the first queue tests were wrong, not the code**, and both premises were measured before
rewriting them: counting model prompts to prove a message "ran" is fragile because one task can make
more than one call (measured: first task → 1 prompt, second → 2), so the cases assert on observables —
the strip, the thread's own rows, the prompt text; and one case asserted `session["task"]` after a
detached item and got `None`, because a new project chat answers in prose and prose has no session.
Correct behaviour, wrong assertion.

**Still not fixed:** `self.status` remains written in roughly sixty places and read by nothing but
tests. The queue shows the shape of that fix; it does not perform it.

## UI 3.1 — the notice speaks the task's language, and a file can be opened from the chat

Four objectives from the user's specification, planned in `docs/UI31-LANG-PREVIEW-PLAN.md` after
measuring each premise, and built on the recommendations written there (A: one helper in `labels.py`;
B: declined — see the Tk note; C: the card says both; D: dropped, no new endpoint; E: rail-or-fallback).
**525 → 565 tests**, offline, green at every step; `node --check` clean; contrast PASS at 126 pairs.

**Our sentences now follow the rule the model already had.** `chat.py` and `engine.py` already told
the model to answer in the language it was asked in; the strings *we* generate were English-only, and
nothing in the codebase could even tell the two apart — `ARABIC_MARKS` strips diacritics and
`_words()` folds words to route modes; neither classifies a script. So: `labels.is_arabic()` over the
Arabic block, `labels.say(arabic, en=…, ar=…)`, and the eleven `STATES` entries mirrored in
`STATES_AR` with a test that fails if the two key sets ever differ. `AgentController.arabic` reads
the session's task and falls back to the last thing the user typed, so the language is the task's and
not the window's. The apply family — `write_notice`, `applied_note`, `checkpoint_note`,
`artifact_card` — builds whole sentences in `labels.py` rather than f-strings spread through the
controller, because `repair.py`, `gui.py` and `controller.py` all emit sentences and the last round's
lesson was *one rule, one place*. Paths, identifiers, `--no-verify` and a commit hash stay Latin in
both languages; a tool row is plain text, so nothing in these strings uses markdown (tested).

**`DATA.banner` was an int and the card guessed the rest.** The English sentence lived in `app.js`,
which is why localising the server alone would have left the card English. The snapshot now carries
`{"count", "text"}` with the sentence already chosen, and `Number(DATA.banner)` is gone. Three
existing assertions pinned `banner` to an int and were updated to the new shape — the only test
content changed this round, and only where a behaviour change is what is being asserted.

**The card stopped lying about the write.** `_artifact` built `f"{n} proposed file(s) in {folder}"`
for every state, so a folder that had Auto-Apply on read "proposed" after the files were on disk.
`artifact_card` now takes `written`, the title becomes `N file(s) applied to demo2 and saved to disk`
(or its Arabic twin), and `written` travels in the payload so the chips can label themselves
honestly. The `APPLIED_UNVERIFIED` headline says both facts on separate lines — applied *and*
"project tests have not run" — because replacing the verification signal with a disk statement would
have deleted the one thing that separates "written" from "proven" (objective C, resolved that way).

**A chip in the chat opens the file in the rail without leaving the conversation.** The file list
comes from `DATA.review.files`, not from the message: messages persist as `{role, content}` only, so
a per-message `files` field would have been dropped on every replay, whereas the review block is
already keyed to the live session and empties itself on a rollback. Clicking one reuses
`select_file` and paints `DATA.review.view` in `#rail` with **Diff / Now / Was** tabs. The
speculation that this needed a new file endpoint (plan item D) turned out to be wrong in the good
direction: `changes[]` already stores the complete `before` and `after`, so **no new HTTP read
surface was added at all**, and `contract.py` still declares five methods.

**One diff painter, two surfaces.** `diffRows()` was already a pure row model, but its DOM emitter
was inlined in `renderReview`; it is now `paintDiff()`, with `paintCode()` beside it for whole-file
tabs. Both isolate path text LTR (`unicode-bidi: isolate`), so an Arabic interface does not mirror a
file name.

**Shortcuts yield to typing, and the icon offer waits for a quiet moment.** `Ctrl+K` / `Ctrl+B` had
no `activeElement`, `altKey` or open-dialog check, so AltGr — Ctrl+Alt on an Arabic layout, the key
that types `÷` — opened the command palette mid-word. `offerIcon` now defers behind the same
`handsBusy()` test with a capped retry and a cancellable timer. The `Ctrl K` chip printed on the
**New chat** button was false: `Ctrl+K` opens the palette and `new-chat` was a plain click. Rather
than delete the affordance, `Ctrl+Shift+K` is now bound to a new chat and the chip says so.

**Two bugs only the running page could show.** The first cut of the guarded handler matched
`Ctrl+Shift+K` in the plain `Ctrl+K` branch too, so it opened the palette *and* started a chat — the
collision class this item was fixing, reproduced by my own fix. And `dir="auto"` on a tool row can
never resolve RTL for an Arabic notice, because the row's first strong run is its English author
label "Tool": the row now takes its direction from `ARABIC_RUN.test(m.text)`, measured live at
`direction: rtl` beside an English row still `ltr`.

**Follow-up the same day — "↻ Again".** The user asked for a way to re-send the message already in
the chat when the model misfires. It refills the composer and stops: one click that *sends* would be
one click that can write files, and on a folder with the switch on nothing reviews what follows. It
also refuses when the box holds unsent typing or a task is running, and the pill is not drawn at all
when the thread has nothing of yours in it. No server action was added — the text is already in
`DATA.messages`, so `contract.py` still declares five methods and only `submit()` calls
`send('send')`, which is now a test.

**Deliberately not built.** The Tk window stays English: `gui.py` reads `STATES` directly and owns
about forty of its own literals, and half-translating the fallback window is worse than a documented
limit. And the phantom `self.status` — written in roughly sixty places in the web controller, read
by nothing but tests — is recorded in `docs/VALIDATION.md` as the next round's work rather than
papered over by translating strings nobody sees; the `status_*` helpers drafted for exactly that were
deleted once the measurement said so. (UI 3.4, later the same day, built the surface — the field is
rendered now, and the sentences written into it are the ones that still need reviewing.)

## Previous release: UI 2.8 — the folder belongs to the selected branch, not to the program

The window used to open on one remembered path. `.agent-projects.json` stored a single
`last_project`, `AgentController.__init__` applied it to everything, and `new_task()` reset every
piece of state except that folder. So a user who double-clicked the app and typed "HI" sent a
greeting into the propose loop against `examples/demo_repo`: the engine prompt offers only
`propose` or `blocked`, the model proposed a file it had just read unchanged, and the run died with
`Model could not produce a proposal: Proposal contains an unchanged file: calculator.py`. Plain chat
was not a mode — it was the absence of a project.

**`self.repo` is now derived.** `_select_branch(kind, key, chat_id=…, composer=…)` is the only writer
of it, of `current_project`, and of the mode, and it refuses a key that is not in the granted
registry. A sidebar entry is a branch: `chat` (no folder, prose only) or `project` (its folder, its
context). `New chat` creates a chat branch and leaves the open project in the tree — it is no longer
a hidden workspace switch. `last_project` is gone from the saved state, replaced by
`last_branch: {"kind", "key"}` plus a per-project composer preference; the granted folder list is
still restored, so nothing the user approved disappears, but nothing is pointed at on its own.
`set_repo` survives as the programmatic "work on this folder" entry point and selects the same
branch in Change mode, which is what the saved-task path and the existing tests use.

**Chat and Change are one visible toggle.** The first pill in the composer says `Chat`, `Chat · repo`
or `Change · repo`, and the header line reads `granted-repo · chat · reads as context` or
`… · change · reviewed diff`. A restored or drop-bound project branch starts in **Chat**, so a
greeting is answered; **granting a folder** — `＋ open`, `＋ new`, opening a saved task, attaching a
plan, the sample — selects Change, because naming a folder to build in is already that decision.
`start_plan` routes to `start_chat` whenever the mode is chat, so
the propose path cannot be reached by accident, and `friendly_error` now answers an editless message
with "Switch the badge next to Send to Chat mode" instead of quoting the internal refusal.

**A bound chat reads, and still cannot write.** `chat.json` gained `project: {key, path}`; a missing
key is `null`, so the chats already on disk load unchanged. `chat.py` grew `context_block()` and a
second prompt: a bound chat is handed `Workspace.repo_map()` and the project's standing notes
labelled as untrusted data collected in advance, and is told it cannot open, search, create or
modify anything and should point at Change mode when the user wants an edit. The map is built inside
the worker thread — walking a 300-file folder does not belong on the UI thread — and any
`PolicyError`/`OSError` degrades to answering with no context rather than failing the question. Both
variants call `generate(..., json_mode=False)`: there is no action envelope to parse.

**Dropping a chat on a project binds it.** Chat leaves are draggable; a project node accepts the
drop, shows a ring while the drag is over it, and sends `bind_chat {chat, project}` — a registry
**key**, never a path, so the browser cannot aim a conversation at a folder the user has not granted.
`bind_chat` also validates the chat id against `[a-f0-9]{32}` before building a path from it, and a
chat that is not the open one is only rewritten, never announced into the wrong conversation.
Detaching keeps every turn. Because a drag is not reachable from a keyboard, the same command is on
the leaf's **⋯** menu (**Move to project… / Leave the project**) and in `Ctrl+K`. Binding adds read
context only: a bound chat still cannot propose, which is asserted by test.

**Sidebar is the only control.** `＋ open` and `＋ new` sit in the Projects header; the right panel's
project row became a label, so nothing outside the tree can repoint the window. The Tk fallback keeps
its own layout but not the defect: `new_chat()` detaches the folder, and `last_project` is no longer
applied at startup, so the fallback cannot reintroduce the greeting bug.

**First use on the author's machine found five more.** A long thread pushed the composer out of the
window: `.main` is a grid item, whose automatic minimum is its content height, so the column grew
past `100vh` and with `body{overflow:hidden}` there was no scrollbar to reach what you were typing —
`min-height:0` puts the scroll back in `.scroll`, and the textarea got `overflow-y:auto`. The folder
picker could not leave C: parent to walk up to, and a project that does not exist yet
cannot be clicked into), so `list_dir` now returns `roots` from `GetLogicalDrives`, the picker shows
them as chips at a drive root, and it has a path field — type `D:\AI\spring-project`, Enter to browse
it, **Use this path** to take it as the answer; `＋ new` starts in the current project, and opening a
path that is not a directory is refused rather than adopted. The project's full path is now a pill
beside the mode badge (tooltip, click to copy) and on the sidebar node. Chat-everywhere had made
"create me a Spring project" into a prose lecture, which is why granting a folder selects Change mode
while a restored or drop-bound branch keeps Chat. And `Model output truncated` now reads as advice —
one file at a time, or raise the output limit in Settings.

**Verification:** `python -m unittest discover -s tests` → **226 tests pass offline** in ~45 s, up
from 194. 17 new `BranchTests` cases in `tests/test_controller.py` cover the launch rules (a restored
project branch answers a greeting in prose; a chat branch restores no folder), a chat created inside
a project recording that project and appearing under it, the bound prompt carrying
`Repository context` while the standalone one carries nothing, two chats in one folder not sharing
history, `bind_chat` by key, refusal of an unknown key and of a `../../evil` chat id, detach keeping
turns, reopening a bound chat in chat mode, the badge reaching the reviewed path while writing
nothing, the mode being remembered per project and refused without one, and the new error wording.
9 new `tests/test_chat.py` cases cover prose-only generation, an empty or failing answer storing no
half conversation, the project link surviving a restart, and a 12 000-character map plus a full
4 000-character note staying inside `context_chars` with the newest turn intact. Five more cover this
round's fixes: a drive listing that carries `roots` and no parent at a drive root, a typed path
creating a project outside the home drive, refusing a path that is not a directory, Change mode plus
its disclosure notice when a folder is granted, and the truncation wording. One Tk assertion was
rewritten, not deleted: `test_preferences_survive_restart` now asserts the folder is *not* reattached
while it stays in the granted list.

The page was then driven live against a scripted model (no real inference, `sandbox/verify-ui-28.py`)
and screenshotted at each step: launch on a standalone chat, "HI" answered in prose with the chat
listed under **Chats · no project**, the drop ring while dragging, the leaf moved under
`granted-repo` with the header reading `chat · reads as context` and the composer notice in the
conversation, the badge flipping to `Change · granted-repo`, the proposal diff, the Apply dialog
naming the folder, `Applied — project tests have not run`, and `Changes rolled back` with
`calculator.py` restored on disk. The saved registry after that session held `last_branch` and the
per-project composer pref, and no `last_project`. Contrast was recomputed from `tokens.css`: the two
new pill colours reuse the existing chip and OK pairs, 108 pairs, 0 failures.

A second live pass checked this round's five: creating `D:\AI\AI-Agent\sandbox\picker-demo` by typing
the path (the folder, the `Change · picker-demo` badge and the `D:\…\picker-demo` pill all on screen,
with the grant notice in the conversation), `C:\` offering `D:\` and `E:\` as chips once it had no
parent to walk up to, and a 1 631-pixel thread inside a 246-pixel scroller that reaches the bottom and
keeps the composer — and the text in it — in view.

**Known flake, not from this release:** `test_webapp.test_an_oversized_body_is_refused` intermittently
raises `ConnectionAbortedError` on Windows. The server answers 400 before reading a 1 MB body, and the
client is still sending; it passes standalone and fails in about one full-suite run in three.
**Fixed in UI 2.9:** the body is now drained in bounded chunks before the 400, and the test asserts
the server is still serving afterwards.

## Current release: UI 3.0 — proactive writing, git checkpoints, and one switch that ends a click

Five items from the "Proactive File-Writing + Native Git" spec, planned first in
`docs/AUTOAPPLY-GIT-PLAN.md` and built in six steps. **317 tests before, 402 after**, offline, green
at every step; `node --check` clean and `tools/check_contrast.py` at 108 pairs, 0 failures, because
every new colour reuses the already graded `--ok-*` and `--warn-*` pairs.

**Two findings changed the spec before any of it was written.** `git branch --show-current` is a git
2.22 flag and this machine runs 2.15 — measured, not assumed — so the branch is read with
`symbolic-ref --short -q HEAD` and a test asserts the missing flag is never asked for. And
`git commit -am` would have swept the developer's unrelated edits into an agent-authored commit while
`git reset` would have deleted work no proposal ever proposed; both were replaced with a path-scoped
`git add -- <the proposal's files>` + `git commit --no-verify -m "agent: … [session-…]"`. A test now
pins the whole vocabulary the program can ask git for — `rev-parse`, `status`, `symbolic-ref`, `add`,
`commit` — so `reset`, `clean`, `checkout` or any network verb would fail the suite rather than a
repository.

**`git_integration.py` is 254 lines and reads.** Five local commands per inspection, argv only,
`shell=False`, `cwd` the granted folder, `runner.child_env()` plus `GIT_TERMINAL_PROMPT=0`,
`GIT_OPTIONAL_LOCKS=0` and a 4 s bound, with `--no-optional-locks` and `-c core.fsmonitor=false` on
every call because a repository on disk is not trusted code — configuration and hooks come from the
folder git runs in, and `status` can start a daemon. `dirty` is `None` rather than `0` when `status`
fails or times out: a repository that did not answer is not a clean repository, and the chip would
say so. `status()` caches for 8 s (~0.2 s to fill on Windows) and is what `snapshot()` uses through
`_git_info()`, which forwards five fields and no path list. `forget()` runs after an apply and after
a rollback so a chip never shows the state from before a write the user just approved. The chip is
`gitChip()`: branch (or a 7-character HEAD when detached), green when clean, amber with the count
when paths differ, and nothing at all for a folder that is not a repository.

**The route is inferred per message, not per session.** A bound chat whose first five folded tokens
ask for files is planned as a change instead of answered: a build verb (`add`, `fix`, `implement`,
`scaffold`, `setup`, `generate`) or an Arabic one (`أنشئ`, `صلح`, `عدل`, `ضيف`, `طوّر`, `سوي`, `ركب`)
anywhere in the window, or a wanting word (`عايز`, `محتاج`, `ياريت`, `ممكن`, `ابني`, …) in it — because
"عايز اعمل مشروع" opens on the wanting word, not the verb. Spelling is folded first (alef/hamza forms,
`taa marbuta`, tashkeel including shadda). Two shapes are refused before any of that: a message opening
with a question word, and a message ending with a question mark — which is what separates "can you fix
this" from "can you add a column?", and `هل ممكن…؟` from `عايز تعمل…`. Nothing is written to
`_composer_pref`: the next "thanks" is prose again, which is the whole reason the route is not simply a
mode switch.

**A code block can become a file, through the same door a model uses.** `blockTarget()` reads a
header line (`# path: src/main.py`, `// calculator.js`, `<!-- notes.md -->`, a bare name) and only
accepts it alone on that line with an extension, so a comment that happens to mention `src/old.py`
stays a comment. The button appears only when the branch has a folder, and `engine.propose_block()`
runs the block through `prepare_changes` — policy, traversal, syntax, size — producing an ordinary
`WAITING_APPROVAL` session whose `proposal_hash` covers the same fields. The handler is wired in
`renderThread`, not inside `mdToHtml`, because `appendToken` repaints the streaming bubble on every
chunk. `join()` also learned to wait for a chain of jobs, since a proposal's completion can now start
an apply whose completion starts a run.

**⚡ Auto-Apply is per folder, off by default, and it is the only click this release removes.**
`auto_apply_ready()` fires from the plan and fix-round completions; `apply` skips its confirmation
when the switch is on, `_applied` sets `snapshot()["banner"]`, says so in the conversation, and runs
the project's own command afterwards. Two cases still stop for an answer: a proposal that empties an
existing file (`removal_notice`), and a folder whose previous apply was interrupted. An
**⚡ Apply to File** block still waits for Apply — that click already is the request. The user's
2026-09-26 ruling that a disk write always needs a conscious click is therefore *not* removed, it is
moved: the click is now the one that turns the switch on, per folder, and the red line still holds by
default in every project. None of this reaches the `--tk` fallback window, which has no pill, no block
button and no chip: there the **Apply changes** click is still the only door.

**Not built, on purpose:** task branches (`git checkout -b agent/task-…`) and a git-shaped rollback.
Moving HEAD in someone's repository is a larger claim on their working tree than anything else in the
spec earned, and the window's own **Roll back** — which refuses once a file has been edited after the
apply — is the single rollback path.

Verified in the real window against a scripted model, screenshotted (`.screens/ui30-01…09`): amber
`master 1` with a planted untracked file, green `master` after committing it in a terminal nine
seconds earlier (the cache expiry, not a refresh), `f4635e7` for a detached HEAD, no pill at all for
a granted folder that is not a repository, `⚡ Apply to File` on the headed block and its absence on
the unheaded one, the `WAITING_APPROVAL` card that click opened while `calculator.py` still held
`return a - b` on disk, the write after the explicit Apply plus `M calculator.py` from git, then the
pill back to clean after **Roll back**. One whole automatic turn was run before the page even opened:
`calculator.py` fixed with no click at all, a commit named
`agent: Fix add() so it adds the two numbers together [session-c89870d22215]`, `git status` showing
only `?? tests/__pycache__/` — proof the checkpoint took exactly the proposal's file and left the
build's droppings alone — and the amber `✅ Applied changes to 1 file(s) automatically` note whose
**↩ Roll back** opened the same confirmation the card does and then removed the note.

The §1 route was driven from the server side (`--routed`) rather than by typing into the window,
because sending a chat message from automation needs a confirmation this session did not carry: the
branch stayed in Chat, the task `add a logout button to the profile page` produced
`● Changes ready for review`, and the routing note sat between the two. 12 tests cover the same ground
(6 controller, 6 word-table, Arabic included).

### Follow-up fixes the same day — 402 → 409 tests

Six items from the review and from field reports. Three of the six premises were measured before
being coded, and two of them were not what the report said:

* **Intent routing widened** (`controller.py:64-133`). The opening-word rule was too narrow for
  natural speech: `عايز اعمل مشروع` opens on the wanting word and `I want to create…` on `I`. Now the
  first five folded tokens are scanned for a build verb, Arabic wanting words count anywhere in that
  window, and `setup`/`generate`/`سوي`/`ركب` joined the tables. The question veto was rebuilt around
  the *shape* of the sentence rather than its first word alone: a question word at the front still
  vetoes, and so does a trailing `?`/`؟` — which is the only thing that separates `can you fix this`
  (a request) from `can you add a column?` (a question). `REQUEST_MODAL` names the four openers that
  carry both readings, and they are released only when a `you` follows and no mark closes the sentence.
  The old `test_only_the_opening_word_counts` asserted the superseded rule and was replaced.
* **Modal flash** (`app.js:811-818`): scrim dismissal moved from `mousedown` to a `click` with a
  200 ms mount guard, so a dialog that mounts under the pointer mid-click no longer dismisses itself
  in the same frame. `offerIcon` (`app.js:139-152`) defers its picker by 400 ms for the same reason,
  and the deferred call re-checks the branch so a picker never opens over a project the user left.
* **Ollama repetition loops** (`providers.py:73-79`): `repeat_penalty: 1.1` beside `temperature: 0`.
  Greedy decoding on a small Arabic-weak model can park on one token; OpenRouter is left alone because
  it handles this server-side.
* **Dead code** (`controller.py:380-383`): an unreachable copy of `_git_info`'s docstring and body sat
  after `options()`'s `return`, a leftover from this session's own edits.
* **TypeScript generics** (`symbols.py:57-66`): the report said a generic class was dropped; measured,
  the *name* was captured and the base class was lost (`MyRepo` with no `extends`), while
  `function make<T>(x)` was dropped outright. One optional `JS_GENERIC` now fronts both, tolerating a
  nested argument (`Map<String, V>`). An intermediate version of that pattern was non-optional and its
  fill could swallow the `{`, which made every non-generic class vanish — caught by measuring the plain
  shapes before committing, and now a test of its own.
* **Backslash paths** (`app.js:410`): `blockTarget` refused only UNC, while `workspace.py` refuses
  *any* backslash ("Use a relative path with forward slashes"), so the Apply-to-File button promised a
  write the server would reject. It now refuses any backslash; the refusal was measured against
  `Workspace.path` first, and the promise holds.

Verified: 409 offline tests green, every module green standalone, `node --check` clean, and both
front-end fixes measured in the live page (four modal cases, ten `blockTarget` shapes, and the
deferred icon picker: no mount on the render tick, one scrim after ~1.6 s of throttled timer, a second
offer for the same project adds none, and none at all once the branch changed).

## The same day, after reading the whole project: 409 → 525 tests

The user asked for a review of everything (`docs/CODE-REVIEW-2026-09-26.md`, ~60 cited findings and
an 18-item punch list) and then said to fix all of it. Every item below is one that finding named;
the three whose premise turned out to be wrong are listed with what was measured instead.

**The P0: a chat dropped on a project could write files with no click.** `bind_chat` handed the chat
the folder's remembered mode, and if that project had been left on Change the bound chat inherited
Change — where `⚡ Auto-Apply` was reachable, so a prose conversation could reach the switch that
skips the review click. That crosses the line the project exists to hold. The branch now carries its
own `bound` flag (`_select_branch` is still the only writer), `set_composer` and `set_auto_apply`
refuse it with a sentence that says where the control lives, and the front end draws a bound chat
without the mode chevron or the Auto-Apply pill. Reproduced first (`sandbox/repro-bind-mode.py`),
then pinned by five tests, including one that clicks Apply on a bound chat and asserts the write
happens *after* the yes and exactly one dialog appears.

**Two more P1s were measured before being touched.** Credential redaction could not match
env-var-shaped names, so `AWS_SECRET_ACCESS_KEY=…` reached the session log and the next cloud turn
untouched: the `ASSIGNMENT` pattern now takes a `NAME_SECRET=value` qualifier, and it was checked
against ten real build-log lines and eight ordinary ones that must not be scrubbed (`tokenCount = 12`
is not a secret). And the repository map cost ~10 s per turn on a 300-file project because
`symbols.dependencies()` compared every file against every other: a reverse index over dotted
suffixes took it to 0.06 s with byte-identical output, and a test now fails if the sweep goes
quadratic again.

**The runner was leaking children.** Four separate findings, all fixed: `run()`'s `finally` now kills
a child that is still alive when anything raises — previously a throwing progress sink left a build
running with its pipe held; `kill_tree` had no Windows fallback and could itself raise on the way out of a
timeout, which would have taken a finished build's result with it; the last `process.wait(30)` was
unguarded; and `report_counts` read every candidate report with no size bound. One fix here was
caught by a measurement, not a test: closing the output wrapper before the child dies blocks forever,
because the reader thread holds the buffer's lock — the first version hung the suite for 60 s per
run. The kill now happens first, and `tests/test_runner.py` pins both the killed child and the
non-raising kill.

**Two claims in my own review were wrong, and the code says so.** The `@media (max-width: 860px)`
block is not dead scaffolding: `MIN_WIDTH` in `launch.py` is advisory only, so a narrow window really
does reach it. What is true there is smaller and stays unfixed on purpose — below 860 px the collapse
button is inside the panel the rule hides, so the sidebar cannot be brought back. Fixing that is a
drawer design, not a defect sweep. And `planbook`'s ledger key was reported as fixed when it was
not: `key_for()` still hashed only the folder's basename, so two checkouts named `demo` shared one
ledger and a verified step could cross between them. It now delegates the folder half to
`memory.key_for()` — the resolved, casefolded digest the notes already use — `open_book` re-reads a
stored `root` it does not recognise as a fresh ledger, and two tests cover both.

**One rule, one place.** The Auto-Apply refusal is `repair.must_ask(session, prior)`; the offer
sentences say the folder's switch state out loud, because they are the reason a user clicks Continue
and the old wording promised a rule the switch overrides; `removal_notice` exists once. The
request-timeout range lives in `config.py` and both windows clamp through it, with a test asserting
the browser's static `min`/`max` attributes match the numbers the server clamps to. Three bare
`ValueError`s became `PolicyError` so the reason survives `friendly_error` instead of becoming
"Could not complete the operation."

**The HTTP boundary closed.** A handler whose token was never set used to authorise every request
(two empty strings compare equal); it now refuses. A refused POST now drains its body and ends the
connection instead of pulling the socket out from under a client still writing. `/api/options`, which
no client ever called, is gone — and with it the drift it hid: `project_info` was called by
`server.py`, named by neither `contract.py` nor `webapp/fake.py`, so `--fake` threw on the first
click that opened the drawer. A test now reads the server's own source for every
`self.controller.<name>(` it calls and requires both controllers, and the Protocol, to answer them.
That test caught a mistake in the fix I had just written.

**Sidebar projects now open collapsed.** `state.expanded` is a sparse map, so nothing is expanded on
first paint — not even the project in front of the window. A search hit still opens its own project,
because a hidden result is not a result. The click is now the *only* other input: the branch clause
that used to OR into the same expression re-opened the active project behind every click, so that one
node could be opened but never closed. `tests/test_webapp.py` pins the shape of that decision, and the
whole sequence was driven in the live page: both nodes closed on load, one click opens, the next
closes, a search opens the matched project and clearing the box restores what the user had chosen.

**What the live check found that the review had not.** Reading the whole project and then driving the
`--fake` window turned up five more, each now fixed and pinned: `webapp/fake.py`'s snapshot had `git`
and `banner` nested inside `plan`, so the drawer rendered empty in exactly the build used for design
review; the preview snapshot and the real one are now compared field by field. `app.js` read a
top-level `DATA.chained`, a field the server never sends, which silently disabled the empty-**Send**
gesture meaning "next step" — the flag lives under `settings`, and the contract test that caught it
now reads every `DATA.<field>` the client touches against what `FakeController.snapshot()` answers.
`policies/` and `skills/` are blocked as config only at the repository root, where the agent's own
rules live, rather than in every nested folder that happens to use those names. `verification.py`'s
container cleanup now passes the scrubbed env and a 20 s bound like every other spawn, and its
snapshot refuses an unreadable file instead of failing a run over a permission-locked artifact. And
`git_integration._done` stopped using a bare `communicate()` that could hang on a git process leaving
a handle behind; it kills the tree on timeout and reaps, the same discipline as the runner.

Also fixed from the same list: `verification.py` spawns with `runner.child_env()` and `stdin=DEVNULL`
(restoring a stated invariant) and lowercases the suffix at both syntax gates beside `engine.py`;
`_jvm`'s type cap no longer dumps a past-cap class's methods into the file's own function list; the
unbounded `self.events` queue the web controller never read is deleted; and the ten dead CSS
selectors the review named are gone (`tools/check_contrast.py` still measures 108 pairs, 0 failures),
while `.send:disabled` and its `:not(:disabled)` guards stay as one deliberate piece of defensive
styling.

**Not done from the list:** the shared Host seam for the two windows (punch item 16). Four adapter
primitives would let ~10 near-identical methods live once, but it is the only recommendation in that
document that rewrites both presentation layers at once, it is not a defect, and this round already
removed the two drifts that were actually biting (items 7 and 8). It stays a written-down decision
rather than half an implementation.

**Verification:** `python -m unittest discover -s tests` → **525 tests pass offline** in ~100 s
(`test_agent` 74, `test_chat` 13, `test_controller` 106, `test_git` 41, `test_gui` 38, `test_memory` 10,
`test_planbook` 19, `test_redaction` 6, `test_repair` 16, `test_runner` 41, `test_symbols` 54,
`test_verification` 35, `test_webapp` 32, `test_workspace` 40). The suite is slow because of what it
drives, not because anything waits: timed test by test, the single longest case is 2.6 s and no case
sits on a real timeout — `test_gui`'s 38 withdrawn Tk windows and `test_controller`'s 106 real
`Controller` instances are the bulk of the wall clock. `node --check` clean, `tools/check_contrast.py`
PASS at 108 pairs over the 6 style/theme combinations, and the front-end claims above were each
re-read from the running page rather than reasoned about from the source.

## Previous release: UI 2.9 — ten items: drawer, per-language index, more toolchains, live logs, chunk diffs

Derived from the user's "Comprehensive Enhancement & Feature Specification" and built in the agreed
order (10 → 3 → 4 → 2 → 8 → 7 → 9 → 1 → 6 → 5). 226 tests before, **317 after**, offline, green at
every step; `node --check` and `tools/check_contrast.py` (108 pairs, 0 failures) re-run at the end.
Each item's design notes and the deviations that turned up are in `docs/ENHANCEMENTS-PLAN.md`.

**Stability first, because everything else is measured against it.** `_body()` reads and discards an
oversized request in 64 KiB chunks before answering 400 — Windows aborts the socket otherwise, which
was the suite's one flake. `gui.py` registers every `after()` id in `self._timers` through one
`_schedule` helper and cancels them on close, so the Tk teardown noise (`invalid command name
"…poll"`) is gone; measured at zero stderr lines.

**The sidebar is reachable from the sidebar.** `#sidebar-expand` lives in the header, Ctrl+B and the
button share one path, and the collapsed state is persisted with the rest of the UI prefs.

**Answers come back in the language they were asked in.** `LANGUAGE_RULE` is prepended to the chat,
bound-chat and engine prompts, and the same directive is in `SYSTEM` for `summary`/`checks`; every
JSON key, path, identifier and code block stays English. Bubbles and tool lines carry
`unicode-bidi: plaintext` with `dir="auto"`, and code blocks are pinned `direction: ltr`, so an
Arabic paragraph and an XML snippet sit in one message without mirroring each other. The window chrome
does not flip.

**One drawer per project** (`GET /api/project?project=<key>`): the resolved root with **Open in
Explorer**, the notes editor with its limit and the file it lives in, the context budget, and the
toolchain. The budget is measured through `chat.context_use()`, which runs the same `_messages()` the
request will use, so it cannot bill for turns the builder dropped; characters are exact and the token
figure is labelled `≈ N tokens (chars ÷ 4, estimate)`. `reveal` takes the **key**, resolves the path
from `self.projects`, and starts `explorer.exe`/`open`/`xdg-open` with a fixed argv and no shell —
ungranted paths, traversal strings and vanished folders start nothing.

**Eleven recipes instead of seven.** `uv-pytest` (ordered before `python-pytest` so a locked project
is not tested with whatever Python launched the app), `pnpm-test`, `cargo-test`, `go-test`, with the
same argv-only, `ENV_KEYS`-scrubbed, tree-killed, capped rules and no new environment variable. `go
test` prints no total, so a recipe may declare `test_ran` — a line whose presence is the evidence —
and cargo/Go green summaries joined the "not a failure" filter, because `test result: ok. 0 failed`
and a package path containing `errors` would otherwise send the model hunting a bug.

**The index learned three languages and stopped re-reading.** TypeScript/JSX, Go and Rust scanners on
the same comment/string-blanked text with the same brace-depth rule for methods vs calls; import
specifiers are read from the original line because the blanker erases them. `repo_map()` now asks
`symbols.indexable(name)` before reading anything and reuses a parsed row while `(mtime_ns, size)`
still match. Two long-standing holes in `dependencies()` closed with it: it split only on `.` (no Go
or JS path ever matched) and compared only tails (`from pkg.mod import name` never linked).
`.go`/`.rs`/`.mjs`/`.cjs` joined the readable suffixes — a Go or Rust project could not be read at all
before.

**Build output streams.** `runner.run` reads line by line and calls `progress` per line;
`_build_line()` redacts each one at that new transmission boundary and emits `{"kind":"log_chunk"}`
without touching the stored log, so a 5 000-line build cannot ride along in every snapshot. Spinner
`\r` redraws collapse to what the terminal would finally show, the tail cap still holds, and the
timeout path kills the tree then keeps reading what flushes. The browser keeps a 300-line live tail
that survives a state re-render. Fixing this surfaced a real bug: `_note()` emitted
`{"kind": "log", "kind": kind}`, and the second key won — the browser switches on `kind`, so every
stored log line was silently dropped until the next full render.

**A small change can be proposed as a small change.** `{"path","edits":[{search,replace}]}`, plus the
one-hunk shorthand, side by side with whole-file content (new files must still be whole). Resolution
happens in `prepare_changes`, not at apply time, so the diff the user reviewed is the bytes written and
`proposal_hash` covers the result. Matching is exact: zero matches names the line that is not there,
more than one lists every line number and says widen it, an overlap is refused, and there is no fuzzy
matching anywhere. Order is load-bearing and tested — a block that matches twice becomes unique after
the earlier hunk deletes the other one.

**Continuing is an answer, not a guess.** After a failed or timed-out run with no round armed, the app
asks *"Keep going? Fix round 1 of 3: send this failure output back to <model> … nothing is written
until you approve it"*, and a verified plan step asks before starting step N+1 unless auto-continue is
on. The wording lives in `repair.fix_offer()`/`step_offer()`. Declining starts no request. The
red line the user drew is untouched: generation may be automatic, the write never is.

Verified in the real window against a scripted model, screenshotted: the Arabic reply with its English
XML block, the collapsed rail and its expand button, the icon sheet, the drawer's four sections with
`1,232 of 24,000 chars ≈ 308 tokens (chars ÷ 4, estimate)` and the notes round-trip, nine `out` rows
streaming while `busy` was still true with `password = "[redacted]"` in place of the planted secret,
the same one-line fix rendered as `@@ -1,2 +1,2 @@` `+1 −1` instead of a whole-file rewrite, and the
"Keep going?" dialog over the diff with Cancel leaving `Python unittest: FAILED` as the last word.

## Security audit — 2026-09-24 (93 tests pass)

Every finding below was reproduced against the real code before it was fixed, and each fix has a regression test.

| # | Finding | Evidence before the fix | Closure |
|---|---|---|---|
| S1 | **8.3 short-name alias bypassed the file policy.** NTFS opens `TOKEN~1.JSON` as the same bytes as `token.json`, and `NODE_M~1` as `node_modules`, so both `SECRET_NAME` and `BLOCKED_PARTS` were checked against a name the OS never uses. | `ws.read("TOKEN~1.JSON")` returned the credential file's content; `ws.path("NODE_M~1/x.js", writable=True)` was accepted. | `workspace.py` rejects any part shaped `~N.ext` and re-runs the protected-path checks on the resolved components. `test_short_name_alias_cannot_reach_a_protected_file`. |
| S2 | **A proposal could write another tool's instructions.** The read-only guard covered only `agents.md`, `skill.md`, `policies`, `skills`, `.github`. | `.vscode/tasks.json`, `.mcp.json`, `QODER.md`, `CLAUDE.md`, `.qoder/repowiki/plan.yaml`, `src/main/java/Codex.md` all passed `path(writable=True)`. | `AGENT_RULE_FILES` / `AGENT_CONFIG_DIRS` now keep agent, editor and build-launch config readable but never writable. `test_agent_rule_files_stay_read_only`. |
| S3 | **Build output carried secrets into the session file and the next model request.** A command the project defines prints whatever it likes; that text is stored and re-sent. | `record_run` persisted `jdbc:postgresql://svc:Sup3rS3cretPass@db…` verbatim in `session.json`. | New `redaction.py` scrubs credential shapes (AWS, GitHub, Slack, Google, OpenAI/Anthropic, JWT, PEM keys, URL user:pass, `token=`/`password=` assignments) at capture in `repair.record_run`, at transmission in `repair.evidence`, and again at the `plan(extra_context=…)` boundary. Placeholders such as `${VAULT_PASSWORD}` and ordinary `Tests run: 8, Failures: 0` lines are left alone. `tests/test_redaction.py`, `test_stored_output_and_evidence_hide_secrets`, `test_build_evidence_is_scrubbed_before_it_is_kept_or_sent`. |
| S4 | **Apply → automatic re-run was not disclosed.** With Run & fix armed, approving a proposal silently executed the project's own build code again. | The Apply dialog said only "Write N file(s)". | The dialog now names the command and states that it executes the project's build and test code. `test_apply_dialog_discloses_the_command_that_runs_next`. |
| S5 | **A second task could stack on unverified files.** Phases were sequenced only because the human sent them one at a time; nothing noticed an applied-but-never-run task. | `start_plan` created a new session with the previous one in `APPLIED_UNVERIFIED`. | `unverified_prior_task` discloses the open task in the conversation; `PARTIAL_APPLY`/`APPLYING` now require an explicit yes before planning continues. `test_new_task_is_disclosed_while_an_earlier_one_is_unverified`, `test_a_second_task_after_an_interrupted_apply_needs_an_explicit_yes`. |

Checked and found already sound: argv-only execution with a scrubbed environment (`runner.py`), no shell anywhere in the command path, loopback-only Ollama endpoint with cloud-backing detection in `preflight()`, no redirects and no proxies in `providers.py`, bearer tokens and provider bodies kept out of error text, `proposal_hash` integrity on every session load, SHA256-guarded apply/rollback with preflight of all targets before the first write, plan files read-only, and control-character stripping in CLI output.

Residual risk that this round did **not** remove: `mvn`/`npm`/`pytest` execute whatever the approved project contains — the review gate is on text, not on program behaviour, and OS-level isolation remains the only strong boundary. A malicious *repository* can still mislead the model, and approvals are local user actions, not signed tokens.

## Previous release: UI 2.7 — the presentation layer moved to a local web page, engine untouched

The Tk window had become the limit on the design: no real typography control, no CSS, no
box model worth the name, and message bubbles measured by hand with font metrics. Restyling it
could not reach the result the user asked for, and adding an Electron/Qt/web-framework stack
would have broken the project's first rule — `dependencies = []`. So the UI moved to a local web
page served by the stdlib and shown in a Chromium app-mode window. **No new dependency was added.**
The files that changed are the presentation layer and nothing else: new `webapp/` package, new
`labels.py`, `gui.py` (which now imports the shared vocabulary instead of duplicating it),
`desktop.pyw`, `Run-Agent.bat`, `.gitignore`, two new test modules. `engine.py`, `workspace.py`,
`providers.py`, `runner.py`, `planbook.py`, `memory.py` and `symbols.py` are reached through the
controller exactly as `gui.py` reached them — the web layer adds no policy, no provider and no
execution path.

**`webapp/controller.py` — the headless half of `AgentWindow`** (1 228 lines)

- `AgentController` holds every decision the window used to make while holding a widget: project
  registry and catalogs, session and plan state, the ledger, `run_job()` on a worker thread,
  proposal/apply/rollback, run-and-fix rounds, cloud consent, and the state/`busy` machine.
- It never imports tkinter. Where the window used a modal dialog it emits an event and blocks on a
  `threading.Event`: `confirm()` and `ask_directory(title, hint, mustexist)` publish a
  `confirm`/`folder` event through a `Sink` callable and wait for `set_reply()`. That single seam
  is what makes the whole flow testable without a display and without a real model.
- `snapshot()` is the only read surface, and `action(type, payload)` the only write surface, so the
  browser cannot reach anything the window could not already do.
- `gui.py` keeps its own behaviour and now imports the same vocabulary instead of duplicating it.
  `labels.py` (72 lines) holds `STATES`, `MUTABLE_STATES`, `UNVERIFIED_STATES`,
  `INTERRUPTED_STATES`, `state_label()` and `friendly_error()` — extracted from `gui.py` precisely
  so the controller would not have to import a module that imports tkinter.

**`webapp/server.py` — HTTP and one-way push** (190 lines)

- `ThreadingHTTPServer` bound to `127.0.0.1` on a random port, daemon threads, `HTTP/1.1`. A
  `secrets.token_urlsafe(16)` generated per launch is compared with `secrets.compare_digest` on
  every `/api/*` request — as `?t=` on GET and as `X-Auth-Token` or `?t=` on POST — and a request
  without it is a 403 before any controller code runs.
- `Hub` fans controller events out to open SSE streams and drops an event for a client whose queue
  is full rather than letting a stalled browser block the worker thread. `/api/events` keep-alives
  every 15 s so an idle proxy cannot half-close it.
- Only `/api/bootstrap`, `/api/options`, `/api/fs` and `/api/events` are readable, and only
  `/api/action` and `/api/confirm` are writable; anything else is a 404, and an exception in an
  action becomes a 500 text line rather than a dead server.
- Static assets are served without the token, deliberately: a browser sends no query string for a
  `<link>` or `<script>` tag, and the bundled files carry no user data. They are still confined by
  `target.is_file() and target.is_relative_to(STATIC)`, which is tested against `..%2f` and
  `%2e%2e/` traversals. `/api/*` is where the token is non-negotiable.
- `MAX_BODY = 1_000_000` bounds a request before it is parsed, and a malformed body answers 400
  instead of a traceback.

**`webapp/static/` — the design system**

- `tokens.css` (172 lines) is colour only: three style families (`claude` warm paper, `codex`
  charcoal, `linear` indigo) × light and dark, selected by `<html data-style data-theme>`.
  `app.css` (466 lines) is components: it reads tokens, and holds exactly three literal colours on
  purpose — white text on a filled accent chip and one translucent white key-cap overlay. That is
  what makes the family swatches in the sidebar real choices rather than a demo. The family is
  still unchosen — deleting the two losers is a small, contained edit.
- `app.js` (701 lines) escapes every string before it renders, then highlights in a **single**
  regex pass. Three sequential `.replace()` calls re-matched the markup the previous pass had
  just inserted and printed `class="tok-c"` as visible text.
- `webapp/fake.py` is a scripted controller with realistic data, so the design is reviewable with
  no engine and no model — this is how the mockups were screenshotted.

**`webapp/launch.py` + `desktop.pyw` + `Run-Agent.bat`**

- `find_browser()` looks for Chrome or Edge and opens `--app=<url>` against a private profile under
  `.agent-webview/`, so the app window cannot be swallowed by an existing browser process.
- Window bounds are forced **only while the profile has no `Preferences` file**. Once it exists the
  browser restores wherever the user moved the window, and re-passing `--window-size` would throw
  that away every launch.
- `desktop.pyw` tries the web window, falls back to `run_tk()` on any failure, and only then shows
  the startup dialog — still without exception details, because GUI fields can include API
  credentials. `Run-Agent.bat`'s probes now check for Python 3.11 only; Tkinter is a fallback, not
  a hard requirement.

**Correctness fixes found by driving the real page**

1. A server-pushed draft (`Try sample project`, an auto-advanced plan step) never reached the
   composer, because the client treated every draft in a snapshot as an echo of its own keystroke
   and skipped it. `applyServerDraft()` compares against what it actually sent instead.
2. Diff rows were numbered by array position, so the `@@ -1,2 +1,2 @@` header consumed a line
   number and every row after it was off. `diffRows()` now parses the hunk header and keeps separate
   old/new counters.
3. Project avatars were first-two-characters, which rendered `demo2` and `demo_repo` identically;
   `initials_for()` splits on `[ . _ - ]` and prefers a trailing digit.
4. The header subtitle went stale on a model switch because only `_sync_project()` rebuilt it.
5. A repeated no-progress stop told the user "try another model" when the real cause is almost
   always that the change already exists in the files — the task the app was shown had already been
   applied and never rolled back. `friendly_error()` now says that, and tells the user to open the
   file or name one concrete edit.
6. "New project folder…" could not create a folder: the modal only offered *choose an existing
   path*. It now takes a name and creates it when `mustexist` is false.
7. The `.pill.tag` file-badge collision clipped model names to `Olla`.

**Accessibility**

- A script computes WCAG contrast from `tokens.css` itself rather than by eye: 11 token
  pairs × 6 style/theme combinations = 66 measurements. Four failed, all in light themes —
  `--faint` on `--bg` in all three families (2.98, 3.19, 3.14) and `--accent` on `--bg` in `linear`
  (3.68) — and all are now ≥4.5:1. The audit reports 0 failing pairs.
- The tab strip is a real `role="tablist"` with `aria-selected`/`aria-controls` and labelled
  `role="tabpanel"` views; toasts are `role="status" aria-live="polite"`.
- `modal()` traps Tab, honours Escape, focuses its first control, restores focus to the opener, and
  sets `role="dialog" aria-modal`. The global Escape handler that deleted the scrim from under an
  open dialog is gone.
- `prefers-reduced-motion` disables the animations, including the streaming caret.

**Verification:** `python -m unittest discover -s tests` → **192 tests pass offline** in ~50 s with
synthetic model responses, up from 155. `tests/test_controller.py` (22 cases) drives the real
orchestration with no display at all: plan → apply → passing run, declining apply writes nothing,
rollback, the cloud-consent gate, commands locked until applied, the unverified-prior disclosure,
`PARTIAL_APPLY` needing a yes, notes reaching the model, the switch-project draft warning, plain
chat never proposing, a plan step closing only on report proof, preferences surviving a restart, and
**the API key never appearing on disk**. `tests/test_webapp.py` (15 cases) runs a real listener on a
loopback port: `/api/*` refused without and with a wrong token, POST refused without one, path
traversal 404s, assets served tokenless, oversized and non-JSON bodies answered 400, and a
`confirm` event verifiably blocking the worker thread until `/api/confirm` wakes it. The page was
also driven live in the app-mode window and screenshotted for each fix above.

## Previous release: UI 2.6 — report-backed proof, a gated plan ledger, project notes, and a real symbol index

Four capabilities from `docs/QODER-COMPARISON.md`, chosen by the user as the ones that make the tool strongest.

**Proof, not exit codes** (`runner.py`, `verification.py`)

- Each recipe now names the report files that prove its test run: Maven `target/surefire-reports/TEST-*.xml`, Gradle `build/test-results/**/TEST-*.xml`, and the `--junitxml` file pytest is handed. `fresh_reports()` keeps only files whose mtime is at or after the start of this run, so a previous build cannot certify the current one.
- `report_counts()` aggregates `tests/failures/errors/skipped` across those files, and `decide_status()` ranks the report above the exit code: failures in the XML mean `failed` even when the build swallowed them, and a green command whose reports show zero executed tests is `unverified`, never a pass. Recipes with no report file fall back to their console summary (`Ran N tests`, `Tests run: N`, `pass N`).
- The counts and the report source are stored on the run record and shown in the Checks view.

**The plan ledger** (`planbook.py`, `gui.py`, `engine.py`)

- `planbook.parse_steps()` turns a plan's numbered headings (`## Phase 1 — title`, `Step 2:`, plain `1.`) into an ordered list, capped at `MAX_STEPS = 12` so a 200-item checklist cannot become 200 auto-runs.
- The ledger is a JSON file keyed by `<repo-name>-<plan sha256[:16]>` under `<app data>/.agent-plans/`. It deliberately lives **outside** the approved project folder: a proposal can never rewrite the gate that controls it. Editing the plan changes the key, so a new ledger starts empty instead of inheriting verdicts.
- `engine.plan()` accepts `plan_step`, recorded on the session but kept **outside** `proposal_hash`, because the number proves sequencing rather than content — the integrity check over the files is unchanged.
- With **Step-by-step plan mode** on, Send stops being a free-text handoff: the task is generated from the current open step, lists what that step asks for, names the already-verified titles under *do not redo*, and keeps whatever the user typed as an optional note. The step is bound to the session, and a fix round re-binds to the same step so it closes on whichever session finally passes.
- A step becomes `verified` only when a bound session reaches `CHECKS_PASSED` **and** the run carries report proof (`planbook.proof_reason()` returns the missing reason otherwise: no bound session, state not passed, no run, or *no test was observed running*). A compile-only recipe is its own proof, so `*compile` recipes are exempt.
- Verifying a step calls `advance_plan()`, which starts the *planning* run for the next step — auto-advance never writes files; the proposal still needs the same explicit Apply click.
- `planbook.reopen()` un-verifies a step when its applied files are rolled back, and the conversation says so.
- Progress is visible without opening Settings: the artifact panel's Sources chip reads `plan.md — step 1/3` / `all steps verified`, Settings shows `Plan step 1/3: project foundation (0 verified) — Send works on it.`, and the review summary names the step and its gate.

**Project notes (memory)** (`memory.py`, `gui.py`, `engine.py`)

- A 1.5B model re-derives package names, Java level and naming rules from scratch on every task, so the user re-types the same constraints in each message. `memory.py` keeps one note file per project folder and `plan()` delivers it on every turn in that folder.
- The notes live at `<app data>/.agent-memory/<folder>-<sha256(resolved root)[:16]>.md`, keyed on the resolved, casefolded path so the same folder typed differently cannot fork into two notes. `Workspace` blocks `.agent-memory` (and `.agent-plans`) by name, and a project nested inside the store still cannot reach it with `../`.
- In the prompt the block is labelled as *the user's own standing instructions, not model output and not observed code*, with an instruction to say so in the summary when a note blocks it — so notes cannot be confused with facts read from the repository, and a note that contradicts the task is visible in the review.
- Notes are recorded on the session (`memory`, `memory_sha256`) for audit but stay **outside** `proposal_hash`, which covers what the proposal changes: editing a note never invalidates a proposal already reviewed.
- Writes are staged through a temp file in the same folder and `os.replace`d, so a crash cannot leave half a note the next task would still obey; emptying the box deletes the file and leaves no `.tmp` behind. Over `MAX_MEMORY = 4000` characters is refused before anything is written.
- Settings → **Project notes (memory)** holds the editor, **Save notes**, and a status line (`107 characters saved · repo-3f2a…md · sent with every task here`, or `unsaved` while an edit is pending). Changing the project box reloads the editor; `project_notes()` feeds both Send and Run & fix.

**Symbol index** (`symbols.py`, `workspace.py`)

- `Workspace.repo_map()` no longer runs one regex over every line. It builds `symbols.parse()` rows for each policy-visible file and renders them with `symbols.render()`, so the map says what a file *declares* rather than what resembles a declaration.
- Python goes through `ast`: classes with their bases and nesting (`Service`, `Service.Inner`), methods with parameter names, module-level and async functions, and imports including relative ones. A file too broken to parse falls back to the old lexical scan instead of vanishing from the map — that is precisely when the model needs to see it.
- Java and Kotlin go through a brace-depth scan of `blank()`ed text, which replaces comments and string contents with spaces while keeping every line where it was. A method is recorded only at one level inside a type body, which is what separates `login(String user, String password)` from `tokenService.login(password)` two lines later, and keeps `// public void commentedOut() {` and `"class InAString {"` out of the index. Package-private JUnit methods are matched too; `record`, `enum`, `interface`, `object` and Kotlin `fun` are named with their kinds.
- `dependencies()` keeps only edges that point inside the repository: an import resolves to a file by matching dotted component tails, so `com.acme.db.UserStore` reaches `src/main/java/com/acme/db/UserStore.java` despite the source root prefix, while `java.util.List` produces nothing a model could act on.
- Everything is capped — `MAX_FILES` 300, `MAX_TYPES` 40 per file, `MAX_MEMBERS` 25 per type, `MAX_IMPORTS` 40, 60 characters per signature, 12 000 characters for the whole map — and a truncated map says how many files it dropped rather than ending quietly.
- The prompt label now names what the block is ("file names, parsed declarations and internal imports") and still tells the model to verify it, because an index is a convenience, not authority.

**Verification:** 155 tests pass offline with synthetic model responses (`python -m unittest discover -s tests`), including 15 new `tests/test_planbook.py` cases, 2 engine cases for `plan_step` integrity, 10 `tests/test_memory.py` cases for the store and its policy, 3 engine cases proving notes reach the model, stay off the hash, and are refused when oversized, 18 `tests/test_symbols.py` cases — among them that a comment, a string literal and a call inside a method body cannot become declarations, that relative Python imports resolve to project files while `java.util.List` does not, and that the map keeps its budget — and 7 GUI-thread cases covering the send/verify/advance/rollback loop plus the notes editor. The window was driven through a real three-step plan with a scripted model and screenshotted: the ledger rows, the `step 1/3` chip, the Settings checkbox and progress line, and the notes editor with its saved-state line were confirmed on screen. The index was checked against `sandbox/spring-auth`, where it names all eight methods across the three test classes and the four internal imports of `AuthController`. One live run against that project with `qwen2.5-coder:1.5b` (draft only, loopback, nothing applied) confirmed the notes reach the session and the map reaches the model, and ended `BLOCKED` when the model overflowed its 4,096-token output on a whole new Java class — the same capability ceiling recorded in `MODEL-BENCHMARK.md`, not an indexing failure.

## Previous release: UI 2.5 — run the project's own command and repair from its output

- `runner.py` executes a fixed allowlist of project commands (Maven test/compile, Gradle, pytest, unittest, node test, npm test) inside the approved folder only: argv list with no shell, scrubbed environment limited to a named variable allowlist so no API key reaches a child, output capped at 200 KB, `CREATE_NEW_PROCESS_GROUP` with a `taskkill /F /T` tree kill on timeout. A command that exits 0 without any observed test is reported `unverified`, never as a pass.
- `repair.py` binds a run to the existing session state machine (`passed`→`CHECKS_PASSED`, `failed`→`VERIFICATION_FAILED`, timeout/unverified/unavailable→`VERIFICATION_BLOCKED`), stores a slim run record, and builds the bounded evidence text for the next turn. Recording requires an applied session and fails on a tampered one.
- The Checks card in the artifact panel detects the project's command, enables **▶ Run** and **Run & fix** only after a change has been applied, and re-arms a run after each approved fix proposal, up to three rounds. Nothing is written without the same explicit Apply click as before; the repair turn only produces a proposal.
- `plan()` accepts `extra_context`, delivered as a clearly labelled untrusted runtime observation and stored on the session for audit; raw command logs are never persisted in the session file.
- **New project folder…** creates and grants an empty folder (`ensure_project_dir` refuses drive roots, existing files and populated folders).
- Request timeout is editable in Settings (30–900 s, default 300) and is used for both planning and provider construction, because a 4,096-token reply from a CPU-only local model can exceed ten minutes. A timeout error now names both fixes.
- Default model is `qwen2.5-coder:1.5b`, preselected on first run and annotated ★ in the model list. `docs/MODEL-BENCHMARK.md` records the measured ranking of every installed model and why speed beat parameter count on this machine.

### Small-model tolerance work in the engine

Measured against the live Spring Boot scenario, where the run died on turn 2 of nearly every phase:

- `parse_action` recovers the outermost brace-balanced object, so prose or a markdown fence around a valid action no longer wastes a turn.
- A proposal rejected for editing a file the model never read is now recovered by the runtime reading that file itself (under the same policy, with its hash recorded as observed) and returning its real content plus a `next_action` instruction. Event: `auto_read`.
- `action="blocked"` is no longer believed immediately after an observation the runtime knows is recoverable — a rejected action, a missing file it may create, an empty search, or an empty project. The hint is bounced back as a note and the model gets up to two more turns; a blocked reason that is genuinely the model's own judgment still ends the run at once. Event: `blocked_retried`.
- `SECRET_NAME` is now boundary-anchored, so `JwtTokenProvider.java` and `PasswordHasher.java` stay visible to the model while `.env*`, `token.json`, `credentials.yml` and `token-service.ts` remain blocked.
- `shrink_warning()` flags a replacement that removes 60% or more of an existing file's non-blank lines, naming the lost lines. It appears in the Changes message and again in the Apply confirmation dialog. Advisory only — the user can still approve — because whole-file rewriting is how the protocol works and this class of damage (a manifest losing its dependencies) was observed live.

- Surefire prints `Tests run: 8, Failures: 0, Errors: 0` on every green run. Those lines match the failure vocabulary, so `_failures` now skips zero-count summaries — otherwise a passing build sends the model hunting for a problem that is not there.

### Live Spring Boot run (the reason for the work above)

`D:\AI\AI-Agent\sandbox\spring-auth`, driven turn by turn through the real engine and provider against `qwen2.5-coder:1.5b`:

- The propose → apply → run → repair loop works end to end: `mvn -B -DskipTests compile` caught every bad proposal, the failure was fed back as evidence, and the turn guard stopped a loop that could not converge.
- `pom.xml` was rewritten by the model with the Spring parent and starters deleted and only jjwt left. The build failed in 4 s, and **rollback restored the file and the project reached `compile: passed` again**. The same recovery was used twice more on truncated Java sources.
- Both engine recoveries fire against a real model, not just tests: `auto_read` on `pom.xml`/`LoginRequest.java`, and `blocked_retried` on missing-file and rejected-action observations.
- The model could not author new Java classes (see `MODEL-BENCHMARK.md`), so the ten remaining sources were written by hand and verified through the tool: `mvn -B test` → **8 tests run, 0 failures**, and the packaged service answered 200 + HS256 token for valid credentials, 401 for a wrong password, 401 for an unknown user and 401 for an unauthenticated protected route.

**Verification:** 83 tests pass offline with synthetic model responses (`python -m unittest discover -s tests`), including 11 runner, 8 repair and 26 GUI-thread cases. The window was opened and screenshotted to confirm the Checks card, the ★ recommendation line, the request-timeout spinbox and the sidebar's new-folder button.

## Previous release: UI 2.4 — optional project, chat-first layout

- Sidebar is a project/chat tree keyed by canonical project root. Each chat has a separate UUID; old sessions appear as individual chats without rewriting their files.
- New chat resets history, attachment, draft, review state, activity, and cloud consent. Selecting another project also clears this state; opening a saved chat restores its own project/plan/history.
- Follow-up generation includes at most six historical task/status/summary entries from the exact project AND chat, bounded to min(4,000 characters, one sixth of the context budget). Other chats are not included. Current files must still be read before replacement.
- Session metadata records chat ID and source context session IDs. Chat ID is covered by proposal integrity hashes for new sessions; old hashes remain compatible.
- Project registry `.agent-projects.json` is persisted and blocked from model file tools. Storage remains the local `.agent-runs` store; this is logical context isolation, not an OS user/security sandbox.
- Targeted fake-provider payload and real Tk checks passed for cross-project/cross-chat exclusion, legacy grouping, bounded history, project switching, new chats, and registry protection. All 28 core tests passed. No live generation or Spring build/test was performed.
- UI 2.2 added selectable read-only message text and Copy/Select all menus, plus Cut/Copy/Paste/Select all in the composer. Those features remain in UI 2.3.

## Previous desktop release: UI 2.1

The current release replaces the heavy form layout with a flat sidebar, rounded message bubbles and composer, compact Chat/Changes/Activity navigation, a Sources/Outputs card, and a separate project/model settings window. Sources collapse on narrow windows. The title displays UI 2.1 so stale open windows are easy to identify. Restart Run-Agent.bat to load updated code.

The user subsequently authorized UI execution and verification. Syntax compilation and real Tk interaction/layout checks passed at 1500×880, 1000×650, and 1850×950. Checks covered composer visibility, settings, model selection with local fixture data, plan reading, busy/stop controls, review navigation, and a new task. A live preview was launched; visual screenshot inspection remains incomplete because Windows capture returned an approval timeout and then `no screenshot targets found`. No model generation, proposal application, Spring build, or project tests were performed for this release. Earlier “not tested” sections below describe historical updates, not these newer targeted checks.

## Attached plan files (not tested at the user's request)

The desktop now has an optional Plan file field with Attach/View/Clear controls. Markdown or text plans inside the selected project are read into the model context separately from the 4,000-character task description. The current task selects the phase and takes precedence over stale phase instructions in the plan. Plan references are recorded with a hash, protected from modification by the proposal, and rechecked before applying it. CLI equivalent: `--plan-file plan.md`. No application/model/test execution was performed for this update.

## New-project planning fix (not tested at the user's request)

Missing files are now distinguished from oversized or non-regular files. The planning read tool returns `not_found` with a policy-derived `can_create` hint instead of counting a missing new file as an invalid action. Instructions explicitly cover scaffolding new projects, and a read-only `list_files` action is available. Creation still occurs only after proposal approval, with existing path and concurrent-edit protections unchanged. No tests or inference calls were run for this fix.

## Latest UI/model update (not tested at the user's request)

- All desktop controls, dialogs, messages and alignment are now English.
- Ollama model discovery runs at startup and via Refresh models. Every model returned by the installed Ollama service is listed; the user selects the model, with no default forced by the GUI.
- OpenRouter Free and Paid selectors load a live catalog, with name filtering, context size and published token prices. No generation is performed while refreshing the catalog.
- Paid inference requires explicitly selecting the Paid provider mode. The CLI remains free-only by default. No automatic switch to paid models exists.
- Cloud-backed Ollama entries are included and require cloud-data approval before generation; normal local models do not.
- No tests, application launches, or inference requests were run for this update, per the user's instruction. Earlier validation results apply to the earlier version.

## Implemented

- Python 3.11+ CLI, no third-party runtime dependencies.
- Ollama loopback endpoint and model preflight; explicit cloud opt-in, with Free/Paid selection in the desktop UI.
- Strict action dispatcher, bounded turns and invalid-action retries; no model shell access.
- File search and a repository map built from parsed declarations (Python `ast`, bounded Java/Kotlin scan) with protected-path filtering.
- Proposal persistence, concrete diff, approval hash, preflight before applying, guarded rollback.
- Conservative file types/size limits; UTF-8 only, maximum 8 changed files per proposal.
- Static verification plus Docker-only fixed build/test recipes; absent isolation fails closed.
- Unit, security-boundary and provider-contract tests; deterministic offline demo.
- Desktop UI: project picker, task composer, local/cloud model selection, progress, saved tasks, colored diff, click-to-approve, apply, static verification and guarded rollback. Rendered as a local web page (UI 2.7) with the Tk window kept as a fallback.
- Planning cancellation at a safe boundary after the current request. The UI stays responsive while work runs in a background thread.
- Run-Agent.bat opens the desktop application; Run-Agent-CLI.bat preserves the earlier text menu.

## Important differences from the full architecture

This is the first vertical slice, not the full production roadmap.

- The repair loop is bounded to three rounds and stops for approval on every write; it does not decide on its own to keep going past that.
- No automatic model fallback; explicit model choice is safer until capability/privacy evaluation is available.
- No native tool calling, streaming, vector database, LSP, type checking, incremental index or context summarizer. Python is parsed with `ast`; Java and Kotlin use a bounded scanner, not a grammar.
- No skills router, executable hooks, MCP, SSO or multi-agent execution yet. The desktop UI is a loopback web page with a per-launch token, not a hosted or shared web app: there is no account, no remote server and no way to reach one machine's session from another.
- Sessions record interrupted apply; mutations do not auto-replay. Full durable distributed recovery and workspace locks are not implemented.
- No worktree creation: proposal/apply target the explicitly selected workspace, guarded by hashes. Use a developer-created isolated checkout for real work.
- Docker recipes need preloaded approved images and offline dependencies. The implementation must be exercised on a Docker-capable host before claiming worker isolation is validated.
- Report-backed proof covers Maven, Gradle and pytest JUnit XML. Other recipes fall back to their console summary line, and a recipe that produces neither is `unverified`.
- Per-project user notes are injected into every planning turn from outside the workspace, labelled as the user's instructions, and excluded from the proposal hash.
- Local session files contain code snapshots. Enterprise audit signing, encryption and retention remain future work.

## Conversation-style desktop layout

- Cool gray workspace: sidebar (new chat, open saved task, search, project chip, tasks/chats tree), central conversation, bottom composer, and a right artifact/source panel that shows the current proposal state and file count.
- Chat and Activity are the two views of the center area, switched from the header; the active view is
  highlighted and tab strips are not used. The proposed files are not a third view: since #28 they open in
  their own window, withdrawn until a proposal exists, with Apply/Check/Roll back in it.
- The project is optional. With none selected the composer sends a plain question answered in prose (`.agent-chats/<id>/chat.json`, no workspace, no tools, no proposal, JSON mode off). With a project selected the same composer requests reviewed changes; the propose/apply/hash-verified path is unchanged.
- Saved tasks and chats restore their own project, attachment and history. Opening a project-free chat detaches any project so a chat cannot silently edit the last folder used.
- Enter (or Ctrl+Enter) sends; Shift+Enter adds a line. Progress and errors remain visible in Activity and the status area.
- In the web window the sidebar, composer and right panel are CSS layout, so cards size themselves and the hand-measured bubble geometry is gone; it remains only in the `--tk` fallback. View switching is a real `role="tablist"`, and state reaches the page over SSE rather than by polling.
- This interface still does not stream model output; each reply arrives when the provider request completes.
- UI 2.4 was verified by running the window locally and inspecting screenshots; 46 offline tests cover the chat mode, sidebar search, grouping, reopening and chat continuation after restart. No live generation was performed.

## Next implementation order

1. Validate the Docker worker on the target machine.
2. Add workspace locking/worktrees and restart reconciliation.
3. Add skills and approved MCP integrations, then team deployment controls.

OpenRouter live testing requires a user-provided environment key. A local model is sufficient to use the planning and review workflow now.
