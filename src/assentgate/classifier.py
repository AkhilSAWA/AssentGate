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


class SensitivityBaseline:
    """Asks for confirmation on every side-effecting call; allows reads."""

    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput:
        if not spec.side_effecting:
            return ClassifierOutput(decision=Decision.SAFELY_DERIVED)
        fields = [f for f in spec.sensitive_fields if f in request.tool_call.arguments]
        return ClassifierOutput(decision=Decision.CONFIRMATION_REQUIRED, affected_fields=fields)
