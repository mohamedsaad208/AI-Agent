# UI 3.1 plan — language-matched status, in-chat file chips, rail preview, honest Apply wording

> **Built the same day.** All four objectives shipped on the recommendations below; 525 → 565 tests,
> contrast PASS at 126 pairs, every front-end claim re-read from the running page. A fifth item
> arrived afterwards — the "↻ Again" control, see *Follow-up the same day* in
> `docs/IMPLEMENTATION-STATUS.md`. What the plan
> got wrong about itself: **decision D became unnecessary** — `changes[]` already stores each file's
> complete `before`/`after`, so the "Full File" tab needed no endpoint and no new HTTP read surface
> was added; **decision B was declined** — `gui.py` reads `STATES` directly and owns ~40 literals of
> its own, so the Tk window is documented English-only rather than half-translated. Two findings
> arrived while building: the `dir="auto"` rule could never resolve RTL for a tool row (its author
> label is the first strong run), and `self.status` in the web controller is a phantom surface —
> written in ~60 places, read by nothing but tests. Both are written up in `docs/VALIDATION.md` and
> in *UI 3.1* in `docs/IMPLEMENTATION-STATUS.md`.

Written 2026-09-26 from the four-objective specification, **after measuring the tree**. Each item
below says what the spec asked, what the code already did at the time with a `file:line`, and what
was actually missing — because three of the five asks were partly delivered already and two rested
on a premise that is not true of this repository.

## Baseline, measured

- `python -m unittest discover -s tests` → **525 tests, OK, ~100 s**, offline. The spec's
  "existing 402+" is the tree from two rounds ago.
- `tools/check_contrast.py` → 6 style/theme combinations, 108 pairs, PASS.
- The stylesheet is a **contract**: `tests/test_webapp.py:366-373` fails if `app.css` reads a
  `var(--x)` that `tokens.css` does not define.
- The `DATA` object is a **contract**: `tests/test_webapp.py:293-306` regexes every `DATA.<field>`
  in `app.js` and requires it in `FakeController().snapshot()`; `:307-315` requires the real
  controller to send the same top-level keys. A new field means three files, not one.
- The HTTP surface is a **contract**: `contract.py:17-37` fixes five methods, and
  `tests/test_webapp.py:137-142` asserts the only GET routes are the ones the client calls.

## Premise corrections — measured, not argued

| the spec says | the tree says |
| --- | --- |
| CSS on `--bg-surface`, `--border`, `--text-muted`, `--bg-card`, `--bg-hover`, `--green`, `--amber`, `--red`, `--font-mono` | **none of those nine tokens exist** (grep count 0 in `tokens.css`). The families are `--panel --field --line --ink-2 --faint --hover --add --del --ok --warn --bad --mono --chip --code`, and every themed name is repeated in all six style/theme blocks (`tokens.css:24-173`). Pasted as written, the round fails the suite on its first run. |
| `_applied` is "around line ~1260" | it is `controller.py:1275`; 1260 is inside `_checkpoint`. |
| `DATA.auto_apply` gates the placeholder | there is no top-level `auto_apply`. `snapshot()` sends `"auto_apply"` **inside** `settings` (`controller.py:411`), and `app.js` already reads it there (`autoPill()` at `:597`). Reading `DATA.auto_apply` would be undefined — the same class of dead gesture that killed the empty-**Send** shortcut last round. |
| `DATA.banner` is status text | it is an **int** (file count, `controller.py:415`); the sentence is hard-coded in `app.js:665-667`. So localising the server string alone leaves the rail card in English — the banner copy has to move server-side to be language-correct at all. |
| add a per-message file list | a message is `{role, author, text, time}` (`controller.py:370-371`) and `chat.json` persists only `{role, content}` (`chat.py:157,166`). Replay rebuilds messages by hand (`controller.py:1691-1696`, `:1724-1738`), so any per-message `files` field is **dropped on every reopen** unless the on-disk turn shape changes too. `session["changes"]` already carries `path/before/after` (`:1807-1811`) and survives. |
| "Full File" tab shows the file | **no endpoint returns a file's contents by path.** The routes are `bootstrap / fs / project / events / action / confirm` (`server.py:133-180`); `list_dir` (`controller.py:460-479`) returns names only. This is the one genuinely new server surface the spec needs. |
| replace "proposed" and "Verification incomplete" with "Applied & Saved to disk" | both strings are real and one **is** wrong: `_artifact`'s title is `f"{len(changes)} proposed file(s) in {root.name}"` (`:1799` area) and it says *proposed* after an Auto-Apply write, when the files are on disk. But `VERIFICATION_BLOCKED` → "Verification incomplete" (`labels.py:16`) is a different fact — the project's own command has not passed. Making that card say "Applied to disk" is truthful; making it say it *instead of* the verification line would delete the one signal that separates "written" from "proven". |
| ignore `Ctrl+K` while typing / when `Alt` is down | **confirmed, and it is worse than described.** The single window handler (`app.js:1158-1161`) checks neither `document.activeElement` nor `e.altKey` nor whether a modal is open, so `Ctrl+Alt+K` (AltGr+K on an Arabic layout, which types `÷`) opens the palette, and `Ctrl+B` inside the composer toggles the sidebar. Found additionally: `index.html:44` prints a `Ctrl K` hint on the **New chat** button while `Ctrl+K` opens the palette and `new-chat` is a plain click (`app.js:1143`) — the label promises a shortcut that does not exist. |
| stop the auto-firing `offerIcon` popup | it fires from `render()` on every state push (`app.js:89` → `:142-161`). Last round added a 400 ms deferral plus a branch re-check, which fixed the mid-click flash; it did **not** add a typing check, and the timer handle is not cancellable. So "quiet while typing" is still a real ask. |
| language-matched summaries already needed? | **partly delivered.** Two system rules already force the *model's* own output to the user's language — `chat.py:24-28` `LANGUAGE_RULE` (chat replies) and `engine.py:22-24` (write `summary`/`checks` in that language). Front end already carries `dir="auto"` (`app.js:503`). What is missing is **our** strings: every `_add("tool", …)`, every `self.status`, `labels.STATES/TONE/friendly_error` and all of `app.js` is English-only. There is also **no script-range test anywhere** — `ARABIC_MARKS` (`controller.py:100`) only strips diacritics, and `_words()` (`:108`) folds words to route modes; neither classifies a text. |

