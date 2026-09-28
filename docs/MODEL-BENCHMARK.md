# Model benchmark — what this machine can actually run

Measured 2026-09-24 on the laptop this tool is developed on, so the model list and the
default recommendation are grounded in observed latency rather than model size folklore.

## Hardware

| | |
| --- | --- |
| CPU | Intel Core i7-1065G7 (4 cores / 8 threads, Ice Lake, AVX-512) |
| RAM | 16 GB |
| GPU | NVIDIA MX330 with 2 GB VRAM |
| Inference | Ollama on `http://127.0.0.1:11434`, CPU execution |

2 GB of VRAM cannot hold a 3 B+ model together with its KV cache, so every model below
ran on CPU. That makes prompt processing, not generation, the dominant cost, and it is
why a 1.5 B model is roughly 3× faster than a 4 B one rather than 2.7×.

## Method

Two realistic proposals were sent through the production path
(`OllamaProvider.generate` with `format="json"`, `temperature=0`, `think:false` whenever
`/api/show` reports a thinking capability, `num_predict=700`):

1. Fix `mul()` in a one-line Python calculator.
2. Fix `login()` so an empty password fails and only `admin`/`s3cret` succeeds.

A run counts as **usable** only if the reply parses to an action-envelope object whose
`changes[].content` is complete file text, *and* the required code appears in it.
Scripts: `%TEMP%/model-bench3.py` (provider path) and `model-bench4.py` (re-check with
`engine.parse_action`, which recovers the outermost brace-balanced object from prose or
markdown fences). Raw results: `bench3.jsonl`, `bench4.jsonl`.

## Results

| Model | Envelope | Correct | Seconds (case 1 / 2) | Verdict |
| --- | --- | --- | --- | --- |
| `qwen2.5-coder:1.5b` | yes | yes | 30.0 / 24.2 | **default — correct and fast enough to iterate** |
| `qwen2.5-coder:0.5b` | yes | no | 14.2 / 15.6 | fast, but invents 5 unrelated files instead of fixing one |
| `deepseek-coder:latest` | yes | yes | 37.8 / 21.9 | usable; see the cache caveat below |
| `qwen3:4b` | yes | yes | 97.7 / 51.7 | most careful answers, ~2× slower than the default |
| `granite-code:3b` | yes | yes | 83.1 / 63.2 | correct but slower than `qwen3:4b` for the same quality |
| `deepseek-coder:6.7b` | yes | yes | 190.2 / 94.3 | correct, but 1.5–3 min per turn is not interactive |
| `codegemma:2b` | no | no | 36.6 / 17.9 | returns a JSON *manifest* or a schema description, never an action |
| `stable-code:3b` | no | no | 46.2 / 26.6 | writes `{"code": "..."}` — right fix, wrong envelope |
| `granite4.2:3b` | no | no | 276.5 / 22.2 | thinking loop, then `done_reason="length"` truncation |
| `gemma4:31b-cloud` | — | — | — | never benchmarked: Ollama runs it remotely, and `preflight()` refuses it unless cloud processing is approved |

`stable-code:3b` is the instructive failure: its `{"code": ...}` answer contains the
*correct* login logic. It is a protocol failure, not a capability failure, and no amount
of JSON-recovery parsing fixes it — `bench4` re-ran every model through `parse_action`
and these three still produced no `action` field. Rather than bend the envelope for one
family, the recovery keeps working for models that add prose around a valid object.

## Consequences for the app

- **Default is `qwen2.5-coder:1.5b`** (`config.py`, and preselected in the GUI with a ★
  annotation). The alternative shown in the model list is `qwen3:4b` for users who want
  more careful answers and will wait for them.
- **Speed ranks above parameter count here.** The two 0.5 B/2 B models that finish
  fastest are not usable, and the 6.7 B that is usable is too slow, so the ranking is
  driven by envelope compliance at interactive latency.
- **Request timeout is user-adjustable (30–900 s, Settings window).** A real proposal is
  up to 4096 output tokens; at the rates above that can exceed ten minutes for a 4 B
  model on CPU. The default local timeout is no longer sufficient, so the GUI exposes it
  and a timeout error explains the two fixes (raise the timeout, or ask for a smaller
  change).
- **Variance caveat:** `deepseek-coder:latest` and `deepseek-coder:6.7b` are the same
  weights with different tags, yet measured 37.8 s against 190.2 s on the identical
  case. The faster pair ran with a warm prompt cache. Treat single-run seconds as an
  upper bound, not a stable throughput.
- Small models also drive the engine's tolerance design: `parse_action` recovery, and
  the runtime reading a file on the model's behalf when it proposes an edit to a file it
  never read — both exist because a 1.5 B model gets the *intent* right and the protocol
  wrong.

## Where the default still fails: authoring new Java files

The table above measures *edits to small Python files*, which is what the tool mostly
does. The Spring Boot scenario exposed a different skill — authoring a new Java class
from a plan description — and `qwen2.5-coder:1.5b` failed it in two opposite ways:

- Unconstrained, it wrote a DTO with `equals`/`hashCode`/`toString` boilerplate and
  stopped mid-`toString()`, leaving a file that would not compile.
- Told to keep the file as short as possible, it wrote `{"username":"","password":""}` —
  a JSON *instance* of the DTO instead of Java source.

Both were caught by the tool's own `mvn -B compile` step, and `rollback` restored the
project to green each time. So the workflow held; the model could not carry this part.
The Spring sources were therefore authored by hand and verified through the tool
(`mvn -B test`: 8 tests, 0 failures; the running service returned 200 + HS256 token for
valid credentials and 401 for a wrong password, an unknown user and an unauthenticated
protected route).

**Choose the model by task, not by default.** For edits inside existing files the 1.5 B
is the right default and keeps iteration fast. For generating a new multi-class project,
use the largest model this machine can run slowly, or author the files and let the tool's
build-and-repair loop verify them.

## Reproducing

```
python %TEMP%/model-bench3.py      # provider path, JSON + correctness + wall time
python %TEMP%/model-bench4.py      # same replies re-checked through parse_action
```

Both need Ollama running with the models above already pulled
(`ollama list` shows the installed set).
