<div align="center">

<img src="assets/banner.svg" alt="AI Code Engineer Banner" width="100%"/>

# AI Code Engineer
### Reviewable code changes, local models, and evidence-based verification.

AI Code Engineer is an open-source coding agent with a local Web UI, a Tk fallback, and a CLI. It reads a project, proposes hash-checked diffs, and helps apply, verify, repair, or roll back changes. Use Ollama locally or configure OpenRouter, OpenAI, Groq, DeepSeek, and compatible endpoints.

[![License](https://img.shields.io/badge/License-MIT-F59E0B?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3B82F6?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-10B981?style=for-the-badge&logo=linux&logoColor=white)]()
[![Developed with AI](https://img.shields.io/badge/Built%20With-AI%20%26%20Human%20Pairing-8B5CF6?style=for-the-badge&logo=openai&logoColor=white)]()
[![Tests](https://github.com/mohamedsaad208/AI-Agent/actions/workflows/ci.yml/badge.svg?branch=main&style=for-the-badge&logo=python&logoColor=white)](https://github.com/mohamedsaad208/AI-Agent/actions/workflows/ci.yml?query=branch%3Amain)

[Quick Start](#-quick-start) •
[Why AI Code Engineer](#-why-ai-code-engineer) •
[Platform Guide](#-platform-guide) •
[Try These First](#-try-these-first) •
[Plan Files](#plan-files-and-progress) •
[Troubleshooting](#troubleshooting) •
[Architecture](#-architecture) •
[Contributing](#-contributing)

<br/>

<img src="assets/demo-webapp.gif" alt="AI Code Engineer Desktop WebApp Interface" width="100%"/>

</div>

---

# 🚀 Why AI Code Engineer

| Feature | Why it matters |
| :--- | :--- |
| 🔒 **Workspace protections** | Path-traversal guards, symlink blocking, sensitive-file restrictions, and credential redaction reduce accidental disclosure. Review what you send to cloud providers. |
| 🛡️ **Reviewed diffs** | Every change is a proposal with a `SHA-256` hash, and applying it is a decision you make (or hand over per folder, explicitly). Rollback is one step, as long as nothing else edited those files afterwards. |
| 🌿 **Git Checkpoints & Restore** | Applying an approved proposal inside a git repo automatically creates a non-destructive checkpoint commit (`--no-verify`, skips hooks). If subsequent edits block local rollback, targeted single-file git restore offers a safe escalation path back to the pre-task commit. Never pushes, pulls, or rewrites history. |
| 🌐 **Native Bilingual & RTL** | First-class Arabic and English dual-engine. Dynamic Right-to-Left (RTL) support in the WebApp, automatic language detection (`is_arabic`), and fully localized system notices, diagnostic reports, and `--arabic` CLI flags. |
| ⚡ **100% Offline & Local** | Full first-class support for **Ollama** (`qwen2.5-coder`, `deepseek-coder`, `llama3`). Code stays on your hardware. |
| ☁️ **Multi-Provider Cloud** | Seamlessly switch between **OpenRouter**, **OpenAI**, **Groq**, **DeepSeek**, or custom OpenAI-compatible endpoints. |
| 🔌 **Endpoints are configuration** | No model server's address is written in the code — the provider table carries one default per row, and a URL resolves as *what you typed → your environment (`OLLAMA_HOST`, `GROQ_BASE_URL`, …) → the table's own row*. Point Ollama at another port without editing a file, and a profile that forgets its endpoint gets **its own** provider's address, never a local default. |
| 🗺️ **AST Symbol Indexing** | In-memory symbol extractor (Python, Java/Kotlin, TypeScript/JS, Go, Rust) provides classes, methods, and types without burning context window tokens. |
| 🧪 **Self-Healing Test Loop** | Auto-detects `pytest`, `unittest`, `Maven`, `Gradle`, `npm`, `cargo`, `go test`. Parses JUnit XML output and feeds failures back to the agent for autonomous repair (up to 3 rounds). |
| ⏱️ **Task queueing** | Queue follow-up requests while a task is running. Quoted context and the actual request stay separate, so a quote does not change whether the request is chat or a file change. |
| 📑 **Session Audit & Export** | Transcripts, diffs, and proof tallies are exportable to structured JSON or clean, readable Markdown reports (`agent export-session`) for documentation and audits. |
| 🖥️ **Desktop WebApp & CLI** | Beautiful local WebApp with real-time streaming, diff previews, task queuing, and an interactive terminal menu. |

---

# 🧭 Three modes, and the limits that go with them

The three modes are **Chat**, **Read-only**, and **Change**. **Chat** answers in prose and reads no files until you hand it a folder. **Change** produces a
proposal you review, and only `Apply` writes. **Auto-Apply** is a switch you turn on *per folder*:
the agent then writes what it proposes without a click, runs that folder's own command afterwards,
and keeps the rollback. It still asks first if the proposal would empty or delete an existing file, or
if a previous task left that folder half-written. The window says so on the card and in the Activity
log when a write happened without a click — that is the one thing about this tool that is easiest to
forget and hardest to undo.

| What you should know before you rely on it | |
| :--- | :--- |
| **Run and Check syntax execute your project's own code** | They invoke `mvn`, `gradle`, `npm`, `pytest`, `cargo`, `go` in that folder with *your* permissions. A build script is code, and code from a repository you did not write gets run here. The command list is an allowlist and the environment is stripped of credentials — that is a **limit**, not a sandbox. The row below is the sandbox. |
| **The sandbox is a tick on the Run button** | `Run in Docker` runs the project's command against a **copy** of the folder, in a container with no network, no capabilities, a read-only root and an image pinned by `sha256` digest — so the build's reports are still read afterwards, and your tree is never mounted into it. Without Docker the run is refused rather than quietly done on the host. Verified here as an argv, not as a build: no container has run on the machine this was written on. |
| **Read-only is a fact about the folder, not about a window** | Setting it in either window, or sealing a folder with `agent read-only --repo PATH`, is written down where every surface reads it back: a terminal that never opened a window cannot `apply` or `rollback` into it, and one window saving its own preferences cannot unseal what the other was told. Lifting it is an explicit act, and the refusal names the line that does it. What it never does is stop reading, searching, mapping, a static check, or a command you asked for by name. |
| **The settings you change from inside the program are signed, not encrypted** | `Settings → Overrides` and `agent overrides --set TARGET KEY VALUE` write `.agent-overrides.json` in this tool's own folder — created at first run, `.gitignore`d, never written into your project. The program signs every row, so a row you typed into the JSON by hand (or one moved, changed, added or deleted there) is **refused and named** rather than quietly obeyed, and the run falls back to your profile. That is tamper evidence, not a password: your own account can rewrite the file, and the standard library has no cipher. It never redirects an address a profile or a field already states, never swaps the provider, and never holds a key — a credential-shaped value is refused on the way in. |
| **Git checkpoints back every applied proposal** | Inside a git repository, applying a proposal commits the approved files with `--no-verify` (skipping hooks) and names the session. If files are edited after review, local rollback is blocked to prevent clobbering your later edits, and the tool offers **Targeted Git Restore** to put only that task's files back to its pre-task commit. Strictly no network commands (`push`/`pull`) and no rewritten history. |
| **The repository map is context, not a compiler** | Symbols are parsed with `ast` for Python and bounded scanners for Java, Kotlin, Go, Rust and TypeScript. It tells the model what files declare; it does not type-check, resolve imports or prove the code works. Only running the project's command does that, and a run that never ran is reported as `unverified`, not as a pass. |
| **Small local models write small diffs** | The reference setup is a CPU-only `qwen2.5-coder` on Ollama. Larger models produce better proposals; none of them produce a diff you should apply without reading. |
| **A cloud endpoint means your code leaves the device** | Cloud rows are refused until you approve, the approval is asked per task, cleartext to a remote host is refused outright, and API keys live in memory only — never in a config file, a log line or an error message. |

Running `python agent.py doctor` prints what this machine can actually reach — the local model list,
whether Docker is installed, and which key variables are set (their values are never printed).

---

# ⚡ Quick Start

## 1. Clone & Verify
```bash
git clone https://github.com/mohamedsaad208/AI-Agent.git
cd AI-Agent
```

```bash
# First run, in order: what this machine can run, who answers, which models exist, what the folder
# grants, the offline proof, the five promises, and what `Send` may become. Exit 1 if a row blocks.
python agent.py setup                       # add --repo PATH to check a folder, --arabic, or --yes

# Self-diagnostics: python version, Ollama reachability, Docker, key variables (never their values)
python agent.py doctor

# Deterministic offline demo — no model, no network, and nothing from your repository is executed
python agent.py demo

# The suite. It is stdlib-only and needs no install step: `tests/*` add `src/` to sys.path itself.
python -m unittest discover -s tests

# Windows: the same command, with the interpreter this project is counted against.
# 3.11 is named on purpose — newer interpreters tally subtests differently, so the total moves.
run-tests.cmd
```

The suite is the gate CI runs (`.github/workflows/ci.yml`: 3.11 on Windows and Linux, `compileall`,
`node --check` on the two UI scripts, a wheel build checked for the files the window needs). There is
no pytest or `pip install -e .` step required for the suite. Provider tests use doubles and local test servers; they do not require a live model or cloud API key.

Run the additional JavaScript behavior checks with Node.js (no npm install required):

```bash
node tests/test_plan_markdown.cjs
node tests/test_web_ui_phase2.cjs
node tests/test_web_ui_phase3.cjs
```

To open the local web window without an engine behind it — the same UI, scripted data, useful for
reading the interface before trusting a folder to it:

```bash
python -m pip install -e .
python -m ai_code_engineer.webapp --fake --no-browser --port 8765
```

---

# 💻 Platform Guide

| Platform | GUI WebApp Launcher | Interactive CLI Launcher | Direct Terminal Command |
| :--- | :--- | :--- | :--- |
| **🪟 Windows** | Double-click `Run-Agent.bat` | Double-click `Run-Agent-CLI.bat` | `python desktop.pyw` |
| **🐧 Linux** | `./run-agent.sh` | `./run-agent-cli.sh` | `python3 desktop.pyw` |
| **🍎 macOS** | `./run-agent.sh` | `./run-agent-cli.sh` | `python3 desktop.pyw` |

<details>
<summary><strong>🐧 Linux & 🍎 macOS First-Time Setup</strong></summary>

Make the shell launchers executable:
```bash
chmod +x run-agent.sh run-agent-cli.sh
```

Launch the GUI:
```bash
./run-agent.sh
```

Launch the interactive CLI:
```bash
./run-agent-cli.sh
```
</details>

<details>
<summary><strong>⚙️ Advanced Headless CLI Usage</strong></summary>

```powershell
# Index repository symbols
python agent.py map --repo examples/demo_repo

# Declare a folder Read-only for every surface — window and terminal alike — and lift it again
python agent.py read-only --repo examples/demo_repo
python agent.py read-only --repo examples/demo_repo --off

# Plan and propose code changes with local Ollama
python agent.py plan "Fix add in calculator.py so it adds two numbers" --repo examples/demo_repo --config profiles/local.toml

# Review proposed diff
python agent.py review "<session_id>"

# Apply approved proposal (cryptographically verified)
python agent.py apply "<session_id>" --approve "<sha256_hash>"

# Execute automated test suite
python agent.py verify "<session_id>"

# Check status and outcome of any session
python agent.py status "<session_id>"

# Export session transcript and diff report to Markdown or JSON
python agent.py export-session "<session_id>" --format markdown --out session-report.md

# A declined proposal must be explicitly reopened before Apply can accept it.
# Reopen only restores the offer to review; it does not write project files.
python agent.py reopen "<session_file>" --approve "<sha256_hash>"

# Rollback if needed
python agent.py rollback "<session_id>" --approve "<sha256_hash>"
```
</details>

---

# 🖥️ Interactive Desktop WebApp

The application features a sleek, local WebApp interface served on `127.0.0.1` with:
- **Compact change card:** One current proposal card updates to the applied state. It shows total files, additions and deletions, the first three files, and a toggle for the rest. File descriptions summarize recorded changes; clicking a file opens its diff.
- **Clear decisions:** Apply and Reject are available for pending proposals; applied changes offer rollback. A rejected proposal must be explicitly reopened for review, including when Auto-Apply is enabled. Reopening does not write files.
- **Resizable right rail:** Separate Changes, Tasks, and Checks tabs, activity indicators, and task execution controls keep details beside the conversation.
- **Compact replies:** Click Reply to quote a message. The reply preview links back to the original message when available. Copy and Reply actions share existing message lines and appear on hover or keyboard focus, with a visible touch fallback.
- 📂 **Multi-Project Workspace:** Manage isolated branches, project memory notes, and saved tasks.
- ⚡ **Review-First Diff Inspector:** Side-by-side **Diff / Now / Was** inspector with one-click rollbacks.
- 🤖 **Universal Model Selector:** Switch on the fly between local models (Ollama/DeepSeek) and cloud APIs (Groq, OpenAI, OpenRouter). A thinking model's deliberation arrives as its own collapsible row — capped, redacted, and never folded into the JSON the loop acts on — and an answer or a proposal streams in as it is written instead of appearing all at once after a minute of dots.
- 🧪 **Evidence-Based Checks:** Native test suite runner with JUnit XML proofs and self-healing fix rounds.
- 🐳 **A sandbox you can tick:** the same Checks card runs the project's command inside a pinned image —
  no network, no capabilities, the project as a copy — and says so in the run line and in the exported
  report, so a green from a container never reads as a green from your machine.
- 🧭 **First-Run Card:** On a machine that has never granted a folder, the window opens with the same
  audit `agent setup` prints — what it can run, what answers, the five promises, the three positions —
  built from rows that ask nothing over the network until you press **Run the checks**.
- ✏️ **Overrides you can see:** Settings → Overrides lists every configuration row the program is
  running on — whose profile, whose field, whose signed file — and refuses to pretend a row it cannot
  verify is in force. Both windows and the terminal read the same one file.
- 🌐 **Full Bilingual Arabic & RTL Support:** Dynamic Right-to-Left (RTL) layout when interacting in Arabic, with comprehensive Arabic localization across system notices, error diagnostics, step cards, and review audits.

Both windows are the same product: the web window and the `--tk` fallback share the engine, the
sentences and the decisions, and each keeps its proposed files in a viewer of its own — a sheet over the
chat there, a second window you can move beside the conversation on the desktop.

Checkpoint commits include only the proposal's target files, leaving unrelated staged files out of the commit. They capture whole files, so existing edits inside a target file are included. On Windows, command cancellation checks whether process-tree termination succeeded, attempts a direct-process fallback, and reports when a process remains alive.

## Plan files and progress

Attach a plan inside the selected project using **+ Plan**. Both Web and Tk use the same step reader and verification ledger.

Numbered headings take priority:

```markdown
# Authentication service

## 1. Set up Spring Boot
Inspect the existing pom.xml and complete the application configuration.

## 2. Add login
Implement the endpoint and tests.
```

Numbered lists are also supported when there are no numbered headings:

```markdown
## Tasks
1. **Set up Spring Boot**
   - Inspect existing files before adding dependencies.
2) Add login
   - Implement the endpoint and tests.
```

- Step IDs follow document order, even if written numbers repeat.
- Indented continuation lines belong to their list item; a new section or unindented prose ends it. Nested items and fenced code examples do not become separate top-level tasks.
- Plans currently support **up to 50 steps**. Larger plans are refused with an explanation rather than silently truncated.
- Each step has its own status and session association. A passing command with verification evidence is required before the ledger marks it verified; applying files alone is not completion.
- Ledgers live in `.agent-plans/`, keyed by project root and plan content. Renaming an unchanged plan does not reset its progress.

## Troubleshooting

| Message or symptom | What to do |
| :--- | :--- |
| **Legacy plan ledger is linked to prior work** | An older reader stored the plan as one step, and that step has execution history. Review its session and current files before moving to a revised plan or a backed-up, reviewed ledger migration. The app preserves linked or verified history instead of resetting it automatically. Untouched legacy fallback ledgers can be rebuilt safely. |
| **Plan has too many steps** | Split the work into smaller plans within the current 50-step limit. Existing ledgers do not bypass the check. |
| **Proposal declined / Apply disabled** | Choose **Reopen for review**, inspect the diff, then apply. The CLI equivalent is `agent reopen SESSION_FILE --approve HASH`. |
| **Files changed after review** | Create a new proposal against the current files; do not bypass the stale-file check. |
| **UI still behaves like an older version** | Restart the application after updating its code. If using an installed package, update that installation too; running the module may otherwise load the installed copy rather than this checkout. |

Project files, conversation history, and verification are separate records. Neither a migration nor a successful UI action should be treated as proof that project tests passed.

---

# 🎯 Try These First

- **Fix a Bug with Automated Verification:**  
  *"Read the failing tests in `tests/test_auth.py` and inspect `src/auth.py`. Fix the token expiration validation without breaking backwards compatibility, then run tests."*

- **Implement a Multi-Phase Plan:**  
  Attach a `plan.md` file using the **+ Plan** button in the WebApp:  
  *"Implement Phase 1 from the attached plan only. Create missing DTO classes and verify syntax."*

- **Refactor with AST Context:**  
  *"Inspect the repository structure and refactor `UserService` to extract email notifications into an independent `NotificationService` interface."*

- **Autonomous Self-Healing Loop:**  
  Click **Run & Fix** on the Checks card to allow the agent to run the test suite, read compiler errors, and rewrite code until all tests turn green.

---

# 🏗️ Architecture

```
                     +---------------------------------------+
                     |       Desktop WebApp / Native UI      |
                     +---------------------------------------+
                                         |
                                         v
+---------------------------------------------------------------------------------+
|                           Agent Orchestration Engine                            |
|  - Task Planner & Queue Manager            - Step-by-Step Ledger (Planbook)     |
|  - AST Symbol Indexer & Repo Map           - Persistent Project Memory          |
+---------------------------------------------------------------------------------+
             |                                                  |
             v                                                  v
+--------------------------+                      +-------------------------------+
|      Model Providers     |                      |      Workspace & Security     |
|  - Ollama (Local)        |                      |  - Path Traversal Guard       |
|  - OpenRouter (Cloud)    |                      |  - Zero-Trust Secret Redactor |
|  - OpenAI / Groq / Custom|                      |  - SHA-256 Hash-Locked Diffs  |
+--------------------------+                      +-------------------------------+
                                                                |
                                                                v
                                                  +-------------------------------+
                                                  |     Verification & Checks     |
                                                  |  - Toolchain Detectors        |
                                                  |  - JUnit XML Evidence Parser  |
                                                  |  - Autonomous Repair Loop     |
                                                  |  - Optional Docker Sandbox    |
                                                  +-------------------------------+
```

---

# 📁 Where everything lives

| path | what it is |
| :--- | :--- |
| `src/ai_code_engineer/` | **the product.** `engine.py` runs the loop, `config.py` is the provider table, `providers.py` and `catalog.py` speak to a model, `runner.py` runs *your* project's command — here, or inside the one container shape the tool knows how to seal — `labels.py` holds every sentence in both languages, `intent.py` holds the three write positions and every refusal they speak, `modes.py` keeps a folder's position where the other window and the terminal both read it, `host.py` is the seam the two windows share, `redaction.py` keeps credentials out of what gets stored. |
| `src/ai_code_engineer/webapp/` | the local web window: `server.py` (loopback-only, per-launch token, Host/Origin/CSP), `controller.py` (the state the UI reads), `static/`. |
| `src/ai_code_engineer/gui.py` | the Tk window. Same engine, same sentences, different screen. |
| `tests/` | Python `unittest` coverage plus standalone Node.js rendering checks. `doubles.py` and `helpers.py` provide shared fixtures; a live model is not required. |
| `agent.py` · `desktop.pyw` · `launcher.py` | entry points: CLI, the desktop window, the interactive menu. |
| `profiles/` | TOML model presets. They name the *variable* holding a key and never a key. |
| `docs/` | plans, implementation status, code reviews — **local working notes, gitignored.** They quote this machine's paths, ports and counts, so they are kept on the device that measured them instead of shipped as product files. The rules they describe live in the modules' own docstrings, and the test suite is the gate: nothing in `src/`, `tests/` or CI reads a `docs/` file. |
| `tools/` | development aids for this repository — contrast checks, the dogfood ledger, wheel inspection, demo generators. |
| `sandbox/` | probes that produced a number someone quoted, kept so the number can be re-measured. |
| `examples/` | folders the agent is pointed at to try it out. |
| `archive/` | see `archive/README.md` — including why two experiment folders were **not** moved into it. |
| `.agent-chats/`, `.agent-runs/`, `.agent-plans/`, `.agent-projects.json` | Local conversations, run records, plan progress, and the granted-folder registry. Ignored by git; publishing the source does not upload these records. |

---

# 🤝 Contributing

We welcome contributions from the global open-source community!

1. **Fork** the repository.
2. **Create your feature branch:**
   ```bash
   git checkout -b feature/amazing-feature
   ```
3. **Ensure all tests pass:**
   ```bash
   python -m unittest discover -s tests -v
   ```
4. **Commit your changes:**
   ```bash
   git commit -m "feat: add amazing new feature"
   ```
5. **Push to your fork and submit a Pull Request (PR).**

Please submit contributions through pull requests and keep automated checks passing.

---

# 📄 License
This project is open-source software licensed under the [MIT License](LICENSE).
