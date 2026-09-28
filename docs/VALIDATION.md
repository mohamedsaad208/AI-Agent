# Validation — first Python milestone

Each section below records what was measured **on its own date**. The case counts inside an older
section are not stale claims — they are that milestone's numbers, kept as run — and the current
totals are in the newest section.

## UI 3.4 — 2026-09-26 (step lines in the chat, and a status line that survives)

`python -m unittest discover -s tests`: **624 tests pass**, offline, synthetic model, no display, in
125 s. Per module: `test_agent` 74, `test_chat` 13, `test_controller` 139, `test_git` 41, `test_gui` 38,
`test_labels` 30, `test_memory` 10, `test_planbook` 19, `test_redaction` 6, `test_repair` 16,
`test_runner` 41, `test_symbols` 54, `test_verification` 35, `test_webapp` 68, `test_workspace` 40.
`node --check` clean. `tools/check_contrast.py`: **6 combinations, 126 pairs, PASS** — unchanged
count, because the strip draws in `faint` on the page background, a pair the audit already graded.

Two premises were measured before being coded against, because both decide the shape of the feature:

- **How many chat rows a step per action costs.** Read out of the 39 stored sessions in `.agent-runs`:
  24 have **zero** tool events, median 0, maximum 5, 34 in total (13 `read_file`, 3 `list_files`). So
  one row per announced action is 0-5 rows per task on this machine's models.
- **What the status line actually did.** Driven in the running page before the change: a
  `{"kind":"status"}` event assigned `$('title').nextElementSibling`, i.e. the header subtitle — the
  probe text appeared there and the following state push replaced it with
  `demo2 · step 2/5 · qwen2.5-coder:1.5b`, while `state.status` still held the probe and nothing
  drew it. `#busybar` measured `textContent: ""` and computed `height: 2px`.

Live, after the change, from the `--fake` window:

- The strip renders as a 26 px line of `rgb(107,102,92)` at `11.5px` reading the server's own status
  (`Turn 3/12: asking qwen2.5-coder:1.5b...`), is visible, and keeps the shimmer as a `::after` rule
  while `busy`.
- An injected `status` event went **into the strip** with `#subtitle` unchanged; an Arabic line came
  back `dir="rtl"`; a full state push restored the server's line rather than erasing it.
- The four scripted step rows rendered in the thread with `Steps` as author — scanning, reading (with
  the long Java path intact), searching, and `✍️ Proposed changes for 3 file(s): …` — and each carried
  a copy button, which is how UI 3.3's row buttons came into the count (7 copy targets in the thread).

Controller-side coverage added with the feature, each one failing without the change: the exact step
sequence in the thread including the **collapsed repeat** of the same file, the Arabic step line
chosen by the task and not the window, the status field ending on the outcome
(`Proposal ready. Review the changes, then apply them if you want.`), a job that says nothing else
clearing its own running line instead of leaving `Turn 5/12…` on screen, the manual-apply row, the
`⚙️ Executing: python -m unittest discover -s tests -v` row before the command runs, the blocked row
carrying `⛔` and the model's own reason — and one redaction test that runs a failing build whose
failure line contains `password = "hunter2-secret-value"` and asserts the string is absent from the
Checks row while `[redacted]` is present.

**The finding behind that last test:** `run_tests.done()` hands `report_run` the raw runner result,
and `runner.run` builds `failures`/`tail` from unredacted output (`repair.record_run` and
`_build_line` scrub only their own paths). Before this round the chat was therefore the one surface
where a credential printed by a test could arrive whole — and since UI 3.3 it is a surface with a copy
button on every row. Fixed at the display boundary, capped at `repair.FAILURES_KEPT` lines, pointing at
Activity for the rest.

Three deviations from the specification's letter, stated because they were its words: the strip is the
existing bar under the header rather than a new one at the bottom (the dock already carries the queue
strip and the composer); "formatted" snippets are ` · `-separated plain text, since tool rows are
escaped and the pill collapses newlines; and `⛔ Blocked` became a prefix on the failure row that
already states the reason, not a second row saying it again.

