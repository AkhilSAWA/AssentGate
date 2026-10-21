# AssentGate build plan (6–8 weeks)

Each phase ends with something runnable. ⚠ marks steps that need your explicit go-ahead
(training, paid LLM inference, cloud compute).

## Phase 0 — Foundations (week 1)  ← you are here
- [x] Repo scaffold: schemas, policy, scoped approvals, gate, dispatcher, audit, tests (`src/assentgate/`). FastAPI deferred.
- [x] Fix known issues listed in `CLAUDE.md` (policy confirmation floor, unchecked extra args, approval normalisation, history wiring) and make the first git commit.
- [~] Clone and **pin** AgentDojo (builder tested on commit 089ed468) (and InjecAgent) to a specific commit/release; record versions in `docs/data_sources.md`.
- [ ] Read each repo's LICENSE and record it before redistributing any derived data.
- [ ] Literature review: read the 5 core papers properly + search for newer argument-level / confirmation work. Write `docs/related_work.md` and a one-paragraph *narrow* novelty claim.
- [ ] Freeze the annotation guidelines draft (`docs/annotation_guidelines.md`).

## Phase 1 — AgentDojo integration + cheap baselines (weeks 1–2)
- [x] Map AgentDojo's **workspace** suite tools (email / calendar / cloud drive) to `src/assentgate/workspace.py` (use AgentDojo's tool names/args).
- [ ] Insert AssentGate in front of tool execution in the AgentDojo agent pipeline (check the pinned version's pipeline API for where a defense element goes).
- [~] **Instrument provenance**: tag every value returned by a tool; when a later call argument contains a tagged value, emit `ArgumentEvidence(source=tool_output, verified=True)`. This is what makes "source evidence" real rather than guessed. (Done in our `Dispatcher` via `provenance.py`; still needs wiring into the AgentDojo pipeline.)
- [ ] Simulated approver: answers confirmation questions from ground truth (yes for benign task needs, no for injected goals). Needed to measure task completion with confirmations.
- [ ] ⚠ Run baselines 1–3 end-to-end (no gate, tool-level rules, confirm-every-sensitive-action). Requires an LLM agent → API cost or a local model.

## Phase 2 — Dataset (weeks 2–4)
- [x] `scripts/build_agentdojo_dataset.py`: AgentDojo saved runs → JSONL with rule labels (see `docs/dataset_and_training.md`).
- [ ] Manually review `needs_review` side-effecting records (`reviewed_label`).
- [ ] **Add benign-but-ambiguous cases** (recipient legitimately taken from an email, doc chosen by search). Benchmarks are attack-heavy; without these you cannot test the "fewer unnecessary confirmations" hypothesis.
- [ ] Pilot: 2 annotators × ~100 records → Cohen's κ; revise guidelines before scaling.
- [ ] Group-wise split by `group_id` (task + all its attack variants together). Hold out ≥1 attack family and ≥1 tool for generalisation tests.
- [ ] Double-annotate the test set; log disagreements.

## Phase 3 — Models (weeks 4–5)
- [x] `scripts/train_classifier.py` written (not yet run).
- [ ] Baseline 4a: sentence embeddings + logistic regression.
- [ ] ⚠ Main: DeBERTa-v3-small, 4-class (+ per-field head or per-field records, see ROADMAP §Improvements).
- [ ] Ablation (baseline 4): same model without evidence/history in the input.
- [ ] Calibrate (temperature scaling) and choose thresholds on validation only.
- [ ] Implement as a `Classifier` so it drops into `AssentGate(..., classifier=...)`.

## Phase 4 — End-to-end evaluation (weeks 5–6)
- [ ] ⚠ AgentDojo runs for all baselines + full AssentGate.
- [ ] Report: macro-F1, per-class P/R, unsafe-allow, false-block, benign utility, attack success rate, confirmations per task, unnecessary-confirmation rate, added latency, unseen tool / attack family.
- [ ] Headline comparison at a **matched unsafe-allow rate** (or as a curve), so "fewer confirmations" can't come from simply allowing more.

## Phase 5 — Scoped approvals & history (weeks 6–7)
- [ ] Approval scopes beyond exact match: value, recipient set, domain, document, count, time window.
- [ ] History checks: detect actions that exceed the original grant (e.g. approved sharing 1 doc, agent now shares 5).
- [ ] Measure: re-prompts avoided vs. scope-violations caught.

## Phase 6 — Demo & write-up (weeks 7–8)
- [ ] Streamlit demo: live tool calls, decisions, targeted questions, approval scopes, audit trail.
- [ ] Paper/report: method, dataset card, results, limitations (no production guarantees).

## Improvements over the initial design
1. **Instrumented provenance before modelling.** The model's key input is source evidence; get it from dataflow tagging in the dispatcher, not inference.
2. **Per-field formulation.** Make each (call, sensitive field) a record labelled ok / unresolved / prohibited, then derive the call decision (worst field + tool-intent check). Gives field labels for free and maps directly to targeted questions.
3. **Benign-ambiguous data** is the scarce, decisive data for the main hypothesis.
4. **Matched-safety reporting** (curves / fixed unsafe-allow) for the confirmation-reduction claim.
5. **Simulated approver** so confirmation burden and task completion are measurable end to end.
6. **Attack the gate itself.** Untrusted tool text enters the classifier input; test adaptive injections aimed at the classifier and consider passing only structured evidence, not raw content.
7. **Inter-annotator agreement early** — the authorized vs. safely-derived boundary is the likeliest source of label noise; consider merging them if κ stays low.
8. **Optional LLM-judge baseline** (zero-shot) — reviewers will ask how a small classifier compares; ⚠ costs inference.
