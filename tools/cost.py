#!/usr/bin/env python3
"""Summarise usage.jsonl from the last run, and turn it into dollars per 1,000 messages.

    python3 tools/cost.py <input_dir> [input_price_per_million] [output_price_per_million]

Prices are the model's OpenRouter prices in dollars per million tokens (look them up on its page).
Delete usage.jsonl before a run so it only holds that run.
"""
import json
import sys
from pathlib import Path


def main(folder, price_in=None, price_out=None):
    usage = [json.loads(line) for line in open("usage.jsonl", encoding="utf-8") if line.strip()]
    n_msgs = sum(1 for line in open(Path(folder) / "messages.jsonl", encoding="utf-8") if line.strip())
    tin = sum(u.get("prompt_tokens") or 0 for u in usage)
    tout = sum(u.get("completion_tokens") or 0 for u in usage)
    print(f"model(s): {sorted({u['model'] for u in usage})}")
    print(f"calls: {len(usage)}   messages: {n_msgs}")
    print(f"tokens in: {tin:,}   out: {tout:,}")
    print(f"per 1,000 messages: {tin * 1000 / n_msgs:,.0f} in, {tout * 1000 / n_msgs:,.0f} out")
    if price_in is not None:
        dollars = tin / 1e6 * float(price_in) + tout / 1e6 * float(price_out or 0)
        print(f"cost: ${dollars:.4f} total, ${dollars * 1000 / n_msgs:.4f} per 1,000 messages")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
