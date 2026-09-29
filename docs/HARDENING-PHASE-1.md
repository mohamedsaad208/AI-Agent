# Hardening, phase 1 — measured before changed

Seven items, each read in the code first. One of them is marked done and is not, one is described as
broken and is not, and the rest are real. Baseline: **881 offline tests green** (UI 4.2 Phase 1 had
just landed), `node --check` clean.

| item | verdict | evidence |
| --- | --- | --- |
| GitHub Actions CI | **NOT PRESENT**, despite the ✓ in the request | `ls .github` → *No such file or directory*; no `*.yml`, no GitLab/Azure/Jenkins config anywhere. There is a remote (`github.com/…`), so a workflow would actually run |
| Cargo detection | **BROKEN, but not where it looks** | `runner.py:111` `"markers": ["cargo.toml"]` *does* match a real `Cargo.toml`, because `detect()` lowercases the filenames at `:143-144`. The two real defects: `timeout_for()` `:126-128` gives cargo the 600 s `DEFAULT_TIMEOUT` while the JVM recipes get 1 500 s — a cold Rust build is a *compile*, and 600 s kills it; and cargo writes no JUnit XML, so `proof` is `None` and `summarize()` `:487-493` reports `Cargo test: passed (exit 0)` while throwing away the `20 passed` it already parsed at `:457` |
| A real lock around controller state | **MISSING** | `grep -rn "Lock()" src/ai_code_engineer/webapp/` → only `server.py:28` (the SSE fan-out) and `git_integration.py:70`. `ThreadingHTTPServer` runs one thread per request, `run_job` adds more, and `self.messages`/`self.log`/`self.session`/`self.busy` have no guard. The comment at `controller.py:474-479` describes the two-clicks-in-one-tick race and mitigates it with `on_busy` — a polite refusal, not mutual exclusion |
| `snapshot()` immutable / deep-copied | **NOT** | `controller.py:844` returns `"messages": self.messages, "log": self.log` — the live objects. `server.py:159,165` then serializes them on the request thread while a worker thread appends. That is both a torn-read and a `RuntimeError: list/dictionary changed size during iteration` waiting to happen |
| Host / Origin / CSP on the server | **NOTHING** | `server.py` checks only the token (`_authorized`, `:64-70`). No `Host` validation (so DNS rebinding is open), no `Origin` check on the state-changing POSTs, no `Content-Security-Policy`, no `X-Frame-Options`, no `Referrer-Policy` — and the launch URL carries the token in its query string |
| Raw exception detail in HTTP responses | **YES, in three places** | `server.py:163` `self._text(f"{type(exc).__name__}: {exc}", 500)`; `:171` and `:177` return `str(exc)` for `/api/fs` and `/api/project`, where an `OSError` carries a Windows path. The provider layer already redacts carefully (`providers.py:38-55`) — the HTTP boundary throws all of that away |
| A clear warning that the run executes project code | **PARTIAL** | `runner.py:1-6` says it in the module docstring and `labels.py:475` says it in a comment. `ENV_KEYS` (`:40-42`) and the allowlist are real blast-radius limits. **No user-facing sentence anywhere**: not in the drawer, not in Tk, not in the step row |

## Two things the measurement found that nobody asked about

1. **The git remote URL contains a live GitHub personal access token**, in plaintext, in `.git/config`.
   It is not in any file in the working tree, so it is not in the repo — but it is on disk, it is
   printed by `git remote -v`, and it would appear in any screenshot or bug report. It should be
   revoked and the remote switched to a credential helper. **Not touched here**: changing git config
   is the user's call, and pushing anything is a separate decision.
2. `index.html:10-25` is an inline `<script>`. A CSP strict enough to matter (`script-src 'self'`)
   would break the theme-before-first-paint it implements, so it moves to `static/boot.js` — a
   render-blocking classic script in `<head>` runs before paint exactly as the inline one did.

## What gets built

**1. The HTTP boundary (`webapp/server.py`).**
- `Host` must name the loopback interface this server is bound to, with the right port: DNS
  rebinding is the attack that makes a token-protected local server reachable from a web page.
- `Origin`/`Referer`, when the browser sends one, must match the same loopback host and port. A
  form or fetch from any other page is refused before the body is read.
- `Content-Security-Policy` with `default-src 'none'` and only what the UI actually uses;
  `script-src 'self'` (no `'unsafe-inline'`, which is why `boot.js` moves out); `frame-ancestors
  'none'`; `base-uri 'none'`; `form-action 'none'`. Plus `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer` because the launch URL holds
  the token and a referred page would be told it.
- One place that puts those headers on every response, so a new route cannot forget them.
- `style-src 'self' 'unsafe-inline'` stays, deliberately: eight template strings in `app.js` set
  layout styles inline (progress-bar widths, avatar colours), and inline styles cannot execute
  script. Documented rather than silently loosened.

**2. No raw exception detail leaves the process.**
- A 500 answers one fixed sentence plus a short id; the real `type`, message and traceback go to the
  controller's Activity log, redacted and capped, which is where a developer looks anyway.
- The two 400s route through `labels.friendly_error` (which now redacts at its own boundary) instead
  of `str(exc)`.
- The provider layer's discipline — never echo the request, the bearer token or the URL — becomes a
  test at the HTTP layer, not just a comment.

