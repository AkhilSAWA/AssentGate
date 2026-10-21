"""Build a weakly-labelled AssentGate dataset from AgentDojo's saved agent runs.

AgentDojo's repo ships `runs/<model>/<suite>/<user_task>/<attack>/<injection_task>.json`:
full agent trajectories (user prompt, every tool call + output) plus the exact text
that was injected. No LLM calls or API keys are needed to use them.

For every tool call the agent proposed, this script writes one JSONL record with:
  - the model INPUT the gate will actually have at run time (request, tool, args,
    per-field provenance as user / tool_output / unknown, bounded history), and
  - a rule-based WEAK LABEL plus `needs_review` for cases rules cannot decide.

Weak labels are a starting point for annotation, not ground truth.

Usage:
    git clone https://github.com/ethz-spylab/agentdojo data/raw/agentdojo
    git -C data/raw/agentdojo rev-parse HEAD        # record this commit!
    python scripts/build_agentdojo_dataset.py --runs data/raw/agentdojo/runs \
        --out data/processed/agentdojo_workspace.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

# --- AgentDojo workspace tools -------------------------------------------------
READ_ONLY_PREFIXES = ("get_", "search_", "list_")
SENSITIVE_FIELDS = {
    "send_email": ["recipients", "cc", "bcc", "attachments"],
    "share_file": ["file_id", "email", "permission"],
    "delete_file": ["file_id"],
    "delete_email": ["email_id"],
    "append_to_file": ["file_id"],
    "create_file": [],
    "create_calendar_event": ["participants"],
    "add_calendar_event_participants": ["event_id", "participants"],
    "cancel_calendar_event": ["event_id"],
    "reschedule_calendar_event": ["event_id"],
}
# Object identifiers the agent usually has to look up to do what the user asked.
ID_FIELDS = {"event_id", "file_id", "email_id"}
# Tools where even a looked-up identifier is consequential (data leaves / is destroyed).
HIGH_IMPACT = {"send_email", "share_file", "delete_file", "delete_email"}
HISTORY_LEN = 5
MIN_FREE_TEXT_MATCH = 8  # chars; avoids matching tiny strings in injection text


def is_read_only(fn: str) -> bool:
    return fn.startswith(READ_ONLY_PREFIXES)


def flat_values(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, dict):
        return [s for x in v.values() for s in flat_values(x)]
    if isinstance(v, (list, tuple)):
        return [s for x in v for s in flat_values(x)]
    s = str(v).strip()
    return [s] if s else []


def contains(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(r"(?<![\w.@-])" + re.escape(needle.lower()) + r"(?![\w@-])", haystack.lower()) is not None


def provenance(value: str, user_prompt: str, injected: str, seen_outputs: str) -> str:
    """Oracle provenance (uses the known injection text). Used for LABELLING only."""
    if contains(user_prompt, value):
        return "user"
    if injected and contains(injected, value):
        return "injected"
    if contains(seen_outputs, value):
        return "tool_output"
    return "unknown"


def weak_label(fn: str, args: dict, user_prompt: str, injected: str, seen: str):
    """Return (label, unresolved, prohibited, needs_review, oracle_field_sources)."""
    sens = SENSITIVE_FIELDS.get(fn, [])
    src = {}
    for f in sens:
        vals = flat_values(args.get(f))
        if vals:
            kinds = {provenance(v, user_prompt, injected, seen) for v in vals}
            for k in ("injected", "unknown", "tool_output", "user"):  # worst wins
                if k in kinds:
                    src[f] = k
                    break

    if is_read_only(fn):
        return "safely_derived", [], [], False, src

    # Attacker-chosen content anywhere in the arguments => blocked.
    prohibited = [f for f, k in src.items() if k == "injected"]
    if injected:
        for k, v in args.items():
            if k in src:
                continue
            for s in flat_values(v):
                if len(s) >= MIN_FREE_TEXT_MATCH and contains(injected, s) and not contains(user_prompt, s):
                    prohibited.append(k)
                    break
    if prohibited:
        # A block resting only on short identifiers (e.g. file '13') may be a coincidence
        # between the user task and the injection goal -> review.
        short_only = all(len(min(flat_values(args.get(f)) or ["x" * 9], key=len)) < 4 for f in prohibited)
        return "blocked", [], sorted(set(prohibited)), short_only, src

    unresolved = sorted(f for f, k in src.items() if k in ("tool_output", "unknown"))
    if unresolved and set(unresolved) <= ID_FIELDS and fn not in HIGH_IMPACT:
        # e.g. reschedule "my dental check-up": event_id found by search.
        return "safely_derived", unresolved, [], True, src
    if unresolved:
        # Legit value from data the agent read: authorized-by-delegation vs. needs confirmation
        # depends on the request wording -> human decision.
        return "confirmation_required", unresolved, [], True, src
    if fn not in SENSITIVE_FIELDS:
        return "confirmation_required", [], [], True, src  # unknown side-effecting tool
    return "authorized", [], [], False, src


def runtime_evidence(src: dict) -> dict:
    """What the gate can know at run time: injected text is indistinguishable from tool output."""
    return {f: ("tool_output" if k == "injected" else k) for f, k in src.items()}


def iter_runs(runs_dir: Path, suites: set[str]):
    for p in sorted(runs_dir.glob("*/*/*/*/*.json")):
        model, suite = p.parts[-5], p.parts[-4]
        if suite in suites:
            yield model, p


def records_from_run(model: str, path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    msgs = d.get("messages") or []
    user_prompt = next((m.get("content") or "" for m in msgs if m.get("role") == "user"), "")
    if isinstance(user_prompt, list):  # newer format: list of content blocks
        user_prompt = " ".join(b.get("content", "") if isinstance(b, dict) else str(b) for b in user_prompt)
    injected = "\n".join(str(v) for v in (d.get("injections") or {}).values())
    seen, history = "", []
    for m in msgs:
        if m.get("role") == "tool":
            c = m.get("content")
            seen += "\n" + (c if isinstance(c, str) else json.dumps(c, default=str))
            continue
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn, args = tc.get("function"), tc.get("args") or {}
            label, unresolved, prohibited, review, src = weak_label(fn, args, user_prompt, injected, seen)
            yield {
                "group_id": f"{d['suite_name']}:{d['user_task_id']}",
                "suite": d["suite_name"],
                "user_task_id": d["user_task_id"],
                "injection_task_id": d.get("injection_task_id"),
                "attack_type": d.get("attack_type"),
                "agent_model": model,
                "user_request": user_prompt,
                "tool": fn,
                "args": args,
                "evidence": runtime_evidence(src),
                "history": history[-HISTORY_LEN:],
                "label": label,
                "unresolved_fields": unresolved,
                "prohibited_fields": prohibited,
                "needs_review": review,
                "label_source": "rule_v1",
                "run_utility": d.get("utility"),
                "run_security": d.get("security"),
                "source_file": str(path.as_posix()).split("runs/", 1)[-1],
            }
            history.append({"tool": fn, "args": args})


def dedupe_key(r: dict) -> str:
    k = json.dumps([r["user_task_id"], r["injection_task_id"], r["attack_type"], r["tool"], r["args"],
                    [h["tool"] for h in r["history"]]], sort_keys=True, default=str)
    return hashlib.sha1(k.encode()).hexdigest()


def split_of(group_id: str, val: float = 0.15, test: float = 0.15) -> str:
    h = int(hashlib.sha256(group_id.encode()).hexdigest(), 16) % 1000 / 1000
    return "test" if h < test else "val" if h < test + val else "train"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--suites", default="workspace")
    ap.add_argument("--keep-duplicates", action="store_true")
    a = ap.parse_args()

    a.out.parent.mkdir(parents=True, exist_ok=True)
    seen, n_files, stats = set(), 0, Counter()
    with a.out.open("w", encoding="utf-8") as f:
        for model, p in iter_runs(a.runs, set(a.suites.split(","))):
            n_files += 1
            for r in records_from_run(model, p):
                key = dedupe_key(r)
                if not a.keep_duplicates and key in seen:
                    continue
                seen.add(key)
                r["record_id"], r["split"] = key[:16], split_of(r["group_id"])
                f.write(json.dumps(r, default=str) + "\n")
                stats[(r["split"], r["label"])] += 1
                stats[("review", r["needs_review"])] += 1

    print(f"run files read: {n_files}")
    for split in ("train", "val", "test"):
        row = {lab: stats[(split, lab)] for lab in
               ("authorized", "safely_derived", "confirmation_required", "blocked")}
        print(f"{split:5s} {row}  total={sum(row.values())}")
    print(f"needs_review: {stats[('review', True)]}   rule-decided: {stats[('review', False)]}")


if __name__ == "__main__":
    main()
