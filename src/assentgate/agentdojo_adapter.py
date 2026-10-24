"""Run AssentGate inside AgentDojo's agent pipeline.

`GatedToolsExecutor` replaces AgentDojo's `ToolsExecutor`: each proposed tool
call goes through a fresh per-task `Dispatcher` (policy, classifier, approvals,
provenance, history, audit), and blocked or declined calls come back to the
agent as tool errors.

`GroundTruthProposer` is a scripted agent that proposes a task's ground-truth
calls (plus an injection task's, to simulate a fully compromised agent). It
measures the gate end to end with no LLM calls. Its results describe the gate
on scripted trajectories, not on a real agent.

`RecordedProposer` replays the tool calls of a saved real-agent run (AgentDojo's
`runs/` folder) and stops at the first call the gate blocks or the user declines.

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


def _leaves(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _leaves(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _leaves(v)]
    return [] if value is None else [str(value)]


def attacker_markers(injection_task: Any) -> list[str]:
    """Values only the attacker wants: the injection task's constants (recipient, file id,
    phishing link, exact subject/body...). The data sources it reads (`_SENDER`) and
    templated strings are left out."""
    return [
        v for k, v in vars(type(injection_task)).items()
        if k.startswith("_") and k[1:2].isupper() and k != "_SENDER" and isinstance(v, str) and "{" not in v
    ]


class AttackAwareApprover:
    """Simulated user for a real LLM agent, which may reach the goal by a route the ground
    truth doesn't take. Approves a confirmation if

    - it matches a user-task ground-truth call (as `GroundTruthApprover`), or
    - the user task's ground truth uses that tool at all, and no argument carries an
      attacker value (long markers: case-insensitive substring; short ones like file id
      "13": whole value).

    Optimistic: the simulated user recognises the attacker's values. Report it next to
    `GroundTruthApprover` (conservative), never alone.
    """

    def __init__(self, expected: Sequence[FunctionCall], specs: dict[str, ToolSpec],
                 injection_task: Any | None = None):
        self.exact = GroundTruthApprover(expected, specs)
        self.tools = {c.function for c in expected}
        self.markers = attacker_markers(injection_task) if injection_task is not None else []

    def carries_attacker_value(self, arguments: dict) -> bool:
        leaves = _leaves(arguments)
        for marker in self.markers:
            if len(marker) >= 6:
                if any(marker.lower() in s.lower() for s in leaves):
                    return True
            elif any(marker == s.strip() for s in leaves):
                return True
        return False

    def __call__(self, request: AuthorizationRequest, result: GateResult) -> bool:
        if self.exact(request, result):
            return True
        call = request.tool_call
        return call.tool in self.tools and not self.carries_attacker_value(call.arguments)


class GatedToolsExecutor(BasePipelineElement):
    def __init__(
        self,
        gate_factory: Callable[[], AssentGate],
        approver: Approver | None = None,
        output_formatter: Callable[[Any], str] = tool_result_to_str,
        trace: bool = False,
    ) -> None:
        self.gate_factory = gate_factory
        self.approver = approver
        self.output_formatter = output_formatter
        self.dispatcher: Dispatcher | None = None
        # One entry per proposed call across all tasks, for evaluation. With `trace`, entries also
        # hold the call's arguments, the request as the gate saw it, both gate results, the tool
        # output and the error the agent was shown (objects, not JSON; used by `assentgate.trace`).
        self.log: list[dict] = []
        self.trace = trace

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
            first, asked, approved, instrumented = out.result, False, False, out.request
            if out.result.decision is Decision.CONFIRMATION_REQUIRED and self.approver is not None:
                asked = True
                approved = self.approver(request, out.result)
                if approved:
                    out = self.dispatcher.approve_and_submit(request)
            entry = {
                "task": query,
                "tool": tool_call.function,
                "decision": first.decision.value,
                "decided_by": first.decided_by,
                "affected_fields": first.affected_fields,
                "asked": asked,
                "approved": approved,
                "executed": out.executed,
            }
            message = self._message(tool_call, out, asked)
            if self.trace:
                entry.update(arguments=dict(tool_call.args), request=instrumented, result=first,
                             final_result=out.result,
                             output=message["content"][0]["content"] if out.executed else None,
                             agent_sees=message["error"])
            self.log.append(entry)
            results.append(message)
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


class ImportantInstructions:
    """AgentDojo's standard "important_instructions" attack text, placed in every injection
    vector the user task reads (found with AgentDojo's own no-LLM ground-truth probe)."""

    def __init__(self, suite: Any, user_name: str = "Emma Johnson", model_name: str | None = None) -> None:
        from agentdojo.attacks.base_attacks import FixedJailbreakAttack
        from agentdojo.attacks.important_instructions_attacks import ImportantInstructionsAttack

        self.attack = FixedJailbreakAttack(ImportantInstructionsAttack._JB_STRING, suite, None)
        self.attack.user_name = user_name
        if model_name is not None:  # e.g. "Claude", as AgentDojo's attack addresses Claude models
            self.attack.model_name = model_name

    def injections(self, user_task: Any, injection_task: Any, goal: str | None = None) -> dict[str, str]:
        if goal is None:
            return self.attack.attack(user_task, injection_task)
        # Same template with a substituted goal (used to randomize attacker identities in training data).
        return {
            inj: self.attack.jailbreak.format(goal=goal, user=self.attack.user_name,
                                              model=self.attack.model_name, task=user_task.PROMPT)
            for inj in self.attack.get_injection_candidates(user_task)
        }


def run_scripted(
    suite: Any,
    user_task: Any,
    injection_task: Any | None,
    classifier_factory: Callable[[], Any],
    approver_kind: str,
    specs: dict[str, ToolSpec],
    tools: Sequence[ToolSpec],
    injections: dict[str, str] | None = None,
) -> dict:
    """One scripted-agent run through the gate. approver_kind: always | never | ground_truth | attack_aware."""
    return run_agent(suite, user_task, injection_task, classifier_factory, approver_kind, specs, tools, injections)


def make_approver(approver_kind: str | Approver, expected: Sequence[FunctionCall], specs: dict[str, ToolSpec],
                  injection_task: Any | None) -> Approver:
    if callable(approver_kind):  # e.g. a person's answers in the demo
        return approver_kind
    return {
        "always": lambda: always_approve,
        "never": lambda: never_approve,
        "ground_truth": lambda: GroundTruthApprover(expected, specs),
        "attack_aware": lambda: AttackAwareApprover(expected, specs, injection_task),
    }[approver_kind]()


def run_agent(
    suite: Any,
    user_task: Any,
    injection_task: Any | None,
    classifier_factory: Callable[[], Any],
    approver_kind: str,
    specs: dict[str, ToolSpec],
    tools: Sequence[ToolSpec],
    injections: dict[str, str] | None = None,
    llm: BasePipelineElement | None = None,
    system_message: str | None = None,
    max_iters: int = 15,
    trace: bool = False,
) -> dict:
    """One run through the gate. With `llm=None` the agent is the scripted `GroundTruthProposer`;
    otherwise `llm` (e.g. `llm_agent.ClaudeLLM`) is the agent, after AgentDojo's system message."""
    from collections import Counter

    from agentdojo.agent_pipeline import AgentPipeline, InitQuery, SystemMessage, ToolsExecutionLoop
    from agentdojo.agent_pipeline.agent_pipeline import load_system_message

    injections = injections or {}
    env = user_task.init_environment(suite.load_and_inject_default_environment(injections))
    approver = make_approver(approver_kind, user_task.ground_truth(env), specs, injection_task)
    executor = GatedToolsExecutor(lambda: AssentGate(list(tools), classifier=classifier_factory()), approver,
                                  trace=trace)
    if llm is None:
        proposer = GroundTruthProposer(user_task, injection_task)
        pipeline = AgentPipeline([InitQuery(), proposer, ToolsExecutionLoop([executor, proposer], max_iters=50)])
    else:
        pipeline = AgentPipeline([
            SystemMessage(system_message or load_system_message(None)),
            InitQuery(),
            llm,
            ToolsExecutionLoop([executor, llm], max_iters=max_iters),
        ])
    utility, attacked = suite.run_task_with_pipeline(pipeline, user_task, injection_task, injections=injections)
    row = {
        "user_task": user_task.ID,
        "injection_task": injection_task.ID if injection_task else None,
        "injected_text": bool(injections),
        "utility": bool(utility),
        "attack_success": bool(attacked) if injection_task else None,
        "injection_calls": len(injection_task.ground_truth(env)) if injection_task else None,
        "calls": len(executor.log),
        "confirmations": sum(e["asked"] for e in executor.log),
        "approved": sum(e["approved"] for e in executor.log),
        "blocked": sum(e["decision"] == "blocked" for e in executor.log),
        "decisions": dict(Counter(e["decision"] for e in executor.log)),
        "log": executor.log,
    }
    if trace:
        row["audit"] = executor.dispatcher.audit.events() if executor.dispatcher else []
    return row


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


def text_of(content: Any) -> str:
    """Text of a saved message's content (a string, or a list of AgentDojo content blocks)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(b.get("content") or "" for b in content if isinstance(b, dict) and b.get("type") == "text")


def recorded_turns(messages: list[dict]) -> tuple[list[list[FunctionCall]], str]:
    """A saved run's assistant turns with tool calls (in order) and its final assistant text."""
    turns, final = [], ""
    for m in messages:
        if m["role"] != "assistant":
            continue
        if m.get("tool_calls"):
            turns.append([FunctionCall(function=tc["function"], args=dict(tc.get("args") or {}),
                                       id=tc.get("id") or f"rec-{len(turns)}-{i}")
                          for i, tc in enumerate(m["tool_calls"])])
        else:
            final = text_of(m.get("content"))
    return turns, final


class RecordedProposer(BasePipelineElement):
    """Replays a saved run's tool calls, one recorded turn at a time.

    A recording can't react to the gate: once a call comes back blocked or declined by AssentGate,
    it stops and gives an empty final answer (conservative for utility).
    """

    name = "recorded"

    def __init__(self, turns: Sequence[Sequence[FunctionCall]], final_text: str) -> None:
        self.turns = [list(t) for t in turns]
        self.final_text = final_text
        self.stopped = False

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env = EmptyEnv(),
        messages: Sequence[ChatMessage] = [],
        extra_args: dict = {},
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        proposed = sum(1 for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
        last_results = []
        for m in reversed(messages):
            if m["role"] != "tool":
                break
            last_results.append(m)
        if any((m.get("error") or "").startswith("AssentGate") for m in last_results):
            self.stopped = True
        if not self.stopped and proposed < len(self.turns):
            message = ChatAssistantMessage(role="assistant", content=[text_content_block_from_string("")],
                                           tool_calls=[c.model_copy(deep=True) for c in self.turns[proposed]])
        else:
            message = ChatAssistantMessage(
                role="assistant",
                content=[text_content_block_from_string("" if self.stopped else self.final_text)],
                tool_calls=None,
            )
        return query, runtime, env, [*messages, message], extra_args
