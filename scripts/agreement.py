"""Compute inter-annotator agreement for a pilot and log disagreements.

Usage:
    python scripts/agreement.py --dir data/processed/annotations/pilot_v1
Reads annotator_A.csv, annotator_B.csv and (optionally) pilot_key.jsonl from --dir,
prints Cohen's kappa (overall, per class, and with authorized+safely_derived merged),
the confusion matrix, rule-vs-human agreement, and writes disagreements.jsonl.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assentgate.annotation import LABELS, cohen_kappa, confusion, merge_allowed, per_class_kappa  # noqa: E402


def read_sheet(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {row["record_id"]: row for row in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    a = ap.parse_args()

    sheet_a = read_sheet(a.dir / "annotator_A.csv")
    sheet_b = read_sheet(a.dir / "annotator_B.csv")
    ids = [i for i in sheet_a if sheet_a[i]["label"].strip() and sheet_b.get(i, {}).get("label", "").strip()]
    bad = [(i, s[i]["label"]) for s in (sheet_a, sheet_b) for i in ids if s[i]["label"].strip() not in LABELS]
    if bad:
        sys.exit(f"Unknown labels (expected one of {LABELS}): {bad[:5]}")
    if not ids:
        sys.exit("No records labelled by both annotators yet.")

    la = [sheet_a[i]["label"].strip() for i in ids]
    lb = [sheet_b[i]["label"].strip() for i in ids]
    print(f"records labelled by both: {len(ids)} / {len(sheet_a)}")
    print(f"raw agreement: {sum(x == y for x, y in zip(la, lb)) / len(ids):.3f}")
    print(f"Cohen's kappa (4 classes): {cohen_kappa(la, lb):.3f}")
    print(f"Cohen's kappa (authorized+safely_derived merged): {cohen_kappa(merge_allowed(la), merge_allowed(lb)):.3f}")
    for k, v in per_class_kappa(la, lb).items():
        print(f"  {k:22s} kappa={v:.3f}  A={la.count(k)}  B={lb.count(k)}")
    print("confusion (rows A, cols B):")
    matrix = confusion(la, lb)
    print(" " * 23 + " ".join(f"{k[:8]:>8s}" for k in LABELS))
    for x in LABELS:
        print(f"  {x:21s}" + " ".join(f"{matrix[x][y]:8d}" for y in LABELS))

    key_path = a.dir / "pilot_key.jsonl"
    if key_path.exists():
        key = {r["record_id"]: r for r in map(json.loads, key_path.read_text(encoding="utf-8").splitlines())}
        agreed = [i for i, x, y in zip(ids, la, lb) if x == y]
        if agreed:
            hits = sum(key[i]["label"] == sheet_a[i]["label"].strip() for i in agreed)
            print(f"rule_v1 matches the agreed human label on {hits}/{len(agreed)} agreed records")

    with (a.dir / "disagreements.jsonl").open("w", encoding="utf-8") as f:
        for i, x, y in zip(ids, la, lb):
            if x != y:
                f.write(json.dumps({"record_id": i, "A": x, "B": y,
                                    "notes_A": sheet_a[i]["notes"], "notes_B": sheet_b[i]["notes"]}) + "\n")


if __name__ == "__main__":
    main()
