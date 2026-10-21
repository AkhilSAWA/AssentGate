# AssentGate

Argument-level authorization and selective confirmation for tool-using AI agents.

AssentGate sits between an agent and its tools. Before a tool runs, it decides whether the
action **and its consequential arguments** (recipients, document IDs, destinations, visibility)
are permitted by the user's request, existing approvals and application policy:

| Decision | Meaning |
|---|---|
| `authorized` | Explicitly permitted (or covered by a scoped approval) |
| `safely_derived` | A bounded step reasonably needed for the authorized task |
| `confirmation_required` | Possibly permissible; specific fields need the user's OK |
| `blocked` | Prohibited or clearly unauthorized |

Task relevance does not imply authorization: a legitimate tool can still be called with an
unauthorized recipient, file or destination.

## Status

This is the initial scaffold. Everything below "Implemented" exists and is tested; everything
under "Roadmap" is the reference design and is **not built yet**.

**Implemented**

- `schemas.py`: Pydantic types for tool specs, tool calls, argument evidence, history and gate results.
- `policy.py`: deterministic hard policy. It blocks unknown or banned tools, missing or
  undeclared args, and blocked recipient domains. Confirmation floors for external recipients
  and public visibility are opt-in flags (off by default). Policy always beats the model.
- `approvals.py`: scoped approvals bound to the normalised values of a tool's sensitive fields,
  so a changed recipient or document triggers reevaluation. Single-use, with a 30-minute TTL by default.
- `provenance.py`: the dispatcher tags tool outputs and the user request. Arguments that reuse
  those values get `verified=True` evidence; caller-supplied evidence is never verified.
- `classifier.py`: the `Classifier` interface plus `SensitivityBaseline`, a placeholder
  that confirms every side-effecting call (evaluation baseline 3). **No trained model yet.**
- `questions.py`: template-based, per-field confirmation questions.
- `gate.py`: the pipeline (policy → classifier → approvals).
- `dispatcher.py` + `audit.py`: controlled execution, re-running the gate after approvals,
  feeding the last 5 calls in as history, and recording every decision to SQLite.
- `workspace.py`: AgentDojo workspace tool specs (names/args as of commit 089ed468) and
  in-memory sandbox handlers for a subset of them.

## Getting started

```bash
python -m venv .venv
```

```bash
.venv\Scripts\python -m pip install -e ".[dev]"
```

```bash
.venv\Scripts\python -m pytest -q
```

Minimal usage:

```python
from assentgate import AssentGate, AuthorizationRequest, Dispatcher, HardPolicy, PolicyConfig, ToolCall
from assentgate.workspace import WORKSPACE_TOOLS, FakeWorkspace

ws = FakeWorkspace()
gate = AssentGate(WORKSPACE_TOOLS, policy=HardPolicy(PolicyConfig(blocked_domains={"attacker.example"})))
dispatcher = Dispatcher(gate)
for name, handler in ws.handlers().items():
    dispatcher.register(name, handler)

req = AuthorizationRequest(
    user_request="Read the airline email and add my flight to my calendar.",
    tool_call=ToolCall(
        tool="send_email",
        arguments={"recipients": ["x@other.example"], "subject": "Fwd", "body": "..."},
    ),
)
out = dispatcher.submit(req)
print(out.result.decision, out.result.questions)
```

## AgentDojo integration

`assentgate.agentdojo_adapter.GatedToolsExecutor` replaces AgentDojo's `ToolsExecutor`, so every tool call an
agent proposes goes through the gate. Blocked or declined calls come back to the agent as tool errors. A
simulated approver (`GroundTruthApprover`) answers confirmation questions from the task's ground truth.
`scripts/run_ground_truth_eval.py` runs a scripted ground-truth agent (benign and fully compromised) through
the gate with no LLM calls. Its numbers describe scripted trajectories, not a real agent. Results for
baselines 1–3 are in [docs/results_scripted_eval.md](docs/results_scripted_eval.md).

```bash
.venv\Scripts\python -m pip install -e data\raw\agentdojo
```

```bash
.venv\Scripts\python -W ignore scripts\run_ground_truth_eval.py --out results\ground_truth_v1.2.2
```

## Annotation pilot

```bash
.venv\Scripts\python scripts\sample_pilot.py --data data\processed\agentdojo_workspace.jsonl --out-dir data\processed\annotations\pilot_v1
```

```bash
.venv\Scripts\python scripts\agreement.py --dir data\processed\annotations\pilot_v1
```

## Roadmap (6–8 week prototype)

1. **Data:** pin AgentDojo (main eval) and InjecAgent (supplementary); check licenses. Convert
   tool calls into JSONL records (request, policy, tool + args, evidence, history, label,
   unresolved fields). Write annotation rules; the benchmarks do **not** provide the four-class
   labels. Keep related tasks/variants in the same split.
2. **Baselines:** no gate; tool-level rules; confirm-every-sensitive-action (current placeholder);
   learned classifier without evidence/history.
3. **Models:** sentence-embeddings + logistic regression, then DeBERTa-v3-small. Training needs
   explicit sign-off first.
4. **End-to-end eval in AgentDojo:** attack success rate, benign utility, confirmations per task,
   unnecessary-confirmation rate, unsafe-allow / false-block rates, latency.
5. **Interfaces:** FastAPI authorization endpoint, Streamlit demo.

Main hypothesis: AssentGate reduces unnecessary confirmations at comparable safety, not
just by allowing more actions. Offline classifier scores alone don't show end-to-end security.

Out of scope: real payments, unrestricted shell execution, production security guarantees.

## Related work

AgentDojo, InjecAgent, BIPIA, Task Shield (closest task-alignment comparator), CaMeL
(capability / information-flow comparator). AssentGate is not the first agent firewall;
the research question is argument-level authorization combined with selective confirmation.
