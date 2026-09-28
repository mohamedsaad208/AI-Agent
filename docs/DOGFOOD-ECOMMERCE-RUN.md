# Dogfood run log — ecommerce

The evidence file for `docs/SPRING-MICROSERVICES-PLAN.md`. Everything below is copied **verbatim** from the
agent's own chat, never rewritten by the assistant — a paraphrased prompt proves nothing about the tool.

Source of truth for the transcripts: `.agent-runs/<sessionId>/session.json` — one entry per Change task,
with its events, its build records and its **whole task text** — grouped by `chat_id`; and
`.agent-chats/<chatId>/chat.json` for the prose threads. The web window draws a conversation from the
first of those (`chat_sessions(runs, root, chat_id)`), which is why the run's history survived every
restart of the server: 89 sessions, 10 conversations under the `ecommerce` row of the sidebar, newest
first. `docs/DOGFOOD-PROMPTS.md` is that same record rendered as one continuous document, prompt by
prompt — regenerate it with `python tools/dogfood_ledger.py`, never by hand. Those two directories are
never cleared during this exercise, because the user reopens the web window afterwards to read the history
himself.

## Contract being enforced

- Assistant = user of the tool only. **Zero** writes and **zero** build/test commands inside `ecommerce/`.
- Fixes land in `src/ai_code_engineer` + tests; the offline suite is re-run green after each one
  (624 at the start of the session → **710** after D9, `python -m unittest discover -s tests`, ~180 s).
- One chat for the whole run, in the web window.
- The generated project is never edited to make a milestone pass. A failed milestone is a finding.

## Run index

| # | Milestone | Date | Model | Prompt id | Build recipe run | Result | Defects found |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M0 | Folder created, git initialised, project not yet granted | 2026-09-28 | — | — | — | infrastructure only | 4 preconditions cleared first (UI 3.5) |
| M1 | Aggregator + 7 module poms | 2026-09-28 | `qwen2.5-coder:3b` | P1, P2a–P2e, P3a–P3d, F1–F4 | `mvn -B test` run by the agent | **BUILD SUCCESS, all 8 reactor modules, exit 0 in 61.5 s** | D2, D3, D4, D5, D6, D7, D8, D9 |
| M2 | eureka-server + config-server applications, their yml, config-repo | 2026-09-28 | `qwen2.5-coder:3b` | P7–P9, R1–R3, C1 | `mvn -B test` after each write (chained) | both services compile; **reactor exit 0 in 16.2 s**; `config-repo/ecommerce-shared.yml` written, committed (`fae4ef0`) and tree clean — the file the vanished prompt (D14) lost, then the APPLYING record (D21) held back | D10, D11 (twice), D12, D13, D14, D18, D19, D20, D21 |
| M3 | common-lib code: envelope, filter, aspect, JWT service + servlet filter | 2026-09-28 | `qwen2.5-coder:3b` | E1–E6 | `mvn -B test` after each write (chained) | `ServerTimestampFilter` ✓, `ServerAuditAspect` ✓ (`80c9472`), `JwtService` ✓ (`9402208`, after D22), `JwtAuthFilter` ✓ one turn (`20d86ce`); `ApiResponse` compiled but was written package-private, and the fix that made it public lost its header (D24) | D20, D22, D23, D24 |

**The one write this session will ever make inside `ecommerce/`:** `git init` plus a single
`--allow-empty` commit (`a1e5a01`, "empty base - no project files authored by the assistant"). It
carries no files. It exists so the agent's checkpoint commits have a parent to name, which is what
makes the new git-shaped rollback able to say "before this task" at milestone 1 rather than only from
milestone 2 onwards. Verified through the tool's own reader: `repo=True, branch=master, head=a1e5a01,
dirty=0`.

Granting the folder through the UI landed it on **Change mode** with the git chip showing `master`, and
Auto-Apply was switched on for that folder — both as designed.

## M1-P1 — result

**The agent wrote a correct aggregator pom on its first attempt** under `qwen2.5-coder:3b`: one file,
`pom.xml`, Auto-Applied, then committed by the tool as git checkpoint `7f44de1`. Reviewed by reading the
file, not by editing it:

- every requirement landed — parent `3.5.16`, `packaging pom`, `java.version 17`, the seven modules in
  the order asked, the Spring Cloud `2025.0.3` import with `type pom` + `scope import`, and nothing extra;
- it added `<relativePath/>` on its own, which is the right thing for a Boot parent and was not asked for;
- **this narrows `docs/MODEL-BENCHMARK.md`.** That file's "small models cannot author new Java/XML"
  verdict was measured on `qwen2.5-coder:1.5b`. A 3 B model carried a 46-line structured XML file with no
  truncation and no repair loop. The benchmark needs re-running on `3b` before its conclusion is repeated.

**D2 — a milestone with an unbuildable exit criterion.** The plan set M1's proof as `mvn compile` green at
the reactor root. An aggregator naming seven modules that do not exist yet cannot compile: Maven fails on
the missing child poms, which is not a defect in anything. The milestone was re-split into P1 (parent) and
P2+ (module poms) before any build was attempted, so the first build runs against a reactor that is
structurally complete.

## D8 — the model wrote an invalid `<build>` section, and only a one-file prompt could fix it

Four of the seven module poms came out with `<build><plugin>…</plugin></build>` instead of
`<build><plugins><plugin>…</plugin></plugins></build>`, and Maven said so plainly:

```
[ERROR] Malformed POM …api-gateway\pom.xml: Unrecognised tag: 'plugin'
[ERROR] The build could not read 4 projects
```

The interesting part is what the agent did with that. Reviewed file by file: `eureka-server` and
`config-server` — the two written by the **two-file** prompt — were correct, and every **single-file**
prompt with a build section made the same mistake. The model is not drifting randomly; it is dropping
one nesting level it has seen less often.

Then the repair, which is the finding worth keeping:

| repair attempt | prompt | outcome |
| --- | --- | --- |
| four files at once | "Fix only that error in each of them" | **the engine's loop guard stopped it**: "The model kept giving the same answer without making progress." Nothing was changed, working tree clean. |
| one file | "Fix one file: api-gateway/pom.xml … Write the complete new content" | **succeeded** — `<plugins>` present, committed as `670b8b5`, about eight minutes and several turns |

Two conclusions, both of them about the *tool* rather than the model:

1. **The loop guard did its job.** A model that repeats itself is stopped rather than allowed to spend
   twelve turns and a quarter of an hour on a task it is not making progress on, and the sentence it
   prints names both real causes. That is a design decision from an earlier round paying off on first
   real contact.
2. **A failure the guard produces is not a diagnosis.** "the requested change is already in the files,
   or the task is too vague" was wrong on both counts here — the change was needed and the task was
   precise. What was actually happening is that the task was **too wide for the model**, and the message
   sends the user to open the file instead of telling them to narrow the request. Worth its own fix.

## Prompts, verbatim

Filled per milestone as `M<n>-P<k>`:

### M1-P1 — the aggregator pom (sent 2026-09-28, `qwen2.5-coder:3b`, Change mode, Auto-Apply ON)

```
Create exactly one new file: pom.xml at the project root. Do not create any other file.

This is the Maven aggregator for a multi-module Spring Boot application.

Requirements:
- modelVersion 4.0.0
- parent org.springframework.boot : spring-boot-starter-parent : 3.5.16
- groupId com.ecommerce, artifactId ecommerce, version 1.0.0, packaging pom
- property java.version = 17
- a modules list containing exactly these seven, in this order: ecommerce-common-lib, eureka-server,
  config-server, api-gateway, auth-service, customer-service, product-service
- dependencyManagement importing org.springframework.cloud : spring-cloud-dependencies : 2025.0.3 with
  type pom and scope import
- nothing else: no dependencies section, no build plugins, no other properties

Write the complete file content, not a fragment.
```

The two versions were checked against Maven Central before the prompt was written, not recalled:
`spring-boot-starter-parent` is at **3.5.16** on the 3.5 line (the newest overall is 4.1.1, which this
exercise deliberately does not use), and the Spring Cloud train that pairs with Boot 3.5 is **2025.0.3**
(2025.1.x belongs to Boot 4). A prompt that invents a version measures nothing about the agent — it
measures the prompt.

### M1-P2a — `ecommerce-common-lib/pom.xml` (one file, `qwen2.5-coder:3b`, after D4 and the 900 s timeout)

```
Create exactly one new file: ecommerce-common-lib/pom.xml. Do not create or change any other file.

It is a Maven module of the aggregator pom.xml in this project root.
- modelVersion 4.0.0
- parent: groupId com.ecommerce, artifactId ecommerce, version 1.0.0, and <relativePath>../pom.xml</relativePath>
- no version element for the module itself
- artifactId ecommerce-common-lib, packaging jar
- dependencies, in this order:
  1. org.springframework.boot:spring-boot-starter-web, scope provided
  2. org.springframework.boot:spring-boot-starter-validation, scope provided
  3. io.jsonwebtoken:jjwt-api, version 0.12.7
  4. io.jsonwebtoken:jjwt-impl, version 0.12.7, scope runtime
  5. io.jsonwebtoken:jjwt-jackson, version 0.12.7, scope runtime
  6. org.projectlombok:lombok, scope provided
  7. org.springframework.boot:spring-boot-starter-test, scope test
- no build section, no properties section

Write the complete file content.
```

**Result: one turn, correct file.** All seven dependencies with the right scopes, the right parent block,
no build section. It reordered `spring-boot-starter-test` to third against an explicit "in this order"
list, and it wrote a comment above every dependency repeating the instruction it was given — noise, not
error. Reviewed by reading the file; nothing was edited.

### M1-P2b — `eureka-server` + `config-server` (two files in one prompt)

```
Create exactly two new files. Do not change any existing file.

Both are Maven modules of the aggregator pom.xml in this project root. Each one uses modelVersion 4.0.0
and a parent with groupId com.ecommerce, artifactId ecommerce, version 1.0.0, and
<relativePath>../pom.xml</relativePath>. Neither module repeats a version element of its own.

File 1: eureka-server/pom.xml
- artifactId eureka-server
- dependencies: org.springframework.cloud:spring-cloud-starter-netflix-eureka-server with no version
  element (the Spring Cloud BOM in the parent supplies it);
  org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

File 2: config-server/pom.xml
- artifactId config-server
- dependencies: org.springframework.cloud:spring-cloud-config-server with no version;
  org.springframework.cloud:spring-cloud-starter-netflix-eureka-client with no version;
  org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete content of both files.
```

**Result: both files correct, but at three turns and ~15 minutes against one turn and ~6 minutes for the
single-file prompt above.** That is the measurement behind the run's first real best practice: *one file
per prompt is not a style preference on this hardware, it is the fast path.* A second file gives a 3 B
model another thing to lose track of, and the engine pays a full model turn for each attempt.

### M1-P3 — `api-gateway/pom.xml` (one file)

```
Create exactly one new file: api-gateway/pom.xml. Do not change any existing file.

It is a Maven module of the aggregator pom.xml in this project root.
- modelVersion 4.0.0
- parent: groupId com.ecommerce, artifactId ecommerce, version 1.0.0, <relativePath>../pom.xml</relativePath>
- no version element of its own
- artifactId api-gateway
- dependencies:
  1. org.springframework.cloud:spring-cloud-starter-gateway with no version
  2. org.springframework.cloud:spring-cloud-starter-netflix-eureka-client with no version
  3. org.springframework.cloud:spring-cloud-starter-config with no version
  4. org.springframework.boot:spring-boot-starter-actuator with no version
  5. org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete file content.
```

### M1-P4 — `auth-service/pom.xml` (one file)

Session `94cae412255f499faa946fbbd7841cce`, final state `VERIFICATION_FAILED`.

```
Create exactly one new file: auth-service/pom.xml. Do not change any existing file.

It is a Maven module of the aggregator pom.xml in this project root.
- modelVersion 4.0.0
- parent: groupId com.ecommerce, artifactId ecommerce, version 1.0.0, <relativePath>../pom.xml</relativePath>
- no version element of its own
- artifactId auth-service
- dependencies:
  1. com.ecommerce:ecommerce-common-lib:1.0.0
  2. org.springframework.boot:spring-boot-starter-web
  3. org.springframework.boot:spring-boot-starter-security
  4. org.springframework.boot:spring-boot-starter-data-jpa
  5. org.springframework.boot:spring-boot-starter-validation
  6. org.springframework.boot:spring-boot-starter-actuator
  7. org.liquibase:liquibase-core
  8. com.h2database:h2 with scope runtime
  9. org.springframework.cloud:spring-cloud-starter-netflix-eureka-client with no version
  10. org.springframework.cloud:spring-cloud-starter-config with no version
  11. org.projectlombok:lombok with scope provided
  12. org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete file content.
```

### M1-P5 — `customer-service/pom.xml` (one file)

Session `735c0167d9434daaa730baee6e0390ec`, final state `VERIFICATION_FAILED`.

```
Create exactly one new file: customer-service/pom.xml. Do not change any existing file.

It is a Maven module of the aggregator pom.xml in this project root.
- modelVersion 4.0.0
- parent: groupId com.ecommerce, artifactId ecommerce, version 1.0.0, <relativePath>../pom.xml</relativePath>
- no version element of its own
- artifactId customer-service
- dependencies:
  1. com.ecommerce:ecommerce-common-lib:1.0.0
  2. org.springframework.boot:spring-boot-starter-web
  3. org.springframework.boot:spring-boot-starter-data-jpa
  4. org.springframework.boot:spring-boot-starter-validation
  5. org.springframework.boot:spring-boot-starter-actuator
  6. org.liquibase:liquibase-core
  7. com.h2database:h2 with scope runtime
  8. org.springframework.cloud:spring-cloud-starter-netflix-eureka-client with no version
  9. org.springframework.cloud:spring-cloud-starter-config with no version
  10. org.projectlombok:lombok with scope provided
  11. org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete file content.
```

### M1-P6 — `product-service/pom.xml` (one file)

Session `905a6cf87e4f40b1a77424c4ef835d2b`, final state `VERIFICATION_FAILED`.

```
Create exactly one new file: product-service/pom.xml. Do not change any existing file.

It is a Maven module of the aggregator pom.xml in this project root. It has the same shape as customer-service/pom.xml.
- modelVersion 4.0.0
- parent: groupId com.ecommerce, artifactId ecommerce, version 1.0.0, <relativePath>../pom.xml</relativePath>
- no version element of its own
- artifactId product-service
- dependencies:
  1. com.ecommerce:ecommerce-common-lib:1.0.0
  2. org.springframework.boot:spring-boot-starter-web
  3. org.springframework.boot:spring-boot-starter-data-jpa
  4. org.springframework.boot:spring-boot-starter-validation
  5. org.springframework.boot:spring-boot-starter-actuator
  6. org.liquibase:liquibase-core
  7. com.h2database:h2 with scope runtime
  8. org.springframework.cloud:spring-cloud-starter-netflix-eureka-client with no version
  9. org.springframework.cloud:spring-cloud-starter-config with no version
  10. org.projectlombok:lombok with scope provided
  11. org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete file content.
```

### M1-F1 — repair `api-gateway/pom.xml` (one file, after the D8 four-file repair changed nothing)

Session `c65be475c0d44a4d86acedfb7da110e5`, final state `VERIFICATION_FAILED`.

```
Fix one file: api-gateway/pom.xml. Change nothing else and create nothing else.

Its build section is invalid XML for Maven. It currently contains a plugin element directly inside build, and Maven rejects it with "Unrecognised tag: plugin".

