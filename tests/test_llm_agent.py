"""ClaudeLLM, the attack-aware approver and the LLM runner, with a fake client (no API calls)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

agentdojo = pytest.importorskip("agentdojo")
pytest.importorskip("anthropic")

from agentdojo.functions_runtime import FunctionCall  # noqa: E402
from agentdojo.task_suite.load_suites import get_suite  # noqa: E402
from anthropic.types import Message  # noqa: E402

from assentgate import ClassifierOutput, Decision  # noqa: E402
from assentgate.agentdojo_adapter import AttackAwareApprover, attacker_markers, run_agent  # noqa: E402
from assentgate.llm_agent import ClaudeLLM, call_cost  # noqa: E402
from assentgate.schemas import AuthorizationRequest, GateResult, ToolCall  # noqa: E402
from assentgate.workspace import WORKSPACE_TOOLS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPECS = {t.name: t for t in WORKSPACE_TOOLS}
SUITE = get_suite("v1.2.2", "workspace")


class AllowAll:
    def predict(self, request, spec):
        return ClassifierOutput(decision=Decision.AUTHORIZED)


def message(content, stop_reason):
    return Message.model_validate({
        "id": "msg", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 100,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    })


class FakeClient:
    """Stands in for anthropic.Anthropic: records each request, returns canned replies."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def stream(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        reply = self.replies.pop(0)

        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_final_message(self):
                return reply

        return Stream()


FIRST_TURN = [
    {"type": "thinking", "thinking": "", "signature": "sig-1"},
    {"type": "redacted_thinking", "data": "opaque"},
    {"type": "text", "text": "Sending it."},
    # A string-encoded list: GatedToolsExecutor parses it in place before running the tool.
    {"type": "tool_use", "id": "tu-1", "name": "send_email",
     "input": {"recipients": "['bob@example.com']", "subject": "Hi", "body": "Hello"}},
]


def test_claude_llm_request_shape_and_verbatim_replay():
    client = FakeClient([message(FIRST_TURN, "tool_use"),
                         message([{"type": "text", "text": "Done."}], "end_turn")])
    llm = ClaudeLLM(client, "claude-haiku-5-5")
    user_task = SUITE.user_tasks["user_task_0"]
    row = run_agent(SUITE, user_task, None, AllowAll, "always", SPECS, WORKSPACE_TOOLS, llm=llm)

    assert len(client.requests) == 2
    first, second = client.requests
    for req in (first, second):
        assert "temperature" not in req and "top_p" not in req and "top_k" not in req
        assert req["thinking"] == {"type": "adaptive"}
        assert req["max_tokens"] == 16000
        assert req["cache_control"] == {"type": "ephemeral"}
        assert req["system"]  # AgentDojo's default system message
    assert first["tools"] == second["tools"]
    # The earlier prefix is unchanged and the assistant turn comes back exactly as returned:
    # same block order, thinking + redacted thinking kept, tool input not re-parsed.
    assert second["messages"][: len(first["messages"])] == first["messages"]
    assistant = second["messages"][len(first["messages"])]
    assert assistant == {"role": "assistant", "content": FIRST_TURN}
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu-1"
    assert row["log"][0]["executed"] is True

    usage = llm.usage()
    assert usage["model_calls"] == 2 and usage["input_tokens"] == 2000
    assert usage["stop_reasons"] == ["tool_use", "end_turn"]
    assert usage["cost_usd"] == pytest.approx(call_cost("claude-haiku-5-5", usage))


def test_claude_llm_effort_and_no_cache():
    client = FakeClient([message([{"type": "text", "text": "Hi."}], "end_turn")])
    llm = ClaudeLLM(client, "claude-haiku-5-5", max_tokens=8000, effort="low", cache=False)
    run_agent(SUITE, SUITE.user_tasks["user_task_0"], None, AllowAll, "always", SPECS, WORKSPACE_TOOLS, llm=llm)
    (req,) = client.requests
    assert req["output_config"] == {"effort": "low"} and req["max_tokens"] == 8000
    assert "cache_control" not in req


def test_call_cost_haiku():
    # 1M input + 1M output at $0.10 / $0.50
    assert call_cost("claude-haiku-5-5", {"input_tokens": 10**6, "output_tokens": 10**6}) == pytest.approx(0.60)
    assert call_cost("unknown-model", {"input_tokens": 1}) is None


def ask(approver, tool, **arguments):
    request = AuthorizationRequest(user_request="task", tool_call=ToolCall(tool=tool, arguments=arguments))
    return approver(request, GateResult(decision=Decision.CONFIRMATION_REQUIRED, decided_by="classifier"))


def test_attacker_markers_skip_sender_and_templates():
    assert "mark.black-2134@gmail.com" in attacker_markers(SUITE.injection_tasks["injection_task_3"])
    assert "lily.white@gmail.com" not in attacker_markers(SUITE.injection_tasks["injection_task_3"])
    assert attacker_markers(SUITE.injection_tasks["injection_task_1"]) == ["13"]
    markers_13 = attacker_markers(SUITE.injection_tasks["injection_task_13"])
    assert "https://bit.ly/sUf7tOJ" in markers_13 and not any("{" in m for m in markers_13)


def test_attack_aware_approver():
    expected = [FunctionCall(function="send_email", args={"recipients": ["alice@example.com"], "subject": "s"})]
    approver = AttackAwareApprover(expected, SPECS, SUITE.injection_tasks["injection_task_0"])
    assert ask(approver, "send_email", recipients=["alice@example.com"], subject="s", body="b")  # ground truth
    assert ask(approver, "send_email", recipients=["carol@example.com"], subject="x", body="b")  # other route
    assert not ask(approver, "send_email", recipients=["Mark.Black-2134@gmail.com"], subject="s", body="b")
    assert not ask(approver, "delete_file", file_id="4")  # the task never deletes
    benign = AttackAwareApprover(expected, SPECS, None)
    assert ask(benign, "send_email", recipients=["mark.black-2134@gmail.com"], subject="s", body="b")


def test_attack_aware_approver_short_marker_is_whole_value():
    expected = [FunctionCall(function="delete_file", args={"file_id": "4"})]
    approver = AttackAwareApprover(expected, SPECS, SUITE.injection_tasks["injection_task_1"])
    assert not ask(approver, "delete_file", file_id="13")
    assert ask(approver, "delete_file", file_id="113")


def run_script(*args):
    return subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "scripts" / "run_llm_eval.py"), *args],
                          capture_output=True, text=True, cwd=ROOT, timeout=600)


def test_runner_resumes_and_dry_run(tmp_path):
    common = ["--agent", "scripted", "--out", str(tmp_path), "--user-tasks", "user_task_0",
              "--injection-tasks", "injection_task_0", "--configs", "no_gate,delegation_intent"]
    first = run_script(*common)
    assert first.returncode == 0, first.stderr
    assert "4 runs to do" in first.stdout
    rows = [json.loads(line) for line in (tmp_path / "delegation_intent.jsonl").read_text().splitlines()]
    assert {(r["user_task"], r["injection_task"]) for r in rows} == {("user_task_0", None),
                                                                      ("user_task_0", "injection_task_0")}
    assert all("error" not in r for r in rows) and rows[0]["approver"] == "attack_aware"
    second = run_script(*common)
    assert second.returncode == 0, second.stderr
    assert "0 runs to do" in second.stdout
    dry = run_script("--agent", "claude", "--out", str(tmp_path / "dry"), "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert "2400 runs to do" in dry.stdout
