# Phase 2 — cleanup, measured before changed

Eight items, read in the code first. Baseline: **903 offline tests green**, `node --check` clean, CI
workflow landed in the hardening round. Three of the eight are already done or already exist in a
different shape; five are real. Nothing here adds a feature.

| item | verdict | evidence |
| --- | --- | --- |
| README + badges | **REAL, and worse than "an old number"** | `README.md:14` is a static `Tests-620+ Passing` badge; the suite is 903. The four caveats the list asks for are **absent**: `grep -ci docker README.md` → `0`, and no Chat/Change/Auto-Apply distinction, no "the run executes the project's own build code", no "the AST map is context, not a compiler". A fixed number in a badge is also a promise no test holds |
| Repository root | **PARTLY ALREADY THERE** | the proposed `examples/ tools/ docs/ sandbox/` **all exist today** (`examples/demo2`, `examples/demo_repo`, `tools/*.py`, `sandbox/*`); only `archive/` is missing. `.gitignore` already covers the runtime state (`.agent-*`, `.screens/`, `.design-preview/*.png`, `build/ dist/ *.egg-info/`, `.env`). Real finds: `spring-rpoject/` is a typo'd tracked Maven demo cited by two docs, `project-2/` is markdown experiments, and **`ecommerce` is recorded as a gitlink — `git ls-files --stage ecommerce` → `160000 2a926c3b…` with no `.gitmodules`** |
| Unify Web/Tk through a Host contract | **ALREADY SHIPPED as the seam; the drift audit is below** | `src/ai_code_engineer/host.py` exists (four verbs — `say`/`line`/`ask`/`stream` — plus the one Apply-dialog builder that had diverged) with `tests/test_host.py` holding both windows to it. `labels.py` (689 lines), `repair.py` and `redaction.py` are the other three files the list proposes creating. So the remaining work is measuring residue, not building structure |
| Real HTTP-server tests | **MOSTLY THERE, three cases missing** | `tests/test_webapp.py` is 126 tests and already covers no-token 403, wrong-token 403, Host refusal, Origin refusal, oversized body without breaking the socket, non-JSON 400, and an exception that leaks nothing. Not covered anywhere: the **`X-Auth-Token` header** path (`server.py:149` accepts it, no test exercises it), **a closed SSE stream removing its client from `Hub`**, and **two servers in one process** not sharing a token or a controller |
| Unified test doubles | **REAL duplication** | `patched_catalog()` is copy-pasted verbatim in `test_controller.py:33` and `test_gui.py:30`; `ChatModel` exists in both; `ScriptedProvider` in `test_agent.py:25` and `test_repair.py:18`; `run_result()` in `test_controller.py:70`; eight model stubs (`ProposalModel`, `SteppingModel`, `DualModel`, `GatingModel`, `ScriptedModel`, …) |
| CLI / catalog / provider tests | **CLI IS UNTESTED** | `grep -rn "cli\.\(main\|doctor\)" tests/` → nothing. `cli.py` exposes `doctor`, `map`, `plan`, `review`, `apply`, `verify`, `rollback`, `status`, `demo` and only `demo()` is called by a test. The catalog half of the item is already covered (`tests/test_connection.py`, 49 tests: both `/models` shapes, fallback rules, key placement, consent, redaction). The **transport** half is not: redirect and proxy refusal are asserted at the endpoint-policy level only, never at the request |
| Packaging check | **CONFIG RIGHT, NOTHING HOLDS IT** | `pyproject.toml` has `[tool.setuptools.package-data] "ai_code_engineer.webapp" = ["static/*"]` with a comment explaining that a wheel without it opens a blank window — and no test or CI step checks it. `build`/`wheel` are not installed locally, so the check belongs in CI |
| Optional lint / static checks | **HALF PRESENT** | CI already runs `compileall` on `src agent.py launcher.py desktop.pyw` and `node --check` on both scripts. Not covered: `tests/` is not byte-compiled, and there is no lint. `ruff` is deliberately **not** added — see "What does not change" |

## The one finding that is not a cleanup

`ecommerce/` — the 412 MB Spring Boot dogfood project — is **inside the repository's index as a
gitlink**, the shape git records for a submodule, but there is no `.gitmodules` to say what it
points at or where it came from. Consequences: a clone of this repo gets an empty `ecommerce/`
directory; `git status` in the parent never shows the dogfood project's own 69 agent commits; and
`git submodule` commands would half-work on it.