Wrap that plugin element in a plugins element, keeping the plugin element and everything inside it exactly as it is. The result must be:

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>

Write the complete new content of api-gateway/pom.xml.
```

### M1-F2 — repair `auth-service/pom.xml` (one file)

Session `7ed374dd7f3147ff928a080012275625`, final state `VERIFICATION_FAILED`.

```
Fix one file: auth-service/pom.xml. Change nothing else and create nothing else.

Its build section is invalid for Maven: a plugin element sits directly inside build, and Maven rejects it with "Unrecognised tag: plugin". Wrap that plugin element in a plugins element, keeping the plugin and its contents exactly as they are. The build section must end up as:

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>

Write the complete new content of auth-service/pom.xml.
```

### M1-F3 — repair `customer-service/pom.xml` (one file)

Session `3acf08c4b3c747d5bbbbcd04c4efff5b`, final state `VERIFICATION_FAILED`.

```
Fix one file: customer-service/pom.xml. Change nothing else and create nothing else.

Its build section is invalid for Maven: a plugin element sits directly inside build, and Maven rejects it with "Unrecognised tag: plugin". Wrap that plugin element in a plugins element, keeping the plugin and its contents exactly as they are. The build section must end up as:

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>

Write the complete new content of customer-service/pom.xml.
```

### M1-F4 — repair `product-service/pom.xml` (one file)

Session `3dcadb452c2449b0a6779bd4e8126b20`, final state `VERIFICATION_BLOCKED`.

```
Fix one file: product-service/pom.xml. Change nothing else and create nothing else.

Its build section is invalid for Maven: a plugin element sits directly inside build, and Maven rejects it with "Unrecognised tag: plugin". Wrap that plugin element in a plugins element, keeping the plugin and its contents exactly as they are. The build section must end up as:

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>

