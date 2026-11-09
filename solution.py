#!/usr/bin/env python3
"""Cold Start solution.

    python3 solution.py <input_dir> <output.json>

Step 1: rules for people, organisations and chats (free).
Step 2: the model finds open loops, and code checks them (xpert/loops.py).
Step 3: the model corrects unsure circles from short profiles (xpert/people.py), and finds goals in
        Arjun's own messages (xpert/goals.py). Goals and loops run at the same time.
Step 4: where to help with each open loop (xpert/help.py): others' loops are left alone, Arjun's own
        go to the model.
If no model is configured (no LLM_* variables), the run still finishes with the rule-based parts.
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xpert.data import load  # noqa: E402
from xpert.goals import find_goals, hint_summary  # noqa: E402
from xpert.help import decide_help  # noqa: E402
from xpert.loops import find_loops  # noqa: E402
from xpert.people import refine_circles  # noqa: E402
from xpert.structure import build  # noqa: E402


def main(input_dir, output_path):
    started = time.time()
    d = load(input_dir)
    s = build(d, refine=lambda c, w, o: refine_circles(d, c, w, o))
    print(f"people done at {time.time() - started:.0f}s", file=sys.stderr)
    circles = {p["id"]: p["circle"] for p in s["people"]}
    with ThreadPoolExecutor(max_workers=2) as pool:  # independent steps: run them side by side
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
    # Keep the reasoning next to the output for debugging (tools/diagnose.py reads it). Never scored.
    Path(output_path).with_suffix(".notes.json").write_text(json.dumps(s["notes"], indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
