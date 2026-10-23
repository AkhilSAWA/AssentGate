# Related work, datasets and novelty (survey of 2026-10-23)

**Method and caveat:** web search, then each paper's arXiv abstract page or each repo's README. Full papers
were **not** read, so items marked *(abstract only)* may do more or less than stated. Most 2026 items are
preprints, not peer-reviewed. Before claiming novelty against a paper, read its full text, especially the
ones flagged **must read**.

## 1. Datasets: is there one that fits better?

**Short answer: no.** No public dataset found has per-call or per-argument labels with a
"confirm/ask" class for workspace tools. Several are useful **secondary evaluation sets**.

| dataset | what it labels | labels from | size | licence / release | fit for AssentGate |
|---|---|---|---|---|---|
| [AgentDojo](https://github.com/ethz-spylab/agentdojo) (current) | task utility + attack success per run; ground-truth calls per task | construction (benchmark authors) | 40 workspace user tasks × 14 injection tasks (v1.2.2) | MIT | **Keep as main e2e eval**; ground-truth calls are a clean per-call source (legit vs attack) |
| [AuthorizationBench](https://arxiv.org/abs/2610.27548) (Oct 2026) | out-of-authorization behaviour per trajectory, from an authorization contract per task | construction pipeline from real cases (abstract only) | 286 executable tasks, 6,864 trajectories, 10 domains | CC BY 4.0, [GitHub](https://github.com/PEACH-Research-Lab/AuthorizationBench) | **Strong secondary eval.** Tests "task-relevant ≠ authorized" *without* injection (benign pressure). Check domains/tools |
| [AgentDrift](https://arxiv.org/abs/2609.06972) (Sep 2026) | per step: benign / injection point / hijacked / failed injection | generation protocol + LLM screen + 1,200 hand-audited | 12,536 trajectories, 71,024 steps, incl. 1,500 hard negatives | CC BY 4.0, [GitHub](https://github.com/Asif-0209/AgentDrift) | Useful for allow-vs-block and **hard negatives** (benign content that looks like an attack). Step-level, not argument-level; synthetic, not AgentDojo |
| [AuthGateBench](https://arxiv.org/abs/2610.16426) (Oct 2026) | allow/block for high-risk coding-agent actions; **contrast pairs** (same action, different request) | LLM-generated, label flipped by construction | 1,400 | CC BY-NC-ND 4.0 (no derivatives); no data link found | Same minimal-pair idea, but coding domain and binary. Licence forbids adapting it |
| [ToolPrivacyBench](https://arxiv.org/abs/2606.28061) (Jun 2026) | which private "atoms" may reach which tool/sink; audits recorded **tool arguments** | per-case policy knowledge base | 2,150 cases | no data link found | Argument-level, but for privacy disclosure, not authorization of actions |
| [InjecAgent](https://arxiv.org/abs/2403.02691) | user tool vs attacker tool per case (implicit allow/block) | construction | — | MIT (per repo page) | Extra block-side test set; no ambiguity, no arguments to reason about |
| [τ-bench](https://arxiv.org/abs/2406.12045) / [ST-WebAgentBench](https://iclr.cc/virtual/2026/poster/10009904) | policy compliance incl. "explicit user confirmation" / user-consent dimension | benchmark policies | ST-Web: 222→375 tasks | — | Confirmation is *always required* by policy there, not selective. Not our task |

**Conclusion:** keep AgentDojo for training-by-construction and e2e evaluation. Add AuthorizationBench and
AgentDrift (both CC BY) as out-of-distribution test sets, after checking their formats. Human annotation
isn't needed to train or evaluate (see §4).

## 2. Prior and concurrent defenses: what they do

### Closest to AssentGate (must read)
- **[agent-action-guard](https://github.com/kostas221/agent-action-guard)** (MIT, README v0.5, Oct 2026): an
  approval layer between agent and tools on AgentDojo. It asks for approval of state-changing banking tools,
  bound to the *exact* call and used once, and **warns on argument values that did not come from the user**
  (rule-based literal checks, optional LLM judge). With a simulated approver, it reports legit-request warnings
  of 14.5% (rules) / 3.8% (hybrid), 0/432 attack success vs 49.8% ungated. One agent model, banking + Slack.
  **This is nearly AssentGate's design and hypothesis.** It's open source, so it should be a baseline.
- **[AuthGraph](https://arxiv.org/abs/2605.26497)** (May 2026, abstract only): an authorization graph derived
  from user intent in a clean context, compared against the execution-provenance graph at the **parameter-source
  level**. It claims to be the first to do so. AgentDojo GPT-4o: ASR 40%→1%, 76% utility; beats CaMeL, DRIFT,
  Progent. Nothing said about confirmations.
- **[RTBAS](https://arxiv.org/abs/2502.08966)** (2025, abstract only): information-flow control; tool calls run
  automatically when integrity/confidentiality hold, and the agent **asks the user only when it can't ensure
  them** (LM-judge and attention-saliency dependency screeners). AgentDojo: blocks all targeted attacks, ~2%
  utility loss. The abstract doesn't quantify confirmations saved.
- **[SARA](https://arxiv.org/abs/2608.27146)** (Aug 2026, abstract only): separates *action induction* (tool
  output suggests an action) from *execution authorization*; calls need goal, chain and **argument-level
  support**. AgentDojo/AgentDyn ASR ≤ 0.63%.

### Policy / capability approaches
- **[CaMeL](https://arxiv.org/abs/2503.18813)**: capabilities + data-flow tracking, by design. It notes user
  fatigue and policy-writing burden.
- **[Progent](https://arxiv.org/abs/2504.11703)**: DSL policies over tool names **and arguments**; an LLM drafts
  the policy from the task; narrowing updates are applied automatically, expansions need approval.
  AgentDojo/ASB/AgentPoison ASR → 0%.
- **[Conseca](https://arxiv.org/abs/2501.17070)**: an LLM generates a per-task policy from *trusted* context;
  a deterministic enforcer; on denial the planner may request user confirmation.
- **[IGAC](https://arxiv.org/abs/2606.22916)** (Jun 2026): intent certificates narrow the tool manifest and
  check payload effects; never exceeds static policy.
- **[ClawGuard](https://arxiv.org/abs/2604.11790)** (Apr 2026, abstract only): a user-confirmed rule set derived
  from the user's objective, enforced at each tool call. A search summary mentions an allow/block/ambiguous →
  approval queue, but that wasn't confirmed in the abstract.
- **[RAC](https://arxiv.org/abs/2609.23498)** (Sep 2026): *authorization drift* — each call is allowed alone,
  but the workflow exceeds the session's authority. Lineage-based check (relevant to AssentGate Phase 5).
- **[TBAC risk-adaptive](https://arxiv.org/abs/2510.11414)** (2025): an LLM judge gives risk + uncertainty;
  high risk/uncertainty → human approval.

### Task-alignment and detection
- **[Task Shield](https://aclanthology.org/2025.acl-long.1435.pdf)** (ACL 2025): does each call serve the user's
  goal? AgentDojo GPT-4o ASR 2.07%, utility 69.79%.
- **MELON** (ICML 2025, masked re-execution), **FIDES** (information-flow control), **DRIFT** (JSON-schema
  parameter checklists), **AgentAntibody** (Aug 2026, learns a user's security boundary over time).
- Detector caveat: [re-evaluation of injection detectors](https://arxiv.org/abs/2610.03448) shows false alarms on
  benign tool outputs turn into failed tasks.

### Framing
- **[Reframing agent security as agent-human interaction](https://arxiv.org/abs/2605.24309)** (May 2026): a survey
  of 59 papers, 21 products, 26 plugins. Users are stuck between "approval fatigue and uncontrolled agent
  autonomy"; runtime approval is common in industry, and intent anchoring is common in academia but absent in
  products. This supports AssentGate's motivation directly.
- **[AuthorizationBench](https://arxiv.org/abs/2610.27548)**: agents act out of authorization in 19.2% of runs.
  Consulting the user lowers this (14.0% vs 21.6%), so asking helps, which makes *when* to ask the question.

## 2b. Full-text check: how do the closest four handle *delegated* values? (2026-10-23)

Question: when a legitimate task makes an argument value come from tool output ("reply to whoever sent the
invoice"), does the system (a) accept it, (b) tell it apart from the same value planted as an instruction, and
(c) check that it is *exactly* what the request selects? Read from the arXiv HTML full texts and the repo docs
(`research-report.md`, `judge-design.md`). The WebFetch summaries cover the main text, but SARA's last ~15% (appendix)
wasn't read.

| system | accepts tool-sourced values? | data vs. planted instruction (≈ our test D) | checks the value is exactly what the request selects (≈ A–C) | when unsure | confirmations measured | trained? |
|---|---|---|---|---|---|---|
| **AuthGraph** | Yes, if an LLM planner (user prompt + tool catalog only) declares `allowed_source=observation_direct` with `source_tools=[…]` for that parameter | **No.** It string-matches the value anywhere in the declared tool's *raw* observation and accepts on a match; the LLM fallback runs only when there's no match. Granularity is per *tool*, not per field. §5 admits "same-observation pollution" | Partially: tool-level source, no field or selection rule | block (layers 1–3) | No | No (prompted GPT-4o/4o-mini) |
| **RTBAS** | Yes, if the developer labels that *region* high-integrity (e.g. Sender trusted, Subject/Content not) | Indirectly, through static per-field integrity labels set by the developer, **not conditioned on the user's request** | No | halt and ask the user (in AgentDojo runs blocked calls are just skipped) | Only FPR/FNR on a privacy benchmark (LM-judge FPR 0.081 vs confirm-every-time 0.297); none on AgentDojo | Attention screener: an LSTM over attention features (trained); LM-judge prompted |
| **SARA** | Yes, as `HISTORY_BOUND`: established by a successful, task-consistent earlier execution. It frames delegation explicitly as "runtime instantiation" vs. "authority expansion" (§2.1) | **Yes, semantically.** An Action Probe separates an address that appears as data from the same address in an operational request; No-History-Promotion stops a once-flagged origin from becoming support later | No explicit selection-rule or exact-set check; the invoice-reply case isn't analyzed | reject and replan; **never asks the user** | No | No (implementation of Probe/Guard unspecified) |
| **agent-action-guard** | Rules: **no**, they warn on any value not from the user or known payees ("refuse that update by design"). Hybrid: an LLM judge may clear the warning **when the request names the source file** | The judge never sees tool-output text, only provenance facts (where a value first appeared). It doesn't detect planted vs. data; the authors call it "refusal, not detection" | Partially (≈ test A: named source); no exact-match check | warn → simulated user rejects | **Yes**: legit-request warnings 14.5% rules / 3.8% hybrid (banking, README) | No (rules + prompted judge) |

**Shared open problem none of them solves (and AssentGate won't either):** a *poisoned source*. If the attacker
can edit the bill/invoice the user points to, the malicious call is identical to the legitimate one
(agent-action-guard keeps such cases warned on purpose; AuthGraph's "same-observation pollution"). Delegation by
reference assumes the named source's *facts* are genuine. State this as a limitation.

## 3. Novelty: what's taken, what's still open

**Taken (don't claim):** argument-/parameter-level checks (AuthGraph, SARA, Progent, DRIFT, agent-action-guard);
provenance vs. user values (agent-action-guard, AuthGraph, CaMeL); selective "ask only when unsure" (RTBAS,
TBAC, ClawGuard, agent-action-guard); exact-call single-use approvals (agent-action-guard); contrast-pair data
(AuthGateBench); workflow drift (RAC). The combination "argument-level authorization + selective confirmation"
from the original brief **is no longer novel by itself**.

**Still open, as far as these abstracts show (verify against full papers):**
1. **Delegation by reference, narrowed after the full-text check (§2b).** The *idea* that tool output can
   legitimately supply values is recognized: SARA's "runtime instantiation" + `HISTORY_BOUND`, AuthGraph's
   `observation_direct` sources, agent-action-guard's judge clearing a named source file. Test D (data vs.
   planted instruction) overlaps with SARA's Action Probe. **What none of them does:** check that the value is
   *exactly what the request's selection rule picks from the named source* (tests B–C: "everyone in the meeting"
   = that participant set, no extra recipient; "view access" ≠ `rw`; one of two matching appointments → ask).
   They also don't turn the unresolved case into a **targeted question instead of a block or replan**. Claim
   this narrower part, cite SARA for the framing, and don't claim the general idea.
2. **Confirmation burden as the headline metric, at matched safety.** Prior work reports ASR + utility.
   agent-action-guard reports a legit-warning rate; RTBAS doesn't quantify. A prompts-per-task vs
   attack-success **curve** across baselines (no gate, tool rules, confirm-all, provenance-only, AssentGate), with
   the same simulated approver, is a measurement contribution.
3. **A small learned gate with no LLM call at runtime.** Most of the systems above use an LLM to write policy or
   to judge (Progent, Conseca, AuthGraph, RTBAS's LM-judge, TBAC, SARA's probe). RTBAS's attention screener *is*
   a small trained model (LSTM), but it predicts which earlier outputs a call depends on, not whether the call is
   authorized, and it needs the agent's attention weights. A small trained scorer is cheaper and
   faster and doesn't itself read instructions as a chat LLM would. Whether it's accurate enough is an open,
   measurable question (it may lose to LLM judges; report it either way).
4. **Labels by construction from AgentDojo ground truth via argument mutations** (extra recipient, wider
   permission, swapped file, vague request, value from planted text). It's argument-level, three-way
   (allow/ask/block), executable, and needs no human labels. It's narrower than AuthGateBench's idea but in a
   different domain and granularity, with a reusable licence.
5. **Scoped-approval reuse** (Phase 5): measuring re-prompts avoided vs. scope violations when approvals
   generalize (same recipient set, same document). Related to RAC's drift and agent-action-guard's single-use
   approvals, but not measured there as far as the abstracts show.

**Suggested narrow claim (revised after §2b):** *Argument-level defenses either distrust tool-sourced values
(agent-action-guard's rules), accept them by coarse source matching (AuthGraph), or accept them by history without
asking the user (SARA). AssentGate checks a tool-sourced value against the request's selection rule (named source,
selection rule, exact match, data-not-instruction) and turns the unresolved cases into field-targeted questions
instead of blocks. On AgentDojo workspace it reduces confirmations per benign task at matched attack success
compared with confirm-all, tool-rule and provenance-only gates, using a small gate trained on labels constructed
from benchmark ground truth.* Limitation to state: poisoned named sources (§2b).

**First evidence (2026-10-23, scripted agent, `docs/results_delegation_eval.md`):** 64% of legitimate
sensitive values in AgentDojo workspace are delegated (exact fields of earlier outputs). Delegation rules + intent
check: 0.15 prompts/benign task at 0.9% attack success, vs strict provenance 0.45 at 0% and loose provenance 0.10
at 84%. The *learned* scorer doesn't beat the rules end to end yet, so the claim should currently rest on the
delegation rules and the measurement, with the learned part framed as an open question.

## 4. Consequences for the plan
- **Baselines to add:** agent-action-guard (MIT, AgentDojo, runnable: the closest competitor), plus Task Shield /
  Progent / CaMeL numbers from their papers where code isn't available.
- **Evaluation focus:** AgentDojo workspace tasks whose ground truth *delegates* a value (sender, participants,
  file found by search). Count them; they're where the claim lives.
- **Data:** labels by construction (no human annotation needed to train or evaluate). Keep the pilot as an
  optional audit of the generator (~50 records) for credibility.
- **Done 2026-10-23:** full text of AuthGraph, RTBAS, SARA and agent-action-guard's report/judge design (§2b).
  Still to read: AuthorizationBench, Progent's full paper, SARA's appendix.
- **Provenance-only baseline:** implement an "AuthGraph-style" variant in our gate (accept a value if it appears in
  the raw output of any earlier tool) and an "agent-action-guard-rules" variant (warn on any value not from the
  user). Both are cheap to build on `provenance.py`, and they're exactly what the claim is measured against.
