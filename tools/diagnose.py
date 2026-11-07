#!/usr/bin/env python3
"""Show what we got wrong against a labelled folder, so each fix targets a real mistake.

    python3 tools/diagnose.py data/dev out.json [circles|orgs|chats|loops]

Uses check.py's own scoring, so the numbers always agree with the official checker.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import check  # noqa: E402
from xpert.data import load  # noqa: E402


def main(folder, out_path, part=None):
    chats, messages, seed, ans = check.load_input(folder)
    out = json.loads(Path(out_path).read_text(encoding="utf-8"))
    notes_path = Path(out_path).with_suffix(".notes.json")
    notes = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else {}
    d = load(folder)
    s = check.score(out, ans, seed, messages)
    pts = {k: (round(v * check.POINTS[k], 1) if v is not None else None) for k, v in s.items()}
    print("scores:", pts, " total:", round(sum(v for v in pts.values() if v), 1))

    if part in (None, "circles"):
        seeded = set(seed.get("circles", {}))
        mine = {p["id"]: p["circle"] for p in out["people"]}
        rule = notes.get("circle_rule", {})
        wrong = [p for p in ans["people"] if p["id"] not in seeded and mine.get(p["id"]) != p["circle"]]
        print(f"\nCIRCLES wrong: {len(wrong)}")
        conf = Counter((p["circle"], mine.get(p["id"])) for p in wrong)
        print("  (truth, ours) counts:", dict(conf.most_common()))
        for p in sorted(wrong, key=lambda p: p["circle"]):
            print(f"  {p['id']:6} truth={p['circle']:10} ours={mine.get(p['id']):10} rule={rule.get(p['id'])!s:32} names={d.contact_names(p['id'])[:3]}")

    if part in (None, "orgs", "chats"):
        orgs, n_ours, n_yours = check.match_orgs(out, ans)
        print(f"\nORGS: answer has {n_ours}, we have {n_yours}")
        names = {o["id"]: o["name"] for o in out["organisations"]}
        for o in ans["organisations"]:
            m = orgs.get(o["id"])
            print(f"  {o['id']:4} {o['name']:14} -> {names.get(m[0]) if m else None!s:14} jaccard={m[1] if m else 0:.2f}")
        tie = {c["chat_id"]: c for c in out["chats"]}
        print("\nCHATS wrong:")
        for c in ans["chats"]:
            mine = tie.get(c["chat_id"], {})
            if "person" in c:
                ok = mine.get("person") == c["person"]
                truth = c["person"]
            else:
                ok = mine.get("organisation") is not None and orgs.get(c["organisation"], (None,))[0] == mine.get("organisation")
                truth = c["organisation"]
            if not ok:
                print(f"  {c['chat_id']:5} truth={truth:5} ours={mine.get('person') or mine.get('organisation')!s:6} name={d.chats[c['chat_id']]['name'][:50]}")


def show_loops(d, out, ans):
    msg = {m["id"]: m for m in d.messages}
    ours = {l["source_msg"]: l for l in out["open_loops"]}
    truth_src = {}
    for a in ans["open_loops"]:
        for s in a["source_msgs"]:
            truth_src.setdefault(s, a)

    def line(mid):
        m = msg[mid]
        return f"{m['chat_id']:5} {m['sender_name'][:12]:12}: {m['body'][:90]!r}"

    hit = [s for s in ours if s in truth_src]
    print(f"\nLOOPS: ours {len(ours)}, answer {len(ans['open_loops'])} ({len(truth_src)} distinct sources), matched {len(hit)}")
    print("  status on matches (truth -> ours):", dict(Counter((truth_src[s]["status"], ours[s]["status"]) for s in hit)))
    print("\n  MISSED (in answer, not ours):")
    for s, a in truth_src.items():
        if s not in ours:
            print(f"    [{a['status']:4}] {a['title'][:60]!s:60} | {line(s)}")
    print("\n  EXTRA (ours, not in answer):")
    for s, l in ours.items():
        if s not in truth_src:
            print(f"    [{l['status']:4}] {l['title'][:60]!s:60} | {line(s)}")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[3] == "loops":
        _, _, _, ans_ = check.load_input(sys.argv[1])
        show_loops(load(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")), ans_)
        sys.exit()
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
