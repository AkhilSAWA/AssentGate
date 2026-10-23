# AssentGate — context for Claude Code

AssentGate is a research prototype: an authorization layer between an AI agent and its tools.
Before a tool runs it returns `authorized` / `safely_derived` / `confirmation_required` (with
field-targeted questions) / `blocked`, judging the tool AND its consequential arguments
(recipients, document ids, destinations, visibility). Earlier name: IntentLock.

Read these before substantive work:
- `docs/project_brief.md` — original spec: objectives, papers, metrics, scope, business case, handoff rules.
- `docs/ROADMAP.md` — phased plan with status checkboxes. Update checkboxes as work lands.
- `docs/dataset_and_training.md` — how the dataset is built from AgentDojo and how to train; first counts.
- `docs/annotation_guidelines.md` — labeling rules for the 4 classes.

## Ground rules (from the brief — do not break)
- Ask before training, paid LLM inference, or cloud execution.
- Never invent dataset counts, metrics, hardware or results; only report what was actually run.
- Don't call AssentGate "the first agent firewall"; novelty = argument-level authorization + selective confirmation, measured.
- Inferred provenance is not verified provenance (`verified=True` only from instrumentation).
- Hard policy always beats the model; no approval lifts a policy block. Confidence is not permission.
- Main hypothesis: fewer unnecessary confirmations at comparable safety — report at matched unsafe-allow rate.
- Scope: workspace tools (email, calendar, documents). No real payments, no shell tools.
- Never `git commit` or `git push`. Only edit the local working tree; the repo owner makes every commit.

## Repo layout
- `src/assentgate/` — schemas, policy (HardPolicy), approvals (scoped, single-use), classifier
  (`Classifier` protocol; `SensitivityBaseline` = baseline 3 "confirm every side effect", NOT a trained model),
  questions, gate (policy → classifier → approvals), dispatcher (controlled execution, provenance, history + audit),
  provenance (value tagging), audit (SQLite), workspace (AgentDojo tool specs + FakeWorkspace),
  annotation (Cohen's κ), agentdojo_adapter (`GatedToolsExecutor`, `GroundTruthApprover`, `GroundTruthProposer`,
  `ImportantInstructions` attack text, `run_scripted`; needs `.[eval]`), construct (labels by construction),
  scorer (`featurize`, `LearnedGate`). Classifiers: `StrictProvenance`, `LooseProvenance`, `DelegationRules`,
  `DelegationIntentRules`, `EscalateOnly`, `ToolLevelRules`, `SensitivityBaseline`.
- `tests/` — test_gate, test_annotation, test_benign_ambiguous, test_train_cv, test_construct_scorer,
  test_agentdojo_adapter (AgentDojo tests skip without it; slow).
- `scripts/build_agentdojo_dataset.py` — AgentDojo saved runs → weakly-labelled JSONL (`rule_v1`, `needs_review`, group-hash split).
- `scripts/sample_pilot.py` / `scripts/agreement.py` — blinded annotation pilot sheets / κ + disagreement log.
- `scripts/run_ground_truth_eval.py` — no-LLM end-to-end run (scripted ground-truth agent, benign + compromised) → `results/`;
  `scripts/summarise_ground_truth_eval.py` → summary over feasible attacks.
- `scripts/make_benign_ambiguous.py` — hand-written minimal-pair records (draft labels).
- `scripts/make_constructed_dataset.py` — labels by construction from AgentDojo ground truth (main data now).
- `scripts/eval_crossfit.py` — trains the learned scorer per fold (held-out user tasks + attack goals) and runs
  offline + e2e comparison of all gates → `results/crossfit_v1`, `models/crossfit_v1`.
- Results docs: `docs/results_delegation_eval.md` (headline), `docs/related_work.md` (prior work + novelty).
- `scripts/train_classifier.py` — `--model lr|deberta`, `--no-evidence` ablation, `--side-effects-only`, `--include-unreviewed`.
- `data/raw/`, `models/` and the regenerated `data/processed/agentdojo_*.jsonl` are gitignored; annotations are tracked.
- `docs/data_sources.md` — pinned versions + licenses.

## Commands (Windows, from repo root)
```
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pip install -e data\raw\agentdojo      # eval extra; slow (~15 min) the first time
.venv\Scripts\python -m pytest -q
git clone https://github.com/ethz-spylab/agentdojo data\raw\agentdojo && git -C data\raw\agentdojo checkout 089ed468
.venv\Scripts\python scripts\build_agentdojo_dataset.py --runs data\raw\agentdojo\runs --out data\processed\agentdojo_workspace.jsonl
.venv\Scripts\python scripts\sample_pilot.py --data data\processed\agentdojo_workspace.jsonl --out-dir data\processed\annotations\pilot_v1
.venv\Scripts\python scripts\agreement.py --dir data\processed\annotations\pilot_v1
.venv\Scripts\python -W ignore scripts\run_ground_truth_eval.py --out results\ground_truth_v1.2.2   # ~30 min CPU
.venv\Scripts\python scripts\train_classifier.py --data data\processed\agentdojo_workspace.jsonl --model lr --include-unreviewed --side-effects-only --cv 5 --cv-repeats 5   # ask before training
```

## Current status (2026-10-21)
Done: core gate/policy/approvals/dispatcher/audit + tests; dataset builder (tested on AgentDojo commit 089ed468:
16,856 workspace run files → 24,021 unique tool calls, 54 task groups); training script written (syntax/data-loading checked, never trained).

