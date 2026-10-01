# AI Developer Agent — Architecture and Implementation Plan

**Target Audience:** Java/Spring engineering teams operating within banking/enterprise environments.  
**Last Updated:** September 23, 2026 — UI 2.1 interface and verified implementation status.  
**Adopted Architectural Decision:** Python for the Agent Runtime to accelerate Version 1 delivery, utilizing local Ollama + OpenRouter. Target repositories remain Java/Spring Boot.  
**Core Objective:** Build a developer-centric coding agent capable of repository understanding, structured planning, bounded code modification, and automated verification, maintaining consistent orchestration logic whether backed by a local model or cloud providers.

This document consolidates earlier strategic discussions into an actionable implementation plan. Section 0 describes what has actually been implemented; the remaining architectural sections specify target design goals. Code contracts, Python snippets, and YAML examples in design sections serve as illustrative specifications unless noted otherwise in the implementation log.

---

## 0. Implementation Log and Approved Decisions — September 23, 2026

### Update UI 2.3 — Projects and Chats with Isolated Contexts

- **Project Tree Sidebar:** The sidebar organizes **Projects & chats**: each project manages its own chat sessions, allowing users to expand/collapse projects, select existing chats, or initiate a **New chat**.
- **Project Identity:** A project's identity is defined strictly by its normalized absolute path, case-folded on Windows. Folder names alone do not define identity, preventing distinct folders with identical names from colliding.
- **Independent Sessions:** Every chat carries a unique `chat_id`. Each request generates an independent execution session saved alongside the project root and chat ID. The chat ID is incorporated into the proposal hash.
- **Bounded Conversational Context:** Continuing within an existing chat injects a summary of its most recent turns only (capped at up to 6 turns, and 4,000 characters or one-sixth of the retrieval budget, whichever is smaller). Both **project root and chat ID must match simultaneously**. Chat histories across different projects or sibling chats are never blended.
- **Structured Context Log:** Context history carries the user request, status, and summary only; prior raw snapshots, diffs, or stale file contents are never re-transmitted. The agent reads current disk state, distinguishing unapplied proposals from applied modifications, and logs utilized session IDs in `context_session_ids`.
- **Project Switching:** Switching projects clears the composer draft, attached plan, active conversation, activity logs, cloud consent status, and review pane. Creating a new chat starts a fresh history under the active project. Opening an existing chat restores its associated project, plan, and message history.
- **Legacy Chat Migration:** Older sessions are grouped by their project root, with each historical session treated as an independent chat keyed by its session ID, preserving original files and hashes.
- **Project Registry:** The list of known project paths is stored in `.agent-projects.json`, which is strictly protected against agent tool access alongside `.agent-runs`. Sessions remain in local storage with logical isolation; operating-system-level multi-tenancy or separate per-project databases are not part of this layer.
- **Test Verification:** Verified via mock provider tests: cross-project or cross-chat context leakage was confirmed absent, even when chat IDs were identical across distinct project roots. Tree navigation, project switching, context flushing, and backward compatibility passed all 28 core unit test cases. Live LLM execution and Spring Boot runtimes were not part of this harness.
- **Supercedence:** This update supersedes the UI 2.1 stateless model; multi-turn execution now leverages the bounded history described above. The window title reflects **UI 2.3**.

### Update UI 2.2 — Copy and Paste Interactions

- Replaced rendered message text with read-only text widgets supporting mouse selection, Ctrl+A (Select All), and Ctrl+C (Copy), while preserving visual bubble styling.
- Added context menus (right-click) for messages (Copy / Select all) and the prompt composer (Cut / Copy / Paste / Select all). Pasting is permitted into the prompt composer only, never into assistant response bubbles.
- Disabled cut and paste operations within the composer while the agent is actively executing. Layout rendering, navigation, and busy-state inspections passed verification without generating or altering demo files.
- Window title reflects **AI Code Engineer · UI 2.2**; the UI 2.1 design detailed below forms the foundation for this enhancement.

### Deployment Environment and Current Version

- **Active Codebase:** `D:\AI\AI-Agent`, launched via `Run-Agent.bat`.
- **Interface Version:** **UI 2.1**; indicated in the window title to distinguish from older processes. The batch launcher must be restarted after code changes.
- **Interactive Terminal:** `Run-Agent-CLI.bat` launches the text-based console menu. No external GUI dependencies required; standard library Python/Tkinter is utilized.
- **Reference Demo Repository:** Located at `examples/demo2`, accompanied by reference plan `examples/demo2/plan.md`. This represents a separate Spring Boot application plan, not the agent's internal development plan.

### Verified Capabilities Matrix

