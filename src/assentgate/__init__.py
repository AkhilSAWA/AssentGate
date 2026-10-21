"""AssentGate: an authorization layer between an AI agent and its tools."""

from assentgate.approvals import Approval, ApprovalStore
from assentgate.classifier import Classifier, ClassifierOutput, SensitivityBaseline, ToolLevelRules
from assentgate.dispatcher import Dispatcher, DispatchResult
from assentgate.gate import AssentGate
from assentgate.policy import HardPolicy, PolicyConfig
from assentgate.schemas import (
    ArgumentEvidence,
    AuthorizationRequest,
    Decision,
    EvidenceSource,
    GateResult,
    HistoryEntry,
    ToolCall,
    ToolSpec,
)

__all__ = [
    "Approval",
    "ApprovalStore",
    "ArgumentEvidence",
    "AssentGate",
    "AuthorizationRequest",
    "Classifier",
    "ClassifierOutput",
    "Decision",
    "Dispatcher",
    "DispatchResult",
    "EvidenceSource",
    "GateResult",
    "HardPolicy",
    "HistoryEntry",
    "PolicyConfig",
    "SensitivityBaseline",
    "ToolCall",
    "ToolLevelRules",
    "ToolSpec",
]
