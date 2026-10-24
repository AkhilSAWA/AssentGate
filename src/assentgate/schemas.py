"""Core data types shared by the gate, policy layer, classifier and dispatcher."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Decision(str, Enum):
    AUTHORIZED = "authorized"
    SAFELY_DERIVED = "safely_derived"
    CONFIRMATION_REQUIRED = "confirmation_required"
    BLOCKED = "blocked"

    @property
    def allows_execution(self) -> bool:
        return self in (Decision.AUTHORIZED, Decision.SAFELY_DERIVED)


class EvidenceSource(str, Enum):
    """Where an argument value came from.

    External sources (tool outputs, documents) are not malicious by default;
    they can legitimately supply values the task needs.
    """

    USER = "user"
    POLICY = "policy"
    TOOL_OUTPUT = "tool_output"
    UNKNOWN = "unknown"


class ToolSpec(BaseModel):
    name: str
    description: str
    required_args: list[str] = Field(default_factory=list)
    optional_args: list[str] = Field(default_factory=list)
    # Consequential arguments: recipients, document ids, destinations, visibility.
    sensitive_fields: list[str] = Field(default_factory=list)
    side_effecting: bool = False


class ToolCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ArgumentEvidence(BaseModel):
    field: str
    source: EvidenceSource
    detail: str | None = None
    # True only when provenance comes from instrumentation, never from a guess.
    verified: bool = False
    # The single argument value this evidence is about (a list argument gets one entry per value).
    value: str | None = None
    # For TOOL_OUTPUT: field path inside the tool result, and whether the value was the whole
    # field ("exact") or only found inside a longer text ("contained"), with surrounding text.
    path: str | None = None
    match: Literal["exact", "contained"] | None = None
    snippet: str | None = None


class HistoryEntry(BaseModel):
    tool_call: ToolCall
    decision: Decision
    executed: bool


class DelegatedSource(BaseModel):
    """A record of an earlier tool output that the user's request explicitly hands authority to
    ("do the actions specified in the email from X", "each person mentioned in file Y")."""

    source: str  # tool output label, e.g. "search_emails#0"
    record: str  # path of the record inside that output, e.g. "[0]"
    named_by: list[str]  # which of its fields the request names (sender, subject, filename, title)
    text: str  # the record's text, for checking that a value or action comes from it


class SelectionCheck(BaseModel):
    """Delegation test C for superlative picks: the request selects one record by a rule
    ("the largest file", "the latest email"). Which record does that rule select among the
    records earlier tool outputs returned, and is it the one the call uses?"""

    field: str  # argument holding the record id, e.g. "file_id"
    value: str  # the id the call uses
    phrase: str  # the request's selection phrase, e.g. "largest file"
    expected: list[str]  # id(s) the rule selects (several on a tie)
    detail: str = ""  # e.g. "size of '13' = 1,024; largest seen = 2,048 ('11')"

    @property
    def ok(self) -> bool:
        return self.value in self.expected


class SourceCheck(BaseModel):
    """Delegation test A for recipients: an address that is a whole field of an earlier tool output
    counts as given by the user only if a record holding it is one the request names (by sender,
    subject, title, filename or contact name), or one named by a record the request explicitly
    delegates to."""

    field: str  # recipient argument, e.g. "recipients"
    value: str  # the address
    found_in: list[str]  # records holding it as a whole field, e.g. "get_day_calendar_events#1[0] (title=Team Sync)"
    named: list[str] = Field(default_factory=list)  # those the request names, with the naming field

    @property
    def ok(self) -> bool:
        return bool(self.named)


class AuthorizationRequest(BaseModel):
    user_request: str
    policy: str = ""
    tool_call: ToolCall
    evidence: list[ArgumentEvidence] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)
    # Filled by the dispatcher only (instrumentation), never by the caller.
    delegated_sources: list[DelegatedSource] = Field(default_factory=list)
    selection_checks: list[SelectionCheck] = Field(default_factory=list)
    source_checks: list[SourceCheck] = Field(default_factory=list)


class GateResult(BaseModel):
    decision: Decision
    affected_fields: list[str] = Field(default_factory=list)
    # Model confidence, if a model was consulted. A confidence is not permission.
    confidence: float | None = None
    reasons: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    decided_by: Literal["policy", "approval", "classifier"]
