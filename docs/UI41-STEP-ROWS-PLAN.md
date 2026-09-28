# UI 4.1 — step rows: what it is doing, and the details behind each one

**Shipped 2026-09-28.** The five open decisions were answered "نفذ", which in this project means the
recommendations stood: rows in the chat thread, detail fetched on demand, one row per action with no
grouping, a persisted `step` event per action, and streamed build output rendered inside the live row.
See `IMPLEMENTATION-STATUS.md` for what landed where.

Asked on 2026-09-28, with a screenshot of another tool's transcript (rows like `Running command ⌄`,
`Ran command`, `Created integrate_watches.py`, `Read Computer Use skill`):

> "عايز اعمل زي دي جوه التول يكتب كدا ايه شغال وبيعمل ايه ولو في تفاصيل هيكون جميل افتحها ابص عليها
> او اعملها كولابس"

So: a row per action, a live row for what is running now, and a disclosure that opens the details of
each row — command output, paths, counts — instead of a flat sentence.

Baseline: **805 offline tests green**, `node --check` clean.

---

## What already exists — most of this is a presentation job, not a data job

| piece | where | what it already gives |
| --- | --- | --- |
| the step sentence | `labels.step_line(arabic, action, …)` `labels.py:349` | server-built EN/AR line **with its glyph** for `read_file` 📖, `search_code` 🔍, `list_files` 📁, `propose` ✏️, `blocked` ⛔, `applied` 💾, `executing` ⏳ + a `⚙️ <action>` fallback so a new tool never vanishes silently |
| who announces them | `engine.announce()` `engine.py:585-589` | calls `progress(line)` **and** `step(line)` for every action — the vocabulary is already one call site |
| the live row | `controller._step()` `controller.py:1512-1527` | `_add("tool", "Steps", line)` into the chat + moves the status strip; consecutive repeats collapse on purpose |
| structured per-action record | `engine.event(session, kind, **values)` `engine.py:265` | persisted on `session["events"]`: `tool name=… path=… sha256=…`, `tool name=… matches=N`, `tool name=… count=N`, `proposal hash=…`, `written path sha256`, `removed path sha256`, `run recipe status exit_code seconds`, `verification status`, `rejected_action reason`, `stopped reason`, `auto_read`, `context_file`, `plan_attached`, `memory_attached`, `blocked_retried`, `approved` — **21 kinds** |
| the detail a run row needs | `repair.record_run` `repair.py:96-109` | `session["runs"][]` keeps `command`, `exit_code`, `seconds`, `status`, `proof`, `truncated`, `timed_out`, **`failures` (12, redacted)** and **`tail` (2 500 chars, redacted)** — real output, on disk, today |
| streamed build lines | `controller._build_line` `:1529-1541` → `log_chunk` → client `LIVE` (`app.js:1245-1252`, cap 300) | live output exists, but it is client-only and cleared when the next job starts |
| a disclosure pattern to copy | sidebar `state.expanded` (`app.js:34`, `test_webapp.py:173-176`), `pv-tabs`/`refreshPreview`, `rail-files` rows | collapse state is already a keyed set held in `state`, and the rail already turns a path into a clickable row that opens the viewer |

## What is missing — the three real gaps

1. **The row is a sentence, so it cannot be opened.** `messages[]` is `{role, author, text, time}`
   (`controller.py:683-686`). The structured twin exists in `session["events"]`, but nothing links a row
   to its event, and the link is what an expander needs.
2. **Reopening a session loses the whole narrative.** `display_session` rebuilds `messages` from the task
   and the summary only (`controller.py:2375-2385`) and rebuilds `log` from events as `k=v` strings
   (`:2366-2368`). Every 📖/⏳/ row that was on screen during the run is gone afterwards — which is the
   opposite of the standing ask "أشوف الشات والهيستوري بتاع المشروع كامل".
3. **`snapshot()["log"]` is unbounded and rides every push** (`self.log.append` at `:678`, shipped at
   `:774`, no cap anywhere). A step list built on top of it would multiply that; a 40-turn task already
   pushes thousands of log entries into every later snapshot.

## Build

1. **Give a step an identity, and persist it.** `announce()` already knows `action` + fields; pass them
   through: `_step(line, action=…, **fields)` → `_add("tool", "Steps", line, step={…})` **and**
   `event(session, "step", action=…, **fields)` so the record survives in `session.json` next to the other
   21 kinds. Message entries grow `id` and `action`; nothing else about a message changes, so every current
   reader (chips, copy button, `ARABIC_RUN` direction) is untouched.
2. **Draw the row as a row.** `renderThread` keeps a `step` message in the `.msg.sys` family but builds it
   from `{glyph, text, chevron}` instead of a bubble: the glyph and sentence come from the server, the
   chevron is the client's only contribution. A row with no detail renders **without** a chevron — an
   expander that opens to nothing is worse than no expander.
3. **Detail on demand, from data that already exists.** A new `step_detail` action answers with the block
   for one step id: for a run, its `session["runs"][i]` row (command, exit, seconds, failures, tail,
   truncated/timed-out note); for a file action, path + sha + `+add/−del` from `review.files`; for a
   proposal, the file list with each path clickable into the side viewer (`openFile(i)`, which UI 4.0 made
   the one way to read a change). Detail is **not** shipped in the snapshot — a run tail is 2.5 KB, and N of
   them per task is the payload this project already refused for streamed output (`app.js:1242-1244`).