Still not validated, unchanged: the Tk fallback window (it already had a real status label and gains
the same step lines through `progress`, but has no strip and no step rows), the Docker worker (absent,
fails closed), live OpenRouter requests (no key), and the client's own English toasts — the language
rule reaches text the server writes, which is why every sentence in this feature goes through
`labels.py`.

## UI 3.3 — 2026-09-26 (model search, row copy, and what the preview window never answered)

`python -m unittest discover -s tests`: **603 tests pass**, offline, synthetic model, no display in
108 s — against 208 s and 438 s earlier the same afternoon for the same machine and a smaller suite,
which is why the README now quotes the count and not the clock. New coverage:
`test_controller` 125 → 131 (six cases on the filter view), `test_webapp` 51 → 62 (the copy row, the
sheet's search, the CSS decision, and the action-parity test). `test_labels` 23, `test_agent` 74,
`test_git` 41, `test_symbols` 54, `test_runner` 41, `test_workspace` 40, `test_gui` 38,
`test_verification` 35, `test_repair` 16, `test_chat` 13, `test_memory` 10, `test_planbook` 19,
`test_redaction` 6.

`node --check` clean; `tools/check_contrast.py`: **6 combinations, 126 pairs, PASS** — unchanged,
because the copy button reuses `faint` on the page background, which the audit already graded.

The premise that needed measuring was not the filter's behaviour but its blast radius: typing in the
old box rewrote the loaded catalog, cleared the selected model and wrote the preferences file per
keystroke. Six tests now hold the read-only view, including one that patches `_save_state` and
asserts a filter keystroke never reaches the disk.

Driven against the running `--fake` window, structurally (no screenshot at this viewport):

- Model sheet: opened with the search box focused, `7 models` → `cloud` = **1 of 7** → `13b` = 1 →
  `zzz` = *"No model matches that search."* → cleared back to 7. **Enter** after `gemma` posted
  `set_model` and the pill read `codegemma:2b`.
- Settings → Models, the control that was silently dead in the preview: typing `cloud` narrowed the
  served list 7 → 1 while `DATA.provider.model` stayed `codegemma:2b` and the settings line kept
  describing it. Clearing restored 7.
- Copy: rendered on rows 0, 2 and 3 — assistant, assistant, and the `Changes` tool notice — and
  **absent** on row 1, the user's own message. Clicking copied the message's raw text (`Project
  **demo2** is attached…`, i.e. the source, not the rendered DOM) and raised *Copied to clipboard*.
  Measured 24–25 px with `--faint` ink, always visible (the first cut was hover-gated; that was
  changed during review because a control you have to find by accident is not a control).
- `↻ Again` with `DATA.busy` forced true: the box took 100 characters of the last sent message and
  the toast was the success line — the old refusal is gone, and `assertNotIn("DATA.busy")` now pins
  that.

Two findings came from running things rather than reading them. `.casefold()` — a Python method — sat
in the new JavaScript filter, so the model sheet threw on open and nothing appeared: `node --check`
does not execute a line, and the page caught it on the first click. And `tests.test_controller`
**on its own** failed an assertion the full suite had been passing all day: `report_run` wrapped its
summary in `**…**` and fenced the failure sample in backticks into a tool row that is escaped, never
rendered. It had been green only while the chained run lost the race against the assertion; the fix
is one plain-text sentence, and the flake is now recorded as a fixed defect rather than left to
return.

Also recorded because it will be re-asked: `--fake` handled 22 of the 42 actions the page posts, and
fell off the end of its dispatch for the rest — a silent no-op in the only window designs get
reviewed in. That is a test now, in both directions, and the scripted window answers all 42: ten
navigation and picker gestures say *"Preview only: …"* instead of pretending.

Still not validated, unchanged: the Tk fallback window (its own model combobox was never affected by
the destructive filter and stays as it was), the Docker worker (absent, fails closed), live
OpenRouter requests (no key), the phantom `self.status` (~60 writes, read only by tests — UI 3.4 made
it a rendered surface later the same day), and the
client's own English toasts — *Copied to clipboard* among them — which the server's language rule
covers only for text the server writes.

## UI 3.2 — 2026-09-26 (the message queue)

`python -m unittest discover -s tests`: **586 tests pass**, offline, synthetic model, no display.
Per module: `test_agent` 74, `test_chat` 13, `test_controller` 125, `test_git` 41, `test_gui` 38,
`test_labels` 23, `test_memory` 10, `test_planbook` 19, `test_redaction` 6, `test_repair` 16,
`test_runner` 41, `test_symbols` 54, `test_verification` 35, `test_webapp` 51, `test_workspace` 40.
About 208 s when first measured, **438 s on the confirming re-run a couple of hours later** — the same
machine-wide drift recorded under UI 3.1, again checked case by case before being believed: nothing
waits on a timeout, the cost is 38 withdrawn Tk windows and 125 real `Controller` constructions. The
two figures are both kept because a single wall-clock number from this box has not been reproducible
all day.

`node --check` clean. `tools/check_contrast.py`: **6 combinations, 126 pairs measured, PASS** — the
pair count did not move, because the strip reuses ink and faint on panel and field, and one warn-ink
on warn-bg pair, all of which the audit already covered. No new colour was introduced on trust.

The strip was read back from the running `--fake` window, structurally, since no screenshot was
available at this viewport:

- Both scripted rows rendered with the server's own sentences — *"runs when the current task ends"*,
  *"next, in its own chat"* for the detached one, and *"1 waiting in another chat — open it to see
  them"* — each row carrying four buttons.
- Clicking them produced exactly `queue_edit`, `queue_now`, `queue_chat`, `queue_drop`, in that order
  and one request each; **✎ Save** changed the row's text in place, **▶** moved it to the front, **↗**
  flipped the row's glyph to the detached wording and put it first, **✕** removed it.
- While a task ran: send label `Queue`, `disabled: false`, placeholder *"Ask the next thing — it queues
  until this task ends."*, and **Enter** produced exactly `["queue_add"]` and cleared the box. Back at
  idle the label returned to `Send`. Nothing client-side guesses the busy state — it follows
  `DATA.busy`.

Two test premises were disproved rather than coded around, which is why 15 new `QueueTests` cases
assert on observables instead of call counts. Prompt counting was wrong because one task can make more
than one model call — measured with a throwaway probe: the first queued task produced 1 prompt, the
second 2. And `session["task"]` after a detached item was `None` because a new project chat opens in
Chat mode and a prose answer creates no session: correct product behaviour, wrong assertion.

**The safety fact worth recording is a decision, not a defect.** The plan recommended that a queued
line wait for a click when the running task ends; the user chose **run it automatically**. A message
queued in a folder with **⚡ Auto-Apply** on therefore writes to disk minutes later with no click at
that moment — the approval being the click that queued it. Measured guardrails around that: **Stop**
sets the held flag and the queue does not resume until `queue_resume` or a per-row **▶**; items only
drain for the `chat_id` they were typed in; the queue is in memory only, so nothing re-fires after a
restart; and the folder's other gates (empty-file proposal, `PARTIAL_APPLY` folder, bound-chat mode
refusal) are untouched by the queue path, which calls the ordinary `start_plan`.

