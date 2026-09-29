# Onboarding / first run — the plan, measured first

Spec (item 7, phase 3): a first-run Wizard — check Python, check Ollama, check a model is present,
pick a workspace, run a small demo, show the security policy, choose Chat or Change — plus one command,
`agent setup`.

## What already exists (four premises that turned out wrong)

| claim in the spec | measured state |
| --- | --- |
| "The user has to learn Ollama, profiles and commands before anything works" — i.e. nothing checks the machine | **Partly false.** `agent doctor` (`cli.py:31`, `doctor()` at `:61`) already reports the Python version, "standard library only", whether Docker is on PATH, whether `OPENROUTER_API_KEY` is set, and whether Ollama answers `/api/tags` — with the model ids it found. `tests/test_cli.py` covers both Ollama branches. What is missing is the **sequence, the advice and the trigger**: `doctor` prints one JSON blob, says nothing about what to do next, and nothing runs it for you. |
| "Add a small demo run" | **Already exists.** `agent demo` (`cli.py:51`, `demo()` at `:76`) plans → applies → statically checks → rolls back a synthetic `calculator.py` in a temp folder, with **no LLM and no project touched**, and prints `llm_used: false`. It is the red-line proof, and a wizard that re-invents it would be a second copy of the thing the product is judged on. |
| "Choose Chat or Change mode" | **Three positions now, not two** — item 4 in this same release shipped `Chat / Read-only / Change` (`intent.py`, `docs/READ-ONLY-MODE.md`). A two-button first run would hide the rung a sensitive folder needs. |
| "Pick a workspace" | **This collides with a decision already made.** UI 2.8 removed the global default folder on purpose (`docs/SIDEBAR-BRANCHES-PLAN.md`): the sidebar owns folders, and access is granted by opening one or dropping a chat on it (`_grant_folder`). So the step is not "set the workspace" — it is **"grant one project"**, landing on the same path the drop uses, and it must keep granting → Change mode the way that path already does. |

Also already there, and easy to duplicate by mistake:

- `launcher.py` — an interactive no-install menu with *"2. Quick offline demo"* and *"3. Check setup / available models"*. It is a first-run flow in all but the name, and it is the thing a `setup` command should subsume rather than compete with.
- `launch.py:77` calls `controller.check_setup()` on **every** window start — that is model discovery for the selected provider, so a wizard must not present "check the model list" as new work when the window has already done it.
- `runner.available()` / `detect()` / `targets()` — the project's own toolchain probe (java/mvn, node/npm, go, cargo, pytest, unittest). `doctor` never calls it, and it is the check that answers "why is the Checks card empty?".
- `providers.make_provider` + `config.needs_consent` — the cloud consent gate, already per provider and per endpoint.

## What `agent setup` should be, then

Not a new engine. One module that **runs the existing probes in order, says what each verdict means,
and offers the next action**, with three entry points over it:

