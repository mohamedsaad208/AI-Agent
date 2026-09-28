# UI 3.4 plan — live step reporting in the chat, a status line that survives, apply/run sentences

> **Built the same day** (2026-09-26) on the recommendations, with three deviations from the
> specification's letter recorded in `docs/IMPLEMENTATION-STATUS.md` (UI 3.4): the activity strip is
> the existing bar under the header rather than a new one at the bottom, "formatted" snippets are
> ` · `-separated plain text because tool rows are escaped, and `⛔ Blocked` prefixes the failure row
> that already states the reason instead of adding a second one. 603 → 624 tests; measurements in
> `docs/VALIDATION.md`. The line numbers below were the baseline's.

Written 2026-09-26 from the English specification: (1) clean human-readable step messages emitted into
the chat as the agent acts (`read_file`, `search_code`, `list_files`, `propose`, `blocked`), (2) a
status bar kept up to date with the real-time active state, (3) sentences for Apply / Executing /
Build passed / Build failed with formatted error snippets.

Baseline: 603 tests green. Every claim below was checked in the tree, and three of them were measured
by running the page or reading the stored sessions — the file:line is given for each.

## What already exists, per item

**The channel exists and the CLI/Tk already use it.** `engine.plan(..., progress=print)`
(`engine.py:384-387`) is the one stream: `:417` attached plan, `:493` `"Turn 3/12: asking <model>..."`,
`:532` file-not-found, `:568` blocked-retried, `:592` auto-read. The web wires it to
`_progress` (`controller.py:1254`), which does three things at once (`:1278-1281`): sets
`self.pending` (the animated line inside the chat's pending bubble), writes the Activity log via
`_note("progress", line)`, and emits `{"kind": "status"}`. The Tk window handles `kind == "progress"`
into its own status label (`gui.py:881`), and the CLI prints it. **So item 1 is not a missing channel;
it is missing calls plus a missing destination.**

**The three actions the spec names produce no line at all.** A successful `list_files`
(`engine.py:510`), `read_file` (`:538`) and `search_code` (`:544`) each record a session *event* and
never call `progress`, so those turns are silent in the window. `propose` (`:557`) does not either —
its result reaches the chat as the file-chip card and the artifact card instead. `blocked` is the
weakest: the web controller contains no "blocked" string at all (verified by grep); a run that ends
blocked leaves `self.status = "No applicable proposal was created. See the conversation for details."`
(`:1270`) — which is the phantom surface, invisible to the user.

**How many chat rows a step-per-action design actually costs — measured, not assumed.** Across the
39 stored sessions in `.agent-runs`: **24 have zero tool events, median 0, maximum 5**, 34 tool events
in total (13 `read_file`, 3 `list_files`, the rest search). Small local models fail the protocol long
before they make twenty calls (`rejected_action` appears 44 times, `blocked_retried` 23). So the
spec's literal shape — one chat row per step — adds about 0-5 rows per task, up to roughly 12 + fix
rounds on a capable model. Flooding is not the risk here; the risk is the *ratio* of machine-shaped
rows to human ones.

**Item 2's status line is written into the wrong element and erased mid-run.** `app.js:80` handles the
`status` event with `$('title').nextElementSibling.textContent = msg.text` — the element after `#title`
is `#subtitle`, the header line that normally reads `demo2 · step 2/5 · qwen2.5-coder:1.5b`. Measured
in the running page:

- `{kind:'status', text:'PROBE-LIVE-STATUS'}` → `#subtitle` becomes the probe text.
- one ordinary `state` push afterwards → `#subtitle` is restored from `DATA.header.subtitle`
  (`app.js:437`) and the live line is **gone, while the job is still running** — even though the
  client kept it in `state.status`, which nothing ever reads.
- `#busybar` (`index.html:77`) is the only real status strip and it has no text at all: measured
  `textContent: ""`, computed `height: 2px`, styled at `app.css:654` as an animated gradient shimmer.
  `app.js:1122` only toggles its `hidden` class.

So item 2 needs a *surface* (and a rule for what happens when the job ends), not a source.

**Item 3: two of the four sentences exist, one of them where the spec wants it.**

- Success/failure of a run: `report_run` (`:1576+`) already adds a tool row —
  `✅ Maven test: passed (41 tests, 0 failed, 0 errors (JUnit XML), 41.2s)` — because
  `runner.summarize` (`runner.py:483-492`) already builds the "(<details>)" the spec asks for, and the
  failure line carries `— command: …` plus `· Errors: …`/`· Output: …`. Reworded to plain text *this
  morning* (it used to be markdown, which the thread escapes).
- Apply under **⚡ Auto-Apply**: `write_notice(...)` tool row (`:1477`) + the `applied_note` banner.
- **Apply by hand is a genuine hole**: `_applied` (`:1465-1486`) ends with
  `self.status = "Changes applied. You can check syntax or run the project's own command."` and the
  phantom surface means the chat gets nothing at all unless the folder happens to be under git (then a
  checkpoint row from `_checkpoint`). "💾 Applied changes to N files" is missing for exactly the reason
  recorded as an open defect.
- "⚙️ Executing: <command>": the job label `Running Maven test in <folder>…` (`:1574`) is passed to
  `run_job`, which emits it as a `status` event (`:325`) — i.e. into the element that gets clobbered —
  and `_note("job", …)`. The command string itself only appears in the failure row.

**The replayed Activity log is machine text.** Open a saved task and `display_session` (`:1924-1926`)
rebuilds the log from session events as `" ".join(f"{k}={v}")` — so the same step the spec wants
"clean and human-readable" reads `tool name=read_file path=pom.xml sha256=9f3c2…` after a restart.
Any sentence vocabulary introduced here has to serve that path too, or a task says one thing live and
another thing once it has been reopened.

## One security finding the request itself exposes

**The failure snippet the spec asks to put in the chat is the one copy of the log that never goes
through `redact()`.** `run_tests.work()` returns `(repair.record_run(path, result), result)` and
`done()` calls `self.report_run(pair[1])` — the **raw** runner result. `runner.run` builds
`tail`/`failures` straight from the process output (`runner.py:480`; no `redact` anywhere in that
module), and the two existing surfaces scrub independently: `repair.record_run` redacts what is stored
and what the model re-reads (`repair.py:91-92`, `FAILURES_KEPT = 12`, `TAIL_KEPT = 2500`), and
`_build_line` redacts each streamed line (`controller.py:1291`). So a test that prints its own
`DATABASE_URL=…` is scrubbed in the session, scrubbed in Activity — and lands in the chat thread
verbatim, from which the copy button puts it on the clipboard. Fix at the display boundary *before*
widening it: this is the project's own rule ("credential-shaped text is removed before it is stored or
sent back to the model"), and the chat is a surface the rule was never applied to.

## Three standing rules this feature has to keep

- **Plain text in tool rows.** The thread escapes and never renders markdown (`renderThread`, and
  `tests/test_controller.py` pins it), so "formatted" here means structure with separators and one
  line per item, not `**` or fences. Measured the same way this morning.
- **Arabic twins, written by the server.** These are our sentences, so they belong in `labels.py` with
  `say(arabic, en=…, ar=…)` like the apply/checkpoint/queue families; emoji, paths, commands and model
  names stay Latin in both languages. The spec's five strings are English-only as written.
- **Redaction and the environment rule** for anything derived from command output (see above), and no
  new subprocess surface.

## Shape

- `labels.py`: one `step_line(arabic, kind, **fields)` covering `reading / searching / scanning /
  proposed / blocked` plus `applied_line(count)` and `executing_line(command)`, and reuse it for the
  replayed log so live and reopened wording match. `engine` calls `progress()` for every action it
  performs; the controller decides where each line lands (chat row + status surface + log).
  `plan()` keeps `progress=print`, so the CLI and Tk gain the same lines with no new coupling, and
  `contract.py` stays five methods.
- Chat rows go in as `tool` messages, which is what makes them plain-text pills that already carry a
  copy button (UI 3.3) and take RTL from their own text (UI 3.1).
- A real status surface: stop writing `status` into `#subtitle`; draw the live line in the strip under
  the header (or a line above the composer), keep `state.status` as the value `render()` re-paints so
  a state push cannot erase it, and clear it when the job ends — with the idle state reading like the
  header does today, not like an empty box.
- `_applied` adds its sentence to the chat on the manual path as well as the automatic one.

## What will break

- `tests/test_webapp.py`'s `DATA.` field-parity and top-level-key parity tests: a new snapshot field
  (`status`/`activity`) must arrive in `controller.py` **and** `fake.py` in the same commit — this is
  the class of drift that was a test failure waiting to happen for a week last round.
- The action-parity test added today: new scripted-window handlers must keep up.
- Every existing assertion on `controller.status` keeps passing (the string stays the source), but any
  test that counts messages per task will shift once steps become chat rows — measured before
  changing, not after.
- The queue: a step row is not a user message, so `queue_add`'s dedupe and the `↻ Again`/last-asked
  logic must keep reading only `role === 'user'` rows.

## Decisions

**A. Do the step lines go in the chat as their own rows?** The spec says yes and the measurement says
it is affordable (0-5 rows per task on this machine's models, worst case ~12). Alternatives: a single
"Steps" card per task that grows in place under the user's message (keeps the thread prose-shaped), or
steps in Activity + the status line only (no chat change at all). Recommendation: **the spec's
shape**, with two refinements — consecutive identical lines collapse (models re-read the same file),
and they are ordinary plain-text tool rows so copy/RTL come free.

**B. Does the phantom `self.status` become visible in this same round?** Roughly sixty writes —
"Choose a project first", the bound-chat refusal, "Task changes rolled back", "Changes applied…" —
currently reach nobody, and *the sentences this spec asks for live in that pile*. Half-fixing it (a
status strip that only the new lines reach) leaves the same defect with a new name. Recommendation:
**fix it properly**: `status` becomes a snapshot field the client draws in the strip, the ~60 writes
start appearing, and each of them gets checked for wording that was never meant to be read. This
widens the round: the window will start saying things it has never said, and some of those sentences
are written for a developer, not a user.

**C. Wording: adopt the spec's five strings verbatim, or reuse the sentences already on screen?**
Where both would describe the same event — `✅ Applied and saved to disk: N file(s)…` (Auto-Apply) vs
`💾 Applied changes to N files.` (the spec) — two phrasings for one fact is how this project keeps
having to undo drift. Recommendation: keep the spec's emoji vocabulary and reuse the existing sentence
where one exists, so the wording never depends on which code path wrote it.

**D. How much of a failed build goes into the chat?** Recommendation: the same 12 failure lines
`repair.FAILURES_KEPT` already keeps (redacted), then a pointer — "the full output is in Activity",
where the runner already streams redacted lines live. The alternative, the 2 500-character tail, makes
one row dominate a screen of answers, and the raw `tail` is the field with the redaction problem
above.
