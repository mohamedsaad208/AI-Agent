# Plan — proactive writing (intent routing, auto-apply, per-block apply) + native Git

Status: **implemented 2026-09-26 — all six items, 402 offline tests.** Two findings changed the spec as written; both are marked
**BLOCKING** below. Everything else is buildable as specced, in the order at the end.

> The `file.py:line` anchors in this plan are from the tree as it stood before the first item was
> built, and they have all drifted since. Read them as "the function named there", not as a
> location; the code and `docs/IMPLEMENTATION-STATUS.md` are the current word.

Baseline: **317 offline tests green** (`python -m unittest discover -s tests`), Python 3.11,
`dependencies = []`, `node --check`, `tools/check_contrast.py` (108 pairs).

---

## BLOCKING 1 — Auto-Apply reverses the rule the user called the project's red line

On 2026-09-26, deciding §5 of UI 2.9, the user wrote: *"الخط الأحمر الذي يميز هذا المشروع"* —
generation, proposal and planning the next round may be automatic, but **the write to disk always
needs a conscious Apply click**. That rule is now encoded in code and tests:

| Where | What pins it |
|---|---|
| `controller.py:1026-1031` | `apply()` calls `self.confirm("Apply changes", …)` and returns if it is refused |
| `controller.py:1148-1151` | `_offer_fix` docstring: *"Nothing is written here either: the round produces a proposal, and Apply stays a separate, deliberate click."* |
| `engine.py:602-613` | `apply_proposal` requires `state == "WAITING_APPROVAL"` **and** a matching `proposal_hash` |
| `tests/test_controller.py:300` | `test_declining_the_apply_confirmation_writes_nothing` |
| `docs/ENHANCEMENTS-PLAN.md` §5, `README.md`, `docs/IMPLEMENTATION-STATUS.md` | the same sentence in three documents |

Item 2 asks for the opposite: apply the moment `action:"propose"` arrives, with no click.

This is not a reason to refuse — the user owns the rule and may lift it. It **is** a reason to ask
once, because the cost of guessing wrong is a tool that edits files unattended, which is the one
behaviour the project was built to make impossible.

**Recommended shape if approved: one-click, never zero-click.** Auto-Apply becomes a real,
persisted, per-project mode that removes every *extra* step — the proposal is applied and the
command run without a second thought from the user — but the first enable of the switch is itself
the consent, and the banner (`✅ Applied to N files · ↩ Undo`) appears with the diff already
rendered. That keeps the property the user wanted (proactive, fast, no passive chat feeling) while
leaving an auditable consent event in the session log. Zero-click auto-apply on top of a model
output is the variant that should need a deliberate "yes, really" — see Decision A.

---

## BLOCKING 2 — two Git commands in the spec do not work on this machine

Observed, not assumed:

