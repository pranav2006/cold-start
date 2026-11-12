"""Organisations, chat ties, circles and links, from rules alone (no model, so no cost).

The signals, strongest first:
  1. seed.json: what Arjun told us at signup (some circles, some organisation names).
  2. How Arjun saved contacts: "Ritu Acme", "Suresh Driver", "Kamla Mausi", "Anil sir tennis".
  3. Group names: "BrandCo: Acme - Ad Targeting" is a client group of Arjun's company Acme.
  4. Who sits in which groups: people in a company's internal groups are colleagues; a family
     group's members are relatives.
Anything these miss falls back to a safe default; later steps let the model correct it.
"""
import re
from collections import Counter, defaultdict

from .data import ME, clean, words

CIRCLES = {"spouse", "child", "parent", "in-law", "sibling", "relative", "friend", "colleague", "client", "vendor", "partner", "staff"}

# ---------------------------------------------------------------- vocabularies
# Words in a contact name or group name that say what someone is. Hindi/Bengali kinship terms are
# included because that is how people actually save relatives.
PARENT_WORDS = {"papa", "pappa", "mummy", "mumma", "mom", "dad", "mother", "father", "maa", "baba", "pitaji", "mataji"}
INLAW_WORDS = {"sasur", "saas", "sasu", "inlaw", "in-law", "jiju", "jijaji", "jija", "bhabhi", "sala", "saali", "nandoi", "devar"}
SIBLING_WORDS = {"bro", "brother", "sis", "sister", "dada", "didi", "bhai", "bhaiya"}
RELATIVE_WORDS = {
    "chacha", "chachi", "chachu", "mama", "mami", "mamaji", "masi", "mausi", "mausa", "bua", "phupha", "fufa",
    "nana", "nani", "dadi", "dadu", "thakuma", "kaka", "kaki", "jethu", "jethi", "pishi", "mashi", "uncle",
    "aunty", "auntie", "cousin", "bhanja", "bhanji", "bhatija", "bhatiji",
}
STAFF_WORDS = {
    "maid", "cook", "driver", "cleaner", "guard", "watchman", "darwan", "peon", "helper", "nanny", "aaya",
    "ayah", "mali", "gardener", "office", "home", "house", "chauffeur", "caretaker", "bai",
}
VENDOR_WORDS = {
    "sir", "madam", "coach", "trainer", "tutor", "teacher", "yoga", "gym", "doctor", "dr", "clinic", "ca",
    "lawyer", "advocate", "trading", "traders", "enterprises", "company", "co", "store", "stores", "shop",
    "salon", "tailor", "plumber", "electrician", "carpenter", "contractor", "agency", "services", "pickleball",
    "travels", "tours", "pharmacy", "chemist", "mart", "dealer", "supplier",
}
FAMILY_GROUP_WORDS = {"family", "siblings", "cousins", "sisters", "brothers", "parivar", "khandan", "bro", "sis"}
HOME_GROUP_WORDS = {"ghar", "kitchen", "maid", "maids", "cook", "driver", "drivers", "carpool", "society", "flat", "building", "staff", "household"}
# Business communities: these belong to Arjun's main company rather than to anyone in them.
WORK_COMMUNITY_WORDS = {"d2c", "d2b", "marketers", "ecommerce", "startups"}
# Words that mark a company's own team group ("Zest operations", "Zest team"): if the rest of the
# name is not a company we know, it is a company we did not know about.
TEAM_WORDS = {"operations", "ops", "team", "finance", "accounts", "internal", "core", "admin", "management"}
# Words that describe a group rather than name a client brand ("Acme Operations", "Acme: Finance").
GENERIC_GROUP_WORDS = {
    "operations", "ops", "team", "finance", "accounts", "ads", "ad", "targeting", "marketing", "community",
    "group", "internal", "core", "sales", "tech", "hr", "meta", "ai", "x", "and", "the", "updates", "connect",
    "circle", "official", "with", "of", "for", "s", "1", "2", "3", "4",
}


def acronym(name):
    """GreenLeaf -> gl, Blue River Labs -> brl. Empty if the name gives no clean acronym."""
    caps = re.findall(r"[A-Z]", name)
    parts = clean(name).split()
    if len(parts) > 1:
        return "".join(p[0] for p in parts if p[0].isalnum()).lower()
    if 2 <= len(caps) < len(name) and not name.isupper():
        return "".join(caps).lower()
    return ""


