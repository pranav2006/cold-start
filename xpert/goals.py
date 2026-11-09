"""Goals: up to 10 goals Arjun holds, each with the messages that show it.

Two steps:
  1. Hints. A goal is something Arjun himself says or answers ("I'll book Coorg", "Yes, 4 runs a
     week"), so we read only his own messages, each with the message he was replying to: about 15% of
     the text. Batched like the loops pass, each request lists the messages that show a goal.
  2. Merge. Hints repeat each other ("gym at 7" on five days is one habit), so one small last request
     merges them: same goal -> one entry, with all its hint messages as evidence. Code checks every field.

We first tried asking the loops pass to note goal hints on the side (no extra input tokens), but the
small model, busy with loops, returned 4 hints for 4,500 messages. A focused prompt does far better.

Why evidence matters: a goal is scored as right when its area matches and at least one evidence
message is shared with the answer, so listing every supporting message raises the chance of a match
at no cost. Invented goals cost points, so the merge is told to keep only goals the messages support.
"""
import datetime as dt
import os
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import model
from .data import ME, clean, msg_id
from .loops import chat_header, format_lines, split_parts

AREAS = {"self", "spouse", "kids", "parents", "work", "friends", "experiences", "finances", "health", "home"}
TYPES = {"habit", "milestone", "experience"}
HORIZONS = {"3mo", "1yr", "3yr", "5yr"}
MAX_GOALS = 6  # the cap is 10; fewer, better-supported goals score higher when precision counts
PER_AREA = 2  # answers match one to one, so a third goal in one area is almost always a split-up duplicate
MAX_EVIDENCE = 20  # extra evidence costs nothing and raises the chance of sharing a message with the answer
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CHUNK_CHARS = int(os.environ.get("GOALS_CHUNK_CHARS", "8000"))
WORKERS = int(os.environ.get("LOOPS_WORKERS", "8"))
TIME_BUDGET = int(os.environ.get("LOOPS_TIME_BUDGET", str(22 * 60)))

HINT_SYSTEM = """You read Arjun's own WhatsApp messages (each with the message he was replying to) and find the ones that show a GOAL he holds.

A goal is something Arjun, or his family with him, wants over the coming months or years:
- milestone: to achieve once (a company target, a launch, hiring a team, buying, building or doing up a home, a child's admission, a fitness target).
- habit: to keep doing (exercise, a diet, reading, prayer, time with the kids, saving every month).
- experience: a trip, holiday or event he is planning or looking forward to.
Not goals: one-off chores and errands (pay a bill, send a file), small talk, forwarded news, other people's plans he has no part in.

List every message that shows a goal, as one hint per message; several messages can support the same goal (use the same goal title). Include the other person's message too when it states the goal Arjun agrees to.
For each hint: msg (the message id), area (one of: self, spouse, kids, parents, work, friends, experiences, finances, health, home) and goal (a short, specific title, e.g. "Run the half marathon in January").
Most chats have no goals. Reply with JSON only: {"goals":[{"msg":"M...","area":"...","goal":"..."}]}"""

SYSTEM = """You turn notes about Arjun's goals into a clean list. Each note is a message from his WhatsApp that may show a goal, with a first guess at its area and title.

A goal is something Arjun (or his family, with him) wants over the coming months or years: to achieve (a company target, a launch, buying or building a home, a child's admission), to keep doing (a habit: exercise, diet, reading, time with the kids) or to experience (a trip, an event).
Not goals: one-off chores and errands, other people's goals he has no part in, things already finished in passing.

Merge notes about the same goal into one. Many notes are steps towards one bigger goal: tiles, lights, windows and furniture for the same house are one goal ("Finish doing up the new home"); several hires are one goal ("Build the AI team"). Roll such steps up into the bigger goal and give it all their message ids. Keep only goals the messages clearly support; if in doubt, leave it out. At most {max_goals} goals, the best supported first.
For each goal:
- title: short and specific, e.g. "Run the half marathon in January".
- area: one of self, spouse, kids, parents, work, friends, experiences, finances, health, home.
- type: habit (keep doing), milestone (reach once), experience (a trip or event).
- horizon: 3mo, 1yr, 3yr or 5yr: when it should happen.
- evidence: the ids of ALL the notes' messages that support it.
{seed}
Reply with JSON only: {"goals":[{"title":"...","area":"...","type":"...","horizon":"...","evidence":["M..."]}]}"""


def default_type(area):
    return {"experiences": "experience", "health": "habit"}.get(area, "milestone")