| Dimension | Implemented Reality |
| :--- | :--- |
| **Language** | Python 3.11+ to accelerate initial delivery, targeting Java/Spring Boot repositories. Python is an execution choice, not an architectural limitation. |
| **Providers** | Local Ollama and cloud OpenRouter, interfaced through a provider layer decoupled from the user interface. |
| **Model Selection** | Enumerates local Ollama models upon startup and refresh; users select from available models. OpenRouter models update dynamically, supporting Free/Paid modes, name search, and context/pricing metadata. |
| **Cloud Controls** | Explicit consent prompt required before transmitting code to cloud endpoints; cloud-hosted Ollama models enforce identical controls. Paid tier selection requires explicit activation; API keys remain ephemeral in memory and are never written to disk. |
| **Agent Loop** | Bounded JSON actions: `list_files`, `read_file`, `search_code`, `propose`, or `blocked`. Enforces hard limits on execution turns and invalid tool attempts. Direct arbitrary shell execution is denied to the model. |
| **Context Retrieval** | Repository map paired with lexical search and policy-bounded file reads; AST/LSP and vector indexing are not yet present in this tier. |
| **File Creation** | Reading a non-existent file returns `not_found` with policy metadata permitting creation rather than aborting. New files must be formally proposed and approved before disk writes. |
| **Plan Attachment** | Attach/View/Clear functionality for project Markdown plans. Plan text is injected into prompt context outside the 4,000-character task prompt limit, under dedicated reference limits. The immediate user prompt specifies the target phase and takes precedence over historical plan steps. |
| **Plan Protection** | Attached plans are strictly read-only. File path and SHA-256 digest are recorded in the session ledger; proposals are prevented from altering the plan, and digests are re-verified prior to apply. CLI supports `--plan-file`. |
| **Review & Diffs** | Proposals are stored in `.agent-runs/<id>/session.json`, providing summary, unified diff, and Before/After inspections. User approval is cryptographically tied to the proposal hash, followed by pre-write file version checks. Proposals are capped at 8 files per turn. |
| **Rollback** | Restores session-specific file changes while protecting subsequent user edits. Interrupted or partial apply states are persisted for recovery. |
| **Verification** | Optional syntax checks and fixed build/test recipes executed strictly via Docker; unexecuted test claims are rejected. Absences of required isolation blocks execution. |
| **Background Execution**| Long-running operations execute on worker threads with activity status feedback; planning terminates safely at the next turn boundary upon stop requests. |

### UI 2.1 Interface Overview

The interface features a modern dark/light layout tailored for developer workflows:
- Light sidebar displaying the active project, recent tasks, and session loader.
- Conversational thread with blue user prompt bubbles; rounded composer dock at the bottom.
- Plan attachment and model selector buttons situated beside the composer dock.
- Clicking the active model name or **Project & model settings** opens provider, model, search, API key, and cloud consent configurations.
- **Outputs / Sources** inspector card on the right displaying proposed files and attached plans; automatically collapses on narrow windows.
- Streamlined **Chat / Changes / Activity** navigation. File review, approval, and rollback reside within Changes.
- **Ctrl+Enter** submits proposals; Enter adds a newline. The Stop button appears only during cancellable operations.
- The interface acts as a proposal review workbench rather than an open-ended conversational chat: project context and plan remain bound, but each submission generates a discrete proposal based on immediate requests and current disk state.

### Demo Application Workflow

1. Launch `Run-Agent.bat` and confirm **UI 2.1** appears in the title.
2. Select `D:\AI\AI-Agent\examples\demo2` and attach `plan.md`.
3. Choose an Ollama model or an OpenRouter tier based on data sensitivity and budget.
4. Specify the target phase explicitly, e.g.:  
   `Read the attached plan and inspect the current project. Implement Phase 1 only. Preserve existing work. Do not run builds, tests, or the application.`
5. Submit the prompt, navigate to Changes to inspect proposed diffs, check approval, and click Apply.
6. Verify the application manually or trigger authorized test recipes before advancing to subsequent phases.

### Verified Testing Boundaries

- Historical core tests do not inherently validate subsequent UI updates. Initial model selection, file creation, and plan attachment features were initially implemented without unit tests per user direction.
- UI validation subsequently verified Tk initialization, layout responsiveness across 1500×880, 1000×650, and 1850×950 resolutions, modal drawers, busy/Stop states, Changes pane transitions, and read-only plan ingestion.
- Automated window screenshot captures encountered Windows authorization timeouts; dimensional checks do not substitute for visual human inspection.
- Cloud generation, Spring Boot code modifications, and live test runs were not executed during UI smoke tests.

### Immediate Backlog Priorities

1. Complete visual UI verification and test the end-to-end lifecycle: Attach → Select Model → Propose → Review → Apply.
2. Execute Spring authentication increments following `examples/demo2/plan.md`, validating live execution before approving changes.
3. Validate Docker container isolation on a configured host and add machine-readable test reports; finalize Gradle test inspection.
4. Implement bounded repair cycles, alternative proposal approvals, and context tool enhancements.
5. Introduce workspace locks/worktrees, robust session recovery, Skills, MCP, and IDE integration.
6. Fulfill enterprise banking prerequisites: SSO, session encryption/auditing, centralized data egress enforcement, model benchmarking, and security isolation. The current release represents an MVP workbench.

---

## 1. Architectural Decision

We construct an **Agent Runtime decoupled from the model provider**, starting as a single orchestrated agent with planning, execution, testing, and review capabilities. Components are separated internally within a modular architecture, avoiding premature microservice or multi-agent complexity.

The model proposes actions; the runtime validates schemas, enforces policy permissions, executes actions within sandboxes, and verifies outcomes. High code generation quality alone is insufficient: success requires reviewable diffs, empirical verification evidence, and strict security and budget guardrails.

```text
Developer → CLI / Web / IDE
                  │
          Agent Orchestrator ───── Session / Event Store
                  │
       ┌──────────┼───────────────┐
       │          │               │
 Context Engine  Planner       Skills Router
       │          │               │
 Repo Map       Plan + Scope    Versioned Skills
 Search           │
       └──────────┼────────────────┘
                  │
             Model Gateway
        ┌─────────┴──────────┐
   Local Provider       Cloud Provider
        └─────────┬──────────┘
            Proposed tool call
                  │
        Schema Validation + Policy Engine
                  │
          Approval, if required
                  │
              Tool Runtime
       ┌──────────┴───────────┐
 Built-in Tools          MCP Adapter
       └──────────┬───────────┘
          Isolated Worker / Sandbox
                  │
       Observe → Verify → Fix / Report
```

All execution paths—including MCP adapters, hooks, and local scripts—must pass through the policy engine. Model inference requests adhere to data classification and egress policies. Sandboxes provide true security isolation; Git worktrees provide workspace isolation.

---

## 2. Why Python? And is it Mandatory?

**Python is not mandatory.** Its adoption in the initial prototype served to accelerate experimentation rather than establish an immutable architectural requirement. Decoupling model inference behind a standard protocol allows the Agent Runtime to be authored in any suitable language.

