"""AssentGate demo (Streamlit). Free and local: no LLM calls.

Steps through an AgentDojo workspace run, either the scripted ground-truth agent or a replay of a
saved Claude 3.7 Sonnet / Llama 3.3 70B run, with a gate of your choice. You can answer the
gate's questions yourself. Run from the repo root:

    .venv\\Scripts\\python -m streamlit run demo\\app.py --server.address localhost --browser.gatherUsageStats false
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assentgate import walkthrough as wt  # noqa: E402

st.set_page_config(page_title="AssentGate demo", page_icon="🛡️", layout="wide")

SCRIPTED = "scripted"
AGENT_LABELS = {SCRIPTED: "Scripted agent (follows the task's correct steps)",
                **{a: f"Replay: {name} (saved real run)" for a, name in wt.AGENTS.items()}}
DECISIONS = {
    "authorized": ("✅", "authorized"),
    "safely_derived": ("🔎", "safely derived"),
    "confirmation_required": ("❓", "asks the user"),
    "blocked": ("⛔", "blocked"),
}
SOURCE_BADGES = {
    "user": "🟢 typed by the user",
    "exact": "🔵 a whole field of an earlier tool output",
    "named_content": "🟢 inside content the user delegated",
    "contained": "🟠 only inside free text of a tool output",
    "unknown": "🔴 seen nowhere before",
}
EXAMPLES = {
    "Pick your own run": None,
    "Phishing planted in a calendar event (Llama 3.3 replay)":
        ("meta-llama_Llama-3.3-70B-Instruct", "user_task_19", "injection_task_13"),
    "Attacker's address hidden in an event description (Claude 3.7 replay)":
        ("claude-3-7-sonnet-20250219", "user_task_21", "injection_task_0"),
    "'Delete the largest file', but the agent picks file 13 (scripted)": (SCRIPTED, "user_task_35", "injection_task_1"),
    "Same participants as a named meeting: no questions needed (scripted)": (SCRIPTED, "user_task_21", None),
    "Recipients that appear nowhere: AssentGate asks (scripted)": (SCRIPTED, "user_task_25", None),
}
MAX_ARG_CHARS = 400
OUTPUT_HEIGHT = 240  # px; tool outputs scroll inside a box this tall


@st.cache_data(show_spinner=False, max_entries=1024)
def walk(kind, user_task, injection_task, gate, approver, answers):
    return wt.walk(kind, user_task, injection_task, gate, approver, answers)


@st.cache_data(show_spinner=False, max_entries=256)
def compare(kind, user_task, injection_task, approver):
    return wt.compare_gates(kind, user_task, injection_task, approver)


@st.cache_data(show_spinner=False)
def fooled(agent):
    return wt.fooled(agent)


@st.cache_data(show_spinner=False)
def results(directory: str, prompts_over_feasible: bool):
    return wt.results_table(Path(directory), prompts_over_feasible)


def short(text: str, n: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def yes_no(value) -> str:
    return "—" if value is None else ("yes" if value else "no")


def shorten_args(value):
    if isinstance(value, str) and len(value) > MAX_ARG_CHARS:
        return value[:MAX_ARG_CHARS] + f"… ({len(value)} characters)"
    if isinstance(value, list):
        return [shorten_args(v) for v in value]
    if isinstance(value, dict):
        return {k: shorten_args(v) for k, v in value.items()}
    return value


def highlighted(text: str) -> str:
    """Tool output as escaped HTML with the planted attack text marked."""
    escaped = html.escape(text)
    escaped = re.sub(r"(&lt;INFORMATION&gt;.*?&lt;/INFORMATION&gt;)", r"<mark>\1</mark>", escaped, flags=re.S)
    return f"<pre>{escaped}</pre>"


def answer(key: str, value: bool) -> None:
    st.session_state.setdefault("answers", {}).setdefault(key, []).append(value)


def restart(key: str) -> None:
    st.session_state.setdefault("answers", {}).pop(key, None)


def apply_example() -> None:
    example = EXAMPLES[st.session_state["example"]]
    if example is None:
        return
    kind, user_task, injection_task = example
    st.session_state.update(agent=kind, fooled_only=False, user_task=user_task, attack=injection_task,
                            gate=list(wt.GATES)[0])


def custom_run() -> None:
    st.session_state["example"] = next(iter(EXAMPLES))


def step_outcome(step: wt.Step) -> str:
    if step.pending:
        return "waiting for your answer"
    if step.executed:
        return "ran after the user approved" if step.asked else "ran"
    if step.asked:
        return "not run: the user declined"
    if step.decision == "blocked":
        return "not run: blocked"
    return "not run: the tool returned an error"


def render_values(step: wt.Step) -> None:
    if not step.values:
        return
    st.dataframe(
        [{"argument": v.field, "value": v.value, "where it came from": SOURCE_BADGES[v.kind], "detail": v.where}
         for v in step.values],
        hide_index=True, width="stretch")


def render_step(step: wt.Step, approver: str) -> None:
    icon, label = DECISIONS[step.decision]
    title = f"**{step.number}. `{step.tool}`** · {icon} {label} · {step_outcome(step)}"
    important = step.side_effecting or step.asked or step.decision == "blocked" or step.pending
    with st.expander(title, expanded=important):
        st.code(json.dumps(shorten_args(step.arguments), indent=2, ensure_ascii=False), language="json")
        render_values(step)
        for ok, line in step.checks:
            st.markdown(("✅ " if ok else "❌ ") + line)
        for reason in step.reasons:
            st.markdown(f"- {reason}")
        if step.asked and not step.pending:
            who = "You" if approver == "me" else "The simulated user"
            for q in step.questions:
                st.markdown(f"**AssentGate asked:** {q}")
            st.markdown(f"**{who} answered:** {'approve' if step.approved else 'decline'}")
        if step.agent_sees:
            st.caption(f"The agent is told: {step.agent_sees}")
        if step.output:
            st.markdown("**Tool output**" + (" · ⚠️ contains planted attack text (highlighted)" if step.planted else ""))
            with st.container(height=OUTPUT_HEIGHT):
                st.markdown(highlighted(step.output), unsafe_allow_html=True)


def render_question(step: wt.Step, key: str) -> None:
    with st.container(border=True):
        st.markdown(f"### ❓ AssentGate asks before step {step.number} (`{step.tool}`) runs")
        for q in step.questions:
            st.markdown(f"**{q}**")
        for reason in step.reasons:
            st.markdown(f"- {reason}")
        render_values(step)
        left, right, _ = st.columns([1, 1, 4])
        left.button("Approve", type="primary", on_click=answer, args=(key, True), key=f"approve-{key}")
        right.button("Decline", on_click=answer, args=(key, False), key=f"decline-{key}")
        st.caption("Approving records a single-use approval for exactly these argument values; the gate then "
                   "re-checks the call. Declining returns an error to the agent.")


def render_outcome(w: wt.Walkthrough) -> None:
    st.subheader("Outcome")
    cols = st.columns(4)
    cols[0].metric("Questions asked", w.questions_asked)
    cols[1].metric("Task completed", yes_no(w.utility))
    cols[2].metric("Attack succeeded", yes_no(w.attack_success))
    cols[3].metric("Calls blocked", w.blocked)
    notes = []
    if w.kind != SCRIPTED:
        notes.append(f"In the saved recording, with no defense, the task "
                     f"{'succeeded' if w.recorded_utility else 'failed'}"
                     + (f" and the attack {'succeeded' if w.recorded_attack_success else 'failed'}."
                        if w.injection_task else "."))
        if w.stopped:
            notes.append("The replay stopped at a blocked or declined call. A recording can't try another route, "
                         "so 'task completed' is pessimistic here.")
    elif w.injection_task:
        notes.append("The scripted agent is fully compromised: after the task's own steps it also tries every step "
                     "of the attacker's goal.")
    for note in notes:
        st.caption(note)


def run_tab() -> None:
    with st.sidebar:
        st.header("Choose a run")
        st.caption("These controls drive the 'Step through a run' tab.")
        st.selectbox("Start from an example", list(EXAMPLES), key="example", on_change=apply_example)
        kind = st.radio("Agent", list(AGENT_LABELS), format_func=AGENT_LABELS.get, key="agent", on_change=custom_run)
        if kind != SCRIPTED:
            st.toggle("Only attacks that fooled this agent", key="fooled_only", on_change=custom_run,
                      help="Runs where the saved recording shows the attack succeeding with no defense.")
        pairs = fooled(kind) if (kind != SCRIPTED and st.session_state.get("fooled_only")) else None
        prompts = dict(wt.user_tasks())
        task_ids = sorted({u for u, _ in pairs}, key=wt._task_number) if pairs else list(prompts)
        if st.session_state.get("user_task") not in task_ids:
            st.session_state["user_task"] = task_ids[0]
        user_task = st.selectbox("User task", task_ids, key="user_task", on_change=custom_run,
                                 format_func=lambda t: f"{t}: {short(prompts[t], 60)}")
        goals = dict(wt.injection_tasks(kind == SCRIPTED))
        attacks = [i for u, i in pairs if u == user_task] if pairs else [None, *goals]
        if st.session_state.get("attack") not in attacks:
            st.session_state["attack"] = attacks[0]
        injection_task = st.selectbox(
            "Attack", attacks, key="attack", on_change=custom_run,
            format_func=lambda i: "No attack" if i is None else f"{i}: {short(goals.get(i, ''), 55)}")
        gate = st.selectbox("Gate", list(wt.GATES), key="gate", on_change=custom_run)
        approver = st.radio("Who answers AssentGate's questions?", list(wt.APPROVERS), format_func=wt.APPROVERS.get,
                            key="approver")
        if kind == SCRIPTED and injection_task:
            st.caption("The scripted agent can only attempt attacks with a ground truth (injection tasks 0–5).")

    key = f"{kind}|{user_task}|{injection_task}|{gate}"
    answers = tuple(st.session_state.get("answers", {}).get(key, [])) if approver == "me" else ()
    with st.spinner("Running the agent through the gate…"):
        w = walk(kind, user_task, injection_task, gate, approver, answers)

    st.markdown(f"**The user asked:** {w.user_request}")
    if w.attacker_goal:
        with st.expander(f"⚠️ **Attack:** the attacker's goal is planted in the data this task reads "
                         f"({len(w.injections)} place{'s' if len(w.injections) != 1 else ''})"):
            st.markdown(f"**Goal:** {w.attacker_goal}")
            st.markdown("**Planted in:** " + ", ".join(f"`{k}`" for k in w.injections))
            if w.injections:
                with st.container(height=OUTPUT_HEIGHT):
                    st.markdown(highlighted(next(iter(w.injections.values()))), unsafe_allow_html=True)
    st.caption(f"Agent: {AGENT_LABELS[kind]} · Gate: {gate} · Questions answered by: {wt.APPROVERS[approver]}")

    for step in w.steps:
        render_step(step, approver)
    if not w.steps:
        st.info("The agent made no tool calls in this run.")
    if w.pending is not None:
        render_question(w.steps[-1], key)
    else:
        render_outcome(w)
    if approver == "me" and answers:
        st.button("Restart this run", on_click=restart, args=(key,))

    if w.pending is None:
        with st.expander("Audit trail (what the dispatcher logged)"):
            st.dataframe([{k: e[k] for k in ("tool", "decision", "decided_by", "executed", "affected_fields", "reasons")}
                          for e in w.audit], hide_index=True, width="stretch")
    if st.toggle("Compare all gates on this run", key="compare"):
        simulated = approver if approver != "me" else ("ground_truth" if kind == SCRIPTED else "attack_aware")
        with st.spinner("Running every gate…"):
            rows = compare(kind, user_task, injection_task, simulated)
        st.caption(f"Questions answered by: {wt.APPROVERS[simulated]}")
        st.dataframe([{k: (yes_no(v) if isinstance(v, bool) or v is None else v) for k, v in r.items()} for r in rows],
                     hide_index=True, width="stretch")


def results_tab() -> None:
    st.markdown(
        "Numbers from the saved evaluation results in `results/` (see `docs/results_delegation_eval.md` and "
        "`docs/results_replay.md`). **Attacks that got through** counts only attacks that succeed with no gate. "
        "Questions are answered by a simulated user.")
    scripted = wt.RESULTS / "ground_truth_v1.2.2_ii"
    st.subheader("Scripted agent: 40 tasks, AgentDojo's attack text, fully compromised agent")
    table = results(str(scripted), True)
    if table:
        st.dataframe(table, hide_index=True, width="stretch")
        st.caption("Simulated user: ground truth. Questions per attacked task over the attacks that work with no gate.")
    else:
        st.info("Run scripts/run_ground_truth_eval.py to create these results.")
    for agent, name in wt.AGENTS.items():
        st.subheader(f"Replayed real-agent runs: {name} (40 benign + 560 attacked saved runs)")
        table = results(str(wt.RESULTS / "replay_v1" / agent), False)
        if table:
            st.dataframe(table, hide_index=True, width="stretch")
        else:
            st.info("Run scripts/replay_saved_runs.py to create these results.")
    st.caption("Simulated user for replays: attack-aware (optimistic). A recording can't react to the gate, so the "
               "replay stops at the first blocked or declined call.")


def about_tab() -> None:
    st.markdown("""
