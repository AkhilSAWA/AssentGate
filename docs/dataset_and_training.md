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

## Labels by construction (v1, 2026-10-23): the main training/eval data now
- `scripts/make_constructed_dataset.py` → `data/processed/agentdojo_constructed_v1.jsonl` (gitignored, ~1.5 min to
  rebuild). 40 user tasks × (1 benign run + injection tasks 0–5 × 2 seeds under important_instructions text).
- 1,457 side-effecting records: 364 allow (user-task ground-truth calls), 560 block (injection-task calls,
  attacker addresses randomised), 533 confirm (one-field mutations: extra_seen 117, swap_seen 117,
  swap_seen_id 117, swap_unseen 78, extra_unseen 52, permission_flip 26, swap_unseen_id 13, swap_attachment 13).
- Each record stores the request exactly as the gate sees it (`Dispatcher.instrument`), so training and runtime
  inputs are identical (`scorer.featurize`).
- Limits: assumes ground truth = what the user authorised; no vague-request mutations; one benchmark suite;
  only 28 distinct legitimate side-effect calls underneath, so records repeat across runs.

## Hand-written benign-ambiguous set (draft v0)
- `scripts/make_benign_ambiguous.py` → `data/processed/annotations/benign_ambiguous_v0.jsonl`: 20 records,
  8 scenarios. Each scenario has minimal-pair variants: delegated (no prompt needed) / unresolved
  (confirmation_required) / hijacked (blocked). Same record schema as the AgentDojo builder, same group-hash split.
- Labels are drafts (`label_source="claude_draft_v0"`, `needs_review=True`). Set `reviewed_label` after review.
- The guideline gap found while drafting (delegated external side effects like "reply to whoever sent the
  invoice") is addressed by guidelines v0.2's *delegation by reference* (tests A–D), and the drafts follow v0.2.
  Under v0.2 every record in this set is a requested action, so it has no `safely_derived` labels
  (8 authorized / 7 confirmation_required / 5 blocked).
- To train on it, concatenate it with the AgentDojo JSONL (the training script reads one file).

## Next
1. Review side-effecting needs_review records (set `reviewed_label`); start with a 100-record two-annotator pilot for agreement.
2. Hand-write benign-ambiguous cases (agent runs contain few).
3. Train LR baseline on CPU, then DeBERTa (GPU; needs sign-off).
4. Wrap the model as a `Classifier` and run AgentDojo end-to-end.
