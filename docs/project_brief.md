# AssentGate — Project Handoff

**Working title:** Learning Argument-Level Authorization and Selective Confirmation for AI Agents  
**Earlier name:** IntentLock

## Project Overview

AssentGate is an authorization layer between an AI agent and its tools. Before a tool executes, it checks whether the action and its consequential arguments are permitted by the user's request, existing approvals, and application policy.

It produces four decisions:

- **Authorized:** Explicitly permitted.
- **Safely derived:** A bounded step reasonably needed to complete the authorized task.
- **Confirmation required:** A potentially permissible action with unresolved authorization.
- **Blocked:** A prohibited or clearly unauthorized action.

**Example:** A user asks an agent to read an airline email and update their calendar. Reading the email and creating the event may be permitted; following an instruction inside the email to forward the user's inbox must be blocked.

The key distinction is that **task relevance does not automatically imply authorization**. A legitimate tool can be used with an unauthorized recipient, amount, file, or destination.

## Four Objectives

1. Classify proposed tool calls into the four authorization categories.
2. Check sensitive arguments against user instructions, policy, and available source evidence.
3. **Research objective:** Ask targeted questions about unresolved fields rather than requesting approval for the entire task repeatedly.
4. **Research objective:** Track scoped approvals and relevant action history to detect later actions that exceed the original permission.

These are proposed research contributions, not claims that approval systems or provenance checking are entirely new.

## Research Papers and Prior Work

| Paper | Existing contribution | Relevance to AssentGate |
|---|---|---|
| [AgentDojo](https://arxiv.org/abs/2406.13352) | Stateful evaluation of prompt-injection attacks and defenses | Main evaluation environment |
| [InjecAgent](https://arxiv.org/abs/2403.02691) | Tool-oriented injection benchmark, including harm and exfiltration | Supplementary attack examples |
| [BIPIA](https://arxiv.org/abs/2312.14197) | Indirect prompt injection and instruction/data boundary defenses | Trust-boundary research |
| [Task Shield](https://arxiv.org/abs/2412.16682) | Checks whether instructions and actions contribute to the user's task | Closest task-alignment comparator |
| [CaMeL](https://arxiv.org/abs/2503.18813) | Capability-based and information-flow protection | Architectural security comparator |

**Research gap:** Investigate argument-level authorization and selective confirmation together, measuring whether a learned model can reduce unnecessary approval prompts without increasing unauthorized actions.

**Novelty boundary:** Existing assistants, frameworks, and security systems already provide tool approvals, policies, and related defenses. Do not describe AssentGate as the first agent firewall. A complete literature review is still needed before asserting a narrower novelty claim.

## Public Data and Training

### Sources

- **AgentDojo:** https://github.com/ethz-spylab/agentdojo
- **InjecAgent:** https://github.com/uiuc-kang-lab/InjecAgent
- **BIPIA, optional:** https://github.com/microsoft/BIPIA

Download permitted benchmark data and environments, pin their versions, and check licenses.

### Dataset Construction

Convert tool-call examples into records containing:

- User request and relevant policy.
- Tool description and arguments.
- Available argument-source evidence.
- Relevant prior actions and approvals.
- Authorization label.
- Unresolved or prohibited fields.

**Important:** These benchmarks do not directly supply AssentGate's complete four-class authorization labels. Additional annotation is required.

Use explicit annotation rules, manually review a held-out evaluation set, and record disagreements. Do not equate "external source" with "malicious": external information can legitimately supply values required by the task.

Keep related tasks, trajectories, and attack variants in the same split to prevent leakage.

## Model and Tech Stack

This is the **reference design**; confirm the actual choices against the implementation.

| Component | Recommended technology | Purpose |
|---|---|---|
| Language and preprocessing | Python, pandas | Prepare and inspect data |
| Data validation | Pydantic | Validate tool calls and decisions |
| Model training | PyTorch, Hugging Face Transformers | Train authorization classifier |
| Main model | DeBERTa-v3-small | Four-class text classification |
| Lightweight baseline | Sentence embeddings + logistic regression | Establish a simple comparison |
| Policy layer | Python rules | Enforce non-overridable constraints |
| Evaluation environment | AgentDojo | Measure security and task utility |
| Backend | FastAPI | Expose authorization endpoints |
| Demo interface | Streamlit | Show calls, decisions, and approvals |
| Storage | JSONL and SQLite | Dataset records and audit events |
| Testing | pytest | Validate policies and approval handling |

Avoid adding infrastructure unless the existing implementation needs it.

### Model Inputs and Outputs

**Input:** User request + policy + proposed tool/schema + arguments + available source evidence + bounded history.

**Output:** Decision category, affected fields, and confidence.

Start with a trained classifier and template-based confirmation questions. Field-level predictions require field-level annotations.

**Example question:** "May this document be shared with this external recipient?"

A confidence score is not permission. Hard policies must take precedence over model predictions.

## System Workflow

User request
→ Agent proposes a tool call
→ AssentGate intercepts it before execution
→ Validate schema and apply hard policies
→ Evaluate authorization using the classifier
→ Allow, request confirmation, or block
→ Revalidate any approved or edited operation
→ Execute through the controlled dispatcher
→ Record the decision and outcome

Bind approvals to the relevant action and arguments. A changed recipient, amount, or document should trigger reevaluation.

Provenance should come from instrumentation or explicit evidence where possible. Do not present inferred source guesses as verified provenance.

## Evaluation

### Baselines

1. No additional authorization gate.
2. Tool-level rules.
3. Confirmation for every sensitive action.
4. Learned classifier without source evidence/history.
5. Full AssentGate.

Include a task-alignment comparator if implementation resources permit.

### Metrics

- Macro F1 and per-class precision/recall.
- Unsafe-allow and false-block rates.
- Benign task completion.
- Attack success rate.
- Confirmation prompts per task.
- Unnecessary-confirmation rate.
- Added latency.
- Performance on unseen tools or attack families.

**Main hypothesis:** AssentGate reduces unnecessary confirmations at a comparable safety level—not simply by allowing more actions.

Offline classification results alone do not demonstrate end-to-end agent security. Use sandbox execution checks for that claim.

## Scope and Feasibility

Target a 6–8 week prototype focused on:

- Workspace tasks: email, calendar, and documents.
- Approximately five or six tools.
- Recipients, document identifiers, destinations, and visibility.
- One small classifier.
- Deterministic policies.
- Template-based confirmations and scoped approvals.

Exclude real payments, unrestricted shell execution, and production security guarantees. General multi-step permission composition is a stretch goal.

## Business Case

**Product:** A model-independent authorization SDK/API for companies deploying tool-using agents.

**Potential customers:** Enterprise workflow software, agent developers, and productivity applications.

**Value proposition:** Fewer unauthorized side effects, fewer unnecessary approval prompts, and understandable audit records.

**Possible revenue:** API usage, private deployment, and enterprise support.

The market is competitive. Differentiation must come from measured safety, lower interruption burden, integration simplicity, or a focused workflow—not the existence of an approval gate.

## Important Handoff Notes

- The earlier handoff described implementation as complete, but no code or results are included here. Confirm actual status and components with the user.
- Do not invent dataset counts, hardware, metrics, screenshots, or implementation details.
- Distinguish this reference design from repository-confirmed features.
- Public benchmark availability does not eliminate annotation or licensing work.
- Do not claim all five papers were fully read without completing that review.
- Obtain explicit permission before training, paid inference, or cloud execution.
- Continue AssentGate rather than proposing another project.
