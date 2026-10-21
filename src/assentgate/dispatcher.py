"""Controlled dispatcher: tools only run after the gate allows them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from assentgate.audit import AuditLog
from assentgate.gate import AssentGate
from assentgate.provenance import ProvenanceTracker
from assentgate.schemas import AuthorizationRequest, GateResult, HistoryEntry

Handler = Callable[..., Any]
# Matches HISTORY_LEN in scripts/build_agentdojo_dataset.py so model inputs agree.
HISTORY_LEN = 5


@dataclass
class DispatchResult:
    result: GateResult
    executed: bool
    output: Any = None
    error: str | None = None


class Dispatcher:
    def __init__(self, gate: AssentGate, audit: AuditLog | None = None):
        self.gate = gate
        self.audit = audit or AuditLog()
        self.handlers: dict[str, Handler] = {}
        self.history: list[HistoryEntry] = []
        self.provenance = ProvenanceTracker()

    def register(self, tool: str, handler: Handler) -> None:
        if tool not in self.gate.tools:
            raise ValueError(f"No ToolSpec registered with the gate for '{tool}'.")
        self.handlers[tool] = handler

    def _instrument(self, request: AuthorizationRequest) -> AuthorizationRequest:
        # Only the dispatcher's own tagging may mark evidence verified, and its
        # own record of past calls replaces any caller-supplied history.
        supplied = [e.model_copy(update={"verified": False}) for e in request.evidence]
        tagged = self.provenance.evidence_for(request.tool_call, request.user_request)
        return request.model_copy(
            update={"evidence": supplied + tagged, "history": self.history[-HISTORY_LEN:]}
        )

    def submit(self, request: AuthorizationRequest) -> DispatchResult:
        request = self._instrument(request)
        call = request.tool_call
        result = self.gate.evaluate(request)
        executed, output, error = False, None, None

        if result.decision.allows_execution:
            if result.decided_by == "approval":
                approval = self.gate.approvals.find(call, self.gate.tools[call.tool])
                if approval is not None:
                    self.gate.approvals.consume(approval)
            try:
                output = self.handlers[call.tool](**call.arguments)
                executed = True
                self.provenance.record_output(call.tool, output)
            except Exception as exc:  # recorded in the audit log, not swallowed silently
                error = f"{type(exc).__name__}: {exc}"

        self.audit.record(call, result, executed, error)
        self.history.append(HistoryEntry(tool_call=call, decision=result.decision, executed=executed))
        return DispatchResult(result=result, executed=executed, output=output, error=error)

    def approve_and_submit(
        self, request: AuthorizationRequest, single_use: bool = True
    ) -> DispatchResult:
        """Record the user's approval for this exact call, then re-run the full gate."""
        spec = self.gate.tools.get(request.tool_call.tool)
        if spec is not None:
            self.gate.approvals.grant(request.tool_call, spec, single_use=single_use)
        return self.submit(request)
