# UI 3.9 — the seven open defects, pain-ranked

Opened 2026-09-28 from the `ecommerce` dogfood run. Baseline: **753 offline tests green**
(`PYTHONPATH=src python -m unittest discover -s tests`), `node --check` clean, `ecommerce` compiling at
`mvn -B test` exit 0. Every premise below was measured against the code before this plan was written;
**two of the seven spec items rest on a premise the code disproves**, and they are restated rather than
implemented as written.

Order of work: Sprint 1 (1–3) because the three together are what makes an unattended batch possible;
Sprint 2 (4–7) because they are correctness of the change contract.

---

## 1 — D33 · active questions belong in the snapshot

**Measured.** `_ask()` (`webapp/controller.py:308-326`) mints `request_id = secrets.token_hex(8)`, emits
`{"kind": kind, "id": request_id, **payload}` once through `self._emit`, and then
`waiter.wait(timeout=ASK_TIMEOUT)` with `ASK_TIMEOUT = 1800` (`:150`). The id is in no snapshot field —
`snapshot()` (`:640-656`) has `pending`, which is the *progress line* written by `_progress()` (`:1379`) —
and `/api/events` replays nothing to a new subscriber. Measured live today: after a server restart the
fix offer was raised at a page holding the previous token, the queue stalled 30 minutes reading
`busy=False, held=False, items=1`, and the only escapes were the timeout or a restart that also drops the
queue.

**Already exists.** The client can draw every ask kind from the payload alone: `applyEvent`
(`static/app.js:99-115`) routes `confirm` → `askConfirm(msg)` and `folder` → `askFolder(msg)`, and
`askConfirm` reads only `msg.title/message/warning/cancel/confirm/id`. `retractAsk(id)` already removes a
sheet by id. So replay is a feed problem, not a rendering problem.

**Build.**

