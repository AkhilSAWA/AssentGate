"""Authorization classifiers behind the `Classifier` protocol.

Baselines (SensitivityBaseline = confirm every side effect, ToolLevelRules, Strict/Loose
provenance), AssentGate's rule gates (DelegationRules, DelegationIntentRules) and the
EscalateOnly combiner. The learned scorer lives in `assentgate.scorer`. The gate uses
SensitivityBaseline when no classifier is given.
"""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, Field

from assentgate.provenance import _parent
from assentgate.schemas import ArgumentEvidence, AuthorizationRequest, Decision, EvidenceSource, ToolSpec


class ClassifierOutput(BaseModel):
    decision: Decision
    affected_fields: list[str] = Field(default_factory=list)
    confidence: float | None = None


class Classifier(Protocol):
    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput: ...


# user > exact structured field > text inside a record the user explicitly delegated to
# > text inside any other record > seen nowhere
SOURCE_RANK = {"user": 0, "exact": 1, "named_content": 2, "contained": 3, "unknown": 4}


def in_delegated_source(e: ArgumentEvidence, request: AuthorizationRequest) -> bool:
    """True if a tool-output value sits inside a record the request explicitly delegated to."""
    for ds in request.delegated_sources:
        if e.detail == ds.source and e.path is not None and (
                _parent(e.path) == ds.record or e.path.startswith(ds.record + ".") or not ds.record):
            return True
    return False


def evidence_kind(e: ArgumentEvidence, request: AuthorizationRequest) -> str:
    if e.source is EvidenceSource.USER:
        return "user"
    if e.source is EvidenceSource.TOOL_OUTPUT:
        kind = e.match or "contained"
        if kind == "contained" and in_delegated_source(e, request):
            return "named_content"
        return kind
    return "unknown"


def value_sources(request: AuthorizationRequest, spec: ToolSpec) -> dict[tuple[str, str], str]:
    """Best verified source per (sensitive field, value), ranked by SOURCE_RANK.

    Only dispatcher-verified evidence counts; caller-supplied evidence is ignored.
    """
    best: dict[tuple[str, str], str] = {}
    for e in request.evidence:
        if not e.verified or e.value is None or e.field not in spec.sensitive_fields:
            continue
        kind = evidence_kind(e, request)
        key = (e.field, e.value)
        if key not in best or SOURCE_RANK[kind] < SOURCE_RANK[best[key]]:
            best[key] = kind
    return best


class _ProvenanceGate:
    """Shared shape: reads are safely derived; a side effect is confirmed on the fields whose
    values have a source in `self.confirm_kinds`, otherwise authorized."""

    confirm_kinds: frozenset[str] = frozenset()

    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput:
        if not spec.side_effecting:
            return ClassifierOutput(decision=Decision.SAFELY_DERIVED)
        sources = value_sources(request, spec)
        fields = sorted({f for (f, _), kind in sources.items() if kind in self.confirm_kinds})
        if fields:
            return ClassifierOutput(decision=Decision.CONFIRMATION_REQUIRED, affected_fields=fields)
        return ClassifierOutput(decision=Decision.AUTHORIZED)


class StrictProvenance(_ProvenanceGate):
    """Baseline in the style of agent-action-guard's rules: ask whenever a sensitive value
    wasn't typed by the user, even if the user delegated it to data."""

    confirm_kinds = frozenset({"exact", "named_content", "contained", "unknown"})


class LooseProvenance(_ProvenanceGate):
    """Baseline in the style of AuthGraph's string match: accept a value seen anywhere in an
    earlier tool output, even inside free text where planted instructions live."""

    confirm_kinds = frozenset({"unknown"})


class DelegationRules(_ProvenanceGate):
    """AssentGate, rule version: accept user-typed values, values that are a whole structured
    field of an earlier output (an approximation of delegation tests A-D), and values inside a
    record the request explicitly delegates to (guidelines v0.3); ask when a value only appears
    inside other free text or nowhere."""

    confirm_kinds = frozenset({"contained", "unknown"})