Still not validated, unchanged from UI 3.1: the Tk fallback window knows nothing about the queue (no
composer strip there, and it stays English-only), the Docker worker (absent, fails closed), live
OpenRouter requests (no key), and item 3 of the first bugfix specification, "Model Response
Degradation", which arrived without its body. The phantom `self.status` is still there; this round
sent the first real `toast` event through that surface without repairing the sixty writes — UI 3.4,
later the same day, is the round that did.

## UI 3.1 — 2026-09-26 (language-matched notices, file chips, rail preview)

`python -m unittest discover -s tests`: **565 tests pass**, offline, synthetic model, no display.
Per module: `test_agent` 74, `test_chat` 13, `test_controller` 110, `test_git` 41, `test_gui` 38,
`test_labels` 21 (new), `test_memory` 10, `test_planbook` 19, `test_redaction` 6, `test_repair` 16,
`test_runner` 41, `test_symbols` 54, `test_verification` 35, `test_webapp` 47, `test_workspace` 40.
155–175 s on this machine. Timed case by case, the longest single test is 5.3 s, so the wall clock is
volume and not anything waiting: 38 withdrawn Tk windows and 110 real `Controller` constructions
account for 147 s of it. That measurement matters here because the same 38 Tk cases took 43.7 s
earlier the same day and 74 s now — the machine got uniformly slower, the suite did not regress.

