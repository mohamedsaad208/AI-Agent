# Configuration overrides — the file, the signature, and why it is not encrypted

The request, in order: an override layer for any configuration value, driven from the UI, naming the
target and the key and the new value, creating a key the file never had; a file next to the tool created
automatically on first run, ignored by git so it never gets pushed; edited when it already exists; and
"encrypted so nobody can change it except from inside the program".

## Why not encryption — three reasons, the first is decisive

1. **The standard library has no cipher.** `hashlib`, `hmac` and `secrets` are there; AES is not. Real
   encryption means a third-party dependency, and stdlib-only is the load-bearing rule of this project —
   the alternative, a hand-rolled cipher, is worse than no cipher.
2. **The key cannot be placed anywhere the attacker cannot reach.** For the program to read the file on
   launch without asking a passphrase, the secret has to sit on this machine, readable by the same
   account that can read the file. Whoever can edit the ciphertext can decrypt, change, and re-encrypt
   it. Encryption buys confidentiality, and these contents are not confidential: the schema refuses
   credential *values* by design (keys live in memory only — `README.md`), so what remains is a
   provider name, an endpoint and four numbers.
3. **It would destroy the thing asked for two messages earlier** — a file created at first run that a
   person can look at and understand. Ciphertext documents nothing.

So the requirement "nobody edits it except from inside the program" gets the mechanism that actually
moves it: **the file is signed by the program and foreign content is not obeyed.**

## Shape

`.agent-overrides.json` next to `.agent-projects.json`, plus `.agent-overrides.key` (a per-install
random secret, mode `0600` where POSIX allows it). Both gitignored explicitly.

```
{ "rows": [{target, key, value, at, by}], "signature": "<hmac-sha256 over the canonical rows>" }
```

- **Created on first run** — not as an empty object but as the schema itself: allowed keys, their ranges,
  the resolution order, and the two ways to change a row. A file that documents itself beats a doc page
  nobody opens; an empty `{}` teaches nothing and gets rewritten every launch.
- **Written only by the program**: the UI section and `agent overrides --set/--unset`. Each write
  re-signs.
- **Read with suspicion**: a bad signature, a row whose `key` is not in the schema, or a value that
  breaks `validate()`/`check_endpoint` is **dropped and named** — the tool carries on with what it trusts
  and says which rows it refused and why. Hand-editing the JSON therefore cannot silently change
  behaviour, which is the actual fear behind "encrypted".
- **Honest scope, in these words:** this is tamper *evidence*, not access control. The owner of the
  account can rewrite both files; nothing on a local filesystem stops them, and pretending otherwise
  would be the worst kind of security feature — the one that makes a person believe they are protected.
  What it does guarantee is that an edit made outside the program is **seen and refused**, not followed.
- **Nothing secret lands in it.** A value shaped like a credential is refused at write time, and
  `api_key_env` may only name a variable, exactly as profiles already work.

## Precedence

```
what the person typed in this window  ->  an override row  ->  the profile  ->  the provider's env var
```

Applied in `load_settings` **before** `validate()`, so an override is judged by the same rules as a
profile line and there is no second validator to drift.

## What already exists (measured 2026-09-30)

| claim in the request | measured |
| --- | --- |
| "override any configuration value" | **Narrowed on purpose.** `load_settings` refuses unknown fields with `"Unknown configuration field."`, and a key nothing validates is a key nothing *reads* — a typo'd limit would silently not apply. The schema is the allowlist; a row may add a key the profile never mentioned (`timeout_seconds` in a profile with no `[limits]`), which is what "create it if missing" means usefully. |
| "a file next to the project" | **Moved to the tool's own directory** at the user's own correction two messages later: it is the tool's settings, so it lives with `.agent-projects.json` and writes nothing into any granted folder — the line the read-only round hardened. |
| "ignored so it never gets pushed" | The repository's `.gitignore` lists `.agent-*` entries **by name, not wildcard**, so each new file needs its own line. |
| "the UI already has settings" | `app.js` `openSettings` with four sections, `gui.py`'s settings window; per-provider `endpoints` and `set_pref` are already override-like writers, and Tk has no `set_pref` at all. |

