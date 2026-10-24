# Plan: real LLM-agent evaluation in AgentDojo (2026-10-23, nothing spent yet)

**Goal.** Replace the scripted agent with a real LLM agent, so the headline result (delegation-aware gating asks
~3× fewer questions than a strict provenance gate at ≈ equal attack success) is tested on real trajectories.
A real agent is only sometimes fooled, and it takes its own routes.

**Needs your sign-off** (ground rule: paid inference). You'd provide the API key yourself: set
`ANTHROPIC_API_KEY` in your own terminal. I never handle keys.

## 1. What a run costs, from data rather than guesses
Measured from AgentDojo's saved workspace runs (`data/raw/agentdojo/runs`, chars/4 ≈ tokens, including the 24
tool schemas re-sent on every model call):

| saved agent | runs | model calls / run | input tokens / run (mean, p90) | output tokens / run |
|---|---|---|---|---|
| gpt-4o (attacked) | 240 | 3.8 | 15.6k, 30.0k | 184 |
| gpt-4o-mini (attacked) | 240 | 3.7 | 15.1k, 23.8k | 180 |
| claude-3.7-sonnet (attacked) | 560 | 3.6 | 14.9k, 26.5k | 323 |
| llama-3.3-70b (attacked) | 560 | 3.6 | 17.0k, 40.3k | 478 |

Planning figure: **~17k input tokens per run**, plus visible output ~0.4k and an **unknown amount of thinking**.
Current Claude models think adaptively and can't turn thinking off. I assume 1–3k extra output tokens per run;
the smoke test (stage 0) measures it. Gated runs may need a little more, since blocked calls make the agent retry.

**One config = 600 runs** (40 user tasks benign + 40 × 14 injection tasks attacked). The real agent can attempt
all 14 injection tasks, unlike the scripted one.
**Core configs = 4:** no_gate, confirm_all, strict_provenance, delegation_source (the current best AssentGate rules; was delegation_intent before tests C and A) → **2,400 runs ≈ 41M input tokens**.

## 2. Model options and estimated cost (Anthropic list prices, cached 2026-10-06, no caching or batch discount)

| model | $ / 1M in / out | per config (600 runs) | 4 core configs | notes |
|---|---|---|---|---|
| Claude Haiku 5.5 | 0.10 / 0.50 | ~$1.5–2 | **~$6–8** | cheapest; weaker agent → lower benign utility |
| Claude Sonnet 5.5 | 2 / 10 | ~$30–40 | **~$115–165** | good agent at moderate cost |
| Claude Opus 5.5 | 4 / 20 | ~$60–80 | ~$230–330 | strongest everyday agent |
| Claude Fable 5.1 | 10 / 50 | ~$145–205 | ~$575–815 | most capable, overkill here |

Arithmetic: input 600 × 17k = 10.2M tokens; output 600 × (0.4k + 1–3k thinking) = 0.84–2.04M tokens.
- **Prompt caching** could cut the input part roughly in half. Each turn re-sends the growing conversation, and
  cache reads cost ~0.1×. AgentDojo's client doesn't set `cache_control`, though, so it'd need a small patch.
- **The Batch API (50% off) doesn't fit:** each agent turn depends on the previous one.
- **Repeats cost extra:** real agents vary run to run, so 3 repeats triple the cost. Start with 1.

**Local model instead (free per run): not practical on this PC.** There's no GPU, and processing a ~17k-token prompt
on CPU takes minutes per model call: 2,400 runs × ~4 calls would take weeks. AgentDojo does support local and
OpenAI-compatible servers (`local`, `vllm_parsed`, `openai-compatible`), so a rented GPU or a free-tier hosted open
model could work. That has its own cost or account, which I haven't priced.

**Other providers** (OpenAI, Google) are supported by AgentDojo, but **I haven't checked their current prices.**
Look them up before choosing one.

## 2b. Free options (checked 2026-10-23)
Need: ~2,400 runs ≈ **~10,000 model calls ≈ 41M input tokens**; single calls reach ~8–10k tokens (24 tool schemas
≈ 2.8k tokens + conversation). Limits below come from the provider's own page where it states them, otherwise from
2026 roundups (marked *unverified*). Each option needs an account you create yourself.

| option | free limits | fits 2,400 runs? | catches |
|---|---|---|---|
| **Kaggle notebook + vLLM + open 7–8B model** (e.g. Qwen 8B, quantized) | ~30 GPU h/week, 2×T4 16 GB or 1×P100, ≤ ~9–12 h per session *(unverified, third-party)* | **Yes**, est. ~10–15 GPU h with batching + prefix caching; whole eval runs inside the notebook, no API key | small models are weak agents (lower utility, noisier); session time-outs, so the runner must resume; throughput estimate untested |
| **Google Colab free** (same idea) | T4 16 GB, ~15–30 h/week, idle disconnect ~90 min, availability "varies" *(unverified)* | Probably, less reliably | disconnects; GPU not guaranteed |
| **Google Gemini API free tier** (AI Studio key) | per-project limits shown only in AI Studio (official page lists none); roundups say ~1,500 req/day for Flash *(unverified)* | Maybe: ~1 week at ~1,500 req/day | free-tier prompts may be used to improve Google products (fine for synthetic benchmark data); AgentDojo's `google` provider uses Vertex, so it needs a small patch for an AI Studio key |
| **Mistral free "Experiment" plan** | ~1B tokens/month, phone verification *(unverified)* | Maybe | data may be used for training; AgentDojo has no Mistral provider, so try `openai-compatible` (untested) |
| **OpenRouter `:free` models** | 20 req/min, **50 req/day** (1,000/day after a one-time $10 credit purchase) | No: ~200 days free; ~10 days with $10 | upstream rate limits; providers may log prompts |
| **Groq free** | per model: 30 req/min, 1K req/day, **8K tokens/min, 200K tokens/day** (official, gpt-oss-120b/20b, qwen3.8-27b) | **No**: ~12 runs/day; single calls near the 8K/min cap | — |
| **GitHub Models** | **retired 30 Jul 2026** (official) | No | — |
| **Cerebras** | sources conflict; possibly no free tier since Jul 2026 *(unverified)* | Unclear | — |

