"""Open loops: things that still need doing, found by the model and checked by code.

How it works:
  1. Each chat becomes compact lines:  "M00391 14:57 Priya: Pls pay 1500 for the milk", with a header for each day.
  2. Long chats are cut into parts sent in order; each part is told which loops are still open from the
     parts before, so a request on Monday can be closed by a "paid" on Thursday.
  3. Small chats are packed several to a request, to save cost.
  4. Code checks every answer: message ids must exist, and a loop can only be "done" if a real later
     message shows it. Where to help is decided afterwards, for open loops only (help.py).
"""
import datetime as dt
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import model
from .data import ME, clean, msg_id

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# About 1,700 tokens of chat per request. Measured on dev: 6,000 found twice the loops of 12,000, because
# a small model skims long input.
CHUNK_CHARS = int(os.environ.get("LOOPS_CHUNK_CHARS", "6000"))
WORKERS = int(os.environ.get("LOOPS_WORKERS", "8"))
TIME_BUDGET = int(os.environ.get("LOOPS_TIME_BUDGET", str(22 * 60)))  # stop starting new work after this
# Only loops raised in the last two weeks are scored (loops raised earlier are dropped from both sides),
# so the loops pass reads only the last WINDOW_DAYS: two weeks plus a margin in case the cut-off is
# counted a little differently. On the six-week hidden set this cuts the biggest cost to about 40%.
WINDOW_DAYS = float(os.environ.get("LOOPS_WINDOW_DAYS", "16"))
DEBUG = bool(os.environ.get("LOOPS_DEBUG"))
MAX_BODY = 400
TYPES = {"commitment", "followup", "deadline", "question"}

SYSTEM = """You read the WhatsApp chats of Arjun (shown as "Arjun") and list EVERY LOOP in them: each request, promise, deadline or question that needed a later action involving Arjun.
List a loop even if it was finished later in the chat: then mark it "done". Most loops get finished, and they still count. Small everyday asks count too ("Pls pay 1500 for the milk", "Can you send me the link", "Pls order a school bag for the kids").

Loop types:
- commitment: Arjun promised or agreed to do something, OR someone asked him to do something that falls to him ("Pls pay 1500 for the milk", "Can you send me the link", "Can you order two more").
- followup: Arjun is waiting on someone else to do something for him (he asked them, or they promised him).
- deadline: something Arjun must do by a specific date or time (a fee due by a date, a booking cut-off, wishing someone on their birthday today).
- question: Arjun must decide or answer something (pick a meeting time, yes/no, choose between options).

Easy to miss: when others in a group wish someone a happy birthday or anniversary, or congratulate them, Arjun should do the same (deadline, today; "done" if he did). An invitation, or a meeting time someone proposes, is a question for Arjun (confirm, or add it to his calendar).

In groups, be strict. Count: requests addressed to Arjun (his name, a reply to something he said) or clearly his to act on; promises someone makes in a small group he is in ("I'll send the invoice today": a followup owned by them); birthdays and invitations as above. Do not count: routine tickets in an operations group (update a bill, change an ad, process an id), requests tagging someone else, questions asked to a whole community group.

Not loops: chit-chat, forwarded news or links, group announcements that ask nothing of Arjun, things between other people, things already settled within the same message.

For each loop:
- source_msg: the id of the message that FIRST raised it (the request, the question, or Arjun's promise).
- title: a short imperative line, e.g. "Pay Priya 1,500 for the milk".
- owner: "ME" if Arjun owes the next action, "THEM" if someone else does.
- due: "YYYY-MM-DD" if a date is stated or clearly implied ("tomorrow", "by Saturday": use the day headers), else null.
- status: "done" ONLY if a LATER message clearly shows it happened (payment sent, "done", "ordered", "sent", the answer given, the meeting fixed); put that message id in closed_by. "dropped" only if clearly cancelled or abandoned, with closed_by. Otherwise "open" with closed_by null. When unsure, say "open".

One loop per real task: do not split one request into several loops or list the same task twice.
Reply with JSON only: {"loops":[{"chat_id":"...","source_msg":"...","title":"...","type":"...","owner":"ME|THEM","due":null,"status":"open","closed_by":null}],"updates":[{"source_msg":"...","status":"done|dropped","closed_by":"..."}]}
"updates" is only for loops listed as still open from earlier parts of a chat."""


# ---------------------------------------------------------------- formatting

def short_name(name):
    name = clean(name)
    return name.split()[0] if name else "?"


def day_label(ts):
    return dt.datetime.fromtimestamp(ts, IST).strftime("%a %d %b %Y")


def format_lines(msgs):
    """Messages as compact lines, with a header whenever the day changes."""
    out, last_day = [], None
    for m in msgs:
        day = day_label(m["ts"])
        if day != last_day:
            out.append(f"--- {day} ---")
            last_day = day
        who = "Arjun" if m["sender_id"] == ME else short_name(m["sender_name"])
        body = clean(m["body"]).replace("\n", " / ")
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY] + "..."
        out.append(f"{m['id']} {dt.datetime.fromtimestamp(m['ts'], IST):%H:%M} {who}: {body}")
    return out


def chat_header(d, cid, circles):
    c = d.chats[cid]
    others = [p for p in c["participants"] if p != ME]
    if c["is_group"]:
        return f"=== CHAT {cid} | group \"{clean(c['name'])}\" | {len(others)} other members ==="
    who = others[0] if others else "?"
    circle = circles.get(who, "unknown")
    return f"=== CHAT {cid} | 1:1 with {clean(c['name']) or d.display_name(who)} ({circle}) ==="


