"""Authorization classifier interface.

The real model (DeBERTa-v3-small, plus an embeddings + logistic-regression
baseline) plugs in behind `Classifier`. Until one is trained, the gate uses
`SensitivityBaseline`, which is evaluation baseline 3 from the project plan:
"confirmation for every sensitive action".
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from assentgate.schemas import AuthorizationRequest, Decision, ToolSpec


class ClassifierOutput(BaseModel):
    decision: Decision
    affected_fields: list[str] = Field(default_factory=list)
    confidence: float | None = None


class Classifier(Protocol):
    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput: ...


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
