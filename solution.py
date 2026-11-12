#!/usr/bin/env python3
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xpert.data import load
from xpert.goals import find_goals, hint_summary
from xpert.help import decide_help
from xpert.loops import find_loops
from xpert.people import refine_circles
from xpert.structure import build


def main(input_dir, output_path):
    started = time.time()
    d = load(input_dir)
    s = build(d, refine=lambda c, w, o: refine_circles(d, c, w, o))
    print(f"people done at {time.time() - started:.0f}s", file=sys.stderr)
    circles = {p["id"]: p["circle"] for p in s["people"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        goals_job = pool.submit(find_goals, d, circles)
        loops = find_loops(d, circles)
        goals, hints = goals_job.result()
    decide_help(d, loops, circles)
    s["notes"]["goal_hints"] = hints
    out = {
        "people": s["people"],
        "organisations": s["organisations"],
        "chats": s["chats"],
        "goals": goals,
        "open_loops": loops,
    }
    print(f"done in {time.time() - started:.0f}s: {len(loops)} open loops, {len(goals)} goals "
          f"from {len(hints)} hints {hint_summary(hints)}", file=sys.stderr)
    Path(output_path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(output_path).with_suffix(".notes.json").write_text(json.dumps(s["notes"], indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit('usage: python3 solution.py <input_dir> <output.json>')
    main(sys.argv[1], sys.argv[2])
