"""A careful wrapper around llm.chat: retries, JSON parsing, and graceful failure.

They run our code once, on a model we name, so a single bad reply or a rate limit must never crash the run.
Every call goes through llm.chat, which logs token use to usage.jsonl.
"""
import json
import os
import re
import time
import urllib.error

import llm

_json_mode_ok = True  # flipped off the first time the endpoint rejects response_format


def available():
    """True when the three LLM_* variables are set. Without them we skip the model and keep rule output."""
    return all(os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"))


def parse_json(text):
    """Pull the first JSON object out of a reply, even if it is wrapped in ```json fences or prose."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def ask_json(system, user, max_tokens=12000, attempts=7):
    """max_tokens is generous because some models spend hidden 'thinking' tokens from the same budget."""
    """Send one prompt and return the parsed JSON object, or None if every attempt failed."""
    global _json_mode_ok
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    delay = 4
    for attempt in range(attempts):
        try:
            fmt = {"type": "json_object"} if _json_mode_ok else None
            reply = llm.chat(messages, temperature=0, max_tokens=max_tokens, response_format=fmt, timeout=180)
            data = parse_json(reply)
            if isinstance(data, dict):
                return data
            print(f"[model] reply was not JSON (attempt {attempt + 1}): {str(reply)[:120]!r}", flush=True)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code == 400 and _json_mode_ok and "response_format" in body.lower():
                _json_mode_ok = False  # this model has no JSON mode: ask in plain text instead
                continue
            if e.code in (429, 500, 502, 503, 504):
                print(f"[model] HTTP {e.code}, retrying (attempt {attempt + 1})", flush=True)
                wait = e.headers.get("Retry-After")
                time.sleep(min(float(wait), 60) if wait and wait.replace(".", "").isdigit() else delay)
                delay = min(delay * 2, 60)
                continue
            print(f"[model] HTTP {e.code}: {body}", flush=True)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[model] network error: {e}", flush=True)
            time.sleep(delay)
            delay *= 2
        except (KeyError, IndexError, TypeError):
            pass  # malformed reply: try again
    return None
