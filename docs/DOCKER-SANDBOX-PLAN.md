# UI 4.3 — the sandbox where the project command actually runs (#44)

Phase 3 item 1, the last unshipped part of release 1's list. Measured on the machine the tool runs on,
2026-09-29, before anything was written.

## Environment facts that change the plan

| fact | how it was measured | consequence |
| --- | --- | --- |
| **`docker` is not installed here** | `docker --version` → `command not found`, `where docker` → nothing | the container path can be built and tested only to the standard `tests/test_verification.py` already holds: `shutil.which` and `subprocess.Popen` patched, argv recorded, no daemon. **A real container run stays unverified on this machine and will be reported as unverified, not as working** |
| **there are two recipe tables and they have already drifted** | `runner.RECIPES` has 11 entries; `verification.RECIPES` has 3 hand-copied commands, and two of them differ from the runner's: `mvn -o -B test` against `mvn -B test`, `gradle --offline --no-daemon test` against `gradle --no-daemon test` | a sandbox that reads the second table refuses 8 of the 11 commands a user can press. And the flag the copy dropped is the one that matters in a container with no network |
| **two recipes name the host interpreter** | `python-unittest` and `python-pytest` start with `sys.executable` | inside an image that path does not exist. A containerised run has to name the program (`python3`/`python`) the image provides, not the absolute path of the interpreter the window is running under |
| **`--network=none` is one of the flags** | `verification.py:109-116` | an online Maven or npm build cannot resolve anything in that container unless the image pre-bakes the repository or it is mounted read-only. The `-o` in the verification table was not decoration, and the runner's commands do not carry it |
| **`/work` is a tmpfs, and the proof lives there** | `docker_check` mounts `--tmpfs /work:rw,exec,...` and copies the tree into it; `runner.run` reads JUnit XML off the folder *after* the process exits (`report_counts(fresh_reports(where, ...))`) | a containerised run loses its test evidence: the reports die with the tmpfs. Green with no proof is exactly the claim the app refuses to make elsewhere (`tests_observed`), so the mount has to change, not the reporting |
| **the CLI has no `run` at all** | `cli.py` exposes `verify --recipe --image` and nothing else | the sandbox is reachable today only as a flag on the *static* verify path. Both windows press a host run (`gui.py:2257`, `controller.py:2671`), so "run the project command in Docker" is a runner feature, not a verification one |

## What already exists, and is not rebuilt

`verification.docker_check` (`:101-170`) already carries the discipline the request describes: `--rm`,
`--pull=never`, `--network=none`, `--read-only`, `--cap-drop=ALL`, `no-new-privileges`,
`--user=65534:65534`, `--pids-limit=128`, memory and cpu limits, the project bind **readonly** at
`/input`, `HOME=/tmp`, an image that must be **pinned by digest** (a tag is refused), a 64 000-char output
cap that kills the run, a `docker rm -f` cleanup that cannot swallow the result, and — the rule the user
asked for twice — **no fallback to the host** when Docker is missing: `{"status": "blocked", "reason":
"Docker is not installed. Host execution is disabled."}`. `tests/test_verification.py` records the argv
against a fake `Popen` and pins all of it.

`runner` owns the other half and is already independent of where the process ran: `child_env()`,
`_collect`, `kill_tree`, `decide_status`, `report_counts`, `fresh_reports`, `_failures`.

So this item is a **move plus a seam**, not a new subsystem: the flags and their tests move to the place
the windows call, and the runner's read side stays untouched.

## The shape of the change

1. **One recipe table.** `verification.RECIPES` goes away; the container builds its command from
   `runner.RECIPES[recipe]["command"]` with `sys.executable` replaced by the interpreter name the image
   provides. A recipe whose command needs network is the image's problem, said out loud in the UI rather
   than discovered as a failing build.
2. **`/work` becomes a host temp directory, not a tmpfs.** The project is copied into
   `tempfile.TemporaryDirectory(prefix="agent-sandbox-")` (the 2 000-file / 20 MB caps move with it),
   mounted `rw,exec` at `/work`, and the run's `where` is that folder — so `report_counts`,
   `fresh_reports` and the mtime grace read the same evidence they read on the host. The user's tree stays
   unwritten because `/input` is still a readonly bind of it.
3. **`runner.run(..., sandbox=None)`.** `sandbox` carries the pinned image; `None` is today's host run,
   byte for byte. With a sandbox named and no Docker, the answer is `blocked` and the command never spawns
   — the existing refusal, reached from the button now.
4. **The record says where the green came from.** `result["sandbox"] = {"image": …}` lands in the session's
   `runs`, so the export, the round timeline and the repair loop can tell a passing Maven run inside
   `eclipse-temurin@sha256:…` from a passing Maven run on this machine's JDK. A proof is a claim about an
   environment; the environment is now part of the proof.
5. **Reachable from where the user presses Run.** A `Run in Docker` switch and one image field in the
   Checks card of both windows, disabled with a reason when the machine has no Docker, and the same two
   values on the CLI's run path.

## Decisions, with the answer taken

1. **Where does the image name live?** → `Settings` (a `sandbox_image` field, validated to the same digest
   pattern) with the window's text box writing it. A per-project file would let a repository name the image
   the tool runs its own build inside, which is the one input the sandbox exists to distrust.