`node --check` clean. `tools/check_contrast.py`: **6 combinations, 126 pairs measured, PASS**, up
from 108 because the chips introduced three pairs the audit did not cover (`ink-2` on `field`,
`faint` on `field`, `ink-2` on `panel`) rather than three colours trusted to look right: 9.30, 4.88
and 9.97, all clearing AA in all six families.

Front-end claims were read back from the running `--fake` window:

- Three chips rendered under the last tool note — `UserService.java ~ Modified`,
  `RegisterController.java ~ Modified`, `DuplicateEmailException.java + Created` — each with `↗ View`.
- The window was 478 px wide, so the rail was genuinely `display:none`, and the first chip click
  fell back as designed: view `review`, `selected: 2`, row `DuplicateEmailException.java`, and the
  rail preview correctly *not* opened (`railFile` stayed `-1`).
- With the rail forced visible, the same click opened it: badge `A`, `+7 −0`, the note
  "`+ Created by this task · proposed, not written`", tabs `Diff* | Now | Was`, and diff rows
  `hunk|hunk|hunk|add|1|+package com.demo.users;` — line numbers counted from `@@`, colour on the
  added rows. **Now** rendered the file numbered from 1; **Was** on a created file was blank, so it
  now says "This file did not exist before this task." **✕ Close preview** returned the artifact card.
- Composer placeholder in the three real states: chat `Ask about demo2 — it answers in prose and
  writes nothing`, change+switch-off `Nothing is written until you click Apply`, change+switch-on
  `This folder writes itself, then runs your command`, and back to the off wording after toggling.
- Keyboard, measured by what the page did and by which requests it sent: `Ctrl+K` while the prompt
  had focus opened nothing; `Ctrl+Alt+7` (the AltGr key that types `÷`) opened nothing; `Ctrl+B`
  while typing left the sidebar alone; `Ctrl+K` with nothing focused opened the palette; and
  `Ctrl+Shift+K` sent exactly `new_chat`. The first cut of that handler had a bug the unit tests
  could not see and the page showed at once: `Ctrl+Shift+K` also matched the plain `Ctrl+K` branch,
  so it opened the palette *and* started a chat. Fixed with `!e.shiftKey` guards and re-measured.
- Arabic: the notice the server's own `write_notice(arabic=True, …)` produces was rendered as a tool
  row and measured `direction: rtl` on the block while an English row in the same thread stayed
  `ltr`, with the file-name spans `ltr` + `unicode-bidi: isolate` in both. This is how the bug in
  `dir="auto"` was found: the row's first strong run is its author label "Tool", so an Arabic
  notice could never have resolved to right-to-left on its own.

- "↻ Again" (asked for after this section was first written) was measured the same way, by clicking
  it and by counting what the page sent: with an empty box the click produced exactly one request —
  `set_draft` — and left the last message in the box focused; **Enter** then sent it with byte-identical
  text (`matchesOriginal: true`). With half a sentence typed, the typed text survived untouched and
  the refusal toast named the reason; with `DATA.busy` set, the box stayed empty. The pill is absent
  when no user row exists and when the only user row is whitespace.

