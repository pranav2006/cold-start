"""Load an input folder and build the lookups every later step uses.

Nothing here guesses anything: it only reads and indexes the data.
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ME = "ME"


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def clean(text):
    """Drop invisible marks (WhatsApp exports are full of them) and squeeze spaces."""
    text = "".join(ch for ch in (text or "") if unicodedata.category(ch) not in ("Cf", "Mn", "Cc"))
    return re.sub(r"\s+", " ", text).strip()


def msg_id(value):
    """A message id from a model reply. Models sometimes copy the whole line ("M00391 14:57 Priya: ..."),
    so take the first token only."""
    parts = str(value or "").replace(",", " ").split()
    return parts[0] if parts else None


def words(text):
    """Lowercase word tokens, keeping letters/digits from any script."""
    return re.findall(r"\w+", clean(text).lower())


@dataclass
class Data:
    chats: dict  # chat_id -> chat
    messages: list  # in time order
    seed: dict
    by_chat: dict = field(default_factory=dict)  # chat_id -> [messages]
    people: list = field(default_factory=list)  # every person id except ME
    chats_of: dict = field(default_factory=dict)  # person id -> [chat ids]
    dm_of: dict = field(default_factory=dict)  # person id -> their 1:1 chat id
    names: dict = field(default_factory=dict)  # person id -> Counter of every name we saw for them
    sent: Counter = field(default_factory=Counter)  # person id -> messages sent
    me_name: str = ""

    def contact_names(self, pid):
        """Every name Arjun's phone shows for this person: the 1:1 chat title and their sender names."""
        out = list(self.names.get(pid, {}))
        if pid in self.dm_of:
            out.append(self.chats[self.dm_of[pid]]["name"])
        return [clean(n) for n in out if clean(n)]

    def display_name(self, pid):
        names = self.contact_names(pid)
        return names[0] if names else pid


def load(folder):
    folder = Path(folder)
    chats = {c["chat_id"]: c for c in read_jsonl(folder / "chats.jsonl")}
    messages = sorted(read_jsonl(folder / "messages.jsonl"), key=lambda m: (m["ts"], m["id"]))
    seed_path = folder / "seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.exists() else {}
    d = Data(chats=chats, messages=messages, seed=seed)

    d.by_chat = defaultdict(list)
    d.names = defaultdict(Counter)
    for m in messages:
        d.by_chat[m["chat_id"]].append(m)
        d.names[m["sender_id"]][m["sender_name"]] += 1
        d.sent[m["sender_id"]] += 1

    d.chats_of = defaultdict(list)
    for c in chats.values():
        for p in c["participants"]:
            d.chats_of[p].append(c["chat_id"])
        if not c["is_group"]:
            others = [p for p in c["participants"] if p != ME]
            if others:
                d.dm_of.setdefault(others[0], c["chat_id"])

    d.people = sorted({p for c in chats.values() for p in c["participants"]} - {ME})
    me_names = d.names.get(ME)
    d.me_name = clean(me_names.most_common(1)[0][0]) if me_names else ""
    return d
