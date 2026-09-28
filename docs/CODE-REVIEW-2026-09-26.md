# Code review — 2026-09-26 (UI 3.0 tree, 402 offline tests)

> **Status: the whole punch list was worked the same day.** Seventeen of its eighteen items are
> fixed and each carries a note in the table at the bottom; the one left open is item 16 (the shared
> Host seam), deliberately — it is the only recommendation here that rewrites both presentation
> layers at once, and it is not a defect. What was done, what was measured first, and what turned
> out to be wrong is written up as *The same day, after reading the whole project* in
> `docs/IMPLEMENTATION-STATUS.md`. The suite stands at **525 tests**, offline and green; the round's
> own measurements are in `docs/VALIDATION.md`. **The `file.py:line` cites in this document are from
> the tree as it stood at 402 tests and many have drifted**; the named functions are still the right
> things to look for, and the punch list is the part worth reading today.
>
> Two findings here were themselves wrong, and are corrected rather than deleted: the
> `@media (max-width: 860px)` block is **not** dead scaffolding (see *P2 — dead code*), and the
> ledger-key item was reported fixed by a delegated edit that had not actually landed — `key_for()`
> still hashed a basename when this round reached it.


Full review of `src/ai_code_engineer/`, `tests/`, `tools/` and the docs, looking for code that
needs refactoring and for defects. Nothing in this file was changed in the source — this is the
comment record, not a patch.

## Method, and how much to trust each line

Six area reviewers read disjoint parts of the tree (engine, engine + policy support modules,
`webapp/controller.py`, the web front end + HTTP surface, the Tk window + runner/symbols, tests +
docs). Every finding that this document calls a **bug** was then re-checked by hand in this
session, and three of them were reproduced by running code. Each item carries its own status:

| Status | Meaning |
| --- | --- |
| **reproduced** | run against the real controller in `sandbox/repro-bind-mode.py`, output quoted |
| **read-verified** | the cited lines were opened and say what the finding claims |
| **reported** | a reviewer's conclusion not independently confirmed — treat as a lead, not a fact |
| **rejected** | investigated and found wrong; kept here so nobody re-reports it |

Two reviewer claims were **rejected** this way (`engine.review()` KeyError, `planbook.complete()`
re-completing a step) — see [Rejected](#rejected-findings).

## Measurements

Line counts, longest function, and how much of each file is a comment or a docstring. The
comment column matters because the project's rule is "explain the *why*, at the one place a
reader would otherwise get it wrong" — so a low number is only bad where the code is subtle.

| file | lines | defs | longest fn | docstring+comment % |
| --- | --- | --- | --- | --- |
| webapp/controller.py | 1914 | 118 | 129 (`__init__:110`) | 14% |
| gui.py | 1901 | 111 | 165 (`_task_page:596`) | 5% |
| engine.py | 709 | 21 | **221 (`plan:383`)** | 11% |
| symbols.py | 604 | 27 | 56 | 21% |
| runner.py | 447 | 19 | 75 (`_collect:308`) | 18% |
| webapp/fake.py | 392 | 18 | 52 | 4% |
| workspace.py | 260 | 12 | 35 | 15% |
| git_integration.py | 254 | 11 | 42 | 36% |
| webapp/server.py | 215 | 17 | 22 | 15% |
| chat.py | 171 | 9 | 22 | 23% |
| planbook.py | 175 | 13 | 23 | 19% |
| cli.py | 150 | 6 | 49 | 1% |
| verification.py | 152 | 5 | 59 | 5% |
| providers.py | 136 | 9 | 24 | 4% |
| memory.py | 91 | 6 | 27 | 29% |
| repair.py | 94 | 9 | 14 | 17% |
| catalog.py | 75 | 3 | 37 | 5% |
| config.py | 59 | 2 | 25 | **0%** |
| redaction.py | 50 | 2 | 8 | 44% |
| labels.py | 83 | 2 | 40 | 24% |
| errors.py | 18 | — | — | 50% |

* 39 method names exist in **both** `gui.py` and `webapp/controller.py`; **10 of them are ≥80 %
  identical** once the presentation calls are normalised (`removal_notice` 99 %, `ledger_for` 99 %,
  `check_changes` 98 %, `undo` 97 %, `run_tests` 94 %, `report_run` 85 %, `apply` 70 %,
  `advance_plan` 51 %, `start_plan` 58 %) — about 3.7 kB of duplicated flow.
* Only 9 five-line windows are literally shared between two source files, all of them
  `gui.py` ↔ `controller.py`. So the duplication is *logical*, not copy-paste — which is exactly
  why it drifts silently (see P0-2).
* Repo-wide: **0** `TODO`/`FIXME`/`XXX`, **0** `shell=True`, **1** `# noqa: BLE001` with a reason
  (`controller.py:291`), **4** broad `except Exception` (all justified where they sit),
  `dependencies = []` and **0** third-party imports in `src/`.
* Test count per module: 74 agent · 13 chat · 97 controller · 41 git · 37 gui · 10 memory ·
  17 planbook · 3 redaction · 9 repair · 38 runner · 47 symbols · 18 webapp = **402**.

## What is in good shape

Stated plainly, because the findings below are the interesting part and not the whole file:

* The write path is honest about itself. `prepare_changes` requires 1–8 changes
  (`engine.py:297`), reads every target file's current hash *before* proposing
  (`engine.py:313-317`), rejects a duplicate target on a case-folded identity
  (`engine.py:307-310`), caps total bytes, and refuses an unchanged file. `apply_proposal`
  preflights **all** files before the first write (`engine.py:655-659`) and marks
  `PARTIAL_APPLY` rather than pretending.
* `workspace.write` is the best function in the tree: `fsync` before `os.replace`, mode
  preserved, and the hash re-checked *immediately* before the replace
  (`workspace.py:242-256`).
* `git_integration.py` is 36 % comment and earns it: argv lists only, `shell=False`,
  `stdin=DEVNULL`, scrubbed env, non-interactive flags, and `dirty=None` rather than `0` when git
  did not answer. The verb vocabulary is now pinned by a test (`tests/test_git.py:317`).
* `redaction.py` and `labels.py` are small, dense and single-purpose.
* The runner's output discipline (spinner-collapse at `runner.py:300-305`, bounded queue,
  report freshness by timestamp) is thought through.

