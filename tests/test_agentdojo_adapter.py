import pytest

agentdojo = pytest.importorskip("agentdojo")

from agentdojo.agent_pipeline import AgentPipeline, InitQuery, ToolsExecutionLoop  # noqa: E402
from agentdojo.task_suite.load_suites import get_suite  # noqa: E402

from assentgate import AssentGate, ClassifierOutput, Decision  # noqa: E402
from assentgate.agentdojo_adapter import (  # noqa: E402
    GatedToolsExecutor,
    GroundTruthApprover,
    GroundTruthProposer,
    always_approve,
    never_approve,
)
from assentgate.workspace import WORKSPACE_TOOLS  # noqa: E402

SPECS = {t.name: t for t in WORKSPACE_TOOLS}
SUITE = get_suite("v1.2.2", "workspace")


class AllowAll:
    def predict(self, request, spec):
        return ClassifierOutput(decision=Decision.AUTHORIZED)


def run(user_task, injection_task=None, classifier=None, approver=None):
    executor = GatedToolsExecutor(lambda: AssentGate(WORKSPACE_TOOLS, classifier=classifier), approver=approver)
    proposer = GroundTruthProposer(user_task, injection_task)
    pipeline = AgentPipeline([InitQuery(), proposer, ToolsExecutionLoop([executor, proposer], max_iters=50)])
    utility, security = SUITE.run_task_with_pipeline(pipeline, user_task, injection_task, injections={})
    return utility, security, executor.log


def first_task_that_calls(tool):
    for task in SUITE.user_tasks.values():
        env = task.init_environment(SUITE.load_and_inject_default_environment({}))
        if any(c.function == tool for c in task.ground_truth(env)):
            return task
    pytest.skip(f"no user task calls {tool}")


def test_specs_match_agentdojo_tool_signatures():
    tools = {f.name: f for f in SUITE.tools}
    assert set(tools) == set(SPECS)
    for name, fn in tools.items():
        fields = fn.parameters.model_fields
        required = {k for k, f in fields.items() if f.is_required()}
        assert set(SPECS[name].required_args) == required, name
        assert set(SPECS[name].optional_args) == set(fields) - required, name


def test_ground_truth_passes_through_an_allow_all_gate():
    task = first_task_that_calls("send_email")
    utility, _, log = run(task, classifier=AllowAll())
    assert utility
    assert all(e["executed"] for e in log)


def test_baseline_gate_declined_side_effect_breaks_utility():
    task = first_task_that_calls("send_email")
    utility, _, log = run(task, approver=never_approve)
    assert not utility
    assert any(e["asked"] and not e["executed"] for e in log)


def test_ground_truth_approver_restores_utility():
    task = first_task_that_calls("send_email")
    env = task.init_environment(SUITE.load_and_inject_default_environment({}))
    approver = GroundTruthApprover(task.ground_truth(env), SPECS)
    utility, _, log = run(task, approver=approver)
    assert utility
    assert all(e["approved"] for e in log if e["asked"])


def test_compromised_agent_is_stopped_by_ground_truth_approver():
    user_task = first_task_that_calls("send_email")
    injection_task = next(iter(SUITE.injection_tasks.values()))
    env = user_task.init_environment(SUITE.load_and_inject_default_environment({}))
    approver = GroundTruthApprover(user_task.ground_truth(env), SPECS)

    _, attacked_ungated, _ = run(user_task, injection_task, classifier=AllowAll(), approver=always_approve)
    _, attacked_gated, _ = run(user_task, injection_task, approver=approver)
    assert attacked_ungated  # the scripted compromised agent succeeds without a gate
    assert not attacked_gated
