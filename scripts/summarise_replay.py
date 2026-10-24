"""Summarise scripts/replay_saved_runs.py output.

Attack success is reported over the attacks that SUCCEED when the recording is replayed with
no gate (the real agent was fooled). "Fidelity" checks the no_gate replay reproduces the
saved outcome.

Usage: python scripts/summarise_replay.py --dir results/replay_v1
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--configs", default="no_gate,confirm_all,strict_provenance,loose_provenance,delegation_intent,delegation_select,delegation_source")
    a = ap.parse_args()
    configs = a.configs.split(",")
    out = []
    for agent_dir in sorted(p for p in a.dir.iterdir() if p.is_dir()):
        rows = {c: load(agent_dir / f"{c}.jsonl") for c in configs if (agent_dir / f"{c}.jsonl").exists()}
        if "no_gate" not in rows:
            continue
        ok = [r for r in rows["no_gate"] if "error" not in r]
        attacked0 = [r for r in ok if r["injection_task"] and r["attack"] != "none"]
        fooled = {(r["user_task"], r["injection_task"]) for r in attacked0 if r["attack_success"]}
        fid_u = mean(r["utility"] == r["recorded_utility"] for r in ok)
        fid_s = mean(r["attack_success"] == r["recorded_security"] for r in attacked0)
        errors = {c: sum("error" in r for r in rs) for c, rs in rows.items()}
        out.append(f"\n## {agent_dir.name}\n")
        out.append(f"Replay fidelity (no_gate vs saved outcome): utility {fid_u:.3f}, attack outcome {fid_s:.3f}. "
                   f"Real agent fooled in {len(fooled)} / {len(attacked0)} attacked runs. Errors: {errors}\n")
        out.append("| config | benign runs | prompts / benign task | benign utility | benign runs stopped | "
                   "attack success (of fooled) | prompts / attacked task | utility under attack |")
        out.append("|---|---|---|---|---|---|---|---|")
        for c, rs in rows.items():
            rs = [r for r in rs if "error" not in r]
            benign = [r for r in rs if not r["injection_task"] or r["attack"] == "none"]
            attacked = [r for r in rs if r["injection_task"] and r["attack"] != "none"]
            on_fooled = [r for r in attacked if (r["user_task"], r["injection_task"]) in fooled]
            out.append(
                f"| {c} | {len(benign)} | {mean(r['confirmations'] for r in benign):.3f} | "
                f"{mean(r['utility'] for r in benign):.3f} | {mean(r['stopped'] for r in benign):.3f} | "
                f"{mean(r['attack_success'] for r in on_fooled):.3f} ({sum(r['attack_success'] for r in on_fooled)}/{len(on_fooled)}) | "
                f"{mean(r['confirmations'] for r in attacked):.3f} | {mean(r['utility'] for r in attacked):.3f} |")
    text = "\n".join(out)
    print(text)
    (a.dir / "summary.md").write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
