# UI 2.8 plan — branch-owned context, sidebar-only control

Status: **implemented 2026-09-25**, shipped as UI 2.8 — see
[Implementation status](IMPLEMENTATION-STATUS.md). Both open choices below were taken as the
recommended option: a project branch opens in Chat mode, and a stale `last_project` is ignored.

## The problem this fixes

1. `.agent-projects.json` stores one global `last_project`, and `AgentController.__init__`
   (`webapp/controller.py:129-131`) auto-attaches it on every launch. There is exactly one
   folder the whole program looks at, and it survives restarts whether you want it or not.
2. `start_plan()` (`controller.py:542`) branches on `if not repo`. So plain chat is not a mode —
   it is the *absence* of a project. `new_task()` (line 1071) resets everything **except**
   `self.repo`, so "New chat" inherits the last folder.
3. Consequence: typing "HI" with `examples/demo_repo` attached enters the propose loop. The
   engine system prompt (`engine.py:21`) offers no prose answer — only `propose` or `blocked`. The
   model proposes a file it read verbatim, `prepare_changes` rejects it (`engine.py:245-246`), the
   model repeats, and after `MAX_BLOCKED_RETRIES = 2` the run dies with
   `Model could not produce a proposal: Proposal contains an unchanged file: calculator.py`.

## The rule

`self.repo` becomes **derived** from the selected branch. It is written by exactly one function,
`_select_branch()`, and never inferred from a remembered global.

| Branch kind | Folder | Composer | Context |
|---|---|---|---|
| `chat` (loose) | none | **Chat** | none — `CHAT_SYSTEM` as today, no tools, no proposal |
| `chat` bound to a project | its folder | **Chat · folder** | read-only repo map + standing notes injected, still no tools, no proposal |
| `project` | its folder | **Chat or Change · folder** | full reviewed propose → approve → apply → run path, unchanged |

UI 2.9 added a third thing per project, in the node's **⋯ → Project settings & status**: a drawer
holding the resolved root, its notes, its measured context budget and its toolchain. It reads a
project without selecting it — opening a drawer never moves `self.repo`, which `_select_branch` still
owns alone. See `docs/ENHANCEMENTS-PLAN.md` §1.

Which mode a project branch opens in is decided by *how you got there*, and the badge always says
so. **Granting a folder** (`＋ open`, `＋ new`, opening a saved task, or any programmatic `set_repo`)
selects **Change**: naming a folder to work in is already a decision to build, and the conversation
says so — "Send proposes a diff, nothing is written until you click Apply". A branch **restored at
launch** and a chat **bound by drop** open in **Chat**, so the window never starts in a mode that
turns a greeting into a rejected diff. Attaching a plan selects Change for the same reason as
granting a folder. Once you choose a mode for a project, that project comes back in it.

## 1. Branch state (`controller.py`)

```python
self.branch = {"kind": "chat", "id": <chat_id>}
# or
self.branch = {"kind": "project", "key": <project_key>, "id": <chat_id>}
```

- `_select_branch(kind, key=None, id=None)` is the only writer of `self.repo`,
  `self.current_project`, `self.plan_file`, and the `last_branch` pref.
- `New chat` appends a loose chat branch and selects it. It does **not** close the open project:
  the project node stays in the tree, so "New chat" stops being a hidden workspace switch.
- `New project…` and `Open a folder` create/select a project branch; the sidebar is where both
  live, matching "all control in the left bar".
- Opening a saved session selects its project branch. Opening a loose chat forces `repo=""`
  (already the behaviour asserted by `open_chat`, line 1016) — and that guard now holds by
  construction instead of by a reset.

## 2. Per-chat project binding (`chat.py`)

`chat.json` gains one field: `"project": null` or `{"key": <project_key>, "path": "<abs>"}`.

- `create_chat(model, chat_id, project=None)`; schema stays `1`, and a missing key reads as `null`,
  so existing chats in `.agent-chats` load unchanged with no compatibility shim.
