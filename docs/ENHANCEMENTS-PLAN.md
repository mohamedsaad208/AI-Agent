# UI 2.9 plan — project drawer, per-language index, more toolchains, live logs, chunk diffs

Status: **implemented** — all ten items, in the order below, 317 offline tests green. Derived from
the user's "Comprehensive Enhancement & Feature Specification" (items 1–10). Each item records what
the code already did before the change, because three of them were largely built and rebuilding them
would have been the real risk; what each section added is written under its own "Shipped" note.

Repo facts this plan depends on:

| Fact | Where |
|---|---|
| Proposal integrity covers the resolved bytes | `engine.proposal_hash` hashes `("id","root","task","summary","checks","changes")`, and each change carries `before/before_hash/after/after_hash` |
| Apply re-verifies before writing anything | `engine.apply_proposal:523-528` prefights every target's `before_hash`, then writes one at a time with `PARTIAL_APPLY` on interruption |
| Commands are constant argv, resolved with `shutil.which`, no shell | `runner.run:241-279`, `ENV_KEYS` allowlist, `MAX_OUTPUT_CHARS` 200 000, `taskkill /F /T` tree kill |
| Progress lines already reach the browser over SSE | `controller._progress` → `{"kind":"log"}` → `app.js applyEvent` |
| The map is rebuilt from disk on every plan | `workspace.repo_map:172-183` reads every policy-visible file, then `symbols.parse` + `symbols.render` (caps: 300 files, 12 000 chars) |
| A chat bound to a project is already a first-class thing | `chat.project`, `bind_chat`, `_chat_context` (UI 2.8) |
| The registry is a flat list of paths | `.agent-projects.json` → `{"projects": [str], "ui": {...}}`, read at `controller.__init__` |

---

## 1. Project Settings & Status drawer

**Already there:** notes editor + saved-state line (`Settings → Notes`), detected recipes and the
current one (`snapshot()["recipes"]`, `recipe`), request timeout, project path in `settings.project`.

**New.** One drawer per project, opened from the sidebar node (⋯ → **Project settings**) and from the
Sources card. Sections:

- **Root** — resolved path, the granted key, and an **Open in Explorer** action (see the safety note).
- **Notes** — the existing `memory_store` editor, moved into the drawer, with its character count and
  `MAX_MEMORY` limit, and the file name it lives in (`<folder>-<hash>.md`, always outside the project).
- **Context budget** — three numbers against `settings.context_chars`:
  - repository map: `len(Workspace(repo).repo_map())` characters;
  - this conversation: characters of the turns `chat._messages` would send (the same selection the
    model actually gets, not the whole history);
  - remaining: `context_chars - map - conversation - system prompt`.
  Displayed as characters with a bar. **Tokens are not shown as a count**: there is no tokenizer in
  the standard library and `dependencies = []` is the project's first rule. The drawer shows
  `≈ N tokens (chars ÷ 4, estimate)` labelled as an estimate, never as a measurement.
- **Toolchain** — detected recipe labels, the selected one, `timeout_for(recipe)` and the request
  timeout, plus whether the recipe writes a JUnit-family report (so "proof" is explainable).

**Cost note.** Building the map to measure it means walking the folder; that happens on the worker
thread inside `run_job`, never on the UI thread, and the result is cached (§7) so the drawer opens
instantly the second time.

**Open in Explorer is a new execution path.** Today nothing outside the recipe allowlist runs. The
proposal: `subprocess.Popen(["explorer.exe", str(root)])` (Windows) / `open` / `xdg-open`, argv-only,
no shell, and **only** for a path already present in `self.projects` — never for a browser-supplied
path, and never for a path the policy would refuse to read. It is a UI convenience that launches a
program; it does not read, write or evaluate project content. Needs the user's explicit yes.

Shipped as `GET /api/project?project=<key>` + the `reveal` action, opened from the sidebar node's ⋯
menu and from the Sources card.