| Evaluation Criteria | Python | Java | Kotlin |
| :--- | :--- | :--- | :--- |
| **Prototyping & AI Ecosystem** | Highly suitable; compact experimentation loops | Mature; requires additional scaffolding | Concise syntax with JVM ecosystem access |
| **Team Alignment (Java/Spring)** | Requires additional tooling and runtime management | Directly aligns with team skills and CI/CD pipelines | Excellent if already adopted within the organization |
| **Type Contracts & Refactoring** | Requires external type checking (mypy) and disciplined testing | Robust static typing and compile-time contract enforcement | Strong static typing with language-level null safety |
| **Enterprise Integration** | Viable, but requires integration with banking platforms | Native fit for existing Spring enterprise stacks | Seamless JVM/Spring interop with Kotlin runtime |
| **Operations & Maintenance** | Requires Python virtual environments and package curation | Leverages existing JVM monitoring, profiling, and deployment tools | Standard JVM operations with library compatibility checks |
| **Model Fine-Tuning & Specialized Research** | Predominant industry choice | Typically consumes inference endpoints | Consumes inference endpoints via JVM clients |

**Approved Decision:** Python is selected for the Agent Runtime to accelerate initial version delivery. While Java/Spring was evaluated to eliminate operational divergence, Python remains the active implementation language. We maintain strict type annotations, explicit contracts, comprehensive unit/integration test suites, and pinned dependencies. The agent's implementation language does not need to match target repositories: it manages Java/Spring codebases and executes Maven/Gradle toolchains within isolated workers.

The agent runtime is structured as a modular Python package with explicit boundaries, decoupled CLI frontends, and provider adapters, avoiding framework lock-in. Production logic, orchestration, and policy rules remain native to the repository. Spring AI serves as a reference for JVM alternatives rather than an active dependency.

The runtime language does not dictate model selection. Models are benchmarked on real Java/Kotlin repositories, evaluating tool accuracy, patch reliability, latency, memory consumption, and cost.

---

## 3. Component Boundaries and Responsibilities

| Component | Core Responsibility | Explicit Exclusions |
| :--- | :--- | :--- |
| **Orchestrator** | Task lifecycle, state transitions, budgets, cancellation | Direct shell execution |
| **Planner** | Testable objectives, file scopes, risk classification, checks | Granting permissions |
| **Context Engine** | Search, indexing, symbol maps, context budgeting | Blindly uploading entire repositories |
| **Model Gateway** | Unifying requests, schemas, responses, and capabilities | Bypassing data egress policies |
| **Policy Engine** | Deterministic decisions: Allow / Deny / RequireApproval | Trusting model self-attestations |
| **Tool Runtime** | Schema validation, execution, timeouts, structured outputs | Expanding task scopes |
| **Verifier** | Executing test recipes and evaluating empirical evidence | Accepting prose as proof of success |
| **Session Store** | Checkpoints, decisions, auditable resumable events | Storing raw secrets or chain-of-thought scratchpads |

The system begins as a modular monolith control plane paired with isolated worker processes for untrusted code execution. Build and test recipes never execute inside the privileged control process.

---

## 4. Model Abstraction and Contracts

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelCapabilities:
    tool_calling: bool
    structured_output: bool
    streaming: bool
    context_window_tokens: int


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, object]


class ModelProvider(Protocol):
    def capabilities(self) -> ModelCapabilities: ...

    async def generate(
        self, request: ModelRequest, cancellation: CancellationToken
    ) -> ModelTurn: ...
```

`ModelRequest`, `ModelTurn`, and `CancellationToken` define runtime interface protocols. Requests carry message histories, allowed tool schemas, timeouts, and output budgets. Responses return text content, tool calls, finish reasons, and usage metrics. Inputs and outputs are validated at runtime; type annotations alone do not guarantee payload validity.

Initial provider adapters: `OllamaProvider` (local) and `OpenRouterProvider` (cloud) implementing the unified `ModelProvider` protocol. OpenRouter adapters utilize OpenAI-compatible transports while maintaining distinct routing and privacy controls. Direct adapters for OpenAI, Azure, Gemini, or vLLM can be added as needed. API compatibility does not imply identical token accounting, tool reliability, or streaming semantics.

Implementation Rules:
- Configure provider/model selections via external configuration; avoid hardcoding model IDs in business logic.
- Execute contract tests across supported model and server versions, validating multi-tool calling, invalid JSON recovery, timeouts, truncation, and cancellation.
- Accumulate complete streaming tool arguments before executing; partial streaming execution is prohibited.
- Reject unrecognized tool names or arguments violating JSON schemas. Allow bounded recovery turns for malformed responses without extracting shell commands from unstructured text.
- If a model fails tool-calling contract tests, restrict it to advisory chat mode; do not grant it write execution tools.
- Network errors employ bounded exponential backoff. Regenerating responses incurs cost; side-effecting tools require state reconciliation prior to retry.
- Silent fallback from local to cloud execution is strictly prohibited. Altering data processing boundaries requires explicit user authorization.

In function-calling architectures, models emit structured tool calls; the runtime executes them and returns results correlated by call ID. Valid JSON does not confer authorization.

---

## 5. Agent Loop and Task State Machine

```text
CREATED → DISCOVERING → PLANNING → [WAITING_APPROVAL]
        → EXECUTING → VERIFYING → COMPLETED
                         │
                         └→ REPAIRING → VERIFYING