```
$ git --version
git version 2.15.0.windows.1          # built late 2017

$ git branch --show-current           # inside a fresh repo
error: unknown option `show-current'  # added in git 2.22 (2019)
```

`D:\AI\AI-Agent` is **not** a git repository (there is a `.gitignore`, never a `git init`), so the
graceful-degradation path is the default state here and must be a first-class test case, not an
afterthought.

| Spec command | Status on 2.15 | Replacement |
|---|---|---|
| `git rev-parse --is-inside-work-tree` | ✅ works | keep |
| `git branch --show-current` | ❌ unknown option | `git symbolic-ref --short -q HEAD` (empty output ⇒ detached/none) |
| `git status --porcelain` | ✅ works | keep |
| `git commit -am "…"` | ⚠️ works, and that is the problem | see BLOCKING 3 |
| `git checkout -b agent/task-…` | ✅ works | keep, but only on an explicit request |
| `git reset` for rollback | ✅ exists | ❌ **do not use** — see BLOCKING 3 |

Feature-detect once at startup (`git --version` + one probe) and cache the result, rather than
discovering a missing flag mid-write.

---

## BLOCKING 3 — `git commit -am` and `git reset` would damage the developer's own work

`commit -am` stages **every tracked file that differs from HEAD**, not the agent's files. A
developer with three unrelated half-edited files who lets the agent apply a one-line change gets
those three files committed inside `agent: … [session-ab12…]`. That is silent authorship theft in
their history, and it is unrecoverable by `rollback()`, which only knows the proposal's own paths.

`git reset` (any flavour that moves HEAD) is worse: it discards or unstages everything else in the
tree, including work the agent never touched.

**Design that keeps the intent and drops the damage:**

1. **Commit only the paths this proposal wrote.** `git add -- <p1> <p2>` then `git commit -m …`
   (no `-a`). Files are taken from `session["changes"][*]["path"]` — the exact set the hash
   covers. Nothing else can enter the commit.
2. **Never reset, never clean, never `checkout .`.** Restore is `git checkout <pre-hash> -- <path>`
   for the proposal's own paths only — file-scoped, non-destructive to anything else.
3. **The existing hash rollback stays the primary undo.** `engine.rollback` (631-663) is *stricter*
   than git: it hard-aborts with `"Rollback would overwrite a later edit: <path>"` if any target
   moved. Git cannot tell you that. So git is added as a **cross-task** safety net and history, not
   as a replacement — and if a git restore would overwrite a later human edit, we refuse and say so.
4. **No commit on a dirty tree that the agent did not create.** Before committing, compare
   `git status --porcelain` against the proposal's paths; if unrelated files are dirty, still commit
   only ours (that is what the explicit pathspec is for) and say in the log line that the tree had
   other changes.

---

## Item-by-item: what already exists

### 1. Smart intent detection → Change mode

**Already there:** the routing decision is one line — `controller.py:853`:
`if not repo or self.composer == CHAT_COMPOSER: self.start_chat(…); return`. `set_composer`
(416-428) is the only writer, refuses Change without a folder, and persists per branch key in
`self._composer_pref` → `ui["composer"]`. `friendly_error` already answers an editless message with
"Switch the badge next to Send to Chat mode" (`labels.py`, `test_an_editless_message_in_change_mode_points_at_the_badge`).

**New:** a pure function `intent_is_editing(text) -> bool` in `controller.py` (or `labels.py`),
consulted at the top of `start_plan` when `self.composer == CHAT_COMPOSER and self.repo`.

Three corrections the spec's keyword list needs:

- **Match the first word, not a prefix.** `"add"` as a prefix matches `address`, and `"make"` matches
  `makeshift`. Tokenise: `re.match(r"^\s*([\w'’\-]+)", text.casefold())` then compare against a set.
- **Arabic needs normalisation.** Users type alef with and without hamza (`أنشئ` / `انشئ` / ` اعمل`),
  and `ال` prefixes the verb (`اضف` vs `ضيف`). Strip tatweel/diacritics and fold `أإآٱ → ا`, then
  match a small set of normalised stems. The spec's list (`عدل صلح اكتب ضيف اعمل احذف غير`) will
  otherwise miss the forms people actually type — and miss `ابني`/`طوّر`/`نفذ`.
- **Do not persist an inferred switch.** `set_composer` writes `_composer_pref[key]`, so an inferred
  Change would overwrite the mode the user chose for that project and it would come back forever
  after. Route *this message* to Change (set `self.composer` without saving), and say so in the
  status line: `⚡ Change mode for this message — the badge still says Chat.` The user's stored
  preference wins on the next message.

`test_a_restored_project_keeps_the_mode_it_was_left_in` and
`test_the_chosen_mode_is_remembered_for_that_project_only` are the tests this must not break.

### 2. Auto-Apply / Turbo mode

**Already there:** the `plan_chained` boolean is the exact template — declared (97), restored from
`_saved_ui` (153), saved as `ui["plan_chained"]` (1620), dedicated setter `set_chained` (627-630),
action key (334), `snapshot()["settings"]["chained"]` (305), rendered as `.switch` in the Settings
sheet (app.js:911, wired 929). `_applied` (1035-1041) already chains straight into `run_tests(False)`
when `_auto_fix` is set, so "apply then run the checks" is one existing call away.
`engine.apply_proposal`'s preflight keeps a stale proposal from being written even unattended.

**New:** `self.auto_apply` + `set_auto_apply` + `ui["auto_apply"]` + `settings.auto_apply`, and one
condition around the `confirm` at 1026-1031. Per-project persistence is a small map like
`_composer_pref` (`ui["auto_apply_per_project"]`), because a global default ON is the scariest
possible reading of the spec. The banner is a `{"kind":"auto_applied", …}` event plus a bar in
`renderThread`; `↩ Undo` calls the existing `rollback` action.

**Not in the spec, and needed:** what happens when auto-apply meets `PARTIAL_APPLY` (interrupted
write) or `removal_notice()` (most of a file disappearing, `controller.py:1008`). Both currently
exist *because* a human was looking. Auto-apply must fall back to asking in exactly those two cases
— otherwise "turbo" silently deletes work.

### 3. "⚡ Apply to File" on chat code blocks

**Already there:** `mdToHtml` (app.js:396-410) splits on ``` and **already extracts the language
token** (`chunk.slice(0, nl).trim()`) and then throws it away except as a label. The `.bar` header
(404) already holds one right-aligned button (Copy, `margin-left:auto` at app.css:226), and the
handler is wired by delegation-ish query in `renderThread` (463-465). So the seam exists.

