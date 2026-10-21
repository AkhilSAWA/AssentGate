import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assentgate import (
    ApprovalStore,
    ArgumentEvidence,
    AssentGate,
    AuthorizationRequest,
    ClassifierOutput,
    Decision,
    Dispatcher,
    EvidenceSource,
    HardPolicy,
    HistoryEntry,
    PolicyConfig,
    ToolCall,
)
from assentgate.workspace import WORKSPACE_TOOLS, FakeWorkspace

USER_REQUEST = "Read the airline email and add my flight to my calendar."
SPECS = {t.name: t for t in WORKSPACE_TOOLS}


class AllowAll:
    def __init__(self):
        self.seen = []

    def predict(self, request, spec):
        self.seen.append(request)
        return ClassifierOutput(decision=Decision.AUTHORIZED, confidence=0.99)


@pytest.fixture
def workspace():
    return FakeWorkspace()


def make_dispatcher(workspace, classifier=None, **policy_kwargs):
    config = PolicyConfig(internal_domains={"me.example"}, **policy_kwargs)
    gate = AssentGate(WORKSPACE_TOOLS, policy=HardPolicy(config), classifier=classifier)
    dispatcher = Dispatcher(gate)
    for name, handler in workspace.handlers().items():
        dispatcher.register(name, handler)
    return dispatcher


def request(tool, **arguments):
    return AuthorizationRequest(user_request=USER_REQUEST, tool_call=ToolCall(tool=tool, arguments=arguments))


def forward_inbox(*recipients):
    return request("send_email", recipients=list(recipients), subject="Fwd: inbox", body="...")


def read_flight_email():
    return request("search_emails", query="flight")


# --- Decisions and hard policy ------------------------------------------------


def test_read_is_safely_derived_and_executes(workspace):
    out = make_dispatcher(workspace).submit(read_flight_email())
    assert out.result.decision is Decision.SAFELY_DERIVED
    assert out.executed and out.output[0]["id_"] == "1"


def test_injected_forward_to_blocked_domain_is_blocked(workspace):
    dispatcher = make_dispatcher(workspace, blocked_domains={"attacker.example"})
    out = dispatcher.submit(forward_inbox("x@attacker.example"))
    assert out.result.decision is Decision.BLOCKED
    assert out.result.decided_by == "policy"
    assert out.result.affected_fields == ["recipients"]
    assert not out.executed and workspace.sent == []


def test_external_recipient_requires_confirmation_with_targeted_question(workspace):
    out = make_dispatcher(workspace).submit(forward_inbox("someone@other.example"))
    assert out.result.decision is Decision.CONFIRMATION_REQUIRED
    assert out.result.affected_fields == ["recipients"]
    assert out.result.questions == ["May 'send_email' send to recipient 'someone@other.example'?"]
    assert out.result.decided_by == "classifier"
    assert not out.executed


def test_policy_does_not_force_external_recipient_confirmation_by_default(workspace):
    out = make_dispatcher(workspace, classifier=AllowAll()).submit(forward_inbox("someone@other.example"))
    assert out.result.decision is Decision.AUTHORIZED
    assert out.executed


def test_opt_in_external_recipient_floor_beats_the_classifier(workspace):
    dispatcher = make_dispatcher(workspace, classifier=AllowAll(), confirm_external_recipients=True)
    external = dispatcher.submit(forward_inbox("someone@other.example"))
    assert external.result.decision is Decision.CONFIRMATION_REQUIRED
    assert external.result.decided_by == "policy"
    internal = dispatcher.submit(forward_inbox("colleague@me.example"))
    assert internal.result.decision is Decision.AUTHORIZED


def test_blocked_domain_applies_to_share_file_and_participants(workspace):
    dispatcher = make_dispatcher(workspace, classifier=AllowAll(), blocked_domains={"attacker.example"})
    share = dispatcher.submit(request("share_file", file_id="13", email="x@attacker.example", permission="r"))
    event = dispatcher.submit(
        request("create_calendar_event", title="Flight", start_time="2026-11-02 09:00",
                end_time="2026-11-02 11:00", participants=["x@attacker.example"])
    )
    assert share.result.decision is Decision.BLOCKED
    assert event.result.decision is Decision.BLOCKED
    assert workspace.shares == [] and workspace.events == []


def test_classifier_cannot_lift_a_policy_block(workspace):
    dispatcher = make_dispatcher(workspace, classifier=AllowAll(), blocked_domains={"attacker.example"})
    assert dispatcher.submit(forward_inbox("x@attacker.example")).result.decision is Decision.BLOCKED