Any active state → CANCELLED / FAILED / BLOCKED
```

1. **Intake:** Record user identity, repository path, data classification, task prompt, and acceptance criteria.
2. **Baseline:** Capture baseline git commit, working tree state, and toolchain versions.
3. **Discovery & Planning:** Assemble context, inspect symbols, and propose a scoped plan.
4. **Approval:** Solicit explicit user approval when required by policy; record approval duration and hash scope.
5. **Execution Cycle:** Construct context → request model action → validate schema → evaluate policy → execute in sandbox → record observation.
6. **Verification & Repair:** Execute test recipes upon change completion; trigger bounded repair rounds upon failure.
7. **Reporting:** Emit an empirical report detailing applied changes, verification test results, and unexecuted checks.

```text
while task is active and budget remains:
    proposed = model.generate(context.allowedView())
    if proposed requests completion:
        run required verification and evaluate acceptance criteria
    else:
        validate schema and resolve canonical targets
        decision = policy.evaluate(identity, scope, targets, action)
        enforce decision and any bound approval
        persist execution intent
        execute once in sandbox; record observation and checkpoint
```

Initial operational thresholds: 30 model turns, 60 tool invocations, 3 repair attempts, and a 20-minute overall task timeout with independent build step timeouts. Detect repetitive identical failures and break loops with actionable diagnostic messages. Budget exhaustion is recorded as a failure, never as a completion.

Persist `taskId`, `toolCallId`, status, baselines, plan versions, approvals, policy versions, model identifiers, artifact hashes, and tool results. Resume interrupted runs from checkpoints; reconcile uncertain states before re-executing remote mutations. Employ idempotency keys where supported.

---

## 6. Context Engineering and Repository Mapping

Initial implementation utilizes ripgrep, Git metadata, and build toolchain descriptors, followed by symbol extraction. Vector databases are not mandatory for initial versions.

```text
Repo snapshot → File inventory → Symbol index → Repo map
Task terms → lexical search → related symbols/tests → selected excerpts
```

For each indexed symbol, record: identifier name, type, signature, file path, line range, module association, file SHA-256 hash, and potential import/call references. The Repository Map represents an architectural summary rather than an exhaustive code dump.

Spring Boot Specifics:
- Detect Maven/Gradle multi-module hierarchies, Java/Kotlin versions, and test source sets.
- Map controller endpoints to corresponding DTOs, service beans, repositories, and unit/integration tests.
- Tree-sitter provides polyglot structural parsing; JavaParser provides deep Java analysis; LSP integration provides semantic reference graphs.
- Treat detected relationships as heuristics given Spring's dynamic reflection and runtime dependency injection; corroborate findings against source declarations and test executions.
- Invalidate cache entries by file content hash; isolate caches per repository and branch; re-read source files prior to patching.
- Exclude `.git`, build directories, compiled binaries, credentials, and sensitive data files from indexing. Enforce read restrictions across untracked files and symlinks.
- Integrate local embeddings only if empirical benchmarks demonstrate that lexical search and symbol indexing are insufficient.

Sample Repository Map:
```text
payments-api
  TransferController.transfer(TransferRequest)
    → TransferService.executeTransfer(...)
  TransferRequest(amount, fromAccount, toAccount)
  TransferServiceTest
```

Reserve context window space for model output generation and tool observation payloads. Indicative allocation: 15% system policies and task contract, 15% plan and summary, 50% relevant source code and test fixtures, 20% recent execution turns. Summarize large tool outputs with references to complete files; preserve critical error diagnostics.

---

## 7. Tools and Tool Registry

| Tool Category | Initial Toolset | Enforcement Controls |
| :--- | :--- | :--- |
| **Read** | `list_files`, `read_file`, `search_code`, `find_symbol` | Authorized paths, character and byte limits |
| **Write** | `create_file`, `apply_patch` | Expected pre-write hash, diff line ceilings, atomic replacement |
| **Git** | `git_status`, `git_diff` | Scoped repository root, metadata inspection only |
| **Verification** | `compile_project`, `run_tests`, `run_test`, `run_lint` | Approved build recipes inside isolated sandbox |
| **Dependencies** | `read_build_file`, `inspect_dependency` | Approved mirrors and internal caches; no unauthorized downloads |
| **Shell** | `restricted_shell` | Disabled by default; explicit executable and argument array |

Tool definitions include JSON Schema specifications, descriptive summaries, side-effect classifications, required permissions, execution timeouts, output size ceilings, and idempotency guarantees. Tool results return `status`, `exitCode`, `duration`, redacted `stdout/stderr`, `artifacts`, `changedPaths`, truncation indicators, and structured error payloads.

`apply_patch` verifies expected file contents prior to writing, preventing accidental overwrites if files were edited concurrently. Modifications execute sequentially per workspace; read operations may execute concurrently under snapshot consistency.

Execute processes using direct executable and argument arrays, avoiding shell string interpolation. Bare allowlists for `mvn` or `gradle` are insufficient, as build plugins, wrappers, and test suites execute arbitrary code. Sandboxed isolation remains mandatory for all build recipes.

---

## 8. Permissions and Policy Engine

The Policy Engine executes deterministic code outside the LLM, issuing decisions: `ALLOW`, `DENY`, or `REQUIRE_APPROVAL`. Denials provide human-readable rationales; configuration errors fail closed.

| Privilege Level | Operations | Default Policy |
| :--- | :--- | :--- |
| **Read** | Authorized file reads, code search, diff inspection | Automatic within task workspace scope |
| **Write** | Patch application, file creation | Permitted within approved plan and isolated workspace |
| **Execute** | Project compilation, test suite execution | Isolated worker running approved recipes |
| **External Effect** | Git push, remote data transfer, ticket updates | Explicit approval per action, payload, and destination |
| **Production** | Production deployment, live databases, production secrets | Strictly prohibited within initial agent scope |

Trust Hierarchy: Enterprise Policy → Trusted Project Settings → Authorized User Scope → Reviewed Repository Skills → Model Outputs and Untrusted Data. `AGENTS.md` and repository instructions cannot expand enterprise permissions. Privilege escalation requires out-of-band administrative authorization.

Practical Safeguards:
- Resolve canonical filesystem paths and reject traversal attempts (`..`), symlinks, and Windows junctions.
- Re-verify target paths upon opening to mitigate race conditions (TOCTOU).
- Decouple secret read access from model provider transmission permissions.
- Enforce resource quotas across CPU, memory, child processes, disk writes, and execution duration; disallow privilege escalation, host mounts, and Docker socket exposure.
- Treat external scripts, MCP responses, README documentation, and code comments as untrusted data susceptible to prompt injection.
- Execute with least-privilege service identities rather than inheriting full developer user environments.

---

## 9. Planning and Approval

A Plan represents a persisted, auditable entity rather than transient conversational text. It encapsulates objectives, acceptance criteria, target files, tooling requirements, data egress parameters, verification checks, risk levels, and rollback procedures.

```yaml
planVersion: 1
objective: Validate transfer amount before service invocation
expectedFiles:
  - payments-api/src/main/java/example/TransferRequest.java
  - payments-api/src/test/java/example/TransferControllerTest.java