- The budget is measured through `chat.context_use()`, which runs the **same** `_messages()` the
  request will use — so the drawer cannot bill the user for history the builder had already dropped,
  and a conversation that does not read this folder is reported as zero turns rather than borrowed.
- The estimate is shown as `1,232 of 24,000 chars ≈ 308 tokens (chars ÷ 4, estimate)`. The word
  "estimate" is in the sentence, because there is no tokenizer and `dependencies = []` is the rule.
- `reveal` takes the **key**, looks the path up in `self.projects`, and starts nothing when the
  folder is missing; the browser never sends a path. Tests cover the fixed argv, the absence of
  `shell`, an ungranted real path, `../../Windows`, an empty key and a vanished folder.
- The drawer is fetched on open instead of riding in every snapshot, because measuring the map means
  walking the folder — §7's cache makes the second open instant.

## 2. Project icons + "new chat in this folder"

- Registry format becomes `{"projects": [{"path": ..., "key": ..., "icon": "🚀"}], "ui": {...}}`.
  The reader accepts a plain string as `{"path": str, "icon": ""}` — one `isinstance` branch, not a
  compatibility layer, because that file already exists on disk from earlier versions.
- `project_icon(key)` / `set_icon(key, icon)` on the controller; `set_icon` is a new action.
- **Palette**: `PROJECT_ICONS` and `DEFAULT_ICON` live in `controller.py`, because that is where a
  submitted icon is validated. `options()` and `snapshot()` expose them as `icons`, so the sheet
  renders whatever the backend will accept instead of keeping a second list in JavaScript.
- **Picker**: after `browse()`/`new_project()` succeeds, the sheet offers that palette with 📁 as the
  default, and it is changeable later from the node's ⋯ menu. Choosing is skippable and never blocks
  the grant; a restored branch is not asked again.
- **Rendering**: `initials_for()`-based avatar keeps its colour-by-name behaviour, and the icon is
  painted inside it when set. The icon is escaped text, not markup.
- **Quick new chat**: a `＋ chat` button appears on node hover (`opacity` like the existing `time`
  element, and always visible on `:focus-within`) and sends `new_chat_in {project: key}` — a
  *chat* branch bound to that project (`composer: "chat"`, `chat.project = {key, path}`), leaving the
  active branch and every existing chat alone. This is `bind_chat` and `_select_branch` composed, so
  it inherits the "read context, never propose" guarantee.

## 3. Sidebar collapse is reachable and remembered

`app.js:805` and `:820` both do `$('app').classList.toggle('side-collapsed')`, and the only button
that does it lives inside `.side` — so collapsing hides the way back, and the state is lost on
relaunch.

- Add `#sidebar-expand` in the header (`.top`), rendered only when collapsed:
  `.app:not(.side-collapsed) #sidebar-expand { display: none }`. Expanding is a `set_collapsed`
  action so the server owns the state, and it is persisted with the other prefs
  (`ui["side_collapsed"]`) and applied before first paint next to the style/theme boot script.
- Both the mouse button and `Ctrl+B` call one `toggleSidebar()` that reads state from `DATA`, so the
  two paths cannot drift.
- Collapsed width already shrinks to 62px; the brand mark, the new-chat button and the expand button
  stay legible there (icon-only, `title` text kept).

## 4. Language matching and RTL

- One directive added to `CHAT_SYSTEM`, `BOUND_CHAT_SYSTEM` (`chat.py`) and `SYSTEM` (`engine.py`):
  *"Detect the language of the user's message and answer in that same language. Keep code, file
  paths, identifiers, diff content and every JSON key in English."* In the engine prompt it is placed
  next to the existing "Return ONE JSON object" line so the envelope is untouched and only `summary`
  and `checks` prose follow the user's language.
