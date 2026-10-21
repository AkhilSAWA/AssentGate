"""Summarise scripted ground-truth runs from scripts/run_ground_truth_eval.py.

An attacked (user task, injection task) pair counts as FEASIBLE only if the scripted
attack succeeds with no gate (`no_gate.jsonl`). Attack success rates are reported over
feasible pairs only; some AgentDojo v1.2 injection tasks have an empty ground truth, so the
scripted agent never attempts them.

Benign prompts are confirmation questions asked during benign runs. Every benign
ground-truth call is one the user's task needs, so each one is a prompt the user
has to answer for an action they wanted.

Usage:
    python scripts/summarise_ground_truth_eval.py --dir results/ground_truth_v1.2.2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ORDER = ["no_gate", "tool_rules", "confirm_all", "confirm_all_no"]


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def rate(rows, key):
    return sum(bool(r[key]) for r in rows) / len(rows) if rows else None


def mean(rows, key):
    return sum(r[key] for r in rows) / len(rows) if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    a = ap.parse_args()

    runs = {p.stem: load(p) for p in sorted(a.dir.glob("*.jsonl"))}
    if "no_gate" not in runs:
        raise SystemExit("no_gate.jsonl is required to decide which attacks are feasible.")
    feasible = {(r["user_task"], r["injection_task"]) for r in runs["no_gate"]
                if r.get("injection_task") and r.get("attack_success")}
    all_pairs = {(r["user_task"], r["injection_task"]) for r in runs["no_gate"] if r.get("injection_task")}

    info_path = a.dir / "run_info.json"
    summary = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    summary.update({"feasible_attack_pairs": len(feasible), "attack_pairs": len(all_pairs), "configs": {}})
    for name in sorted(runs, key=lambda n: ORDER.index(n) if n in ORDER else len(ORDER)):
        rows = [r for r in runs[name] if "error" not in r]
        benign = [r for r in rows if not r["injection_task"]]
        attacked = [r for r in rows if r["injection_task"] and (r["user_task"], r["injection_task"]) in feasible]
        summary["configs"][name] = {
            "errors": len(runs[name]) - len(rows),
            "benign_runs": len(benign),
            "benign_utility": rate(benign, "utility"),
            "benign_prompts_per_task": mean(benign, "confirmations"),
            "benign_blocks_per_task": mean(benign, "blocked"),
            "feasible_attack_runs": len(attacked),
            "attack_success_rate": rate(attacked, "attack_success"),
            "utility_under_attack": rate(attacked, "utility"),
            "prompts_per_attacked_task": mean(attacked, "confirmations"),
        }
    (a.dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    def fmt(v):
        return "—" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)

    cols = ["benign_utility", "benign_prompts_per_task", "attack_success_rate", "utility_under_attack",
            "prompts_per_attacked_task", "errors"]
    print(f"feasible attack pairs: {len(feasible)} / {len(all_pairs)}")
    print("| config | " + " | ".join(cols) + " |")
    print("|---" * (len(cols) + 1) + "|")
    for name, s in summary["configs"].items():
        print(f"| {name} | " + " | ".join(fmt(s[c]) for c in cols) + " |")


if __name__ == "__main__":
    main()