acceptance:
  - Zero and negative amounts receive the existing validation error contract
  - Valid requests preserve current behavior
checks:
  - compile
  - focused-unit-tests
  - api-contract-tests
risk: medium
```

In initial enterprise deployments: read-only analysis proceeds automatically, followed by plan approval prior to code modification. Teams may subsequently enable auto-apply for low-risk changes under predefined policies. Approvals are not re-requested within approved scopes; modifications to protected files or external destinations trigger re-evaluation.

Approvals are cryptographically bound to user identity, task ID, plan hash, action type, target arguments, expiration timestamps, and policy versions. Approving a code change plan does not grant permission to push to remote repositories.

---

## 10. Skills, Repository Instructions, and Hooks

Skills are modular, versioned packages of procedural knowledge loaded on demand, replacing oversized static system prompts:

```text
skills/
  task-intake/SKILL.md
  java/SKILL.md
  kotlin/SKILL.md
  spring/SKILL.md
  testing/SKILL.md
  security/SKILL.md
  code-review/SKILL.md
  api-compatibility/SKILL.md
  debugging/SKILL.md
```

Each Skill specifies applicability criteria, version/hash, execution guidelines, reference links, and verification criteria. Selection rationales are recorded in the audit log. Example: modifying an authentication endpoint loads Spring, Security, and API Compatibility skills, excluding Kotlin if inapplicable.

Review skill sources and bundled scripts; pin trusted package versions. Skills provide contextual guidance; they do not grant permissions. Repository instructions are discovered hierarchically from trusted paths according to deterministic precedence rules.

Hooks (`pre_tool`, `post_edit`, `pre_report`) execute validation checks or intercept actions. Hooks cannot override security policies or execute un-sandboxed code. Failure of a mandatory security hook halts task execution.

---

## 11. Verification Loop

```text
Patch → Compile → Lint / Static checks → Unit tests
      → Relevant integration / contract tests → Security checks
      → Diff review → Acceptance decision
                      │
                      └─ Failure → Diagnose → Bounded fix → Recheck
```

Tailor verification checks to the modification: DTO changes require serialization and API contract validation; persistence alterations require transactional or integration tests. Production databases are never utilized; isolated test environments and synthetic fixtures are mandatory.

- Capture baseline test results prior to modifications to differentiate pre-existing defects from regressions.
- Correlate test outcomes with specific code commits/hashes; subsequent edits invalidate affected checks.
- Persist executed command strings, exit codes, test counts, and report files. Exit code 0 without executed tests does not constitute verification.
- Prohibit repairing code by disabling tests, suppressing security rules, or weakening assertions without explicit justification and review.
- Terminate with a partial/blocked status after 3 failed repair rounds, presenting diagnostic outputs and current diffs.
- Tooling failures (missing Docker daemon, dependency download timeouts) are recorded as unexecuted checks rather than test passes or code failures.
- Diff review inspects for scope creep, secrets, breaking API changes, and unexpected generated artifacts.

Every completed task emits an empirical report: modifications made, rationale, modified files, test outputs, unexecuted checks, remaining risks, and rollback commands. Verification claims require backing artifacts.

---

## 12. Model Context Protocol (MCP) as an Integration Layer

Core filesystem and build tools remain built-in. MCP clients provide extensibility for GitLab/GitHub, Jira, SonarQube, Confluence, and internal enterprise services as reliable servers become available.

```text
MCP Server discovery → Approved registry → Tool schema normalization
                   → Policy → Approval if needed → Invocation
                   → Output validation / redaction → Agent context
```

Pin server identities, versions, allowed tools, and schemas. Schema alterations or newly introduced tools require administrative review. Server-reported metadata (e.g. read-only) provides advisory signals; the runtime independently evaluates policy risks.

For local stdio transports, spawn dedicated processes with sanitized environments. For remote transports, enforce authentication, least-privilege scopes, and destination validation. Mitigate SSRF vulnerabilities and block access to cloud metadata endpoints.

Pin protocol versions compatible across client and server implementations; test negotiation, timeouts, cancellation, and tool schema evolution. Failures in optional MCP tools must not impair core agent operations; unexecuted verification checks prevent claiming task completion.

---

## 13. Online / Offline Deployment Profiles

| Dimension | Air-Gapped Environment | Enterprise On-Premises | Controlled Online |
| :--- | :--- | :--- | :--- |
| **Inference** | Local within isolated perimeter | Internal enterprise model server | Approved cloud provider via secure gateway |
| **Network** | Zero outbound routing; loopback only for local inference | Internal allowlist; no public internet | Egress restricted to approved endpoints |
| **Dependencies** | Pre-bundled packages and container images | Internal artifact mirrors (Nexus/Artifactory) | Approved mirrors with policy-governed downloads |
| **MCP** | Local within secure zone | Internal enterprise servers | Scoped servers per integration |
| **Embeddings** | Local or disabled | Internal services | Subject to data classification policies |
| **Telemetry** | Local storage only | Enterprise telemetry platform | Redacted and sent to approved collectors |
| **Cloud Fallback** | Strictly prohibited | Disabled by default | Permitted only under explicit policy |

Offline deployments encompass package registries, tokenizers, model weights, license validations, telemetry, crash reporting, MCP endpoints, and DNS resolution. On-premises networks are not automatically air-gapped.

Proposed Configuration Schema:
```yaml
profile: bank-onprem
model:
  provider: ollama
  modelRef: approved-coding-model
  endpointRef: internal-inference
  cloudFallback: false
