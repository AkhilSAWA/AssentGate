"""Replay AgentDojo's saved REAL-agent runs through the gates (no LLM calls, free).

Each saved run (data/raw/agentdojo/runs/<agent>/workspace/<user_task>/<attack>/<injection_task>.json)
holds the tool calls a real agent made with no defense. We re-execute those calls, turn by
turn, in a fresh AgentDojo environment with the run's own injections, through each gate.
The gates therefore see real structured tool outputs and real provenance.

A recording can't react to the gate: when a call is blocked or the simulated user declines,
the replay stops there and the agent's final answer is empty (conservative for utility).
`no_gate` replays everything and checks the replay reproduces the recorded outcome.

Usage:
    python scripts/replay_saved_runs.py --out results/replay_v1 --agents claude-3-7-sonnet-20250219,meta-llama_Llama-3.3-70B-Instruct
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from assentgate.agentdojo_adapter import RecordedProposer, recorded_turns  # noqa: E402

CONFIGS_DEFAULT = "no_gate,confirm_all,strict_provenance,loose_provenance,delegation_intent"


def replay_job(job):
    agent, config, files, approver, out, part = job
    from agentdojo.task_suite.load_suites import get_suite
    from run_ground_truth_eval import CONFIGS

    from assentgate.agentdojo_adapter import run_agent
    from assentgate.workspace import WORKSPACE_TOOLS

    specs = {t.name: t for t in WORKSPACE_TOOLS}
    suite = get_suite("v1.2.2", "workspace")
    classifier_cls, approver_kind = CONFIGS[config]
    if approver_kind == "ground_truth":
        approver_kind = approver
    path = Path(out) / agent / (f"{config}.jsonl" if part is None else f"{config}.part{part}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for p in files:
            rec = json.loads(Path(p).read_text(encoding="utf-8"))
            base = {"agent": agent, "user_task": rec["user_task_id"], "injection_task": rec["injection_task_id"],
                    "attack": rec["attack_type"], "recorded_utility": rec["utility"],
                    "recorded_security": rec["security"]}
            try:
                user_task = suite.user_tasks[rec["user_task_id"]]
                inj_id = rec["injection_task_id"]
                injection_task = suite.injection_tasks[inj_id] if (inj_id and rec["attack_type"] != "none") else None
                turns, final = recorded_turns(rec["messages"])
                proposer = RecordedProposer(turns, final)
                row = run_agent(suite, user_task, injection_task if rec["attack_type"] != "none" else None,
                                classifier_cls, approver_kind, specs, WORKSPACE_TOOLS,
                                rec.get("injections") or {}, llm=proposer, max_iters=len(turns) + 2)
                row.pop("log", None)
                row.update(base, approver=approver_kind, stopped=proposer.stopped, recorded_calls=sum(map(len, turns)))
            except Exception as exc:  # recorded, not hidden
                row = {**base, "error": f"{type(exc).__name__}: {exc}"}
            f.write(json.dumps(row) + "\n")
    return agent, config, part, len(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=ROOT / "data" / "raw" / "agentdojo" / "runs")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--agents", default="claude-3-7-sonnet-20250219,meta-llama_Llama-3.3-70B-Instruct")
    ap.add_argument("--configs", default=CONFIGS_DEFAULT)
    ap.add_argument("--approver", choices=["attack_aware", "ground_truth"], default="attack_aware")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--shards", type=int, default=1, help="split each (agent, gate) job into this many parts")
    a = ap.parse_args()

    jobs = []
    for agent in a.agents.split(","):
        base = a.runs / agent / "workspace"
        files = sorted(str(p) for p in base.glob("user_task_*/none/none.json"))
        files += sorted(str(p) for p in base.glob("user_task_*/important_instructions/injection_task_*.json"))
        print(f"{agent}: {len(files)} saved runs")
        for c in a.configs.split(","):
            if a.shards == 1:
                jobs.append((agent, c, files, a.approver, str(a.out), None))
            else:
                jobs += [(agent, c, files[k::a.shards], a.approver, str(a.out), k) for k in range(a.shards)]
    a.out.mkdir(parents=True, exist_ok=True)
    with (a.out / "run_info.jsonl").open("a", encoding="utf-8") as f:  # one line per invocation
        f.write(json.dumps({**{k: str(v) for k, v in vars(a).items()}, "benchmark_version": "v1.2.2"}) + "\n")
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for agent, config, part, n in pool.map(replay_job, jobs):
            print(f"done {agent} {config} part {part} ({n} runs)", flush=True)
    if a.shards > 1:  # merge the parts into one file per (agent, gate)
        for agent in a.agents.split(","):
            for c in a.configs.split(","):
                parts = [a.out / agent / f"{c}.part{k}.jsonl" for k in range(a.shards)]
                with (a.out / agent / f"{c}.jsonl").open("w", encoding="utf-8") as f:
                    for p in parts:
                        f.write(p.read_text(encoding="utf-8"))
                        p.unlink()


if __name__ == "__main__":
    main()