## Build order

1. `overrides.py`: the signed store — schema, canonical bytes, sign/verify, refuse-and-report reads.
2. `config.load_settings`: apply before `validate`, one order, no second validator.
3. `.gitignore`: two explicit lines.
4. First run: `setup`/launcher creates the template when the file is absent.
5. CLI: `agent overrides [--list] [--set TARGET KEY VALUE] [--unset TARGET KEY] [--arabic]`.
6. Both windows: a Settings section (web drawer + Tk frame), the snapshot field, `--fake` rows.
7. Tests: signature, foreign row refused and named, unknown key refused, out-of-range refused,
   credential-shaped value refused, template created once and never rewritten, precedence order,
   both windows' setters, and the DATA contract.

## Built — 2026-09-30

All seven shipped. **1466 → 1534 offline tests**, `node --check` clean, and the section driven live in
a `--fake` window: save a row and watch it land, type an out-of-range number and watch it be refused
without touching the file, remove a row and watch the status say so.

Measured against the code, six of the shapes above changed. None of them is a preference:

| planned | built, and why |
| --- | --- |
| a row names a target — provider, or a profile label | **provider key or `*`, no profile label.** Overriding one profile by its name is a second way to do what that profile's own file already does, and it puts a string typed into a dropdown inside the matching rule. The provider key is what both windows and the table already agree on. |
| "override any configuration value" | **`provider` is excluded, and that exclusion buys the rest.** A side file able to swap which vendor answers a task is the failure `providers.py` already refuses OpenRouter's upstream fallbacks for. With the provider fixed per read, one pass of `values(app_dir, provider)` covers the merge — the two-pass version this replaced had to guess which row's address the other's provider would use. |
| a row beats the profile | **every key except an address, and except the model a window is showing.** An address is where the code and the key go; a row that silently redirects one a profile spelled out is the config-injection shape, so a row only ever *supplies* an address nobody wrote down. Two tests written on the opposite assumption failed first — mine, and wrong. |
| an unreadable file answers as nothing overridden | **it is named.** A corrupt file that reads as "no rows" is a silent behaviour change, which is the exact thing this layer exists to prevent. Same for a whole-file refusal: it says "the whole file" rather than inventing a row nobody edited. |
| the file documents its ranges | **it reads them out of `validate`.** `config.LIMITS` is a new module constant hoisted out of the loop, and `overrides.document()` prints from it. A file that restated `1 to 30` was the second table waiting to drift — the same trap the request-timeout range already fell into. |
| the refusal is English | `overrides` writes its own sentences in both languages and `put()` takes the asking window's language, so an Arabic drawer refuses in Arabic. The **ranges** stay English because they are `validate`'s, in every surface of this tool — half-translating one rule is how two windows end up disagreeing about a number. |

Also from the build, not from the plan: the signature covers the **stored order**, so a hand-edit that
only reorders rows is refused too (an earlier version sorted before signing, and a test caught it); the
first-run hook lives in the two window constructors and `agent setup`, not in four entry points; and
both new file names went into `ignore.PROTECTED_DIRS` beside `.agent-projects.json` — the signature is
the backstop, but a proposal should not get to rewrite the file that decides where tasks run.

One cost the round found by itself: the rows ride in `snapshot()`, and the first version re-read the
signing key file on every push — a snapshot goes out on every streamed log line. Cached now by the same
`(mtime_ns, size)` stamp idiom `modes.py` uses. The symptom was a queue test counting two tasks instead
of one, only under suite load: it passed alone, failed inside `discover`, passed on a rerun. Timing, not
logic — but the only thing that moved it was this read, so it is measured and reported here rather than
filed as flake.

**What remains true and unwelcome:** the file is not protected from its owner. `agent overrides --set`
will re-sign anything it is handed, so this is evidence about who wrote a row, not a lock on the row.
Said in the file's own first block, in `overrides.scope()`, in both windows, and in the README.
