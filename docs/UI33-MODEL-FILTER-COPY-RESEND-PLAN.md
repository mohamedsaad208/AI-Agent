# UI 3.3 plan — fast model filter, a copy button on every message, resend on my own

> **Built the same day** (2026-09-26), with two changes to the request made while it was being
> built: the copy button went on the server's rows only, and the row-level **resend was dropped
> entirely** — `↻ Again` stays as it is. The shipped state, the four live measurements and the two
> defects the running page and a single-module test run exposed are in `docs/IMPLEMENTATION-STATUS.md`
> (UI 3.3) and `docs/VALIDATION.md`. The line numbers below were the baseline's.

Written 2026-09-26 from the user's request, in three parts: **فلتر سريع للبحث في الموديلات**
(a quick search over the model list), **زرار نسخ لكل رسالة من رسائل السيستم** (a copy icon on every
message/reply the server produces), and **لو الرسالة مني يكون في زرار resend لنفس الرسالة** (on a
message that came from them, a button that repeats that same message).

Baseline: 586 tests green (`test_controller` 125, `test_webapp` 51, `test_labels` 23). Every claim
below was checked against the tree first, with the line that proves it.

## 0. Three things the measurement turned up before any of this is built

- **The model filter that exists is destructive.** `filter_models()`
  (`controller.py:978-986`) ends with `self.catalogs[self.mode] = kept`, which *replaces* the loaded
  catalog with the matching subset — the dropped models are gone until **Refresh models** — and then
  `if self.model and not any(entry["id"] == self.model for entry in kept): self.model = ""`, so
  typing a filter that does not match your current model **deselects the model you are running**.
  Searching the list today costs you the list and your selection.
- **`askAgain`'s busy refusal is obsolete as of yesterday.** `app.js:589` refuses with "Wait for the
  running task, then ask again", and `tests/test_webapp.py:411` pins that string. It was written when
  the composer was `disabled` during a run. UI 3.2 made the box typable while busy and Enter queue —
  so the refusal now blocks something the app handles correctly. Changing it edits a pinned
  assertion, which is why it is called out here rather than done silently.
- **`--fake` answers 22 of the 42 actions the client sends; the real controller answers all 42.**
  Measured by extracting every `send('name')` / `sendQuiet('name')` from `app.js` and both dispatch
  tables: `set_filter`, `set_model`, `set_mode`, `refresh_models`, `set_draft`, `set_icon`,
  `set_consent`, `set_key`, `save_memory`, `new_chat`, `new_chat_in`, `new_project`, `example`,
  `open`, `pick_plan`, `clear_plan`, `reveal`, `bind_chat`, `apply_block`, `set_collapsed` are missing
  from the scripted window, and `fake.action()` falls through to `return None`
  (`fake.py:291-357`) — a **silent no-op**, not an error. That is exactly why the drift survived: the
  design review happens in `--fake`, and the filter box there does nothing at all. The `DATA.` field
  parity test (`tests/test_webapp.py:299-306`) covers reads, not actions, so nothing checks this.

## 1. Fast model filter — what already exists

- One filter input exists: `app.js:1323` *"Filter models by name"* in **Settings → Models**, wired to
  `send('set_filter')` (`app.js:1340`) → `controller.set_filter` (`:947-949`) → the destructive
  `filter_models` above. **Refresh resets it** (`:938`).
- The list you actually pick a model from is a different surface with **no filter at all**: the model
  pill (`app.js:631`) opens `choose('model', DATA.provider.model, DATA.provider.models)`
  (`app.js:1291-1304`), which renders every entry as a row.
- The Settings tab draws no model list either — its body is the input plus `model_info` for the one
  already-selected model (`app.js:1322-1324`). So today: type a filter in Settings, close Settings,
  reopen the pill, and see the effect.
- Size premise, measured: this machine's Ollama returns **11** models over `/api/tags`. The OpenRouter
  Free/Paid catalogs are the long ones (the README describes them as the full published list) and
  cannot be counted here without an API key, so the filter's value is mostly on the cloud lists.

**Shape:**

