import json
import os
import re
import time
import urllib.error

import llm

_json_mode_ok = True
DEADLINE = time.time() + int(os.environ.get("RUN_DEADLINE", str(26 * 60)))
REQUEST_TIMEOUT = 120


def time_left():
    return DEADLINE - time.time()


def available():
    return all(os.environ.get(k) for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"))


def parse_json(text):
    if not text:
        return None
    text = re.sub(r"<(think|thought|thinking)>.*?</\1>", "", text, flags=re.S | re.I)
    text = re.sub(r"^\s*<(think|thought|thinking)>.*", "", text, flags=re.S | re.I)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if text.lstrip().startswith("["):
        start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def items(reply, key):
    reply = reply or {}
    found = reply.get(key)
    if found is None:
        found = reply.get("items")
    return [x for x in found if isinstance(x, dict)] if isinstance(found, list) else []


def ask_json(system, user, max_tokens=12000, attempts=7):
    global _json_mode_ok
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    delay = 4
    for attempt in range(attempts):
        if time_left() < 15:
            print("[model] run deadline reached: skipping this request", flush=True)
            return None
        try:
            fmt = {"type": "json_object"} if _json_mode_ok else None
            reply = llm.chat(messages, temperature=0, max_tokens=max_tokens, response_format=fmt,
                             timeout=max(10, min(REQUEST_TIMEOUT, time_left() - 5)))
            data = parse_json(reply)
            if isinstance(data, list):
                data = {"items": data}
            if isinstance(data, dict):
                return data
            print(f"[model] reply was not JSON (attempt {attempt + 1}): {str(reply)[:120]!r}", flush=True)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code == 400 and _json_mode_ok and "response_format" in body.lower():
                _json_mode_ok = False
                continue
            if e.code in (429, 500, 502, 503, 504):
                print(f"[model] HTTP {e.code}, retrying (attempt {attempt + 1})", flush=True)
                wait = e.headers.get("Retry-After")
                pause = min(float(wait), 60) if wait and wait.replace(".", "").isdigit() else delay
                time.sleep(max(0, min(pause, time_left() - 15)))
                delay = min(delay * 2, 60)
                continue
            print(f"[model] HTTP {e.code}: {body}", flush=True)
            return None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[model] network error: {e}", flush=True)
            time.sleep(max(0, min(delay, time_left() - 15)))
            delay = min(delay * 2, 60)
        except (KeyError, IndexError, TypeError):
            pass
    return None
