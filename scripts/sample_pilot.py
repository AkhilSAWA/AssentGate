"""Sample a blinded annotation pilot from the AgentDojo dataset.

Draws side-effecting calls from the TRAIN split only (the test set gets its own
double annotation later), stratified by rule label, at most one record per
(group, tool, rule label) so the pilot isn't dominated by one task. Annotators
never see the rule label.

Writes to --out-dir:
  annotator_A.csv, annotator_B.csv   identical blank sheets, one per annotator
  pilot_key.jsonl                    record ids with rule labels (don't show annotators)

Usage:
    python scripts/sample_pilot.py --data data/processed/agentdojo_workspace.jsonl \
        --out-dir data/processed/annotations/pilot_v1 --n 100 --seed 0
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

READ_ONLY_PREFIXES = ("get_", "search_", "list_")
LABELS = ("authorized", "safely_derived", "confirmation_required", "blocked")
SHEET_COLUMNS = [
    "record_id", "user_request", "tool", "args", "evidence", "recent_tools",
    "label", "unresolved_fields", "prohibited_fields", "notes",
]


def load_candidates(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["split"] == "train" and not r["tool"].startswith(READ_ONLY_PREFIXES):
                out.append(r)
    return out


def sample(records: list[dict], n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    by_label, seen = defaultdict(list), set()
    shuffled = records[:]
    rng.shuffle(shuffled)
    for r in shuffled:
        key = (r["group_id"], r["tool"], r["label"])
        if key not in seen:
            seen.add(key)
            by_label[r["label"]].append(r)

    # Round-robin over labels so rare classes get an equal share while they last.
    picked, queues = [], [by_label[k] for k in LABELS if by_label[k]]
    while len(picked) < n and any(queues):
        for q in queues:
            if q and len(picked) < n:
                picked.append(q.pop())
    rng.shuffle(picked)
    return picked


def sheet_row(r: dict) -> dict:
    return {
        "record_id": r["record_id"],
        "user_request": r["user_request"],
        "tool": r["tool"],
        "args": json.dumps(r["args"], ensure_ascii=False),
        "evidence": json.dumps(r["evidence"]),
        "recent_tools": " > ".join(h["tool"] for h in r["history"]),
        "label": "", "unresolved_fields": "", "prohibited_fields": "", "notes": "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    picked = sample(load_candidates(a.data), a.n, a.seed)
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("annotator_A.csv", "annotator_B.csv"):
        # utf-8-sig so Excel opens non-ASCII text correctly.
        with (a.out_dir / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
            w.writeheader()
            w.writerows(sheet_row(r) for r in picked)
    with (a.out_dir / "pilot_key.jsonl").open("w", encoding="utf-8") as f:
        for r in picked:
            key = {k: r[k] for k in ("record_id", "group_id", "tool", "label", "needs_review",
                                     "unresolved_fields", "prohibited_fields", "source_file")}
            f.write(json.dumps(key) + "\n")

    counts = {k: sum(r["label"] == k for r in picked) for k in LABELS}
    print(f"sampled {len(picked)} records from {len({r['group_id'] for r in picked})} task groups")
    print(f"rule labels (hidden from annotators): {counts}")


if __name__ == "__main__":
    main()