**3. A real lock (`webapp/controller.py`).**
- One `threading.RLock` held for the critical sections: the `busy` claim and release, the message and
  log mutators (`_add`, `_step`, `_note`), `_emit`, `action` dispatch, `set_reply`, and the whole of
  `snapshot()`.
- **Never held across a wait**: `confirm_choice` blocks on an event for a user answer, and a worker
  that holds the state lock there would freeze the HTTP thread that is trying to deliver the answer.
  That is the one way to turn this fix into a deadlock, so it gets a test.

**4. `snapshot()` returns a copy nobody else can change.** `copy.deepcopy` of the assembled snapshot
inside the lock. Measured cost on the worst realistic state (a 300-entry catalog, 200 messages, 400
log rows) before deciding — a state push per action is a handful per task, not a per-token path.

**5. Cargo.** `timeout_for()` puts cargo in the compile league; a console-declared proof gives cargo
(and go, which has the same hole) a real count instead of `exit 0`; the marker table stays as it is
because it was never the bug.

**6. The warning, in the user's face rather than in a docstring.** One sentence from `labels.py`, in
both languages, drawn in the drawer next to the run buttons and in Tk's Settings page, and repeated
in the run's own step row: the command is the project's own build tool, run with this user's
permissions, in this folder. The allowlist and the stripped environment limit what it can touch; they
do not sandbox it.

**7. CI.** `.github/workflows/ci.yml`: Python 3.11 and 3.13 on `windows-latest` and `ubuntu-latest`
(the tool is Windows-first and its own tests use Tk, so Linux runs headless under `xvfb` with
`python3-tk`), running the same command the repo runs by hand —
`python -m unittest discover -s tests` — plus `node --check` on `app.js`.

## What does not change

- The token stays the authentication. Host and Origin checks are defence in depth against a page
  that cannot read the token, not a replacement for it.
- `Hub`'s lock and the SSE fan-out are already right; they are not touched.
- No behaviour change to the agent loop, the review-before-write rule, or the redaction patterns.
- `run-tests.cmd` stays the local command; CI calls the same unittest module, not a new runner.

## Tests

New: a server-security class (Host, Origin, CSP on every route including 404 and the SSE stream, the
500 body carrying no exception text, no traceback on stderr), a controller concurrency class (two
threads claiming a job at once; a snapshot taken while another thread appends; a reply delivered while
a worker waits), cargo timeout and console-proof tests, and a labels test that the run warning exists
in both languages. Expect **881 → ~915**, all green, with no existing assertion weakened.

## As built — 2026-09-29

**881 → 903 offline tests, green on both `PYTHONPATH=src` and the bare CI command, and
`node --check` clean on `app.js` and `boot.js`.** The 915 was a guess at granularity, not a
requirement; nothing that was passing changed its mind. Verified live in a real browser against the
scripted window (a document load, a same-origin `POST /api/action`, an `EventSource` receiving
events, CSP on an asset response, a wrong token refused).

Seven items shipped. Four places where the build disagreed with the plan above:

1. **The `Origin` check had to be written twice.** The first version fed `Origin` through the same
   `_host_allowed()` used for `Host`, and a test that asserted `https://localhost` is refused came
   back 200 — because `Host: localhost` legitimately arrives without a port, so that helper allows a
   missing port. An `Origin` never arrives portless and always carries a scheme, so the two questions
   are not the same question. `_origin_allowed()` requires `scheme == http`, a loopback name **and
   this exact port**; `null` (a `file://` page or a sandboxed frame) is refused with it. `Host` keeps
   allowing the portless form.
2. **CI is pinned to 3.11 only, not 3.11 + 3.13.** The plan asked for both; `run-tests.cmd` names 3.11
   *because* the suite counts subtests differently on 3.13 — so a second version in CI would report a
   different total for the same code and turn the count, which is this project's acceptance measure,
   into noise. Documented in the workflow's own comment.
3. **Only cargo declares a console proof.** The plan said "cargo (and go, which has the same hole)".
   Go does not: `go test` prints no total by design, and the recipe already counts one `ok <package>`
   line per package through `test_ran`. Giving it a second, contradictory counting rule would make
   its proof depend on which of the two matched first. The function is generic — any recipe may
   declare `console_proof` — and cargo is the one that needed it.
4. **A guard nobody asked for, because the measurement made it cheap:** `serve()` refuses a non-loopback
   `host` instead of binding one, so the whole Host/Origin design cannot be switched off by the caller.

Two things the plan listed and the build did **not** change, on purpose: the token remains the
authentication (Host/Origin are defence in depth — a refusal still requires the token to say why), and
`Hub`'s existing lock is untouched.

The Activity-log half of item 2 is a new controller method, `note_failure(reference, detail)`: the
browser gets `…(id 3f9a1c)` and the reason goes to the log, redacted and capped at 600 characters,
because a provider's 401 body is exactly the text that can echo a bearer token back.
`test_the_api_key_is_never_written_to_disk` still passes unmodified.

The concurrency tests found one thing worth naming: an unthrottled writer thread appends faster than
a frozen snapshot can be copied, so the first version of `test_a_snapshot_never_shows_a_message_half_written`
spent minutes in lock contention and timed out. It is paced now. That is the real cost of the deepcopy
measured from the wrong side — and the reason item 4 was scoped to a state push per action rather than
a per-token path.

