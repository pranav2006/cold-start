import re
from collections import Counter, defaultdict

from .data import ME, clean, words

CIRCLES = {"spouse", "child", "parent", "in-law", "sibling", "relative", "friend", "colleague", "client", "vendor", "partner", "staff"}

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
WORK_COMMUNITY_WORDS = {"d2c", "d2b", "marketers", "ecommerce", "startups"}
TEAM_WORDS = {"operations", "ops", "team", "finance", "accounts", "internal", "core", "admin", "management"}
GENERIC_GROUP_WORDS = {
    "operations", "ops", "team", "finance", "accounts", "ads", "ad", "targeting", "marketing", "community",
    "group", "internal", "core", "sales", "tech", "hr", "meta", "ai", "x", "and", "the", "updates", "connect",
    "circle", "official", "with", "of", "for", "s", "1", "2", "3", "4",
}


def acronym(name):
    caps = re.findall(r"[A-Z]", name)
    parts = clean(name).split()
    if len(parts) > 1:
        return "".join(p[0] for p in parts if p[0].isalnum()).lower()
    if 2 <= len(caps) < len(name) and not name.isupper():
        return "".join(caps).lower()
    return ""


def aliases_for(name):
    out = {clean(name).lower(), re.sub(r"\W", "", name.lower())}
    if acronym(name):
        out.add(acronym(name))
    parts = clean(name).lower().split()
    if len(parts) > 1 and len(parts[0]) >= 4:
        out.add(parts[0])
    return {a for a in out if len(a) >= 2}


def mentions(text, alias_set):
    low = clean(text).lower()
    toks = set(words(text))
    for a in alias_set:
        if a in toks or (len(a) >= 5 and a in low):
            return True
    return False


def last_name(name):
    parts = [p for p in re.findall(r"[A-Za-z]+", clean(name))]
    return parts[-1].lower() if len(parts) >= 2 else ""


def new_org(oid, name, type_, side, role):
    return {"id": oid, "name": name, "type": type_, "side": side, "role": role, "aliases": aliases_for(name)}


def find_organisations(d):
    orgs, chat_org, client_groups = {}, {}, {}
    seed_names = d.seed.get("organisations") or []
    for i, name in enumerate(seed_names, 1):
        orgs[f"org{i}"] = new_org(f"org{i}", name, "company", "work", "owner")
    work_ids = list(orgs)

    groups = [c for c in d.chats.values() if c["is_group"]]

    for c in groups:
        hits = [oid for oid in work_ids if mentions(c["name"], orgs[oid]["aliases"])]
        if hits:
            oid = hits[0]
            chat_org[c["chat_id"]] = oid
            brand = [w for w in words(c["name"]) if w not in GENERIC_GROUP_WORDS and not mentions(w, orgs[oid]["aliases"])]
            if brand:
                client_groups[c["chat_id"]] = brand

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

    internal_members = defaultdict(set)
    for cid, oid in chat_org.items():
        if cid not in client_groups:
            internal_members[oid].update(p for p in d.chats[cid]["participants"] if p != ME)
    for c in groups:
        if c["chat_id"] in chat_org:
            continue
        others = [p for p in c["participants"] if p != ME]
        if len(others) < 3:
            continue
        best = max(work_ids, key=lambda o: len(internal_members[o] & set(others)), default=None)
        if best and len(internal_members[best] & set(others)) >= 0.5 * len(others):
            chat_org[c["chat_id"]] = best

    if work_ids:
        main = Counter(chat_org.values()).most_common(1)[0][0] if chat_org else work_ids[0]
        for c in groups:
            if c["chat_id"] not in chat_org and set(words(c["name"])) & WORK_COMMUNITY_WORDS:
                chat_org[c["chat_id"]] = main

    home_chats = [c["chat_id"] for c in groups if c["chat_id"] not in chat_org and set(words(c["name"])) & HOME_GROUP_WORDS]
    if home_chats:
        orgs["home"] = new_org("home", "Home", "residence", "home", "owner")
        for cid in home_chats:
            chat_org[cid] = "home"
    return orgs, chat_org, client_groups


def tie_chats(d, chat_org):
    ties = []
    for cid, c in sorted(d.chats.items()):
        if cid in chat_org:
            ties.append({"chat_id": cid, "organisation": chat_org[cid]})
            continue
        others = [p for p in c["participants"] if p != ME]
        if not others:
            others = d.people[:1]
        senders = Counter(m["sender_id"] for m in d.by_chat.get(cid, []) if m["sender_id"] != ME)
        owner = max(others, key=lambda p: (senders.get(p, 0), -others.index(p)))
        ties.append({"chat_id": cid, "person": owner})
    return ties


def name_words(d, pid):
    toks = set()
    for n in d.contact_names(pid):
        toks.update(words(n))
    return toks


def guess_circles(d, orgs, chat_org, client_groups):
    seeded = d.seed.get("circles") or {}
    me_last = last_name(d.me_name)
    circles, why = {}, {}

    def put(pid, circle, rule):
        if pid not in circles:
            circles[pid] = circle
            why[pid] = rule

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


SMALL_GROUP = 6


def known_contact(d, pid):
    return pid in d.dm_of or pid in (d.seed.get("circles") or {}) or len(name_words(d, pid)) > 2


def deals_with(d, pid, chat_org):
    if pid in d.dm_of or pid in (d.seed.get("circles") or {}):
        return True
    for cid in d.chats_of[pid]:
        c = d.chats[cid]
        if cid in chat_org and c["is_group"] and len(c["participants"]) <= SMALL_GROUP \
                and any(m["sender_id"] == pid for m in d.by_chat.get(cid, [])):
            return True
    return len(name_words(d, pid)) > 2 and d.sent[pid] >= max(30, len(d.messages) / 150)


def find_links(d, orgs, chat_org, circles):
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
