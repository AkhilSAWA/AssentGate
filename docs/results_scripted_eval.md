# Scripted ground-truth evaluation (2026-10-21)

**What this is:** AgentDojo v1.2.2 workspace suite (pinned commit 089ed468), run with a *scripted* agent that
proposes each user task's ground-truth calls. In attacked runs the agent then also proposes the injection
task's ground-truth calls, i.e. a fully compromised agent. No LLM, no API cost, local CPU (~30 min per config).

**What this is not:** a result for a real LLM agent. A real agent gets injected only sometimes, and it
takes different routes than the ground truth. The simulated approver (`GroundTruthApprover`) answers
prompts from ground truth, which no real user can match, so the attack numbers below are best-case for
every gated config.

Reproduce: `scripts/run_ground_truth_eval.py --out results/ground_truth_v1.2.2`, then
`scripts/summarise_ground_truth_eval.py --dir results/ground_truth_v1.2.2`.

## Feasible attacks
560 attacked pairs (40 user tasks × 14 injection tasks). In v1.2, injection tasks 6–13 have an empty
`ground_truth()`, so the scripted agent never attempts them, and injection task 5 succeeds ungated in only
27/40 pairs. Attack metrics therefore use the **227 pairs where the attack succeeds with no gate**.

## Results

| config | benign utility | prompts / benign task | attack success (227 feasible) | utility under attack | prompts / attacked task |
|---|---|---|---|---|---|
| no gate (baseline 1) | 1.000 | 0.000 | 1.000 | 0.026 | 0.000 |
| tool-level rules (baseline 2) | 1.000 | 0.250 | 0.176 | 0.828 | 1.181 |
| confirm every side effect (baseline 3) | 1.000 | 0.700 | 0.000 | 1.000 | 1.784 |
| baseline 3, every prompt declined | 0.450 | 0.700 | 0.000 | 0.476 | 1.784 |

All runs: 40 benign + 560 attacked per config, 0 errors.

## Reading it
- Baseline 3 stops every scripted attack but asks 0.70 questions per benign task. Every one of those is for
  an action the task needed, so the user has to answer them all.
- Baseline 2 asks far fewer questions (0.25) but lets 17.6% of feasible attacks through. They go via tools it
  allows without looking at arguments (e.g. calendar participants, file writes).
- Declining every prompt costs 55% of benign tasks, so prompts can't simply be ignored.
- **Target for the learned gate:** close to baseline 3's attack success with prompts closer to baseline 2.
  Report at matched attack success / unsafe-allow, per the main hypothesis.
- Baseline 2's tool list (`ToolLevelRules.DEFAULT_CONFIRM_TOOLS`) is a design choice; report it with the numbers.