network:
  default: deny
  allowedServiceRefs: [internal-inference, artifact-mirror]
execution:
  sandboxRequired: true
  rawShell: false
  maxToolCalls: 60
  maxRepairAttempts: 3
mcp:
  enabled: true
  allowedServerRefs: [internal-readonly-docs]
telemetry:
  destination: internal
  rawPrompts: false
policy:
  bundleRef: approved-bank-policy
  planApproval: required
```

Resolve references (`*Ref`) through an administrator-managed registry. API tokens are never stored in plain YAML configuration files. In air-gapped profiles, pre-bundle dependencies, JDKs, and build plugins. Validate execution from clean container images with network egress severed at the infrastructure level.

Initial deployment: Local CLI on developer workstations with isolated worker processes and local/internal model servers. Multi-user deployment: Python API control plane + PostgreSQL state storage + internal artifact cache + ephemeral per-task workers. Kubernetes orchestration remains optional based on organizational standards.

---

## 14. Target Codebase Structure

```text
ai-code-engineer/
  pyproject.toml                  # Package metadata and dependencies
  src/ai_code_engineer/
    domain/                       # Task, Plan, ToolCall, Events
    agent/                        # Orchestrator, Planner, Verifier
    models/
      provider.py                 # Protocol and capability contracts
      ollama.py
      openrouter.py
    context/                      # Search, symbols, repo map, budgets
    tools/                        # Registry, filesystem, Git, build/test
    policy/                       # Decisions, approvals, data egress
    skills/                       # Discovery, routing, trusted versions
    mcp/                          # Client and approved registry
    memory/                       # Events, checkpoints, recovery
    worker/                       # Isolated execution and cancellation
    cli/                          # Console interface
    server/                       # WebApp control plane and API
  skills/                         # SKILL.md packages
  policies/                       # Permission, command, and path rules
  profiles/                       # Offline, on-prem, and online presets
  tests/
    unit/
    contract/                     # Provider and tool contracts
    integration/
    security/
  evals/                          # Representative benchmarks and rubrics
  docs/                           # Architecture decision records, specifications
