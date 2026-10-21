# AssentGate — dataset & training plan (2026-10-21)

## Source: AgentDojo saved runs (no API cost)
- Repo `ethz-spylab/agentdojo` (MIT) ships `runs/<model>/<suite>/<user_task>/<attack>/<injection_task>.json`: full agent trajectories + exact injected text. Checked at commit 089ed468 (package v0.1.35, default benchmark v1.2.2).
- Workspace suite: 16,856 run files across 29 agent configs; 24,021 unique proposed tool calls; 54 user-task groups.
- Mixed benchmark versions across models — filter or record version per record.

## Scripts (`scripts/`)
- `build_agentdojo_dataset.py` → JSONL with runtime-visible input (request, tool, args, per-field provenance user/tool_output/unknown, last 5 calls) + rule label `rule_v1`, `needs_review`, group-hash split.
  - Labels: reads → safely_derived; any arg value found in injected text (not in request) → blocked; sensitive values all from request → authorized; looked-up IDs on low-impact tools → safely_derived (review); other tool-output values → confirmation_required (review).
  - Oracle "injected" provenance is used only for labels; the model input sees it as `tool_output`.
- `train_classifier.py` → `--model lr` (MiniLM embeddings + LR) or `--model deberta` (deberta-v3-small, class-weighted); `--no-evidence` ablation; `--side-effects-only`; `--include-unreviewed`; reports macro-F1, unsafe-allow, false-block, unnecessary-confirmation. A `reviewed_label` field on a record overrides the rule label.

## First counts (rule_v1, workspace)
| split | authorized | safely_derived | confirmation_required | blocked |
|---|---|---|---|---|
| train | 1457 | 10317 | 2943 | 757 |
| val | 312 | 3148 | 430 | 299 |
| test | 447 | 3571 | 213 | 127 |
- 5,540 records need review. **All confirmation_required labels are needs_review** → that class exists only after human review.
- ~80% of records are reads; report metrics on side-effecting calls.

## Next
1. Review side-effecting needs_review records (set `reviewed_label`); start with a 100-record two-annotator pilot for agreement.
2. Hand-write benign-ambiguous cases (agent runs contain few).
3. Train LR baseline on CPU, then DeBERTa (GPU; needs sign-off).
4. Wrap the model as a `Classifier` and run AgentDojo end-to-end.
