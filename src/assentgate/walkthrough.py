"""Step-by-step walkthrough of one AgentDojo run through a gate (drives the demo, `demo/app.py`).

A run is either the scripted ground-truth agent or a replay of a saved real-agent run (AgentDojo's
`runs/` folder). A simulated user or a person answers the gate's questions. For a person,
`walk` re-runs the (deterministic) run from the start with the answers given so far. The first
unanswered question is declined for now and marked pending, and the steps after it are hidden.

Needs the `eval` extra (AgentDojo). No LLM calls.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentdojo.task_suite.load_suites import get_suite

from assentgate.agentdojo_adapter import ImportantInstructions, RecordedProposer, recorded_turns, run_agent
from assentgate.classifier import (
    SOURCE_RANK,
    AllowAll,
    DelegationIntentRules,
    DelegationSelectRules,
    DelegationSourceRules,
    LooseProvenance,
    SensitivityBaseline,
    StrictProvenance,
    evidence_kind,
)
from assentgate.provenance import field_name
from assentgate.schemas import AuthorizationRequest, GateResult
from assentgate.workspace import WORKSPACE_TOOLS

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "data" / "raw" / "agentdojo" / "runs"
RESULTS = ROOT / "results"
BENCHMARK_VERSION = "v1.2.2"
SPECS = {t.name: t for t in WORKSPACE_TOOLS}

# label -> (classifier, config name used in results/)
GATES = {
    "AssentGate (delegation_source)": (DelegationSourceRules, "delegation_source"),
    "No gate": (AllowAll, "no_gate"),
    "Confirm every side effect": (SensitivityBaseline, "confirm_all"),
    "Strict provenance": (StrictProvenance, "strict_provenance"),
    "Loose provenance": (LooseProvenance, "loose_provenance"),
    "AssentGate without tests A and C (delegation_intent)": (DelegationIntentRules, "delegation_intent"),
    "AssentGate without test A (delegation_select)": (DelegationSelectRules, "delegation_select"),
}
MAIN_GATES = list(GATES)[:5]
CONFIG_LABELS = {config: label for label, (_, config) in GATES.items()} | {
    "tool_rules": "Tool-level rules", "confirm_all_no": "Confirm every side effect, user always declines",
    "delegation_rules": "AssentGate values only (delegation_rules)"}
AGENTS = {"claude-3-7-sonnet-20250219": "Claude 3.7 Sonnet", "meta-llama_Llama-3.3-70B-Instruct": "Llama 3.3 70B"}
APPROVERS = {"me": "You", "attack_aware": "Simulated user (attack-aware)", "ground_truth": "Simulated user (ground truth)"}
KINDS = {
    "user": "typed by the user",
    "exact": "a whole field of an earlier tool output",
    "named_content": "inside a record the user explicitly delegated to",
    "contained": "only inside the free text of an earlier tool output",
    "unknown": "seen nowhere before",
}


@lru_cache(maxsize=1)
def suite() -> Any:
    return get_suite(BENCHMARK_VERSION, "workspace")


@lru_cache(maxsize=1)
def _attack() -> ImportantInstructions:
    return ImportantInstructions(suite())


@lru_cache(maxsize=1024)
def scripted_injections(user_task: str, injection_task: str) -> dict[str, str]:
    """AgentDojo's important_instructions text, placed in every injection vector the task reads."""
    s = suite()
    return _attack().injections(s.user_tasks[user_task], s.injection_tasks[injection_task])


def _task_number(task_id: str) -> int:
    return int(task_id.rsplit("_", 1)[1])


def user_tasks() -> list[tuple[str, str]]:
    return sorted(((t.ID, t.PROMPT) for t in suite().user_tasks.values()), key=lambda x: _task_number(x[0]))


@lru_cache(maxsize=2)
def injection_tasks(scripted: bool) -> tuple[tuple[str, str], ...]:
    """(id, goal). The scripted agent can only attempt injection tasks with a non-empty ground truth."""
    s = suite()
    probe = s.load_and_inject_default_environment({})
    tasks = [t for t in s.injection_tasks.values() if not scripted or t.ground_truth(probe)]
    return tuple(sorted(((t.ID, t.GOAL) for t in tasks), key=lambda x: _task_number(x[0])))