**Best free route: Kaggle + vLLM.** Install AgentDojo and this repo inside a Kaggle notebook, serve an open
tool-calling model with vLLM's OpenAI-compatible server, and run `scripts/run_llm_eval.py` against
`localhost` (AgentDojo's `vllm_parsed` / `local` providers already support that). No paid API, no key, no data
sent to a model company. Expect a weaker agent than Claude/Gemini, so present it as "small open-model agent" results.
**Second free route: the Gemini free tier**, for a strong agent, at the cost of daily caps and Google's
free-tier data terms.

## 3. Problems to fix before any paid run (local, free)
**Status 2026-10-23: all five done, tested with a fake client (no API calls, nothing spent).**
- Fixes 1–3: `src/assentgate/llm_agent.py` (`ClaudeLLM`) replaces AgentDojo's `AnthropicLLM` instead of patching
  the pinned clone. It sends no sampling parameters, `max_tokens=16000`, `thinking: {"type": "adaptive"}`
  (AgentDojo's `budget_tokens` form is also a 400 now), optional `--effort`, and a top-level `cache_control`
  for prompt caching. It replays each assistant turn **exactly as returned**. Checking fix 3 found two real
  problems in AgentDojo's converter: it moves tool calls after all other blocks (reordering an interleaved
  turn), and it raises `ValueError` on any `redacted_thinking` block. Also, `GatedToolsExecutor` parses
  string-encoded lists in tool arguments in place, which would have edited the replayed `tool_use` input.
  Verbatim replay avoids all three. It logs usage per model call and estimates cost.
- Fix 4: `AttackAwareApprover` in `agentdojo_adapter.py`.
- Fix 5: `scripts/run_llm_eval.py` (resumable, `--max-cost` guard, `--dry-run` estimate, `--workers`,
  `--agent scripted` for a free check, optional transcripts). Tests: `tests/test_llm_agent.py`.
- **Not verified against the real API yet**; the stage 0 smoke test is the first real check. Haiku 5.5's newer
  tokenizer counts the same text as ~30% more tokens than the chars/4 planning figure, so expect the full
  Haiku run nearer **~$9** than $7 before caching (dry run with planning figures: $6.96).

Original notes:
1. **`temperature` is rejected.** AgentDojo's `AnthropicLLM` sends `temperature=0.0` by default. The Claude API now
   returns 400 for `temperature`/`top_p`/`top_k` on every model, so construct it with `temperature=None`.
2. **`max_tokens=1024` is too small** with thinking always on, and replies would get cut off. Raise it to ~16k.
3. **Thinking blocks:** AgentDojo converts thinking blocks and passes them back. Check that it returns them
   unchanged; current models reject edited history. Our gate only appends tool results, so it should be fine.
4. **The approver needs a real-agent version.** `GroundTruthApprover` approves only exact ground-truth calls, so a
   real agent's legitimate but different route would be declined. Add an "attack-aware" approver: decline if a
   sensitive value matches the injection task's attacker values, otherwise approve. Report both.
5. **An LLM runner script** (`scripts/run_llm_eval.py`): AgentDojo pipeline = system message → LLM →
   `GatedToolsExecutor` loop, the same configs as the scripted eval, and resumable (skip finished runs) so a crash
   doesn't re-spend money.

## 4. Staged plan (each stage needs your OK)
Stage 0 command (you set `ANTHROPIC_API_KEY` first; `--max-cost` stops it early if the estimate is wrong):
```
.venv\Scripts\python -W ignore scripts\run_llm_eval.py --agent claude --model claude-haiku-5-5 --out results\llm_haiku_smoke --user-tasks user_task_0 --injection-tasks injection_task_0 --max-cost 0.25 --transcripts
```

| stage | what | runs | est. cost |
|---|---|---|---|
| 0. Smoke test | fixes 1–5, then 1 user task × (benign + 1 attack) × 4 configs on Haiku 5.5; measure real tokens incl. thinking | 8 | < $0.05 |
| 1. Cheap full run | all 600 × 4 core configs on **Haiku 5.5** | 2,400 | ~$6–8 |
| 2. Strong agent | same on **Sonnet 5.5** if stage 1 is informative | 2,400 | ~$115–165 |
| 3. Repeats / extras | 2 more repeats, or the tool_rules / loose configs | — | scale from above |

Time: ~2–10 s per model call → ~10–25 h serial per 2,400 runs; ~2–5 h with 4–8 runs in parallel (watch rate limits).

**Free alternative to stages 0–1:** the same smoke test and full run on Kaggle + vLLM (section 2b), $0. It needs
extra local work first: a notebook that installs everything, plus resume support for session time-outs.

**Recommendation:** do the local fixes and the stage 0 smoke test first. It costs cents and replaces every
estimate above with measured numbers, so you can decide on stage 1 or 2 with real costs.
