# LR baseline on weak labels (2026-10-22)

**Setup:** `scripts/train_classifier.py --model lr --include-unreviewed`. Text is encoded with
`sentence-transformers/all-MiniLM-L6-v2`, then a class-weighted `LogisticRegression(max_iter=2000)` is fit.
Data: `data/processed/agentdojo_workspace.jsonl` rebuilt at AgentDojo 089ed468 (rule_v1 labels), with the
builder's group-hash split. CPU only (torch 2.14.1+cpu, 12 logical cores). Models in `models/` (gitignored),
each with a `metrics.json`.

**Caveat — read first:** no human labels exist yet. Every `confirmation_required` rule label is
`needs_review`, so the only way to train all four classes was `--include-unreviewed`. These scores
measure **agreement with rule_v1**, not correctness. They are not results about AssentGate.

## Results

| run | train records | split | macro-F1 | unsafe-allow | false-block | unnecessary-confirm |
|---|---|---|---|---|---|---|
| side effects, with evidence + history | 5,615 | val (1,653) | 0.309 | 0.543 | 0.467 | 0.035 |
| | | test (1,491) | 0.669 | 0.597 | 0.008 | 0.000 |
| side effects, no evidence (baseline 4 ablation) | 5,615 | val | 0.431 | 0.557 | 0.334 | 0.001 |
| | | test | 0.631 | 0.615 | 0.011 | 0.000 |
| all calls, with evidence + history | 15,474 | val (4,189) | 0.376 | 0.532 | 0.179 | 0.018 |
| | | test (4,358) | 0.640 | 0.606 | 0.005 | 0.000 |

Test-set per-class recall (side effects, with evidence): authorized 0.98, safely_derived 0.81,
confirmation_required 0.23, blocked 0.59.

## Repeated group-wise CV (5 folds × 5 repeats)

`--cv 5 --cv-repeats 5 --seed 0`: train+val pooled (44 task groups; the test split isn't used), groups
shuffled into 5 folds per repeat with seeds 0–4, texts encoded once, LR refit per fold. Values are
mean ± std over 25 held-out folds, [min, max] in brackets. Same weak-label caveat as above.

| config | records | macro-F1 | unsafe-allow | false-block | unnecessary-confirm |
|---|---|---|---|---|---|
| side effects, with evidence | 7,268 | 0.366 ± 0.170 [0.14, 0.72] | 0.262 ± 0.240 [0.00, 0.79] | 0.229 ± 0.224 | 0.361 ± 0.298 |
| side effects, no evidence | 7,268 | 0.397 ± 0.173 [0.07, 0.81] | 0.201 ± 0.251 [0.00, 0.79] | 0.188 ± 0.235 | 0.380 ± 0.317 |
| all calls, with evidence | 19,663 | 0.529 ± 0.126 [0.30, 0.72] | 0.274 ± 0.237 [0.01, 0.81] | 0.092 ± 0.088 | 0.076 ± 0.086 |

Recall (side effects, with evidence): blocked 0.82 ± 0.12, confirmation_required 0.49 ± 0.30,
authorized 0.36 ± 0.34, safely_derived 0.10 ± 0.26 (absent from 2 folds).

**Paired evidence vs. no-evidence** (both side-effect runs hold out exactly the same groups per fold):
with evidence, macro-F1 −0.031 (sd 0.090; better in 6/25 folds), unsafe-allow +0.061 (sd 0.093; better
in 10/25), false-block +0.041 (better in 3/25), unnecessary-confirm −0.019 (better in 10/25).

What CV changes:
- **The single-split numbers above are within fold-to-fold noise.** Macro-F1 ranges 0.07–0.81 across folds,
  so which ~9 task groups land in the held-out fold matters more than any setting. Compare configs with paired
  folds, never with one split.
- **Unsafe-allow is lower on average than the single split suggested (0.20–0.27, not 0.54–0.62),** but it swings
  from 0 to ~0.8 by fold. The model's behaviour depends heavily on the task.
- **Evidence is slightly worse on these labels in most paired folds.** That's consistent with point 3 below
  (weak labels use oracle injection text the model can't see). Retest on human labels.
- Reproduce: `scripts/train_classifier.py --data data/processed/agentdojo_workspace.jsonl --model lr
  --include-unreviewed --side-effects-only --cv 5 --cv-repeats 5` (~5–7 min CPU per config).

## Reading it (single split)
1. **Unsafe-allow is 0.53–0.62 in every run.** The model approves most calls the rules would confirm or
   block. It almost never predicts `confirmation_required` (test recall 0.20–0.23) and over-predicts `authorized`.
2. **Val and test disagree by ~0.3 macro-F1.** The split is by task group, and there are only 54 groups, so val
   and test each hold a handful of tasks. One split is too noisy to compare runs. Use repeated group-wise splits
   before drawing conclusions.
3. **Evidence + history doesn't help here** (no-evidence is better on val, slightly worse on test). That's expected
   with these labels: rule_v1 separates `blocked` from `confirmation_required` using the oracle injection text,
   and the model sees both as `tool_output`. The labels depend on information the input doesn't contain. This is
   a label problem, not evidence that provenance is useless.
4. **What follows:** the weak labels can't support the main hypothesis. The pilot annotation and review of
   side-effecting records are the bottleneck, not the model.