# Verbs that would ask for each side effect (guidelines rule 1: was this *action* requested?).
# Written from tool semantics; lexical, so it misses paraphrases. Under explicit delegation
# (guidelines v0.3) the delegated record's text counts as part of the request.
ACTION_VERBS = {
    "send_email": r"send|e-?mail|mail|reply|respond|forward|tell|inform|let\b.*\bknow|notify|message|write|contact|ask",
    "delete_email": r"delete|remove|trash|clean",
    "create_calendar_event": r"create|add|schedule|book|set up|organi[sz]e|plan|event|meeting|appointment|calendar|remind",
    "cancel_calendar_event": r"cancel|delete|remove|call off",
    "reschedule_calendar_event": r"reschedule|move|postpone|change|shift|push|delay",
    "add_calendar_event_participants": r"add|invite|include",
    "create_file": r"create|make|write|save|draft|new (?:file|document)|list",
    "append_to_file": r"append|add|update|write|record",
    "delete_file": r"delete|remove|clean|free up|trash",
    "share_file": r"share|give .*access|grant|send|permission",
}


def action_requested(tool: str, user_request: str) -> bool:
    pattern = ACTION_VERBS.get(tool)
    return pattern is None or re.search(rf"\b(?:{pattern})", user_request.lower()) is not None


class DelegationIntentRules(DelegationRules):
    """Delegation rules plus a lexical action-intent check: also ask when neither the request nor a
    record it explicitly delegates to contains any verb that would ask for this tool's side effect."""

    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput:
        out = super().predict(request, spec)
        asked_by = " ".join([request.user_request, *(ds.text for ds in request.delegated_sources)])
        if out.decision is Decision.AUTHORIZED and not action_requested(spec.name, asked_by):
            return ClassifierOutput(decision=Decision.CONFIRMATION_REQUIRED, affected_fields=[])
        return out


SEVERITY = {Decision.SAFELY_DERIVED: 0, Decision.AUTHORIZED: 0, Decision.CONFIRMATION_REQUIRED: 1,
            Decision.BLOCKED: 2}


class EscalateOnly:
    """Combine a base gate with a second opinion that may only make the decision stricter."""

    def __init__(self, base: Classifier, second: Classifier):
        self.base, self.second = base, second

    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput:
        a, b = self.base.predict(request, spec), self.second.predict(request, spec)
        if SEVERITY[b.decision] <= SEVERITY[a.decision]:
            return a
        fields = sorted(set(a.affected_fields) | set(b.affected_fields))
        return ClassifierOutput(decision=b.decision, affected_fields=fields, confidence=b.confidence)


class ToolLevelRules:
    """Baseline 2: a fixed decision per tool, ignoring arguments.

    Reads are allowed, and tools listed in `confirm_tools` always need
    confirmation. Every other write is allowed. The default list mirrors
    HIGH_IMPACT in scripts/build_agentdojo_dataset.py (data leaves the account
    or is destroyed).
    """

    DEFAULT_CONFIRM_TOOLS = frozenset({"send_email", "share_file", "delete_file", "delete_email"})

    def __init__(self, confirm_tools: frozenset[str] | set[str] = DEFAULT_CONFIRM_TOOLS):
        self.confirm_tools = frozenset(confirm_tools)

    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput:
        if not spec.side_effecting:
            return ClassifierOutput(decision=Decision.SAFELY_DERIVED)
        if spec.name in self.confirm_tools:
            fields = [f for f in spec.sensitive_fields if f in request.tool_call.arguments]
            return ClassifierOutput(decision=Decision.CONFIRMATION_REQUIRED, affected_fields=fields)
        return ClassifierOutput(decision=Decision.AUTHORIZED)


class SensitivityBaseline:
    """Asks for confirmation on every side-effecting call; allows reads."""

    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput:
        if not spec.side_effecting:
            return ClassifierOutput(decision=Decision.SAFELY_DERIVED)
        fields = [f for f in spec.sensitive_fields if f in request.tool_call.arguments]
        return ClassifierOutput(decision=Decision.CONFIRMATION_REQUIRED, affected_fields=fields)