---

## P0 — a bound chat can write files with no click

> **FIXED the same day, and closed by tests.** A bound branch now carries `bound`, takes neither
> the folder's remembered mode nor its Auto-Apply switch, and the two setters refuse it with the
> sentence the UI shows. The reproduction below is kept because it is the clearest statement of
> how the hole opened; its line numbers are the ones the review read and have since shifted a few
> lines either way.

**reproduced.** The README's guarantee is *"A chat can be given a project… still no tools, no
proposal, no writes"* (README.md:41-46). Dropping a chat onto a project node does not hold it.

`bind_chat` ends with `controller.py:586`:

```python
self._select_branch(BRANCH_PROJECT if key else BRANCH_CHAT, key, chat_id=chat_id)
```

`composer` is left `None`, and `_select_branch` then does
`self.composer = composer or self._branch_mode(self.branch["key"])` (`controller.py:493`) —
`_branch_mode` returns that **folder's** remembered mode (`controller.py:504-508`) — and the very
next line arms that folder's Auto-Apply switch (`controller.py:494`). `open_chat` gets this right
by passing `composer=CHAT_COMPOSER` explicitly; the drop path does not.

Reproduction (`sandbox/repro-bind-mode.py`, scripted model, temp folders):

```
project armed          kind=project  repo=repo  composer=change  auto=True
new chat               kind=chat     repo=-     composer=chat    auto=False
after the drop         kind=project  repo=repo  composer=change  auto=True
   status              : Chat moved into repo.
   action calls        : 2  prose calls: 2        <- "What does add do here?"
   dialogs             : []
   file changed        : True | return a + b
   session state       : VERIFICATION_BLOCKED
```

A question in a chat that was told *"This is still chat mode… nothing is proposed"*
(`controller.py:588-590`) produced a proposal and **wrote `calculator.py` with no dialog at all**,
because the folder it was dropped onto had been left in Change mode with the switch on.

Fix, in order of value:

1. `bind_chat` passes `composer=CHAT_COMPOSER`, and `_select_branch` refuses
   `auto_apply=True` whenever `self.branch["kind"] != BRANCH_PROJECT`.
2. Better: make the invariant structural instead of a call-site habit. Branch *kind* should be
   the gate: a branch that is a chat cannot hold a change-mode preference or a switch, no matter
   which method selected it. Today the two live in `self._composer_pref` / `self._auto_pref`,
   keyed by **folder** — see P0-2.
3. A test that drives exactly the sequence above (grant → change → switch on → new chat → drop →
   ask a question) and asserts the file is unchanged and `asked == []`.

## P0-2 — mode and Auto-Apply are keyed by folder, but the model says they belong to a branch
**read-verified.** `set_composer` writes `self._composer_pref[self.branch["key"]]`
(`controller.py:516-517`) and `set_auto_apply` writes the same map keyed by folder
(`controller.py:1854`). For a *bound chat* the branch key **is** the project's key, so:

* flipping a bound chat to Change overwrites the project branch's remembered mode;
* turning Auto-Apply on from a bound chat arms the project.

The reproduction printed exactly one entry per folder:
`{'…\\repo': 'change'}` / `{'…\\repo': True}`. This is the same root cause as P0, and it is why
fix 2 above is the one worth doing: once prefs are keyed by branch identity, the folder's switch
cannot be reached from a chat at all.

## P1 — the program tells the user a rule its own switch overrides

**read-verified.** Three user-visible strings still promise the pre-UI-3.0 rule:

| where | text | problem | status |
| --- | --- | --- | --- |
| `repair.py:25-28` (`fix_offer`) | *"It proposes a diff — nothing is written until you approve it."* | shown as the confirmation whose "yes" starts a round that Auto-Apply then writes **without** asking | ✅ `_approval_clause()` names the switch state in both offers |
| `repair.py:31-34` (`step_offer`) | same sentence | same, on the plan-step offer | ✅ |
| `README.md:154` | *"226 tests, all passing"* | measured 402 | ✅ now 525, with the per-module coverage named |
| `README.md:9` | *"`--tk` … is also what runs automatically if no Chromium browser is available"* | `launch.py:73-83` opens a normal browser tab and sleeps; Tk runs only on `--tk` or when `launch.main()` **raises** (`desktop.pyw:16-20`). `launch.run`'s own docstring ("Falls back to the Tk window", `launch.py:64`) is wrong the same way | ✅ both rewritten to what the code does |
| `README.md:124` | *"The ledger key is the project folder plus the plan's SHA-256"* | it is the folder **basename** — see P1-ledger | ✅ README describes the digest; the digest is what `planbook` now uses |
| `README.md:225` (Arabic) | *"لا تُشغّل النسخة أي أوامر build/test على المضيف"* | contradicted by `README.md:99` and `runner.py:385`; host runs are the normal path | ✅ paragraph rewritten to say host runs are the normal path, with the scrubbed env and the tree kill |
| `docs/IMPLEMENTATION-STATUS.md:125` | "`git_integration.py` is 250 lines" | 254 | ✅ |
| `docs/VALIDATION.md:5,27` | "22 cases in test_controller.py", "18 in test_symbols", "36 in test_gui" | 97 / 47 / 37 | ✅ left as history, and the file now says so at the top: each section is that milestone's measurement, with the current totals in a new dated section |
| `docs/AUTOAPPLY-GIT-PLAN.md:19-24` | code anchors `controller.py:1026-1031`, `engine.py:602-613`, `tests/test_controller.py:300` | all shifted; the file is a dated plan, so a "anchors are from the pre-UI-3.0 tree" note is enough | ✅ note added |