def split_parts(lines, budget):
    """Cut one chat's lines into parts of at most `budget` characters, repeating the day header."""
    parts, cur, size, day = [], [], 0, None
    for line in lines:
        if line.startswith("--- "):
            day = line
        if cur and size + len(line) > budget:
            parts.append(cur)
            cur, size = ([day] if day and not line.startswith("--- ") else []), 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        parts.append(cur)
    return parts


def plan_jobs(d, circles, since=0):
    """Return jobs. A job is a list of requests that must run in order; each request is a list of
    (chat_id, header, lines) blocks. Big chats become multi-request jobs; small chats are packed.
    Only messages at or after `since` (unix seconds) are read."""
    jobs, pack, pack_size = [], [], 0
    sized = []
    for cid in d.chats:
        msgs = [m for m in d.by_chat.get(cid, []) if m["ts"] >= since]
        if msgs:
            lines = format_lines(msgs)
            sized.append((sum(len(x) + 1 for x in lines), cid, lines))
    for size, cid, lines in sorted(sized, reverse=True):
        header = chat_header(d, cid, circles)
        if size > CHUNK_CHARS:
            jobs.append([[(cid, header, part)] for part in split_parts(lines, CHUNK_CHARS)])
            continue
        if pack and pack_size + size > CHUNK_CHARS:
            jobs.append([pack])
            pack, pack_size = [], 0
        pack.append((cid, header, lines))
        pack_size += size
    if pack:
        jobs.append([pack])
    return jobs


# ---------------------------------------------------------------- calling the model

def run_job(job, end_label, deadline):
    """Run one job's requests in order, carrying open loops from part to part. Returns raw loop dicts."""
    found = {}  # source_msg -> loop
    for request in job:
        if time.time() > deadline:
            break
        carried = [l for l in found.values() if l.get("status") == "open"]
        text = [f"The data ends on {end_label}. Anything not closed by then is open."]
        if carried:
            text.append("Loops still open from earlier parts of this chat (close them in \"updates\" if this part shows it):")
            text += [f"- {l['source_msg']}: {l.get('title', '')}" for l in carried]
        for cid, header, lines in request:
            text.append(header)
            text += lines
        reply = model.ask_json(SYSTEM, "\n".join(text))
        if DEBUG:
            chats = ",".join(cid for cid, _, _ in request)
            n = len((reply or {}).get("loops") or [])
            print(f"[loops] {chats[:40]:40} lines={sum(len(ls) for _, _, ls in request):4} carried={len(carried):2} "
                  f"-> {'FAILED' if reply is None else f'{n} loops'}", flush=True)
        if not reply:
            continue
        for loop in model.items(reply, "loops"):
            if not isinstance(loop, dict):
                continue
            loop["source_msg"], loop["closed_by"] = msg_id(loop.get("source_msg")), msg_id(loop.get("closed_by"))
            if loop["source_msg"] and loop["source_msg"] not in found:
                found[loop["source_msg"]] = loop
        for upd in reply.get("updates") if isinstance(reply.get("updates"), list) else []:
            if isinstance(upd, dict) and msg_id(upd.get("source_msg")) in found:
                found[msg_id(upd["source_msg"])].update({"status": upd.get("status"), "closed_by": msg_id(upd.get("closed_by"))})
    return list(found.values())


# ---------------------------------------------------------------- checking the answers

def tidy(d, raw, end_ts):
    """Turn the model's loops into valid output entries, dropping anything we can't verify."""
    msg = {m["id"]: m for m in d.messages}
    out, seen = [], set()
    for l in raw:
        src = msg.get(l.get("source_msg"))
        if not src or src["id"] in seen:
            continue
        seen.add(src["id"])
        cid = src["chat_id"]  # trust the message, not the model's chat_id
        others = [p for p in d.chats[cid]["participants"] if p != ME]
        typ = l.get("type") if l.get("type") in TYPES else "commitment"
        if str(l.get("owner", "ME")).upper() == "ME":
            owner = ME
        elif src["sender_id"] != ME:
            owner = src["sender_id"]
        else:
            owner = others[0] if others else ME
        due = l.get("due") if isinstance(l.get("due"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", l["due"]) else None

        status, closed_by = l.get("status"), l.get("closed_by")
        closer = msg.get(closed_by)
        # A loop is only closed if a real message in the same chat, after the source, shows it.
        if status not in ("done", "dropped") or not closer or closer["chat_id"] != cid or closer["ts"] < src["ts"] or closer["id"] == src["id"]:
            status, closed_by = "open", None

        out.append({"title": clean(str(l.get("title") or "Follow up"))[:160], "type": typ, "owner": owner, "chat_id": cid,
                    "source_msg": src["id"], "due": due, "status": status, "closed_by": closed_by, "help": None})
    return out


def find_loops(d, circles):
    """The whole open-loops step. Returns a list of output entries (empty if no model is configured)."""
    if not model.available() or not d.messages:
        return []
    end_ts = d.messages[-1]["ts"]
    end_label = day_label(end_ts)
    jobs = plan_jobs(d, circles, since=end_ts - WINDOW_DAYS * 86400)
    deadline = time.time() + TIME_BUDGET
    raw = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for loops in pool.map(lambda j: run_job(j, end_label, deadline), jobs):
            raw.extend(loops)
    return tidy(d, raw, end_ts)
