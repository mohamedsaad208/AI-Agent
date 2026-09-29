# Phase 3 — product features, measured before built

Ten items, and the ordering the request itself proposes (release 1 = Docker sandbox, monorepo, repair
loop, GitHub PR, session export). Measured first. Six of the ten already exist in some form, and two
of them exist in a *stronger* form than the request assumed.

Baseline: **951 offline tests green**, `node --check` clean, CI gating the suite and the wheel.

## Environment facts that change the plan

| fact | consequence |
| --- | --- |
| **`docker` is not installed on this machine** (`docker --version` → command not found) | the container path can be built and tested to the standard the existing Docker tests already use — `shutil.which` and `subprocess.Popen` patched, no daemon — but a real container run stays **unverified here** and will be reported as such, not as "works" |
| **no `gh` CLI** | a GitHub workflow must be stdlib `urllib` against the REST API, or `git` subprocess. Both are available |
| **git 2.15.0** | several modern flags do not exist; the code already carries constraints learned the hard way, and any new git call has to be checked against that version before it is claimed |
| **`GEMINI_API_KEY` is present in the environment** | a Gemini adapter is something the user can actually run today, which changes the answer to UI 4.2's open decision #1 ("add an Anthropic adapter only if you will actually use it") |
| **Maven 3.9.9 / JDK 17 / Node 25 / 11 Ollama models** | monorepo and repair-loop behaviour can be verified against real toolchains, in a temp folder — never inside `ecommerce/` |

## Verdict per item

