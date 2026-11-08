"""Circles, second opinion: the model reads a short profile of each person the rules were unsure about.

Rules (structure.py) are good when Arjun saved a contact with a tag ("Kamla Mausi", "Suresh Driver"),
but most contacts are just a name, and the rules then fall back to a guess ("friend", or "client"
because someone sits in one client group). A human would read a few of their messages and know at
once. That is what this step does, cheaply:

  - Only people Arjun deals with (a 1:1 chat, a tagged name, or active in a small group) get a profile.
  - A profile is a few hundred characters: saved names, shared groups, the rule's guess, and a sample
    of their 1:1 chat (or of their group messages if there is no 1:1 chat).
  - Profiles are batched, about 12 per request.
  - Strong rules (seed, a kinship word in the name, the spouse rule) are never overridden.
"""
import os
from concurrent.futures import ThreadPoolExecutor

from . import model
from .data import ME, clean
from .structure import CIRCLES, deals_with, last_name

BATCH = int(os.environ.get("PEOPLE_BATCH", "12"))
WORKERS = int(os.environ.get("LOOPS_WORKERS", "6"))
STRONG_RULES = {"seed", "kinship word in contact name", "surname + busiest 1:1 chat"}
LINE_CHARS = 140

SYSTEM = """You help sort the contacts of Arjun, an Indian founder, into circles. For each person you get the names his phone shows for them, the groups they share with him, a first guess from simple rules, and a sample of their messages.

Circles (pick exactly one):
- spouse: his wife or husband.
- child: his son or daughter.
- parent: his mother or father.
- in-law: his spouse's parents (and sometimes spouse's siblings' spouses).
- sibling: his brother or sister (or his spouse's sibling, if they treat each other as siblings).
- relative: any other family: cousins, uncles, aunts, nieces, the wider family.
- friend: personal friends, school or college mates, neighbours, people he socialises with.
- colleague: people who work in or for his own companies ({orgs}): employees, team members, co-founders.
- partner: business partners and peers his companies work with as equals: investors, advisers, people at partner firms.
- client: customers of his companies: brands and businesses his companies sell to or run work for.
- vendor: people or businesses who sell him a service or goods: shops, tutors, coaches, doctors, contractors, agencies.
- staff: people employed by his household or office: maid, cook, driver, guard, office help.

Judge from what they talk about and how they talk to each other. Indian usage: "bhaiya", "didi", "uncle", "aunty" are also used for non-family elders, so a respectful word alone does not make a relative.
Family comes first: someone who is family stays family even if they also work with or buy from his companies.
Sharing his surname makes family likely but not certain; then decide which kind: a parent runs the household he lives in and talks about home rules, festivals and fasts, his spouse and kids as family; a sibling is a peer who asks or does favours as an equal; otherwise relative.
If the messages say nothing useful, keep the rule's guess.
Reply with JSON only: {"people":[{"id":"P..","circle":"..."}]}"""


def profile(d, pid, rule_circle, rule_why, sample_lines=10):
    names = list(dict.fromkeys(d.contact_names(pid)))[:3]
    groups = sorted((c for c in d.chats_of[pid] if d.chats[c]["is_group"]), key=lambda c: len(d.chats[c]["participants"]))
    head = [f"## {pid} | saved as: {' / '.join(names) or '?'} | rule guess: {rule_circle} ({rule_why})"]
    if last_name(d.me_name) and any(last_name(n) == last_name(d.me_name) for n in names):
        head.append("shares Arjun's surname")
    if groups:
        head.append("groups with Arjun: " + "; ".join(f"\"{clean(d.chats[c]['name'])[:40]}\" ({len(d.chats[c]['participants'])})" for c in groups[:5]))

    def fmt(m, where=""):
        who = "Arjun" if m["sender_id"] == ME else (clean(m["sender_name"]).split() or ["?"])[0]
        body = clean(m["body"]).replace("\n", " / ")[:LINE_CHARS]
        return f"  {where}{who}: {body}"

    if pid in d.dm_of:
        msgs = [m for m in d.by_chat[d.dm_of[pid]] if m["body"].strip() and m["body"] != "[photo]"]
        pick = msgs if len(msgs) <= sample_lines else msgs[: sample_lines - 4] + msgs[-4:]
        head.append(f"1:1 chat ({len(d.by_chat[d.dm_of[pid]])} messages):")
        head += [fmt(m) for m in pick]
    else:
        own = [m for c in groups for m in d.by_chat.get(c, []) if m["sender_id"] == pid and m["body"].strip()][:6]
        if own:
            head.append("their messages in groups:")
            head += [fmt(m, f"[{clean(d.chats[m['chat_id']]['name'])[:20]}] ") for m in own]
    return "\n".join(head)


def wanted(d, pid):
    """People worth a profile: Arjun deals with them directly, or they talk in a small group."""
    if pid in (d.seed.get("circles") or {}):
        return False
    small_active = d.sent[pid] >= 3 and any(
        d.chats[c]["is_group"] and len(d.chats[c]["participants"]) <= 8 for c in d.chats_of[pid])
    return deals_with(d, pid) or small_active


def refine_circles(d, circles, why, org_names):
    """Return (circles, why) with the model's corrections applied. Unchanged if no model is set."""
    if not model.available():
        return circles, why
    todo = [p for p in d.people if wanted(d, p) and why.get(p) not in STRONG_RULES]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    system = SYSTEM.replace("{orgs}", ", ".join(org_names) or "his companies")

    def ask(batch):
        text = "\n\n".join(profile(d, p, circles[p], why[p]) for p in batch)
        return model.ask_json(system, text, max_tokens=4000) or {}

    circles, why = dict(circles), dict(why)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for batch, reply in zip(batches, pool.map(ask, batches)):
            for item in reply.get("people") or []:
                if not isinstance(item, dict):
                    continue
                pid, circle = item.get("id"), item.get("circle")
                if pid in batch and circle in CIRCLES and circle != circles[pid]:
                    why[pid] = f"model (rules said {circles[pid]}: {why[pid]})"
                    circles[pid] = circle
    return circles, why
