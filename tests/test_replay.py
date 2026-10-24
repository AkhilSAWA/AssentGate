"""Replay of saved real-agent runs: the recorded proposer stops at the first gated call."""
import pytest

pytest.importorskip("agentdojo")

from agentdojo.task_suite.load_suites import get_suite  # noqa: E402

from assentgate import ClassifierOutput, Decision  # noqa: E402
from assentgate.agentdojo_adapter import RecordedProposer, recorded_turns, run_agent  # noqa: E402
from assentgate.workspace import WORKSPACE_TOOLS  # noqa: E402

SPECS = {t.name: t for t in WORKSPACE_TOOLS}
SUITE = get_suite("v1.2.2", "workspace")

MESSAGES = [
    {"role": "user", "content": "task"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": "get_current_day", "args": {}, "id": "a"}]},
    {"role": "tool", "content": "2024-05-15", "tool_call_id": "a"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"function": "delete_file", "args": {"file_id": "13"}, "id": "b"}]},
    {"role": "tool", "content": "ok", "tool_call_id": "b"},
    {"role": "assistant", "content": "All done."},
]


class BlockDeletes:
    def predict(self, request, spec):
        if request.tool_call.tool == "delete_file":
            return ClassifierOutput(decision=Decision.BLOCKED)
        return ClassifierOutput(decision=Decision.AUTHORIZED)


class AllowAll:
    def predict(self, request, spec):
        return ClassifierOutput(decision=Decision.AUTHORIZED)


def test_recorded_turns():
    turns, final = recorded_turns(MESSAGES)
    assert [[c.function for c in t] for t in turns] == [["get_current_day"], ["delete_file"]]
    assert final == "All done."


@pytest.mark.parametrize("classifier,stopped,calls", [(AllowAll, False, 2), (BlockDeletes, True, 2)])
def test_replay_stops_after_gate(classifier, stopped, calls):
    turns, final = recorded_turns(MESSAGES)
    proposer = RecordedProposer(turns, final)
    row = run_agent(SUITE, SUITE.user_tasks["user_task_0"], None, classifier, "always", SPECS, WORKSPACE_TOOLS,
                    llm=proposer, max_iters=len(turns) + 2)
    assert proposer.stopped is stopped
    assert row["calls"] == calls
    executed = [e["executed"] for e in row["log"]]
    assert executed == ([True, True] if not stopped else [True, False])
