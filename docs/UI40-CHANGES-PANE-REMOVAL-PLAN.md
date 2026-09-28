# UI 4.0 — delete the Changes pane, put the viewer beside the chat

**Shipped 2026-09-28** — see `IMPLEMENTATION-STATUS.md` for what landed where. Asked as a removal, not a
refactor: "عندي مشكلة في صفحة ال changes / ملهاش لازمة وينفع نستغني عنها / عايز أشيلها خالص /
هنستبدلها ب viewer جانبي عشان ماخرجش برا الشات خالص". Decisions 1–5 below were answered with "نفذ", which
in this project means the recommendation stood: web only, modal sheet, Apply in the rail card, Activity
stays a pane, and the word "Changes" survives in sentences while losing its tab.

Baseline: **796 offline tests green**, `node --check` clean.

## What the pane is today

`index.html:92-101` — `<section class="view" id="view-review">` holding four ids that only it uses:

| id | built by | what it is the only home of |
| --- | --- | --- |
| `#banner` | `renderReview()` `app.js:1167-1179` | the **Apply changes** button, and the only *visible* Check syntax / Roll back pair |
| `#files` | `app.js:1181-1189` | a full file list with `+add / −del` counts per file, and the `on` row marking `review.selected` |
| `#dtabs` | `app.js:1192-1198` | the four tabs diff / **before** / **after** / **checks** |
| `#dcode` | `app.js:1199-1205` | the diff body at pane width |

Selected by `markTabs()` (`app.js:1232-1236`), the `Changes` tab in `#seg` (`index.html:72`), the rail's
`Review proposed files ↗` link (`app.js:984-986`), the deep link `?view=review` (`app.js:1676`), and three
server emits: `controller.py:1491` (a proposal is ready), `:1645` (a chat block became a proposal), `:2110`
(a fix round produced a proposal).

## What already exists without it — the reason the pane feels pointless

- **The side viewer is already built.** `renderRail()` shows one row per file (`app.js:971-978`) and
  `railPreviewCard()` (`app.js:1122-1165`) renders badge, path, `+add/−del`, three tabs (Diff / Now / Was),
  the body, `✕ Close preview` and `↩ Roll back`. That is the viewer the request asks for, in production today.
- **The chat already reaches it without leaving Chat.** `openFile()` (`app.js:1077-1089`) takes the rail branch
  whenever the rail is on screen — measured live: clicking a chip sets `state.railFile` and renders the
  preview, without touching `state.view`.
- **Every action in the banner is reachable elsewhere.** The command palette (`app.js:1588-1591`) already
  carries "Run the project command", "Run and fix", "Check syntax", "Roll back changes" and "Apply changes";
  the artifact card carries Roll back (`app.js:965-968`) and the rail preview carries Roll back too.
- **`DATA.review` is not the pane.** The chips, the rail rows and the preview all read the same snapshot
  block (`controller.py:2358-2396`). Deleting the pane deletes **no data and no server logic**.

So the pane's remaining unique value is three things, and the plan keeps each one deliberately: the Apply
button in a visible place, the `checks` tab, and *an answer at all* below 1180px where `app.css:685-689`
sets `.rail { display: none }`.

## Build

1. **Remove the pane and its tab.** `index.html`: drop `#view-review` (92-101) and the `Changes` button
   (line 72). `app.js`: delete `renderReview()` and its call (`:169`), drop `'review'` from `markTabs`' list
   (`:1236`), delete the `Review proposed files ↗` link (`:984-986`), and keep `state.view` for
   `task`/`details` only.
2. **Move the three actions into the rail's artifact card** (`renderRail`, `app.js:953-968`): `Apply changes`
   (`solid`, `disabled=!r.canApply`), `Check syntax` and `Roll back` (`line-btn`, `disabled=!r.canMutate`) —
   the same enablement rules the banner used, same `send()` verbs. The palette keeps its copies; a duplicated
   *action* is fine, a vanished one is not.
3. **Give the preview the pane's two missing tabs.** `railPreviewCard`'s tab list (`app.js:1126`) becomes
   `Diff / Now / Was / Checks`, reading `r.view.checks` — the text the server already sends
   (`controller.py:2404-2409`) and the rail does not draw today.
4. **Retarget the "look at this" intent instead of deleting it.** The three emits become
   `{"kind":"view","value":"preview"}`, and the client's `case 'view'` (`app.js:134`) handles `preview` by
   `state.railFile = 0; renderRail()` — a proposal that arrives still shows itself, but inside Chat.
   `?view=review` is accepted as an alias for `preview` (a bookmark must not dead-end); the `Changes` label
   disappears everywhere else.