| item | verdict | evidence |
| --- | --- | --- |
| **1. Docker sandbox** | **ALREADY EXISTS, for the half that matters most — and unreachable** | `verification.py:109-116` builds exactly the container the request describes: `--rm --pull=never --network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges --user=65534:65534 --pids-limit=128 --memory=1g --cpus=1`, the project mounted **readonly** at `/input`, copied to a `/work` tmpfs so a build cannot write into the user's tree, `HOME=/tmp`, no key in the child env, a 180 s deadline, a 64 000-char output cap, `--rm` cleanup, and an image that must be **pinned by digest** (`:105` — a tag is refused). With no docker it answers `blocked` / "Docker is not installed. Host execution is disabled" and **never falls back to the host** (`:102-104`). What is genuinely missing: the **runner** — the thing either window actually calls when you press Run — has no container path at all (`runner.py`: the word "docker" appears once, in its docstring; `run()` is always a host `Popen` at `cwd=root`), and `docker_check` cannot be reached from either UI (`gui.py:2039`, `controller.py:2174` call `verify(path)` with no recipe or image; only `cli.py:137` passes them) |
| **2. Monorepo** | **REAL GAP** | `runner.detect()` uses `repo.glob("*")` — non-recursive — so `backend/pom.xml` is invisible; no `-pl/--projects`, no `settings.gradle` read, no `package.json` `workspaces`. `self.repo` is one root; `recipes` is a list of alternative **commands at the same root**, not alternative folders. Surefire evidence reaches one level down (`*/target/surefire-reports/TEST-*.xml`) but Gradle's `build/test-results/**` has no equivalent depth, and `repo_map` caps at `MAX_FILES=300` then breaks mid-list at `render(limit=12000)` — alphabetical order means a later module silently disappears from the map a model is asked to plan against |
| **3. Plugin system** | **NOT NEEDED IN THE FORM ASKED — the real extensibility is item 9** | `config.KINDS` and `runner.RECIPES` are already data tables that both windows read; adding a provider or a toolchain today is one table entry, not an edit of `providers.py`'s logic. A `ProviderPlugin`/`ToolchainPlugin` class protocol with no second contributor to write plugins against is abstraction ahead of demand. What is actually missing is the thing item 9 asks for: a project that declares its own command |
| **4. More providers** | **MOSTLY SHIPPED** | LM Studio, vLLM, OpenAI, Groq, DeepSeek, OpenRouter and a `generic` OpenAI-compatible row are all live from UI 4.2 Phase 1, and Azure OpenAI is what `generic` + a `/.openai/v1` endpoint is. The five rules the request lists (timeout, size cap, no fallback, redaction, explicit consent, no key storage) are enforced at the shared layer, not per provider — `providers.request_json`, `config.check_endpoint`, `make_provider`'s consent gate, `controller.key` memory-only. What does not exist: Gemini's native wire and Anthropic's (both are second body shapes and second response parsers, not config rows) |
| **5. Repair loop** | **EXISTS, BOUNDED, DUMB IN THREE WAYS** | `repair.MAX_FIX_ROUNDS = 3`; the loop re-enters `plan()` with the run output; the round counter is in both windows. No classification at all (`grep categor/classif` → zero hits in `src/`), no check that the new proposal is not the one just tried, and no progress comparison between rounds — a fix round that turns 7 failures into 7 *different* failures is treated identically to one that clears them |
| **6. GitHub / PRs** | **ABSENT — and the local half is already good** | `git_integration.py` never contacts a remote: task branches (`agent/task-<slug>-<sha>`), a checkpoint commit per apply, restore, branch state reading. No `push`/`fetch`/`clone`/`ls-remote` anywhere in `src/`. So PR support is a new module, and the standing rule applies: **nothing pushes without a clear yes** |
| **7. Session export** | **DATA IS THERE, THE REPORT IS NOT** | the session JSON already holds the request, model, state, `proposal_hash`, events with timestamps, per-run results (`runner.run` returns recipe/label/command/status/exit_code/seconds/tests_observed/proof/truncated/timed_out/output/tail/failures) and `change_audit`. `agent status` dumps it as JSON — that is item 7's `--format json` minus the name. Missing: a stable way to name a session from the CLI (`project_key` + `.agent-runs/` layout, no index), a markdown renderer, and refusal reasons as a first-class column |
| **8. Dashboard** | **PARTLY SHIPPED** | `snapshot()` already carries `runInfo`, `recipes`, `recipe`, `canRun`, `queue`, `branch` (with checkpoint sha), `context`/context-use, `projects`, `runs`, plan-step state. Missing: a *time* dimension (seconds per phase, tokens), failure categories (depends on item 5), and a place that draws them together |
| **9. Custom commands** | **REAL GAP, AND THE ONLY HOLE WORTH OPENING** | `runner.RECIPES` is a module constant and the only source; `run()` raises `PolicyError("Unknown recipe")`; `--recipe` on `verify` accepts only the three docker names; there is **no UI text field** for a command anywhere (Tk's combobox is `state="readonly"`, the web buttons come from the detected list, and `selected_recipe()` returns `None` for an unknown label). That readonly-ness is the security property, so item 9 has to open it deliberately: a project-declared file the tool reads, argv as a list, no `shell=`, shown before it runs, consent per new command, the same caps |
| **10. Proposal quality / clarification loop** | **DEFERRED (release 3)** | the accepted action vocabulary is fixed (`engine.py`), and adding "ask a clarifying question and stop" changes the loop's contract for a CPU-only 1.5B model that is already the weakest link in proposal quality. Building it now, unmeasured against that model, would be a prompt change dressed as a feature |

## What gets built — release 1, in the order that can be verified

1. **Session export** (`agent export-session <id> --format json|markdown`) — self-contained, no environment dependency, and it makes every later item auditable.
2. **Repair loop** — classify the failure, send only the relevant slice back, refuse a proposal already tried, stop on no progress, and show a round timeline in both windows.
3. **Monorepo** — recursive subproject discovery, a target chosen per project, and the command run from the right folder.
4. **Docker sandbox** — move `verification`'s container discipline into `runner`, keep the refusal-not-fallback rule, and make it reachable from both windows and the CLI.
5. **GitHub** — push a task branch and open a PR with the test evidence in the body, behind an explicit consent, with the token read from the environment only, and no force-push ever.

Then, if the round holds: **Gemini** as a real second wire, since the key is already in this environment.
Deliberately not built in this round, with the reason in each case: the plugin protocol (item 3 — the
tables already are the extension points), `ruff`-style abstraction layers, and the clarification loop
(item 10, release 3).

## Shipped in this round

**7. Session export** — `src/ai_code_engineer/report.py` and `agent export-session <id|prefix|folder|
session.json> --format json|markdown [--out FILE]`. The markdown is the audit answer ("what did this
task do, and why did it stop"): request verbatim, files read split by who read them, every refusal
with its recorded reason, each command with its proof source, and a timeline with the gap between two
events. A session that stopped with no reason recorded says that instead of printing a blank.
Secrets are re-redacted at this boundary, in both formats, because a report is the one artefact that
leaves the machine. 24 tests.

**5. Repair loop** — all six asks, and the "dumb in three ways" row above is what changed:

- *Classification*: `repair.CATEGORIES` names six kinds from the tool's own wording (`environment`,
  `timeout`, `dependency`, `syntax`, `type`, `assertion`) plus `unknown` as an honest answer. Match
  order is fixed and tested: `mvn: command not found` above an `AssertionError` on the next line is
  still a missing binary, and `AssertionError: expected 5 but was 4` is an assertion even though it
  contains a compiler's own `error: expected` phrasing. A passing run has **no** kind — the report
  does not print a diagnosis for something that did not fail.
- *Only the relevant slice*: `repair.relevant()` picks the tail lines that speak to the kind, keeps
  the tool's own summary line, drops stack frames and duplicate lines (`FRAME_PATTERNS`), and is what
  `evidence()` sends. The full output stays in Activity; the model sees the diagnosis.
- *No repeated proposal*: `change_digest()` fingerprints the files, not the sentence around them, so
  a re-worded answer to the same question is caught. `already_tried()` runs after the round produces
  its proposal — the first moment its contents exist — and the window says so in the thread.
- *Comparison between rounds*: `stalled()` refuses to compare two different commands and refuses to
  read a trend out of run that produced no counts; a same-kind `environment`/`dependency` failure
  twice stops the loop by kind, because no code change moved it.
- *Stopping*: `should_stop()` is the one question both windows ask before spending a turn, and
  `stop_line`/`stop_status` are the one answer. The budget check left the web window's
  `_offer_fix` with it: a window may display "of 3", only `repair` may compare against it.
- *Timeline*: `repair.timeline()` renders the attempts from `attempts()` — the same rows the report
  exports — into the offer card before the click, and into the chat where the history stays readable
  afterwards. Both windows print it at a stop and at a repeated proposal, through their `line` verb,
  so the Host ratchet still holds.

**Defects found while building it**, each now covered: the categorical stop branch was unreachable
(the no-test-count guard returned first, so a missing tool could burn the whole budget); "the same as
the round before" was printed for a round that had got **worse**; `should_stop` claimed "the command
still fails" without looking; the `[ERROR]`-prefixed Maven frames were being sent as diagnosis; and
`fixRounds.attempts` in the snapshot read every session file on every poll for a field no window drew.

Tests: 951 → 1023. Browser-verified against `--fake` (the round chip appears only once a round is
spent, and the scripted loop counts rounds the way the real one does).

**2. Monorepo / multi-part projects** — `runner.projects()` walks to a bounded depth for folders that
hold their own build file, `runner.targets()` answers "which of those has a command I can run here",
and `runner.run(..., target="backend/auth-service")` runs it there:

- The opened folder stays first in the list and a folder with no build file of its own (`backend/` in
  a tree of modules) is not offered — a button that can only answer "no command here" is noise.
  `node_modules`, `target/`, `build/`, `dist/`, `venv/` and friends are walked past, so a vendored
  dependency's `package.json` cannot become the project's build.
- **Nothing outside the opened folder is reachable**: `project_folder()` normalises the relative path,
  refuses absolute/drive/`..` shapes, resolves symlinks and re-checks containment. A target arrives
  from a click on a label, so it is treated as input, not as a trusted argument.
- The module is **recorded on the run** (`"target"`) and carried everywhere the run goes: the status
  summary ("Maven test in auth-service: FAILED…"), the evidence handed to the next round
  (`Folder: backend/auth-service of the opened project`), the fix task itself, the timeline rows, and
  the exported report's heading. A fix round that did not know the module would read the wrong tree.
- **A stalled loop compares one module against itself**: the same recipe in `auth-service` and
  `product-service` is two problems, not a round that made no progress.
- Both windows pick it: the web Checks card grows a module pill above the command pill (hidden when
  there is only one), Tk gets a second combobox packed only for a multi-module folder, and choosing a
  module re-reads the commands that module answers to. The preview controller ships the same three
  modules so the picker can be reviewed.
- `symbols.spread()` gives a multi-project folder a map that names **each** module before naming any
  of them twice — the 12 000-char budget used to be spent on the first three modules in alphabetical
  order and the rest simply did not exist as far as the model was concerned. A truncated map now also
  says which modules it left out entirely.
- A task rooted somewhere other than the folder the list was scanned from is run at its own root, and
  never handed a module path that only made sense next to the other folder.

Measured on the real reactor in this workspace: discovery of nine modules costs ~80 ms (it was ~540 ms
until `detect()` stopped re-resolving every executable against PATH once per module).

Tests: 1023 → 1045, including a two-module Python fixture where the same recipe passes in one module
and fails in the other. Browser-verified: the picker listed `demo2 (whole project)`, `backend`,
`frontend`, and choosing `frontend` changed the offered command to `npm test`.