**New:** a filename resolver (`# path: src/main.py`, `// path: x.y`, `//@`, `<!-- path: x -->`, and a
bare `src/main.py` fence title), plus a second button. Two constraints:

- `appendToken` (469-472) re-renders the last bubble's `innerHTML` on **every streamed chunk**, so a
  button wired inside `mdToHtml` would be destroyed and re-created mid-stream. Wire it in
  `renderThread` beside the Copy handler, and disable it while `DATA.busy`.
- A chat branch has `self.session = None` (951) by design. Writing from chat must go through a real
  proposal (`prepare_changes` → `WAITING_APPROVAL`) so the hash, the diff and the rollback all still
  exist — the click becomes "propose this file", and with Auto-Apply off the user still sees the
  diff before it lands. That is one click of consent, which is what the red line asks for.
- The target path must be resolved by `Workspace.path(name, writable=True)` — never trusted from the
  fence text. `AGENTS.md`/`.github/…` and everything in `BLOCKED_PARTS` stay refused, and the button
  must not appear at all for a chat with no project.

### 4. Native Git integration

**Already there:** nothing. Zero git code. The one sanctioned non-allowlist subprocess is
`controller.reveal` (784-811) with its three tests (897/911/919) — fixed argv, no shell, path
resolved from the granted map by key. `git_integration.py` follows that precedent exactly:
`subprocess.Popen(argv, …)`, no `shell=True`, `cwd=` the granted root, `env=` a scrubbed copy
(reuse `runner.child_env()`; git needs `HOME`/`USERPROFILE`, `PATH`, `SYSTEMROOT`, and
`GPG_TTY`-style extras are not needed), plus a hard timeout so a credential prompt can never hang
the worker.

**New, and where it attaches:**

- `available(root) -> dict` — `{repo: bool, branch: str, dirty: [...], head: str}`, cached with a
  short TTL; called from `snapshot()` only when a project is in front (it must not add a subprocess
  to every paint — `snapshot` runs on every state event).
- `checkpoint(session, root) -> str|None` — path-scoped commit per BLOCKING 3, called from
  `_applied` (not from `engine.apply_proposal`, which must stay subprocess-free and testable).
- `restore(root, pre_hash, paths)` — file-scoped checkout, offered only when the proposal's own
  `before_hash` check would also pass.