1. `self._active_asks: list[dict] = []` — a **list**, not one slot: two asks were simultaneously live
   today (one from the previous task's offer, one from the current), and a single `_active_ask` would drop
   the first and leave its waiter hanging forever.
2. `_ask()` appends `{"kind", "id", **payload}` before emitting, and removes it in both exits — the
   answered path and the expiry path that already emits `retract`.
3. `snapshot()` gains `"asks": list(self._active_asks)`.
4. `render()` walks `DATA.asks` and draws any id it has not drawn, tracking drawn ids in a client-side set
   so the live SSE path and the snapshot path cannot both create one sheet — the exact duplicate-row
   mistake D29 was. `retractAsk` clears the id from that set.

**Tests.** An ask raised with no subscriber is present in `snapshot()["asks"]` and `set_reply` on its id
still unblocks the waiter; answering removes it; expiry removes it and emits `retract`; two simultaneous
asks both appear. Client: `render()` replays and cannot double-draw (source-assert, the convention in
`tests/test_webapp.py`).

## 2 — D30 · a third answer on the fix offer

**Measured.** `report_run` (`:1865`) → `_offer_fix` (`:1869`) → `self.confirm(repair.FIX_OFFER_TITLE,
repair.fix_offer(self.model, self._fix_round + 1, self.auto_apply), ok_label="Run the fix round")`.
`confirm()` (`:328-331`) reads `answer.get("ok")` and so discards anything else the reply carried — while
`set_reply` (`:347-352`) already stores the **whole** reply body, so an extra key needs no transport work.
Measured cost: ~5 min/task with the offer, 2.5 min without, over 19 tasks.

**Deviation from the spec, deliberately.** The spec offers "leverage `set_recipe("")` or a batch flag".
Clearing the recipe does that job by destroying the folder's verification — the operator's chosen run
command silently disappears for every later task, and `refresh_recipes()` (`:1779`) picks it back up on
the next project switch, so the state is neither durable nor obvious. The flag is the honest object: it
says "do not ask me again **for this batch**", not "stop verifying my project".

**Build.**

1. `_ask("confirm", {..., "alt": label})`; `confirm_choice()` returns the reply dict, and `_offer_fix`
   reads `alt`.
2. The label comes from `repair` next to `fix_offer`, so both windows read one copy of the sentence.
3. `self._batch_fixes_off = True` on that answer. `_offer_fix` returns False immediately while it is set,
   and says so in a Tool row — a silent "no" would look like the tool stopped caring.
4. Cleared when the queue empties, and on any task the operator starts by hand: a batch is the queue, so
   a fresh Send is a fresh batch.

**The asymmetry to name, not hide.** `gui.py` has no message queue (only a `queue.Queue` event pump), so
"the rest of the batch" does not exist in the Tk window. The third button is therefore web-only, exactly
like `labels.SINGLE_WINDOW_STATUS` is for `ask_expired`. Tk keeps two buttons and its answers mean what
they mean today. A test pins that the shared sentence list still passes.

**Tests.** The alt answer runs no fix round, silences the *next* offer in the same batch, and does not
survive a new manual task; the plain "no" still asks again next time; `MAX_FIX_ROUNDS` behaviour unchanged.

## 3 — D28 · Run belongs to the folder, not to the last session

**Measured.** `_can_run()` (`:1796-1798`) and `run_tests()` (`:1800-1805`) both gate on
`state in MUTABLE_STATES` = `{APPLIED_UNVERIFIED, VERIFICATION_BLOCKED, VERIFICATION_FAILED,
CHECKS_PASSED}` (`labels.py:67`). So after `BLOCKED`, `CANCELLED`, `ROLLED_BACK` — and after an
interrupted apply (`PARTIAL_APPLY`/`APPLYING`, which are in `UNVERIFIED_STATES` but **not** in
`MUTABLE_STATES`) — the project cannot be built at all, even though the files worth verifying are on disk.

**Good news: the documented lock survives.** `test_commands_are_locked_until_a_proposal_is_applied`
(`tests/test_controller.py:506`) plans a fix and asserts `run_tests` does nothing with
"Apply a reviewed proposal…" — that is `WAITING_APPROVAL`, a *pending* proposal. The rule that expresses
both facts is "locked while the work in front of you has not been written yet", i.e. locked in
`{DISCOVERING, WAITING_APPROVAL}` only. That test needs no edit, and D28 closes.

**Build.**

1. `_can_run` = `not busy and bool(recipes) and repo is a directory and state not in {DISCOVERING,
   WAITING_APPROVAL}` (no session at all → allowed: nothing is pending).
2. `run_tests` takes the folder from `self.session["root"]` **or** `self.repo`, and records the run only
   when there is a session that can hold it — `repair.record_run` raises
   "Apply the reviewed proposal before running commands." for a state outside its own set, so a BLOCKED
   session must not be written into. The Checks row, `run_info` and the state line still come from
   `report_run`, which reads the runner result, not the session record.
3. When nothing can hold the record, say so in one plain sentence — a run that vanished into the folder
   with no record is the D12 failure this file already documents.

**Tests.** `canRun` is True after a BLOCKED task with files on disk; `run_tests` runs the recipe and
reports it without touching the blocked session; still locked at `WAITING_APPROVAL`; still locked while
busy; allowed with no session in a folder that has a recipe.

## 4 — D31 · a delete entry in the change contract

**Measured.** No delete verb exists anywhere: `workspace.py` has `read`/`write`/`path` and no unlink; the
only `unlink()` in the engine is inside `rollback()` (`engine.py:931-934`) and it keys off
`change["before"] is None` — which is exactly why a task cannot retire a file, and why `ecommerce` shipped
three near-duplicate security configs.

**The `before is None` collision is the central design fact.** Today that field means "this file did not
exist, so undo means remove it". A delete entry means "this file existed, so undo means restore it" — the
opposite, and indistinguishable without a flag. So the entry carries `"delete": True` explicitly and
`rollback()` branches on **that**, not on `before`.

**None-consumers that must change together** (each is a crash or a silent misrender, from recon):

| site | today | with `after = None` |
| --- | --- | --- |
| `engine.review:862` | `change["after"].splitlines(keepends=True)` | AttributeError |
| `engine.shrink_warning:848` | `change["after"].splitlines()` | AttributeError, on the ≥8-line case that a delete always is |
| `engine.diff_size:825` | `change.get("after") or ""` | reports "every line replaced", not "removed" |
| `verification.static_check:30-33` | reads the file, compares to `after_hash` | reads a file that must not exist |
| `controller._review:2279-2300`, `_diff:2404` | `kind = "A" if before is None else "M"`, preview `chosen["after"]` | TypeError; a delete drawn as a create |
| `gui.py:1625, 1698-1702` | same Added/Modified rule, same diff | crash in the Tk review tab |
| `host.apply_prompt:64` | "Write N file(s)" | a card that says Write about a removal |
| `git_integration.checkpoint:242-250` | `git add` of the paths | a deleted path is never staged |
| `labels.write_notice` / `applied_line` | counts files, lines | must say removed |

**Build order inside the item** (one change, not two: a half-built delete would unlink a file the review
card drew as a modification).

1. `_change_form` admits `{"path", "delete"}` with `delete is True`; anything else stays refused.
2. `prepare_changes`: the file must exist and must have been read (the existing `observed` guard applies);
   record `before`, `before_hash`, `after=None`, `after_hash=None`; skip the language gates (there is no
   content to parse) and skip the "unchanged file" refusal; the 100 KB budget counts the removed text.
3. `apply_proposal`: preflight already compares `actual != change["before_hash"]`, which is the right guard
   for a delete — then `target.unlink()` instead of `ws.write`, and the event is `removed` not `written`.
4. `rollback`: branch on the flag; `ws.write(path, change["before"], change["after_hash"])` restores it.
5. `must_ask`: **a delete never auto-applies.** It joins `removal_notice`'s reasons, because the run's own
   rule is that Auto-Apply removes a click, not a decision.
6. `git_integration.checkpoint`: `git rm --force` for delete paths, `git add` for the rest, one commit.
7. Both review surfaces: kind `"D"`, "Removed" label, the diff rendered as the whole file with `-` lines,
   the notice saying "N file(s) removed".
8. The model contract: `PROPOSE_SHAPE` and the system prompt gain the delete form, otherwise only the
   operator can ever use it.

**Tests.** delete lands and unlinks; rollback restores it; Auto-Apply refuses to do it without a click; a
delete of a file nobody read is refused; a delete of a missing file is refused; the review card says D;
git checkpoint stages the removal; `review()` and `gui`'s tab both survive a delete entry; proposal hash
covers it.

## 5 — D35 · one task-length constant, and advice a large file can follow

**Measured.** 4 000 appears as a task limit in six places, with two different sentences for the same rule
(`engine.py:473` "1-4000", `engine.py:520` "1–4000" — hyphen vs en-dash) and three more truncations
(`controller.py:424`, `:449`, `:1278`, `:1502`). `engine.py:690` caps a proposal's `summary` and is **not**
the same rule — it stays a field cap.

**Measured, the sentence that is unreachable.** `engine.py:446`
"Invalid XML in … Put actual complete file text directly in content." — and `plan()` refuses a task longer
than 4 000 characters (`:519`), which is the only channel the operator has for that text. That is how
`product-service/pom.xml` blocked twice today.

**Build.** `MAX_TASK_CHARS = 4000` in `engine.py`, imported at the five call sites; both refusal sentences
(`:446` XML, `:448` general syntax) add the route that works at any file size: "or send one anchored edit:
search for a line unique to this file and replace it with itself plus yours." The two hyphen variants
become one string.

**Tests.** The limit is one number (a test reads the constant and the two messages, so a future edit
cannot fork it again); a 4 001-character task is refused identically from `plan`, `propose_block`,
`start_plan` and `queue_add`; the XML refusal now names the anchored edit.

## 6 — D34 · the spec's check cannot catch the incident; here is the one that does

**Premise, disproved by measurement.** The item asks to "parse and validate the XML structure via
ElementTree before and after applying edits to verify AST integrity". Today's gate already parses the
**resulting whole file** (`engine.py:410`, `ElementTree.fromstring(content)` inside `prepare_changes`),
which is strictly stronger than before/after — `before` is disk content and always parses. And the file
that actually got written, `git show 0ea2238:product-service/pom.xml`, **parses cleanly**:

```
ElementTree: PARSES FINE -> a before/after XML parse check would have let this through
parent of <dependency>: ['dependencies', 'project']
```

Maven rejected it with `Unrecognised tag: 'dependency'` because a `<dependency>` was hanging off
`<project>` instead of `<dependencies>` — well-formed XML, invalid **Maven model**. So the check that
catches it is small and specific.

**Build.** In `prepare_changes`, next to the existing `.xml` branch: when the root element is `project`,
every `dependency` must be a child of a `dependencies` element and every `plugin` a child of a `plugins`
element; otherwise refuse, naming the element and the parent it was found under. Doc and test names call
it what it is — a **POM model check**, not a general XML AST check.

**Honest limit.** It cannot catch the *other* D34 incident, where the model deleted the dependency element
and left its comment: that result is well-formed and model-valid, and only the build sees it. That is
item 7's batch summary, and this file says so rather than pretending a gate covers it.

**Tests.** the measured bad pom is refused with the parent named; the same pom with the element under
`dependencies` passes; a non-pom XML (a Liquibase changelog, whose root is `databaseChangeLog`) is
untouched; a `<dependency>` inside `dependencyManagement/dependencies` passes.

## 7 — queue persistence, and D36's batch summary

**Premise, corrected.** The spec reads like an omission; it is a **documented decision with its own test**.
`controller.py:237-239`: "In memory on purpose: a queued change request that outlives a reboot can be
applied to files that have moved on since it was typed", and
`test_the_queue_is_in_memory_only` (`tests/test_controller.py:2105`) asserts it. So the fix is not
"persist it" but "persist it **without resurrecting an unattended write**".

**Path deviation.** The spec says `.agent-runs/queue.json`. `.agent-runs` holds one directory per session
and nothing else; the stores that already exist are `.agent-projects.json` (written by `_save_state`,
`:2339-2358`, which already carries `mode`, `last_chat`, `composer`, `auto_apply`…), `.agent-plans/<key>.json`
and `.agent-memory/<key>.md`. A queue is per-window UI state, so it goes in the `ui` block of the file the
controller **already** writes atomically — one store, one reader, no new failure mode. Say so here rather
than silently moving it.

**Build.**

1. `ui["queue"]` persists items with their `chat`, `branch`, `project` and the `at` stamp.
2. On load the queue is restored **`_queue_held = True`**: the strip shows exactly what is waiting, and
   nothing starts until the operator presses ▶. That keeps the invariant the old test names, so the test is
   rewritten deliberately — from "resurrects nothing" to "resurrects it held, and held means nothing runs".
3. Each restored item carries the note that it was typed before the restart, in `labels` (Arabic and
   English), because a queued "delete the config class" from yesterday needs that sentence in front of it.
4. **Batch summary.** `self._batch` records each queue-started task: first line of the task, the paths its
   proposal wrote, and the file count. When a drain finds the queue empty, one Tool row: batch finished, N
   tasks, M files, the paths listed. The discrepancy flag is deliberately narrow: it fires only when the
   task text literally contains the scaffold phrase ("Create exactly two new files", "…three files…") and
   fewer paths landed — the shape D36 actually hit. Parsing arbitrary prose for a count would refuse good
   work, and that is stated in the code comment rather than discovered later.

**Tests.** restart keeps the queue visible and **held**; ▶ resumes it; a new manual task does not start it;
the batch summary appears once with the paths; the flag fires on the literal phrase and stays quiet on a
task that never named a count.

---

## Build order and expected count

| # | item | files | new tests (est.) |
| --- | --- | --- | --- |
| 1 | D33 asks in snapshot | `controller.py`, `static/app.js` | 5 |
| 2 | D30 third answer | `controller.py`, `repair.py`, `static/app.js`, `labels.py` | 4 |
| 3 | D28 Run gate | `controller.py` | 5 |
| 4 | D31 delete contract | `engine.py`, `workspace.py`, `verification.py`, `git_integration.py`, `host.py`, `controller.py`, `gui.py`, `labels.py` | 9 |
| 5 | D35 one limit | `engine.py`, `controller.py` | 3 |
| 6 | D34 POM check | `engine.py` | 4 |
| 7 | queue + summary | `controller.py`, `labels.py` | 5 |

Sprint 1 first (1–3, ~14 tests), then 5–6 (cheap, isolated), then 4 (widest), then 7. Suite target:
**753 → ~788**, each run against the full `discover`, never a single module, because this suite has
already hidden a race inside `discover` that only appeared when a module ran alone.

One security note kept from review: the new POM check parses proposal text, so it inherits the existing
hardening — `prepare_changes` refuses a DTD **before** `ElementTree.fromstring` is ever called
(`DOCTYPE.search(content)`, `engine.py:408-410`), which is what closes entity expansion and external
fetch on model-authored XML. The check adds no parser and no new entry point.

## Open decisions (answers needed before any source edit)

1. **D31 scope**: full contract in one item (recommended — half-built, Auto-Apply could unlink a file the
   review card drew as a modification), or engine first and the two windows after?
2. **D30 alt answer scope**: only the fix-round offer (recommended), or every confirm that fires inside a
   batch? The apply confirmation is a per-write decision and I would not batch it.
3. **Queue home**: `ui["queue"]` in `.agent-projects.json` (recommended, one store already atomic-written),
   or the spec's new `.agent-runs/queue.json`?
4. **D28 with no session at all**: allow Run on a freshly opened folder that has a recipe and no task yet
   (recommended), or require that some task has written something?
5. **D34 naming**: call it a POM model check in code, tests and docs (recommended) — the spec's "XML AST
   integrity" name would promise a guarantee it does not give.
