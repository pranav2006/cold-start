#!/usr/bin/env python3
"""Turn a Claude Code session transcript (.jsonl) into a readable, data-free Markdown log for ai-logs/.

    python3 tools/export_ai_log.py <transcript.jsonl> <data_dir> <out.md>

What it keeps: the user's messages, the assistant's replies, and one line per tool call (its short
description, e.g. "ran: Score the rules-only output on dev"). What it drops: tool outputs, tool inputs,
hidden reasoning, and system notes, because that is where raw chat data and file contents sit.

Then it redacts, because the challenge data must not be shared or committed:
  - every word that occurs in a contact or chat name in <data_dir>, unless the same word also occurs in
    the starter kit or our own code (so "Home", "Office", "Arjun" from the brief stay; real-looking
    names become [name]);
  - any quoted text that matches a message in <data_dir> -> [message text];
  - e-mail addresses and anything shaped like an API key.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT_FILES = ["README.md", "WRITEUP.md", "check.py", "schema.json", "llm.py", "solution.py", "run.sh"]


def safe_words():
    """Words that appear in the kit or our code: generic, or already public in the brief."""
    text = " ".join((ROOT / f).read_text(encoding="utf-8") for f in KIT_FILES if (ROOT / f).exists())
    for f in list((ROOT / "xpert").glob("*.py")) + list((ROOT / "example").glob("*")):
        if f.is_file():
            text += " " + f.read_text(encoding="utf-8")
    return {w.lower() for w in re.findall(r"[^\W\d_]+", text)}


def data_vocab(data_dir):
    """Name words from the data (senders and chat names) that are not safe words, and all message bodies."""
    safe = safe_words()
    names, bodies = set(), []
    for line in open(Path(data_dir) / "chats.jsonl", encoding="utf-8"):
        names.update(re.findall(r"[^\W\d_]+", json.loads(line)["name"]))
    for line in open(Path(data_dir) / "messages.jsonl", encoding="utf-8"):
        m = json.loads(line)
        names.update(re.findall(r"[^\W\d_]+", m["sender_name"]))
        bodies.append(m["body"].lower())
    names = {n.lower() for n in names if len(n) >= 3 and n.lower() not in safe}
    return names, "\n".join(bodies)


def redact(text, names, bodies):
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", text)
    text = re.sub(r"\b(AIza[\w-]{20,}|sk-[\w-]{20,}|sk-or-[\w-]{20,})\b", "[secret]", text)

    def quote(m):
        inner = m.group(2)
        return f"{m.group(1)}[message text]{m.group(1)}" if len(inner) >= 8 and inner.lower() in bodies else m.group(0)

    text = re.sub(r"([\"“'])([^\"”'\n]{8,200})[\"”']", quote, text)
    return re.sub(r"[^\W\d_]+", lambda m: "[name]" if m.group(0).lower() in names else m.group(0), text)


def strip_noise(text):
    """Remove harness wrappers (system reminders, IDE context) from a user message."""
    text = re.sub(r"<(system-reminder|ide_selection|ide_opened_file)[^>]*>.*?</\1[^>]*>", "", text, flags=re.S)
    return text.strip()


def main(transcript, data_dir, out):
    names, bodies = data_vocab(data_dir)
    lines = ["# AI log: building the Cold Start solution with Claude Code", "",
             "Exported with `tools/export_ai_log.py`: the user's messages, Claude's replies, and one line per "
             "tool call. Tool outputs are left out and names and message text from the challenge data are "
             "redacted, because the data must not be shared.", ""]
    last_tool = False
    for raw in open(transcript, encoding="utf-8"):
        o = json.loads(raw)
        if o.get("type") not in ("user", "assistant"):
            continue
        msg = o.get("message") or {}
        content = msg.get("content")
        if o.get("isCompactSummary"):
            lines += ["", "> *(The conversation was long; earlier context was summarised for the model here.)*", ""]
            continue
        if o.get("isMeta"):
            continue
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for b in blocks:
            if b.get("type") == "text" and b.get("text", "").strip():
                text = b["text"] if o["type"] == "assistant" else strip_noise(b["text"])
                if not text or text.startswith("[Request interrupted"):
                    continue
                who = "**User**" if o["type"] == "user" else "**Claude**"
                lines += ["", f"### {who}", "", redact(text, names, bodies), ""]
                last_tool = False
            elif b.get("type") == "tool_use" and o["type"] == "assistant":
                inp = b.get("input") or {}
                what = inp.get("description") or inp.get("file_path") or inp.get("query") or inp.get("pattern") or ""
                what = Path(what).name if b["name"] in ("Read", "Write", "Edit") and what else what
                if not last_tool:
                    lines.append("")
                lines.append(f"- *{b['name']}*: {redact(str(what)[:160], names, bodies)}")
                last_tool = True
    Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(lines)} lines, {len(names)} name words redacted when present")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
