<div align="center">

<img src="assets/banner.svg" alt="AI Code Engineer Banner" width="100%"/>

# AI Code Engineer
### Give your coding workflow an autonomous, privacy-first software engineer.

AI Code Engineer is an open-source autonomous agent framework for real-world software engineering: AST repository indexing, cryptographic diff proposals, zero-trust workspace security, automated JUnit test loops, and multi-provider LLM support (Ollama local, OpenRouter, OpenAI, Groq, DeepSeek).

[![License](https://img.shields.io/badge/License-MIT-F59E0B?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3B82F6?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-10B981?style=for-the-badge&logo=linux&logoColor=white)]()
[![Developed with AI](https://img.shields.io/badge/Built%20With-AI%20%26%20Human%20Pairing-8B5CF6?style=for-the-badge&logo=openai&logoColor=white)]()
[![Tests](https://img.shields.io/badge/Tests-620%2B%20Passing-06B6D4?style=for-the-badge&logo=pytest&logoColor=white)]()

[Quick Start](#-quick-start) •
[Why AI Code Engineer](#-why-ai-code-engineer) •
[Platform Guide](#-platform-guide) •
[Try These First](#-try-these-first) •
[Architecture](#-architecture) •
[Contributing](#-contributing)

</div>

---

# 🚀 Why AI Code Engineer

| Feature | Why it matters |
| :--- | :--- |
| 🔒 **Zero-Trust Security** | Path-traversal guards, symlink blocking, and automatic secret redaction (AWS, GitHub tokens, Bearer keys, `.env`) prevent leakage to logs or LLMs. |
| 🛡️ **Cryptographic Diffs** | Nothing reaches your disk without explicit approval. All changes generate `SHA-256` proposal hashes and support one-click rollbacks. |
| ⚡ **100% Offline & Local** | Full first-class support for **Ollama** (`qwen2.5-coder`, `deepseek-coder`, `llama3`). Code stays on your hardware. |
| ☁️ **Multi-Provider Cloud** | Seamlessly switch between **OpenRouter**, **OpenAI**, **Groq**, **DeepSeek**, or custom OpenAI-compatible endpoints. |
| 🗺️ **AST Symbol Indexing** | In-memory symbol extractor (Python, Java/Kotlin, TypeScript/JS, Go, Rust) provides classes, methods, and types without burning context window tokens. |
| 🧪 **Self-Healing Test Loop** | Auto-detects `pytest`, `unittest`, `Maven`, `Gradle`, `npm`, `cargo`, `go test`. Parses JUnit XML output and feeds failures back to the agent for autonomous repair (up to 3 rounds). |
| 🖥️ **Desktop WebApp & CLI** | Beautiful local WebApp with real-time streaming, diff previews, task queuing, and an interactive terminal menu. |

---

# ⚡ Quick Start

## 1. Clone & Verify
```bash
git clone https://github.com/mohamedsaad208/AI-Agent.git
cd AI-Agent
```

```bash
# Run self-diagnostics
python agent.py doctor

# Run deterministic sandbox demo (No LLM required)
python agent.py demo

# Run comprehensive test suite
python -m unittest discover -s tests -v
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

# Plan and propose code changes with local Ollama
python agent.py plan "Fix add in calculator.py so it adds two numbers" --repo examples/demo_repo --config profiles/local.toml

# Review proposed diff
python agent.py review "<session_id>"

# Apply approved proposal (cryptographically verified)
python agent.py apply "<session_id>" --approve "<sha256_hash>"

# Execute automated test suite
python agent.py verify "<session_id>"

# Rollback if needed
python agent.py rollback "<session_id>" --approve "<sha256_hash>"
```
</details>

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
                                                  +-------------------------------+
```

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

> ⚠️ **Branch Protection Note:** Direct pushes to `main` are restricted. All contributions must go through Pull Requests and pass automated verification.

---

# 📄 License
This project is open-source software licensed under the [MIT License](LICENSE).