def tidy(goals, msg_ids):
    """Keep valid goals only: known area, real evidence ids, sensible defaults for the rest."""
    out, used = [], set()
    for g in goals:
        if not isinstance(g, dict) or g.get("area") not in AREAS:
            continue
        evidence = [m for m in dict.fromkeys(map(msg_id, g.get("evidence") or [])) if m in msg_ids][:MAX_EVIDENCE]
        title = clean(str(g.get("title") or ""))[:160]
        if not evidence or not title or (g["area"], title.lower()) in used:
            continue
        used.add((g["area"], title.lower()))
        out.append({
            "title": title,
            "area": g["area"],
            "type": g.get("type") if g.get("type") in TYPES else default_type(g["area"]),
            "horizon": g.get("horizon") if g.get("horizon") in HORIZONS else "1yr",
            "evidence": evidence,
        })
    # Best supported first (stable, so the model's order breaks ties), then the per-area and total caps.
    out.sort(key=lambda g: -len(g["evidence"]))
    kept, per_area = [], Counter()
    for g in out:
        if per_area[g["area"]] < PER_AREA:
            per_area[g["area"]] += 1
            kept.append(g)
    return kept[:MAX_GOALS]


def fallback(hints):
    """No merge reply: group hints that share an area and title, best supported first."""
    groups = defaultdict(list)
    for h in hints:
        groups[(h["area"], clean(h["goal"]).lower())].append(h["msg"])
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    return [{"title": title, "area": area, "evidence": ids} for (area, title), ids in ranked if len(ids) >= 2]


def own_turns(msgs):
    """Arjun's messages, each with the message just before it if someone else wrote that one."""
    keep = []
    for i, m in enumerate(msgs):
        if m["sender_id"] != ME:
            continue
        if i and msgs[i - 1]["sender_id"] != ME and (not keep or keep[-1] is not msgs[i - 1]):
            keep.append(msgs[i - 1])
        keep.append(m)
    return keep


def plan_requests(d, circles):
    """Pack each chat's turns into requests of about CHUNK_CHARS; big chats are split."""
    requests, cur, size = [], [], 0
    for cid in d.chats:
        turns = own_turns(d.by_chat.get(cid, []))
        if not turns:
            continue
        header = chat_header(d, cid, circles)
        for part in split_parts(format_lines(turns), CHUNK_CHARS):
            n = sum(len(x) + 1 for x in part) + len(header)
            if cur and size + n > CHUNK_CHARS:
                requests.append(cur)
                cur, size = [], 0
            cur += [header] + part
            size += n
    if cur:
        requests.append(cur)
    return requests


def find_hints(d, circles):
    """Step 1: every message that shows a goal. Returns raw hint dicts."""
    deadline = time.time() + TIME_BUDGET

    def ask(lines):
        if time.time() > deadline:
            return []
        reply = model.ask_json(HINT_SYSTEM, "\n".join(lines), max_tokens=6000) or {}
        hints = model.items(reply, "goals")
        for h in hints:  # models sometimes rename fields ("new_goal", "message_id"): accept the usual ones
            h["msg"] = msg_id(h.get("msg") or h.get("message_id") or h.get("id"))
            h["goal"] = h.get("goal") or h.get("new_goal") or h.get("title")
        return hints

    hints = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for found in pool.map(ask, plan_requests(d, circles)):
            hints += found
    return hints


def find_goals(d, circles):
    """The whole goals step. Returns (goals, hints); both empty without a model."""
    if not model.available() or not d.messages:
        return [], []
    hints = find_hints(d, circles)
    return merge(d, hints), hints


def merge(d, hints):
    """Step 2: one request merges the hints into at most MAX_GOALS goals."""
    msg = {m["id"]: m for m in d.messages}
    hints = [h for h in hints if h.get("msg") in msg and h.get("area") in AREAS and h.get("goal")]
    seen = set()
    hints = [h for h in hints if not (h["msg"] in seen or seen.add(h["msg"]))]
    if not hints:
        return []
    hints.sort(key=lambda h: msg[h["msg"]]["ts"])

    lines = []
    for h in hints:
        m = msg[h["msg"]]
        who = "Arjun" if m["sender_id"] == ME else (clean(m["sender_name"]).split() or ["?"])[0]
        day = dt.datetime.fromtimestamp(m["ts"], IST).strftime("%d %b %Y")
        lines.append(f"{m['id']} ({day}) [{h['area']}] {clean(h['goal'])[:80]} | {who}: {clean(m['body'])[:200]}")

    seed_goals = [g.get("title") for g in d.seed.get("goals") or [] if g.get("title")]
    seed_text = ("He already told us this goal; do not list it again: " + "; ".join(seed_goals)) if seed_goals else ""
    system = SYSTEM.replace("{max_goals}", str(MAX_GOALS)).replace("{seed}", seed_text)
    reply = model.ask_json(system, "\n".join(lines), max_tokens=6000)
    goals = tidy(model.items(reply, "goals"), set(msg))
    if not goals:
        goals = tidy(fallback(hints), set(msg))
    return goals


def hint_summary(hints):
    """For debugging: how many hints per area."""
    return dict(Counter(h.get("area") for h in hints))