def aliases_for(name):
    """The ways an organisation's name shows up in chat and contact names."""
    out = {clean(name).lower(), re.sub(r"\W", "", name.lower())}
    if acronym(name):
        out.add(acronym(name))
    parts = clean(name).lower().split()
    if len(parts) > 1 and len(parts[0]) >= 4:  # "Brightside Design" also shows up as just "Brightside"
        out.add(parts[0])
    return {a for a in out if len(a) >= 2}


def mentions(text, alias_set):
    """True if any alias appears in text, as a whole word (short aliases) or a substring (long ones)."""
    low = clean(text).lower()
    toks = set(words(text))
    for a in alias_set:
        if a in toks or (len(a) >= 5 and a in low):
            return True
    return False


def last_name(name):
    parts = [p for p in re.findall(r"[A-Za-z]+", clean(name))]
    return parts[-1].lower() if len(parts) >= 2 else ""


# ---------------------------------------------------------------- organisations and chats

def new_org(oid, name, type_, side, role):
    return {"id": oid, "name": name, "type": type_, "side": side, "role": role, "aliases": aliases_for(name)}


def find_organisations(d):
    """Return (orgs, chat_org, client_groups).

    orgs: org id -> org dict. chat_org: chat id -> org id for chats we could tie to an organisation.
    client_groups: chat id -> brand words, for groups that are one client's channel with Arjun's company.
    """
    orgs, chat_org, client_groups = {}, {}, {}
    seed_names = d.seed.get("organisations") or []
    for i, name in enumerate(seed_names, 1):
        orgs[f"org{i}"] = new_org(f"org{i}", name, "company", "work", "owner")
    work_ids = list(orgs)

    groups = [c for c in d.chats.values() if c["is_group"]]

    # Rule A: a group whose name mentions one of Arjun's companies belongs to it.
    for c in groups:
        hits = [oid for oid in work_ids if mentions(c["name"], orgs[oid]["aliases"])]
        if hits:
            oid = hits[0]
            chat_org[c["chat_id"]] = oid
            brand = [w for w in words(c["name"]) if w not in GENERIC_GROUP_WORDS and not mentions(w, orgs[oid]["aliases"])]
            if brand:
                client_groups[c["chat_id"]] = brand

    # Rule A2: a team group ("Zest operations") of a company not in the seed is a new company.
    for c in groups:
        toks = words(c["name"])
        if c["chat_id"] in chat_org or not set(toks) & TEAM_WORDS:
            continue
        if set(toks) & (FAMILY_GROUP_WORDS | HOME_GROUP_WORDS | WORK_COMMUNITY_WORDS):
            continue
        brand = [w for w in re.findall(r"[^\W\d_]+", clean(c["name"])) if w.lower() not in GENERIC_GROUP_WORDS | TEAM_WORDS]
        if not brand:
            continue
        name = " ".join(brand)
        oid = next((o for o in orgs if orgs[o]["name"].lower() == name.lower() or mentions(name, orgs[o]["aliases"])), None)
        if not oid:
            oid = f"org{len(orgs) + 1}"
            orgs[oid] = new_org(oid, name, "company", "work", "other")
            work_ids.append(oid)
        chat_org[c["chat_id"]] = oid

    # Rule B: a group made up mostly of a company's internal team belongs to that company.
    internal_members = defaultdict(set)
    for cid, oid in chat_org.items():
        if cid not in client_groups:
            internal_members[oid].update(p for p in d.chats[cid]["participants"] if p != ME)
    for c in groups:
        if c["chat_id"] in chat_org:
            continue
        others = [p for p in c["participants"] if p != ME]
        if len(others) < 3:  # with one or two people, a single shared colleague proves nothing
            continue
        best = max(work_ids, key=lambda o: len(internal_members[o] & set(others)), default=None)
        if best and len(internal_members[best] & set(others)) >= 0.5 * len(others):
            chat_org[c["chat_id"]] = best

    # Rule C: business communities (D2C founder groups and the like) go to the main company,
    # the one with the most chats so far.
    if work_ids:
        main = Counter(chat_org.values()).most_common(1)[0][0] if chat_org else work_ids[0]
        for c in groups:
            if c["chat_id"] not in chat_org and set(words(c["name"])) & WORK_COMMUNITY_WORDS:
                chat_org[c["chat_id"]] = main

    # Rule D: household groups (kitchen, maids, carpool, society) belong to Home.
    home_chats = [c["chat_id"] for c in groups if c["chat_id"] not in chat_org and set(words(c["name"])) & HOME_GROUP_WORDS]
    if home_chats:
        orgs["home"] = new_org("home", "Home", "residence", "home", "owner")
        for cid in home_chats:
            chat_org[cid] = "home"
    return orgs, chat_org, client_groups


