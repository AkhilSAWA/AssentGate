"""End-to-end gate evaluation on AgentDojo with a SCRIPTED agent (no LLM, no API cost).

The agent proposes each user task's ground-truth calls; in attacked runs it then
also proposes the injection task's ground-truth calls, i.e. a fully compromised
agent. Numbers from this script describe the gate on scripted trajectories.
They are not results for a real LLM agent.

Configs:
  no_gate         baseline 1: everything allowed
  tool_rules      baseline 2: ToolLevelRules (confirm high-impact tools only),
                  answered by the ground-truth simulated approver
  confirm_all     baseline 3: SensitivityBaseline (confirm every side effect),
                  answered by the ground-truth simulated approver
  confirm_all_no  baseline 3 with every confirmation declined (shows the cost
                  of unanswered prompts)

Writes one <config>.jsonl per config. Summarise with scripts/summarise_ground_truth_eval.py,
which only counts attacks that are feasible (they succeed with no gate). Some AgentDojo
v1.2 injection tasks have an empty ground truth, so the scripted agent can't attempt them.

Usage:
    python scripts/run_ground_truth_eval.py --out results/ground_truth_v1.2.2 [--configs tool_rules,...]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentdojo.agent_pipeline import AgentPipeline, InitQuery, ToolsExecutionLoop  # noqa: E402
from agentdojo.task_suite.load_suites import get_suite  # noqa: E402

from assentgate import AssentGate, ClassifierOutput, Decision  # noqa: E402
from assentgate.agentdojo_adapter import (  # noqa: E402
    GatedToolsExecutor,
    GroundTruthApprover,
    GroundTruthProposer,
    always_approve,
    never_approve,
)
from assentgate.classifier import SensitivityBaseline, ToolLevelRules  # noqa: E402
from assentgate.workspace import WORKSPACE_TOOLS  # noqa: E402

SPECS = {t.name: t for t in WORKSPACE_TOOLS}


class AllowAll:
    def predict(self, request, spec):
        return ClassifierOutput(decision=Decision.AUTHORIZED)


CONFIGS = {
    "no_gate": (AllowAll, "always"),
    "tool_rules": (ToolLevelRules, "ground_truth"),
    "confirm_all": (SensitivityBaseline, "ground_truth"),
    "confirm_all_no": (SensitivityBaseline, "never"),
}


def run_one(suite, user_task, injection_task, classifier_cls, approver_kind):
    env = user_task.init_environment(suite.load_and_inject_default_environment({}))
    approver = {
        "always": always_approve,
        "never": never_approve,
        "ground_truth": GroundTruthApprover(user_task.ground_truth(env), SPECS),
    }[approver_kind]
    executor = GatedToolsExecutor(lambda: AssentGate(WORKSPACE_TOOLS, classifier=classifier_cls()), approver)
    proposer = GroundTruthProposer(user_task, injection_task)
    pipeline = AgentPipeline([InitQuery(), proposer, ToolsExecutionLoop([executor, proposer], max_iters=50)])
    utility, attacked = suite.run_task_with_pipeline(pipeline, user_task, injection_task, injections={})
    return {
        "user_task": user_task.ID,
        "injection_task": injection_task.ID if injection_task else None,
        "utility": bool(utility),
        "attack_success": bool(attacked) if injection_task else None,
        "injection_calls": len(injection_task.ground_truth(env)) if injection_task else None,
        "calls": len(executor.log),
        "confirmations": sum(e["asked"] for e in executor.log),
        "approved": sum(e["approved"] for e in executor.log),
        "blocked": sum(e["decision"] == "blocked" for e in executor.log),
        "decisions": dict(Counter(e["decision"] for e in executor.log)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--benchmark-version", default="v1.2.2")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    a = ap.parse_args()

    suite = get_suite(a.benchmark_version, "workspace")
    a.out.mkdir(parents=True, exist_ok=True)
    meta = {"benchmark_version": a.benchmark_version, "agent": "scripted ground truth (no LLM)",
            "date": date.today().isoformat()}
    (a.out / "run_info.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for name in a.configs.split(","):
        classifier_cls, approver_kind = CONFIGS[name]
        benign, attacked, errors = [], [], []
        for user_task in suite.user_tasks.values():
            for injection_task in [None, *suite.injection_tasks.values()]:
                try:
                    row = run_one(suite, user_task, injection_task, classifier_cls, approver_kind)
                except Exception as exc:  # recorded, not hidden
                    errors.append({"user_task": user_task.ID,
                                   "injection_task": injection_task.ID if injection_task else None,
                                   "error": f"{type(exc).__name__}: {exc}"})
                    continue
                (attacked if injection_task else benign).append(row)
        with (a.out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for row in benign + attacked + errors:
                f.write(json.dumps(row) + "\n")
        print(f"{name}: {len(benign)} benign, {len(attacked)} attacked, {len(errors)} errors")


if __name__ == "__main__":
    main()