The `repair.py` pair is the one that actually misleads: those sentences are the *reason* a user
clicks Continue. Either name the switch in the sentence ("… nothing is written until you approve
it — this folder's Auto-Apply switch is off"), or, if the switch is on, say what will happen.

## P1 — credential redaction cannot match env-var-style names

**read-verified, measured.** `ASSIGNMENT` (`redaction.py:31-33`) starts with `\b(password|…|token)`.
`_` is a word character, so a keyword preceded by `_` has no boundary and the whole line passes
through untouched:

```
AWS_SECRET_ACCESS_KEY=MOCK_SECRET_ACCESS_KEY_EXAMPLE_ABC12345     → unchanged
SPRING_DATASOURCE_PASSWORD=Sup3rS3cretValue                      → unchanged
DB_PASSWORD=hunter2hunter2                                       → unchanged
SLACK_SIGNING_SECRET=1234567890abcdef1234567890abcdef            → unchanged
```

Those values are exactly what a Spring Boot or CI build prints, and they reach the session store
and the next model request through `repair.py:47-49` and `controller.py:1072`. Lowercase and
dotted forms (`spring.datasource.password=…`) do match, which is why the existing tests pass:
`tests/test_redaction.py:13-21` covers 6 of the 10 shapes and none of them is an underscored
SCREAMING_SNAKE name.

Fix: allow a qualifier before the keyword — `([\w.]*_)?(` in place of `\b(` — which keeps every
current negative test clean (`tokenCount = 12`, `username = "admin"`, `Tests run: 8…` all still
unmatched, verified) and add the four cases above to `test_known_credential_shapes_are_removed`.

Related, smaller: three of the eight `PATTERNS` (Slack, Google, GitLab) have no test at all even
though `README.md:108` names Slack.

## P1 — unescaped server text reaches the DOM

**read-verified.** `el(tag, cls, html)` assigns `innerHTML` (`app.js:7-12`), and two call sites
feed it data that did not come from the code:

* `app.js:299-300` — `'Saved as ' + info.notes_file + ' in ' + info.notes_dir + …` from
  `/api/project`. The folder name is user-typed (`＋ new` accepts a path that does not exist yet),
  so a project called `<img src=x onerror=…>` is a live sink. Three lines below, the same drawer
  correctly uses `textContent` (`app.js:295`) — this one just missed `esc()`.
* `app.js:849-850` — `msg.cancel` / `msg.confirm` from the `confirm` SSE event. No caller sets
  them today (`controller.py:1007,1508` pass literals), so this is latent, not exploitable — but
  it is the same function, and the fix is one line.

Hardening worth doing at the same time, all **reported** and consistent with what was read:

* `app.js:633,704,796` interpolate `tone`/`level` straight into `class="…"`; a `"` escapes it.
* No `Host`/`Origin` check anywhere (`server.py:115-131`), so DNS rebinding would make a hostile
  page same-origin and the URL token is the only gate; no CSP is ever sent.
* `launch.py:76` / `__main__.py:33` `webbrowser.open(url)` puts the per-launch token in the
  user's real browser profile and history, while `open_window` already uses a private
  `--user-data-dir`.
* `server.py:54` — the class default is `token: str = ""`, so `compare_digest("", "")` would
  authorise a request if `serve()` ever ran with an empty token. One `if not self.token: return
  False` makes the default fail closed instead of open.
* `server.py:128-131` — `do_POST` answers 403/404 **without draining the body** while
  `protocol_version = "HTTP/1.1"` keeps the socket open (`server.py:50`); the unread bytes are
  then parsed as the next request. `_drain()` already exists at `server.py:86` and is used on the
  oversized-body path (`server.py:105`) — the two early replies just do not call it.

## P1 — the plan ledger is keyed by folder name, and the stored root is never checked

**read-verified.** `planbook.key_for` (`planbook.py:42-43`) is
`re.sub(r"[^A-Za-z0-9._-]", "-", Path(root).name) + "-" + plan_sha[:16]`, and `open_book`
(`planbook.py:61-66`) validates `schema` and `plan_sha256` but **not** the `root` it wrote at
`planbook.py:69`. Two different projects whose folder shares a name and whose `plan.md` is
byte-identical therefore read the same ledger and inherit each other's verified steps. The doc
(README.md:124) already promises more than the code does. One-line fix: refuse a book whose
`root` differs, or hash `engine.project_key(root)` into the filename.

## P1 — the runner can leak a live child, and its kill has no Windows fallback

**read-verified.** Four things in `runner.py`, in order of how likely they are to bite:

1. `run()`'s `finally` (`runner.py:427-429`) only removes the temp report directory. If
   `_collect` raises — a `progress` sink that raises, anything between `Popen` and the return —
   the child is left running with its pipe open, and the `return` at 430 then reads unbound
   locals. A `finally` that kills `process` when `process.returncode is None` costs three lines.
2. `_kill` on Windows (`runner.py:158-160`) is a bare `subprocess.run(["taskkill",…], timeout=30)`
   with no `try`, while the POSIX branch (`runner.py:161-166`) does fall back to
   `process.kill()`. A `TimeoutExpired` or `FileNotFoundError` from `taskkill` escapes before
   anything is killed — the opposite of what the function is for.
3. `process.wait(timeout=30)` at `runner.py:377` is unguarded: a child that survives the second
   kill raises out of `run()` after the project's build already ran, and the model gets nothing.
4. `progress(line)` runs **inside** the read loop (`runner.py:363`) and the deadline is re-checked
   only at the top of it (`runner.py:334`). Both app sinks are non-blocking (`Hub.publish` uses
   `put_nowait` and drops on `queue.Full`, `server.py:42-46`; `gui.py` uses an unbounded queue),
   so this is the CLI's problem: `progress=print` against a Windows console in QuickEdit select
   mode freezes the reader and defers the timeout indefinitely.

Also **read-verified** as design risks rather than bugs: `npm test` / `pnpm test` are allowlisted
but the argv that actually runs is the project's own `package.json` script (`runner.py:92-104`),
and every recipe interprets project-controlled config (`conftest.py`, `pytest.ini`, `uv.lock`,
`pom.xml`). The Apply dialog already names the command, which is the honest mitigation, but the
README should say in one sentence that *running* a project's command is running that project's
code. `maxsize=2000` (`runner.py:314`) bounds lines, not bytes, and `report_counts` reads every
candidate report with no size cap (`runner.py:233,256`).

## P1 — verification can call a green run "unverified", and an empty proposal can pass

**read-verified.** `verification.py:114-116` gives Gradle `counts = []`, so `proven_tests` is
always False and a green Gradle run becomes `"unverified"` — in the **Docker sandbox path only**,
which the window never uses (`gui.py:1850`, `controller.py:1285` call `verify(path)` with no
recipe; `docker_check` is reachable only from `cli.py:137`). It is a real dead end for a user who
follows `README.md:223`, and it is undocumented.

`static_check` returns `[]` for a session with no changes, so `any(failed)` is False
(`verification.py:131-133`) and `apply_proposal` never asserts `session["changes"]` is non-empty
(`engine.py:646-649`). No session the program itself builds can be empty
(`engine.py:297` requires 1–8), so this is a one-line guard against a hand-edited
`session.json`, not a live bug.

## P1 — the repository map is quadratic, and its own file cap costs ~10 s a turn

**reproduced by measurement.** `symbols.dependencies` (`symbols.py:541-564`) walks
*rows × imports × every global key*, and calls `_linked` — which does two `re.split`s — on each
pair. `render()` calls it unconditionally (`symbols.py:567-570`), and `render()` is what builds
the map that goes into **every** planning turn (`workspace.py:190-204` → `engine.py:429`). The cap
that bounds it is `MAX_FILES = 300` (`symbols.py:17`).

Measured on this machine with synthetic rows (`sandbox/bench-symbols.py`, same shape a Java
project produces — deep packages, ten internal imports each):

```
 20 files ->   0.05 s
 40 files ->   0.18 s
 80 files ->   0.69 s
160 files ->   3.01 s
300 files ->  10.02 s        <- the cap, i.e. the worst case the code allows
```

Quadratic in the number of files, and it is paid before the model is asked anything — on the CPU
only, which is the exact machine class this project targets. `render`'s 12 000-character limit
(`symbols.py:567`) cannot help, because the edge sweep finishes first and the limit is applied
afterwards.

Fix, in order: build a suffix index of `owners` once (`dependencies` already computes the key set
at 547-550) and split `dotted` into segments **outside** the inner loop, so a lookup is
`O(segments)` instead of `O(owners × segments)`; require ≥2 shared segments before an edge counts,
which is also what stops same-named types in different packages from linking
(`_linked` at 536-538 matches on a one-segment tail).

## P1 — the Tk window ignores its own Request-timeout setting on Send

**read-verified.** `gui.py:1302-1303` builds the settings for an ordinary Send as
`replace(Settings(), provider=…, model=…, max_turns=…)` — with **no `timeout_seconds`**, so it
stays at `config.py:14`'s 120 s default. The same window sets it correctly in the other path
(`gui.py:1751`: `timeout_seconds=self.request_timeout_seconds()`), the spinbox is wired and
persisted (`gui.py:740-744`, `201-204`, `921`), and the web controller does it right in both
places (`controller.py:970`, `1410`). A user who raises the timeout to 900 s because a 1.5B model
is slow gets the setting honoured for fix rounds and ignored for the first request — and
`engine.py:480` derives its whole-run budget from that same number, so the 1200 s floor is
computed from a value the user never chose.

Two lines. This is also the clearest example of why the P2 duplication is worth closing: the same
object is built twice and only one copy was updated.

## P2 — the "still ask" decision is in one expression, and its copy in the other window is 99 % identical

**read-verified.** The Auto-Apply refusal lives in `apply()` as `forcing`
(`controller.py:1201-1204`): `removal_notice()` non-empty **or** a prior interrupted apply.
Two docstrings describe only the empty-file half. Meanwhile:

* `removal_notice` exists at `controller.py:1141` **and** `gui.py:1666`, 99 % identical;
* `run_tests` (94 %), `undo` (97 %), `check_changes` (98 %), `ledger_for` (99 %),
  `report_run` (85 %) are the same pattern;
* `repair.py:20` even carries the comment *"The two windows ask the same question in the same
  words, so the wording lives beside the budget it names"* — the strings were already centralised;
  the **flow** around them was not.

The seam is small and already visible in the data: the two files differ only in how they write a
status (`self.status.set()` ×52 vs `self.status =` ×72), how they add a conversation line
(`chat_message` ×15 vs `_add` ×19), how they ask (`messagebox.*` ×6 vs `self.confirm` ×6) and how
they stream (`events.put` ×7 vs `_emit` ×13). `self.root` (33 uses) is Tk-only and has no
counterpart. Four primitives, not ten.

Recommended shape, in this order:

1. Extract the decision, not the window: `def must_ask(session) -> str` returning the reason (or
   `""`), used by `apply()`, quoted by both docstrings, and unit-tested on its own. One function,
   no risk, and it kills the 99 % copy of `removal_notice` at the same time.
2. Give `gui.py` the same four adapter methods the controller already has, then move the 10
   ≥80 %-identical methods into a shared base that both windows subclass. This is the only change
   in this document that touches both presentation layers; do it after the P0s, with
   `tests/test_gui.py` and `tests/test_controller.py` as the net.
3. Do **not** split `controller.py` into modules yet. Its length is real but it is 118 small
   methods with a 14 % comment ratio and a clear job; step 2 removes most of the reason to care.

Inside `controller.py` itself, the named duplication is: five copies of the
key→`setdefault`→`_select_branch` tail (`626-632, 667-670, 684-686, 700-703, 1754-1756`) →
`_grant_folder(path, composer, keep_chat)`; `1029-1050` ≡ `1420-1441` (a `work()`/`done()` pair
that records to the ledger) → `_plan_job(...)`; `1540-1554` ≡ `1556-1570` (session/chat cache
twins) → `_load_cached(path, cache, loader)`. `__init__` is 129 lines and contains a second
registry parser (`183-238`) that must stay format-compatible with `_save_state` (`1847-1866`) —
that pair wants `_load_ui_state()`, and it is where a registry-format change will otherwise break
quietly.

## P2 — job lifecycle and concurrency

**read-verified.** `worker()` clears `self.busy` at `controller.py:293` and only then runs
`on_done(result)` at `302`. That ordering is deliberate — `_applied` starts a chained
`run_tests`, and `run_job` refuses when `busy` — but it means an HTTP action can land in the
middle of a completion that is still mutating `session`, `messages` and `repo`. `run_job`'s
guard itself (`controller.py:276-279`) is an unlocked check-then-set, and `server.py` dispatches
each POST on a `ThreadingHTTPServer` thread. The browser disables the button while busy, so this
is defense-in-depth, not a live race — but `snapshot()` returning the live `self.messages` /
`self.log` lists (`controller.py:381-404`) while a worker appends to them is not guarded at all,
and a JSON mid-append iteration is how a UI gets a 500.

**reported, plausible, not confirmed:** `join()` clears `self._jobs` (`controller.py:329`) so a
thread appended after the last scan is never waited on; `_replies` keeps an entry after its 1800 s
timeout (`controller.py:241-248`); `_nav_projects` calls `self.projects.setdefault(...)` while
merely *listing* chats (`controller.py:1587,1600`), which turns a folder named in a `chat.json`
into a grant with no user gesture. That last one deserves 10 minutes of reading before the next
release that touches the registry.

## P2 — front-end correctness

All **reported** (the cited lines were read for the security section and are consistent, but the
behaviours were not exercised in a browser in this session):

* `connectEvents()` runs before `/api/bootstrap` resolves (`app.js:1124`), so an early `busy` or
  `message` event can reach a `DATA` that is still null (`app.js:67,784`).
* `submit()` never clears `state.draftTimer` (`app.js:612-618` vs the timer set at `1111`), so the
  debounced `set_draft` can re-store text that has already been sent.
* `appendToken` repaints the streaming bubble with `innerHTML` (`app.js:516-519`), which drops the
  Copy / Apply-to-File wiring done in `renderThread` — the delegation belongs on `#thread` once.
* `JSON.parse(localStorage…)` is unguarded in three places (`app.js:1128`, `index.html:15`,
  `app.js:1084`); a throw at boot is swallowed by the `.catch` at `1133`, which then blames the
  server and leaves `.booting` on the document, freezing every transition.
* Status is written by positional lookup (`$('title').nextElementSibling`, `app.js:64`) while the
  header also carries `DATA.header.subtitle` (`:393`) — two writers for one node.
* `contract.py` declares five methods but `server.py:159` calls a sixth, `project_info`, which
  `fake.FakeController` does not implement — so `python -m ai_code_engineer.webapp --fake` cannot
  open the Project drawer at all. That is the UI-development mode the contract's own docstring
  points at (`contract.py:4-6`).

## P2 — dead code and stale scaffolding

**Status: all of it dealt with, except the last bullet, which was measured and kept.** Deleted:
`/api/options` with `options()` in both controllers and in the Protocol, the orphaned `_git_info`
copy, `verification.py`'s unused `AgentError`, the `self.events` queue and its put, `ICON.cog`, the
`clear_project` and `search` actions with the `self.search` state and the two inert server-side nav
filters they fed, the unread `"open"` field on a sidebar group, the unreachable non-dict `break` in
`_balanced_object`, and ten dead CSS selectors.

* `controller.py:349-359` — an orphaned copy of `_git_info`'s docstring and body sitting below
  `options()`'s `return`. Unreachable, and a trap for anyone who reads `options()` first. It is
  left over from this session's own edits. Delete 349-359; `361-372` is the live one.
* `verification.py:15` imports `AgentError` and never uses it.
* `server.py:148` `/api/options` and `contract.py:26` `options()` are never called by the client;
  the same for controller actions `clear_project` and `search` (`app.js:1105` filters locally).
  Either delete them or make the client use them — an unused endpoint is a surface.
* `app.css:23,368,445,535,553,561` (`.app.rail-hidden`, `.row.wrap`, `.line-btn.sm`, `.trow.sel`,
  `.cmd .kbd`, `.skel`) have no producer in `app.js`; `ICON.cog` (`app.js:25`) is unused; the
  860 px breakpoint (`app.css:584`) is unreachable because `launch.py:16` sets `MIN_WIDTH = 1180`.
  **The breakpoint claim is wrong.** `MIN_WIDTH` only feeds `--window-size`, and only for a profile
  with no saved `Preferences`, so a hand-resized narrow window reaches the rule. What is true there
  is smaller: the collapse button lives inside the panel the rule hides, so below 860 px the sidebar
  cannot be brought back. That needs a drawer design and was left alone.
* `engine.py:126` — a brace-balanced `{…}` that `json.loads` accepts is always a dict, so the
  non-dict `break` cannot be reached.
* **reported:** `workspace.clear_index_cache` and `verification.py`'s `snapshot_hash` are written
  but never read outside tests. **Measured, and kept:** the first is the only way three cache tests
  force a re-index, and the second is a field of the verification record that gets persisted.

## P2 — the guarantees the two windows do not share

**Status: three of the four are fixed; the second is a written-down decision.** The Tk streaming
lines are now redacted at `poll()`, the one place every runner line reaches the window through, with
a test that plants a password in a streamed line and reads both the log and the status line back.
`verification.py` passes `env=runner.child_env()`. `git_integration._done` waits on
`Popen.communicate(timeout=…)` and tree-kills through `runner.kill_tree` (the function the build
runner uses, renamed when it became shared) on both the timeout and the failure path.

The run-report divergence stays: the Tk `on_done` runs on the worker thread, and a `messagebox`
there pumps a Tk loop from a thread that does not own it — which is why the offer dialogs were
built for the web window only. It is a documented difference in behaviour, not a silent one, and
`docs/ENHANCEMENTS-PLAN.md` §5 says so.

The four were, as written:

**read-verified.** The Tk window is a fallback, but it advertises the same safety story, and three
of them are only implemented in the web one:

* **Streaming output is not redacted in Tk.** The web path scrubs every line at capture
  (`controller.py:1072`: `redact(line).strip()[:500]`); the Tk path pushes the raw runner line
  into the log and the status line (`gui.py:1356`, `1773`, `1824` → drained unredacted at
  `gui.py:877-881`). A build that prints its JDBC URL shows the password whole in the Tk window.
  One `redact(...)` in the lambda.
* **Run reporting has already diverged in a rule, not just in looks.** `gui.py:1781-1794` and
  `controller.py:1362-1377` are the 85 % copy, and the missing 15 % is the behaviour: after a
  manual run fails, the web controller offers a fix round (`controller.py:1379-1393`) and the Tk
  window silently stops. This is the concrete case for punch-list item 12 — the copies do not fail
  symmetrically.
* **`verification.py:84` starts `docker` with no `env=`,** so the CLI inherits the whole parent
  environment, API keys included — the one subprocess in the tree that does not use
  `runner.child_env()` (`runner.py:407`) or `git_integration`'s scrubbed map. The container itself
  is properly sealed (`--network=none --read-only --cap-drop=ALL`, `verification.py:74-81`), so
  this is a broken invariant rather than an open leak, and it is reachable only from
  `cli.py:137`. Pass `env=runner.child_env()`.
* `git_integration._done` uses `subprocess.run(timeout=…)`, which kills only the direct git child —
  a credential helper git spawned can outlive it, while `runner._kill` does a `taskkill /F /T`
  tree kill (`runner.py:157-166`). Reusing the tree kill here is the consistent answer. **reported.**

## P2 — unused code and config drift still under maintenance

* **read-verified:** `controller.py:120` creates `self.events` and `controller.py:1059` puts
  `("progress", line)` into it, and **nothing in `webapp/` ever reads that queue** — `grep` for
  `events.get` returns only `gui.py:877`. So the web controller accumulates one entry per build
  output line for the life of the process, on an unbounded `queue.Queue`. Delete the queue and the
  put; `_build_line` already emits to the hub.
* **read-verified:** the request-timeout clamp exists in four places with two different ranges —
  `config.py:54` allows 1..900 while `gui.py:204`, `controller.py:206` and `controller.py:754,766`
  all clamp to 30..900. `config.validate` should own the range and the UIs should only enforce the
  narrower one it advertises.
* **reported:** `runner.py:228-233` `report_counts` calls `read_bytes()` on every globbed report
  with no size cap and hands it to `ET.fromstring`; the recursive `build/test-results/**/TEST-*.xml`
  pattern globs unbounded. Two cheap guards (`st_size` limit, candidate count limit).
* **reported:** `symbols.py:180-200` — when `MAX_TYPES` is reached, the declaration line takes
  neither branch, its brace is never pushed, and that type's later methods fall into the
  file-level bucket; the other scanners route type creation through `_declare_type`, which enforces
  the cap properly. Fixing `_jvm` to use the shared helper closes this and the duplication at once.
* **reported:** `errors.py` vs `labels.py:81-82` — `runner.py:389` and `controller.py:443,1846`
  raise bare `ValueError`, which `friendly_error` does not preserve, so the specific reason is
  replaced by "Could not complete the operation." in both windows. Raise `PolicyError`.

## P3 — structure and duplication that is worth one sitting each

* `engine.plan` is **221 lines** (`engine.py:383`), the longest function in the tree by 55 lines.
  Reported seams, all read-verified as plausible against the line ranges: validate inputs
  (396-400) → `open_session` (401-428) → `build_context` (429-473) → budget/trim (474-492) →
  action dispatch (500-571) → error recovery (572-596) → terminal persistence (597-603). The
  dispatch block is the one worth taking first: it is where a new action type has to be threaded
  through.
* `propose_block` (`engine.py:341-380`) re-implements the task-length check (354 vs 396, with a
  different dash glyph in the message), the `chat_id` regex (365 vs 407), the session skeleton,
  `proposal_hash`, the event and the `atomic_json` — i.e. it is a second copy of the tail of
  `plan()`. Two shared helpers (`_new_session`, `_finalize_proposal`) and the two entry points
  cannot drift apart. **This matters more than it looks:** the block button is the newest and
  least exercised write path, and its integrity comes from being *the same* code as a model
  proposal.
* Three ideas of "same file" exist: `seen` case-folds only when `os.name == "nt"`
  (`engine.py:307`), `observed` keys on the model's raw text (`engine.py:315`), and the plan guard
  compares unresolved `Path`s. On a case-insensitive non-Windows mount two changes can hit one
  file and end in `PARTIAL_APPLY`. One canonical identity on `Workspace` fixes all three.
* Three atomic-write variants (`engine.py` `atomic_json`, `workspace.py:242-256`,
  `memory.py:68-72`) — and `memory.write`'s comment claims crash safety without the `fsync`
  `workspace.write` does. **read-verified.**
* Four separate "did a test actually run" evaluators: `verification.py:110-116`,
  `planbook.py:105-127`, `repair.py:36-38`, `runner.py:267-276`. The Gradle dead end above is what
  happens when one of them is edited and the others are not.
* `symbols.py`: the depth/brace bookkeeping is written three times
  (`201-203, 329-333, 404-408`) and `_jvm` re-implements `_owner_of` inline (`193-200` vs
  `337-344`). **reported**, and the fix is one shared helper for all five scanners.
* The suffix case rule: `engine.py:325-327` compares `path.suffix == ".py"` / `".json"` while
  `workspace.py:132` admits any case via `.lower()` — so `APP.PY` skips `ast.parse` on its way to
  disk. `verification.py:33` repeats the same case-sensitive comparison. One `suffix.lower()` at
  both gates. **read-verified.**
* `repair.py:51` does `STATE_FOR_RUN[result["status"]]` — a hard index into a dict of five keys,
  *after* the run was already appended at `:50`. `record_run` is only ever called with a
  `runner.run` result (checked at `gui.py:1774`, `controller.py:1354`), so the `blocked`/`stale`
  statuses that `verification.py` produces cannot reach it today; the fragility is that a sixth
  runner status raises `KeyError` from the function that records the run. `.get()` with an
  explicit "unknown status" is the whole fix.

## P3 — tests

**read-verified** except where marked.

* **No `tests/test_workspace.py` and no `tests/test_verification.py`.** The 260-line path policy
  and the snapshot rule are only touched incidentally through other modules. Every P1 above that
  lives in those two files has no test that would catch it.
* `tests/test_controller.py:1007` — `assertEqual(command[0], "explorer.exe" if os.name == "nt"
  else command[0])` compares a value to itself off Windows, so the fixed-argv guarantee
  `README.md:108` leans on is only asserted on one OS.
* Auto-Apply's **second** mandatory stop (a prior `PARTIAL_APPLY`) has no test; the existing case
  (`test_controller.py:461`) covers the `start_plan` warning with the switch off.
* `catalog.py` (75 lines, including the `:free`-pricing guard behind `README.md:148`) and
  `cli.py` (150 lines, the documented `python agent.py plan/apply/verify/rollback` surface) have
  effectively no coverage.
* `/api/events` is never requested in any test (`server.py:166`), and `server.py:63`'s
  `X-Auth-Token` header branch is never exercised — every test uses `?t=`.
* 11 model doubles across five test files do one job: `ScriptedProvider` appears twice
  (`test_agent.py:23`, `test_repair.py:18`), `ChatModel` is byte-identical in
  `test_controller.py:46-54` and `test_gui.py:78-86`, and `DualModel` (`test_controller.py:600`)
  is `ProposalModel` + `ChatModel` recombined. Three copies of the 12-key runner result dict
  (`test_controller.py:57-62`, `test_gui.py:320-328`, `test_repair.py:35-40`), and the
  temp-folder + patch trio is written four times inside `test_controller.py` alone
  (`124-141, 628-643, 1140-1163, 1229-1241`). A `tests/doubles.py` would delete ~200 lines and
  make the next feature round cheaper.
* Flakiness on a loaded machine (this is the shape of the one intermittent failure the last
  regression could not reproduce): `test_runner.py:372-378` asserts a live child finished in
  under 20 s; `test_git.py:234-239` depends on an 8 s cache TTL not elapsing between two calls;
  `test_git.py:373-386` uses a non-lenient `TemporaryDirectory` as the cwd of live git commands
  while `test_controller.py:124` already needed `ignore_cleanup_errors=True` for the same reason;
  `test_agent.py:405` patches process-wide `time.monotonic`; `test_git.py:465` pops `GIT_DIR`
  permanently and `test_runner.py:101,221` mutate `os.environ`. **reported.**
* Tests that reach into privates (`_fix_round`, `_draft`, `_composer_pref`, `_select_branch`,
  `_pending_model`, `_timers`, `_subtitle`) will fail on a pure rename. Some of those are the
  honest way to test a state machine; the fix is not "never" but "know which ones are load-bearing
  and drive the rest through `action()`/`snapshot()`".

## P3 — packaging and repo hygiene

**read-verified.**

* `pyproject.toml` has no `[tool.setuptools.package-data]` and there is no `MANIFEST.in`, while
  `server.py:16` resolves `STATIC = Path(__file__).parent / "static"`. Run from source (the only
  supported way today) it works; `pip install .` would ship a package with no UI. One stanza fixes
  it, or the README should say plainly that installation is not supported.
* `.gitignore` ignores the run stores but **not `.agent-projects.json`** — the registry that holds
  every granted absolute path on the machine, plus `ui.auto_apply` and `ui.composer`. It also
  misses `.screens/`, `profiles/` and the stray `.design-preview$1.png` in the root, and there is
  a `spring-rpoject/` directory whose name is misspelled. Not academic: this tree is not a git
  repository yet, and the round that adds git-backed checkpoints is the round that will `git init`.
* No CI of any kind, and no lint config — for a project whose whole safety argument is "402
  offline cases pass", the cheapest real improvement is a `Run-Agent-Tests.bat` (or a scheduled
  task) that runs `python -m unittest discover -s tests` + `node --check` + `check_contrast.py`
  and fails loudly.