- Branch chip in `snapshot()["branch"]` (296) → `modeBadge()` (app.js:505-526). Its colour must be a
  `tokens.css` variable or `DesignTokenTests`/`check_contrast.py` fails.
- Task branching lives in the §1 project drawer, off by default, and creates the branch **before**
  the first write, on explicit request only.

**Shipped (step 1, read-only half).** `src/ai_code_engineer/git_integration.py` exists and does
nothing but read. Five questions, in this order, and no more:

| Command | Why this form |
|---|---|
| `rev-parse --is-inside-work-tree` | one call, and a non-repo stops there — 1 subprocess, not 5 |
| `rev-parse --show-toplevel` | normalised to OS separators; git prints forward slashes on Windows |
| `rev-parse --short HEAD` | fails on a repo with no commits, which is why `head` can be empty |
| `symbolic-ref --short -q HEAD` | BLOCKING 2: `--show-current` is a git 2.22 flag and this box has 2.15 |
| `status --porcelain` | count capped at `MAX_PATHS` 500; renames take the new name, quotes come off |

Every call goes through one `_done()` helper: list argv, `shell=False`, `cwd` the granted folder,
`stdin=DEVNULL`, a 4 s timeout, and `runner.child_env()` plus `GIT_TERMINAL_PROMPT=0`,
`GIT_OPTIONAL_LOCKS=0`, `GIT_PAGER=cat`. `--no-optional-locks` and `-c core.fsmonitor=false` are
per-invocation because the repository on disk is not trusted code — configuration and hooks come
from the folder git runs in, and `status` can otherwise start a daemon.

`dirty` is `None`, not `0`, when `status` fails or times out: a repo that did not answer is not a
clean repo, and the chip would claim it. `reason` carries git's own first line, redacted.

`inspect()` is the uncached call; `status()` is the cached one (8 s TTL, ~0.2 s per fill on
Windows) and is what `snapshot()` uses through `controller._git_info()`, which forwards only
`repo / branch / detached / head / dirty` — the path list stays on the server. `forget()` is called
from `_applied` and from rollback's completion, so a chip never shows the state from before the
write the user just approved.

The chip is `gitChip()` in `app.js`, in the composer bar after the path pill: branch name (or a
7-character HEAD when detached) tinted with the already-audited `--ok-*` pair for a clean tree and
the `--warn-*` pair for uncommitted work, with the count only when it is non-zero. It renders
nothing at all for a folder that is not a repository.

### 5. Proactive scaffolding prompt

**Already there, almost entirely.** `engine.SYSTEM` (21-53) already says: *"You may propose NEW files
directly, with their complete content, without reading them first"*, *"For a task asking to
create/scaffold a project, files and parent directories may not exist yet"*, *"When can_create=true
AND the task calls for creating that file, include it in propose.changes"*, *"Never use
action='blocked' to repeat an error the runtime reported"*, and `blocked` is bounced back twice
(`MAX_BLOCKED_RETRIES = 2`). The empty-project observation already says "propose the files the plan
calls for instead of blocking".

**New:** one sentence of framing ("you have write access; emit a proposal rather than explaining"),
plus — this is the part that actually changes behaviour — make sure the *chat* prompt is not the one
answering scaffolding requests. Item 1 and §1's mode routing are what fix "it only explains"; a
stronger SYSTEM text cannot, because in Chat mode `engine.plan` is never called at all
(`controller.py:853`).

---

## Order of work

1. **§4 read-only git first** (detection + branch/status chip). No writes, no risk, and it gives the
   feature-detection harness everything else needs.
2. **§1 intent routing** (mode only, unpersisted). Small, testable, and it is what makes the tool
   feel proactive today.
3. **§5 prompt framing** (one sentence + a test that the directive reaches all three prompts).
4. **§3 per-block apply** (goes through the proposal path, so it needs nothing from §2).
5. **§4 checkpoint commit + file-scoped restore** (the first git *write*; after the read-only half is
   proven).
