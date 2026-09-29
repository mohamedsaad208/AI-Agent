# Item 11 — better search and project understanding

Spec (as sent, 2026-09-29):

> 11. تحسين البحث وفهم المشروع — إضافة search أذكى:
> البحث بالرموز وليس النص فقط · البحث عن references لدالة أو class · dependency graph بصري ·
> اختيار الملفات ذات العلاقة بالمهمة · تجاهل الملفات المولدة تلقائيًا · دعم .gitignore ·
> دعم ملفات configuration المهمة حتى لو لم تكن source

Measured first, as usual. Four of the seven asks are already partly built, and the biggest search
problem in this project is not an algorithm at all — it is what the index is currently fed.

## What the code said

All numbers below are from this machine, read-only, 2026-09-29.

| Ask | Reality |
|---|---|
| Symbol search, not just text | **The symbol index already exists.** `symbols.py` (~30 KB) parses Python via `ast` and Java/Kotlin/JS/TS/Go/Rust via a brace-depth scan into rows of `{path, package, imports, types[{name,kind,line,members,extends}], functions}`, cached per root (`INDEX_CACHE`). What does not exist is any *query* over it: the model's only search verb is `search_code`, and `Workspace.search()` (`workspace.py:195-209`) is a case-folded **substring** match over file **contents**, ≤40 hits, never filenames, never symbols. |
| References to a function/class | **Does not exist.** `symbols.dependencies()` (`:549`) is forward-only, file → the project files it imports, and is what prints "depends on:" in the map. No name → sites index, no call graph. `symbols.blank()` (`:106`, strips strings and comments) is the piece that makes an honest reference scan possible without a parser per language. |
| Choose the files relevant to the task | **Exists in one literal form only.** `engine.py:652-674`: walk `ws.files()`, keep a file if `re.search(r"(?<![\w.])" + basename + r"(?![\w.])", task)` — the file's name must appear **verbatim in the user's sentence** — stop at **3 files**, budget `context_chars // 3`. "Fix the token expiry in the auth service" selects nothing; "look at JwtTokenProvider.java" selects it. No scoring anywhere. |
| Ignore generated files | **Partly, by name, as a security rule — and leaking.** `BLOCKED_PARTS` (`workspace.py:26-29`) prunes `target build dist node_modules __pycache__ .venv venv .gradle .m2 .idea` from the walk, and `runner.PROJECT_SKIP` (`runner.py:198`) plus `controller.SKIP_DIRS` (`:3265`) are two more local copies of roughly the same idea — **three divergent lists**. There is no "generated" concept (no `*.min.js`, no `*_pb2.py`, no `generated-sources`), and the tool's own output is not in any of them: `Workspace(Path('.')).files()` on this repo returns **591 paths, of which 383 are `.agent-webview/`, 9 `.agent-chats/`, 8 `.design-preview/`** — 400 of 591 are the agent's own artefacts, and `repo_map()` comes back at **12 206 characters against a 12 000 budget**, so on a large workspace the model's map is mostly this tool's chat transcripts and 34 real source files get truncated away. |
| `.gitignore` support | **Does not exist** — zero reads anywhere in `src/` or `tests/`. `git_integration.py` runs `status --porcelain`, `rev-parse`, `add`, `commit`, `checkout`; it never asks git what is ignored (and `status --porcelain` could not answer that without `--ignored`; `ls-files --others --exclude-standard` is the shape that can). Git is optional by design (`git_program()` may return `None`) and a granted folder may not be a repository, so this must be a pure-Python parser with git at most an accelerator. **Honest cost/benefit:** on `ecommerce` the map currently considers 48 files and that `.gitignore` (`target/ .mvn/ *.iml .idea/ .vscode/`) would drop **exactly 0 of them** — the name blocklist already prunes those trees. The value is on repos that ignore source-shaped directories (`logs/`, `data/`, `vendor/`, a generated client), which this tool will be pointed at even though its dogfood target is not one. |
| Important config files even if not source | **Half-built.** `.xml .gradle .yml .yaml .properties .json` pass `TEXT_SUFFIXES`, so they are readable and searchable today; but `symbols.INDEXABLE` (`:23`) is `{.py,.java,.kt,.go,.rs}` + scripts, so in the map a `pom.xml` is a **bare filename line with no declarations** — the modules of a Maven reactor, the artifactIds, and `server.port` are invisible to the model unless it happens to `read_file` them. |
| Visual dependency graph | **Data half-exists, surface does not.** `dependencies()` already yields the edges, aggregated per file, and `symbols.module_of()` (`:575`) already names the modules. Nothing renders it. The web window has the seams a sheet needs: `#rail` (`renderRail():1115`, with the Sources card at `:1188`), `sheet()` in `#modal-root`, actions through `/api/action` → `controller.action():953`, snapshots frozen under `self._state`. |

