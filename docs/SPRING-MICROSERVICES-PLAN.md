# Ecommerce dogfood run — target architecture and build plan

Created 2026-09-27. Two things are being specified here at once, and they must not be confused:

- **The subject under test** is `ai-code-engineer` (the agent in this repo).
- **The test vehicle** is `D:\AI\AI-Agent\ecommerce`, a multi-module Spring Boot microservices app.

The Java app is a means. The deliverable is the list of agent defects this task exposes, the fixes that
land in `src/ai_code_engineer`, and the prompt/plan evidence that shows what the tool actually did.

## The hard rules of this exercise

These come straight from the user and are not negotiable mid-run:

1. **Nothing in `ecommerce/` is authored by the assistant.** No `Write`, no `Edit`, no file, not even a
   `pom.xml`. The assistant creates the empty directory and nothing else inside it.
2. **No experiments and no verification from the assistant inside `ecommerce/`.** No `mvn`, no starting
   services. Building and testing is the agent's job, through its own recipes and build/repair loop.
3. The assistant acts **only as a user** of the tool. It may read and judge the tool's output; it may not
   repair it.
4. **The full execution prompts and the plan the model produced are kept verbatim** in
   `docs/DOGFOOD-ECOMMERCE-RUN.md`. Paraphrase is not evidence.
5. Every defect found goes into `src/ai_code_engineer` **plus a test**, with the 624-test offline baseline
   staying green. The generated project is never touched to make a test pass.
6. **Replay requirement:** the user reopens the web window afterwards and reads the whole chat and history
   for the `ecommerce` project, then opens the project and runs it himself. Therefore: drive the web
   window (`Run-Agent.bat` → `desktop.pyw` → `webapp.launch`), never the CLI or Tk; stay in **one chat**
   for the run; never clear `.agent-chats/` or `.agent-runs/`; and leave the app runnable with a written
   start order.

## Target architecture

Seven Maven modules, six runnable processes:

```
ecommerce/                          aggregator pom (packaging=pom)
├── ecommerce-common-lib/           jar — not started
├── eureka-server/            :8761
├── config-server/            :8881   native/filesystem config-repo, not git
├── api-gateway/              :8080   WebFlux
├── auth-service/             :8081   register · login · general configuration · /me
├── customer-service/         :8082
└── product-service/          :8083
```

- **Java 17**, Spring Boot on the **3.5.x** line with the matching Spring Cloud BOM. Deliberately not 4.x:
  4.x moves to Jakarta EE 11 / Spring Framework 7, and a 3 B model has no training corpus for it — the
  exercise measures the agent, not the model's tolerance for a moving stack.
- **Unauthenticated:** `/api/auth/register`, `/api/auth/login`, `/api/config/**` (the general
  configuration API — **lives in auth-service** per the user), `/actuator/health`, and H2 console in dev.
- **Everything else** requires a JWT.
- **Registry:** the three business services and auth-service register with Eureka; the gateway routes by
  service id through it.
- **Config server** backs all services from `ecommerce/config-repo/` on the `native` profile.

### Cross-cutting: server time on every request and response

Requested explicitly. Two mechanisms, because they cover different things:

- **`ServerTimestampFilter`** (`OncePerRequestFilter`, in `ecommerce-common-lib`): stamps every request
  with the server clock as a request attribute and an MDC key, and sets `X-Server-Timestamp`
  (ISO-8601, `Asia/Jerusalem`-free — the server default zone, formatted explicitly) on **every** response,
  including error responses and 401s. This is the part that reliably covers all outbound traffic.
- **`ServerAuditAspect`** (`@Around` over `@RestController` methods, `spring-boot-starter-aop`): records
  entry and exit server times and the handler duration, so the log line says which endpoint ran at what
  time on which server.
- **`ApiResponse<T>` envelope** in common-lib carries a `serverTime` field, so the timestamp is in the JSON
  body as well as the header for every endpoint that uses the envelope.

**The trap to watch:** `api-gateway` is reactive (WebFlux). The servlet `OncePerRequestFilter` and the
AspectJ `@Around` on controller methods do **not** fire there. The gateway needs a
`WebFilter`/`GlobalFilter` doing the same job. Whether common-lib ships both, or the gateway gets its own
filter, is a real design point — and a likely first defect for a small model, which will copy the servlet
filter into the gateway module and be surprised when it never runs.

### Data

- **H2 file-per-service**: `./data/<service>.mv.db`, so restarting a service keeps its rows and the user
  can actually try the app. No shared schema between services — that is the point of separate databases.
- **Liquibase per service**: `db.changelog-master.xml` including `01-schema.xml` (DDL) and
  `02-seed.xml` (DML), run at startup.
- **Default admin**: `admin` / `admin` inserted by Liquibase DML with a **fixed, precomputed BCrypt hash**
  written into the changelog. Generating the hash at runtime changes the checksum on every run and breaks
  Liquibase validation.
- Passwords only ever via `BCryptPasswordEncoder`; never logged, never in a response DTO.

### Security

- **`jjwt` HS256**, issuance and verification both in `ecommerce-common-lib`, so one filter and one token
  format serve all services rather than four copies drifting apart.
- Claims: `sub`, `uid`, `roles`, `iat`, `exp`. Access token TTL 60 min. **No refresh tokens in v1.**
- Roles `ROLE_ADMIN` / `ROLE_USER`. The secret comes from config-server properties with a dev default,
  never hardcoded in Java.

## Milestones

One prompt per milestone, and each prompt names exact file paths and a finite file count. The sizing rule
from `docs/MODEL-BENCHMARK.md` applies: a 3 B model can edit a file it can see, and cannot hold a
seven-module architecture in its head.