Still not validated, and stated so because it is load-bearing: **the Tk fallback window is
English-only.** `labels.state_label` can now answer in Arabic, but `gui.py` reads `STATES` directly
and its ~40 status literals were left alone. The web window is where the language rule applies.

**A defect found and deliberately not fixed this round:** `self.status` in `webapp/controller.py`
is written in about sixty places and read nowhere. It is not in `snapshot()`, and the client only
renders `{"kind": "status"}` events, which `run_job` and `_progress` emit for in-flight job labels.
So every guard and outcome sentence assigned to it — "Choose a project first", the bound-chat
refusal, "Task changes rolled back" — is visible to `tests/test_controller.py` and invisible to the
user. Twenty-five assertions read `controller.status`, which is why it survives. Localising those
strings would have been dead work, which is why `labels.py` carries only the sentences the window
actually draws (`write_notice`, `applied_note`, `artifact_card`, `checkpoint_note`) and the three
`status_*` helpers drafted for them were deleted. The fix is its own round: make the write reach a
surface — the `{"kind":"toast"}` event the client already handles — and then translate what lands.

**That round came the same day.** UI 3.4 made `status` a snapshot field drawn in the strip under the
header, so the ~60 writes reach the user; the guard sentences still need reading for whether they are
worded to be read. See the UI 3.4 section at the top of this file.

## Review round — 2026-09-26 (read the whole project, then fix what it said)

`python -m unittest discover -s tests`: **525 tests pass** offline in ~100 s, with synthetic model
responses and no display required. Per module: `test_agent` 74, `test_chat` 13, `test_controller` 106,
`test_git` 41, `test_gui` 38, `test_memory` 10, `test_planbook` 19, `test_redaction` 6, `test_repair` 16,
`test_runner` 41, `test_symbols` 54, `test_verification` 35, `test_webapp` 32, `test_workspace` 40. Two
new modules came out of the review: `tests/test_workspace.py` (path policy and the write gate) and
`tests/test_verification.py` (snapshot, fingerprints, the fail-closed rules).

The wall clock was measured rather than assumed. Timed test by test, the longest single case is 2.6 s
and no case waits on a real timeout — the cost is 38 withdrawn Tk windows in `test_gui` and 106 genuine
`Controller` instances in `test_controller`. That check exists because a fix during this round hung the
suite for 60 s per run (closing the runner's output wrapper while the reader thread held the buffer's
lock); the hang is gone and `tests/test_runner.py` pins the order that fixed it.

`node --check src/ai_code_engineer/webapp/static/app.js` clean. `tools/check_contrast.py`: **6
combinations, 108 pairs measured, PASS**, tightest pair `claude` dark's shortcut chip — after ten dead
selectors were deleted from `app.css`, which is the only reason the pair count moved.

Front-end behaviour was read back from the running `--fake` window, not reasoned about from source:

- Project nodes are collapsed on load — `["demo2:closed", "demo_repo:closed"]` — one click opens, the
  next closes. Before this round the second click did nothing to the project in front of the window.
- Typing `hashing` in the search box expanded only the project holding that chat, and clearing the box
  restored the collapsed state the user had chosen.
- Empty **Send** reaches the server as a POST in step-by-step mode and does nothing otherwise. It had
  been reading `DATA.chained`, a field neither controller sends, so the gesture was dead in both builds.
- The Project drawer renders against `--fake` (it threw before: `project_info` existed in `server.py`
  and in neither controller). Field parity between the preview snapshot and the real one is a test now.
- The mode badge, git chip and auto-apply pill were read from the page for a bound chat: no chevron,
  no pill, and the refusal sentence appears when a bound chat is asked to change mode.

