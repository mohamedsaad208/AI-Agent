"""Reproduce the Ollama HTTP 500 the agent hit, and read the body it threw away.

The run's second prompt (three module poms) died with "Provider HTTP 500; no automatic retry or
fallback", and the session record kept no reason. This asks the same endpoint the same way with a
prompt of the size the engine actually sends, and prints what Ollama answered.

Run:  python sandbox/probe-ollama-500.py
"""
import json
import urllib.error
import urllib.request

URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5-coder:3b"

# Roughly the shape the engine sends: a system block, the task, and the files it read.
FILLER = ("This is the Maven aggregator for a multi-module Spring Boot application. "
          "Requirements: modelVersion 4.0.0; parent org.springframework.boot : "
          "spring-boot-starter-parent : 3.5.16; groupId com.ecommerce. ")


def ask(prompt_chars, num_predict=700, num_ctx=None):
    body = {"model": MODEL, "stream": False, "format": "json",
            "options": {"temperature": 0, "num_predict": num_predict},
            "messages": [{"role": "user", "content": "x" * 0 + FILLER * (prompt_chars // len(FILLER))}]}
    if num_ctx:
        body["options"]["num_ctx"] = num_ctx
    request = urllib.request.Request(URL, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=240) as answer:
            data = json.loads(answer.read().decode("utf-8", "replace"))
            return f"200 ok | prompt_eval_count={data.get('prompt_eval_count')} " \
                   f"| eval_count={data.get('eval_count')}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code} | {exc.read().decode('utf-8', 'replace')[:300]}"
    except Exception as exc:                                  # noqa: BLE001 - a probe reports, never raises
        return f"{type(exc).__name__}: {str(exc)[:300]}"


for size, ctx in ((2000, None), (8000, None), (20000, None), (20000, 8192), (60000, None)):
    print(f"prompt ~{size:>6} chars | num_ctx={str(ctx):>5} -> {ask(size, num_ctx=ctx)}")
