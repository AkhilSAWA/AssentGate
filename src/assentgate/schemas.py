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


class HistoryEntry(BaseModel):
    tool_call: ToolCall
    decision: Decision
    executed: bool


class AuthorizationRequest(BaseModel):
    user_request: str
    policy: str = ""
    tool_call: ToolCall
    evidence: list[ArgumentEvidence] = Field(default_factory=list)
    history: list[HistoryEntry] = Field(default_factory=list)


class GateResult(BaseModel):
    decision: Decision
    affected_fields: list[str] = Field(default_factory=list)
    # Model confidence, if a model was consulted. A confidence is not permission.
    confidence: float | None = None
    reasons: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    decided_by: Literal["policy", "approval", "classifier"]