def tie_chats(d, chat_org):
    """Every chat goes to exactly one organisation or person.

    1:1 chats go to the other person. Groups we tied to an organisation go there. Any other group
    goes to its most active member besides Arjun: it costs nothing on the organisation score, while
    inventing an organisation for it would.
    """
    ties = []
    for cid, c in sorted(d.chats.items()):
        if cid in chat_org:
            ties.append({"chat_id": cid, "organisation": chat_org[cid]})
            continue
        others = [p for p in c["participants"] if p != ME]
        if not others:  # a chat with only Arjun in it: tie it to anyone so the output stays valid
            others = d.people[:1]
        senders = Counter(m["sender_id"] for m in d.by_chat.get(cid, []) if m["sender_id"] != ME)
        owner = max(others, key=lambda p: (senders.get(p, 0), -others.index(p)))
        ties.append({"chat_id": cid, "person": owner})
    return ties


# ---------------------------------------------------------------- circles and links

def name_words(d, pid):
    toks = set()
    for n in d.contact_names(pid):
        toks.update(words(n))
    return toks


def guess_circles(d, orgs, chat_org, client_groups):
    """Return (circles, why): person id -> circle, and person id -> the rule that decided it."""
    seeded = d.seed.get("circles") or {}
    me_last = last_name(d.me_name)
    circles, why = {}, {}

    def put(pid, circle, rule):
        if pid not in circles:
            circles[pid] = circle
            why[pid] = rule

    # Who sits where: internal company groups, client groups, family groups.
    internal, client_of = defaultdict(set), defaultdict(set)
    for cid, oid in chat_org.items():
        for p in d.chats[cid]["participants"]:
            if p == ME:
                continue
            if cid in client_groups:
                client_of[p].add(cid)
            elif orgs[oid]["type"] == "company":
                internal[p].add(oid)
    family_members = set()
    for c in d.chats.values():
        if c["is_group"] and (set(words(c["name"])) & FAMILY_GROUP_WORDS or (me_last and me_last in words(c["name"]))):
            family_members.update(p for p in c["participants"] if p != ME)
    brand_words = {w for ws in client_groups.values() for w in ws if len(w) >= 4}
    work_aliases = set().union(*(o["aliases"] for o in orgs.values() if o["type"] == "company")) if orgs else set()

    # Spouse: shares Arjun's surname and has by far the busiest 1:1 chat.
    dm_size = {p: len(d.by_chat.get(cid, [])) for p, cid in d.dm_of.items()}
    same_name = [p for p in d.people if me_last and any(last_name(n) == me_last for n in d.contact_names(p))]
    if same_name:
        top = max(same_name, key=lambda p: dm_size.get(p, 0))
        busiest = sorted(dm_size.values(), reverse=True)
        if dm_size.get(top, 0) and dm_size[top] == busiest[0] and (len(busiest) < 2 or busiest[0] >= 2 * busiest[1]):
            spouse = top
        else:
            spouse = None
    else:
        spouse = None

    for pid in d.people:
        if pid in seeded:
            put(pid, seeded[pid], "seed")
            continue
        toks = name_words(d, pid)
        names = d.contact_names(pid)
        if pid == spouse:
            put(pid, "spouse", "surname + busiest 1:1 chat")
        if toks & PARENT_WORDS:
            put(pid, "parent", "kinship word in contact name")
        if toks & INLAW_WORDS:
            put(pid, "in-law", "kinship word in contact name")
        if toks & RELATIVE_WORDS:
            put(pid, "relative", "kinship word in contact name")
        if any(mentions(n, work_aliases) for n in names) and not toks & STAFF_WORDS:
            put(pid, "colleague", "company name in contact name")
        if toks & STAFF_WORDS:
            put(pid, "staff", "staff word in contact name")
        if toks & brand_words:
            put(pid, "client", "client brand in contact name")
        if toks & VENDOR_WORDS:
            put(pid, "vendor", "vendor word in contact name")
        if me_last and any(last_name(n) == me_last for n in names):
            put(pid, "relative", "shares Arjun's surname")
        if internal[pid]:
            put(pid, "colleague", "in a company's internal group")
        if len(client_of[pid]) >= 2:
            put(pid, "colleague", "in several client groups")
        if len(client_of[pid]) == 1:
            put(pid, "client", "in one client group")
        if pid in family_members:
            put(pid, "relative", "in a family group")
        put(pid, "friend", "default")
    return circles, why