def test_unknown_tool_and_missing_args_are_blocked(workspace):
    dispatcher = make_dispatcher(workspace)
    assert dispatcher.submit(request("delete_inbox")).result.decision is Decision.BLOCKED
    missing = dispatcher.submit(request("send_email", recipients=["a@me.example"]))
    assert missing.result.decision is Decision.BLOCKED
    assert missing.result.affected_fields == ["body", "subject"]


def test_undeclared_arguments_are_blocked_and_never_reach_the_handler(workspace):
    calls = []
    dispatcher = make_dispatcher(workspace, classifier=AllowAll())
    dispatcher.handlers["search_emails"] = lambda **kw: calls.append(kw)
    out = dispatcher.submit(request("search_emails", query="flight", forward_to="x@other.example"))
    assert out.result.decision is Decision.BLOCKED
    assert out.result.affected_fields == ["forward_to"]
    assert calls == []


def test_declared_optional_arguments_are_accepted(workspace):
    out = make_dispatcher(workspace, classifier=AllowAll()).submit(
        request("send_email", recipients=["a@me.example"], subject="s", body="b", cc=["c@me.example"])
    )
    assert out.executed


def test_opt_in_public_visibility_floor(workspace):
    from assentgate.schemas import ToolSpec

    publish = ToolSpec(name="publish_page", description="Publish a page.", required_args=["page_id"],
                       optional_args=["visibility"], side_effecting=True)
    call = AuthorizationRequest(user_request="", tool_call=ToolCall(tool="publish_page",
                                arguments={"page_id": "p1", "visibility": "public"}))
    assert AssentGate([publish], classifier=AllowAll()).evaluate(call).decision is Decision.AUTHORIZED
    floor = HardPolicy(PolicyConfig(confirm_public_visibility=True))
    out = AssentGate([publish], policy=floor, classifier=AllowAll()).evaluate(call)
    assert out.decision is Decision.CONFIRMATION_REQUIRED
    assert out.affected_fields == ["visibility"]


def test_tool_level_rules_ignore_arguments(workspace):
    from assentgate import ToolLevelRules

    dispatcher = make_dispatcher(workspace, classifier=ToolLevelRules())
    internal = dispatcher.submit(forward_inbox("colleague@me.example"))
    assert internal.result.decision is Decision.CONFIRMATION_REQUIRED  # same tool, same answer
    event = dispatcher.submit(
        request("create_calendar_event", title="Flight", start_time="2026-11-02 09:00",
                end_time="2026-11-02 11:00", participants=["anyone@other.example"])
    )
    assert event.result.decision is Decision.AUTHORIZED and event.executed
    assert dispatcher.submit(read_flight_email()).result.decision is Decision.SAFELY_DERIVED


# --- Scoped approvals ---------------------------------------------------------


def test_approval_is_bound_to_argument_values(workspace):
    dispatcher = make_dispatcher(workspace)
    approved = dispatcher.approve_and_submit(forward_inbox("friend@other.example"))
    assert approved.result.decision is Decision.AUTHORIZED
    assert approved.result.decided_by == "approval"
    assert approved.executed

    changed = dispatcher.submit(forward_inbox("stranger@other.example"))
    assert changed.result.decision is Decision.CONFIRMATION_REQUIRED
    assert not changed.executed


def test_single_use_approval_is_consumed(workspace):
    dispatcher = make_dispatcher(workspace)
    dispatcher.approve_and_submit(forward_inbox("friend@other.example"))
    again = dispatcher.submit(forward_inbox("friend@other.example"))
    assert again.result.decision is Decision.CONFIRMATION_REQUIRED


def test_approval_matching_normalises_case_type_and_order():
    spec = SPECS["send_email"]
    store = ApprovalStore()
    store.grant(ToolCall(tool="send_email", arguments={"recipients": "Bob@Other.example",
                                                       "cc": ["b@x.example", "a@x.example"]}), spec)
    same = ToolCall(tool="send_email", arguments={"recipients": ["bob@other.example"],
                                                  "cc": ["A@x.example", "b@x.example"]})
    assert store.find(same, spec) is not None
    extra = ToolCall(tool="send_email", arguments={"recipients": ["bob@other.example", "eve@other.example"],
                                                   "cc": ["a@x.example", "b@x.example"]})
    assert store.find(extra, spec) is None


def test_file_ids_stay_case_sensitive():
    spec = SPECS["share_file"]
    store = ApprovalStore()
    store.grant(ToolCall(tool="share_file", arguments={"file_id": "Doc-A", "email": "a@me.example",
                                                       "permission": "r"}), spec)
    other = ToolCall(tool="share_file", arguments={"file_id": "doc-a", "email": "a@me.example", "permission": "r"})
    assert store.find(other, spec) is None