**Not fixed here.** Removing it means `git rm --cached ecommerce` — a staged change to what the
repository records, which is the user's call, and it is the kind of edit that touches a shared
surface rather than a working tree. It gets reported with the command and left alone. The
`ecommerce/` folder's own contents are not touched in this round at all, per the standing rule that
it is a project to *use*, not to edit.

## What gets built

**1. README.** Replace the `620+` badge with the CI status badge — a number a workflow produces is
checkable, a number typed into a markdown file is not. Add the run/test commands exactly as
`run-tests.cmd` and CI use them, and a *Limits* section carrying the four missing caveats: Chat mode
reads and answers, Change mode proposes, Auto-Apply writes without a click but still only after a
green run; **Run and Check syntax execute the project's own build code with this user's permissions**
— the allowlist and the stripped environment are a limit, not a sandbox, and Docker is the only real
isolation for a folder you do not trust; the repository map is parsed for context and is not a
compiler, so it cannot promise it understood the code.

**2. Root.** `git mv spring-rpoject archive/spring-project-typo`? No — rename it to
`examples/spring-demo` where the other examples live, and update the two docs that cite it. Move
`project-2/` into `archive/`. Leave `sandbox/` as the tools folder it already is, since `docs/` cites
its probe scripts as the source of measured claims. Add `.gitignore` lines for what is actually on
disk and not yet covered.

**3. Host drift.** Report from the audit folded into `host.py`'s docstring; fix only rules that
genuinely differ, and say plainly which differences are presentation.

**4–6. Tests.** The three HTTP cases; `tests/test_cli.py`; the provider-transport cases; then
`tests/doubles.py` + `tests/helpers.py` for the copies that are verbatim, with a guard test that a
second copy of `patched_catalog` cannot come back. Model stubs whose behaviour differs per file stay
where they are — merging them would be a rewrite dressed as a cleanup.

**7. CI.** A packaging gate: build the wheel, list it, assert `static/index.html`, `app.js`,
`app.css` and `boot.js` are inside, install it into a clean venv with no `src/` reachable, and start
the server from the installed package. Byte-compile `tests/` too.

## What does not change

- No new runtime dependency, and no `ruff` yet. The project is stdlib-only on purpose; a lint gate
  added to 5 100 lines of window code that was never linted produces a wall of findings nobody reads,
  and the first one that blocks a green build teaches the wrong lesson. `compileall` + `node --check`
  + 900 tests is the gate today.
- The `Host` seam stays four verbs, not a base class — `gui.AgentApp` already inherits from Tk.
- `ecommerce/` is not moved, renamed, de-indexed, edited or run in.
- `tests/test_controller.py` keeps its own stubs where their behaviour is the point of the test.

Expected: **903 → ~940**, all green.

## As built — 2026-09-29

**903 → 951 offline tests, green.** `node --check` clean on both scripts, `compileall` widened to
`src tests`, and the packaging gate was **run locally, not only written**: `python -m build --wheel`
produced a wheel that `tools/check_package.py` confirms carries 27 modules and all four UI files, and
`tools/package_smoke.py` installed into a throwaway venv served the window from `site-packages` and
answered `/api/bootstrap`. The venv, `build/`, `dist/` and the egg-info were deleted afterwards.

### What the audit found that the list did not expect

**`host.Host` was a contract nobody had signed.** The module names four verbs, says "a window
implements them and nothing else may reach the screen", and *neither window defined any of them* —
four `raise NotImplementedError` stubs, zero call sites, and a test that checked the stubs were
documented. So item 3's premise ("the duplication caused differences") was right and its proposed fix
was already half-shipped as a name. Both windows now implement the four verbs, and
`tests/test_host.py` holds them to three things: the same signature, the web verbs actually reaching
the surfaces the browser reads, and a **ceiling on the raw primitives** (`status.set(` 59,
`self.status = ` 89, `_add(` 33, `_emit(` 21, `messagebox.` 6, `events.put(` 6, `_note(` 9,
`chat_message(` 16). Converting 89 assignments in one pass is still the rewrite this module exists to
make unnecessary; what changed is that the un-routed half can no longer grow unnoticed.