6. **§2 auto-apply** last — it is the only item that touches the red line, and by then the undo
   story (git checkpoint + existing hash rollback + banner) actually exists to back it.

After each step: the suite stays green offline (baseline **317**), `node --check` on `app.js`,
`tools/check_contrast.py` for any new colour, and UI steps driven live with
`sandbox/verify-ui-28.py` and screenshotted.

New tests: intent detection (English + normalised Arabic + `address`/`makeshift` negatives + "does
not overwrite the stored pref"); auto-apply (on ⇒ applied with no confirm, off ⇒
`test_declining_the_apply_confirmation_writes_nothing` still passes, partial-apply and
removal-notice fall back to asking, per-project persistence survives restart); git (non-repo
degrades silently; no `git` binary degrades; commit contains **only** the proposal's paths and leaves
an unrelated dirty file untouched; restore is file-scoped; a later human edit blocks the git
restore; every call is argv with no `shell`; a hanging git times out); per-block apply (path resolved
through policy, `.agent-*` and `AGENTS.md` refused, button absent with no project, content written
via a real proposal so `proposal_hash` and rollback apply).

---

## Decisions needed

**A. Auto-Apply without a click — confirm the reversal.** Do you want (i) **one-click**: enabling the
switch is the consent, the switch is per-project and defaults OFF, and apply still needs you to be in
that project — my recommendation; or (ii) **zero-click** exactly as specced: any proposal is written
the moment it lands, with the banner and Undo after the fact? This is the red line you set on
2026-09-26; I will implement either, but not by default.

**B. Auto-apply and the two dangerous cases.** When a proposal wipes most of a file
(`removal_notice`) or an earlier apply left `PARTIAL_APPLY`, fall back to asking even with
Auto-Apply ON? (Recommended: yes.)

**C. Git commits.** Accept the corrected design — commit **only the proposal's own paths**, never
`commit -am`; **no `reset`/`clean` ever**, file-scoped `checkout <hash> -- <path>` only; commit
created in `_applied`, not inside `engine.apply_proposal`? And do you want the commit to happen by
default in a git repo, or only when a per-project "checkpoint commits" switch is on?

**D. §1 keywords.** Agree to (a) first-word matching rather than "begins with", (b) Arabic
normalisation (alef/hamza, `ال` prefix) and a few stems the spec omits (`ابني`, `طوّر`, `نفذ`), and
(c) **not persisting** an inferred switch so your stored per-project mode wins next message?

**E. Scope of this round.** All six steps above in one pass, or steps 1-3 (git read-only, intent
routing, prompt) first so you can feel the difference before anything can write on its own?

---

## What shipped

**§4 git, read half** — `src/ai_code_engineer/git_integration.py`. Five commands, all local, argv
only, no shell, `runner.child_env()` plus `GIT_TERMINAL_PROMPT=0` / `GIT_OPTIONAL_LOCKS=0`, 4 s
timeout each, `--no-optional-locks` and `-c core.fsmonitor=false` on every call because the folder
on disk is not trusted code. Branch comes from `symbolic-ref --short -q HEAD`, not
`branch --show-current` (BLOCKING 2). `dirty` is `None` when `status` fails or times out — a repo
that did not answer is not a clean repo. `status()` caches for 8 s (~0.2 s to fill); `forget()` runs
after an apply and after a rollback. Surfaces as `snapshot()["git"]` — five fields, no path list —
and as `gitChip()` in the composer bar.