def test_approvals_expire():
    spec = SPECS["send_email"]
    now = [datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)]
    store = ApprovalStore(default_ttl=timedelta(minutes=30), clock=lambda: now[0])
    call = ToolCall(tool="send_email", arguments={"recipients": ["bob@other.example"]})
    store.grant(call, spec)
    now[0] += timedelta(minutes=29)
    assert store.find(call, spec) is not None
    now[0] += timedelta(minutes=1)
    assert store.find(call, spec) is None
    assert len(store) == 0


def test_approval_cannot_lift_a_policy_block(workspace):
    dispatcher = make_dispatcher(workspace, blocked_domains={"attacker.example"})
    out = dispatcher.approve_and_submit(forward_inbox("x@attacker.example"))
    assert out.result.decision is Decision.BLOCKED
    assert not out.executed


# --- Provenance and history ---------------------------------------------------


def test_arguments_reusing_tool_output_get_verified_provenance(workspace):
    classifier = AllowAll()
    dispatcher = make_dispatcher(workspace, classifier=classifier)
    dispatcher.submit(read_flight_email())
    dispatcher.submit(request("send_email", recipients=["noreply@airline.example"], subject="flight", body="thanks"))

    evidence = {(e.field, e.source): e for e in classifier.seen[-1].evidence}
    recipients = evidence[("recipients", EvidenceSource.TOOL_OUTPUT)]
    assert recipients.verified and recipients.detail == "search_emails#0"
    assert ("body", EvidenceSource.TOOL_OUTPUT) not in evidence


def test_arguments_from_the_user_request_are_tagged_user(workspace):
    classifier = AllowAll()
    dispatcher = make_dispatcher(workspace, classifier=classifier)
    dispatcher.submit(
        AuthorizationRequest(
            user_request="Email bob@me.example that I'm late.",
            tool_call=ToolCall(tool="send_email",
                               arguments={"recipients": ["bob@me.example"], "subject": "s", "body": "b"}),
        )
    )
    sources = {(e.field, e.source) for e in classifier.seen[-1].evidence}
    assert ("recipients", EvidenceSource.USER) in sources


def test_caller_supplied_evidence_is_never_verified(workspace):
    classifier = AllowAll()
    req = forward_inbox("x@other.example").model_copy(
        update={"evidence": [ArgumentEvidence(field="recipients", source=EvidenceSource.USER, verified=True)]}
    )
    make_dispatcher(workspace, classifier=classifier).submit(req)
    assert [e.verified for e in classifier.seen[-1].evidence] == [False]


def test_dispatcher_feeds_bounded_history_to_the_gate(workspace):
    classifier = AllowAll()
    dispatcher = make_dispatcher(workspace, classifier=classifier)
    dispatcher.submit(request("delete_inbox"))  # blocked, still part of history
    for _ in range(6):
        dispatcher.submit(read_flight_email())

    assert classifier.seen[0].history[0].tool_call.tool == "delete_inbox"
    last = classifier.seen[-1].history
    assert len(last) == 5
    assert all(h.tool_call.tool == "search_emails" and h.executed for h in last)


def test_caller_supplied_history_is_replaced(workspace):
    classifier = AllowAll()
    fake = HistoryEntry(tool_call=ToolCall(tool="send_email"), decision=Decision.AUTHORIZED, executed=True)
    req = read_flight_email().model_copy(update={"history": [fake]})
    make_dispatcher(workspace, classifier=classifier).submit(req)
    assert classifier.seen[-1].history == []


def test_every_decision_is_audited(workspace):
    dispatcher = make_dispatcher(workspace)
    dispatcher.submit(read_flight_email())
    dispatcher.submit(forward_inbox("someone@other.example"))
    events = dispatcher.audit.events()
    assert [e["decision"] for e in events] == ["safely_derived", "confirmation_required"]
    assert [e["executed"] for e in events] == [1, 0]
    assert len(dispatcher.history) == 2


# --- Consistency with the dataset builder -------------------------------------


def test_sensitive_fields_match_dataset_builder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_agentdojo_dataset.py"
    spec = importlib.util.spec_from_file_location("build_agentdojo_dataset", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    for name, fields in builder.SENSITIVE_FIELDS.items():
        assert SPECS[name].sensitive_fields == fields, name
        assert SPECS[name].side_effecting, name
    for name, tool in SPECS.items():
        assert tool.side_effecting != builder.is_read_only(name), name
