"""Build the labels-by-construction dataset from AgentDojo workspace ground truth (no LLM, no humans).

For every user task: one benign run (no attack text) and, for every injection task with a
non-empty ground truth, `--seeds` runs under AgentDojo's important_instructions attack text with
randomised attacker identities. See src/assentgate/construct.py for labels and mutations.

Usage:
    python scripts/make_constructed_dataset.py --out data/processed/agentdojo_constructed_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentdojo.task_suite.load_suites import get_suite  # noqa: E402

from assentgate.agentdojo_adapter import ImportantInstructions  # noqa: E402
from assentgate.classifier import value_sources  # noqa: E402
from assentgate.construct import generate_run  # noqa: E402
from assentgate.schemas import AuthorizationRequest  # noqa: E402
from assentgate.workspace import WORKSPACE_TOOLS  # noqa: E402

SPECS = {t.name: t for t in WORKSPACE_TOOLS}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--benchmark-version", default="v1.2.2")
    ap.add_argument("--seeds", type=int, default=2)
    a = ap.parse_args()

    suite = get_suite(a.benchmark_version, "workspace")
    attack = ImportantInstructions(suite)
    probe = suite.load_and_inject_default_environment({})
    injection_tasks = [t for t in suite.injection_tasks.values() if t.ground_truth(probe)]
    rows, errors = [], []
    for user_task in suite.user_tasks.values():
        runs = [(None, 0)] + [(t, s) for t in injection_tasks for s in range(a.seeds)]
        for injection_task, seed in runs:
            try:
                rows += generate_run(suite, user_task, injection_task, attack, SPECS, WORKSPACE_TOOLS, seed)
            except Exception as exc:  # recorded, not hidden
                errors.append(f"{user_task.ID}/{injection_task.ID if injection_task else '-'}/{seed}: "
                              f"{type(exc).__name__}: {exc}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            r["record_id"] = i
            f.write(json.dumps(r) + "\n")

    print(f"records: {len(rows)}  errors: {len(errors)}  injection tasks used: {[t.ID for t in injection_tasks]}")
    for e in errors[:10]:
        print("  ERROR", e)
    print("labels:", dict(Counter(r["label"] for r in rows)))
    print("mutations:", dict(Counter(r["mutation"] for r in rows if r["mutation"])))
    # How legitimate (allow) calls get their sensitive values, from benign runs only (one per task).
    kinds, tasks_with = Counter(), Counter()
    for r in rows:
        if r["label"] != "allow" or r["injection_task"] is not None:
            continue
        req = AuthorizationRequest.model_validate(r["request"])
        sources = value_sources(req, SPECS[r["tool"]])
        kinds.update(sources.values())
        for kind in set(sources.values()):
            tasks_with[kind] += 1
    n_allow = sum(1 for r in rows if r["label"] == "allow" and r["injection_task"] is None)
    print(f"benign legitimate side-effect calls: {n_allow}; sensitive values by source: {dict(kinds)}")
    print(f"calls with at least one value of each source: {dict(tasks_with)}")


if __name__ == "__main__":
    main()