- **RTL**: `dir="auto"` on `.bub`, on the markdown output container and on `<textarea id="prompt">`.
  `dir="auto"` is the browser's own per-paragraph heuristic, which is what mixed Arabic + code needs;
  no JS detection and no `unicode-bidi` tricks. Code blocks keep `dir="ltr"` explicitly, and the
  sidebar/header chrome stays LTR — the window layout does not flip.
- Tests assert the prompt text and that `mdToHtml` output carries `dir="auto"` on the bubble.

## 5. Scaffolding and the fix loop — mostly already built

Checked against the code rather than assumed:

| Spec | Status |
|---|---|
| Propose missing files instead of halting | **done** — `SYSTEM` lines "You may propose NEW files directly…", `read_file` returns `not_found` + `can_create` + `next_step`, `blocked` after a recoverable observation is bounced back twice (`blocked_retried`, `MAX_BLOCKED_RETRIES = 2`), and an empty project/search says "propose the files the plan calls for" |
| Parse failure output and ask for the next fix | **done** — `repair.fix_task` / `repair.evidence`, redacted at capture, at storage and at transmission |
| Up to `MAX_FIX_ROUNDS = 3` | **done** — `repair.MAX_FIX_ROUNDS = 3`, enforced in `controller.ask_for_fix`, and the round number is in the status line |
| "Automatically continue" | **deliberately not done** — every round still ends in a proposal the user approves. This is the review-first rule, and the spec's own philosophy section keeps it |

**Actual gap:** when a run fails and no fix round is armed, or when the rounds are exhausted, the
next step is a status line — not a question. Proposal: after a failed run, and after
`advance_plan` leaves a step open, put a **confirm** in the conversation naming what happens next
("run the fix round", "start planning step 3 of 5", "stop here") so continuing is an answer rather
than a guess. Nothing writes files either way.