**§4 git, write half** — `checkpoint(root, task, session_id, paths)`: `git add -- <the proposal's own
paths>` then `git commit --no-verify -m "agent: <task> [session-…]"`, called from `_applied`. Never
`-a` (BLOCKING 3: it would sweep the developer's unrelated edits into an agent-authored commit),
never `reset`, never `clean`; `--no-verify` because a hook is arbitrary code that folder installed
and nobody is at the keyboard. Paths are filtered by `relative()`: absolute, drive-letter, `..` and
`.git` segments return "" and never reach git. Failure is a log line, not a failed apply — the
change is already on disk and the hash rollback is untouched. **No git-shaped rollback was built:**
`Roll back` already restores from the session's own `before` bytes and refuses once a file is edited
afterwards, so a second rollback path would only add a way to disagree with the first.

**§1 intent routing** — `asks_for_a_change()` reads the opening word (after "please"-style
lead-ins), folds Arabic spelling (`أ إ آ ٱ → ا`, `ى → ي`, `ة → ه`, `ء/ئ/ؤ` carriers, tashkeel
including shadda) and refuses anything that opens with a question word. A match routes **that one
message** down the Change path and writes nothing to `_composer_pref`, so the next "thanks" is prose
again. The route announces itself in the conversation and in the running line.

**§5 prompt** — four lines in `engine.SYSTEM`: a task that needs files is answered with
`action="propose"` carrying every file the project needs, boilerplate included, "never with
instructions for the user to create those files by hand. Explaining a change is not the same as
proposing one." It says *write access through proposals*, because that is still the only kind there
is.

**§3 Apply to File** — `blockTarget()` recognises `# path: x`, `// x`, `<!-- x -->`, `-- x`, `% x`,
`; x`, `! x`, `** x` and a bare name, only on the block's first line and only with an extension;
`blockBody()` drops that label line before the content is sent. The button renders only when the
branch has a folder, and dispatches `apply_block` → `engine.propose_block()` → the same
`prepare_changes` a model goes through → `WAITING_APPROVAL`. It is wired in `renderThread`, not in
`mdToHtml`, because `appendToken` repaints the streaming bubble on every chunk. A block is still a
proposal: nothing about the button writes.

**§2 Auto-Apply** — per folder, off by default, persisted in `ui["auto_apply"]` as a map from branch
key, restored when the branch is selected. With it on, `auto_apply_ready()` fires from the plan and
fix-round completions and calls `apply()`, which skips the confirmation; `_applied` then says so in
the conversation, sets `snapshot()["banner"]`, and runs the project's own command if one was
detected. Two cases still stop for an answer: a proposal that empties an existing file
(`removal_notice`) and a previous task left in an interrupted state. The Apply-to-File button is
deliberately **not** auto-applied — that click already is the request. `↩ Roll back` in the note
calls the same rollback as the card.

### Two spec deviations, both chosen

1. **Zero-click, not one-click.** The switch writes a proposal the moment it lands, as specced. The
   safety that replaces the missing click is that it is per folder, off until asked, said out loud
   on the card, reversible from the same card, and it still asks for the one destructive case.
2. **No task branches.** `git checkout -b agent/task-…` was not built. It moves HEAD in the
   developer's repository as a side effect of an agent task, which is a bigger claim on their
   working tree than anything in this list earned, and nothing else here depends on it.

### Live pass

`python -m unittest discover -s tests` → **402 OK** offline. `node --check` clean,
`tools/check_contrast.py` → 108 pairs, 0 failures (the chip and the switch reuse the already graded
`--ok-*` / `--warn-*` pairs). Screenshots in `.screens/ui30-01…09`: a dirty amber chip
(`master 1`), a clean green one, the detached form (`f4635e7`), no chip at all for a folder that is
not a repository, the Apply-to-File button on the headed block and its absence on the unheaded one,
the proposal that click opened with the file still unchanged on disk, the write after the explicit
Apply, the Auto-Apply pill, and the note with its `↩ Roll back`.

The §1 route was driven from the server side (`--routed`), because sending a chat message from
automation needs a confirmation this session did not carry: the branch stayed in Chat, the task
produced `● Changes ready for review`, and the routing note sat between the two. 12 tests cover the
same ground.
