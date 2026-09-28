# UI 3.2 plan — a queue for messages sent while a task is running

> **Built the same day** (2026-09-26), on the answers below — and **decision A came back against the
> recommendation written here**: the user chose *run it automatically*, so a queued message starts by
> itself when the running task ends. The shipped shape, the three call sites of the drain and what
> that does to the no-click-no-work rule are recorded in `docs/IMPLEMENTATION-STATUS.md` (UI 3.2) and
> measured in `docs/VALIDATION.md`. The line numbers below are the baseline's and have since drifted
> (`run_job` is now `controller.py:317`, `start_plan` `:1154`), and the planned `queue_promote`
> shipped as `queue_now` while "open in a separate chat" shipped as the action `queue_chat`
> (`controller.queue_detached`). An item carries `{"id", "text", "chat", "branch", "project", "at"}`.

Written 2026-09-26 from the user's request: send a message while a request is still running, see it
queued as a line, and per item: **open it in a separate chat**, **edit it**, **delete it**.
Each claim below was checked in the tree first; the rest of this file is the plan as it was written,
before any of it existed.

## What already exists

- **A job is one thread with a hard "one at a time" gate.** `run_job` returns immediately when
  `self.busy` (`controller.py:308-311`), and `start_plan` returns silently when busy
  (`controller.py:1008-1010`). So the refusal today is a `return` with no message: the message you
  typed is gone and nothing said so — the same silent-drop class as the phantom `self.status`.
- **The composer is disabled rather than queued.** `ta.disabled = !!DATA.busy` and the
  `.composer.busy` class (`app.js`, `renderComposer`), while a **Stop** button appears beside Send.
  There is no queue anywhere: no `queue` key in `snapshot()`, no `self._jobs`-adjacent waiting list
  (`_jobs` holds *running* threads only), and no client state for pending messages.
- **A finished job can already start another one, and that is waited for.** `join()`
  (`controller.py:346-363`) re-scans for jobs that appeared while it was waiting, precisely because
  `on_done` chains (Auto-Apply: propose → apply → run). A queue drained from the end of a job
  therefore lands on machinery that already tolerates chained jobs.
- **Nothing auto-starts a task today, by design.** The chained-plan step advance asks first
  (`repair.step_offer`, UI 2.9 item 5: "Explicit confirmation before the next round or step"), and
  `run_job`'s own docstring says a job's `on_done` may begin another. A queue that fires by itself
  would be the first path in the app that starts work nobody clicked for at that moment.
- **Branch identity is the rule the queue has to follow.** `_select_branch` is the only writer of
  `self.repo`; a message queued against `demo2` must not run after the user switches to
  `demo_repo`. `new_chat_in` / `bind_chat` already give the "send this one elsewhere" gestures.
- **The action vocabulary is in place to hang this on.** `action()` dispatches a dict of lambdas
  (`controller.py:423-447`), so `queue_add` / `queue_edit` / `queue_drop` / `queue_promote` are
  ordinary entries; `contract.py` stays five methods because the queue is payload, not surface.
- **Front end**: a new snapshot field means three files at once — `controller.py`, `fake.py` (the
  `--fake` window is what designs get reviewed in) and the `DATA.` contract test
  (`tests/test_webapp.py:293-306`).

## Decisions I need

**A. When the running task finishes, does a queued message start by itself?**
This is the safety fork, and the project's answer has always been "no click, no work". With
**⚡ Auto-Apply** on for that folder, an auto-starting queue item is a disk write that nobody
confirmed at the moment it happened.

**B. "Open it in a separate chat" — separate how?** A new chat on the same project folder keeps the
context that made the message sensible; a standalone chat reads nothing and can only answer in
prose. The word "separate" could mean either.

**C. Does the queue survive a restart?** Persisting it means the folder, the branch and the model
it were all still valid on the next launch — and a queued change request outliving a reboot is the
kind of thing that fires against files that have moved on since.

## Shape, once answered

- `self.queue: list[dict]`, each `{"id", "text", "branch", "kind"}` — keyed to the branch it was
  typed on, and only ever drained for that branch.
- Sending while busy becomes an explicit **Queue** affordance rather than a silent `return`, so the
  text is never lost by an action that looks like it did nothing.
- One line per item above the composer: the text on one row, `▶ Run now`, `✎ Edit`, `↗ New chat`,
  `✕` on the right. Not a modal, not a panel — the user asked for "a line".
- The composer stops being `disabled` while busy (that is the whole point), but Send turns into
  Queue, and `⚡ Auto-Apply` and the mode badge keep refusing what they refuse today.

## What will break when this lands

`ta.disabled` and the `.composer.busy` rule are asserted by nothing today, but the `DATA.` contract
test fails the moment the client reads `DATA.queue` unless `fake.py` answers it in the same commit,
and `test_the_real_and_scripted_snapshots_agree_on_their_top_level_keys`
(`tests/test_webapp.py:307-315`) is the other half of that. Every queue string that reaches the user
goes through `labels.say`, so the Arabic twins arrive with the feature rather than after it.
