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

from agentdojo.task_suite.load_suites import get_suite  # noqa: E402

from assentgate import ClassifierOutput, Decision  # noqa: E402
from assentgate.agentdojo_adapter import ImportantInstructions, run_scripted  # noqa: E402
from assentgate.classifier import (  # noqa: E402
    DelegationIntentRules,
    DelegationRules,
    LooseProvenance,
    SensitivityBaseline,
    StrictProvenance,
    ToolLevelRules,
)
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
    "strict_provenance": (StrictProvenance, "ground_truth"),
    "loose_provenance": (LooseProvenance, "ground_truth"),
    "delegation_rules": (DelegationRules, "ground_truth"),
    "delegation_intent": (DelegationIntentRules, "ground_truth"),
}


def run_one(suite, user_task, injection_task, classifier_cls, approver_kind, attack=None):
    injections = attack.injections(user_task, injection_task) if (attack and injection_task) else {}
    row = run_scripted(suite, user_task, injection_task, classifier_cls, approver_kind, SPECS,
                       WORKSPACE_TOOLS, injections)
    row.pop("log")
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--benchmark-version", default="v1.2.2")
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--attack", choices=["none", "important_instructions"], default="none",
                    help="important_instructions puts AgentDojo's standard attack text into the data the "
                         "user task reads, so attacker values appear in tool outputs (realistic for provenance gates)")
    ap.add_argument("--skip-empty-injections", action="store_true",
                    help="skip injection tasks whose ground truth is empty (the scripted agent can't attack)")
    a = ap.parse_args()

    suite = get_suite(a.benchmark_version, "workspace")
    attack = ImportantInstructions(suite) if a.attack == "important_instructions" else None
    injection_tasks = list(suite.injection_tasks.values())
    if a.skip_empty_injections:
        probe_env = suite.load_and_inject_default_environment({})
        injection_tasks = [t for t in injection_tasks if t.ground_truth(probe_env)]
    a.out.mkdir(parents=True, exist_ok=True)
    meta = {"benchmark_version": a.benchmark_version, "agent": "scripted ground truth (no LLM)",
            "attack": a.attack, "injection_tasks": [t.ID for t in injection_tasks],
            "date": date.today().isoformat()}
    (a.out / "run_info.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for name in a.configs.split(","):
        classifier_cls, approver_kind = CONFIGS[name]
        benign, attacked, errors = [], [], []
        for user_task in suite.user_tasks.values():
            for injection_task in [None, *injection_tasks]:
                try:
                    row = run_one(suite, user_task, injection_task, classifier_cls, approver_kind, attack)
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