- A chat created inside a project branch records that project, so it renders under the project node.
- A chat created by `New chat` records `null` and stays independent: no folder, no injected context.
- Context is still per-chat (`_messages` walks only this chat's turns) and per-project-folder for
  tasks (`engine.chat_context`, which needs both the same repo root **and** the same chat id).

## 3. Drag a loose chat into a folder

Gesture: drag a chat leaf from **Chats · no project** (or out of another project) and drop it on a
project node in the same sidebar.

- `app.js`: `draggable` on `.leaf`; `dragstart` carries `{kind:"chat", id}`; project `.node`
  handles `dragover`/`drop` with a visible drop ring, and rejects drops on a node whose key already
  matches. `index.html`/`app.css` get the two states (`.droppable`, `.drop-on`).
- One action: `send("bind_chat", {"chat": <id>, "project": <key>})`; `{"project": null}` detaches.
- **Security:** the payload carries a **project key**, never a path. `bind_chat` resolves it through
  `self.projects` — the registry of folders the user already granted — and refuses an unknown key.
  A browser cannot aim a chat at an arbitrary directory.
- **Safety:** binding only adds read context. It never turns a chat into a proposal path, and
  detaching keeps every past turn.
- **Accessibility:** drag alone is not enough, so the same command exists as a leaf menu —
  **Move to project… →** (reusing the existing `choose()` sheet) and **Detach from project** — plus
  a `Ctrl+K` entry. The drop is the shortcut, not the only door.
- After a bind, one line lands in the conversation:
  `Now reading <folder> as context. Still chat mode — nothing is proposed.`

## 4. Chat with context (`chat.py`)

`_messages(chat, settings, context=None)` and two system variants:

- unbound → today's `CHAT_SYSTEM`, verbatim ("NO access to any project, file system, or tools").
- bound → it may *see* parsed declarations the tool read for it, and still **cannot** open, write,
  run, or propose: "ask the user to switch to Change mode if the answer requires edits".
- Injected, with the same untrusted labels the engine already uses:
  `Repository context (untrusted, read-only): <Workspace.repo_map()>` and
  `Standing notes for this project: <memory block>`.
- Budget: `context_chars // 3` (the snapshot budget in `engine.py:323`); `symbols.render` already
  caps the map at 12 000 characters.
- The map is built inside the worker thread (`run_job`), never on the UI thread, and any
  `PolicyError`/`OSError` degrades to "no context" rather than failing the chat.
- `provider.generate(..., json_mode=False)` in both cases: there is no action envelope at all.

## 5. Composer and header

- Mode badge in the composer pill row (`app.js:266`): `Chat` / `Chat · demo_repo` /
  `Change · demo_repo`, clickable to toggle `Chat ⇄ Change` for the current branch only.
- `_subtitle()` (line 1171): loose branch → `Standalone chat — no folder attached`; project branch →
  folder · mode · model, as today.
- `Try the sample project` keeps its prefilled edit request and selects **Change**, because that task
  genuinely changes `calculator.py`.
- The `Workspace` chip in the pill row stops opening a file dialog: it becomes a read-only label of
  the current branch, and `pick_project` / `new_project` / `clear_project` live only in the sidebar.

## 6. Persistence (`.agent-projects.json`)

`last_project` + `last_chat` collapse into `last_branch: {"kind", "key"?, "id"}`.

- On launch: a `chat` branch restores no folder — the program starts with nowhere to write.
- A `project` branch restores its folder, still gated on `Path(...).is_dir()`.
- `last_project` is **not** read on the next launch. An existing registry file keeps its `projects`
  list (so the sidebar still shows every granted folder); the stale `last_project` key is simply
  ignored and rewritten as `last_branch` on the next save.

## 7. Error wording (`labels.py:friendly_error`)

- `Proposal contains an unchanged file` / `Model could not produce a proposal` on a project branch →
  `Your message did not ask for a file change. Switch the badge next to Send to Chat and ask freely.`
- `Turn budget exhausted` on a project branch → same pointer, plus the existing smaller-task advice.

## 8. Tk fallback window (`gui.py`)

Only the two defects that matter get fixed there, so the fallback cannot reintroduce the bug:
`new_task()` clears `self.repo` (line 1103), and `last_project` is not auto-applied (lines 235-237).
Drag, the sheet menus and the badge stay web-only. The bound-context behaviour is free there, because
it lives in `chat.py`.

## 9. Tests

`tests/test_controller.py`
1. `New chat` while a project is open → `repo == ""`, `respond` reached with `json_mode=False`, the
   new `chat.json` has `project: null`, and the project node is still in the tree.
2. Creating a project while a loose chat is open → project branch selected, the loose chat untouched.
3. Opening a chat bound to a project → its folder selected, `_messages` contains `Repository context`,
   reply is prose.
4. A loose chat's `_messages` contains **no** `Repository context` and no folder anywhere.
5. `bind_chat` with a known key → written to disk, and the leaf moves under that project in
   `_nav_projects()`.
6. `bind_chat` with an unknown key or a path-shaped payload → refused; nothing leaves the registry.
7. After binding, `send` still cannot produce a session: `self.session is None`, `json_mode=False`.
8. Detach → `project: null`, past turns intact, leaf back under **Chats · no project**.
9. Launch with `last_branch.kind == "chat"` → no folder attached.
10. Launch with `last_branch.kind == "project"` → folder + its last task restored.
11. A project branch that receives a non-edit message ends with the new Chat-mode hint, not raw
    `PolicyError` text.
12. Two chats in one folder: neither sees the other's context (tightens the existing `chat_context`
    rule to the bound-chat case).

