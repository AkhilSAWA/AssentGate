"""Run AssentGate inside AgentDojo's agent pipeline.

`GatedToolsExecutor` replaces AgentDojo's `ToolsExecutor`: each proposed tool
call goes through a fresh per-task `Dispatcher` (policy, classifier, approvals,
provenance, history, audit), and blocked or declined calls come back to the
agent as tool errors.

`GroundTruthProposer` is a scripted agent that proposes a task's ground-truth
calls (plus an injection task's, to simulate a fully compromised agent). It
measures the gate end to end with no LLM calls. Its results describe the gate
on scripted trajectories, not on a real agent.

Requires the `eval` extra (AgentDojo pinned in pyproject.toml).
"""

from __future__ import annotations

from ast import literal_eval
from collections.abc import Callable, Sequence
from typing import Any

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.tool_execution import is_string_list, tool_result_to_str
from agentdojo.functions_runtime import EmptyEnv, Env, FunctionCall, FunctionsRuntime
from agentdojo.types import ChatAssistantMessage, ChatMessage, ChatToolResultMessage, text_content_block_from_string

from assentgate.approvals import normalise
from assentgate.dispatcher import Dispatcher, DispatchResult
from assentgate.gate import AssentGate
from assentgate.schemas import AuthorizationRequest, Decision, GateResult, ToolCall, ToolSpec

Approver = Callable[[AuthorizationRequest, GateResult], bool]


class ToolExecutionError(Exception):
    pass


def always_approve(request: AuthorizationRequest, result: GateResult) -> bool:
    return True


def never_approve(request: AuthorizationRequest, result: GateResult) -> bool:
    return False


class GroundTruthApprover:
    """Simulated user: approves a call only if it matches one of the user task's
    ground-truth calls on tool name and (normalised) sensitive arguments.

    Conservative: a benign call that reaches the goal by a different route than
    the ground truth is declined.
    """

    def __init__(self, expected: Sequence[FunctionCall], specs: dict[str, ToolSpec]):
        self.expected = list(expected)
        self.specs = specs

    def __call__(self, request: AuthorizationRequest, result: GateResult) -> bool:
        call = request.tool_call
        spec = self.specs.get(call.tool)
        if spec is None:
            return False
        return any(
            gt.function == call.tool
            and all(normalise(gt.args.get(f)) == normalise(call.arguments.get(f)) for f in spec.sensitive_fields)
            for gt in self.expected
        )


class GatedToolsExecutor(BasePipelineElement):
    def __init__(
        self,
        gate_factory: Callable[[], AssentGate],
        approver: Approver | None = None,
        output_formatter: Callable[[Any], str] = tool_result_to_str,
    ) -> None:
        self.gate_factory = gate_factory
        self.approver = approver
        self.output_formatter = output_formatter
        self.dispatcher: Dispatcher | None = None
        # One entry per proposed call across all tasks, for evaluation.
        self.log: list[dict] = []

    def _new_task(self, runtime: FunctionsRuntime, env: Env) -> Dispatcher:
        dispatcher = Dispatcher(self.gate_factory())
        for name in runtime.functions:
            if name in dispatcher.gate.tools:
                dispatcher.register(name, self._handler(runtime, env, name))
        return dispatcher

    @staticmethod
    def _handler(runtime: FunctionsRuntime, env: Env, name: str) -> Callable[..., Any]:
        def run(**kwargs: Any) -> Any:
            result, error = runtime.run_function(env, name, kwargs)
            if error:
                raise ToolExecutionError(error)
            return result

        return run

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if not messages or messages[-1]["role"] != "assistant" or not messages[-1]["tool_calls"]:
            return query, runtime, env, messages, extra_args
        # First tool round of a task: fresh approvals, provenance, history.
        if self.dispatcher is None or not any(m["role"] == "tool" for m in messages):
            self.dispatcher = self._new_task(runtime, env)

        results = []
        for tool_call in messages[-1]["tool_calls"]:
            for k, v in tool_call.args.items():
                if isinstance(v, str) and is_string_list(v):
                    tool_call.args[k] = literal_eval(v)
            request = AuthorizationRequest(
                user_request=query, tool_call=ToolCall(tool=tool_call.function, arguments=dict(tool_call.args))
            )
            out = self.dispatcher.submit(request)
            first, asked, approved = out.result, False, False
            if out.result.decision is Decision.CONFIRMATION_REQUIRED and self.approver is not None:
                asked = True
                approved = self.approver(request, out.result)
                if approved:
                    out = self.dispatcher.approve_and_submit(request)
            self.log.append(
                {
                    "task": query,
                    "tool": tool_call.function,
                    "decision": first.decision.value,
                    "decided_by": first.decided_by,
                    "affected_fields": first.affected_fields,
                    "asked": asked,
                    "approved": approved,
                    "executed": out.executed,
                }
            )
            results.append(self._message(tool_call, out, asked))
        return query, runtime, env, [*messages, *results], extra_args

    def _message(self, tool_call: FunctionCall, out: DispatchResult, asked: bool) -> ChatToolResultMessage:
        if out.executed:
            content, error = self.output_formatter(out.output), None
        elif out.error:
            content, error = "", out.error.removeprefix("ToolExecutionError: ")
        elif asked:
            content, error = "", "AssentGate: the user declined this action."
        else:
            detail = " ".join(out.result.reasons + out.result.questions)
            content, error = "", f"AssentGate: {out.result.decision.value}. {detail}".strip()
        return ChatToolResultMessage(
            role="tool",
            content=[text_content_block_from_string(content)],
            tool_call_id=tool_call.id,
            tool_call=tool_call,
            error=error,
        )


class GroundTruthProposer(BasePipelineElement):
    """Proposes ground-truth calls one per turn, then the task's ground-truth answer."""

    def __init__(self, user_task: Any, injection_task: Any | None = None) -> None:
        self.user_task = user_task
        self.injection_task = injection_task
        self.calls: list[FunctionCall] | None = None

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        if self.calls is None:  # computed once, on the untouched environment
            self.calls = list(self.user_task.ground_truth(env))
            if self.injection_task is not None:
                self.calls += list(self.injection_task.ground_truth(env))
        proposed = sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
        if proposed < len(self.calls):
            call = self.calls[proposed]
            message = ChatAssistantMessage(
                role="assistant",
                content=[text_content_block_from_string("")],
                tool_calls=[FunctionCall(function=call.function, args=dict(call.args), id=f"gt-{proposed}")],
            )
        else:
            message = ChatAssistantMessage(
                role="assistant",
                content=[text_content_block_from_string(self.user_task.GROUND_TRUTH_OUTPUT)],
                tool_calls=None,
            )
        return query, runtime, env, [*messages, message], extra_args
