# Read-only mode — the plan, measured first

Spec (item 4, phase 3): an explicit ladder `Chat only / Read-only analysis / Reviewed changes /
Auto-Apply`, where Read-only may read the project, search the code, build the repository map and
explain problems, but creates no proposal and runs no command without a clear choice — for reviewing
a sensitive or production folder.

## What already exists (the two premises that turned out wrong)

| claim in the spec | measured state |
| --- | --- |
| "There is no read-only position" | **Partly false.** A *bound* chat is already read-only in substance: `controller.py:1048` forces `composer = CHAT_COMPOSER` when `branch["bound"]`, `set_composer` refuses Change for it (`:1095-1099`) with the sentence "A chat moved into a project reads that folder only", and Auto-Apply is forced off for it (`:1058-1059`). What is missing is the **name**, and one behaviour: an imperative message in that state is still planned as a proposal (`as_change`, `:1715`). |
| "The ladder is four modes" | **Three positions exist, in two of them.** `chat` / `change` are the composer value (`CHAT_COMPOSER`, `CHANGE_COMPOSER`, `:95`), and **Auto-Apply is a per-folder switch on top of `change`** (`set_auto_apply`, `:1109-1138`, `_auto_pref[key]`), not a fourth mode: it removes the click after a proposal lands, and it still refuses to empty a file or to write a code block you clicked. Making it a mode would duplicate a switch that already persists per folder. |

So the build is: **one new position, `read`**, with the bound-chat rules made explicit, and Auto-Apply
left where it is. Chat mode keeps its "a message that asks for files is planned as a proposal"
behaviour (pinned by `test_controller` §"Answers in prose when asked for prose") — refusing that *is*
what distinguishes Read-only from Chat.

## What Read-only refuses, and where

Every refusal is one shared rule with one shared sentence, so the two windows cannot disagree.

| action | today | read-only |
| --- | --- | --- |
| Send | prose, or a proposal when the message asks for a change | analysis answer built on the repository map + project notes; **never** `plan()`, so no proposal exists |
| Apply / Auto-Apply | writes after a click (or by itself when the switch is on) | refused in `apply()` and `auto_apply_ready()` — including a proposal left over from Change mode |
| Roll back | restores files after a click | refused: it writes to disk |
| Run the project command | runs on a click | runs only after an explicit yes that names the command; declining runs nothing |
| Run & fix / fix rounds | proposes the next diff | refused, and said: a fix round's output is a proposal |
| Read files, search, repository map, syntax-only static checks | allowed | allowed (static checks do not execute the project) |
| Auto-Apply switch | per folder | cannot be armed while the conversation reads; the folder's stored pref is untouched |

## Build order

1. `labels.py`: the three positions as data (`COMPOSER_MODES`) + the read-only sentences, English and
   Arabic, written once.
2. `controller.py`: `READ_COMPOSER`, `set_composer` accepting it, the gates above, and the snapshot
   fields the client needs.
3. `webapp/static/app.js` + `fake.py`: the badge as three positions with honest tooltips, the menu
   entry, the placeholder, and the scripted snapshot carrying every new field.
4. `gui.py`: the same three positions (a segmented row in the Checks card) and the same refusals, plus
   a repository-map context for its analysis answers so read-only can actually see the project.
5. Tests: mode persistence and every refusal in both windows, the run-confirm path declining *and*
   accepting, and a drift check that neither window words the refusal itself.

## Deliberately not built

- Auto-Apply as a fourth radio button (it is a switch, and it already persists per folder).
- ~~A read-only flag on disk that a second process cannot ignore — the refusal lives in the window that
  runs the command, same as every other gate here.~~ **Built six days later, against this reasoning.**
  Measuring found what the choice cost: the position was stored inside `ui` of `.agent-projects.json`,
  which each window rebuilds from its own named keys, so opening the desktop window once deleted every
  folder the web window had left in Read-only — and a folder with no stored row is granted on Change.
  See `docs/READ-ONLY-FLAG-PLAN.md` for the declaration, `modes.py`, and `agent read-only`.
- A "sandbox" that makes reads safe from a malicious project: reading is already confined to the
  approved folder by `Workspace`, and this mode adds no new reach.

## Shipped

`intent.py` is the new module: three positions, and one copy of every sentence each of them says.

**Two corrections to this plan, found while building it.**

1. *Build order §1 said `labels.py`.* It is its own file instead. `labels.py` is the status-text
   module (1 100 lines of `status_text` keys); the intent axis is a different thing, and putting it
   there would have made "which module owns what Send may become" unanswerable.
2. *Build order §4 said "the same three positions" for Tk.* **Tk has no positions at all.** Its
   `self.mode` is the provider picker, and its rule has always been "a folder named → `plan()`; no
   folder → chat". So there was nothing to add a third value to, and building the whole badge in Tk is
   a different item. What shipped there is the one rung Tk can hold on its own: a **Read-only
   checkbutton** beside Send, greying Apply / Roll back / Run & fix and refusing the same six things,
   with the analysis answers now carrying the repository map and the project notes (Tk's chat had
   never passed a `context=` at all, so read-only there would otherwise have been blind).

**A defect found on the way, in the same file.** The bound-chat tooltip at `app.js:935` was assembled

```js
b.title = 'A chat moved into … proposal you '
      'approve, but the mode and Auto-Apply …';
```

with no `+` on the second line. ASI makes that two legal statements — `x = 'a'` then `'b';` — so
`node --check` passes, the sentence ships ending mid-word, and the second half is dead code. Fixed,
and `tools/scan_asi_strings.py` now walks the static files for that shape (it finds exactly this one
case in `app.js`, `boot.js` and `index.html`). A syntax checker cannot see this class at all; that is
why the scanner exists.

**What the gates ended up being** (`controller.py`, then `gui.py` with the same `intent` calls):

- `start_plan` decides the position **before** reading the message, so `asks_for_a_change` can never
  promote a read-only conversation into a proposal. The refusal is a transcript row, not only a status
  line: a status line is replaced by the next click, and "this conversation builds no proposal" is
  something the operator reads back afterwards.
- `apply`, `undo` and `offer_block` refuse by name; `_review()` reports `canApply`/`canRollback` false
  so the buttons are not drawn either.
- `run_tests` asks one question per command, naming the command (`runner.display_command`, extracted so
  both windows and the transcript line print the same string). Declining says nothing ran.
- A fix round is refused where the decision is actually made — `_offer_fix` in the web window,
  `ask_for_fix` in Tk — so the sentence appears after a real failure instead of as a warning about
  something that may not happen.
- Auto-Apply cannot be armed while reading, and `_select_branch` now disarms it for any branch that is
  not in Change mode. That last one was a hole: the pref was read back purely from `_auto_pref[key]`,
  so restarting a folder left in `read` would have re-armed the write switch under the refusal.

**Tests**: `tests/test_intent.py` (15) holds the axis and the drift guard — it fails if either window
writes one of the thirteen sentences itself, if a window stops exposing `reading_only`, or if a
character from a third alphabet lands in a translation (that bug is how this round's first draft of
`intent.py` arrived). `ReadOnlyModeTests` in `test_controller.py` (10) and six tests in
`DesktopTests` cover both windows gate for gate, `ThePreviewKnowsTheThreePositions` (5) keeps `--fake`
honest, and `TheCommandAPersonApproves` (3) pins the command line an operator agrees to.