**Four real asymmetries, all fixed:**
- Tk called `messagebox.askyesno(prompt["title"], prompt["message"])` and dropped `prompt["warning"]`.
  The delete/empty warning reached the web dialog only. It now goes through `self.ask(...)`, which
  carries it.
- Tk called `repair.must_ask(self.session)` without the prior task, so the branch that refuses to
  stack a new proposal on a folder a previous task left half-written was **dead code in Tk**. It now
  passes `unverified_prior_task(...)`, the method it already had and was not using here.
- Tk wrote `runner.summarize(result)` straight into the conversation; the summary interpolates the
  recipe's `reason`, and a Windows "command not found" reason is a path. The web window redacts every
  field of the same sentence. Tk now does too.
- `controller.py` had the one un-routed exception sink left in either window:
  `"Could not open the folder: " + str(exc)[:120]` into the status line the browser reads. The
  sentence the program wrote stays; the exception's goes to Activity, redacted.
- Also: Tk hand-clamped the request timeout (`min(900, max(30, …))`) where `config` already exports
  `clamp_request_timeout`. It agreed with the shared bounds by coincidence, which is the kind of
  agreement that survives exactly one edit.

**Seven sentences were already different, not merely duplicated.** "click Send" vs "press Send", the
web note about saved project notes losing the half that explains why they are safe, Tk promising "no
changes will be applied" where the web window had stopped saying it. They are one table in `labels.py`
(`NOTE_TEMPLATES`, 21 entries, both languages, `{fields}` checked for agreement by
`tests/test_doubles.py`), and `gui.py`'s inline "select a model" line now uses the `pick_model` key
the same file already imports.

**A latent test bug the extraction caught.** Both copies of `patched_catalog()` answered a free-only
row with `list(FREE_ENTRY)` — a dict's **five keys**, not an entry list. No test had read an id back,
so both windows were being handed `["id", "name", "free", "cloud", "description"]` as a model catalog.
The shared double returns copies of the dicts, and the new guard test asserts the shapes.

### The item that was refused, and why

`ecommerce/` is recorded in the index as a **gitlink** — `160000 2a926c3b…`, the shape git reserves
for a submodule, with no `.gitmodules` behind it. The list asked to verify exactly this; the answer is
that it is real, and the fix is `git rm --cached ecommerce`, which changes what the *repository*
records. Not done: it is a shared-surface edit, the dogfood folder's own contents are off-limits by
standing rule, and the finding is reported instead.

`spring-rpoject/` and `project-2/` were **not** moved to `archive/`. They are referenced by absolute
path from the tool's own history — `.agent-projects.json` and four `.agent-chats/<id>/chat.json` —
and the requirement that the window open afterwards and show the whole history of each project is
older and heavier than a typo in a directory name. `archive/README.md` records this, including what
moving them would require, and the README gained a "where everything lives" table, which is what the
item actually wanted.

`ruff` was not added. The project is stdlib-only on purpose, the two windows have 5 100 lines nobody
has ever linted, and a first lint run that blocks a green build teaches the wrong lesson. `compileall`
+ `node --check` + 951 tests is the gate.

### What else changed

- `serve()` builds **a handler subclass per launch**. The four session fields (token, controller, hub,
  port) lived on the shared `Handler` class, so a second `serve()` in one process did not open a
  second window — it replaced the first one's lock and its controller. The list asked for a test that
  two servers do not mix; the reason it did not exist is that it would have failed.
- Test-module hygiene: every server fixture now calls `server_close()` as well as `shutdown()`; the
  suite was leaking one bound port per class and printed `ResourceWarning` at exit.
- `tests/doubles.py` + `tests/helpers.py`: `patched_catalog`, `ChatModel`, `ProposalModel`,
  `run_result`, `PROOF`, the two catalog entries and the two calculator fixtures moved out of the
  three files that each carried a copy; `sandbox_repo()` replaces eight hand-rolled temp-repo setUps.
  Stubs whose behaviour *is* the test (`SteppingModel`, `DualModel`, `GatingModel`, the two
  `ScriptedProvider`s, which differ) stayed where they are.
- README: the `620+` badge is gone, replaced by the CI badge; "Nothing reaches your disk without
  explicit approval" became the truth about Auto-Apply; and the four caveats the list asked for are a
  section of their own.

