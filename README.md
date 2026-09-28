# 🤖 AI Code Engineer (Autonomous Software Engineering Agent)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-green.svg)]()
[![Developed with AI](https://img.shields.io/badge/Developed%20with-AI%20%26%20Human%20Pairing-8A2BE2.svg)]()
[![Tests](https://img.shields.io/badge/Tests-620%2B%20Passing-brightgreen.svg)]()

> 💡 **Developed with AI:** This project was architected and developed using advanced Human-AI pair programming, demonstrating modern agentic coding workflows, zero-trust security isolation, and self-healing software loops.
>
> 🚀 **Autonomous, privacy-first AI Software Engineer Agent** running locally or in the cloud. Designed to inspect repositories, parse code structures via AST, propose cryptographic diffs, execute sandboxed verification, and fix errors autonomously.

---

## 🌟 Key Features

- 🔒 **Zero-Trust Security & Workspace Isolation:**
  - Strict path traversal barriers, symlink blocking, and secret redaction (AWS, GitHub tokens, Bearer keys, .env).
  - Explicit cryptographic review (`SHA-256` proposal hashes) before any file write.
  - Safe rollback capability without destructive git resets.

- 🧠 **Multi-Provider LLM Engine:**
  - **Local (Offline & Private):** Full integration with **Ollama** (e.g., `qwen2.5-coder`, `deepseek-coder`, `llama3`).
  - **Cloud:** **OpenRouter**, **OpenAI**, **Groq**, **DeepSeek**, and generic OpenAI-compatible APIs.

- 🗺️ **Repository AST Indexing & Context Optimization:**
  - In-memory symbol parser (Python AST, Java/Kotlin, JS/TS, Go, Rust) provides the LLM with method signatures, class hierarchies, and dependencies without burning context tokens on unnecessary file reads.

- 🧪 **Automated Test & Self-Correction Loop:**
  - Automatic detection of project toolchains (`pytest`, `unittest`, `Maven`, `Gradle`, `npm/node`, `cargo`, `go test`).
  - Parses JUnit XML reports for evidence-backed test verification.
  - Self-healing loop that feeds test failures back to the LLM for autonomous fixes (up to 3 rounds).

- 🖥️ **Modern Desktop WebApp & Interactive CLI:**
  - Integrated local WebApp with real-time streaming, diff previews, task queuing, and interactive chat.
  - Interactive CLI text menu for headless or remote server workflows.

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.11** or newer.
- *(Optional for Local LLM)*: [Ollama](https://ollama.com/) running locally (`ollama serve`).

### 1. Clone the Repository
```bash
git clone https://github.com/mohamedsaad208/AI-Agent.git
cd AI-Agent
```

### 2. Run Diagnostics & Verification
```bash
# Run self-diagnostics
python agent.py doctor

# Run deterministic sandbox demo (No LLM required)
python agent.py demo

# Run comprehensive test suite
python -m unittest discover -s tests -v
```

---

## 💻 How to Run (Platform Guide)

### 🪟 Windows
* **Desktop WebApp (GUI):**
  - Simply double-click `Run-Agent.bat`
  - *Or via terminal:*
    ```powershell
    python desktop.pyw
    ```
* **Interactive CLI:**
  - Double-click `Run-Agent-CLI.bat`
  - *Or via terminal:*
    ```powershell
    python launcher.py
    ```

---

### 🐧 Linux (Ubuntu, Debian, Fedora, Arch)
1. **Give execution permissions (first time only):**
   ```bash
   chmod +x run-agent.sh run-agent-cli.sh
   ```
2. **Launch Desktop WebApp:**
   ```bash
   ./run-agent.sh
   ```
3. **Launch Interactive CLI Menu:**
   ```bash
   ./run-agent-cli.sh
   ```

---

### 🍎 macOS
1. **Give execution permissions (first time only):**
   ```bash
   chmod +x run-agent.sh run-agent-cli.sh
   ```
2. **Launch Desktop App:**
   ```bash
   ./run-agent.sh
   # Or directly:
   python3 desktop.pyw
   ```
3. **Launch Interactive CLI Menu:**
   ```bash
   ./run-agent-cli.sh
   ```

---

### ⚡ Direct Python Execution (All Platforms)
```bash
# Start WebApp server directly
python -m ai_code_engineer.webapp

# Start headless CLI directly
python agent.py --help
```

---

## 💡 Usage Examples

### Running with a Local Model (Ollama)
```powershell
# Index repository symbols
python agent.py map --repo examples/demo_repo

# Plan and propose code changes
python agent.py plan "Fix add in calculator.py so it adds two numbers" --repo examples/demo_repo --config profiles/local.toml
```

### Review, Apply & Verify
```powershell
# Review proposed diff
python agent.py review "<session_id>"

# Apply approved proposal (cryptographically verified)
python agent.py apply "<session_id>" --approve "<sha256_hash>"

# Execute automated test suite
python agent.py verify "<session_id>"

# Rollback if needed
python agent.py rollback "<session_id>" --approve "<sha256_hash>"
```

---

## 🏗️ Architecture Overview

```
       +-------------------------------------------------------+
       |             Desktop UI / WebApp / CLI                 |
       +-------------------------------------------------------+
                                  |
                                  v
+---------------------------------------------------------------------+
|                      Agent Orchestration Engine                      |
|  - Task Planner & Queue Manager     - Step-by-Step Ledger           |
|  - AST Symbol Indexer & Repo Map    - Memory & Standing Notes       |
+---------------------------------------------------------------------+
            |                                         |
            v                                         v
+-----------------------+                 +-----------------------+
|   Model Providers     |                 |  Workspace & Security |
|  - Ollama (Local)     |                 |  - Path Traversal Guard
|  - OpenRouter (Cloud) |                 |  - Secret Redactor    |
|  - OpenAI / Generic   |                 |  - Hash-locked Diff   |
+-----------------------+                 +-----------------------+
                                                      |
                                                      v
                                          +-----------------------+
                                          | Verification & Checks |
                                          |  - JUnit XML Parser   |
                                          |  - Self-Healing Loop  |
                                          +-----------------------+
```

---

## 🤝 Contributing

We welcome contributions from the community! Because this repository enforces strict code quality and branch protection, please follow these steps:

1. **Fork** the repository.
2. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Write clean code & ensure all tests pass:**
   ```bash
   python -m unittest discover -s tests -v
   ```
4. **Commit your changes:**
   ```bash
   git commit -m "feat: describe your feature clearly"
   ```
5. **Push to your fork and submit a Pull Request (PR).**

> ⚠️ **Branch Protection Note:** Direct pushes to `main` are restricted. All changes must be submitted via Pull Requests and pass automated checks before merging.

---

## 📄 License
This project is open-source under the [MIT License](LICENSE).