| # | Milestone | Exit criterion (the agent's own build, not ours) |
| --- | --- | --- |
| M0 | Create `ecommerce/`, register it as a project, grant it, set Change mode | Folder appears in the sidebar bound to a chat |
| M1 | Aggregator pom + 7 module poms + 7 empty Spring Boot apps | `Maven compile` green at the reactor root |
| M2 | `eureka-server` + `config-server` + `config-repo` | Both start and register; config served to one client |
| M3 | `ecommerce-common-lib`: envelope, error handling, `jjwt` service + filter, timestamp filter | `Maven test` green for the module |
| M4 | `auth-service`: users/roles tables via Liquibase, `admin`/`admin` seed, register + login | Login returns a token; wrong password 401 |
| M5 | `api-gateway`: routes + Eureka discovery + JWT enforcement, public paths open | Public 200, protected without token 401 |
| M6 | `customer-service`: CRUD, authenticated, H2 + Liquibase, timestamp filter + aspect | `mvn -B test` green |
| M7 | `product-service`: CRUD + the public general-configuration API | Config API reachable unauthenticated |
| M8 | Full reactor `mvn -B test` + a written run order and smoke list | User can start all six and try it |

**Model choice is decided by measurement, not by default.** `docs/MODEL-BENCHMARK.md:77-98` already
recorded `qwen2.5-coder:1.5b` failing exactly this kind of task — truncating a half-written DTO and
returning a JSON instance where Java source was asked for. The model currently selected in
`.agent-projects.json` is `qwen2.5-coder:3b`, which that benchmark never measured. So M1 and M4 (the two
most "author new Java" milestones) run under both `qwen2.5-coder:3b` and `qwen3:4b`, and whichever
produces a compiling reactor with fewer repair loops wins per milestone type.

## What already exists (checked against the code, not assumed)

| Thing | Status | Evidence |
| --- | --- | --- |
| `ecommerce/` directory | **absent** | not in the root listing |
| A Spring project in this repo | present but unusable as a base: **single** module, Boot **3.0.2**, 5 files, no security/H2/Liquibase | `spring-rpoject/pom.xml`, `spring-rpoject/src/main/java/com/ai/` |
| An earlier JWT experiment | present: single module, Boot, with `JwtTokenProvider`, `UserStore` and 3 surefire reports — prior art for M4, not a base | `sandbox/spring-auth/` |
| Maven build recipes in the agent | present: `maven-test` (`mvn -B test`), `maven-compile` | `src/ai_code_engineer/runner.py:45-56` |
| Long timeout for Maven builds | present: 1500 s | `runner.py:31,128` |
| Maven **verification** command | present but **offline** — `mvn -o -B test` | `src/ai_code_engineer/verification.py:22,150` |
| Java/Kotlin symbol indexing | present | `symbols.py`, UI 2.9 item 6 |
| Chat + session history per project | present: `.agent-chats/<id>/chat.json`, `.agent-runs/<id>/session.json` | `webapp/controller.py:151-152` |
| Auto-Apply + queue per folder | present, already `true` for `spring-rpoject` and `project-2` | `.agent-projects.json` |
| `.mvn/` treated as a sensitive path | present | `workspace.py:43-49` |

## Known blockers, to be solved one at a time during the run

| # | Blocker | Source | Why it bites here |
| --- | --- | --- | --- |
| B1 | The verifier runs `mvn -o` (offline) | `verification.py:22` | A first build must download Spring Cloud, Liquibase and jjwt. An offline-only verifier cannot bootstrap any new Maven project — expected to be the first real defect. |
| B2 | 1500 s ceiling on Maven recipes | `runner.py:31` | A 7-module reactor's cold first build plus downloads is the worst case for that ceiling. |
| B3 | No measured model for authoring new Java | `MODEL-BENCHMARK.md:77-98` | Expect truncated files, JSON-instead-of-source, and `rejected_action` noise in early milestones. |
| B4 | Whole-file proposals on new files | the UI 2.9 rationale | Prompts must stay one-class-per-task or the small model invents unrelated files. |
| B5 | `ecommerce` has no git, so the agent's checkpoints are the only undo | repo is not a git working tree | Whether to `git init` there is the user's call, since it changes what "never touch the project" means. |
| B6 | WebFlux vs servlet cross-cutting code | architecture above | The filter/aspect will be copy-pasted into the gateway where it cannot fire. |

## Decisions, settled 2026-09-28

1. **The old items were cleared first**, as the precondition for this run: git task branches, the
   git-shaped rollback, the Host seam, and Arabic in the Tk window. All four are built and written up in
   `docs/IMPLEMENTATION-STATUS.md` under *UI 3.5*, including the two places where this plan's own
   recommendation was not followed: the Host seam's method migration was left undone, and the Tk
   window's RTL is alignment only.
2. **Auto-Apply for `ecommerce`: on.** Milestones run unattended; the agent's build-and-repair loop is
   the reviewer, and the new git task branch is what makes a milestone rewindable.
3. **`git init` in `ecommerce`: yes**, at M0, by the assistant as infrastructure. It is the only write
   this session will ever make inside that folder, and it is what closes B5.
4. **Spring Boot 3.5.x, not 4.x**, for the reason in the architecture section: the exercise measures the
   agent, and a 3 B model has no corpus for a Jakarta EE 11 stack.
5. **The general configuration API lives in `auth-service`.** JWT via `jjwt` HS256 from `common-lib`.
6. **The agent builds and tests.** The assistant issues prompts and reads results; it never authors,
   edits, builds or runs anything inside `ecommerce`.

## Open decisions

Nothing blocking. Two get answered by measurement during the run rather than in advance: which model
drives the authoring milestones (`qwen2.5-coder:3b` against `qwen3:4b` on M1 and M4), and whether B1
needs a tool fix or only a one-time online bootstrap of `~/.m2`.