1. `agent setup` — interactive in the terminal (the spec's one command).
2. `agent doctor` — the same audit, non-interactive, one-shot. JSON stays JSON (a script reads it), and
   the human lines become the same rows the wizard prints. One implementation, two renderings.
3. The window's first run — a card, not a modal, over the same controller verbs, because the web window
   is where the model picker and the folder grant already live.

### The steps

| step | probe (existing) | verdict + advice the wizard adds |
| --- | --- | --- |
| 1. Our runtime | `sys.version_info`, import `tkinter` | "3.11+ is required" / "Tk is missing, so the desktop window cannot start — the web window still works" |
| 2. Your project's commands | `runner.available()` | which of Maven / npm / go / cargo / pytest this machine can actually run; names the ones a folder might need and does not have |
| 3. Ollama | `doctor()` reachability, `catalog.models_for` | reachable · not running (with `ollama serve`) · running with zero models (with `ollama pull qwen2.5-coder:1.5b`) |
| 4. A model | the same catalog, filtered local vs cloud | picks one local model into the existing pref, or explains that a cloud model needs the policy step |
| 5. A project | `Workspace(path)` + `runner.targets(path)` | grant one folder through the path that already grants (`_grant_folder`), then say what command it was found to have |
| 6. The demo | `cli.demo()` | run it for real: apply → check → roll back in a temp folder, so the operator watches the red line work once before trusting it on their own files |
| 7. The policy | text only | five lines: review before write; nothing leaves the device unless you approve that provider; keys live in memory only; an apply is committed and rolled back from the card; `agent export-session` writes the audit report |
| 8. The position | `intent.MODES` | **three** rungs, and Auto-Apply named as the switch on top of Change rather than a fourth choice |

## Deliberately not built

- **No new settings file.** Whatever setup chooses goes through the writers that already exist
  (`_save_state`, `_composer_pref`, `_auto_pref`, the model selection) — a second store of the same
  preferences is how two surfaces start disagreeing.
- **No forced wizard on every launch.** It offers itself when there is no registry file at all, and is
  reachable afterwards from Settings.
- **No auto-install of Ollama or of a model.** The wizard says the command; running an installer is not
  this tool's to decide.
- **No LLM call inside setup.** Step 6 is `demo()`, which is `llm_used: false` by construction: a first
  run that needs a model to prove the model works is the wrong order.
- **No reformatting of `doctor`'s JSON keys.** A script may already read them.

## Build order

1. `setup.py` — `audit(app_dir)` returning rows (`{id, status, text, advice}`), calling only existing
   probes, and `steps` describing the order above. No printing, no Tk, no HTTP.
2. `cli.py` — `agent setup` (interactive: enter to accept a suggestion, a name to type, `q` to stop) and
   `doctor` rendering the same rows. Exit codes unchanged for `doctor`.
3. `controller.py` — `setup_state()` / `run_setup_step(...)` actions for the window's first-run card, and
   the "no registry yet" condition.
4. `app.js` + `fake.py` — the card, the same eight rows, and scripted answers so it is reviewable in
   `--fake`.
5. `gui.py` — the same rows in a dialog built from `audit()`, refusing nothing (the switches are already
   in that window).
6. Tests: `test_setup.py` for the rows and each verdict branch, `test_cli.py` for `agent setup` answers
   driven from a pipe, `test_controller.py`/`test_gui.py` for the surfaces, and a check that the wizard
   never claims a model is installed from a built-in fallback list.

## Shipped

Built as planned above, in that order. `1084 → 1166` tests.

**What the code said that the spec did not.** Recorded in the Premises section, restated here because
each one changed what was built: `doctor` and `demo` already existed, so `setup.py` sequences them
instead of replacing them; there are three write positions, not two, so step 8 offers three; "choose a
workspace" contradicts UI 2.8's no-global-default-folder decision, so the wizard *checks* a folder named
with `--repo` and points at the sidebar for granting one; and `check_setup()` already runs at every
launch, so the card had to be built from rows that cost nothing rather than from another probe.

**Two defects the tests caught, both now fixed:**

- `controller._save_state()` rebuilds its preference dict from named keys. `setup_seen` was written by
  "Don't show this again" and then erased by the next unrelated save — the dismissal lasted until
  something else changed. The flag is now one of the named keys, and a test writes an unrelated
  preference between the dismissal and the restart to keep it that way. (`read_only` needs no such fix:
  it lives per branch in `_composer_pref`, which was already listed.)
- The Tk window's check ran `setup_rows()` inside the worker thread, and building a row reads four
  `StringVar`s — off-thread that raises *main thread is not in main loop*, and the click answered with
  the generic failure line. `setup_values()` now reads them on the UI thread and the job body receives
  the dict. The web controller already did this correctly by copying the values before `run_job`.

**One thing removed while building:** a `setup_show` action. The only route back to a dismissed card is
the Settings entry whose own label says it will ask this machine, so re-opening and re-checking are one
click, and an action that only flipped a flag had no way to be reached.

**Single-sourced along the way.** `setup.tally()`, `setup.checks_done()` and `setup.proof_done()` are now
the only copies of the count line and the proof verdict; both windows had written them, which left the
Tk window saying both in English to a person reading Arabic, and the web card's tally was being
assembled in `app.js` from bare counts. The card now receives the sentence, the way its rows already
did. `tests/test_setup.py` greps both windows for the old literals, the same guard `test_intent.py` runs
on the refusals.

**Deliberately not built** (unchanged from the plan): no forced wizard, no installer, no model pull, no
LLM call inside setup, no second preference store, no reformatting of `doctor`'s JSON keys.