- Make the filter a *view*, not a mutation: keep the loaded catalog as loaded, compute
  `visible_models()` from `model_filter`, and serve that in `snapshot()["provider"]["models"]`.
  A filter never clears `self.model` — the selected model stays selected even while hidden, and the
  pill keeps showing it.
- Put the fast filter where the list is: a filter box at the top of the **Choose a model** sheet that
  matches as you type against `id` + name + description, **client-side** — a keystroke must not pay a
  round trip, and the sheet already holds every entry it needs. Show `N of M`, and Enter picks the
  first match. Same for **Choose a provider** only if it is free (3 entries — probably not).
- The Settings input keeps working, now non-destructively, over the same view.
- `--fake` implements `set_filter` / `set_model` / `set_mode` / `refresh_models` so this can be
  reviewed in the preview window it is reviewed in.

## 2. A copy button on every server message — what already exists

- Copy exists twice already, as a pattern to reuse: **Copy** on every code fence (`app.js:483`,
  handler `553-555`) and click-to-copy on the project-path pill (`615-617`), each finishing with
  `toast('Copied to clipboard')`. Both use `navigator.clipboard.writeText`, which works here because
  the page is served on `127.0.0.1` — a secure context. No new fallback needed.
- **There is no per-message copy.** `renderThread` (`app.js:521-568`) draws rows with no action strip.
- Messages are `{"role", "author", "text", "time"}` (`controller.py:506-509`) with no id, so the row's
  index in `DATA.messages` is the address. Append-only within a chat, and reloaded chats keep order.
- Constraint found in the same file: `appendToken` (`app.js:570-573`) rewrites the streaming bubble's
  `innerHTML` on every chunk, so **anything with a handler inside `.bub` loses it mid-reply**. The
  buttons therefore go on the row body, beside the bubble, and are wired once by delegation on
  `#thread` instead of per-render.
- What it copies: `m.text`, verbatim. For an assistant reply that is the markdown source — which is
  what you want to paste into a terminal or another chat, not the rendered DOM. For a tool row the
  text is already plain (no markdown is allowed in those, per the UI 3.1 rule and its test).

**Shape:** one small ghost icon per row — `⧉` — on the body edge, revealed on hover/focus and always
visible on the row that is animating in; `title`/`aria-label` say what it does; the row it copied is
confirmed by the existing `toast`, not by a new one. Every row gets it: user, assistant and tool,
including the pending "connecting to the model…" row where there is nothing to copy yet — that one is
skipped.

## 3. Resend my own message — what already exists

- `lastAsked()` + `askAgain()` (`app.js:578-605`) already refills the composer with **the last** user
  message, focus it, refuse to overwrite unsent typing, and mark the refill as the server's draft
  (`state.lastDraftSent = text;` + `sendQuiet('set_draft', …)`) so the echo guard does not drop it.
- The load-bearing rule is a test: **`send('send'` appears exactly once in the whole client**
  (`tests/test_webapp.py:399-402`, asserting it is not inside `askAgain`). Any per-row resend must
  keep that true or the test is telling us something real.
- So this is not a new mechanism: it is `askAgain` generalised from "the last one" to "this row",
  sharing one helper, with `↻ Again` by the composer left alone as the "last message" shortcut.
- Only rows with `role === 'user'` and non-whitespace text get the second icon.
- While a task runs: the refusal goes (item 0) — refill, and Enter queues like any other typing.

## 4. What will break, and the test list

- `tests/test_webapp.py:411` pins the busy refusal string that item 0 removes. That is the one
  existing assertion edited this round, and only because the behaviour it pins is the one UI 3.2 made
  wrong.
- New tests, against the 586 baseline:
  - `filter_models` no longer loses entries: catalog survives a filter, the selected model stays
    selected when hidden, and a refresh restores the full list (`tests/test_controller.py`).
  - Action parity: every `send('x')` in `app.js` is handled by **both** controllers — the drift class
    in item 0. This test fails the moment it is written and ships with the fills.
  - `--fake` answers `set_filter` / `set_model` / `refresh_models` and its snapshot's `provider.models`
    narrows the same way the real one does (the field-parity test already pins the keys).
  - Copy: `⧉` on every rendered row except the pending one; the click handler is delegated on
    `#thread`, not inside `.bub`; the copied value is `m.text` (`msg.text`), never `innerHTML`; and no
    new caller of `send('send'` appears.
  - Resend: a row control on user rows only, sharing the refill helper; unsent typing still refused
    with the same words; a running task no longer refuses; `↻ Again` still reads the last message.