```

Pin supported Python and package versions via lockfiles; maintain wheels for offline deployment. JDK, Maven, and Gradle installations belong to the target project testing environment rather than the agent's build system.

Directory boundaries establish logical decoupling. Dependency flow: applications/adapters → core orchestration → domain models. Core logic relies on abstract interfaces rather than concrete provider SDKs. Policy enforcement intercepts all tool invocations regardless of underlying automation libraries.

API Endpoints: Task creation, status/event streaming, plan/diff inspection, scoped approval submission, cancellation, and artifact retrieval. Authorization is enforced across individual tasks and artifacts.

---

## 15. Practical Workflow: Bank Transfer Validation

**Task Prompt:** *"Reject zero and negative transfer amounts in Transfer API while preserving existing error contract."*

1. **Intake & Discovery:** The agent inspects the workspace and active changes, analyzing `TransferController`, `TransferRequest`, services, and existing tests.
2. **Skill Ingestion:** Ingests Java, Spring, Testing, and API Compatibility skills.
3. **Pattern Recognition:** Identifies existing Bean Validation annotations and global `@ControllerAdvice` exception handlers, avoiding custom ad-hoc error structures.
4. **Planning:** Emits a plan: add validation constraints to DTO, add unit/integration tests covering zero, negative, valid, and null amounts, and verify that services are not invoked on invalid requests.
5. **Approval:** Upon user approval, applies cryptographic hash-locked patches within an isolated worktree/sandbox.
6. **Verification & Repair:** Executes compilation and focused test suites, analyzes failure reports, and performs bounded repairs within budget.
7. **Quality Audit:** Executes contract tests, inspects diffs, and verifies secret redaction.
8. **Completion:** Presents unified diffs and empirical test proofs. Git branching or PR generation occurs only under authorized developer commands.

**Cryptographic Hardening Note:** Requests involving sensitive cryptographic operations (e.g. payload encryption) require formal threat modeling, key management lifecycles, and TLS layer evaluations. Agents utilize approved cryptographic libraries rather than implementing custom algorithms.

---

## 16. Git, Rollback, and Preserving Developer Work

- Record baseline commits and dirty file states prior to execution. Never clobber uncommitted developer modifications.
- Isolate task execution within dedicated git worktrees; uncommitted changes are not migrated automatically without explicit instruction.
- Record file patches and SHA-256 digests before and after modification. Rollback operations revert task-specific edits after confirming no conflicting subsequent edits exist.
- Prohibit indiscriminate `git reset --hard` or `git clean -fd` as recovery mechanisms. If conflicts arise, halt and present affected files.
- Local rollback cannot reverse external side effects (remote pushes, ticket updates); compensatory actions require distinct authorized workflows.

---

## 17. Roadmap with Acceptance Deliverables

Core security policies and sandboxed execution are prioritized at the foundation rather than deferred.

| Phase | Core Deliverables | Exit Gate / Acceptance Criteria |
| :--- | :--- | :--- |
| **0 — Foundation** | Threat model, scope boundaries, data classification, worker isolation, read-only CLI, baseline evals | Proven blocking of workspace escapes and unauthorized egress in automated tests |
| **1 — Providers & Tools** | Model SPI, Ollama adapter, OpenRouter adapter, typed tool registry | Identical tool-calling scenarios pass across providers; malformed calls rejected; 2 cloud models benchmarked |
| **2 — Agent Loop** | State machine, resource budgets, event streaming, cancellation, basic tool suite | Small end-to-end task completion and safe recovery after process interruption |
| **3 — Context Engine** | Repository map, lexical search, Java/Spring metadata, token budgeting | Correct retrieval of target files and edit sites on benchmark tasks under measured budgets |
| **4 — Plan & Edit** | Plan schemas, user approval gates, scoped patching, worktrees, rollback | Zero out-of-scope edits; developer uncommitted work preserved |
| **5 — Verify & Repair** | Build/test/lint/security recipes, JUnit parsing, bounded repair loop | Bug-fix benchmarks verified; timeouts/missing tests do not emit false passes |
| **6 — Skills & Policy** | Skill routing, versioned packages, execution hooks, adversarial injection tests | Prompt injection cannot expand permissions; approvals cannot be replayed across targets |
| **7 — MCP Integration** | Server registry, authentication, tool normalization, audit logging | Unapproved servers and schema drift blocked; integration failures handled gracefully |
| **8 — Deployment Profiles** | Offline packaging, internal mirrors, egress proxy configuration | Offline execution validated on clean environment without external network calls; cloud leak prevention verified |
| **9 — Team Pilot** | Web/IDE interfaces, SSO/RBAC, operational metrics, incident runbooks | Pilot acceptance criteria satisfied; human review required for all applied changes |
| **10 — Scale & Evolution** | Performance tuning, caching, task queuing, optional specialized subagents | Measurable improvements in quality or latency without compromising sandbox isolation or multiplying costs |

Development commences with a single vertical slice: reading a Spring Boot project → proposing a plan → patching validation logic → running tests → inspecting diffs within a sandbox. Multi-agent swarms and complex vector databases are deferred until core reliability is established.

---

## 18. Evaluation, Observability, and Operations

Establish an initial benchmark corpus of 20–30 sanitized enterprise tasks: input validation, regression fixes, targeted refactoring, test additions, Kotlin interop, and build failure remediation. Incorporate adversarial test cases evaluating prompt injection, path traversal, and data exfiltration.

Benchmark configurations against identical baselines and acceptance rubrics, executing multiple runs to measure variance. Do not rely solely on automated LLM-as-a-judge evaluations; combine deterministic test suite outcomes with human code review.

Key Operational Metrics: Task acceptance rate, test pass rate, regression frequency, denied tool invocations, approval frequency, repair round distribution, context token consumption, p50/p95 task duration, cost per task, and post-delivery human modification volume.

Audit Logging: Events record actor, task ID, tool identifier, policy version, decision, timestamp, duration, and artifact references. Raw prompts, source code, and secrets are redacted prior to storage. Enforce access controls and retention policies with project isolation.

Operational Runbooks: Prepare runbooks covering inference outages, disk exhaustion, stuck workers, artifact mirror disconnections, failed resumes, and exfiltration incidents. Implement global kill switches to sever execution and network egress instantly.

---

## 19. Security Considerations

| Threat Vector | Required Mitigation | Verification Proof |
| :--- | :--- | :--- |
| **Prompt Injection via repo/docs/tools** | Decoupled trust zones, deterministic policy enforcement, no raw text execution | Injection fixtures attempting exfiltration are blocked |
| **Source / Secret Exfiltration to Cloud** | Data classification, context sanitization, egress gateway, no silent fallback | Automated tests verifying blocking of unauthorized uploads |
| **Filesystem Traversal outside Workspace** | Canonical path resolution, symlink/junction rejection, OS-level constraints | Traversal and junction escape test suites pass |
| **Malicious Build or Test Scripts** | Least-privilege workers, resource quotas, severed network access | Attempts to access host secrets or spawn unauthorized processes fail |
| **Compromised MCP Server or Skill** | Approved server registry, cryptographic hash pinning, minimal scopes | Schema drift and unapproved server connections blocked |
| **Tainted Dependencies and Models** | Approved internal repositories, integrity checks, SBOMs, vulnerability scans | Reproducible clean builds from verified package mirrors |
| **Replay of Prior User Approvals** | Approval cryptographically bound to plan hash, action, target, and expiration | Altering target arguments invalidates approval |
| **Corrupted or Conflicting Edits** | Pre-write expected file hashes, per-workspace file locks, checkpoint commits | Concurrent external edits reject overwrite |
| **Cross-Tenant / Cross-Project Leakage** | Explicit authorization per artifact and isolated cache namespaces | Cross-tenant access test suites pass |
| **False Completion Claims** | Deterministic verifier requiring fresh test reports bound to code hash | Skipped tests or exit code 0 without tests reject completion |

Offline operation reduces external exfiltration risks but does not eliminate malicious code execution. Secret scanning provides defense in depth; secrets should never be loaded into context. Test data must be synthetic; production data requires out-of-band enterprise authorization.

---

## 20. Production Definition of Done

### Runtime Readiness Checklist

- [ ] Core contracts decoupled from providers; contract test suites pass across all deployment configurations.
- [ ] Policy engine defaults to deny, governing built-in tools, MCP adapters, hooks, and model egress.
- [ ] Sandboxed isolation validated via container escape and network egress tests; workers carry zero production credentials.
- [ ] Approvals are scoped, auditable, and non-reusable across targets.
- [ ] Cancellation, timeouts, and restart recovery prevent unsafe state replay.
- [ ] Developer uncommitted changes are protected; rollback is deterministic and testable.
- [ ] Task completion requires all mandatory verification checks to pass.
- [ ] Offline profile operates from clean packages with pre-bundled dependencies and verified provenance.
- [ ] Online profile routes exclusively through approved enterprise gateways with data controls.
- [ ] Project, user, artifact, and memory isolation are validated.
- [ ] Audit logs are redacted, retention policies enforced, and records accessible to authorized reviewers.
- [ ] Benchmark evaluations meet target accuracy thresholds with human review.
- [ ] Vulnerability scans, license audits, and SBOM generations integrated into release pipelines.
- [ ] SLOs, support runbooks, emergency kill switches, and incident response procedures approved.

### Task Completion Checklist

- [ ] Task objective and acceptance criteria clearly defined; plan operates within authorized scope.
- [ ] Code modifications are targeted, reviewed, and free of secrets or scope creep.
- [ ] Mandatory build, lint, and test checks pass on final patched code.
- [ ] Unexecuted checks and limitations are explicitly reported; incomplete mandatory checks block completion.
- [ ] Unified diffs, empirical test evidence, impact summaries, and rollback instructions provided to developer.
- [ ] External actions (push, PR creation) occur only through authorized explicit commands.

---

## 21. Implementation References

The scope originates from initial architecture alignment discussions. The following technical specifications support implementation details:

1. [Spring AI Reference](https://docs.spring.io/spring-ai/reference/) — Abstractions and JVM ecosystem integrations.
2. [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling) — Tool invocation lifecycles and structured outputs.
3. [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling) — Native tool invocation on local open models.
4. [Aider Repository Map](https://aider.chat/docs/repomap.html) — Context selection via structural symbol graphs.
5. [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices) — Integration security and delegated authorization.

---

## 22. Approved Update: Integrated Cloud Tiers

This section incorporates the initial provider baseline: **Python + Local Ollama + OpenRouter**. We interface multiple cloud models through a unified OpenRouter adapter, deferring direct custom SDK integrations for Gemini, Groq, and others until necessary.

### Model Tiers

The tool exposes three operational tiers, maintaining agent core independence:

| Tier | Primary Use Case |
| :--- | :--- |
| `local` | Approved local models via Ollama; default for enterprise codebases |
| `cloud-free` | Specific, benchmarked free cloud models via OpenRouter for non-sensitive code |
| `cloud-auto` | `openrouter/free` router for exploratory evaluation on public or synthetic data |

OpenRouter documentation indicates that `openrouter/free` dynamically routes across available free endpoints matching request parameters (such as tool calling). Consequently, reproducible benchmark evaluations require pinning specific model identifiers, logging the serving model for each generation turn.

Configuration Example:
```yaml
models:
  local:
    provider: ollama
    modelRef: approved-local-coder
  cloud-free:
    provider: openrouter
    modelRef: evaluated-free-coder-primary
    credentialRef: openrouter-api-key
  cloud-auto:
    provider: openrouter
    model: openrouter/free
    credentialRef: openrouter-api-key
    allowedDataClasses: [public, synthetic]