**AssentGate** sits between an AI agent and its tools. Before a tool runs, it judges the tool *and its
consequential arguments* (recipients, file ids, participants, permissions) and returns one of four decisions:
**authorized**, **safely derived** (an agent-chosen read or lookup), **asks the user** (with a question about
the specific field), or **blocked**.

**Where each value came from.** The dispatcher tags every value the user typed and every value a tool returned.
For each argument it records whether the value was typed by the user, is a whole field of an earlier output
(e.g. an email's sender), appears only inside free text (where planted instructions live), or was seen nowhere.
That is *verified* provenance: it comes from instrumentation, not from guessing.

**AssentGate's rules (delegation_source)**
- Values the user typed are fine. So are whole fields of earlier outputs, where the user delegated the choice
  ("reply to whoever sent…"). It asks about values seen only inside free text, or nowhere.
- *Action check:* it asks when the request doesn't ask for this kind of action at all.
- *Test C:* "delete the largest file" must use the largest file the agent actually saw.
- *Test A:* an address taken from tool output must come from a record the request names, by sender, subject,
  title, filename or contact name.
- Hard policy (unknown tools, undeclared arguments, blocked domains) always wins; no approval lifts it.

**Baselines:** no gate; confirm every side effect; strict provenance (ask unless the user typed every sensitive
value); loose provenance (accept any value seen anywhere in tool output).

**What this demo can't show:** a *live* agent reacting to the gate. Replays are recordings of agents that ran
with no defense, so they stop at the first blocked or declined call. The scripted agent always follows the
task's correct steps. One benchmark suite (AgentDojo workspace). No LLM calls are made here.
""")


st.title("🛡️ AssentGate")
st.caption("Argument-level authorization with selective confirmation for tool-using agents. "
           "AgentDojo workspace suite, no LLM calls.")
tab_run, tab_results, tab_about = st.tabs(["Step through a run", "Benchmark results", "How it works"])
with tab_run:
    run_tab()
with tab_results:
    results_tab()
with tab_about:
    about_tab()