4. **The running row.** While `busy`, the last step row is the live one: `⏳ Running mvn -B test` with the
   spinner, and if it is expanded the `LIVE` chunks render inside it (capped as today) instead of only in
   Activity. When the job ends the row settles into its final sentence and its detail becomes the stored
   run — the same row, now backed by disk rather than by the stream.
5. **Cap what travels.** `snapshot()["steps"]` (or the message list's step tail) keeps the last 60 step
   rows and a server-built "… N earlier steps" line; `self.log` gets a cap in the same place, with the
   dropped count said out loud rather than silently trimmed. This is the piece that keeps 4 from being a
   regression.
6. **History shows what live showed.** `display_session` builds step rows from `events` where
   `kind == "step"`, so reopening a session from the sidebar gives back the 📖/⏳/💾 narrative and its
   details. Sessions recorded before this change have no `step` events: they render their existing rows and
   their `written`/`run`/`proposal` events, which is already more than today's task+summary pair.
7. **Collapse state.** `state.openSteps = new Set()`, keyed by step id, like `state.expanded` for the tree.
   Not persisted: a reload that opens everything would defeat the point.

## Invariants

- Sentences stay server-side (`labels.py`), glyphs too — the client adds only the chevron and the box. This
  is the rule every round has had to re-undo when broken ([[status-is-a-surface]]).
- `snapshot()["review"]`, `changeActions()`, `railPreviewCard()` and `openFile()` are untouched: a proposal
  row's detail **calls** the viewer, it does not rebuild it.
- Nothing new is written to disk per step beyond one `step` event inside the session that already exists.
- Redaction stays where it is: `record_run` redacts `failures`/`tail`, `_build_line` redacts each streamed
  line, and `step_detail` answers from those already-redacted fields.
- No auto-scroll surprise: an expanded row must not shove the thread while a task streams.
- Tk stays as it is (its Changes tab is already its own open item, #28).

## Tests

- Server: a `step` event is recorded with `action` + fields and survives a save/load round-trip;
  `step_detail` answers a run id with command/exit/seconds/tail and refuses an unknown id; the step tail is
  capped and the dropped count is stated; `display_session` rebuilds step rows from events.
- Client shape (this suite asserts source text, since no Python test runs a DOM): a step row is built from
  `{glyph, text}` and never re-assembles a sentence; the chevron appears only when detail exists; the live
  row renders `LIVE` inside itself; `state.openSteps` toggles and never persists.
- `node --check`; full `discover` and each touched module alone. Baseline **805 → ~815**.

---

## Open decisions

1. **Where do the rows live?** Recommended: **the chat thread** — that is where `_step` already puts them,
   and "what is it doing now" belongs beside the conversation. Activity keeps the raw log for grepping. The
   alternative (Activity only) leaves the thread as prose and moves the question to another tab.
2. **Detail on demand or in the snapshot?** Recommended: **on demand** (`step_detail`), because a run tail is
   2.5 KB and the project already refused shipping streamed output in every snapshot. In the snapshot is
   simpler and one less action, at the cost of the payload.
3. **Group or not?** Recommended: **one row per action, no grouping** — the screenshot is one row per action,
   and `_step` already collapses exact repeats. A "3 steps" card reads tidier and hides the thing the user
   asked to see.
4. **Persist a `step` event per action** (recommended — it is what makes history complete, and it is one
   append to a list that already holds 21 kinds) or keep steps as live-only UI and accept that a reopened
   session shows only task + summary?
5. **Streaming output inside the expanded live row** (recommended — it is the "افتحها أبص عليها" moment for
   a build) or keep live output in Activity only and let the row expand to the stored tail after the run?

---

## As built — three things the plan could not have known

**The proposal's own row was being thrown away.** `engine.plan` saved the session and *then* announced the
proposal, so the `step` event for the most important row in the thread was appended after the last write and
never reached disk. A reopened task would have lost exactly the row that says what was offered. The save moved
after the announcement, and `test_a_step_is_recorded_so_the_row_survives_reopening_the_task` pins the
difference between the record (four events, including the repeated read the loop really did take) and the
thread (three rows, because `_step` collapses a repeat) — the rebuild now collapses the same way, or history
would show a step nobody saw.

**`.step` was already taken.** The first live probe asked for `.steprow .st-txt` and found nothing, because
the row was written with class `step` — which the plan card in the rail has used for two rounds (`.step.done`,
`.step.now`, `.step i`). Two rules for one name, each wearing the other's layout. The row is `.steprow` now,
and a test asserts `.step {` appears exactly once in the stylesheet.

**A row's tense had to change with it.** `run_tests` announces `⏳ Executing: <argv>` before the child starts —
that sentence is the point (the project's own build runs code the repository defines). Left standing after the
build it describes a moment that has passed, so `_settle_run_step` rewrites that same row into
`⚙️ Ran <argv> — failed · exit 1 · 7.1s` and flips its `detail` flag, which is what gives it a chevron. One
existing test asserted the present tense and was rewritten deliberately, with the reason in its docstring.

Verified live, read-only, on the real folder: a session reopened from history carried its run row
(`⚙️ Ran mvn -B test — failed · exit 1 · 7.1s`), and opening it fetched the stored command, verdict, the two
redacted `[ERROR]` lines and the tail of the actual Maven output. In the scripted window the same rows show
four named sections, a proposal row whose file list opens the side viewer, and a row with no digest drawn
without a chevron. The live branch was exercised by marking a row `executing` under `DATA.busy`: it opens
without a round trip, streams the chunks into itself, and closes when the job ends.