Shipped for the webapp. `_offer_fix()` asks after a failed or timed-out run when no round was armed,
naming the round number, the model and the budget ("Fix round 1 of 3 … nothing is written until you
approve it"), and `_offer_next_step()` does the same for step N+1 when auto-continue is off. Both
questions are built in `repair.fix_offer()` / `repair.step_offer()` so the two windows cannot drift,
and the exhausted-rounds case deliberately asks nothing: there is no next action to name, so a status
line is the honest answer. Declining is the safe branch — no request leaves the machine, and the
run's own summary stays as the last word.

The Tk fallback window keeps its current behaviour on purpose: its `on_done` runs on the worker
thread, and `messagebox` pumps a Tk loop from a thread that does not own it. The webapp is the UI the
user drives; the desktop window stays a working fallback with the same guarantees, minus this one
question.

## 6. Chunk / search-replace proposals

The schema grows a second change shape, accepted side by side with the whole-file one:

```json
{"action":"propose","summary":"…","checks":["…"],
 "changes":[{"path":"calculator.py","edits":[
     {"search":"def add(a, b):\n    return a - b","replace":"def add(a, b):\n    return a + b"},
     {"search":"return a - b  # legacy","replace":""}]}]}
```

- `{"path","content"}` stays valid (new files must still be whole). `{"path","edits"}` is the chunk
  form; `edits` is 1–10 ordered hunks, each with `search` (non-empty) and `replace` (empty allowed =
  delete). `{"path","search","replace"}` is accepted as a one-hunk shorthand, which is the shape in
  the spec.
- **Where it is resolved — a deliberate deviation from the spec.** The spec says "in
  `apply_proposal`". Doing the replacement at apply time means the diff the user reviewed is not the
  bytes that get written, `after_hash` would be computed after approval, and rollback loses its
  reference. So the replacement is resolved **once in `prepare_changes`**, which already holds the
  current file content and the observed hash; `changes[]` keeps storing `before`/`after`, and
  `proposal_hash`, `apply_proposal`, `rollback`, `shrink_warning`, the diff view and the 100 KB cap
  all keep working on resolved content unchanged. `apply_proposal`'s existing `before_hash` preflight
  stays as the guard that the file did not move under us — which is exactly what makes a stale chunk
  fail closed.
- **Unambiguous match, strictly.** Against the content as read:
  - `search` not found → `PolicyError` naming the path and the first non-matching line of the block,
    so the model gets a usable observation instead of a silent near-match;
  - found more than once → refused, listing the 1-based line numbers, with the instruction to widen
    the block until it is unique. No fuzzy matching, no whitespace normalisation, no "first
    occurrence" — a wrong-but-plausible anchor writes code into the wrong place, and the review gate
    is on text, not on intent.
  - an edit whose `search` overlaps a previous edit's replaced range → refused.
- Edits apply sequentially to the working copy, so hunk 2 sees hunk 1's result.
- **Multi-hunk per file** replaces today's flat `Duplicate change target` refusal: two `changes[]`
  entries for one path are still rejected (order would be ambiguous), but `edits[]` gives ordered
  hunks for the same file.
- `SYSTEM` gains the chunk shape and the rule "quote the current text exactly, including
  indentation; if you are not sure what the file says, read it first", and `PROPOSE_SHAPE` shows one
  example of each shape.
- Tests: unique match applied; zero matches refused with a line number; two matches refused with both
  positions; overlapping edits refused; delete-to-empty; chunk + whole-file in one proposal; resolved
  content covered by `proposal_hash`; apply refuses when the file changed after the chunk was
  resolved; rollback of a chunk-applied file restores the original bytes.

Shipped. `_change_form()` keeps the shape rules in one place, `_apply_edits()` the matching rules,
and `prepare_changes()` only ever stores resolved `before`/`after` — so `proposal_hash`, the diff
view, `shrink_warning`, the 100 KB cap, rollback and apply's `before_hash` preflight are untouched.

- Overlap is tracked as spans in the **working** text, shifted by each replacement's length delta,
  so hunk 3 can be refused for claiming bytes hunk 1 rewrote even though the offsets moved.
- The sequencing test is the interesting one: `return a - b` occurs twice in the fixture, so the
  second hunk is only unique *after* the first hunk deletes the other function. Order is load-bearing
  rather than cosmetic.
- Refusals name what the model must do next — the first line of the block that matched nothing, every
  line number that matched twice, "widen the search block until it is unique" — because the engine
  feeds these back as observations and a vague one wastes the turn.
- Verified in the browser: the same one-line fix that the whole-file shape renders as a complete
  rewrite now shows as `@@ -1,2 +1,2 @@` with `+1 −1` in the Changes card.

## 7. Symbol index: JS/TS/TSX, Go, Rust, and an mtime cache

- New scanners in `symbols.py`, all on top of the existing `blank()` (comments and string literals
  replaced by spaces, line positions preserved) so a `// func x()` or a `"class Foo {"` cannot become
  a declaration — the property already proven for Java/Kotlin:
  - **JS/TS/TSX**: `function <name>(`, `async function`, `class <name>`, `const|let|var <name> = … =>`
    (arrow), `interface|type <name>`, `enum`, and `export`-prefixed forms (recorded once, with an
    `export` marker). `.tsx` JSX is skipped by the blanker's angle-bracket ignorance — declarations
    are found by keyword, so unmatched markup in between is harmless.
  - **Go**: `func <name>(`, `func (recv <T>) <name>(` (method with its receiver type), `type <name>
    struct|interface`, `const`/`var` blocks kept to names only.
  - **Rust**: `fn <name>` (plus `pub fn`, `async fn`), `struct`/`enum`/`trait`/`impl <name>`,
    `impl<T> Trait for <Type>`, `mod`.
  - Shared brace-depth rule: a function is recorded as a member of the type one level above it, which
    is what separates a method from a call inside a body.
- `parse()` dispatches on suffix: `.js .jsx .ts .tsx .mjs .cjs`, `.go`, `.rs`. Unknown suffixes keep
  today's behaviour (lexical `fallback` only where it already applies).
- Import edges: `import … from "…"`, `require("…")`, `@Module`, Go's grouped `import ( … )`, Rust's
  `use …` — resolved against repository files by the existing `dependencies()` tail-matching, so
  `java.util.List` and `std::collections::HashMap` still produce nothing actionable.
- **Cache**: a module-level `{(root, relpath): (mtime_ns, size, row)}` filled by `repo_map()`. A hit
  skips `read()` *and* `parse()`; a changed file re-parses; entries for files that vanish or fall out
  of the policy are dropped. Keyed on the resolved root so two projects never share rows, and capped
  (`MAX_FILES`) so it cannot grow without bound. Correctness is unchanged: the map is still labelled
  untrusted convenience, and the files on disk stay authoritative for every hash check.
- Tests per language (declaration found; comment and string literal ignored; method vs call; export
  and receiver forms), plus cache tests: unchanged file is not re-read (instrumented read counter),
  touched file is, a file renamed out of the policy disappears from the map.

Shipped, with three things the plan could not know in advance:

- **Import specifiers live inside string literals, which `blank()` erases.** Every new scanner
  therefore reads each line twice: the blanked copy decides whether a declaration is really there,
  the original copy supplies the module path. Go needed more — a line whose whole content is a
  quoted path blanks to whitespace, so `_go_imports()` extracts the block from the raw text and
  skips lines that start with `//`.
- `dependencies()` had two blind spots the new languages exposed, both in `_linked`: it split only
  on `.`, so a Go or JavaScript **path** import could never match, and it compared tails only, so
  `from pkg.mod import name` (import longer by its trailing symbol) never matched either. Both
  directions are now accepted, which also fixed Python edges that had been silently missing.
- The cache sits in `workspace.py`, not `symbols.py`, because it is the *read* that costs, and
  `repo_map()` now asks `symbols.indexable(name)` **before** reading — a folder of Markdown and
  JSON is skipped outright instead of being parsed and thrown away. Keyed `(root, relpath) →
  (mtime_ns, size, row)`, swept at the end of every map so a deleted or de-policyed file cannot
  keep its row, cleared wholesale at `MAX_FILES` entries, and `clear_index_cache(root=None)` exists
  for tests and for a project the user forgets.
- `.go`, `.rs`, `.mjs` and `.cjs` joined `TEXT_SUFFIXES`: before this section a Go or Rust project
  could not be *read* at all, index or no index.

## 8. More build/test recipes

Added to `RECIPES` with the same shape as the existing entries — constant argv, `markers`, `label`,
`test_counts`, and report globs where the tool writes XML:

| key | argv | markers | proof |
|---|---|---|---|
| `uv-pytest` | `uv run pytest -q` | `pyproject.toml` + `uv.lock` | `--junitxml` file, `test_counts` `(\d+) passed` |
| `pnpm-test` | `pnpm test` | `pnpm-lock.yaml` | console `pass (\d+)` / `(\d+) passing` |
| `cargo-test` | `cargo test` | `Cargo.toml` | console `test result: ok. (\d+) passed` |
| `go-test` | `go test ./...` | `go.mod` | console `^ok\s` lines counted; `--- FAIL:` in the failure vocabulary |

- `available()` already hides a recipe whose executable is missing, so nothing appears for a toolchain
  the machine does not have; `detect()` keeps root-marker matching, and `uv-pytest` is ordered before
  `python-pytest` so a `uv.lock` project prefers uv.
- `ENV_KEYS` already carries `GOPATH/GOROOT/CARGO_HOME/RUSTUP_HOME` and `PATH`; **no new environment
  variable is added**, and `HOME`/`USERPROFILE` stay because cargo and go need them.
- The zero-count-summary rule from UI 2.5 applies to the new `test_counts` patterns: `test result: ok.
  0 passed` must not read as a proof, and a green command with no observed test stays `unverified`.
- `timeout_for`: cargo/go/pnpm get `DEFAULT_TIMEOUT`; `uv` too. No new long-timeout class.

Shipped, with two additions the table could not predict:

- `go test` prints **no total at all**, only one `ok  <package>` line per package, so a numeric
  `test_counts` pattern cannot express "a test ran". Recipes may instead carry `test_ran`, a regex
  whose mere presence is the evidence; `tests_ran()` and `expects_proof()` now answer that question
  in one place instead of inlining `any(int(v) > 0 …)` twice.
- Green summary lines are the failure-vocabulary trap this file already documents for Surefire, and
  both new toolchains walk into it: cargo prints `test result: ok. 5 passed; 0 failed`, and a Go
  package path can literally contain the word `errors`. `CLEAN_SUMMARY` matches those two prefixes so
  they never reach the model as evidence, while `test result: FAILED.` and `FAIL\t…` still do.
- `detect()` lowercases the files it finds, so the cargo marker is written `cargo.toml`.
- Tests: detection by marker, argv exactly as specified, environment scrubbed of a planted key,
  proof counting per recipe, and `available()` hiding a missing executable.

## 9. Live log streaming

`runner.run` today calls `process.communicate(timeout=…)` and hands the whole blob to `progress` at
the end. The controller already has a per-line channel to the browser (`_progress` → SSE `log`), so
this is a reader change, not a new protocol.

- `Popen(stdout=PIPE, stderr=STDOUT, text=True, bufsize=1)`, read line by line in the existing worker
  thread, calling `progress(line)` per line.
- Cap stays at `MAX_OUTPUT_CHARS`: counting keeps the **tail**, and once the cap is passed the
  streaming callback switches to a throttled "… N more lines" so a chatty compiler cannot flood the
  SSE queue (`Hub` already drops events for a stalled client rather than blocking the worker).
- Timeout path unchanged: `wait` on the reader with the remaining budget, then `_kill` (tree kill on
  Windows) and a final drain. No line may outlive the process.
- Carriage-return progress (`npm`/`cargo` spinner lines) is normalised to a line so the UI does not
  grow one bubble per redraw.
- `repair.record_run` still stores the capped tail, redacted — streaming does not change what is
  persisted or sent back to the model.
- UI: the Activity view appends streamed lines as they arrive and the Checks card shows the running
  tail; a `log_chunk` event is added only if the Activity view needs to distinguish streamed lines from
  state notes — the existing `{"kind":"log"}` shape probably carries it.
- Test: a recipe that prints markers with delays (a `python -c` script under the existing test
  harness) asserts lines reach the callback **before** the process exits, and that the stored output
  is still the capped tail.

Shipped. Three things turned out to matter more than the sketch assumed:

- **`Popen` has no `newline` argument**, and both the default universal-newlines mode and
  `newline=""` end a line at every `\r` — which is the opposite of the spinner rule. The pipe is
  therefore wrapped in `io.TextIOWrapper(..., newline="\n")`: split on `\n` only, and `_visible()`
  collapses the `\r` redraws to the last one, after peeling a real CRLF ending.
- **Streaming is a new transmission boundary**, so it is redacted there: a test that prints its own
  connection string would otherwise reach the browser intact. `_build_line()` runs `redact()` on
  every line and keeps them out of `self.log`, so a 5 000-line build cannot ride along in every
  later snapshot; the browser keeps its own 300-line tail (`LIVE`) that survives a state re-render
  and clears when the next job starts.
- The SSE envelope was colliding with itself: `_note()` emitted `{"kind": "log", "kind": kind}`, so
  the second key won and the browser — which switches on `kind` — silently dropped every stored log
  line until the next full render. Entries now travel as `{"kind": "log", "entry": {...}}`.

Verified in the browser rather than only in Python: with a suite that prints and sleeps, the page
showed `busy:1 → busy:2 → busy:3 → idle:9` streamed rows appearing while the run was still going,
`password = "[redacted]"` in place of the planted secret, and no build line in `DATA.log`.

## 10. Stability

- **`server._body` drain on Windows.** When `content-length > MAX_BODY`, read and discard the body in
  fixed chunks before answering 400, so the client is never writing into a closed socket — this is the
  `ConnectionAbortedError [WinError 10053]` that makes `test_an_oversized_body_is_refused` fail in
  about one full-suite run in three. Bounded drain (stop after `MAX_BODY + 64 KiB`), then 400.
- **Tk timer cancellation.** `gui.py` schedules `self.root.after(80, self.poll)`,
  `after(150, self.check_setup)` and more; the ids are dropped, so after `root.destroy()` in tests Tk
  fires dead callbacks and stderr fills with `invalid command name "…poll"`. Store every id in
  `self._timers`, cancel them in `destroy()`/teardown, and make `poll` re-register through one
  `_schedule(delay, fn)` helper so no `after(` call is left untracked.
- Both are covered: the oversized-body test stops being flaky, and a Tk teardown test asserts no
  pending timer id survives `destroy()`.

---

## Order of work

1. **§10** (two independent fixes; makes the suite deterministic before anything else moves)
2. **§3** sidebar toggle + persistence
3. **§4** language directive + `dir="auto"`
4. **§2** registry with icons, `＋ chat` per node
5. **§8** recipes (pure data + detection tests)
6. **§7** scanners, then the cache
7. **§9** streaming runner
8. **§1** project drawer (needs §7's cache to be cheap, and the Explorer decision)
9. **§6** chunk diffs — last, because it changes the proposal schema and every test around it
10. **§5** the confirm-before-continuing question, once 6 and 9 are in place

After each step: `python -m unittest discover -s tests` must be green offline (baseline **226**), and
`node --check` on `app.js` plus `tools/check_contrast.py` for any style change. UI-visible steps are
additionally driven in the real window with `sandbox/verify-ui-28.py` and screenshotted.

**Done, in that order.** The count moved 226 → 236 (§10/§3/§4/§2) → 251 (§8) → 280 (§7) → 286 (§9)
→ 296 (§1) → 311 (§6) → **317** (§5), green at every step, offline, with `node --check` and the
contrast audit re-run at the end. Screenshots of the UI steps are in `.screens/`
(`ui29-01-rtl`, `ui29-02-streaming`, `ui29-03-drawer`, `ui29-04-chunk-diff`, `ui29-05-fix-offer`).

## Decisions needed

All five were put to the user and approved before the code that depends on them was written; the
approvals are what the sections above implement.

1. **§1 Open in Explorer** — **approved with the condition the user set**: argv-only, no shell, and
   the path resolved from `self.projects` by key so the browser can never name a folder. *"هذا يمنع
   أي ثغرة لفتح مسارات نظام حساسة من جهة الواجهة."*
2. **§6 where the chunk is resolved** — **approved as proposed**: `prepare_changes`, because the
   engine must build the new bytes in memory first, which is what makes `difflib.unified_diff`,
   `proposal_hash` and the SHA-256 pair mean anything. *"معتمد وهو الأصح معمارياً 100%."*
3. **§6 strictness** — **approved**: exact match only, refused with line numbers on zero or multiple
   matches. *"استخدام التخمين أو الـ Fuzzy في كود برمجي يؤدي حتماً لتعديل الدوال الخاطئة."*
4. **§5 auto-continue** — **approved as the red line**: generation, proposal and planning the next
   round may be automatic; the write to disk always needs a conscious Apply click. *"الخط الأحمر الذي
   يميز هذا المشروع."*
   **Superseded on 2026-09-26** by UI 3.0, which moved that click rather than deleting it: it is now
   the click that turns **⚡ Auto-Apply** on for one folder, off by default — see
   `docs/AUTOAPPLY-GIT-PLAN.md` §BLOCKING 1 for the ruling and what still asks anyway.
5. **§1 token count** — **approved for both**: exact characters first, then a figure explicitly
   tagged `est.` (`14,200 chars (~3,550 est. tokens)`).