---

## Rejected findings

Kept so they are not re-reported:

1. **`engine.review()` raises `KeyError` on `DISCOVERING`/`CANCELLED` sessions** — rejected.
   Every session is built with both keys at `engine.py:403-404`, and `review()` at 628-630 only
   reads those two. No reachable session lacks them.
2. **`planbook.complete()` lets an already-verified step re-complete with any session** —
   rejected, it is the reverse. `planbook.py:135-136` *returns early* when the row is verified, so
   nothing is re-completed; the binding check at 137 is skipped precisely because there is nothing
   to bind. The real residue in that function is cosmetic: `verified_at` stores
   `session["created"]` (`:142`), not a completion time.
3. **`ws.write()`'s returned digest is discarded so nothing proves the bytes on disk** —
   downgraded from *bug* to *nice-to-have* after reading `workspace.py:232-260`: the returned
   value is `digest(raw)` of the same buffer the caller already hashed, so comparing it at
   `engine.py:665` would be a tautology. A post-`os.replace` re-read would be the only real
   addition, and the preflight loop already re-reads every `before_hash`.

## Punch list, ordered

**Status column added the same day.** Every measurement behind a ✅ is in
*The same day, after reading the whole project* in `docs/IMPLEMENTATION-STATUS.md`; the one ❌ is a
decision, not an oversight, and is argued in [Deliberately not recommended](#deliberately-not-recommended)
adjacent text. The suite went **402 → 525 tests** across the round, offline, green.

| # | item | files | effort | risk if left | status |
| --- | --- | --- | --- | --- | --- |
| 1 | Bound chat must not inherit Change mode or arm Auto-Apply; add the reproduction as a test | `controller.py:586,493-494` | S | **High** — breaks the project's stated red line | ✅ reproduced in `sandbox/repro-bind-mode.py`, then gated; 5 tests |
| 2 | Key mode + switch by branch identity, not folder | `controller.py:504-541,1854` | M | High — same class of bug, new call sites | ✅ `branch.bound` owns the refusal; prefs untouched by a bound chat |
| 3 | Index the import sweep so the 300-file map stops costing ~10 s a turn | `symbols.py:536-570` | M | High on the target machine class — measured | ✅ 10.02 s → 0.06 s, byte-identical output, cap-pinning test |
| 4 | Redact `NAME_SECRET=value` env forms; test the 3 untested shapes | `redaction.py:31` | S | Med-High — secrets into logs and cloud turns | ✅ 10 real build lines scrubbed, 8 ordinary ones left alone |
| 5 | `esc()` the two `innerHTML` sinks; drain bodies on POST errors; fail closed on empty token | `app.js:299,849`; `server.py:54,128-131` | S | Med — local-only today, one line to keep it that way | ✅ both sinks plus the class-attribute tones; `_refuse` drains; empty token refuses; tests for each |
| 6 | Kill the child in `run`'s `finally`; guard `taskkill` and the last `wait` | `runner.py:157-166,377,427` | S | Med — leaked build processes | ✅ plus the report-size cap, and a pipe-close hang found while measuring it |
| 7 | Tk Send must pass `timeout_seconds` like every other path does | `gui.py:1302-1303` | S | Med — the fallback window ignores a setting the user can see | ✅ |
| 8 | Redact streamed lines in the Tk log too | `gui.py:877-881,1356,1773,1824` | S | Med | ✅ at `poll()`, the one drain point; test plants a password |
| 9 | Say the folder's switch state in the offer text | `repair.py:25-34` | S | Med — the sentence is why users click Continue | ✅ `_approval_clause(auto_apply)` in both offers |
| 10 | Ledger key must include the real root; check `book["root"]` | `planbook.py:42-66` | S | Med — cross-project step inheritance | ✅ second time of asking: the first report of a fix had not landed |
| 11 | `must_ask(session)` + delete the 99 % copy of `removal_notice` | `controller.py:1141,1201`; `gui.py:1666` | M | Med — the refusal rule is maintained twice | ✅ one rule, one notice, four unit tests |
| 12 | Lowercase the suffix at both syntax gates | `engine.py:325`; `verification.py:33` | S | Low-Med | ✅ and pinned for `.PY`/`.JSON` |
| 13 | Delete `controller.py:349-359`, the unread `self.events` queue, the unused import, dead CSS, `/api/options` or its callers | several | S | Low, but `--fake` and the drawer already drifted apart through this | ✅ see *P2 — dead code*; 10 selectors and 4 dead actions gone |
| 14 | `env=runner.child_env()` for the docker call | `verification.py:84` | S | Low-Med, and it restores a stated invariant | ✅ the spawn **and** the `docker rm -f` cleanup, which the first fix missed |
| 15 | `tests/doubles.py` + `test_workspace.py` + `test_verification.py` | tests | L | Low now, high later — three of the P1s above live in files with no test | ✅ 75 new cases (40 + 35) in the two missing files. `tests/doubles.py` was not built: the stubs stay next to the tests that need them |
| 16 | Shared Host seam for the two windows (4 primitives) | `gui.py`, `controller.py` | L | Med — 10 methods drifting, and #7/#8 are what drift looks like | ❌ not built; see the note under *Deliberately not recommended* |
| 17 | Doc sweep: 226→402, the `--tk` claim, the ledger-key claim, the Arabic "no host build" line, stale anchors | README, docs | S | Low, but the docs are the safety argument | ✅ all five, and this document's own wrong 860 px claim |
| 18 | `package-data`, `.gitignore` for the registry, a test-runner batch file | root | S | Low | ✅ wheel verified by building one; `run-tests.cmd` resolves its own folder |

### What the round found that this review had missed

Fixing item 18's drift with a test that compares what `app.js` reads against what the two
controllers send turned up four things on its first run, all now fixed:

* `webapp/fake.py`'s `snapshot()` had `git` and `banner` nested **inside** `plan`, so the preview
  window showed no git chip and no auto-apply banner — for the whole time it has existed.
* The preview sent no `branch`, no `composer` and no `icons`, so the mode badge, the Auto-Apply
  pill and the project-mark picker were invisible in the one window meant for reviewing them.
* `app.js` gated the empty-Send-means-next-step gesture on `DATA.chained`, a field the real
  controller never sends; the gesture was therefore dead in the shipped UI and alive only in the
  preview. It reads `DATA.settings.chained` now.
* `workspace.AGENT_CONFIG_DIRS` carried bare `policies` and `skills`, so a Java package named
  `com.acme.policies` was refused as a write target. Those two are root-level-only now.

`verification.snapshot()` also refused the *entire* Docker verification for one file over the
128 KiB prompt ceiling — a routine `package-lock.json`. It carries what it can and skips the rest,
with the skip applied identically on both sides of the drift comparison.

The last one came from the change the user asked for on top of this list — every project node
collapsed when the window opens. Implementing it exposed that the arrow was a one-way control for the
active project: `open` was `state.expanded[key] === true || branch-in-front || search-hit`, while the
click wrote `state.expanded[key] = !open`, so on the node in front of the window the click stored
`false` and the branch clause immediately re-opened it. The stored choice now wins outright and only a
search overrides it (`app.js:173-180`, pinned by a shape test in `tests/test_webapp.py` and re-read
from the live page in both directions).

## Deliberately not recommended

* **Splitting `controller.py` by line count.** 1914 lines is a lot, but it is 118 small methods
  with one job and the best-commented file in the tree after `git_integration.py`. Item 8 and 12
  address the real problem; a mechanical split would move the duplication and break 97 tests.
* **Deleting the Tk window.** It is 1901 lines of drift, but it is the only thing that runs when
  Chromium or a free port is missing, and `tests/test_gui.py`'s 37 cases are load-bearing for that
  claim. Item 12 is how it stops costing.
* **Any new dependency** (an HTML sanitiser, `ruff`, `pytest`, a CI service). The stdlib-only rule
  is worth more than the ergonomics it costs, and `check_contrast.py` + `unittest discover`
  already do the jobs a tool would.
* **Turning the Auto-Apply switch off by default or widening the refusal cases.** That is a
  product decision the user made on 2026-09-26 and it is documented as such; this review's issue
  is that a *chat* can reach the switch, not that the switch exists.
* **Making `approved_hash` an authorization check.** It is a mistake guard and says so
  (`engine.py:646-649`); the only record that distinguishes a click from the switch is the
  `approved` event, which is the correct place. Worth one sentence in the docs, not a redesign.