Still not validated here: the Docker worker (absent on this machine, fails closed), live OpenRouter
requests (no key configured), any claim that a green report proves the project's tests are *correct*,
and — recorded as an open question rather than a pass — item 3 of the first bugfix specification,
"Model Response Degradation", which arrived without its body and was never reproduced or fixed.

## UI 2.7 — 2026-09-24 (web presentation layer)

`python -m unittest discover -s tests`: **192 tests passed** in ~50 s, offline, with synthetic model responses and no display required. New coverage: `tests/test_controller.py` (22 orchestration cases against the headless controller — plan → apply → passing run, declining apply writes nothing, rollback, the cloud-consent gate, commands locked until applied, the unverified-prior disclosure, `PARTIAL_APPLY` needing an explicit yes, notes reaching the model, the switch-project draft warning, plain chat never proposing, a plan step closing only on report proof, preferences surviving a restart, stop cancelling, a folder grant, refusal on a non-empty folder, and the API key never appearing on disk) and `tests/test_webapp.py` (15 cases against a real loopback listener — `/api/*` refused with no token and with a wrong one, POST refused, traversal 404s for `..%2f..%2fserver.py` and `%2e%2e/%2e%2e/server.py`, assets served tokenless, non-JSON and oversized bodies answered 400, unknown action answered 500, and a `confirm` event verifiably holding the worker thread until `/api/confirm` wakes it). The 36 cases in `tests/test_gui.py` still pass unchanged — 34 of them driving withdrawn Tk windows — which is the point: the fallback window was not rewritten.

Seven defects were found only by driving the real page, and each has a test or a screenshot:

- A server-pushed draft never reached the composer (the client treated it as an echo of its own keystroke). Verified live: after `send('example')` the textarea held `Fix the add function in calculator.py so it adds the numbers inste…`.
- Diff line numbers counted array positions, so the `@@` header consumed one and every row after it drifted. Verified live: `·|--- a/calculator.py`, `·|+++ b/calculator.py`, `·|@@ -1,2 +1,2 @@`, `1| def add(a, b):`, `2|-    return a - b`, `2|+    return a + b`.
- `demo2` and `demo_repo` rendered the same avatar. Now `["d2","dr","pp","te","te"]`.
- The header subtitle went stale on a model switch. Now `demo_repo · deepseek-coder:latest`.
- "New project folder…" could not create a folder. The modal now offers a name field and a **Create** button; verified with `role="dialog"` present, buttons `["Cancel","Create my-new-thing","Choose here"]`, and Cancel releasing the blocked server thread.
- A repeated no-progress stop blamed the model. It usually means the change is already in the files — confirmed by dumping the message array for a stalled turn: the tool observation was `def add(a, b):\n    return a + b`, because an earlier session had applied that fix and never rolled it back, leaving the task with nothing to do at `temperature: 0`.
- `.pill.tag` inherited the file-badge width and clipped `Ollama` to `Olla`.

Contrast was measured, not judged. `.design-preview/contrast.py` parses `tokens.css` and computes WCAG ratios for 11 token pairs across all 6 style/theme combinations (66 measurements): **4 failed**, all in light themes — `--faint` on `--bg` in every family (2.98, 3.19, 3.14) and `--accent` on `--bg` in `linear` (3.68). After the fix the audit reports **0 failing pairs**, and the live page computed `faint` as `rgb(107,102,92)`.

Role and live-region attributes were read back from the running page: `segRoles: "tablist/tab"`, `toastLive: "polite"`, three labelled `tabpanel` views, and dialogs that trap Tab, honour Escape and restore focus.

Screenshots were taken with headless Chrome at `--window-size=1440,900 --force-device-scale-factor=1.5`, because the MCP browser viewport (534×560) cannot represent the design being reviewed. Style and theme are applied by a boot script before first paint — mid-animation captures otherwise showed the previous theme's colours.

Still not validated here: the Docker worker (absent, fails closed), live OpenRouter requests (no key), and any claim that a green report proves the tests are *correct*. The style family (`claude`/`codex`/`linear`) is a user decision and remains unchosen; the two unused families stay in `tokens.css` until it is made.

