"""A tiny client for any OpenAI-compatible chat API, standard library only.

It reads LLM_BASE_URL, LLM_API_KEY and LLM_MODEL from the environment, which is how we run your
code with our own key when we score it. Every call appends its token counts to usage.jsonl, so you
can see what a run costs before we do.

    from llm import chat
    reply = chat([{"role": "user", "content": "Hello"}])

You don't have to use this file. Any client is fine, as long as it reads those three variables and
sends nothing anywhere else.
"""
import json
import os
import time
import urllib.request
from pathlib import Path


def _load_dotenv():
    """For local testing only: read KEY=VALUE lines from a .env next to this file.
    Variables already set in the environment (as they are when the graders run us) always win."""
    env = Path(__file__).resolve().parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def chat(messages, model=None, temperature=0, max_tokens=None, response_format=None, timeout=120):
    base = os.environ["LLM_BASE_URL"].rstrip("/")
    body = {"model": model or os.environ["LLM_MODEL"], "messages": messages, "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if response_format:
        body["response_format"] = response_format
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + os.environ["LLM_API_KEY"], "Content-Type": "application/json"},
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    usage = data.get("usage") or {}
    with open("usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "model": body["model"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "seconds": round(time.time() - started, 2),
        }) + "\n")
    return data["choices"][0]["message"]["content"]
