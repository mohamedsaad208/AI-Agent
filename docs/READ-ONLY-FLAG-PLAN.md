# Read-only as a flag — the request, measured

The request: *there is something called read-only mode — make it a flag and finish it.*

Two readings were possible and one of them is refused outright.

| reading | verdict |
| --- | --- |
| Put Read-only behind an opt-in switch, off by default, so it can be shipped dark | **Refused.** Read-only is not a feature someone waits for, it is the promise that keeps a sensitive folder unwritten. A default-off gate on it makes the *writable* path the default, which is the opposite of what the mode is for. The ladder already ships and 1403 tests hold it. |
| Make the folder's position a **durable declaration** — one stored fact every surface and every process reads, instead of a badge state inside one window | **Built.** This is the item `docs/READ-ONLY-MODE.md` §*Deliberately not built* left open on purpose ("a read-only flag on disk that a second process cannot ignore"), and measuring today found the concrete damage that choice was carrying. |

## What already exists (and what did not)

Measured against the tree on 2026-09-30, after the queue dump in the conversation.

| claim | measured |
| --- | --- |
| Read-only is only a window state | **True, and worse than that.** The web window persists per-folder positions in `ui["composer"]` (`controller.py:3362`) and Tk persists one global bool in `ui["read_only"]` (`gui.py:1293`). **Both** `_save_state` bodies rebuild the whole `ui` block from a list of named keys, so the Tk window's next save deletes the web window's map — and a folder whose position was deleted falls back to `_branch_mode`'s default, `CHAT`… no: `_grant_folder` (`:1164`) hands a folder with no stored row **Change**. So opening the desktop window once silently re-armed writing on every folder the web window had left in Read-only. That is the bug this round exists to fix, not the missing feature. |
| `git_branch` and `git_restore` are ungated writes | **True.** `controller.py:2526` runs `checkout -b` / a branch switch — which rewrites tracked files — and `:2488` replaces real bytes with `git restore`. Neither asks `reading_only()`. The badge says *writes nothing* while both are clickable. |
| `canMutate` is a hole in the same list | **False — my own queue dump was wrong.** `canMutate` (`:3307`) drives exactly one control: `verify.disabled = !r.canMutate` (`app.js:1489`), the **Check syntax** button. A static check executes nothing from the project, and `docs/READ-ONLY-MODE.md` lists it as *allowed* in Read-only. Filtering it would have broken a promise the mode is supposed to keep. Left alone. |
| The headless CLI ignores the mode | **True.** `cli.py` has no reference to `intent` at all. `agent apply <session>` writes into a folder the operator set to Read-only in a window; nothing says so. |
| Auto-Apply as a fourth position | Still refused, still a switch on top of `change`. |

## Shape

`src/ai_code_engineer/modes.py` — one file, one purpose: **the position a folder keeps when no window is
looking at it.** `.agent-modes.json` next to the app, `{"modes": {<project_key>: {"mode","by","at"}}}`,
written through one helper and read by a `(mtime_ns, size)`-cached reader — the same idiom the session
cache already uses, because a gate asks this on every snapshot and a snapshot goes out on every streamed
log line.

- `intent.py` keeps owning what the positions *mean* and every sentence. It gains three: a refusal that
  names the declaration and who made it, the command that lifts it, and the line the window prints when
  the badge has to follow a declaration that arrived from elsewhere.
- Both windows **write through** the store and read it at folder-selection time, so the badge and the
  fact cannot diverge on open. `reading_only()` in each window becomes *badge OR store*, which is what
  catches a declaration made by the terminal while the window is open.
- `ui["composer"]` and `ui["read_only"]` stop being written. Their rows are adopted into the store once
  at load (the Tk bool applies to the folder it was saved with, not to every folder — that was the
  second half of the same design error).
- The CLI gains `agent read-only [--repo PATH] [--off] [--arabic]`: declare, lift, and list.
  `plan`, `apply` and `rollback` refuse a declared folder with the sentence naming the command that lifts
  it. `map`, `review`, `status`, `verify` and `export-session` keep working — reading is what the mode
  allows, and a command the operator typed themselves *is* the explicit yes the mode asks for.

## Build order

1. `modes.py` + `tests/test_modes.py` (the store, the cache, a corrupt file, an empty folder).
2. `intent.py`: the three new sentences + the drift-guard table in `tests/test_intent.py`.
3. `controller.py`: write-through, read-at-select, `git_branch`/`git_restore` gates, the `declared`
   snapshot field.
4. `gui.py`: the same store behind the checkbutton, the bool gone, refresh on folder change.
5. `cli.py`: the `read-only` command and the three refusals.
6. `fake.py` + `app.js`: the lock on the badge, so the preview can be reviewed with it.
7. `.gitignore`, docs, README row.