## The four items, sized

1. **Language-matched tool/status text** — new, and the cheapest of the four *if* it gets one home.
   A `script` test plus a two-argument sentence helper in `labels.py` (the file that already owns
   user-visible text), not f-strings spread through `controller.py`. Requires moving the banner
   sentence from `app.js:665-667` to the server, and it changes strings that **seven existing tests
   pin by content** (`test_controller.py:262,276,1145,1153,699-701,1324,1360`) — those assertions
   go Arabic-under-test and English-under-test, which is a change to test *content*, permitted only
   because the new behaviour is what we are asserting.
2. **In-chat file chips** — derived from `session["changes"]`, attached to the apply message, one
   click → the rail. No new persisted turn shape. Also fixes the dead `cursor:pointer` on `.tool`
   (`app.css:238-242`, no handler anywhere).
3. **Rail file/diff viewer** — `diffRows()` (`app.js:723-734`) is already a pure row model and is
   reusable as-is; only its DOM emitter is entangled with `#dcode` (`:771-774`), so the emitter gets
   factored out and called twice. Constraint nobody mentioned: **the rail is `display:none` below
   1180 px** (`app.css:570-573`), so a rail-only preview is unreachable on a narrow window.
4. **Honest Apply wording + dynamic placeholder** — small, and item 4's wording fix is a defect, not
   a preference.

Estimated: item 1 and 3 are the long ones; 2 and 4 are an evening.

## Decisions I need before building

**A. Where Arabic strings live.** Recommend: one helper in `labels.py`
(`say(arabic=…, english=…)`) chosen by a new `is_arabic(text)` over `\u0600-\u06FF`, and the banner
sentence sent from the server as text. Alternative in the spec: inline f-strings in `_applied`. I
would not do that — `repair.py`, `gui.py` and `controller.py` all emit user-visible sentences, and
the last round's whole lesson was *one rule, one place*.

**B. Does the Tk window go bilingual too?** It reads the same `labels` strings, so a `labels.py`
helper gives it Arabic for free. The spec only names the web window. Recommend: yes, since it comes
free with the chosen design, and a Tk window that answers Arabic prose in English status lines is the
same drift item 7/8 of the last review.

**C. "Verification incomplete."** Recommend: the card says **both** — `● Applied to disk` as the
headline, and the command state as its own line, never merged into a green "done". Option B is the
spec's literal wording, which replaces the verification signal with a disk statement. I read the
second as losing the project's safety argument; confirm and I will still do it if you want it.

**D. The new file endpoint for the "Full File" tab.** Options:
 (i) **only what this task touched** — the endpoint accepts a path that is already in the current
 proposal's `changes` list, resolved through `Workspace` (128 KiB ceiling `workspace.py:14`,
 `BLOCKED_PARTS`, the traversal/symlink/device rules at `:116-144`). Small surface, can't read
 arbitrary files.
 (ii) any path inside the granted folder — more useful for "open this file I didn't change", but it
 is a new read primitive over HTTP and `list_dir` already walks any absolute path with only the
 launch token in front of it, so this is the moment that gap starts to matter.
 (iii) no Full File tab — the rail shows the diff from the stored `before`/`after`, zero new routes.
 Recommend **(i)**, and if you want (ii) I'd gate `list_dir` in the same change.

**E. Rail preview when the window is narrow.** Recommend: a chip click opens the rail, and when the
rail is hidden by width it falls back to the existing Changes view for that file — one branch, no new
layout. Alternative: make the rail an overlay under 1180 px, which is a drawer redesign and belongs in
its own round.

## Build order, once answered

1. `is_arabic` + `labels.say`, banner sentence to the server, `artifact` wording, placeholder reads
   `DATA.settings.auto_apply` — one commit, its own tests, English path unchanged.
2. Keyboard guard + `offerIcon` idle/typing check + the false `Ctrl K` label.
3. `diffRows` emitter factored out; rail preview (diff from stored before/after); chip rendering.
4. The file endpoint from decision D, with contract + fake + route tests extended in the same commit.
5. CSS built only from existing tokens (plus new ones added to **all six** blocks if a state truly
   needs a colour), re-measured with `tools/check_contrast.py`.

Every step ends with the full suite, `node --check`, and the claims re-read from the running
`--fake` window rather than reasoned about from source.