5. **Answer narrow widths without a pane.** Below 1180px the chip currently calls `switchView('review')`
   (`app.js:1084`); with the pane gone that branch opens the same viewer in a **modal sheet** (`modal()`,
   `app.js:1245+`, already used by every dialog) built from the same `railPreviewCard` markup. Chat stays
   where it is, the sheet closes back onto it.
6. **The chips stop being rail-only.** `chipCard` keeps its `↗ View` label; the rail rows stay; the third
   surface (`#files`) is gone, so `openFile` and `kindTag` lose one caller and keep their contract.

## What does NOT change (invariants to keep)

- `snapshot()["review"]` keeps every field, including `selected`, `tab` and `view.checks` — 25 assertions in
  `tests/test_webapp.py` and 16 in `tests/test_controller.py` read that block, and the rail is a consumer of
  it, not a copy of it. No server-side change except the three `view` emit values.
- "You always review first" still holds: Apply stays a click (`solid` button, `disabled` unless
  `canApply`), and D31's rule (a delete never auto-applies) still needs a visible place to click — which is
  what step 2 provides.
- `select_file` / `select_tab` stay the server's actions; the rail already calls them.
- The `checks` text stays server-built and language-aware; the client keeps supplying glyphs only.

## Tests

- Rewrite deliberately, with the reason in the diff: the `TheWriteCardAndTheChips` / pane-shape assertions in
  `tests/test_webapp.py` that pin `#view-review`, `renderReview()` and the `Changes` tab become assertions
  that they are **gone** and that the rail carries Apply/Checks instead — a removal needs a test that the
  removal is the current shape, or the next refactor "restores" it.
- New: `preview` opens the rail without changing `state.view`; `?view=review` still opens something; the
  Checks tab renders `view.checks`; the rail's Apply is disabled outside `WAITING_APPROVAL`; the narrow
  branch opens a sheet rather than calling a pane that no longer exists.
- Server: the three emit sites assert `value == "preview"` (`test_controller.py` has 16 `review` references;
  those that assert the *data* block stay untouched).
- `node --check` on `app.js`; full `discover` and each touched module alone. Baseline 796 → expected ~804.

## Tk window — an open question, not an assumption

`gui.py` has the same three-view shape (`ViewStack` at `gui.py:89`, the `Changes` tab built at `:419-429`,
`"Review proposed files ↗"` at `:385`, `select_view("review")` at `:450`). Its Changes tab is a full
`ttk.Frame` with no side rail at all — the desktop window has **no** side viewer to move into, so the same
move there means building a PanedWindow sidebar first. Recommendation: web-only this round, Tk as its own
item, because the request came from the web window the user has been reading history in.

---

## Open decisions

1. **Tk too, or web only?** (recommended: web only now, Tk a separate item — the desktop window has no rail
   to receive these controls, so it is a rebuild, not a move)
2. **Narrow widths:** modal sheet for the viewer (recommended, uses `modal()` and keeps Chat in place) or an
   always-visible narrow rail column (needs a grid change under 1180px and squeezes the chat)?
3. **Apply's place:** in the rail artifact card (recommended — visible, one click, same `send('apply')`) or
   only in the command palette (fewer pixels, but the primary CTA becomes undiscoverable)?
4. **`Activity` stays a pane?** Removing Changes leaves two tabs, Activity and Chat. Recommended: keep both as
   panes — Activity is a log, not a viewer, and merging it into the chat is a different argument.
5. **The `Changes` word everywhere else:** the drawer's "Changes" section header, the palette's "Roll back
   changes", the `~ Modified` badge. Recommended: keep the vocabulary in sentences, drop it only as a *tab
   name* — renaming the app's nouns is a bigger question than deleting a pane.

---

## As built — two additions and one finding

Step 2 was written as "move the banner's three buttons", and the banner turned out not to be their only
home: the artifact card also carried a **Roll back inside the auto-write sentence** (`<button data-undo>`).
With `changeActions()` on the same card that made two undo controls in one card, one of them wearing the
enablement and one not, so the inline button and its CSS went — the note is a sentence now, the bar under
the file list is the control.

`fake.py` had **no `canRollback` at all**. Nothing read it while the pane owned the button, so its absence
was invisible; the moment the rail grew a Roll back, the scripted window — the one the design is reviewed
in — would have shown a permanently disabled escape. `test_the_snapshot_still_sends_every_field_the_viewer_reads`
pins the field list on both controllers, and the fake derives its three flags from
`labels.MUTABLE_STATES`/`labels.INTERRUPTED_STATES` rather than a hand-copied set.

Live check, read-only, on the real folder: history session opened, chip → sheet, Checks tab drew the last
`mvn -B test` verdict, `Apply off / Check syntax on / Roll back on` for a change already on disk, and
`state.view` never left `task`. Closing the sheet had to be fixed to repaint the rail, or the dismissed
preview stayed in the hidden rail's DOM and reappeared when the window widened.