Write the complete new content of product-service/pom.xml.
```

### From here on the prompts are captured from the session records, not retyped

`sandbox`-side script `capture_prompts.py` walks `.agent-runs/*/session.json`, skips any session already
written up above, and appends the rest with the task text and the files it touched. It runs after each
milestone, so the verbatim record cannot drift from what was actually sent — the hand-written sections
above were transcribed during the run and four of them were removed as duplicates when the script first
covered the same sessions.

### `75853759` — create → —

State `BLOCKED`. Session `75853759167f44cb9ba6e093146942b8`.

```
Create exactly three new files. Do not modify the existing pom.xml and do not create any other file.

All three are Maven modules of the existing aggregator pom.xml in this project root. Each one uses modelVersion 4.0.0 and a parent with groupId com.ecommerce, artifactId ecommerce, version 1.0.0, and <relativePath>../pom.xml</relativePath>. Do not add a version element for the module itself.

File 1: ecommerce-common-lib/pom.xml
- artifactId ecommerce-common-lib, packaging jar
- dependencies: org.springframework.boot:spring-boot-starter-web with scope provided; org.springframework.boot:spring-boot-starter-validation with scope provided; org.springframework.boot:spring-boot-starter-test with scope test; io.jsonwebtoken:jjwt-api:0.12.7; io.jsonwebtoken:jjwt-impl:0.12.7 with scope runtime; io.jsonwebtoken:jjwt-jackson:0.12.7 with scope runtime; org.projectlombok:lombok with scope provided
- no build section

File 2: eureka-server/pom.xml
- artifactId eureka-server
- dependencies: org.springframework.cloud:spring-cloud-starter-netflix-eureka-server (no version, it comes from the Spring Cloud BOM); org.springframework.boot:spring-boot-starter-test with scope test
- build section with one plugin: org.springframework.boot:spring-boot-maven-plugin

File 3: config-server/pom.xml
- artifactId config-server
- dependencies: org.springframework.cloud:spring-cloud-config-server (no version); org.springframework.cloud:spring-cloud-starter-netflix-eureka-client (no version); org.springframework.boot:spring-boot-starter-test with scope test
- build section with one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete content of all three files.
```
### `aeff2280` — create → —

State `BLOCKED`. Session `aeff2280be334873902f73b0765e9cc7`.

```
Create exactly three new files. Do not modify the existing pom.xml and do not create any other file.

All three are Maven modules of the existing aggregator pom.xml in this project root. Each one uses modelVersion 4.0.0 and a parent with groupId com.ecommerce, artifactId ecommerce, version 1.0.0, and <relativePath>../pom.xml</relativePath>. Do not add a version element for the module itself.

File 1: ecommerce-common-lib/pom.xml
- artifactId ecommerce-common-lib, packaging jar
- dependencies: org.springframework.boot:spring-boot-starter-web with scope provided; org.springframework.boot:spring-boot-starter-validation with scope provided; org.springframework.boot:spring-boot-starter-test with scope test; io.jsonwebtoken:jjwt-api:0.12.7; io.jsonwebtoken:jjwt-impl:0.12.7 with scope runtime; io.jsonwebtoken:jjwt-jackson:0.12.7 with scope runtime; org.projectlombok:lombok with scope provided
- no build section

File 2: eureka-server/pom.xml
- artifactId eureka-server
- dependencies: org.springframework.cloud:spring-cloud-starter-netflix-eureka-server (no version, it comes from the Spring Cloud BOM); org.springframework.boot:spring-boot-starter-test with scope test
- build section with one plugin: org.springframework.boot:spring-boot-maven-plugin

File 3: config-server/pom.xml
- artifactId config-server
- dependencies: org.springframework.cloud:spring-cloud-config-server (no version); org.springframework.cloud:spring-cloud-starter-netflix-eureka-client (no version); org.springframework.boot:spring-boot-starter-test with scope test
- build section with one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete content of all three files.
```
### `a6c1b118` — repair → —

State `BLOCKED`. Session `a6c1b118ef554dfbbfd18b6a1da121b0`.

```
Four existing files have one XML error each. Fix only that error in each of them, and change nothing else in them.

Files: api-gateway/pom.xml, auth-service/pom.xml, customer-service/pom.xml, product-service/pom.xml

The error: each build section contains a plugin element directly. Maven rejects that with "Unrecognised tag: plugin". A build section must contain a plugins element, and the plugin element goes inside it.

So in each of the four files, change this shape:
    <build>
        <plugin>...</plugin>
    </build>

to this shape, keeping the plugin element and its contents exactly as they are:
    <build>
        <plugins>
            <plugin>...</plugin>
        </plugins>
    </build>

Do not touch any other file. Do not change eureka-server/pom.xml or config-server/pom.xml; they are already correct.
```
### `f8f7e4e7` — create → —

State `CANCELLED`. Session `f8f7e4e7247749319daa0f93002c63de`.

```
Create exactly one new file: eureka-server/src/main/java/com/ecommerce/eureka/EurekaServerApplication.java. Do not create or change any other file.

It is the Spring Boot entry point of the eureka-server module, whose pom.xml already exists.

Requirements:
- package com.ecommerce.eureka
- class EurekaServerApplication, public, with no other members
- annotations @SpringBootApplication and @EnableEurekaServer
- imports: org.springframework.boot.SpringApplication, org.springframework.boot.autoconfigure.SpringBootApplication, org.springframework.cloud.netflix.eureka.server.EnableEurekaServer
- main: public static void main(String[] args) calling SpringApplication.run(EurekaServerApplication.class, args)
- plain Java: no Lombok, no comments, no javadoc
```
### `3688e644` — create → eureka-server/src/main/java/com/ecommerce/eureka/EurekaServerApplicati

State `VERIFICATION_FAILED`. Session `3688e644a04841f48309ef1992095771`.

```
Create exactly one new file: eureka-server/src/main/java/com/ecommerce/eureka/EurekaServerApplication.java. Do not create or change any other file.

It is the Spring Boot entry point of the eureka-server module, whose pom.xml already exists.

Requirements:
- package com.ecommerce.eureka
- class EurekaServerApplication, public, with no other members
- annotations @SpringBootApplication and @EnableEurekaServer
- imports: org.springframework.boot.SpringApplication, org.springframework.boot.autoconfigure.SpringBootApplication, org.springframework.cloud.netflix.eureka.server.EnableEurekaServer
- main: public static void main(String[] args) calling SpringApplication.run(EurekaServerApplication.class, args)
- plain Java: no Lombok, no comments, no javadoc
```
### `4eeafe86` — fix-round → —

State `BLOCKED`. Session `4eeafe86623541f381957fda5791c38d`.

```
The Maven test command failed. Read the affected files, then propose the smallest change that makes it pass. Keep existing behavior and public APIs; do not delete tests or weaken assertions to pass; do not add dependencies.
```
### `cf10c06a` — create → eureka-server/src/main/resources/application.yml

State `VERIFICATION_FAILED`. Session `cf10c06af2fa474b9d8409848e9ede27`.

```
Create exactly one new file: eureka-server/src/main/resources/application.yml. Do not create or change any other file.

It is the only configuration file of the eureka-server module. Write valid YAML, two-space indentation, no comments.

Settings, exactly these and nothing else:
- server.port: 8761
- spring.application.name: eureka-server
- eureka.instance.hostname: localhost
- eureka.client.register-with-eureka: false
- eureka.client.fetch-registry: false
- eureka.server.enable-self-preservation: false
- eureka.client.service-url.defaultZone: http://localhost:8761/eureka/
```
### `3a7bdc24` — create → config-server/src/main/java/com/ecommerce/config/ConfigServerApplicati

State `VERIFICATION_FAILED`. Session `3a7bdc242eb745f1a05e8c6a6015b7d8`.

```
Create exactly one new file: config-server/src/main/java/com/ecommerce/config/ConfigServerApplication.java. Do not create or change any other file.

It is the Spring Boot entry point of the config-server module, whose pom.xml already exists.

Requirements:
- package com.ecommerce.config
- class ConfigServerApplication, public, with no other members
- annotations @SpringBootApplication and @EnableConfigServer
- imports: org.springframework.boot.SpringApplication, org.springframework.boot.autoconfigure.SpringBootApplication, org.springframework.cloud.config.server.EnableConfigServer
- main: public static void main(String[] args) calling SpringApplication.run(ConfigServerApplication.class, args)
- plain Java: no Lombok, no comments, no javadoc
```
### `4b6ed197` — create → config-server/src/main/resources/application.yml

State `VERIFICATION_FAILED`. Session `4b6ed197189b44bb94eddc8e335df0f6`.

```
Create exactly one new file: config-server/src/main/resources/application.yml. Do not create or change any other file.

It is the only configuration file of the config-server module. Write valid YAML, two-space indentation, no comments.

Settings, exactly these and nothing else:
- server.port: 8881
- spring.application.name: config-server
- spring.profiles.active: native
- spring.cloud.config.server.native.search-locations: file:../config-repo
- eureka.client.service-url.defaultZone: http://localhost:8761/eureka/
```
### `b3ae4b22` — edit → eureka-server/src/main/java/com/ecommerce/eureka/EurekaServerApplicati

State `APPLIED_UNVERIFIED`. Session `b3ae4b225af94e88a2ab3f99cff97f6d`.

```
Fix exactly one file: eureka-server/src/main/java/com/ecommerce/eureka/EurekaServerApplication.java. Change nothing else and create nothing else.

The file is broken: it holds a Maven pom, which is XML starting with an <?xml declaration. It must hold the Java compilation unit of the same module instead. Replace the whole content with Java.

Requirements:
- package com.ecommerce.eureka
- class EurekaServerApplication, public, with no other members
- annotations @SpringBootApplication and @EnableEurekaServer
- imports: org.springframework.boot.SpringApplication, org.springframework.boot.autoconfigure.SpringBootApplication, org.springframework.cloud.netflix.eureka.server.EnableEurekaServer
- main: public static void main(String[] args) calling SpringApplication.run(EurekaServerApplication.class, args)
- plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc
```
### `2d9c50c2` — edit → —

State `BLOCKED`. Session `2d9c50c25f5349abb01a80b1e449307c`.

```
Fix exactly one file: ecommerce-common-lib/pom.xml. Change nothing else and create nothing else.

Add two dependencies to the existing <dependencies> list, immediately after the spring-boot-starter-validation entry, in this order:
1. org.springframework.boot:spring-boot-starter-aop with scope provided
2. org.springframework.boot:spring-boot-starter-security with scope provided

Both inherit their version from the Spring Boot parent, so neither gets a version element of its own. Keep every existing dependency, comment and element exactly as it is, and keep the file well-formed XML.
```
### `f6f811d9` — edit → ecommerce-common-lib/pom.xml

State `VERIFICATION_FAILED`. Session `f6f811d94fb04dd5b24fef45cbd68bb6`.

```
Fix exactly one file: ecommerce-common-lib/pom.xml. Change nothing else and create nothing else.

Rewrite the whole file. Its <dependencies> list must contain, in this order, and nothing else:
1. org.springframework.boot:spring-boot-starter-web, scope provided
2. org.springframework.boot:spring-boot-starter-aop, scope provided
3. org.springframework.boot:spring-boot-starter-validation, scope provided
4. org.springframework.boot:spring-boot-starter-test, scope test
5. io.jsonwebtoken:jjwt-api, version 0.12.7
6. io.jsonwebtoken:jjwt-impl, version 0.12.7, scope runtime
7. io.jsonwebtoken:jjwt-jackson, version 0.12.7, scope runtime
8. org.projectlombok:lombok, scope provided

Keep modelVersion 4.0.0, the parent block with groupId com.ecommerce, artifactId ecommerce, version 1.0.0 and <relativePath>../pom.xml</relativePath>, the artifactId ecommerce-common-lib, and packaging jar. Give no version element to a Spring Boot starter. The file must be well-formed XML.
```
### `e4e9511f` — edit → config-server/src/main/java/com/ecommerce/config/ConfigServerApplicati

State `APPLIED_UNVERIFIED`. Session `e4e9511f20524bc3b421cf45b80f6b46`.

```
Fix exactly one file: config-server/src/main/java/com/ecommerce/config/ConfigServerApplication.java. Change nothing else and create nothing else.

The file is broken: it holds a Maven pom, which is XML starting with an <?xml declaration. It must hold the Java compilation unit of the same module instead. Replace the whole content with Java.

Requirements:
- package com.ecommerce.config
- class ConfigServerApplication, public, with no other members
- annotations @SpringBootApplication and @EnableConfigServer
- imports: org.springframework.boot.SpringApplication, org.springframework.boot.autoconfigure.SpringBootApplication, org.springframework.cloud.config.server.EnableConfigServer
- main: public static void main(String[] args) calling SpringApplication.run(ConfigServerApplication.class, args)
- plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc
```
### `4246d157` — edit → ecommerce-common-lib/pom.xml

State `VERIFICATION_BLOCKED`. Session `4246d157c9104911a7ed85f6aebaca10`.

```
Fix exactly one file: ecommerce-common-lib/pom.xml. Change nothing else and create nothing else.

One dependency is missing from its <dependencies> list. Add org.springframework.boot:spring-boot-starter-security with scope provided, immediately after the spring-boot-starter-aop entry.

Change nothing else: keep every existing dependency, the parent block, the artifactId, the packaging and the build section exactly as they are. The file must stay well-formed XML.
```
### `aa8acc0e` — create → ecommerce-common-lib/src/main/java/com/ecommerce/common/web/ServerTime

State `VERIFICATION_FAILED`. Session `aa8acc0e8fe341d48955371bdc788fdf`.

```
Create exactly one new file: ecommerce-common-lib/src/main/java/com/ecommerce/common/web/ServerTimestampFilter.java. Do not create or change any other file.

It is a servlet filter that stamps the server date on every response. Plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc.

Requirements:
- package com.ecommerce.common.web
- public class ServerTimestampFilter extends OncePerRequestFilter
- annotations on the class: @Component, and @Order(1)
- override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain) throws ServletException, IOException
- in the method body: add one response header named X-Server-Timestamp whose value is OffsetDateTime.now().toString(), then call filterChain.doFilter(request, response)
- no other methods and no fields
- imports exactly: jakarta.servlet.FilterChain, jakarta.servlet.ServletException, jakarta.servlet.http.HttpServletRequest, jakarta.servlet.http.HttpServletResponse, java.io.IOException, java.time.OffsetDateTime, org.springframework.web.filter.OncePerRequestFilter, org.springframework.stereotype.Component, org.springframework.core.annotation.Order
```
### `0605b8b4` — create → —

State `BLOCKED`. Session `0605b8b40fff460499875a36cfe6dbe2`.

```
Create exactly one new file: ecommerce-common-lib/src/main/java/com/ecommerce/common/web/ServerTimestampFilter.java. Do not create or change any other file.

It is a servlet filter that stamps the server date on every response. Plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc.

Requirements:
- package com.ecommerce.common.web
- public class ServerTimestampFilter extends OncePerRequestFilter
- annotations on the class: @Component, and @Order(1)
- override protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain) throws ServletException, IOException
- in the method body: add one response header named X-Server-Timestamp whose value is OffsetDateTime.now().toString(), then call filterChain.doFilter(request, response)
- no other methods and no fields
- imports exactly: jakarta.servlet.FilterChain, jakarta.servlet.ServletException, jakarta.servlet.http.HttpServletRequest, jakarta.servlet.http.HttpServletResponse, java.io.IOException, java.time.OffsetDateTime, org.springframework.web.filter.OncePerRequestFilter, org.springframework.stereotype.Component, org.springframework.core.annotation.Order
```
### `b1eac97d` — edit → ecommerce-common-lib/src/main/java/com/ecommerce/common/web/ServerTime

State `VERIFICATION_FAILED`. Session `b1eac97d673d42c585bf8baaf390cc8e`.

```
Fix exactly one file: ecommerce-common-lib/src/main/java/com/ecommerce/common/web/ServerTimestampFilter.java. Change nothing else and create nothing else.

The file does not compile: the class it extends is never imported, so javac reports cannot find symbol: class OncePerRequestFilter. Add that one import line and change nothing else.

Requirement:
- add exactly this import, alongside the existing ones: org.springframework.web.filter.OncePerRequestFilter
- keep the package line, the @Component and @Order(1) annotations, the class declaration, the doFilterInternal method and its body, and every existing import, unchanged
- plain Java only: no XML, no pom element, no comments, no javadoc
```
### `f3cfaa92` — edit → ecommerce-common-lib/src/main/java/com/ecommerce/common/web/ServerTime

State `VERIFICATION_BLOCKED`. Session `f3cfaa924ab64873929bcbea3231eba6`.

```
Fix exactly one file: ecommerce-common-lib/src/main/java/com/ecommerce/common/web/ServerTimestampFilter.java. Change nothing else and create nothing else.

The only problem is one wrong import: the line reading import org.springframework.boot.web.filter.OncePerRequestFilter; names a package that does not exist. Replace that single line with this exact line, character for character:

import org.springframework.web.filter.OncePerRequestFilter;

Requirements:
- after the change, no import line in this file contains the word boot
- keep the package line, every other import, the @Component and @Order(1) annotations, the class declaration and the doFilterInternal body exactly as they are
- plain Java only: no XML, no pom element, no comments, no javadoc
```
### `b915d064` — create → —

State `DISCOVERING`. Session `b915d064bb654a3d924a23b24a601990`.

```
Create exactly one new file: ecommerce-common-lib/src/main/java/com/ecommerce/common/api/ApiResponse.java. Do not create or change any other file.

It is the response envelope every service returns. Plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc.

Requirements:
- package com.ecommerce.common.api
- public class ApiResponse<T>
- private fields: T data, String message, java.time.OffsetDateTime serverTime
- a private constructor taking (T data, String message, OffsetDateTime serverTime) that assigns the three fields
- public static <T> ApiResponse<T> ok(T data) returning a new response with that data, the message "success", and OffsetDateTime.now() as serverTime
- public static <T> ApiResponse<T> error(String message) returning a new response with null data, that message, and OffsetDateTime.now() as serverTime
- public getters getData, getMessage, getServerTime returning those fields, and no setters
- imports: java.time.OffsetDateTime only
- no annotations on the class, and it extends nothing
```

### `4cced7cc` — create → pom.xml

State `APPLIED_UNVERIFIED`. Session `4cced7cc03784be096ceda13718eb251`.

```
Create exactly one new file: pom.xml at the project root. Do not create any other file.

This is the Maven aggregator for a multi-module Spring Boot application.

Requirements:
- modelVersion 4.0.0
- parent org.springframework.boot : spring-boot-starter-parent : 3.5.16
- groupId com.ecommerce, artifactId ecommerce, version 1.0.0, packaging pom
- property java.version = 17
- a modules list containing exactly these seven, in this order: ecommerce-common-lib, eureka-server, config-server, api-gateway, auth-service, customer-service, product-service
- dependencyManagement importing org.springframework.cloud : spring-cloud-dependencies : 2025.0.3 with type pom and scope import
- nothing else: no dependencies section, no build plugins, no other properties

Write the complete file content, not a fragment.
```
### `6b082df5` — create → ecommerce-common-lib/pom.xml

State `VERIFICATION_FAILED`. Session `6b082df5245f43999b6deef918feefae`.

```
Create exactly one new file: ecommerce-common-lib/pom.xml. Do not create or change any other file.

It is a Maven module of the aggregator pom.xml in this project root.
- modelVersion 4.0.0
- parent: groupId com.ecommerce, artifactId ecommerce, version 1.0.0, and <relativePath>../pom.xml</relativePath>
- no version element for the module itself
- artifactId ecommerce-common-lib, packaging jar
- dependencies, in this order:
  1. org.springframework.boot:spring-boot-starter-web, scope provided
  2. org.springframework.boot:spring-boot-starter-validation, scope provided
  3. io.jsonwebtoken:jjwt-api, version 0.12.7
  4. io.jsonwebtoken:jjwt-impl, version 0.12.7, scope runtime
  5. io.jsonwebtoken:jjwt-jackson, version 0.12.7, scope runtime
  6. org.projectlombok:lombok, scope provided
  7. org.springframework.boot:spring-boot-starter-test, scope test
- no build section, no properties section

Write the complete file content.
```
### `67848971` — create → eureka-server/pom.xml, config-server/pom.xml

State `VERIFICATION_FAILED`. Session `67848971c5234783b608912e539c8900`.

```
Create exactly two new files. Do not change any existing file.

Both are Maven modules of the aggregator pom.xml in this project root. Each one uses modelVersion 4.0.0 and a parent with groupId com.ecommerce, artifactId ecommerce, version 1.0.0, and <relativePath>../pom.xml</relativePath>. Neither module repeats a version element of its own.

File 1: eureka-server/pom.xml
- artifactId eureka-server
- dependencies: org.springframework.cloud:spring-cloud-starter-netflix-eureka-server with no version element (the Spring Cloud BOM in the parent supplies it); org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

File 2: config-server/pom.xml
- artifactId config-server
- dependencies: org.springframework.cloud:spring-cloud-config-server with no version; org.springframework.cloud:spring-cloud-starter-netflix-eureka-client with no version; org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete content of both files.
```
### `52fcdff5` — create → api-gateway/pom.xml

State `VERIFICATION_FAILED`. Session `52fcdff5f86d46bcb97711197404da04`.

```
Create exactly one new file: api-gateway/pom.xml. Do not change any existing file.

It is a Maven module of the aggregator pom.xml in this project root.
- modelVersion 4.0.0
- parent: groupId com.ecommerce, artifactId ecommerce, version 1.0.0, <relativePath>../pom.xml</relativePath>
- no version element of its own
- artifactId api-gateway
- dependencies:
  1. org.springframework.cloud:spring-cloud-starter-gateway with no version
  2. org.springframework.cloud:spring-cloud-starter-netflix-eureka-client with no version
  3. org.springframework.cloud:spring-cloud-starter-config with no version
  4. org.springframework.boot:spring-boot-starter-actuator with no version
  5. org.springframework.boot:spring-boot-starter-test with scope test
- a build section containing exactly one plugin: org.springframework.boot:spring-boot-maven-plugin

Write the complete file content.
```
### `599535b3` — create → —

State `BLOCKED`. Session `599535b3c184434a974e780dbe1a00cb`.

```
Create exactly two new files. Do not create or change any other file.

File 1: config-repo/ecommerce-shared.yml
It is a Spring Cloud Config Server resource served to every service from the local config-repo folder. Write valid YAML, two-space indentation, no comments, and exactly these settings:
- management.endpoints.web.exposure.include: health,info
- logging.level.org.springframework: INFO

File 2: .gitignore
It is a plain-text ignore file for a Maven project. One entry per line, no comments, exactly these five lines in this order:
target/
.mvn/
*.iml
.idea/
.vscode/
```
### `93b790d3` — create → ecommerce-common-lib/src/main/java/com/ecommerce/common/api/ApiRespons

State `VERIFICATION_BLOCKED`. Session `93b790d325e74079a680fbf50dee1d2e`.

```
Create exactly one new file: ecommerce-common-lib/src/main/java/com/ecommerce/common/api/ApiResponse.java. Do not create or change any other file.

It is the response envelope every service returns. Plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc. This task is about the api package only, and no other file in this project.

Requirements:
- package com.ecommerce.common.api
- public class ApiResponse<T>
- private fields: T data, String message, OffsetDateTime serverTime
- a private constructor taking (T data, String message, OffsetDateTime serverTime) that assigns the three fields
- public static <T> ApiResponse<T> ok(T data) returning a new ApiResponse with that data, the message "success", and OffsetDateTime.now()
- public static <T> ApiResponse<T> error(String message) returning a new ApiResponse with null data, that message, and OffsetDateTime.now()
- public getters getData, getMessage, getServerTime and no setters
- imports: java.time.OffsetDateTime only
- send the whole file as complete content, never as edits
```
### `0662918b` — create → config-repo/ecommerce-shared.yml

State `APPLYING`. Session `0662918be25c4aaf8823ba5d38920e58`.

```
Create exactly one new file: config-repo/ecommerce-shared.yml. Do not create or change any other file.

It is a Spring Cloud Config Server resource served to every service from the local config-repo folder on the native profile. Write valid YAML, two-space indentation, no comments.

Settings, exactly these and nothing else:
- management.endpoints.web.exposure.include: health,info
- logging.level.org.springframework: INFO

Send the whole file as complete content, never as edits.
```
### `40ec1754` — create → —

State `BLOCKED`. Session `40ec175418da43e5959f6ee054a023a6`.

```
Create exactly one new file: .gitignore at the project root. Do not create or change any other file.

It is the ignore list for a Maven project whose build output currently shows as uncommitted work. Plain text, one entry per line, no comments.

Lines, in this order and exactly these:
target/
.mvn/
*.iml
.idea/
.vscode/

Send the whole file as complete content, never as edits.
```
### `0b7379c2` — create → —

State `BLOCKED`. Session `0b7379c237334592ae85d969a0d3bd0b`.

```
Create exactly one new file: ecommerce-common-lib/src/main/java/com/ecommerce/common/audit/ServerAuditAspect.java. Do not create or change any other file.

It logs the server date around every controller call. Plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc. This task is about the audit package only, and no other file in this project.

Requirements:
- package com.ecommerce.common.audit
- public class ServerAuditAspect, annotated @Aspect and @Component
- one private static final Logger field named log, created with LoggerFactory.getLogger(ServerAuditAspect.class)
- a public void method named restControllers annotated @Pointcut("within(@org.springframework.web.bind.annotation.RestController *)") with an empty body
- a public Object method named audit annotated @Around("restControllers()"), taking one ProceedingJoinPoint named joinPoint, throwing Throwable
- in its body: Object result = joinPoint.proceed(); then log.info("serverTime={} call={}", OffsetDateTime.now(), joinPoint.getSignature().toShortString()); then return result
- imports exactly: org.aspectj.lang.ProceedingJoinPoint, org.aspectj.lang.annotation.Around, org.aspectj.lang.annotation.Aspect, org.aspectj.lang.annotation.Pointcut, org.slf4j.Logger, org.slf4j.LoggerFactory, org.springframework.stereotype.Component, java.time.OffsetDateTime
- send the whole file as complete content, never as edits
```
### `a361af19` — create → .gitignore

State `VERIFICATION_BLOCKED`. Session `a361af19d7b54509857aae628bf90eef`.

```
Create exactly one new file: .gitignore at the project root. Do not create or change any other file.

It is the ignore list for a Maven project whose build output currently shows as uncommitted work. Plain text, one entry per line, no comments.

Lines, in this order and exactly these:
target/
.mvn/
*.iml
.idea/
.vscode/

Send the whole file as complete content, never as edits.
```
### `5e24daff` — create → ecommerce-common-lib/src/main/java/com/ecommerce/common/audit/ServerAu

State `VERIFICATION_BLOCKED`. Session `5e24daffa82e4000b61617bb811bb159`.

```
Create exactly one new file: ecommerce-common-lib/src/main/java/com/ecommerce/common/audit/ServerAuditAspect.java. Do not create or change any other file.

It logs the server date around every controller call. Plain Java only: no XML, no pom element, no Lombok, no comments, no javadoc. This task is about the audit package only, and no other file in this project.

Requirements:
- package com.ecommerce.common.audit
- public class ServerAuditAspect, annotated @Aspect and @Component
- one private static final Logger field named log, created with LoggerFactory.getLogger(ServerAuditAspect.class)
- a public void method named restControllers annotated @Pointcut("within(@org.springframework.web.bind.annotation.RestController *)") with an empty body
- a public Object method named audit annotated @Around("restControllers()"), taking one ProceedingJoinPoint named joinPoint, throwing Throwable
- in its body: Object result = joinPoint.proceed(); then log.info("serverTime={} call={}", OffsetDateTime.now(), joinPoint.getSignature().toShortString()); then return result
- imports exactly: org.aspectj.lang.ProceedingJoinPoint, org.aspectj.lang.annotation.Around, org.aspectj.lang.annotation.Aspect, org.aspectj.lang.annotation.Pointcut, org.slf4j.Logger, org.slf4j.LoggerFactory, org.springframework.stereotype.Component, java.time.OffsetDateTime
- send the whole file as complete content, never as edits
```

## Plan text the model produced through the tool

The agent's own plan output, pasted exactly as it came back, including any malformed or truncated part.

## Defects found in the agent

| id | Symptom seen in the run | Root cause in code | Fix | Test added | Re-run outcome |
| --- | --- | --- | --- | --- | --- |
| D1 | predicted before the run: `mvn -o -B test` cannot bootstrap a cold project | `verification.py:22` | open — not reached yet | | |
| D2 | M1's exit criterion was unbuildable: an aggregator naming seven absent modules cannot compile | the plan, not the code | M1 re-split into P1 (parent) then P2+ (modules) before any build | — | P1 wrote cleanly |
| D3 | `Provider HTTP 500; no automatic retry or fallback.` and nothing else — the milestone died with no reason anywhere | `providers.py` deliberately dropped the error body, and the session record kept no event for it | a capped, one-line, **redacted** excerpt of the body is now appended; the URL, payload and key still never appear | 3 cases in `test_agent.py` (reason carried, 90 KiB body cannot become a log, unreadable body still reports the code) | re-sent as P2 |
| D4 | **the model was answering from a third of the prompt, silently** | `OllamaProvider` never set `num_ctx`, so Ollama applied its own default. Measured: a 20 000-char and a 60 000-char prompt both returned `prompt_eval_count=2050` — and `num_predict=4096` was capped the same quiet way against a 2048 window | `context_window(prompt_chars, output_tokens)` sizes a power-of-two `num_ctx` (2048…16384) per request | 3 cases: the measured sizes, "every window is a power of two ≥ the default", and the payload carries it | re-sent as P2 |
| D5 | the folder was granted into Change mode, Auto-Apply was remembered for it, and **after a restart the same folder was back in Chat** — so its first message was answered in prose | only `set_composer` wrote `_composer_pref`; the three grant paths (`set_repo`, `browse`, `new_project`) each called `_select_branch(composer=CHANGE_COMPOSER)` and persisted nothing | one `_grant_folder(key, path)` helper: a never-seen folder lands on Change and *stays* there; a folder the person switched by hand comes back as they left it | 3 cases in `test_controller.py` (`RememberedFolderModeTests`) — including the one that failed the first version of the fix | verified live: re-granting shows `Change · ecommerce` |

**D5 is the user's own stated rule, enforced by code.** "Granting a folder must land on Change mode"
was true for one session and false for the next; the first attempt at the fix over-corrected and made
every re-grant overwrite a hand-picked Chat mode, and `test_a_mode_the_person_picked_by_hand_is_remembered_the_same_way`
caught it before it shipped. That test is the reason the fix only persists an explicit *Change*.

## The observer effect in this exercise

Running the tool's own 710-test suite while a model turn was in flight is the most likely cause of the
D3 500: the suite opens real Tk windows and the box has 16 GB with CPU-only inference. **A dogfood run
and a full test run do not share a laptop.** Sequencing them (fix → suite → idle → re-run the milestone)
is part of the method, not a workaround.

## D6 — the honest context costs minutes

After D4 shipped, a **one-file** prompt took about six minutes and a **three-file** one timed out. The
cause is not the prompt: `num_ctx` is now sized to the request (≈8192 here), so Ollama has to reload the
model to change its KV size, and prompt processing on four CPU cores is the dominant cost — exactly the
shape `MODEL-BENCHMARK.md` measured, arriving now that the tool is no longer being fed a truncated view.

This is not a reason to undo the fix. A reply written from a third of the files is faster and wrong. The
dial is the one the tool already exposes: **Settings → Project & plan → Request timeout**, raised to 900 s
through the UI during this run and confirmed in `.agent-projects.json`. Prompts are sized to one or two
files, which was the plan's rule before the fix and is now a measured one.

## D7 — a reactor cannot build before its children exist

The tool ran `mvn -B test` itself after the second write and reported Maven's own first error lines,
redacted, in 15.0 s: `Child module … does not exist`. Predicted as D2 and it arrived exactly there.

**D1 also needs correcting, in this file's own style of measuring rather than assuming.** The predicted
offline blocker was about `verification.py`'s `mvn -o -B test`. What the run exercised first is the
*recipe* path, `mvn -B test`, which is online — and it resolved the Spring Boot 3.5.16 parent from Maven
Central on a cold `~/.m2` in 15 s. So D1 stays open but is narrower than predicted: it belongs to the
syntax-check/verification surface, not to the project's own build command.

## M1 exit — the agent built the reactor and it is green

With all seven module poms present and the four malformed `<build>` sections repaired one file at a
time, the tool's own **Run** action executed `mvn -B test` at the reactor root. Nothing was run from
the shell during this exercise. The captured output, from the session record the agent wrote:

```
[INFO] ecommerce .......................................... SUCCESS [  0.011 s]
[INFO] ecommerce-common-lib ............................... SUCCESS [  8.096 s]
[INFO] eureka-server ...................................... SUCCESS [ 21.576 s]
[INFO] config-server ...................................... SUCCESS [  7.549 s]
[INFO] api-gateway ........................................ SUCCESS [  6.851 s]
[INFO] auth-service ....................................... SUCCESS [ 11.193 s]
[INFO] customer-service ................................... SUCCESS [  0.381 s]
[INFO] product-service .................................... SUCCESS [  0.324 s]
[INFO] BUILD SUCCESS
```

The tool's own verdict on that was `no tests ran (exit 0)` and a review state of **Verification
incomplete** — which is the right answer, not a complaint: a green build with zero tests proves the
poms parse and the dependency graph resolves, and nothing else. `tests_observed=False`, `proof=None`.

**The history promise was tested, not assumed.** Opening the project's sidebar row rehydrated the
whole thread through `display_session` → `chat_sessions(runs, root, chat_id)`: 77 lines of Activity
covering all 12 tasks that share chat `94eacf96`, from "Create exactly three new files" to the last
`mvn -B test`. Note the shape of the storage, because it is easy to mistake for data loss: **Change-mode
tasks are not chats** — each is a `.agent-runs/<sessionId>` record grouped under a `chat_id`, and no
`chat.json` exists for a folder that was only ever driven in Change mode. That is by design, and it is
why the sidebar shows one row per task thread rather than one per build.

## D9 — nine invisible dialogs stacked over a working window

`_ask()` blocks a worker for 1800 s waiting for the browser. When that wait expires the worker gives
up, the job ends and the status line updates — and the modal stays on screen forever. Nine fix-round
offers were pushed across this long build session, nobody answered them, and `#modal-root` ended up
holding **nine scrims**, each `pointer-events: auto`, the topmost one covering the app while the eight
behind it were invisible. The window looked idle; every click hit a dead dialog. `git dirty=0` and
`busy=False` from the server, nine blocking overlays in the browser: state the two halves of the app
had no way to reconcile.

Measured, not inferred: `[...document.getElementById('modal-root').children].length === 9`, all
`className: "scrim"` with no `out` class.

The fix is at the boundary that owns the fact: the server stops waiting, so the server withdraws the
question. `_ask()` now keeps the result of `waiter.wait()` and, when it times out, emits
`{"kind": "retract", "id": request_id}` and says so in the status line; the front-end removes the sheet
whose `data-ask` matches, walking the mounted scrims instead of building a CSS selector from a network
string. `set_reply()` drops a reply whose question is already gone, which also closes the `_replies`
and `_answers` leak.

| id | Symptom seen in the run | Root cause in code | Fix | Test added | Re-run outcome |
| --- | --- | --- | --- | --- | --- |
| D9 | nine "Keep going?" modals left stacked over the app after their jobs ended; the top one swallowed every click | `controller.py` `_ask()` treated `waiter.wait(1800)` as fire-and-forget — nothing told the browser the question had closed | retract event on expiry + `data-ask` tags + a DOM-walking `retractAsk()`; late replies dropped in `set_reply()` | 3 cases in `test_controller.py` (`WithdrawnQuestionTests`: withdrawn, answered-then-not-withdrawn, late reply dropped) and 3 in `test_webapp.py` (`WithdrawnAskWindowTests`) | 275 tests green in the three affected modules; suite re-run below |

Tk is exempt from the shared-sentence rule for exactly this reason, and says so: `labels.SINGLE_WINDOW_STATUS`
names `ask_expired`, because a `messagebox` holds the mainloop until it is answered and has no expiry to
report. `test_only_a_real_key_is_exempt_from_the_other_window` keeps that list honest.

## D10 — driving the composer by its class pressed Stop

Recorded honestly: the agent's logic was right, the *handle* was wrong. While a task runs the composer
renders two buttons with the `send` class — `send stop` first, then the real Send, now labelled "Queue".
A scripted `document.querySelector('.send')` matched the **first** one, so the four prompts meant for the
queue each landed on Stop, and the Eureka task that had been running for seven minutes ended `CANCELLED`.
Stop did precisely what it says; there was just no way to name it that did not also name Send.

The fix is two ids on the buttons that share a class (`#stop`, `#send`), so the composer's destructive
control cannot be reached by an expression intended for the ordinary one. `ComposerButtonHandlesTests`
pins both handles *and* the render order that made the mistake, so the test says why it exists.

Worth keeping beside the defects because it is a cost, not a bug: **one cancelled turn is seven minutes
of a 3 B model on four CPU cores**, and at this rate a mis-click is the most expensive mistake available
in this exercise. Every later prompt in this file goes through `#send`.

## D11 — the agent wrote a pom into a `.java` path and Auto-Apply wrote it

The first Java file of the whole exercise came back as this, at
`eureka-server/src/main/java/com/ecommerce/eureka/EurekaServerApplication.java`:

```java
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" …
    <artifactId>eureka-server</artifactId>
```

Maven found it in 11.2 s: `class, interface, enum, or record expected` at `[1,1]`. The prompt named the
Java entry point and mentioned "whose pom.xml already exists"; a 3 B model answered with the pom. The
write itself was unremarkable — path legal, content non-empty, diff small — so every existing gate let
it through, and with Auto-Apply on nobody was looking at the card that showed it.

**The tool already had this check.** `prepare_changes` refuses a `.py` that does not parse as Python and
a `.json` that does not parse as JSON — the exact same class of error, for the two languages it happened
to know. Java and XML were simply never added, and this run added them:

- `.java` is refused if the content opens with `<` (it is a markup document) or contains no declaration
  keyword at all. Not a Java parser — the two facts about a compilation unit that cannot be otherwise.
  Folded on the suffix like the gate above it, because `workspace.py` admits `APP.JAVA`.
- `.xml` is parsed for real, so an unbalanced tag is refused before the file reaches the disk.
- A DTD is refused outright, **before** the parser is handed the text: the content is a model's output,
  and a DTD is the only way to make an XML parser reach beyond what the proposal itself wrote. No Maven
  pom carries one — they declare schemas in `xsi:schemaLocation`.

| Test | Case |
| --- | --- |
| `test_agent.py` | pom into `.java` refused; `.java` with no declaration refused; real compilation unit passes; unbalanced XML refused; well-formed pom passes; DTD refused without parsing; `Application.JAVA` gets the same rule |

The cost this saves is measurable already: the failure burned the eleven seconds of a build **plus a full
fix round of `qwen2.5-coder:3b` on four cores** to change one file back into the language its name
promised. D8 (the four malformed `<build>` sections) is the same shape of finding two days earlier: the
model writes structurally invalid text, and the tool learns about it only from the project's own command.

**The live window was not running this code when D11 happened.** `webapp.main` imports the controller at
start, so a fix to `engine.py` reaches the dogfood run only after a restart — and a restart discards the
in-memory queue. Restarts therefore happen only at a milestone boundary, with the queue drained.

### D11 recurred on the very next module, which settles what it is

`config-server/src/main/java/com/ecommerce/config/ConfigServerApplication.java` — written by the task
after the Eureka one, before the restart, so also before the guard existed — came out the same way:
`<?xml version="1.0"?>` where a compilation unit belongs, and Maven again reporting
`[1,1] class, interface, enum, or record expected`. Two for two. It is not the model misreading one
sentence: **a prompt that names a module pom next to the Java entry point it must write gets the pom as
an answer from `qwen2.5-coder:3b`.** The prompts then began to say "plain Java only: no XML, no pom
element", and that version produced correct Java for both services — which is the remedy D11's gate now
applies automatically, by refusing the wrong write instead of waiting for a human to word the prompt
differently.

What the restart also bought is the first live reading of D12. The common-lib pom edit had blocked
earlier with no reason anywhere; re-run under the fixed engine, its session record said:

```
auto_read        -> ecommerce-common-lib/pom.xml
rejected_action  -> Read the current file before proposing a change: ecommerce-common-lib/pom.xml
proposal, written, run -> failed
```

One line, and the whole loop becomes explicable: the model proposed against a file it had not re-read,
the engine auto-read it for the model, and the second attempt landed. That is a recoverable sequence that
previously read as "the model kept repeating itself".

The write that landed was still not the asked-for one: it added `spring-boot-starter-aop` and **silently
dropped `spring-boot-starter-security`** from an eight-item numbered list. A 3 B model honouring seven of
eight explicit entries is the measured shape of D8 restated — the list has to be re-verified against the
file afterwards, not assumed.

## D12 — a blocked task recorded that it blocked, and nothing else

The fix round for D11 spent five turns and ended `BLOCKED`. Its session record, read afterwards:

```
tool             read_file …EurekaServerApplication.java
rejected_action  {}      ← no reason
rejected_action  {}
tool             read_file …EurekaServerApplication.java
rejected_action  {}
stopped          {}      ← no reason
```

The model re-read the file, was refused three times, and gave up — and the history says none of that.
`event(session, "rejected_action")` was written one line below `result = {"error": str(exc)[:300]}`, so
the reason was in scope and simply never stored; same for `event(session, "stopped")`. Worse, **no engine
path ever set `session["error"]`**, and the history replay for a failed task is built from exactly that
field (`state_label(state) + "\n\n" + (summary or error or "No applicable proposal was created.")`), so
every blocked milestone reopened as that one bland sentence.

Now both carry the tool's own sentence, capped and `redact()`ed the way D3 made the provider's — the
policy against persisting raw model output still holds, because a `PolicyError` message is the tool's
words, not the model's.

| id | Symptom seen in the run | Root cause in code | Fix | Test added | Re-run outcome |
| --- | --- | --- | --- | --- | --- |
| D12 | three blank `rejected_action` rows and a blank `stopped` in a BLOCKED session; reopened history said only "No applicable proposal was created." | `engine.py` wrote the events without the reason that was in scope one line above, and never set `session["error"]` | `reason=` on both events, `session["error"]` set on block/cancel, both capped at 180/300 chars after `redact()` | 2 cases in `test_agent.py` — one asserts the rejection reason survives, one asserts `session["error"]` equals the redacted raise and the `stopped` row matches it | 89 tests green in `test_agent.py` |

The guard that ended that round was `Model repeated the same action without progress; try another model
or a narrower task.` For once the suggestion was right rather than a guess: the model had been asked to
repair a file whose contents were a different language from its name, and repeating the request to it
does not help — a prompt that *names* the mistake does. The repair that landed was re-asked as
"the file holds a Maven pom, replace the whole content with Java", and D11's gate now refuses the
original write before it can ever reach the disk.

## D13 — a card that disagrees with the server, and the one time it was not what it looked like

Two separate facts got conflated here, and the second is a correction of the first, so both are recorded.

**The defect that is real.** `connectEvents()` reconnects a dropped `EventSource` by itself after 1.5 s,
and every event missed in between was a whole state push; nothing on the reconnect path asked the server
what the state was now. A window can therefore sit on a stale snapshot indefinitely, and only a reload
proves it. The fix is the cheapest possible — `src.onopen` re-reads `/api/bootstrap` and renders it, one
request per (re)connect against a localhost server — and `ReconnectedStreamTests` pins both that line and
that the message/error paths were not disturbed.

**The diagnosis that was wrong, caught by measuring instead of assuming.** During M2 the review card drew
`#apply` disabled while `/api/bootstrap` reported `canApply: True`, and it looked exactly like the drift
above. It was not. Reading the ordering in `run_job`'s worker:

```python
self.busy = False                                   # line 370
on_done(result)                                     # Auto-Apply calls apply(), which blocks on
                                                    # the removal notice
self._emit({"kind": "state", "data": self.snapshot()})   # line 393 — never reached while it waits
```

The push that would describe the pending proposal is emitted *after* the confirmation that is waiting for
a human. The card was stale in the mundane sense that nothing newer had been sent, and the live gate was
the modal — which had been on screen the whole time, and answering it is what let the write through. The
reload did not fix the situation; it showed both truths at once, a card saying "apply me" and a dialog
asking "are you sure".

What survives it, and is a design observation rather than a fix for this round: **a blocked question and
the card for it are rendered from different moments.** A window waiting on an answer does not say so on
the card, only in the dialog — and the dialog can be missed (D9) while `busy=False` reads as idle.

And the guard did its job on the way: the write went through only after the **removal notice** —
"removes 45 of 45 existing lines", because replacing a pom with a class is exactly the shape of accident
`shrink_warning` was built for. With Auto-Apply on, that confirmation is the last human step in the path,
and it appeared precisely where it should have.

## D14 — a second Send disappeared without a trace, twice

Three M3 prompts were sent back to back. One task started. The other two produced **nothing**: no status
line, no queue row, no session in `.agent-runs`, no toast. Later, with one task running and the window
showing a stale `busy=false`, two more prompts did the same thing.

`run_job` opens with `if self.busy: return`, and `start_plan` opens with the same line. The client's
`submit()` guards on `DATA.busy`, but that flag is a *copy* of the server's, updated when an action's
response or an SSE push arrives — so two clicks inside one tick both legitimately see `false`, and the
second one is discarded server-side with no acknowledgement of any kind. A written task prompt is the
most expensive thing the user produces in this app; losing it silently is the worst failure mode in the
run so far, and it is the reason a "just send the next one while that runs" workflow cannot be trusted
here.

The fix moves the decision to the side that actually knows: `start_plan` queues the message instead of
dropping it, reusing the mechanism that already exists for "typed while a task runs" — so the sender gets
the "Queued" toast, the strip shows the line, and the drain starts it in order.

| id | Symptom seen in the run | Root cause in code | Fix | Test added | Re-run outcome |
| --- | --- | --- | --- | --- | --- |
| D14 | prompts sent during a running task vanished — no message, no queue row, no session | `controller.py` `start_plan()` began with a bare `if self.busy: return`; the only guard the user ever saw was the client's copy of that flag, which is a race | a message that arrives while a task runs is handed to `queue_add()`, which acknowledges it and runs it in order | 2 cases in `test_controller.py` (`SecondSendBecomesAQueueItem`: queued not dropped, and both land in order without starting) | verified in tests; the running process predated the fix, so the next two prompts were dropped a second time before the restart |

The last column is the honest one: **the defect reproduced itself while the report was being written**,
which is how the ordering got confirmed — `run_job` sets `busy = False` at line 370 and only pushes the
state afterwards, so the window's flag can be wrong in both directions.

## D15 — the engine computed the advice it wanted to give, then threw it away

`ServerTimestampFilter.java` came out of the model with **one import missing from an explicitly
enumerated nine** — `org.springframework.web.filter.OncePerRequestFilter` — so javac said
`cannot find symbol: class OncePerRequestFilter`. The tool caught it in 11 s, which is the design. What it
did next is the defect.

The fix round re-proposed the same file, byte for byte. The engine rejected it: `Proposal contains an
unchanged file`. It did that three times and then the loop guard ended the task with *"the requested
change is already in the files"* — an accurate diagnosis, arrived at by accident, because the sentence
that would have produced it was never sent. In `engine.py`:

```python
recoverable = ("A rejected action is not a failure. Choose exactly one action again, …")
…
if stale:                      # the ONLY path that ever puts `recoverable` into the observation
    result = {**result, "read": item, "next_action": recoverable}
event(session, "rejected_action", …)
```

Every rejected action except a stale read therefore reached the model as a bare error string with no next
step — and a 3 B model given "Proposal contains an unchanged file" and nothing else answers with the same
file. The variable was even named `recoverable`, which is the code agreeing that this was meant to be a
conversation.

Two changes: `result.setdefault("next_action", recoverable)` so the advice always travels with the
error, and a specific sentence for the unchanged case — *"the content you proposed is identical to the
file already on disk, so it cannot change what the build reported; name the line you add, remove or
rewrite"* — because the generic one was about JSON shape, which was true and useless here.

| id | Symptom seen in the run | Root cause in code | Fix | Test added | Re-run outcome |
| --- | --- | --- | --- | --- | --- |
| D15 | a fix round proposed the same unchanged file three times and was stopped by the loop guard | `recoverable` was computed for every rejection and delivered only on the stale-read path | `result.setdefault("next_action", recoverable)`, plus an unchanged-file-specific remedy | `test_a_rejection_also_says_what_to_do_next` — asserts the second prompt carries both `next_action` and the identical-file sentence, and that the task still lands | 22 tests green in `ChunkProposalTests` |

The run's own rhythm is worth stating plainly, because it is the point of the exercise: **the model's
mistake was one line, and the tool's mistake was not explaining the mistake.** The first is a property of
a 3 B model on a laptop and is what the review step exists for. The second was a three-line gap in code
this project had already decided to solve.

## D16 — the two create tasks that blocked, and the two messages that made them hopeless

With D12 live, a blocked task now says what was refused. Two consecutive create tasks blocked, and the
reasons turned out to be **two different wrong sentences from the tool**:

```
Create exactly one new file: …common/api/ApiResponse.java
  rejected_action -> Edit 1 in …/web/ServerTimestampFilter.java matches nothing in the file as it is
  rejected_action -> Edit 1 in …/web/ServerTimestampFilter.java matches nothing in the file as it is
  stopped         -> Model repeated the same action without progress

Create exactly two new files. Do not create or change any other file.
  rejected_action -> Edit 1 has an empty search block; it would match anywhere.
  rejected_action -> Edit 1 has an empty search block; it would match anywhere.
  stopped         -> Model repeated the same action without progress
```

**The first is a model failure the tool handled correctly and then failed to explain.** Asked for
`ApiResponse.java`, the model proposed an edit to `ServerTimestampFilter.java` — the file the *previous*
task in the same chat had touched. It is not a typo, it is attention: the conversation carries the last
task's files, and a 3 B model reaches for the most recent one. The engine refused it; the model got "that
edit matches nothing", which is true, and something about JSON shape, which was not the problem, so it
sent the same edit again. There is now a specific observation for this: *that file was never named by
this task; the task named X — propose that file, or block and say why another is needed.*

**The second is the tool complaining about the wrong thing entirely.** `edits` with an empty `search`
against a file that does not exist is the model's way of writing "put this content in a new file". The
engine has the correct rule and the correct sentence for exactly that case — *"A new file needs complete
content; edits only apply to a file that already exists"* — but `_change_form` validated the hunk shape
*before* the path was resolved, so the model was told "it would match anywhere", about nowhere. The
existence check now happens first and the right message wins.

| id | Symptom seen in the run | Root cause in code | Fix | Test added | Re-run outcome |
| --- | --- | --- | --- | --- | --- |
| D16a | a create task spent every turn editing the previous task's file | the refusal said only "matches nothing", so the model had no way back to the asked-for file | a drift-specific `next_action` naming the file the task actually asked for | `test_an_edit_to_a_file_the_task_never_named_is_called_out_to_the_model` | 92 tests green in `test_agent.py` |
| D16b | a create task proposed an empty-search edit and was told it "would match anywhere", twice | `_change_form` checked hunk shape before the path existed, pre-empting the correct new-file rule | resolve the path first; empty search on a missing file falls through to *"needs complete content"* | `test_an_empty_search_on_a_missing_file_gets_the_new_file_answer` — asserts the right sentence and the *absence* of the wrong one | 92 green |

Both are the same lesson as D15, stated once more because it is the run's dominant pattern: **on a small
local model, the quality of the tool's error sentences is the throughput.** A wrong-but-true message
costs the remaining turns of a task, which is six minutes of this machine.

## D17 — a dead link retried itself 33 times and never said so

Restarting the window rotates its token, and the old URL is not merely invalid — it is *silently*
invalid. Opening one gave a page that rendered nothing, and the console held **33 identical
`403 Forbidden` entries**, nearly all of them `/api/events`, one every 1.5 seconds, still going.

The reason is that `EventSource` cannot read the body of a rejected response, so a stale token is
indistinguishable from a dropped network, and `connectEvents()`' one line of error handling —
`setTimeout(() => src.close() || connectEvents(), 1500)` — is correct for a blip and wrong for an
answer. The bootstrap `fetch` *does* surface the reason as a toast, but the stream's retry loop runs
regardless, so the window can be permanently deaf while looking merely slow.

The retry budget is now counted, capped at four, and on giving up the source is closed and the window
says what to do — *"Lost the connection to the agent. Reload this page to open it again."* A stream that
successfully opens resets the budget, so a real blip in a long session still self-heals with the D13
snapshot re-read rather than a message.

| id | Symptom seen in the run | Root cause in code | Fix | Test added | Re-run outcome |
| --- | --- | --- | --- | --- | --- |
| D17 | 33 `/api/events` 403s from one dead tab, page blank, no explanation | unbounded 1.5 s reconnect in `connectEvents()`; `EventSource` can't see a 403 body | a counted budget (`STREAM_RETRY_LIMIT = 4`), `src.close()` on giving up, and one plain sentence naming the fix | 4 cases in `test_webapp.py` (`DeafStreamTests`, plus the reset assertion in `ReconnectedStreamTests`) | 86 webapp tests green, `node --check` clean |

This one is also the run's reminder that **a restart is not free to the user either**: the printed URL is
the only copy of the new token, and every link opened before it is now a deaf tab.

It fired for real an hour later, caused by exactly that: I navigated a tab with a token from the previous
launch. The console recorded one `403` on `/api/bootstrap` and then **four** on `/api/events`, and silence
— `STREAM_RETRY_LIMIT = 4` doing what the lab test said it does, instead of the 33 requests the first
version made. Two days of history say the difference between those two numbers is the difference between
"the page is broken" and "the page told me why".

## D18 — a write that succeeded and a record that didn't, and what caught it

`config-repo/ecommerce-shared.yml` is on disk. The session that wrote it is not marked applied: the file
was written, then the session record's `os.replace` failed with

```
[WinError 5] Access is denied: 'D:\AI\AI-Agent\.agent-runs\0662918b…\session.json'
```

**The cause was the observer, not the tool.** A polling script in another process had that very file
open at that microsecond — the same "watching the run changes the run" hazard that produced the D3 500,
now in a new form. Windows refuses a rename over a destination someone is reading, and an atomic write
has no answer for that except to wait. So two changes:

- `atomic_json` now retries the rename (20 tries, 50 ms apart) and only then raises. A momentary reader
  is normal on Windows — an editor, the search index, an antivirus scan — and it should not be able to
  fail a task mid-apply. 2 cases in `test_agent.py` (`test_a_session_write_waits_out_a_momentary_reader`,
  which asserts the write lands on the third attempt, and `test_the_wait_gives_up_rather_than_hanging_a_job`).
- The run's rule is now: **poll `GET /api/bootstrap` only**, and read `.agent-runs` after `busy=False`.

What is worth recording is that the tool then handled the inconsistent state better than expected. The
Apply dialog did not just offer to re-write the file; it said, in its own words:

> The last task in this folder stopped part way through writing, so what is on disk is not what you
> reviewed.

That is the stale-workspace guard in `apply_proposal` doing precisely its job at a moment when a less
careful design would have silently re-written a file the reviewer had never seen in that state. The
half-applied session is not a hole in the review promise; it is the case the promise was written for.

## D19 — the queue spent two messages and produced nothing

`.gitignore` and `ServerAuditAspect.java` were queued, counted down in the strip, and are **not on disk,
not in `.agent-runs`, and were never mentioned in a status line**. Four rows went in, two sessions ran,
two messages no longer exist anywhere. Nothing in the history says they were ever sent.

The mechanism is in `_drain_queue`:

```python
if item.get("chat") == self.chat_id:
    self.queue.pop(0)                       # spent
    self.start_plan(item.get("text", ""))   # …which may refuse, seven different ways
```

`start_plan` returns early without starting anything when no model is selected, when the folder has
gone, when the message is over 4,000 characters, when cloud consent has not been given, when the attached
plan cannot be read — and each of those is a *refusal*, not a failure of the queue. Popping first turns
any of them into silent deletion of the most expensive thing the user produces. The detached branch had
the same shape, and additionally returned after popping when the branch switch left a job running.

Now the row is removed only when `run_job` reports that it claimed the work. The claim is recorded where
it is known — a flag on `run_job`, read back by the drain — rather than threaded out through seven
`return`s, because a contract that has to be repeated at every exit is one that will be missed.

| Test | Case |
| --- | --- |
| `test_controller.py` | a refused start leaves the row in the queue; a start that takes the row is the one that removes it |

**Honest limit: the specific refusal that fired here is still unidentified.** Two candidates fit the
timeline (both drains happened while an apply or a chained build was mid-flight), and neither can be
confirmed from the session records because the sessions that would have carried it were never created —
which is the defect. What changes now is that the next occurrence is diagnosable rather than silent: the
message stays in the strip, and the status line is the only thing that has to be read.

## D20 — the file an agent cannot write is the one a scaffolding task always asks for

D19's two lost prompts were re-sent after the restart, and both came back **BLOCKED** with the same
recorded reason:

```
{'kind': 'rejected_action', 'reason': 'Only supported text files are accessible.'}
{'kind': 'blocked_retried', 'attempt': 1}
{'kind': 'stopped', 'reason': 'Model repeated the same action without progress…'}
```

Two causes, one of them real and one of them a symptom:

- `.gitignore` has no suffix. `Path(".gitignore").suffix` is `""`, so the gate at `workspace.py:140`
  refused it as surely as it refuses `setup.exe`. A tool whose stated job is scaffolding projects
  could not write the one file that keeps their `target/` directories out of the first commit — and the
  `ecommerce` repo has been showing three `target/` rows as uncommitted work for two milestones
  because of it. The gate is a *text or binary* check wearing a suffix costume.
- The `ServerAuditAspect.java` rejection cannot be explained by the name — `.java` is admitted. What is
  certain is that the sentence the model was given named nothing, so it had no way to tell a path
  problem from a JSON-shape problem, and it repeated the action until the loop guard fired. Five minutes
  of `qwen2.5-coder:3b` per attempt. That half is the same lesson as D11–D16: **on a small model the
  quality of the tool's error sentences is the throughput.**

Three changes, all small:

| Where | Change |
| --- | --- |
| `workspace.py` | `TEXT_NAMES`: a closed allowlist of suffix-less text files (`.gitignore`, `.gitattributes`, `.editorconfig`, `.dockerignore`, `Dockerfile`, `Makefile`, `Jenkinsfile`, `LICENSE`, `README`), case-folded like the suffix gate |
| `workspace.py:153` | the refusal carries the offending name |
| `engine.py` | a name rejection gets its own remedy — *"this tool cannot write that name; propose a file it accepts, or block and name the file"* — instead of the generic *choose an action again, for example in this shape* |

The allowance is deliberately a list rather than "any name without a dot", and it is checked *after* the
guards that run earlier on the path segments: `.env`, `.env.production` and every credential-shaped name
are still refused by `BLOCKED_PARTS`/`SECRET_NAME` before the suffix gate is reached, none of the nine
names is in `AGENT_RULE_FILES`, and `.cursorrules` / `cursorrules` / `windsurfrules` still never reach the
write gate at all — that behaviour is pinned in the same test, so a future widening of `TEXT_NAMES` that
admits a rule file fails loudly.

| Test | Case |
| --- | --- |
| `test_workspace.py` | each admitted name resolves readable *and* writable, deeper (`src/main/docker/Dockerfile`); `setup`, `gradlew`, `sub/x` stay refused; `.env` and `.env.production` refuse as *protected*, not as *unsupported* |
| `test_agent.py` | a refused name records the name in the history and tells the model a name remedy, with the shape advice absent |

Suite: 738 → **740**, green.

**Then it worked.** Re-sent after the restart, the same prompt produced the file in one turn, the Maven
recipe ran (`no tests ran`, exit 0, 13.8 s), and the checkpoint committed it. `git status` in `ecommerce`
went from four untracked rows to one, which is the whole point of the file:

```
target/
.mvn/
*.iml
.idea/
.vscode/
```

Five lines, exactly the five asked for, and no other file touched — the thing the agent is *for*, which
the suffix gate had been silently refusing.

## D21 — an apply that finished and a record that never caught up

The config-repo task `0662918b` is still sitting in `APPLYING`: its file is on disk, correct and
reviewed, and the session says nothing happened after approval. The chain is in `apply_proposal`:

```python
session["state"] = "APPLYING"; atomic_json(path, session)     # lands
ws.write(...)                                                  # lands
event(session, "written", ...); atomic_json(path, session)     # WinError 5 — D18's lock
…
except (OSError, AgentError):
    session["state"] = "PARTIAL_APPLY"
    atomic_json(path, session)                                 # same file, same lock, raises
```

The handler for the failure *is* the failure: the second `atomic_json` threw out of the `except` block,
so the sentence "Apply interrupted… No automatic replay." never ran, the status line showed a bare
`[WinError 5] Access is denied: 'D:\AI\AI-Agent\.agent-runs\…'`, and the session stayed `APPLYING` with
no `error` field. Clicking Apply again then answered *"Approval must match the pending proposal hash."* —
true, and about as useful as the first message. A task that had **succeeded at writing** now looked like a
task that was still running, and the one action that resolves that state (Rollback, which does accept
`APPLYING`) was never offered in words.

| Change | Effect |
| --- | --- |
| The recovery note is best-effort (`try`/`except OSError: pass`) | the user always gets the sentence, never the raw OS error |
| The sentence names the files that landed and why it stopped | "Already written: X. Rollback removes the files named above." |
| `test_agent.py` | patches `atomic_json` to let the opening note through and fail the rest, then asserts the file is on disk, the message names it, and the record is stuck behind it |

Suite: 740 → **741**, green. `0662918b` itself is left as found: the reviewed content is on disk, so the
recovery is a commit through the tool's own git action, not a rollback of a change that is wanted.

## D22 — a repair round that had the right evidence and the wrong hole in the edit contract

`JwtService.java` came back with one compile error, and the error was mine: the prompt dictated
`import java.time.ChronoUnit;`. `ChronoUnit` is in `java.time.temporal`. The agent wrote the import I
named, byte for byte, and Maven said no — which is the correct behaviour for a tool and the wrong
behaviour for a spec.

The Run & fix round then blocked after three rejections. Before blaming the model, the evidence it was
given was checked in the session record, because the natural suspicion was that the extractor had
stripped the useful lines:

```
"failures": ["…JwtService.java:[9,17] cannot find symbol",
             "[ERROR]   symbol:   class ChronoUnit",
             "[ERROR]   location: package java.time", …]
```

Those lines **are** forwarded (`repair.evidence()` keeps `failures` plus the tail, 12 k capped;
`evidence_attached: 3384 characters` on this run). So the model was told the symbol and the package it
was missing, and still could not turn that into an edit. What it did instead is the finding:

| Turn | Proposal | Refusal the model saw |
| --- | --- | --- |
| 1 | the file, byte-identical to what it had just read | "Proposal contains an unchanged file" — with D15's remedy, which worked |
| 2 | `{"search": "", "replace": "import …"}` | "Edit 1 has an empty search block; it would match anywhere." |
| 3 | the same empty anchor | the same sentence → loop guard fired |

The refusal is true and it is the *only* hole in the edit contract — a hunk must quote text that exists —
and a 3 B model reaching for "insert a line" falls into it because the natural mental model of an edit is
the position, not the anchor. The sentence said what was invalid; it did not say what a valid insert looks
like. Now it does:

> An edit has to quote text that is already in the file. To add a line, search for the existing line it
> belongs next to and replace that line with itself plus yours; to change a line, search for that line
> exactly as the file shows it, indentation included.

| Test | Case |
| --- | --- |
| `test_agent.py` | an empty-anchor proposal is refused, the next prompt carries the anchor lesson, and a correctly anchored insert is accepted |

Two notes for whoever writes the next prompt in this doc:

- **The operator's prompts are part of the system under test.** A dictated API detail that is wrong gets
  written faithfully and costs one full build cycle to discover. Where a prompt names an import, a
  coordinate or a package, it is the prompt that should be reviewed first when the build disagrees.
- Fixture gotcha, hit twice now: on Windows `Path.write_text("a\n")` stores `a\r\n`, so an edit hunk that
  quotes `"a\n"` matches nothing — and the tool's answer ("quote the current text exactly") is correct
  while being useless to the test author. Pin such fixtures with `write_bytes`.

## D23 — "invalid fields" is true of eight mistakes and fixes none of them

The retry of that fix (`0c197200`) got a *narrower* prompt — one named line, the search block spelled out
in words — and died in three turns with the friendliest failure in the file:

```
{"kind": "rejected_action", "reason": "Unknown action or invalid fields. Allowed: list_files, read_file, search_code, propose, blocked."}
{"kind": "rejected_action", "reason": "Unknown action or invalid fields. Allowed: …"}
{"kind": "stopped", "reason": "Model repeated the same action without progress…"}
```

Two copies of a refusal, a third copy stopped by the repeat guard, and — this is the part that matters —
**nowhere in the record is what the model actually sent.** Raw replies are not persisted, by design: they
carry project content and can carry secrets, and the session file is a document a user reads afterwards.
So the operator reviewing history sees that the tool refused something, and the model that was refused was
told only that its fields were invalid. `next_action` did append a correct example shape; the model sent
the same object again. Neither side could see the one difference that mattered.

| Change | Effect |
| --- | --- |
| `action_shape(action)` | renders an action as `action propose with fields action, changes, checks, path, summary` — names only, no values, so the no-raw-output rule is kept and the refusal becomes diffable against the allowed shape |
| the shape refusal quotes it | the model is told which of *its* keys are wrong, not just which actions exist |
| the repeat guard quotes the last refusal | a task that dies on three identical copies now says what each copy was refused for, in the status line and in `session["error"]` |

The field names of a rejected action also land in `rejected_action.reason`, which is the row D12 added for
exactly this purpose: a blocked task that explains itself to whoever opens the history a day later.

| Test | Case |
| --- | --- |
| `test_agent.py` | a `propose` carrying a stray `path` is refused by name, and the stop line after the third copy repeats the refusal it was |

**How the file was finally fixed** is the honest part of this entry: not by a better repair round, but by a
prompt that avoided the hole entirely — *"send the whole file back as complete content with exactly one
difference"*. That model wrote `JwtService.java` correctly from a spec in one turn; it cannot reliably
hunk-edit one line of it. A round is ~6 minutes, so knowing which of the two the task needs is worth more
than any cleverness in the loop. Add to the notes list:

13. **Ask for a rewrite, not an edit, when the file is small.** Complete content is the shape a small
    model handles; `edits` is the shape that lets it fail silently. Say which one to use in the prompt, and
    say *"do not use edits"* when it matters.

## D24 — a one-line fix that silently deleted the file's first two lines

`ApiResponse.java` had been written by the agent as a **package-private** class: the prompt never said
`public`, the model never said it back, and nothing in the reactor used the type yet, so it compiled for
two milestones. Making it public was a one-line task. The prompt was as explicit as it knows how to be —
*"Every other line stays character for character as it is"* — and the file that came back started here:

```java
public class ApiResponse<T> {
    private T data;
```

The `package com.ecommerce.common.api;` line and `import java.time.OffsetDateTime;` were gone. The task was
approved by Auto-Apply, committed (`13d633b`), and the build went red one module away from where it looked.

What the tool checked, and what it could not: `shape_mismatch` asks whether a `.java` file has *a*
declaration in it, and this file still had a class line, so it passed. `shrink_warning` counts removed
*lines* against a mass-removal threshold, and 2 of 35 is nothing. The write gate is a text-and-syntax gate,
and a Java file without a package line is perfectly legal syntax — it is a file in the default package,
which is exactly the thing that cannot be imported. Only javac knows, and javac is six minutes away.

So the gate now owns the one Java fact that a *rewrite* has no business changing:

> `prepare_changes`: if the file on disk had a `package` line and the proposal does not, refuse — *"A
> rewrite of a Java file keeps the package statement and the imports it already had; send the current
> first lines unchanged."*

Two edges were checked before writing it, because both are legitimate and must not be blocked: **moving**
a class between packages is allowed (the line is still there, only different), and a **new** file with no
package line is allowed (there is no `before` to compare against). The rule is about disappearance, not
about absence.

| Test | Case |
| --- | --- |
| `test_agent.py` | the header-stripping rewrite is refused and nothing is written; a package *change* passes; a fresh default-package file passes |

And the honest caveat about ordering, since this entry reads like a story: the file was damaged **before**
this gate existed, so the gate could not have stopped it — the repair prompt had to restore the header by
hand, one more task and one more build. What the gate buys is the next six minutes.

**Prompt lesson for the operator, same shape as D22's:** a sentence like *"keep every other line as it is"*
is aimed at the model's attention, and a 3 B model under a rewrite task is rewriting, not preserving. The
reliable form is to name the lines that must survive by content: *"the file must begin with `package X;`
then a blank line then `import Y;`"*. That prompt worked in one turn.

## D25 — a file of imports and nothing else, and nine errors in the wrong file

`UserRepository.java` came back 167 characters long: `package`, three imports, no interface. The model's own
proposal summary said *"Create UserRepository interface"*, so the intent was there and the body was not.
Auto-Apply wrote it (a new file has nothing to compare against), the checkpoint committed it, and the
reactor answered with **nine compile errors — every one of them in `AuthService.java`, the file that
referenced the missing type.** Six minutes of build to say "the file you sent is empty", and the sentence
points at the innocent file.

No existing gate could see it: `shape_mismatch` asks for *a* Java declaration keyword and `import` is one,
and D24's package rule needs a `before` to compare with. So the write gate now asks the one question that
does not need a compiler:

> `prepare_changes`: a `.java` file whose content declares no `class`, `interface`, `enum` or `record` is
> refused — *"A file of package and import lines alone compiles to nothing: send the whole class,
> interface, enum or record, with its closing brace."* `package-info.java` is excluded, being the one file
> that legitimately declares no type.

| Test | Case |
| --- | --- |
| `test_agent.py` | the import-only body is refused; the same imports **plus** the interface pass; `package-info.java` passes |

Three side-findings from the same ten minutes, all of them about reading the tool rather than its code:

- **A pending "Keep going?" holds the queue, and the strip says nothing about it.** Two rows sat in the
  strip with `held: False`, `elsewhere: 0`, `busy: False` — the signature of D19's stall — and the whole
  explanation was an unanswered modal. That is the intended rule (a fix round belongs to the task ahead of
  it), but a strip that reads "runs when the current task ends" beside a question that will not expire for
  30 minutes is not a sentence about the moment. Candidate for the next round: the strip's note should
  change while an ask is open.
- **`snapshot()["pending"]` is a question or a proposal, never a status line** — and it read `null` while
  two questions were open, because the *asked* state lives in `_replies`, not in the snapshot. The
  misreading cost a wrong "the queue is stalled" conclusion; the field that would answer it is the one
  nobody looks at (`/api/bootstrap` has no "how many asks are open" count). Noted rather than changed:
  the client already knows, because it is the one drawing the sheet.
- **D21's late-reply guard was exercised for real**: a click-loop pressed *Cancel* six times on a sheet
  whose removal is asynchronous, and every duplicate reply was dropped without a trace, with the queue
  resuming on the first answer. Six minutes of `qwen2.5-coder:3b` were saved by a rule written to stop
  nine orphaned dialogs.

## D26 — the repair that undid a repair, and the number that makes it visible

D24's damage was repaired by a task that restored the `package` line and — in the same two minutes —
dropped `import java.time.OffsetDateTime;`. Then the *next* repair added the `public` modifier and dropped
the same import again. Each of those tasks got a green checkpoint commit and a red reactor build, because
the promise in the prompt was the kind a small model cannot keep:

> "Keep every other line character for character as it is."

The tool has no way to hold that promise, and the file it was written into is 35 lines long — a whole-file
rewrite of a 35-line file looks identical to a one-line fix in every place the user looks. Except one: the
proposal card's file chip already carried `+3 −5`, and the diff tab shows the rest. The row that a user
actually reads — the Auto-Apply notice — said only "saved 1 file(s)".

So the notice now says what changed, in lines:

```
⚡ Auto-Apply saved 1 file(s) directly to the project, because this folder's switch is on.
🗒 Task summary: Make ApiResponse public so auth-service can use it
🗓 3 line(s) changed in a file of 33.
```

and when the rewrite replaced every line the file had, that line reads *"3 line(s) changed — every one of
the 33 lines the file already had was replaced, not only the lines the task named."* The count ignores
whitespace, for the same reason `shrink_warning` does: a Maven-touched file that was only re-indented is
not damaged, and a warning that fires on it stops being read.

| Change | Case |
| --- | --- |
| `engine.diff_size(changes)` | (lines changed, lines the files had, was all of them replaced) — 1 case, covering unchanged, one-line, swept, new-file, empty and re-indented |
| `labels.write_notice(..., lines, total, rewrote)` | three sentences, both languages — 2 cases |

What the tool got *right* in the same minute is worth recording, because the temptation is to keep adding
gates: the file chip counted `+3/−5`; Auto-Apply refused to keep writing after a task left the folder with
a failed verification (`repair.must_ask` saw the unverified prior and stopped asking the folder to write
itself); the failed build produced a fix offer that named its own rules. None of those needed new code.
The one thing none of them did was put the size of the change in the sentence that is read without
clicking anything — which is what this adds.

**Prompt pattern that does work, measured four times:** a numbered block of the lines that must exist.

```
The file must begin with exactly these four lines, in this order, and nothing may come before them:
line 1: package com.ecommerce.common.api;
line 2: an empty line
line 3: import java.time.OffsetDateTime;
line 4: an empty line
```

Compare with "keep every other line as it is", which the same model answered by deleting an import. The
first names positions and contents; the second names an abstraction (*other lines*) and asks a 3 B model
to hold a 35-line file in its head while editing it.

**One gap deliberately not gated.** `AuthDtos.java` arrived as a file whose only type had an *empty body* —
`public final class AuthDtos { }` — which is how a whole nested-record payload disappears from a create
task. D25's rule cannot see it (a type is declared), and `shrink_warning` cannot either (a new file has no
`before`, and that comparison is the whole rule). A rule like "refuse a new `.java` type with no members"
was written and deleted: `@Configuration`/`@EnableWebSecurity` classes with empty bodies are real Spring
idioms, and a scaffolding tool that refuses them trades this incident for a class of tasks it cannot do at
all. What is built instead is the number in the notice (D26) and the diff behind it, so the empty body is
one click from the row that says why it should not be there. Open item, with the reason it stayed open.

## D27 — the previous task's code walked into this task's file

`AuthController.java` was asked for two one-line methods that delegate to `AuthService`. What arrived:

```java
if (authService.findByUsername(request.username()).isPresent()) {        // AuthService has no such method
    throw new ResponseStatusException(HttpStatus.CONFLICT, "username taken");
}
User user = new User(request.username(), authService.encodePassword(…)); // `User`, `repository`,
repository.save(user);                                                   //  `jwtService`, `List`
return new AuthDtos.AuthResponse(jwtService.generate(…));                // none of them in scope
```

Every invented line is text from the **two prompts earlier in the same chat** — `AuthService`'s duplicate
check, its `repository.save`, its `jwtService.generate`. This is D16's drift, in content rather than in
path: the model was not confused about *which file* to write, only about *what code belongs in it*, and
the source it reached for was the nearest previous answer.

Nothing in the tool can gate that without a Java compiler: the file is well-formed, declares a type, keeps
its package, and every rejection rule it has would say "fine". The build is the only judge, and it was
right. What is actionable is the cause, and it is on our side of the boundary:

- `plan()` puts the chat's previous turns into every new request (`chat_context`, capped at
  `settings.context_chars // 6`), which is what makes a follow-up like "now do the same for product" work.
- For a *copy* task — where the prompt already contains the exact text wanted — that context is pure
  contamination risk, and this run shows it firing.
- Both prompts in this run that produced clean files (`AuthDtos`, `SecurityConfig`, the two repair that
  followed) were the ones that said **"containing exactly this text and nothing else"**. That sentence is
  doing work; keep it in every scaffolding prompt.

Candidate for the next round, not built here: a per-task toggle for chat context ("answer this on its own"
in the composer), so a copy task does not inherit the last two answers. It is one boolean through
`start_plan` → `plan()` and a chip on the composer, and the run now has the measurement to justify it.

## D28 — after a refusal, the tree it left behind cannot be built

The gateway task blocked (`9e69f1fc`: two rejected shapes, then two repeats, then stop). The four files
the previous tasks wrote are on disk and unverified, and the window's Run control is grey:

```
canRun = not busy and session.state in MUTABLE_STATES and bool(recipes)
MUTABLE_STATES = {APPLIED_UNVERIFIED, VERIFICATION_BLOCKED, VERIFICATION_FAILED, CHECKS_PASSED}
```

A blocked task is not in that set, so `self.session` — the *refused* session — is what the gate reads,
and verification belongs to nothing. The only way to build the tree is to make the tool write something
else first, which is a different act than checking what is already there.

Invisible while work is flowing, because the operator's next move is "send another task" anyway; it
bites the moment you want to stop and check. Not fixed in this round: the run was mid-milestone and the
workaround (send the next task, then Run) cost one task, not a wrong claim. The fix is small — the gate
has to ask whether *this project* has files, not whether *this session* has a mutable state — and it
needs the test that a blocked task leaves Run alive.

## D29 — the history kept a second copy of a step the thread had already collapsed

`AuthController.java` again. The activity log for that task:

```
11:08  progress  📖 Reading file: …/AuthController.java
11:08  progress  📖 Reading file: …/AuthController.java
11:10  progress  ✍️ Proposed changes for 1 file(s): …/AuthController.java
11:10  progress  ✍️ Proposed changes for 1 file(s): …/AuthController.java
```

while the session's own event list holds exactly one `tool` and one `proposal`. So the model really did
read the file twice in one turn — and the second read produced a stored row even though the chat thread
correctly showed one.

The cause was one method call further along than the first diagnosis, and the wrong half was shipped
before the right half replaced it. `_step()` was not the whole story: `engine.plan` announces every tool
action **on both callbacks with the same line** —

```python
    def announce(action: str, **fields) -> None:
        line = labels.step_line(arabic, action, **fields)
        progress(line)          # → _progress → _note("progress") → one log row
        if step is not None:
            step(line)          # → _step → _add("tool") → and, until now, _progress again
```

so the log gained a row from the announcement *and* a row from the step, for every single tool call,
whether or not it repeated. The repeat path was a second, smaller version of the same collision, which is
what made it look like a dedupe bug — and the first patch fixed only that, so the next task showed the
duplicates again, live, in the window the patch had been restarted into. Measuring the premise before
coding it is the standing rule in this project; the premise here came from two rows and a plausible
branch, and the cost was one wrong patch and one server restart.

Fixed properly: `_step()` adds the conversation row and moves the status, and records no log row of its
own — the announcement owns the history. `test_one_tool_action_leaves_one_log_row` asserts both halves:
three announcements (one of them a repeat) leave three log rows and two conversation rows. **751 offline
tests** green, `node --check` clean.

## D30 — with a red build waiting at every task, an Auto-Apply batch stops for a human at every task

Ten scaffolding writes queued at once. With the recipe selected, each one ended:

```
apply → mvn -B test → FAILED (18-21 s) → "Keep going? Fix round 1 of 3" → the queue does not move
```

`held=False, items=10, busy=False` is the signature note 11 already describes, and the strip says so
honestly — `when: "runs when you answer the question on the screen"`. What is new is the shape of the
batch: during scaffolding the build is *supposed* to be red until the last module file lands, so the
question is not a decision but a reflex, raised once per task, ten times, for a failure that is expected
and will be answered "no" every time. Measured: 2.5 min/task with no build, ~5 min/task with one.

The switch exists; it just isn't one. `_applied` runs the recipe only `if not self._auto_fix and
self.selected_recipe()` (`controller.py:1607`), and Settings → run command can be cleared
(`set_recipe("")`), which is what this run did: the remaining writes landed unchecked, then one
`mvn -B test` verified the whole reactor. That is the right order for scaffolding, and nothing in the UI
says so.

Candidate, not built: a third button on the fix offer — "not again for this batch" — which is the same
object the window already has, scoped to the queue rather than the question.

## D31 — a task cannot delete a file, so a design change that removes a class has no path

The plan had one shared `JwtSecurityConfig` in `ecommerce-common-lib`, and `auth-service`'s own
`SecurityConfig` retired in front of it. The tool can create a file and edit a file. Removal exists only
inside `rollback()`, and only for a file that session created (`engine.py:934`, `before is None` →
`unlink`). Rolling back the session that wrote `SecurityConfig.java` would delete it and put that
session in `ROLLED_BACK`, while its checkpoint commit still sits in the history pointing at a file that
is no longer on disk — a truthful record of an untruthful tree.

So the app ships three near-duplicate security configs. That is a design cost paid from a tool gap, and
the gap is what matters here: `repair.removal_notice` already has the vocabulary for announcing that a
file goes away, and the change form has `before_hash` for every write. A `delete` entry — path plus
`before_hash`, reviewed as "removes this file", no more privileged than a write — is the missing form.

## D32 — "send it back whole" is not a field name, and the model answered an edit

`AuthController.java` was still the D27 file after the task that was supposed to replace it. The prompt
said *"send it back whole as complete content, exactly this text and nothing else"*. The commit it
produced changed **two lines**:

```
$ git show --stat de6ed39
 auth-service/.../AuthController.java | 4 ++--
 1 file changed, 2 insertions(+), 2 deletions(-)
```

The model returned `edits`, and the edits it returned were the ones it liked: `"username taken"` became
`"username already exists"`, and `new User(…)` grew two `null` arguments. Both legal — `PROPOSE_SHAPE`
offers the model `content` *or* `edits` and the engine accepts whichever arrives, because the two forms
are equally valid ways to change a file. The words "whole file" in the prompt have no counterpart in the
contract, so nothing could check them. The build said what it said: eleven errors, the same eleven.

Re-sent as `{"path","content"}` — naming the field instead of the feeling — the model did send content,
and content equal to the file it had just read:

```
09:41:31 rejected_action  Proposal contains an unchanged file: …/AuthController.java
09:43:21 rejected_action  Proposal contains an unchanged file: …/AuthController.java
09:45:13 stopped          Model repeated the same action without progress: every copy was refused with …
```

That refusal is correct and the repeat guard worked exactly as designed (D23's echo is what named the
reason on the third copy). What is interesting is *why* it echoed: the prompt contained the text to
write, the read contained the text that is there, and a 3 B model handed back the nearer of the two.

Three things come out of this, and only the first is a tool change:

1. **Operator vocabulary.** The contract's words are `content` and `edits`. A prompt that says *"send it
   back whole as complete content"* is asking a feeling; a prompt that says *`changes` must hold one
   entry with `path` and `content` — not edits* names the field, and the model can only obey one of the
   two. Every copy prompt in the ledger after this point uses the field name.
2. **A file the model will not rewrite is a file the operator should split.** The run finished this file
   as two anchored edit tasks (bodies, then imports and constructor), the shape that had landed reliably
   all day. Recorded here because the temptation in the moment was to write the file by hand, and that is
   the one move this exercise forbids.
3. **The tool cannot tell an unintended echo from an intended no-op** — and should not try. "Proposal
   contains an unchanged file" is the right sentence for both, and the guard that ends the task after
   three copies is the right cost. What was missing was not a gate but a hint: an unchanged `content`
   proposal *after a task that supplied text* is nearly always the model copying what it read, and the
   refusal could say "the file already says that — write the version your task asked for". Candidate for
   the next round, next to D16's drift remedies.

## D33 — a question the window missed cannot be answered at all

`_ask()` mints `request_id = secrets.token_hex(8)`, emits `{"kind":"confirm","id":…}` over SSE and blocks
on an `Event.wait(timeout=1800)`. Nothing stores that id: the snapshot has no field for it — `pending` is
the *progress line*, not the question — and `/api/events` replays nothing on connect. So a window that was
not attached at the moment the question was raised can never see it, and the queue behind it waits out
the full 30 minutes with `busy=False`, `held=False`, `items>0`.

This run hit it for real: the server was restarted to load a fix, the browser was still on the dead
token's URL (D17 had closed its stream, correctly), and a red build asked its "Keep going? Fix round 1 of
3" into nobody. The only ways out were to wait 30 minutes or restart again — and the restart drops the
queue, because the queue is not persisted either.

`_replies` is right there in the controller. The fix is one field in the snapshot, `asks: [{"id", "kind",
"title", "message", "ok_label"}]`, and the client drawing them in `render()` the same way it draws a
streamed `confirm` — the code path already exists, it just has no re-entry. Until then the operator rule
is: **attach a window before you let a batch run**, because the batch will ask.

## D34 — an anchored edit is only as anchored as the line it searched for

Two ways this run watched a correctly-matched edit land in the wrong place, both on a pom, both after
D11's syntax gate had nothing to say:

- asked to insert before `<!-- com.ecommerce:ecommerce-common-lib:1.0.0 -->` it removed the
  `<dependency>` element that followed the comment along with the line it searched for, and left the
  comment twice — valid XML, silently no dependency, and the reactor answered three modules later with
  `package com.ecommerce.common.security does not exist`.
- re-asked with a single-line search (`    <dependencies>`) it reproduced the nine replacement lines but
  **reordered** them: the dependency block above the `<dependencies>` tag, the tag itself de-indented.
  That one the reactor caught in 3.8 s with `Malformed POM … Unrecognised tag: 'dependency'`.

The engine's guarantees are about the text: the search must exist, must match once, must be current. None
of them can express *"the replacement keeps the element its parent encloses"*, and a gate that tried would
be a Maven parser inside the agent. So the useful response is the one the tool already has: the second
pom came back from `Rollback changes` — the UI action built in UI 3.5, used here for the first time on a
write the tool itself regretted, answering a question the plan had only claimed it could answer:

```
13:23  written           product-service/pom.xml        (the reordered block)
13:23  run               maven-test failed, exit 1, 3.8 s
13:27  rolled_back_file  product-service/pom.xml
13:27  rolled_back
```

The ask between them ("Restore this task's files to their previous contents?") is in none of those rows —
that is D33, and it is why the operator had the window open when they clicked Roll back.

The checkpoint commit of the bad write stayed in the history (correct — it is what git did), and the tree
went back to the previous contents, which is what the operator wanted. The retry used an anchor whose
search line is unique *and* whose replacement is a pure insertion in front of it, which is the shape a
3 B model cannot reorder: nine lines, the searched comment last.

Best practice, measured twice: **anchor an insertion on the line the new block must end up in front of,
and say which line of the replacement is the anchor.** Do not anchor on a container tag.

## D35 — the advice "send complete content" is unreachable for a file bigger than the task limit

`.xml` refusal, `engine.py:438`:

```
Invalid XML in product-service/pom.xml: mismatched tag: line 29, column 10.
Put actual complete file text directly in content.
```

The gate is right (the malformed write never reached the disk — this is D11's XML check paying for
itself, twice, on the same file) and the advice is right in general. It is impossible here: the operator's
only channel for "the complete file text" is the task, and `plan()` refuses a task over 4 000 characters
(`engine.py:511`). `product-service/pom.xml` is 3 019 characters once its comments are removed and the
missing dependency is added, so the prompt that carries it fits — but `auth-service/pom.xml` at 4 338
characters never can, and a file that size is exactly the kind that gets mangled by an edit.

The model did what the sentence said, then repeated it, and the guard ended the task:

```
10:31:53 rejected_action  Invalid XML … Put actual complete file text directly in content.
10:33:25 rejected_action  Invalid XML … (same)
10:34:27 stopped          Model repeated the same action without progress: every copy was refused with …
```

Two things follow, and neither is a gate. The refusal should say what the *operator* can still do when the
suggested route is closed — "or make it a narrower edit anchored on a line unique to this file" — because
right now a correct sentence silently points at a wall. And the size limit should be one number in one
place: the task cap, the queue's own 4 000-character refusal (`controller.py:424`) and this advice are
three copies of the same fact, and only two of them say it out loud.

Recorded with the outcome: the retry that finally worked was a whole-file `content` copy of the 3 019-character
pom, which fits; the run's budget note becomes **copy tasks top out at about 3.3 k of prompt**, and a
bigger file has to be narrowed instead.

## D36 — a two-file task came back with one file, and the tool told the truth about it

`p2` asked for two new files in `product-service` — the `Product` entity and the `ProductRepository` that
uses it. What landed, nine modules' errors later:

```
$ git show --stat f7b8dce
 .../com/ecommerce/product/repository/ProductRepository.java | 11 +++++++++++
 1 file changed, 11 insertions(+)
```

The thread said `✍️ Proposed changes for 1 file(s): product-service/.../ProductRepository.java` and the
Auto-Apply card said *1 file(s) saved*, and both were exactly right. Nothing lied; the count simply
described the proposal instead of the request, and no one reading a batch of thirteen tasks compares two
numbers in different places. The same task shape had worked for `c2` (customer entity + repository) an
hour earlier, which is how the gap stayed invisible until `package com.ecommerce.product.entity does not
exist`.

The tool cannot fix this by gate: knowing that a task named two files means parsing English prose, and a
check that guessed at the count would refuse more good work than bad. Two honest options are on the list
for the next round — the review card comparing "files in this proposal" against the paths the task text
mentions, as a *question* rather than a refusal, and a batch summary at the end of a queued run listing
each task and the paths it wrote. What ships today is the operator rule (#16): after any multi-file task,
count the files.

## D37 — a proposal that forgot its prose was refused as if it had forgotten its code

`d4` carried a 3 019-character pom. The model sent the file correctly and dropped `summary` and `checks`:

```
10:41:59 rejected_action  Unknown action or invalid fields: action propose with fields action, changes.
                          Allowed: list_files, read_file, search_code, propose, blocked.
                          propose takes exactly action, summary, checks, changes.
10:45:23 rejected_action  (same)
10:48:53 stopped          Model repeated the same action without progress: every copy was refused with …
```

D23's echo did its job — it told the model the field names it had actually sent — and the model, having
spent its care on the file, sent the same short reply again. Twelve minutes for two sentences that no
code executes: `summary` is shown to a human, `checks` is a list of descriptions.

Shipped: `propose` now accepts the two prose fields being *absent* and fills them (`engine.NO_SUMMARY`,
`engine.DEFAULT_CHECKS` — the second is the string the block-chosen path already used, now shared rather
than copied). A field of the wrong type, or a 4 001-character summary, is still refused: filling what is
missing and accepting what is broken are different things, and the second test says so. Strictness should
be proportional to what a field does, and this gate was enforcing a style rule with a six-minute fine.
Two cases: `test_a_proposal_that_forgets_its_prose_is_still_a_proposal`,
`test_a_summary_of_the_wrong_kind_is_still_refused`.

## D38 — the tool can prove a build and cannot prove an application

`runner.RECIPES` is a closed allowlist of constant argv lists — `mvn -B test`, `mvn -B -DskipTests
compile`, gradle, pytest, and the rest — chosen because a build command executes code the repository
defines and that is already the largest blast radius worth granting. It has consequences for a
microservice project that only show up at the end:

- nothing in the tool can bind a port, wait for Eureka, or issue the one HTTP request that would have
  proved `admin`/`admin` logs in. The run's `mvn -B test` is exit 0 and the app is, in the tool's own words,
  unverified: `tests_observed=False`, state `VERIFICATION_BLOCKED`, sentence "no tests ran".
- there is no `clean` recipe, so a green build after a red one is incremental. The reactor here really did
  recompile everything (the failures were compile errors in changed modules, and success arrived only when
  the last one was repaired), but the operator cannot *see* that from `mvn -B test`'s output alone.
- the two things a service actually needs proving — a request reaching its controller, and a header being
  set — are exactly what the `checks` list describes in prose and nothing executes.

The honest position is not "add a run recipe": starting a long-lived process inside a job the window treats
as finite is a different machine than a command that exits. It is that **the tool's verification story ends
at the compiler for an application like this**, and everything after it belongs to the operator — which is
why this file now carries the start order and the smoke list instead of a green tick.

## Checkpoints the agent made in `ecommerce`

Final count, from the repository the agent wrote into:

```
$ git rev-list --count HEAD          → 70
$ git log --oneline | grep -c agent: → 69        # every one written by _checkpoint()
$ git status --short                 → (empty)
```

`a1e5a01` is the only commit that is not the agent's: the agreed empty base that created the repository.
The other 69 are one per task that touched a file, each carrying its `[session-…]` id, and the working tree
has been clean at every idle moment of the run — including after D34's rollback, where the bad write's
commit stayed in the history and the tree went back to the previous contents. The first and last of them:

```
7f44de1 agent: Create exactly one new file: pom.xml at the project root …   [session-4cced7cc0378]
…
8f5fc9f agent: Create exactly one new file: product-service/…/Product.java … [session-3936bee02210]
8330042 agent: Fix exactly one file: auth-service/…/db.changelog-master.xml… [session-71f51c7746d3]
```

Eighty-nine sessions, sixty-nine commits: the difference is the tasks that blocked, the ones that were
refused a shape, and the one that was rolled back — all of them recorded in `.agent-runs` and all of them
rendered verbatim in `docs/DOGFOOD-PROMPTS.md`.

## Best-practice notes for running the agent on a large multi-module build

Written from what happened, not from intention. Each one is a measurement in this file.

1. **One file per prompt, and under about 2.6 k characters.** A single-file pom took one model turn and
   about six minutes; the same model given two files took three turns and about fifteen. A second file is
   a second thing a 3 B model loses track of, and the engine pays a full turn for each attempt. The size
   limit is measured, not guessed: a 3.5 k two-file copy prompt blocked with two rejected shapes and two
   repeats (D23 fired on it), and the same content split into 1.4 k and 1.7 k tasks each landed in two
   turns. Copy work has a byte budget, and it belongs to the operator, not to the tool.
2. **Do not ask for a build before the reactor is structurally complete.** An aggregator naming absent
   modules fails with `Child module … does not exist`, and the tool dutifully reports that failure after
   every single write. M1's exit criterion was wrong in the plan and is fixed there.
3. **Verify every version against the registry before it enters a prompt.** `3.5.16`, `2025.0.3` and
   `0.12.7` were read from Maven Central metadata. A prompt that invents a version measures the prompt,
   not the agent — and the model will faithfully write whatever number it is handed.
4. **Name the parent block in full, every time.** `<relativePath>../pom.xml</relativePath>` was asked for
   in each prompt and appeared in each file. A multi-module Maven build has no room for "obvious".
5. **Give the dependency list as an ordered, numbered list with explicit scopes.** The model honoured
   every scope across four poms; the only deviation was reordering one entry.
6. **Do not run the tool's test suite while a milestone is in flight.** See the observer-effect note
   above — it is the most likely source of the run's first 500.
7. **Expect the honest context to be slow, and set the timeout before the first prompt.** The default
   300 s is sized for a truncated request. Raising it is a Settings change, not a code change.
8. **Let the tool commit.** Every write here produced its own `agent: … [session-…]` checkpoint, and the
   working tree came back clean each time. That is what makes a wrong milestone cheap.
9. **A Send while a task runs is a Queue, not a submission.** The button changes label to "Queue" and
   `submit()` routes to `queue_add`, so a prompt believed submitted may only be waiting. The tell is a
   session that never appears in `.agent-runs` for a prompt that clearly went out.
10. **One broken file poisons every later task, because Auto-Apply runs the command after each write**
    (`controller.py:1607`). While `EurekaServerApplication.java` held a pom, the config-server task also
    "failed" on the same eleven-second build and asked the same question. Fix the broken file **first**:
    the queue row's ▶ "Run this one next" re-prioritises without losing anything.
    *Corrected in M5:* there is a switch, it just isn't labelled as one — clear the run command in
    Settings (`set_recipe("")`) and `_applied` skips `run_tests`, so a scaffolding batch writes without
    building and one command verifies the whole reactor afterwards. That is D30.
11. **Answer the fix offer, don't let it age.** An unanswered question blocks the worker that asked it
    for the whole 30-minute wait, and the queue waits behind that. The window says `busy=False` the whole
    time, so it reads as idle; `held=False, items>0, busy=False` is the signature of a stalled queue.
    Since D9 the modal is at least withdrawn when it expires rather than left on screen.
12. **A prompt that names the mistake beats the build output as evidence.** Re-asking the model to fix
    "the command failed, see this output" blocked after five turns (D12); the same file rewritten by a
    prompt that says *the file holds a Maven pom, replace the whole content with Java* is a different
    request, and the guard in D11 makes the first write impossible.
13. **The queue is the batch API, and `/api/action` is the same window.** `{"type":"send"}` starts a task
    and `{"type":"queue_add"}` appends the rest, all carrying `?t=<launch token>` — the controller does the
    same work it does for a click, so the chat, the sessions and every checkpoint land identically. The
    nineteen prompts of M5–M8 went out from one script instead of nineteen round trips through the
    composer, and the window the operator opens afterwards shows all of it. Two things it does not
    survive: a server restart drops the queue (it is not in the saved state), and a question raised while
    no window is attached can never be answered (D33).
14. **Check a property namespace against the jar that is actually on the classpath.** Spring Cloud Gateway
    4.3.5 — what `2025.0.3` resolves — marks `spring.cloud.gateway.routes` deprecated in its own
    `spring-configuration-metadata.json` and defines `spring.cloud.gateway.server.webflux.routes`. The
    prompt was written from the metadata, not from memory of 3.x. Same trick as note 3, one level deeper:
    a version is not a configuration shape.
15. **Every file the tool writes needs its dependency stated in *that* module** when the shared library
    declares its starters `provided`. `ecommerce-common-lib` compiles against web/aop/security and hands
    its services nothing but its own classes, so scanning `com.ecommerce.common` into a service that has
    no `spring-boot-starter-aop` gives a green compile and a startup `NoClassDefFoundError` on
    `ServerAuditAspect`. The build cannot see this one; only the boot does. Caught here by reading the
    poms, and recorded as a prompt-side bug, not a tool bug.
16. **Count the files after a multi-file task.** D36: a task that asked for two got one, the notice said
    `1 file(s)` truthfully, and the missing entity surfaced nine errors later in a different module. The
    thread's `✍️ Proposed changes for N file(s)` line is the number to compare with the number in the task.
17. **Say "the summary is X and the checks are Y" out loud in an edit prompt.** On a copy task the model
    spends its care on the file and drops the prose fields first (D37 — now filled, not refused, but a
    named summary still arrives named). It also stops the shape refusal from being the thing the model
    repeats.
18. **Anchor an insertion on the line the new block must sit in front of, never on a container tag.**
    `    <dependencies>` is a real, unique, current line and the model reordered the nine lines it was
    given around it (D34). The comment above a dependency is a better anchor: it is unique, it is a leaf,
    and the replacement is "new block, then that same line".
19. **A hash or a secret in a prompt is a claim you authored.** The seeded `admin` password went in as a
    BCrypt string copied out of a prompt, one character short, and nothing in the tool or the build could
    know: the XML parses, the column is a VARCHAR, and login would simply have returned 401. Measured in a
    scratch program against `spring-security-crypto` (60 characters, `checkpw("admin", …) == true`) and
    fixed by one edit task. Same family as the `java.time.ChronoUnit` mistake in D22's note: the operator's
    prompt text is part of the system under test.
20. **Verify with the recipe the tool has, and say what it does not cover.** `mvn -B test` on this reactor
    finishes in 10-18 s and compiles everything, but it is incremental and it runs no tests (the app has
    none), so the tool ended every task in `APPLIED_UNVERIFIED` / `VERIFICATION_BLOCKED` and said
    "no tests ran" — which is the honest sentence, and the one to repeat to whoever asks whether the
    project works.

## M8 — what `ecommerce` is, and what the run did and did not prove

Every milestone the plan set out is reached: seven modules, 28 Java files, `mvn -B test` exit 0 from the
tool's own run control, every write committed by the tool as its own `agent: … [session-…]` checkpoint, and
a working tree that has been clean at every idle moment of the run.

| module | port | what it holds |
| --- | --- | --- |
| `ecommerce-common-lib` | — | `JwtService`, `JwtAuthFilter`, `ApiResponse`, `ApiExceptionHandler`, `ServerTimestampFilter` (response header on every request), `ServerAuditAspect` (server time logged around every `@RestController` call) |
| `eureka-server` | 8761 | registry |
| `config-server` | 8881 | native `file:../config-repo`, which holds `ecommerce-shared.yml` |
| `auth-service` | 8081 | `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/config/general`, Liquibase DDL + the `admin` seed, its own `SecurityConfig` |
| `customer-service` | 8082 | `GET`/`POST /api/customers`, `CustomerSecurityConfig` (everything authenticated but actuator and H2) |
| `product-service` | 8083 | `GET`/`POST /api/products`, `ProductSecurityConfig` |
| `api-gateway` | 8080 | WebFlux routes under `spring.cloud.gateway.server.webflux.routes`, and `JwtRelayFilter`: 401 without a Bearer token for anything but `/api/auth/**`, `/api/config/**`, `/actuator`; `X-User` from the token's subject; `X-Server-Timestamp` on every request it forwards |

### What the run verified

- **Compile**: `mvn -B test`, run by the tool (Settings → Maven test), exit 0, `BUILD SUCCESS` for all
  seven reactor modules. That is how D24's lost package line, D25's file of imports only, D34's two mangled
  poms and D36's missing entity were found — every one of them was caught by a command the agent ran, in
  seconds, without the operator opening a terminal.
- **Syntax gates**: the `.java`/`.xml`/`.json` checks refused a malformed pom *before* it reached the disk
  twice (D35), which is why no session ever had to recover from unreadable XML.
- **The seed**: `admin` / `admin` now hashes-true against the value in `db.changelog-master.xml`, checked
  out of band against `spring-security-crypto` (note 19).

### What it did not verify, and why

Nothing ran the application. `runner.py` allowlists build and test commands; there is no recipe that starts
a service, binds a port, or issues a request, and the exercise's own rule — never try anything in the
project by hand — ruled the operator doing it instead. So the JWT relay, the two timestamp surfaces, the
409 on a duplicate username and the H2 console are **specified, compiled and unproven**. The next section
is the shortest path from that to proven, and every command in it is a command the tool cannot run for you.

### Start order

Run each module from inside its own directory (the config server resolves `file:../config-repo` against the
working directory, so `mvn -B -f config-server/pom.xml spring-boot:run` from the root will not find it):

```
1  cd eureka-server     && mvn -B spring-boot:run     # http://localhost:8761
2  cd config-server     && mvn -B spring-boot:run     # http://localhost:8881
3  cd auth-service      && mvn -B spring-boot:run     # 8081, Liquibase runs, admin seeded
4  cd customer-service  && mvn -B spring-boot:run     # 8082
5  cd product-service   && mvn -B spring-boot:run     # 8083
6  cd api-gateway       && mvn -B spring-boot:run     # 8080
```

Wait for each client's `Registering application <name> with eureka…` line before starting the next one; the
gateway resolves `lb://…` names lazily, so starting it last avoids a first-request 503.

### Smoke list

```
curl -s http://localhost:8761                                   # Eureka dashboard, 5 apps registered
curl -s http://localhost:8881/ecommerce-shared/default          # the shared yml, from the native repo
curl -s -X POST http://localhost:8081/api/auth/login \
     -H 'content-type: application/json' \
     -d '{"username":"admin","password":"admin"}'                # {"token":"eyJhb…","roles":["ADMIN","USER"]}
curl -si http://localhost:8081/api/config/general                # 200 without a token, serverTime in the body
curl -si http://localhost:8081/api/customers                     # 401 or 403: every other API is authenticated
TOKEN=…from the login call above…
curl -si -H "Authorization: Bearer $TOKEN" http://localhost:8082/api/customers   # 200 + X-Server-Timestamp header
curl -si -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/products    # through the gateway: 200,
                                                                                  # and the service sees X-User=admin
curl -si -H "Authorization: Bearer $TOKEN" -X POST http://localhost:8080/api/customers \
     -H 'content-type: application/json' \
     -d '{"name":"Nour","email":"nour@example.com"}'              # 201
curl -si -X POST http://localhost:8080/api/auth/register \
     -H 'content-type: application/json' \
     -d '{"username":"admin","password":"x","email":"a@b.c"}'     # 409 username taken
```

Two things to watch while you run it, both named in the defects above:

- the app logs `serverTime=…` for every controller call (`ServerAuditAspect`) and answers with an
  `X-Server-Timestamp` header (`ServerTimestampFilter`) — that is the spec's "server date on every request
  and response", and if either is missing the first thing to check is that the module's pom really has
  `spring-boot-starter-aop` (note 15);
- `/h2-console/**` is permitted in `auth-service`'s config but that config does not set
  `frameOptions().sameOrigin()`, so the console page will refuse to render inside its frame. The customer
  and product configs do set it. That asymmetry is D31's fault — the shared config the plan wanted would
  have had one behaviour, and the tool has no way to retire the file it duplicated.