- CSS: a `.macts` strip on `.msg .body` (which is already `align-items: flex-end` for
  `.msg.me`, `app.css:195`), using existing tokens only — otherwise `tools/check_contrast.py` gets new
  pairs to measure, and 126 is today's count.
- Not touched: `contract.py` stays five methods (copy and resend are client-only; the draft already
  has `set_draft`), `server.py` unchanged, the queue unchanged.

## Decisions — and how they were answered

**A. Refill or send immediately?** Moot: the row-level resend was **dropped**. The user's answer was
that `↻ Again` stays exactly as it is and a per-message version has no use. So `askAgain()` remains
the only repeat path, the one-`send('send')` rule is untouched, and item 3 of the plan reduces to
removing its obsolete busy refusal.

**Copy scope.** Narrowed by the user: **the icon goes on the server's messages only** — the assistant's
reply and the tool notices — and not on the user's own row. Item 2 therefore renders
`m.role !== 'user'`, and the pending typing row is skipped.

**B. Hover or always visible?** Decided during the build, against the recommendation written above:
always visible. Measured reason — a row action hidden until the pointer crosses it is a control nobody
finds, and only three rows in the scripted thread carry one, each a 24 px faint-grey button.

**C. Which surface gets the fast filter?** Both, as recommended: the client-side box in the
**Choose a model** sheet, and Settings' input kept working over the now-non-destructive view.

**D. Fill all 20 missing `--fake` actions, or whitelist?** Filled. Ten of them are gestures whose
interesting part is a dialog or a disk read the scripted thread cannot reproduce; those answer with a
sentence naming that limit (`fake.PREVIEW_ONLY`), and the dispatch's final `else` says *"Not scripted
in this preview: <name>"* rather than falling off the end quietly. The parity test then asserts both
lists cover every name the page posts.

## What the running page caught that the tests could not

`matches()` in the new sheet filter was written with `.casefold()` — a Python method — so opening the
model picker threw and the sheet never appeared. `node --check` parses, it does not run, and no unit
test executes a DOM here; the defect surfaced on the first scripted click in the `--fake` window. The
filter box, its `N of 7 models` count, `Enter` picking the first match, the Settings filter narrowing
7 → 1 while the selected model stayed selected, and the copy buttons on rows 0/2/3 with none on the
user's row 1 were all then read back from the live page.

**A. What does "resend" do — refill the box, or send immediately?**
Last round, the same question came up for `↻ Again` and the answer was *refill*. Now that the queue
exists, "send immediately" is not the same risk it was: while a task runs it lands in the queue, and
the click that queues is the approval you chose. Idle, though, a row button that sends is a click that
can write files in an Auto-Apply folder with no review step after it.
Recommendation: **refill** (same as `↻ Again`), because the one-send-path rule is tested and a row
button that fires breaks it. Option: refill on the first click, and the *second* click on the same row
sends.

**B. Does the copy button live on every row, or on hover only?**
Recommendation: hover/focus-revealed, always visible for the newest row — the thread is dense and
twelve grey icons per screen is noise. Option: always visible (easier on touch, and the app-window has
no hover state after a tap).

**C. Which filter surface is the "fast" one — the model sheet, Settings, or both?**
Recommendation: the **Choose a model** sheet (that is the list you use), client-side, plus Settings'
input repaired to be non-destructive. Option: also render the filtered model list *inside* Settings,
which duplicates the sheet and makes two places to pick a model.

**D. Fill all 20 missing `--fake` actions, or only the ones this round needs?**
Recommendation: the parity test as written fails on 20 names, so either fill all of them or have the
test list the exceptions explicitly. Filling all of them is ~100 lines of scripted-window handlers and
makes every future design review honest; the alternative is a named allowlist of holes.
