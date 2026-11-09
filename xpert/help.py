"""Where to help: for each loop still open at the end, assist, remind or leave.

The scoring decides the policy. A right action earns 1. Saying assist or remind where Arjun wanted to
be left alone costs 1. Mixing up assist and remind costs nothing, and "leave" can never lose a point.
So:
  - Loops someone else owns get "leave" in code, no model needed ("someone else's" is one of the
    brief's own reasons, and a nudge about another person's task is what annoys people).
  - Arjun's own open loops go to the model in batches, with the message that raised them and what
    came right after, and a prompt that only says assist or remind when it is clearly wanted.
  - If the model fails, his own loops fall back to "remind": the most common right answer for them.
"""
import datetime as dt
import os
from concurrent.futures import ThreadPoolExecutor

from . import model
from .data import ME, clean

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BATCH = int(os.environ.get("HELP_BATCH", "25"))
WORKERS = int(os.environ.get("LOOPS_WORKERS", "8"))
LEAVE_REASONS = ("someone else's", "too personal", "would annoy him")
CONTEXT_AFTER = 2

SYSTEM = """You are Arjun's assistant. You get loops (tasks, promises, questions, deadlines) that Arjun himself still owes at the end of his WhatsApp data, each with the message that raised it and the messages right after. Decide how you help with each, answering these questions in order and stopping at the first yes:

1. Is it about feelings: comforting someone, a quarrel, his marriage, someone's illness or grief? -> leave, reason "too personal".
2. Is it a throwaway social line ("will call you later", "see you there")? -> leave, reason "would annoy him".
3. Is the next step his own decision, a call, a visit, being somewhere, a payment from his account, or work he does himself (writing a deck, reviewing a document)? -> remind, with remind_at shortly before it is due, in waking hours.
4. Otherwise, can you prepare it with his phone: search and compare options, draft the message or reply, fill in a booking, order or form for him to confirm? -> assist, with next_step (one line) and needs_yes (true if it sends, pays, books or orders; false if it only prepares).
5. Anything else -> remind.

Examples (different from your loops): train tickets for a cousin's wedding -> assist (find trains, fill the booking). A report he promised to send by Thursday -> remind (his own work). Whether to hire a candidate -> remind (his decision). Ordering a water purifier his wife asked for -> assist. Travel, purchases, household staff and their pay, a child's school matter, a gift or a plan to meet friends are NOT "too personal": those are everyday logistics he wants help with. Most of Arjun's own loops are assist or remind; leave is rare.
The data ends at {end}; reminders must be after that.
For each loop first write "step": the number (1-5) of the first question above that is a yes, then the action that question gives.
Reply with JSON only: {"help":[{"n":1,"step":4,"action":"assist","next_step":"...","needs_yes":true},{"n":2,"step":3,"action":"remind","remind_at":"YYYY-MM-DDTHH:MM:00+05:30"},{"n":3,"step":1,"action":"leave","reason":"too personal"}]}"""


def iso_after(value, end_ts):
    """value if it is a valid ISO time after the end of the data, else None."""
    try:
        t = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=IST)
    return t.isoformat() if t.timestamp() > end_ts else None


def default_remind(due, end_ts):
    """9 am on the due date if that is still ahead, else 9 am the day after the data ends."""
    end_day = dt.datetime.fromtimestamp(end_ts, IST).date()
    day = end_day + dt.timedelta(days=1)
    if due:
        try:
            day = max(dt.date.fromisoformat(due), day)
        except ValueError:
            pass
    return f"{day.isoformat()}T09:00:00+05:30"


def tidy(h, loop, end_ts):
    h = h if isinstance(h, dict) else {}
    action = h.get("action")
    if action == "assist":
        return {"action": "assist", "next_step": clean(str(h.get("next_step") or "Draft the next message for Arjun"))[:200],
                "needs_yes": bool(h.get("needs_yes", True))}
    if action == "leave":
        reason = h.get("reason") if h.get("reason") in LEAVE_REASONS else "would annoy him"
        return {"action": "leave", "reason": reason}
    return {"action": "remind", "remind_at": iso_after(h.get("remind_at"), end_ts) or default_remind(loop.get("due"), end_ts)}


def describe(d, n, loop, circles):
    """One loop as the model sees it: what, where, due, the source message and what followed."""
    chat = d.chats[loop["chat_id"]]
    msgs = d.by_chat[loop["chat_id"]]
    i = next((k for k, m in enumerate(msgs) if m["id"] == loop["source_msg"]), 0)
    if chat["is_group"]:
        where = f"group \"{clean(chat['name'])}\""
    else:
        other = next((p for p in chat["participants"] if p != ME), None)
        where = f"1:1 with {clean(chat['name']) or d.display_name(other)} ({circles.get(other, 'unknown')})"
    lines = [f"[{n}] {loop['title']} | type {loop['type']} | {where} | due {loop.get('due') or 'not stated'}"]
    for m in msgs[i:i + 1 + CONTEXT_AFTER]:
        who = "Arjun" if m["sender_id"] == ME else (clean(m["sender_name"]).split() or ["?"])[0]
        when = dt.datetime.fromtimestamp(m["ts"], IST).strftime("%d %b %H:%M")
        lines.append(f"    {when} {who}: {clean(m['body']).replace(chr(10), ' / ')[:200]}")
    return "\n".join(lines)


def decide_help(d, loops, circles):
    """Fill in help for every open loop, in place. Others' loops: leave. Arjun's: the model decides."""
    if not d.messages:
        return loops
    end_ts = d.messages[-1]["ts"]
    mine = []
    for loop in loops:
        if loop["status"] != "open":
            loop["help"] = None
        elif loop["owner"] != ME:
            loop["help"] = {"action": "leave", "reason": "someone else's"}
        else:
            loop["help"] = tidy({}, loop, end_ts)  # fallback until the model answers
            mine.append(loop)
    if not mine or not model.available():
        return loops

    end = dt.datetime.fromtimestamp(end_ts, IST).strftime("%a %d %b %Y %H:%M")
    system = SYSTEM.replace("{end}", end)
    batches = [mine[i:i + BATCH] for i in range(0, len(mine), BATCH)]

    def ask(batch):
        text = "\n".join(describe(d, n, loop, circles) for n, loop in enumerate(batch, 1))
        return model.ask_json(system, text, max_tokens=6000) or {}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for batch, reply in zip(batches, pool.map(ask, batches)):
            for h in model.items(reply, "help"):
                try:
                    n = int(h.get("n"))
                except (TypeError, ValueError, AttributeError):
                    continue
                if 1 <= n <= len(batch):
                    batch[n - 1]["help"] = tidy(h, batch[n - 1], end_ts)
    return loops