routing:
  default: local
  paidFallback: false
  localToCloudFallback: false
  cloudFreeFallbackChain:
    - evaluated-free-coder-secondary
    - local
```

CLI Interface Example:
```text
agent --model local
agent --model cloud-free
agent --model cloud-auto
```

The selection interface displays model name, provider, tool capabilities, and data policy; selecting a model does not alter task permissions.

### Fallback Rules and Quota Management

Operational Fallback Chain: Primary Free Model → Approved Secondary Free Model → Local Ollama. The runtime triggers this fallback strictly upon availability errors or rate limits, applying bounded retries and respecting provider `Retry-After` headers. Shared upstream quotas may affect multiple free endpoints simultaneously; switching models does not guarantee immediate availability.

Prior to fallback transitions, verify policy compliance, tool capabilities, and context limits, rebuilding request payloads from persisted state for the target provider. Never re-execute side-effecting tools that already succeeded. Authentication failures or policy denials halt execution with clear diagnostic messages; automatic transitions to paid endpoints are prohibited. Offline profiles never fall back to cloud endpoints.

Free quotas govern model calls rather than complete task outcomes; a single coding task may require multiple turns. Monitor usage metrics and decouple behavior from hardcoded daily quotas.

### Model Evaluation for POC

Model candidates discussed in early planning (e.g. specialized coding checkpoints) represent illustrative exploratory candidates. Specific model identifiers and context capacities are validated directly against current provider catalogs during implementation.

Model Selection Criteria:
1. Verified free API availability and validated tool-calling accuracy in contract tests.
2. Code generation quality on Java/Spring tasks: Bean Validation, bug fixing, test authorship, and multi-file editing.
3. Patch success rates, test pass rates, generation latency, effective context window, and endpoint stability.
4. Provider data handling terms, retention policies, and training opt-outs.
5. Successful completion of the same policy and contract tests applied to local models.

Benchmark results are recorded with measurement timestamp, model ID, and active provider route. Large nominal context windows alone do not guarantee agent suitability.

### Data Isolation between Free Tiers and Enterprise Code

Cloud free tiers are restricted to experimental repositories and public or synthetic datasets. Data processing terms vary across providers, and OpenRouter privacy configurations must be evaluated alongside inference provider terms.

Labeling an endpoint as free or altering logging levels does not authorize transmitting proprietary banking source code. Enterprise code remains restricted to local or on-premises infrastructure unless explicit institutional authorization is granted. If provider routing cannot be restricted to compliant endpoints, `cloud-auto` is disabled for that data classification.

**Approved Baseline:** Python Agent Runtime + Local Ollama + OpenRouter + Focused Tooling + Day-One Sandboxing and Policy Engine + Automated Verification Loop. Cloud models are integrated following empirical evaluation; direct proprietary provider SDKs and autonomous subagent swarms remain future phases.
