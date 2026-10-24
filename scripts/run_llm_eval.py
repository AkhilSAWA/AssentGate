"""End-to-end gate evaluation on AgentDojo with a REAL LLM agent (Claude), resumable.

Costs money with `--agent claude`: ask the repo owner first. The API key is read by the
anthropic SDK from ANTHROPIC_API_KEY, which you set in your own terminal.

Each finished run is appended to <out>/<config>.jsonl at once; rerunning the same
command skips (user_task, injection_task) pairs already finished, so a crash or Ctrl-C
never re-spends money. Runs that errored are retried. `--max-cost` stops starting new
runs once the estimated spend in <out> (all configs) reaches the limit.

`--agent scripted` runs the same loop with the scripted ground-truth agent (no API,
free); use it to check the runner, not as a result.

Approvers: `ground_truth` approves only exact ground-truth calls (conservative; a real
agent's different-but-fine route is declined). `attack_aware` also approves other calls
to tools the task needs unless they carry the attacker's values (optimistic). Report both.

Examples:
    # free check of the runner
    python scripts/run_llm_eval.py --agent scripted --out results/llm_check --user-tasks user_task_0 --injection-tasks injection_task_0
    # stage 0 smoke test (8 runs, Haiku 5.5): see docs/llm_agent_run_plan.md
    python scripts/run_llm_eval.py --agent claude --model claude-haiku-5-5 --out results/llm_haiku_smoke \
        --user-tasks user_task_0 --injection-tasks injection_task_0 --max-cost 0.25
    # estimate a full run without spending anything
    python scripts/run_llm_eval.py --agent claude --out results/llm_haiku --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentdojo.task_suite.load_suites import get_suite  # noqa: E402
from run_ground_truth_eval import CONFIGS  # noqa: E402

from assentgate.agentdojo_adapter import ImportantInstructions, run_agent  # noqa: E402
from assentgate.workspace import WORKSPACE_TOOLS  # noqa: E402

SPECS = {t.name: t for t in WORKSPACE_TOOLS}
CORE_CONFIGS = "no_gate,confirm_all,strict_provenance,delegation_source"
# Planning figures from docs/llm_agent_run_plan.md (saved AgentDojo runs; thinking is a guess).
EST_INPUT_PER_RUN, EST_OUTPUT_PER_RUN = 17_000, 2_400


def pick(names: str, available: dict, none_ok: bool = False) -> list:
    if none_ok and names == "none":
        return []
    if names == "all":
        return list(available.values())
    return [available[n] for n in names.split(",")]


def load_done(path: Path) -> tuple[set, float]:
    """Finished (user_task, injection_task) pairs in one config file, and their total cost."""
    done, cost = set(), 0.0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            cost += (row.get("usage") or {}).get("cost_usd") or 0.0
            if "error" not in row:
                done.add((row["user_task"], row["injection_task"]))
    return done, cost


def to_json(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def make_llm(a):
    if a.agent == "scripted":
        return None
    import anthropic

    from assentgate.llm_agent import ClaudeLLM

    if not hasattr(make_llm, "client"):
        make_llm.client = anthropic.Anthropic(max_retries=0)  # retries are done in ClaudeLLM
    return ClaudeLLM(make_llm.client, a.model, max_tokens=a.max_tokens, effort=a.effort, cache=not a.no_cache)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--agent", choices=["claude", "scripted"], default="claude")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    ap.add_argument("--no-cache", action="store_true", help="don't use prompt caching")
    ap.add_argument("--benchmark-version", default="v1.2.2")
    ap.add_argument("--configs", default=CORE_CONFIGS)
    ap.add_argument("--approver", choices=["ground_truth", "attack_aware"], default="attack_aware",
                    help="simulated user for gated configs (no_gate always approves)")
    ap.add_argument("--attack", choices=["important_instructions", "none"], default="important_instructions")
    ap.add_argument("--user-tasks", default="all", help="comma-separated ids, or all")
    ap.add_argument("--injection-tasks", default="all", help="comma-separated ids, all, or none (benign only)")
    ap.add_argument("--no-benign", action="store_true", help="skip the benign (no injection) run of each task")
    ap.add_argument("--max-iters", type=int, default=15, help="tool rounds per run (AgentDojo default 15)")
    ap.add_argument("--workers", type=int, default=1, help="runs in parallel (watch your rate limits)")
    ap.add_argument("--max-cost", type=float, default=None, help="stop starting runs at this estimated USD spend")
    ap.add_argument("--transcripts", action="store_true", help="save each run's full message list")
    ap.add_argument("--dry-run", action="store_true", help="count remaining runs and estimate cost; no API calls")
    a = ap.parse_args()

    if a.agent == "claude" and not a.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Set it in your own terminal first.")

    suite = get_suite(a.benchmark_version, "workspace")
    user_tasks = pick(a.user_tasks, suite.user_tasks)
    injection_tasks = pick(a.injection_tasks, suite.injection_tasks, none_ok=True)
    pairs = [(u, i) for u in user_tasks for i in ([] if a.no_benign else [None]) + injection_tasks]
    attack = (ImportantInstructions(suite, model_name="Claude" if a.agent == "claude" else None)
              if a.attack == "important_instructions" else None)
    configs = a.configs.split(",")

    a.out.mkdir(parents=True, exist_ok=True)
    todo, spent = [], 0.0
    for name in configs:
        done, cost = load_done(a.out / f"{name}.jsonl")
        spent += cost
        todo += [(name, u, i) for u, i in pairs if (u.ID, i.ID if i else None) not in done]

    from assentgate.llm_agent import PRICES, call_cost  # no API use

    per_run = call_cost(a.model, {"input_tokens": EST_INPUT_PER_RUN, "output_tokens": EST_OUTPUT_PER_RUN})
    est = f"~${per_run * len(todo):.2f} ({a.model}, planning figures, no caching)" if a.model in PRICES else "unknown"
    print(f"{len(todo)} runs to do ({len(configs)} configs x {len(pairs)} task pairs, minus finished); "
          f"spent so far in {a.out}: ${spent:.4f}; estimate for the rest: {est if a.agent == 'claude' else '$0'}")
    if a.dry_run or not todo:
        return

    info = {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(a).items()}
    info["started"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with (a.out / "run_info.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(info) + "\n")

    lock = threading.Lock()
    state = {"spent": spent, "stopped": False, "errors_in_a_row": 0}

    def one(job):
        name, user_task, injection_task = job
        with lock:
            if state["stopped"]:
                return
            if a.max_cost is not None and state["spent"] >= a.max_cost:
                print(f"--max-cost ${a.max_cost} reached (${state['spent']:.4f}); stopping.")
                state["stopped"] = True
                return
        classifier_cls, approver_kind = CONFIGS[name]
        if approver_kind == "ground_truth":
            approver_kind = a.approver
        injections = attack.injections(user_task, injection_task) if (attack and injection_task) else {}
        llm = make_llm(a)
        key = {"user_task": user_task.ID, "injection_task": injection_task.ID if injection_task else None}
        try:
            row = run_agent(suite, user_task, injection_task, classifier_cls, approver_kind, SPECS,
                            WORKSPACE_TOOLS, injections, llm=llm, max_iters=a.max_iters)
            row["approver"] = approver_kind
            messages = llm.last_messages if llm is not None else None
        except Exception as exc:  # recorded, retried on the next invocation
            row, messages = {**key, "error": f"{type(exc).__name__}: {exc}"}, None
        if llm is not None:
            row["usage"] = llm.usage()
        with lock:
            state["spent"] += (row.get("usage") or {}).get("cost_usd") or 0.0
            with (a.out / f"{name}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            if a.transcripts and messages is not None:
                tdir = a.out / "transcripts" / name
                tdir.mkdir(parents=True, exist_ok=True)
                (tdir / f"{key['user_task']}__{key['injection_task']}.json").write_text(
                    json.dumps(messages, default=to_json, indent=1), encoding="utf-8")
            if "error" in row:
                state["errors_in_a_row"] += 1
                print(f"{name} {key}: {row['error']}")
                if state["errors_in_a_row"] >= 3:
                    print("3 errors in a row; stopping. Fix the cause and rerun to resume.")
                    state["stopped"] = True
            else:
                state["errors_in_a_row"] = 0
                print(f"{name} {key['user_task']} {key['injection_task']}: utility={row['utility']} "
                      f"attack={row['attack_success']} asked={row['confirmations']} "
                      f"spent=${state['spent']:.4f}")

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(one, todo))
    print(f"done; estimated spend in {a.out}: ${state['spent']:.4f}")


if __name__ == "__main__":
    main()
