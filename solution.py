#!/usr/bin/env python3
"""Cold Start solution.

    python3 solution.py <input_dir> <output.json>

Step 1 (now): rules only, for people, organisations and chats. No model calls, so no cost.
Later steps add the model for what rules can't see (goals, open loops, ambiguous circles).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xpert.data import load  # noqa: E402
from xpert.structure import build  # noqa: E402


def main(input_dir, output_path):
    d = load(input_dir)
    s = build(d)
    out = {
        "people": s["people"],
        "organisations": s["organisations"],
        "chats": s["chats"],
        "goals": [],
        "open_loops": [],
    }
    Path(output_path).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    # Keep the reasoning next to the output for debugging (tools/diagnose.py reads it). Never scored.
    Path(output_path).with_suffix(".notes.json").write_text(json.dumps(s["notes"], indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
