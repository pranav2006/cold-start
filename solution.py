#!/usr/bin/env python3
"""Cold Start solution.

    python3 solution.py <input_dir> <output.json>

Step 1: rules for people, organisations and chats (free).
Step 2: the model finds open loops, and code checks them (xpert/loops.py).
If no model is configured (no LLM_* variables), the run still finishes with the rule-based parts.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xpert.data import load  # noqa: E402
from xpert.loops import find_loops  # noqa: E402
from xpert.structure import build  # noqa: E402


def main(input_dir, output_path):
    started = time.time()
    d = load(input_dir)
    s = build(d)
    circles = {p["id"]: p["circle"] for p in s["people"]}
    loops = find_loops(d, circles)
    out = {
        "people": s["people"],
        "organisations": s["organisations"],
        "chats": s["chats"],
        "goals": [],
        "open_loops": loops,
    }
    print(f"done in {time.time() - started:.0f}s: {len(loops)} open loops", file=sys.stderr)
    Path(output_path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    # Keep the reasoning next to the output for debugging (tools/diagnose.py reads it). Never scored.
    Path(output_path).with_suffix(".notes.json").write_text(json.dumps(s["notes"], indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