## What will break when this lands

`tests/test_host.py`'s ceilings sit **exactly** at their limits for `controller.py`
(`self.status = ` 89, `self._add(` 33, `self._emit(` 21, `self._note(` 9), and Tk's
`messagebox.` is 6/6 — every new refusal goes through `say()`/`line()` or it fails the ratchet.
`tests/test_webapp.py`'s top-level-key contract fails unless `fake.py` answers `declared` in the same
commit, and `test_doubles` pins the snapshot key set. `test_the_saved_block_keeps_only_named_keys`
(the onboarding guard) is the one that will object to the store being read at load rather than kept in
`ui` — that objection is correct to expect, and the answer is written here.

## Deliberately not built

- **A per-call `--allow-write` override on the CLI.** A refusal you can rename your way around is not a
  refusal; lifting happens by name, on the folder, with `agent read-only --off`.
- **Sealing the whole `ui` block against cross-surface clobber.** `style`, `theme`, `queue` and
  `auto_apply` still get erased when the other window saves. Only the position is durable, because only
  the position changes what a process is allowed to do to your files. `auto_apply` is the one to watch
  — it loses in the safe direction (off), which is why it was left.
- **A file watcher**, so a terminal declaration reaches an open window's badge immediately. The gate
  reads the store live and refuses; the badge row is what waits for a re-open.

## As built — one precedence rule I got wrong twice, and two defects a live run caught

**1403 → 1450 offline tests**, suite green, `node --check` clean, `tools/scan_asi_strings.py` clean on
the static files. The first draft of this line said 1487, written from an addition of the tests I
believed I had authored while the suite was still running; the measured number is 1450, and the count
of a round is a measurement like any other, so it goes in last.

**What shipped.** `modes.py` (the store, 15 tests of its own), three new `intent` sentences with their
Arabic twins, `agent read-only [--repo] [--off] [--arabic]`, and gates that read the store: the two git
writes, the three CLI writes, both windows, and the preview.

**The precedence rule, twice.** Build order §Shape said the store answers "what was this folder told to
be", and the first cut made `_branch_mode` prefer *any* stored row over the window's own map. Two
existing tests failed on the same sentence — *a hand-picked Chat has to survive a restart* — and they
were right: a stored `change` outranking a remembered `chat` turns "the tool remembers your choice"
into "the tool overrules it". The rule that survived is one-directional:

| stored row | window map says | the folder opens on |
| :--- | :--- | :--- |
| `read` | anything | **read** — a seal outranks everything, because it is the only row that promises *less* |
| `change` | `chat` | **chat** — Chat is not a promise about files, and overruling a choice nobody made is not what a store is for |
| nothing | `chat` / nothing | as before this round |

So `ui["composer"]` stayed, with its job narrowed to *this window's memory including Chat*, and the
durable file carries the promise. The claim in §Shape that both `ui` keys stop being written was wrong;
`read_only` in Tk's block stayed too, re-described as the window's starting position rather than the
folder's fact.

**Two defects only a live run would show.** Driving the real command (`agent read-only --arabic`)
printed `??????` — a cp1252 console cannot carry Arabic, and `run_setup` had the four-line fix while
the new command did not. Now `cli.speak_arabic()` is called by both. And the listing printed
`c:\users\adam\appdata\...`, because the *key* is normalised for matching — lower-cased on Windows — so
the row now also carries the path as the file system spells it, and the listing reads like a person's
own folders.

**What the drift guards did.** `tests/test_host.py`'s ceiling caught the one new `self.status = ` in
`set_composer` (90 > 89) and it went through `say()` instead; `test_intent`'s copy guard passed because
every new sentence landed in `intent.py` first, and the five new callables are in its `SENTENCES` table
so a window that rewords one fails the suite. `ThePreviewKnowsTheThreePositions` is where the lock's
scripted half is pinned, next to the refusals it draws.

**Honest limits of this round.**
- The declaration is per folder, matched by `project_key`, so **moving or renaming a folder on disk
  leaves its seal behind at the old path** — the new path opens unwritten-about. Nothing was built for
  that, and it is the same identity rule the granted-folder registry already uses.
- A seal written by one window is read by the other **when the folder is selected**; the gate itself
  reads live, so a write asked for after a terminal seals is refused immediately, but the second
  window's badge keeps saying what it was told until that folder is opened again. `declared.note` is
  what covers that gap on screen.
- `modes.forget` is the only deletion, and a folder nobody declared answers as *nothing was said* —
  which is Change for a granted folder. The store protects folders that were told; it cannot protect
  one that never was.