SMALL_GROUP = 6  # participants, Arjun included


def known_contact(d, pid):
    """Arjun has a 1:1 chat with them, told us about them, or saved them with a tag."""
    return pid in d.dm_of or pid in (d.seed.get("circles") or {}) or len(name_words(d, pid)) > 2


def deals_with(d, pid, chat_org):
    """People Arjun deals with himself: he has a 1:1 chat with them, told us about them, they speak
    in a small group of an organisation (a 4-person house-staff group), or he saved them with a tag
    and they are active. Someone who only sits in a big group is not linked to anything: linking
    every member of a 20-person ops group would swamp the organisation with strangers. A tagged
    name that almost never speaks is the same: measured on dev, none of them were linked."""
    if pid in d.dm_of or pid in (d.seed.get("circles") or {}):
        return True
    for cid in d.chats_of[pid]:
        c = d.chats[cid]
        if cid in chat_org and c["is_group"] and len(c["participants"]) <= SMALL_GROUP \
                and any(m["sender_id"] == pid for m in d.by_chat.get(cid, [])):
            return True
    return len(name_words(d, pid)) > 2 and d.sent[pid] >= max(30, len(d.messages) / 150)


def find_links(d, orgs, chat_org, circles):
    """Colleagues and partners run the companies they show up in; staff support Home (or the office)."""
    links = defaultdict(list)
    org_chats = defaultdict(set)
    for cid, oid in chat_org.items():
        org_chats[oid].add(cid)
    for pid in d.people:
        if not deals_with(d, pid, chat_org):
            continue
        circle = circles.get(pid)
        if circle in ("colleague", "partner"):
            for oid, o in orgs.items():
                if o["type"] != "company":
                    continue
                in_internal = any(cid in org_chats[oid] for cid in d.chats_of[pid] if d.chats[cid]["is_group"])
                named = any(mentions(n, o["aliases"]) for n in d.contact_names(pid))
                if in_internal or named:
                    links[pid].append({"organisation": oid, "link": "runs"})
        elif circle == "staff":
            if "office" in name_words(d, pid):
                if "office" not in orgs:
                    orgs["office"] = new_org("office", "Office", "workspace", "work", "owner")
                links[pid].append({"organisation": "office", "link": "supports"})
            else:
                if "home" not in orgs:
                    orgs["home"] = new_org("home", "Home", "residence", "home", "owner")
                links[pid].append({"organisation": "home", "link": "supports"})
    return links


def build(d, refine=None):
    """Run every rule and return the people, organisations and chats parts of the output, plus notes.

    refine(circles, why, org_names) -> (circles, why) lets the model correct circles before links
    are worked out, since links depend on circles."""
    orgs, chat_org, client_groups = find_organisations(d)
    circles, why = guess_circles(d, orgs, chat_org, client_groups)
    if refine:
        circles, why = refine(circles, why, [o["name"] for o in orgs.values() if o["type"] == "company"])
    links = find_links(d, orgs, chat_org, circles)
    people = []
    for pid in d.people:
        entry = {"id": pid, "circle": circles[pid]}
        if links.get(pid):
            entry["links"] = links[pid]
        people.append(entry)
    organisations = [{k: v for k, v in o.items() if k != "aliases"} for o in orgs.values()]
    return {
        "people": people,
        "organisations": organisations,
        "chats": tie_chats(d, chat_org),
        "notes": {"circle_rule": why, "client_groups": client_groups},
    }