## UI 2.6 — 2026-09-24 (proof, plan ledger, project notes, symbol index)

`python -m unittest discover -s tests`: **155 tests passed**, offline, with synthetic model responses and withdrawn Tk windows. New coverage: `tests/test_planbook.py` (15 ledger/step cases), `tests/test_memory.py` (10 store and path-policy cases), `tests/test_symbols.py` (18 index cases), plus engine cases for `plan_step` and note integrity and GUI-thread cases for send → verify → advance → rollback and the notes editor.

The index was also run against a real project rather than fixtures: `sandbox/spring-auth` renders with the package, every type, all eight test methods across the three test classes, and the four internal imports of `AuthController` — while `java.util.List` and a `class` inside a comment stay out.

One live turn through that project with `qwen2.5-coder:1.5b` over loopback (draft only; nothing was applied) confirmed the notes arrive on the session and the map arrives in the prompt. The run ended `BLOCKED` when the model overflowed its 4,096-token output while writing a whole new Java class — the capability ceiling already recorded in `MODEL-BENCHMARK.md`, not an indexing or policy failure.

The window was driven for real, not only in tests: a three-step plan with a scripted model produced the `step 1/3` chip, the Settings progress line and ledger rows; the Settings **Project notes (memory)** editor was screenshotted showing the saved-state line and the note file written to `<app data>/.agent-memory/` outside the project folder.

Still not validated here: live OpenRouter requests (no key), the Docker worker (absent, fails closed), and any claim that a green report proves the tests are *correct* — reports prove the tests ran.

## Desktop update — 2026-09-23

`python -B -m unittest discover -s tests -v`: **36 tests passed**, including eight new desktop/cancellation/key-handling checks. Tk windows were created withdrawn for the tests; no real model or external API was called by the GUI tests.

The desktop workflow was exercised through its actual handlers: plan → inspect diff → reject apply before approval → approve/apply → static verification → guarded rollback. Additional tests cover resetting approval on reopening, rejecting a changed proposal after review, cloud opt-in, responsive background work, and cancellation before a proposal can be saved.

The earlier live Ollama smoke test remains the model integration evidence. Live OpenRouter and Docker validation remain pending as described below.

Date: 2026-09-22. Host: Windows, Python 3.11.1.

## Automated checks

- `python -m unittest discover -s tests -v`: 28 tests, covering provider contracts, invalid actions, cloud opt-in, path escape, approval matching, concurrent-edit protection, guarded rollback, and verification fail-closed behavior.
- `python agent.py demo`: deterministic synthetic proposal → apply → static verification → rollback passed. This demo does not call a model or run project code.
- Module compilation and CLI help checked.

## Live local model smoke test

Provider: Ollama. Model: `qwen3:4b`, already installed on the machine. No new model downloaded.

Task: fix the synthetic `calculator.py` so `add(a, b)` returns `a + b` instead of `a - b`.

The model produced the correct one-file diff. The proposed full content and target were independently checked before applying. Apply, Python syntax check, and guarded rollback succeeded. The sample was restored to its original deliberately broken state for future demonstrations.

Earlier attempts with `qwen2.5-coder:1.5b` and `granite4.2:3b` did not complete the action protocol reliably. The final implementation adds explicit-file context, repeated-action detection, and Python/JSON syntax rejection before approval. The successful small smoke test is not a benchmark of Java/Spring coding quality.

## Not validated on this machine

- OpenRouter live requests: no `OPENROUTER_API_KEY` was present. Contract tests use mocked responses; no bank code or credentials were sent.
- Docker verification worker: Docker was absent. Missing-worker behavior was tested to block execution, but real container isolation/build/test execution has not been validated.
- The example project's tests were not executed by the agent. Static validation is explicitly reported as **unverified**, not as test success.
- Production policies, multi-user isolation, Java/Spring task evaluations and the remaining roadmap are not complete.