`tests/test_webapp.py` — `bind_chat` over `/api/action` requires the per-launch token (403 without).
`tests/test_agent.py` / `tests/test_memory.py` — `_messages` budget with a full 12 000-character map,
and notes reaching a bound chat but not a loose one.

Baseline to stay green: `python -m unittest discover -s tests` → 192 tests, offline, synthetic model.

## 10. Out of scope

- Streaming replies, vector search, LSP, native tool calling — all still absent.
- No change to proposal hashing, apply/rollback, the plan ledger, notes storage, or the redaction and
  path policy. The security audit closures S1-S5 stay intact.
- Still one active branch and one writer at a time; no OS-level sandbox claim.
- Multiple folders open simultaneously is **not** added: isolation is per branch and per chat, not a
  multi-workspace tab strip.

## 11. Build order

1. `chat.py` — `project` field, `context=` in `_messages`, the two system variants.
2. `controller.py` — branch state, `_select_branch`, `bind_chat`, `new_task`, nav split, launch
   restore, `_save_state`, subtitle.
3. `labels.py` — the two new hints.
4. `webapp/static/` — drag/drop, badge, leaf menu, `Ctrl+K` entry, CSS states.
5. `gui.py` — the two minimal fixes.
6. Tests, then `python -m unittest discover -s tests`, then drive the window live and screenshot the
   sidebar, the drop ring, the badge toggle, and a bound chat's answer.
7. `README.md` + `docs/IMPLEMENTATION-STATUS.md` as **UI 2.8**, and this file marked implemented.

## 12. First-use fixes, same day

Driving the real window on the user's own machine turned up five things the design section above did
not cover. All five are fixed and verified in the page, not only in tests.

1. **A long thread pushed the composer off screen.** `.main` is a grid item, and a grid item's
   automatic minimum is its content height, so once the conversation grew the column outran
   `100vh`; `body{overflow:hidden}` meant there was no scrollbar anywhere, and the text being typed
   sat below the visible area. `min-height: 0` on `.main` puts the scroll back inside `.scroll`,
   where it was always meant to be. The composer textarea got `overflow-y: auto` for the same reason.
2. **The folder picker could not leave C:.** It starts at the home folder and walks up with `..`,
   but `C:\` has no parent, so the other disks were unreachable — and a project folder that does not
   exist yet cannot be clicked into at all. `list_dir` now returns `roots` (mounted drives via
   `GetLogicalDrives`, filtered to the ones that answer), the picker shows them as chips once it
   reaches a drive root, and it has a path field: type or paste `D:\AI\spring-project`, press Enter
   to browse it or click **Use this path** to take it as the answer. `＋ new` starts the picker in
   the current project when there is one, and creating a project from a typed path is tested.
   Opening a path that is not a directory is refused with a status line rather than adopted.
3. **The project's folder is now visible where you type**, on a pill next to the mode badge, with
   the full path as its tooltip and a click that copies it. The sidebar node carries the same path
   plus a hint that dropping a chat there binds it.
4. **Granting a folder selects Change mode.** Chat-by-everywhere made "create me a Spring project"
   into a prose lecture, which is the opposite of what the tool is for. Naming a folder to work in —
   `＋ open`, `＋ new`, opening a saved task, or `set_repo` — is treated as the decision it is, and the
   conversation says what will happen. A launch-restored branch and a chat bound by drop still open
   in Chat, so the greeting case the whole release started from still holds.
5. **A cut-off answer now says so.** `Model output truncated` reaches `friendly_error` as prose the
   user can act on: ask for one file at a time, or raise the output limit in Settings. (Repetition
   garbage inside the token budget is a model-quality limit, recorded in `MODEL-BENCHMARK.md`, not a
   rendering bug — the scroll fix above is what made the rest of the reply reachable.)

**Verification:** 226 tests pass offline (5 new: drive roots in the listing, a typed path creating a
project outside the home drive, refusing a non-directory, Change mode plus its notice on granting a
folder, and the truncation wording). The page was driven again with the scripted model: a project
created by typing `D:\AI\AI-Agent\sandbox\picker-demo`, the path pill and `Change · picker-demo`
badge on screen, `C:\` showing `D:\` and `E:\` chips, and a thread of 1 631 CSS pixels inside a 246
pixel scroller that reaches the bottom and keeps the composer in view.


## The two choices, and what was built

1. **A project branch opens in Chat mode** when it is restored or bound; **Change** when the folder
   is granted — see §12. The mode is remembered per project either way.
2. **A stale `last_project` is ignored.** The window never starts pointed at a folder; the granted
   list is still restored so every approved folder stays in the sidebar.
