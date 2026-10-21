"""The authorization pipeline: hard policy -> classifier -> scoped approvals.

Hard policy always takes precedence over model predictions, and no approval
can lift a block.
"""

from __future__ import annotations

from assentgate.approvals import ApprovalStore
from assentgate.classifier import Classifier, SensitivityBaseline
from assentgate.policy import HardPolicy
from assentgate.questions import confirmation_questions
from assentgate.schemas import AuthorizationRequest, Decision, GateResult, ToolSpec


class AssentGate:
    def __init__(
        self,
        tools: list[ToolSpec],
        policy: HardPolicy | None = None,
        classifier: Classifier | None = None,
        approvals: ApprovalStore | None = None,
    ):
        self.tools = {t.name: t for t in tools}
        self.policy = policy or HardPolicy()
        self.classifier = classifier or SensitivityBaseline()
        self.approvals = approvals or ApprovalStore()

    def evaluate(self, request: AuthorizationRequest) -> GateResult:
        call = request.tool_call
        spec = self.tools.get(call.tool)

        verdict = self.policy.evaluate(request, spec)
        if verdict.blocked:
            return GateResult(
                decision=Decision.BLOCKED,
                affected_fields=verdict.blocked_fields,
                reasons=verdict.reasons,
                decided_by="policy",
            )
        assert spec is not None  # unknown tools are blocked by policy

        prediction = self.classifier.predict(request, spec)
        if prediction.decision is Decision.BLOCKED:
            return GateResult(
                decision=Decision.BLOCKED,
                affected_fields=prediction.affected_fields,
                confidence=prediction.confidence,
                reasons=verdict.reasons + ["Classifier judged the call unauthorized."],
                decided_by="classifier",
            )

        pending = set(verdict.confirm_fields)
        if prediction.decision is Decision.CONFIRMATION_REQUIRED:
            pending.update(prediction.affected_fields)
        needs_confirmation = bool(verdict.confirm_fields) or (
            prediction.decision is Decision.CONFIRMATION_REQUIRED
        )

        if not needs_confirmation:
            return GateResult(
                decision=prediction.decision,
                confidence=prediction.confidence,
                reasons=verdict.reasons,
                decided_by="classifier",
            )

        if self.approvals.find(call, spec) is not None:
            return GateResult(
                decision=Decision.AUTHORIZED,
                affected_fields=sorted(pending),
                confidence=prediction.confidence,
                reasons=verdict.reasons + ["Covered by a scoped user approval."],
                decided_by="approval",
            )

        fields = sorted(pending)
        return GateResult(
            decision=Decision.CONFIRMATION_REQUIRED,
            affected_fields=fields,
            confidence=prediction.confidence,
            reasons=verdict.reasons,
            questions=confirmation_questions(call, fields),
            decided_by="policy" if verdict.confirm_fields else "classifier",
        )
