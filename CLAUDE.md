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

## Repo layout
- `src/assentgate/` — schemas, policy (HardPolicy), approvals (scoped, single-use), classifier
  (`Classifier` protocol; `SensitivityBaseline` = baseline 3 "confirm every side effect", NOT a trained model),
  questions, gate (policy → classifier → approvals), dispatcher (controlled execution, provenance, history + audit),
  provenance (value tagging), audit (SQLite), workspace (AgentDojo tool specs + FakeWorkspace).
- `tests/test_gate.py` — 24 tests, passing as of 2026-10-21.
- `scripts/build_agentdojo_dataset.py` — AgentDojo saved runs → weakly-labelled JSONL (`rule_v1`, `needs_review`, group-hash split).
- `scripts/train_classifier.py` — `--model lr|deberta`, `--no-evidence` ablation, `--side-effects-only`, `--include-unreviewed`.
- `data/raw/` and `models/` are gitignored.

## Commands (Windows, from repo root)
```
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
git clone https://github.com/ethz-spylab/agentdojo data\raw\agentdojo
.venv\Scripts\python scripts\build_agentdojo_dataset.py --runs data\raw\agentdojo\runs --out data\processed\agentdojo_workspace.jsonl
```

## Current status (2026-10-21)
Done: core gate/policy/approvals/dispatcher/audit + tests; dataset builder (tested on AgentDojo commit 089ed468:
16,856 workspace run files → 24,021 unique tool calls, 54 task groups); training script written (syntax/data-loading checked, never trained).

Initial scaffold committed (bf98639). Known issues 1–6 fixed 2026-10-21:
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

Next after fixes: 100-record two-annotator pilot (Cohen's κ) → review side-effecting records (`reviewed_label`) →
hand-write benign-ambiguous cases → LR baseline (CPU) → DeBERTa-v3-small (GPU, needs sign-off) →
wrap as `Classifier` → AgentDojo end-to-end (needs an LLM agent: API cost or local model).