Known issues 1–6 fixed 2026-10-21:
1. Confirmation floors are opt-in `PolicyConfig` flags (`confirm_external_recipients`, `confirm_public_visibility`,
   both off). Policy by default only blocks (unknown/banned tools, missing/undeclared args, `blocked_domains`).
2. `provenance.py`: dispatcher tags tool outputs + the user request; reused values get `verified=True` evidence.
   Caller-supplied evidence is downgraded to `verified=False`. Matching is by value (exact, or containment for
   ≥8 chars), so short coincidental matches are possible.
3. Undeclared args (not in `required_args`/`optional_args`) are a policy block.
4. Approvals compare normalised values (str≡[str], order-free, emails case-insensitive, ids case-sensitive), 30 min TTL.
5. Dispatcher always sets `request.history` to its own last 5 calls (matches the builder's `HISTORY_LEN`).
6. `workspace.py` has all 24 AgentDojo workspace tools; a test checks sensitive fields match the builder.
   `FakeWorkspace` implements only a subset. AgentDojo signatures were read from GitHub at 089ed468, not from a local clone.

Dataset facts to remember: every `confirmation_required` rule label is `needs_review` (that class exists only
after human review); ~80% of records are read-only calls; oracle "injected" provenance is used only for labels —
model input sees it as `tool_output`.

Also done 2026-10-21: AgentDojo cloned + pinned at 089ed468 and installed into the venv; dataset rebuild reproduced
the counts above exactly; pilot sheets generated (100 records, 37 groups, seed 0); AgentDojo adapter + simulated
approver; `ToolLevelRules` = baseline 2 (confirm send/share/delete tools only); scripted ground-truth eval of
baselines 1–3 → `docs/results_scripted_eval.md` (scripted agent, NOT an LLM; attack metrics only over the 227/560
feasible pairs because v1.2 injection tasks 6–13 have empty ground truth); 20 draft benign-ambiguous records
(`scripts/make_benign_ambiguous.py`, labels need human review).
Annotation guidelines v0.2 (owner-approved, FROZEN for pilot_v1 2026-10-22; don't edit until κ is in, then v0.3).
Pilot sheets were regenerated (same 100 records, seed 0) with a `context` column of prior tool outputs from the run
files, needed for tests C/D; `pilot_v1/QUICK_REFERENCE.md` is the annotators' one-pager. *Delegation by reference*
tests A–D (named source, selection rule, exact match, data not instructions). `authorized` vs `safely_derived` is
now "who chose the action" (user-requested → authorized; agent-chosen read/lookup → safely_derived), and
send/share/invite/delete are never safely_derived. rule_v1 still uses the old boundary; reviewers override it.

Done 2026-10-22 (user approved): ml extra installed (torch 2.14.1+cpu); LR baseline trained on rule_v1 WEAK labels
(`--include-unreviewed`), 3 runs → `docs/results_lr_baseline.md`. Unsafe-allow 0.53–0.62, val/test macro-F1 differ
by ~0.3 (only 54 groups → noisy single split), evidence didn't help (rule labels use oracle injection text the
model can't see). Not a result about AssentGate; the human labels are the bottleneck.
`train_classifier.py` now tags weak-label runs `-weaklabels` and writes config + label counts into metrics.json.
Added `--cv K --cv-repeats R` (LR only): repeated group-wise CV on train+val, test untouched. 5×5 CV shows
macro-F1 std ~0.13–0.17 across folds (range ~0.1–0.8) → single-split numbers are noise; compare configs with
paired folds. Paired: evidence slightly worse than no-evidence on weak labels (−0.03 F1, better in 6/25 folds).

Done 2026-10-23 (pivot to labels by construction; user asked to build as much as possible):
field-level provenance (exact field vs contained-in-text + record snippet); realistic scripted attacks
(`--attack important_instructions`); provenance baselines; delegation rules (+ lexical action-intent check);
constructed dataset (1,457 records); cross-fitted learned scorer + hybrid. Headline (scripted agent, 218 feasible
attacks): delegation_intent 0.15 prompts/benign task @ 0.9% ASR vs strict provenance 0.45 @ 0%, loose 0.10 @ 84%.
Learned scorer alone is worse (15–18% ASR on unseen attack goals); hybrid reaches 0% only at strict's prompt cost.
Explicit delegation to content added (guidelines v0.3, `ProvenanceTracker.delegated_sources`,
`AuthorizationRequest.delegated_sources`, kind `named_content`): delegation_intent now 0.10 prompts/benign task @ 0.9% ASR.
Open issues: test C (semantic "is this the record the request picked?"), no LLM-agent run yet (plan + costs in
`docs/llm_agent_run_plan.md`; AgentDojo's AnthropicLLM sends temperature=0.0 → 400 on current API, max_tokens=1024
too small: fix before any paid run), no GPU for DeBERTa.

Older plan (human pilot is now an optional audit): humans fill the pilot sheets → κ → revise guidelines → review side-effecting records (`reviewed_label`) →
review + scale up the benign-ambiguous set → LR baseline (CPU, ask first) → DeBERTa-v3-small (GPU, needs sign-off) →
wrap as `Classifier` → AgentDojo with an LLM agent (API cost or local model, needs sign-off).