## Decisions

1. **One owner for "is this path noise".** A new `ignore.py` holds the three questions separately,
   because they are three different decisions that today share one list by accident:
   `protected()` (the security blocklist — credentials, `.git`, agent rule files; moved verbatim from
   `workspace.BLOCKED_PARTS` so nothing about the write gate changes), `generated()` (build output, the
   tool's own `.agent-*`/`.design-preview` dirs, `*.min.js`, `*_pb2.py`, `*.g.cs`, `generated-sources`),
   and `gitignored()` (parsed rules). `Workspace.files()` prunes all three; `runner.projects()` prunes
   `protected()+generated()` only, because a `.gitignore`d directory can still be a Maven module that has
   to be offered a command; the folder picker prunes `protected()+generated()` and keeps showing
   dot-folders it currently hides. That collapse of three lists into one is what makes the two windows
   and the planner agree for the first time.
2. **Symbol queries live in `symbols.py`**, the module that owns the index: `find_symbol(rows, name)`
   and `find_references(root, rows, name)`. A reference is found in `blank()`ed source — code, not
   strings or comments — and each hit is labelled `declaration` / `import` / `call` / `mention`. It is
   called *references*, not a call graph, and the row says which kind it got; the tool has no type
   inference, and pretending otherwise would be a worse answer than an honest one.
3. **Two new model verbs, `find_symbol` and `find_references`**, added to the action vocabulary and the
   `PolicyError` that lists it. Small local models currently guess file paths from a truncated map;
   a name → path lookup is the difference between a plan that reads the right file and one that burns
   three of twelve turns.
4. **Relevance becomes a score, and says why.** `symbols.rank(rows, task, limit)` scores every indexed
   file: a type or member whose name appears in the task (strongest), an identifier-shaped token of the
   task present in the file's declarations, one import hop from a strong hit, same module as a strong
   hit. The verbatim-basename rule stays as a signal (so today's behaviour is a subset of the new one),
   and the window prints the reason it chose each file — a context block nobody can explain is the same
   complaint the memory notes already record about injected context.
5. **Config files enter the map as facts, never as values.** A `_config()` parser adds `pom.xml`
   (artifactId, `<modules>`, dependency artifactIds), `build.gradle`, `package.json` (name, deps) and
   `application.yml`/`.properties` — but only keys from a small allow-list (`server.port`,
   `spring.application.name`, datasource **host**, never a userinfo or a value), and any key matching
   the credential shape `SECRET_NAME` is dropped rather than redacted. A map that leaks a database
   password into a prompt and out through `agent export-session` would be the second time this project
   learned that lesson the hard way.
6. **The graph is a sheet of server-computed data rendered as plain SVG** — module nodes, edges with
   counts, columns by longest-path depth, no force-directed simulation and no library. Computed on the
   click, not per snapshot: the walk is cached but a snapshot goes out on every streamed log line.

## Deliberately not built

- **No third-party indexer.** No ctags, no tree-sitter, no `jedi`, no `rope`, no graph-drawing npm
  package. Stdlib only is the project's load-bearing constraint.
- **No call graph in the compiler's sense.** No overload resolution, no generics, no Spring bean
  wiring, no CDI resolution. `find_references` reports textual occurrences in code with their kind, and
  says so.
- **No incremental file watcher.** The index cache is keyed by root with an mtime/size check already;
  a watcher thread for a desktop tool that opens a folder per task is a second source of truth.
- **No semantic embeddings.** There is no network model to call for them, and a local embedding model
  is not what "search by symbol" asked for.
- **No change to what `search_code` does today** — substring search stays, because the model uses it
  for string literals and log lines, which a symbol index cannot answer.
- **No graph in the Tk window.** Its inspector has no sheet surface; the Host contract is satisfied by
  the sentence the web window prints, not by pixel parity.

## Shipped

All five phases are in. The suite moves from 1167 to **1373** across them, and every number below was
measured on the dogfood target (`ecommerce`, read-only) rather than asserted.

| Phase | What landed | Measured effect |
| --- | --- | --- |
| 1 | `ignore.py` owns protected / generated / gitignored, one composition each; `workspace.files()` walks with nested `.gitignore` rules and carries its skip counters; `runner` and `controller` gave up their private directory lists. | This repo's map lost all 400 of the tool's own artefact paths; the walk's note says what it hid instead of leaving the reader to guess. |
| 2 | `symbols.find_symbol()` / `find_references()`, and `find_symbol` / `find_references` as model verbs (seven verbs in the refusal text now). | A name → path lookup costs one action instead of a guessed path plus a failed read. |
| 3 | `symbols.rank()` scores the index against the task sentence; `engine.py` injects up to `MAX_CONTEXT_FILES = 5` already-read snapshots and announces each with its reason; `labels.CONTEXT_REASON` words the reason in both languages. | "why does run return the wrong number?" now reaches the file that declares `run()`; the old rule needed the filename typed verbatim. |
| 4 | Configuration facts in the map: `noteworthy()` + `config_facts()` + `config_row()`, printed by `render()` under the path, excluded from `sources()` so name queries stay code-only. | `ecommerce` map: **8 515 → 10 551 characters of the 12 000 budget**, and it now carries 8 poms (parent / artifact / modules / needs) and 7 services' `port` + `name` + `registry` — the start order, from the index, without three turns of `read_file`. |
| 5 | `symbols.graph()` — module nodes, counted edges, a peeling column layout, the heaviest modules kept and the rest counted — and the sheet: a 🕸️ button on the Sources card, a `show_graph` action answered through the POST reply, plain-SVG columns with edge weights, and a caption that says what the drawing leaves out. | `ecommerce`: 8 modules, 9 edges, 0 hidden, no cycle, drawn in 3 columns. The cycle path is exercised by the `--fake` preview, because the real reactor has no cycle to draw. |

### Premises that turned out wrong while building

The spec's own table was written from measurement, and six things it did not see:

1. **The map's budget comment lied about the retrieval budget.** `engine.py` said "the budget is
   unchanged" while `remaining = context_chars // 3` was spent *on top of* the instruction block and the
   map. `SYSTEM` alone is 3 868 characters, so a configured `context_chars` of 4 000 — legal until now,
   and what the CLI fixture used — could not start any task: the refusal blamed the repository for the
   tool's own prompt. `config.MIN_CONTEXT_CHARS = 6000` names the floor, the retrieval budget is
   `min(one third, what the window actually leaves)`, and `test_agent` pins the two against `SYSTEM`.
2. **`noteworthy()` promised `.properties` support the parser did not deliver.** `server.port=8080` is
   the same fact with a different separator, and a Spring project written in properties arrived with no
   facts at all.
3. **Two manifest shapes were unread, not misread.** `cargo.toml` was dispatched to the Go reader (it is
   TOML), and Go's grouped `require ( … )` — the usual spelling for any real module — matched nothing.
4. **`URL_HOST` could not see a JDBC URL.** `jdbc:postgresql://…` nests a second scheme, and a
   single-scheme pattern read it as "no host", which is most of the useful half of a Spring config file.
5. **A hyphenated module ranked nothing.** `task_words` splits `auth-service` into two words exactly as
   it splits a camel-case class, but the folder test compared the whole name — so every module of a
   Maven reactor, which is how a reactor spells itself, was invisible to the ranking.
6. **`add` was in the stop list.** It is one of the most common names in code (`add(a, b)`), so the
   sentence this project's own tests use — "fix add" — selected no file.

### The credential rule, as shipped

A configuration value reaches the map only when its **whole dotted key** is allow-listed
(`KEY_FACTS` / `LEAF_FACTS`), and any key or path matching `CREDENTIAL_KEY` is dropped before its value
is considered. `test_a_key_that_names_a_credential_never_yields_its_value` carries a canary through YAML,
properties, a JDBC URL with `user:password@`, and a CI variable; the same canary is asserted absent from
`repo_map()` at the workspace level. Reading a file on purpose is still verbatim — this is the boundary
of the *automatic* half, not of reading.

## Build order

Each phase keeps the suite green on its own; the count moves from 1167.

1. **`ignore.py`** — the three predicates, the `.gitignore` parser, `workspace.py` importing its
   `protected()` verbatim, the walk pruning `generated()+gitignored()`, `runner`/`controller` giving up
   their private lists. Test: this repo's map contains no `.agent-webview`, `.agent-chats` or
   `.design-preview` line, and the file count drops from 591 to ~190.
2. **`find_symbol` + `find_references`** in `symbols.py`, then the two model verbs + vocabulary + shape
   validation + the refusal text.
3. **`symbols.rank()`** and the `engine.py` selection rewrite, with the reason line on both windows.
4. **`_config()`** in the index and `render()` printing it, plus the allow-list and the credential
   guard.
5. **The graph**: `symbols.graph()` (module nodes, counted edges, depth columns), a `show_graph` action,
   the sheet, the SVG renderer, and the `--fake` preview so it can be reviewed without an engine.
6. **Tests throughout**: `test_symbols.py` for the queries and ranking, a new `test_ignore.py`,
   `test_workspace.py` for the walk, `test_agent.py` for the new verbs through a real plan,
   `test_controller.py`/`test_gui.py` for the surfaces, `test_webapp.py` for the fake's snapshot parity,
   and `test_host.py`'s ratchet for every sentence a window prints.