2. **Default on or off?** → off. A machine without Docker must still build, and the refusal-not-fallback
   rule only means anything when somebody asked for the sandbox. The switch is per folder, like Auto-Apply.
3. **What about recipes that cannot work offline?** → they run, and fail, and the failure is read like any
   other; the card says `--network=none` next to the switch so the reason is on screen before the click.
   No recipe is removed from the list because it might not suit an image nobody has chosen yet.
4. **Does `verification.verify()` keep its own docker path?** → no. It calls the runner's, so there is one
   container in the codebase. Its static checks and hash re-verification stay exactly where they are.

## Not built in this round

- Any claim that a container ran. There is no daemon here; the tests record argv and fake the process, and
  the release notes say so.
- Mounting a package cache, per-recipe network policy, and a `--privileged` escape hatch.
- Podman/`nerdctl` as an alternate engine: `shutil.which("docker")` becomes a small lookup, but the flag
  set is Docker's and pretending otherwise is untested surface.

## As built — the flags moved, the second table died, and one decision was wrong

**1380 → 1400 tests, suite green, `node --check` clean.** Everything below was written against the code
before it was changed, and two things came out differently.

**What shipped.** `runner.sandbox_argv()` is now the only place in the codebase that names a container's
flags, and `runner.run(..., sandbox="image@sha256:…")` is the project command inside one:

- the project is copied to a host temp folder (`copy_for_sandbox`, 2 000 files / 20 MB, skipping `.git`,
  `node_modules`, `venv` and friends), and **that copy is the only writable mount** — the folder the
  operator opened is never mounted, so the sentence "nothing is written back to your files" is a property
  of the argv rather than of the flags on one path inside it;
- the copy is a directory, not a tmpfs, so `report_counts`/`fresh_reports` read the same JUnit XML they
  read on the host. A test writes the report into the mount and the run still calls it 8 tests, 1 failed;
- `sys.executable` becomes `python3` and the digest is checked **before** anything is copied;
- no Docker → `{"status": "blocked", "reason": "… did not run on the host either."}` and `Popen` is never
  reached. The refusal is the answer, not a detour;
- a killed container gets the `docker rm -f` it never ran, and the result carries
  `sandbox: {image, container}` so `summarize()` says "Maven test in Docker" and the session record keeps
  which environment a green was a claim about;
- both windows ask the same question in the same card: a `Run in Docker` tick and one image field, greyed
  out with the reason when `runner.sandbox_available()` says no. The four sentences are
  `labels.NOTE_TEMPLATES["sandbox_*"]` and the four-way decision is `runner.sandbox_state()`, so the
  desktop window, the web window and the preview cannot disagree about what a half-typed digest means.

**Two corrections to this plan.**

1. **Decision 4 said `verify()` would call the runner's container. It does not, and it should not.**
   `verification.verify()` answers a different question — *is this session's proposal still what was
   approved, and did the pinned build pass* — with its own result shape, its own 64 KB cap and its
   before/after fingerprint drift check. Merging the two would have made one function serve a button and
   an audit. What actually moved is the part that must not be duplicated: the flag list, the digest rule
   and the recipe argv, all now shared through `runner.sandbox_argv` /
   `verification.container_command`.
2. **The `/input` + `cp -R` + tmpfs dance was two copies of the project and a shell.** The old call was
   `--entrypoint=/bin/sh image -c 'cp -R /input/. /work/ && exec "$@"' agent …` — a readonly bind of a
   *host temp folder* (not the user's tree, which is what the readonly was protecting) copied into a
   tmpfs the build could not leave evidence in. The new call mounts that same temp folder once, rw, and
   passes the recipe as argv with the image's own entrypoint. So the only shell text in the whole
   container path is gone, and the test that used to pin that text now pins its absence
   (`test_the_recipe_is_handed_over_as_argv_and_no_shell_is_asked_for`).

**The drift the second table had already caused** was worth the finding: `verification.RECIPES` carried
`mvn -o -B test` and `gradle --offline --no-daemon test`, while `runner.RECIPES` — the commands actually
pressed — carry no offline flag. In a `--network=none` container that difference is the whole build.
`OFFLINE = {"mvn": "-o", "gradle": "--offline"}` now derives the container command from the runner's table
instead of copying it, and a test pins all three derived commands.

**What is still unverified, in plain words.** No container has run. Every claim above about what the flags
do comes from reading Docker's documented flag meanings and from the argv a fake `Popen` recorded. Whether
a Maven reactor actually builds inside `--network=none` with a digest-pinned image, whether a Windows
temp path mounts cleanly under Docker Desktop, and whether uid 65534 can write the copied tree on a Linux
host — none of that is answered here, and `setup.checks` will keep saying the sandbox is unproven until it
runs on a Docker-capable machine.

One copy detail worth stating rather than leaving to a reader of `shutil.copytree`: it is called with
`symlinks=False`, so a symlink inside the project is copied as the file it points at. That is not a new
reach — the host run reads the same tree with the same permissions — but it does mean a link out of the
folder puts a copy of the target inside the container's mount.

**The CLI did not gain a `run` command.** This plan asked for "the same two values on the CLI's run path";
there is no CLI run path (`cli.py` has `verify --recipe --image` and nothing else), and adding one would
put a way to execute a project's own build scripts outside both windows' consent gates. The CLI keeps its
`verify`, which now shares the same builder.