def saved_run_path(agent: str, user_task: str, injection_task: str | None, runs: Path = RUNS) -> Path:
    attack = "important_instructions" if injection_task else "none"
    return runs / agent / "workspace" / user_task / attack / f"{injection_task or 'none'}.json"


def load_saved(agent: str, user_task: str, injection_task: str | None, runs: Path = RUNS) -> dict:
    return json.loads(saved_run_path(agent, user_task, injection_task, runs).read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def fooled(agent: str, runs: Path = RUNS) -> tuple[tuple[str, str], ...]:
    """(user task, injection task) pairs whose saved run shows the attack succeeding with no defense."""
    out = []
    for p in (runs / agent / "workspace").glob("user_task_*/important_instructions/injection_task_*.json"):
        if json.loads(p.read_text(encoding="utf-8")).get("security"):
            out.append((p.parents[1].name, p.stem))
    return tuple(sorted(out, key=lambda x: (_task_number(x[0]), _task_number(x[1]))))


class AnswerBook:
    """Approver that replays a person's answers in order. The first unanswered question is declined for
    now and becomes `pending`."""

    def __init__(self, answers: Sequence[bool] = ()):
        self.answers = list(answers)
        self.asked = 0
        self.pending: int | None = None

    def __call__(self, request: AuthorizationRequest, result: GateResult) -> bool:
        k, self.asked = self.asked, self.asked + 1
        if k < len(self.answers):
            return self.answers[k]
        if self.pending is None:
            self.pending = k
        return False


@dataclass
class ValueSource:
    field: str
    value: str
    kind: str  # key of KINDS
    where: str


@dataclass
class Step:
    number: int
    tool: str
    arguments: dict
    side_effecting: bool
    decision: str  # the gate's first decision
    decided_by: str
    reasons: list[str]
    questions: list[str]
    affected_fields: list[str]
    values: list[ValueSource]
    checks: list[tuple[bool, str]]  # (passed, description) for tests A and C
    asked: bool
    approved: bool
    executed: bool
    pending: bool = False
    output: str | None = None
    agent_sees: str | None = None  # the error the agent got back, if the call didn't run
    planted: bool = False  # the output contains planted attack text


@dataclass
class Walkthrough:
    kind: str  # "scripted" or an agent id from AGENTS
    user_task: str
    injection_task: str | None
    gate: str
    approver: str
    user_request: str
    attacker_goal: str | None
    injections: dict[str, str]
    steps: list[Step]
    pending: int | None  # number of the step waiting for a person's answer
    utility: bool | None  # None while a question is pending
    attack_success: bool | None
    stopped: bool  # replay stopped at a blocked or declined call
    recorded_utility: bool | None = None  # replay: the saved run's own outcome, with no defense
    recorded_attack_success: bool | None = None
    audit: list[dict] = field(default_factory=list)

    @property
    def questions_asked(self) -> int:
        return sum(s.asked for s in self.steps)

    @property
    def blocked(self) -> int:
        return sum(s.decision == "blocked" for s in self.steps)


def _kinds(request: AuthorizationRequest) -> dict[tuple[str, str], set[str]]:
    kinds: dict[tuple[str, str], set[str]] = {}
    for e in request.evidence:
        if e.verified and e.value is not None:
            kinds.setdefault((e.field, e.value), set()).add(evidence_kind(e, request))
    return kinds


def value_sources(request: AuthorizationRequest, sensitive: Sequence[str]) -> list[ValueSource]:
    """Best verified source of each sensitive value, with where it was seen."""
    best: dict[tuple[str, str], tuple[str, Any]] = {}
    for e in request.evidence:
        if not e.verified or e.value is None or e.field not in sensitive:
            continue
        kind = evidence_kind(e, request)
        key = (e.field, e.value)
        if key not in best or SOURCE_RANK[kind] < SOURCE_RANK[best[key][0]]:
            best[key] = (kind, e)
    rows = []
    for (f, v), (kind, e) in best.items():
        if kind == "user":
            where = "the user's request"
        elif kind == "unknown":
            where = ""
        else:
            where = f"{e.detail}, field '{field_name(e.path or '')}'"
            if e.snippet:
                where += f": {e.snippet}"
        rows.append(ValueSource(f, v, kind, where))
    return rows


def check_lines(request: AuthorizationRequest) -> list[tuple[bool, str]]:
    """Tests C (superlative picks) and A (recipient sources), as AssentGate uses them."""
    out = []
    for c in request.selection_checks:
        verdict = "is" if c.ok else "is not"
        out.append((c.ok, f"Test C: the request asks for the {c.phrase}; '{c.value}' {verdict} it ({c.detail})."))
    kinds = _kinds(request)
    for c in request.source_checks:
        if kinds.get((c.field, c.value), set()) & {"user", "named_content"}:
            continue  # typed by the user or written in delegated content: needs no named record
        if c.ok:
            out.append((True, f"Test A: '{c.value}' comes from a record the request names: {c.named[0]}."))
        else:
            out.append((False, f"Test A: '{c.value}' only comes from records the request doesn't name: "
                               f"{'; '.join(c.found_in[:3])}."))
    return out


OUTPUT_LABEL = re.compile(r"\b([a-z_]+)#(\d+)((?:\[\d+\])?)")


def _relabel(text: str, outputs: dict[int, int]) -> str:
    """'get_day_calendar_events#1[0]' -> 'step 2 (get_day_calendar_events), item 1'."""
    def sub(m: re.Match) -> str:
        k = int(m.group(2))
        if k not in outputs:
            return m.group(0)
        item = f", item {int(m.group(3)[1:-1]) + 1}" if m.group(3) else ""
        return f"step {outputs[k]} ({m.group(1)}){item}"

    return OUTPUT_LABEL.sub(sub, text)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _planted(output: str | None, injections: dict[str, str]) -> bool:
    if not output:
        return False
    if "<INFORMATION>" in output:
        return True
    flat = _normalise(output)
    return any(len(v.strip()) >= 20 and _normalise(v)[:40] in flat for v in injections.values())


def walk(kind: str, user_task: str, injection_task: str | None, gate: str = "AssentGate (delegation_source)",
         approver: str = "attack_aware", answers: Sequence[bool] = (), runs: Path = RUNS) -> Walkthrough:
    """Run one task through `gate` and return every step. `kind` is "scripted" or an agent id;
    `approver` is "me" (a person, via `answers`), "attack_aware" or "ground_truth"."""
    s = suite()
    task = s.user_tasks[user_task]
    attack_task = s.injection_tasks[injection_task] if injection_task else None
    classifier, _ = GATES[gate]
    book = AnswerBook(answers) if approver == "me" else None
    recorded: dict = {}
    if kind == "scripted":
        injections = scripted_injections(user_task, injection_task) if injection_task else {}
        llm, max_iters = None, 15
    else:
        recorded = load_saved(kind, user_task, injection_task, runs)
        injections = recorded.get("injections") or {}
        turns, final = recorded_turns(recorded["messages"])
        llm, max_iters = RecordedProposer(turns, final), len(turns) + 2
    row = run_agent(s, task, attack_task, classifier, book or approver, SPECS, WORKSPACE_TOOLS, injections,
                    llm=llm, max_iters=max_iters, trace=True)

    steps, asked_so_far, pending_step = [], 0, None
    outputs: dict[int, int] = {}  # index of a recorded tool output -> step that produced it
    for number, entry in enumerate(row["log"], start=1):
        request, first = entry["request"], entry["result"]
        spec = SPECS.get(entry["tool"])
        is_pending = False
        if entry["asked"]:
            is_pending = book is not None and book.pending == asked_so_far
            asked_so_far += 1
        values = value_sources(request, spec.sensitive_fields) if (request and spec) else []
        for v in values:
            v.where = _relabel(v.where, outputs)
        steps.append(Step(
            number=number, tool=entry["tool"], arguments=entry["arguments"],
            side_effecting=bool(spec and spec.side_effecting), decision=first.decision.value,
            decided_by=first.decided_by, reasons=[_relabel(r, outputs) for r in first.reasons],
            questions=first.questions, affected_fields=first.affected_fields, values=values,
            checks=[(ok, _relabel(line, outputs)) for ok, line in check_lines(request)] if request else [],
            asked=entry["asked"],
            approved=entry["approved"] and not is_pending, executed=entry["executed"] and not is_pending,
            pending=is_pending, output=None if is_pending else entry["output"],
            agent_sees=None if is_pending else entry["agent_sees"],
            planted=_planted(entry["output"], injections),
        ))
        if entry["executed"] and not is_pending:
            outputs[len(outputs)] = number
        if is_pending:
            pending_step = number
            break
    done = pending_step is None
    return Walkthrough(
        kind=kind, user_task=user_task, injection_task=injection_task, gate=gate, approver=approver,
        user_request=task.PROMPT, attacker_goal=attack_task.GOAL if attack_task else None, injections=injections,
        steps=steps, pending=pending_step, utility=row["utility"] if done else None,
        attack_success=row["attack_success"] if done else None,
        stopped=bool(done and llm is not None and llm.stopped),
        recorded_utility=recorded.get("utility") if recorded else None,
        recorded_attack_success=recorded.get("security") if (recorded and injection_task) else None,
        audit=row.get("audit", []) if done else [],
    )


def compare_gates(kind: str, user_task: str, injection_task: str | None, approver: str = "attack_aware",
                  gates: Sequence[str] = MAIN_GATES, runs: Path = RUNS) -> list[dict]:
    """The same run through each gate, with a simulated user."""
    rows = []
    for gate in gates:
        w = walk(kind, user_task, injection_task, gate, approver, runs=runs)
        rows.append({"gate": gate, "questions": w.questions_asked, "blocked": w.blocked,
                     "task completed": w.utility, "attack succeeded": w.attack_success,
                     "stopped early": w.stopped if kind != "scripted" else None})
    return rows


def _load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _benign(row: dict) -> bool:
    return not row.get("injection_task") or row.get("attack") == "none"


def results_table(directory: Path, prompts_over_feasible: bool = False) -> list[dict]:
    """Headline metrics per gate from an eval folder (`results/ground_truth_v1.2.2_ii` or
    `results/replay_v1/<agent>`). Attack success counts only attacks that succeed with no gate.
    Questions per attacked task are averaged over those attacks (`prompts_over_feasible`, as
    `summarise_ground_truth_eval.py` does) or over all attacked runs (as `summarise_replay.py` does)."""
    files = {p.stem: p for p in directory.glob("*.jsonl")}
    if "no_gate" not in files:
        return []
    rows = {name: [r for r in _load_rows(p) if "error" not in r] for name, p in files.items()}
    feasible = {(r["user_task"], r["injection_task"]) for r in rows["no_gate"] if not _benign(r) and r["attack_success"]}
    order = ["no_gate", "confirm_all", "confirm_all_no", "tool_rules", "strict_provenance", "loose_provenance",
             "delegation_rules", "delegation_intent", "delegation_select", "delegation_source"]
    table = []
    for name in sorted(rows, key=lambda n: order.index(n) if n in order else len(order)):
        benign = [r for r in rows[name] if _benign(r)]
        attacked = [r for r in rows[name] if not _benign(r)]
        through = [r for r in attacked if (r["user_task"], r["injection_task"]) in feasible]
        table.append({
            "gate": CONFIG_LABELS.get(name, name),
            "questions per benign task": round(sum(r["confirmations"] for r in benign) / max(len(benign), 1), 3),
            "benign tasks completed": round(sum(r["utility"] for r in benign) / max(len(benign), 1), 3),
            "attacks that got through": f"{sum(r['attack_success'] for r in through)} / {len(through)}",
            "questions per attacked task": round(sum(r["confirmations"] for r in (through if prompts_over_feasible
                                                                                  else attacked))
                                                 / max(len(through if prompts_over_feasible else attacked), 1), 3),
        })
    return table
