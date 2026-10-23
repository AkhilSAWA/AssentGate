"""Learned AssentGate scorer: P(allow / confirm / block) for a side-effecting call.

`featurize` is the single definition of model input, used both when building training
data (scripts/make_constructed_dataset.py) and at runtime (`LearnedGate`). It reads only
what the gate has at runtime: the request, the proposed call, and dispatcher-verified
evidence (value sources, field paths, snippets).

Decision rule (thresholds are not tuned on test data):
  block   if P(block) >= t_block
  allow   elif P(allow) >= t_allow
  confirm otherwise, asking about the fields whose values weren't typed by the user
          or taken from an exact structured field.
"""

from __future__ import annotations

import re
from typing import Any

from assentgate.classifier import SOURCE_RANK, ClassifierOutput, evidence_kind
from assentgate.schemas import AuthorizationRequest, Decision, ToolSpec

LABELS = ("allow", "confirm", "block")
SIDE_EFFECT_TOOLS = (
    "send_email", "delete_email", "create_calendar_event", "cancel_calendar_event",
    "reschedule_calendar_event", "add_calendar_event_participants", "create_file",
    "append_to_file", "delete_file", "share_file",
)
INSTRUCTION_CUES = re.compile(
    r"\b(please|before you|after you do|make sure|do not|don't|ignore|important|instead|"
    r"send|forward|delete|share|email the|transfer)\b", re.I)
_STOP = {"the", "and", "for", "with", "that", "this", "from", "you", "your", "have", "are", "was", "will",
         "can", "all", "any", "not", "but", "has", "our", "out", "about", "into", "what", "which"}
SNIPPET_IN_TEXT = 200


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOP}


def best_evidence(request: AuthorizationRequest, spec: ToolSpec) -> dict[tuple[str, str], Any]:
    """Best verified evidence entry per (sensitive field, value)."""
    best: dict[tuple[str, str], Any] = {}

    def kind(e):
        return evidence_kind(e, request)

    for e in request.evidence:
        if not e.verified or e.value is None or e.field not in spec.sensitive_fields:
            continue
        key = (e.field, e.value)
        if key not in best or SOURCE_RANK[kind(e)] < SOURCE_RANK[kind(best[key])]:
            best[key] = e
    return {k: (kind(e), e) for k, e in best.items()}


def featurize(request: AuthorizationRequest, spec: ToolSpec) -> tuple[str, dict[str, float]]:
    """Text for the sentence encoder plus named numeric features."""
    ev = best_evidence(request, spec)
    lines = [f"[REQUEST] {request.user_request}", f"[TOOL] {spec.name}"]
    for ds in request.delegated_sources:
        lines.append(f"[DELEGATED] {ds.source} {ds.record} named by {','.join(ds.named_by)}")
    request_words = _words(request.user_request)
    counts = {k: 0 for k in SOURCE_RANK}
    overlaps, cue = [], 0.0
    for (field, value), (kind, e) in sorted(ev.items()):
        counts[kind] += 1
        snippet = (e.snippet or "")[:SNIPPET_IN_TEXT]
        lines.append(f"[ARG] {field}={value} <- {kind} {e.path or ''} | {snippet}".rstrip(" |"))
        if kind == "user":
            overlaps.append(1.0)
        elif kind == "exact":
            ctx = _words(f"{value} {snippet}")
            overlaps.append(len(ctx & request_words) / max(1, len(ctx)))
        if kind == "contained" and INSTRUCTION_CUES.search(snippet):
            cue = 1.0
    n = sum(counts.values())
    feats = {f"frac_{k}": (counts[k] / n if n else 0.0) for k in SOURCE_RANK}
    feats.update(
        n_values=float(n),
        any_contained=float(counts["contained"] > 0),
        any_unknown=float(counts["unknown"] > 0),
        contained_instruction_cue=cue,
        overlap_min=min(overlaps) if overlaps else 0.0,
        overlap_max=max(overlaps) if overlaps else 0.0,
        n_history=float(len(request.history)),
        explicit_delegation=float(bool(request.delegated_sources)),
    )
    for tool in SIDE_EFFECT_TOOLS:
        feats[f"tool_{tool}"] = float(spec.name == tool)
    return "\n".join(lines), feats


def confirm_fields(request: AuthorizationRequest, spec: ToolSpec) -> list[str]:
    ev = best_evidence(request, spec)
    fields = sorted({f for (f, _), (kind, _) in ev.items() if kind not in ("user", "exact", "named_content")})
    return fields or sorted({f for f, _ in ev}) or [f for f in spec.sensitive_fields if f in request.tool_call.arguments]


class LearnedGate:
    """Classifier wrapper around a trained scorer (see scripts/eval_crossfit.py)."""

    def __init__(self, model: dict, encoder: Any, t_allow: float = 0.5, t_block: float = 0.5):
        self.model, self.encoder = model, encoder
        self.t_allow, self.t_block = t_allow, t_block

    def probabilities(self, request: AuthorizationRequest, spec: ToolSpec) -> dict[str, float]:
        import numpy as np

        text, feats = featurize(request, spec)
        x = np.hstack([self.encoder.encode([text], show_progress_bar=False)[0],
                       self.model["scaler"].transform([[feats[k] for k in self.model["feature_names"]]])[0]])
        probs = self.model["clf"].predict_proba([x])[0]
        return {LABELS[c]: float(p) for c, p in zip(self.model["clf"].classes_, probs)}

    def predict(self, request: AuthorizationRequest, spec: ToolSpec) -> ClassifierOutput:
        if not spec.side_effecting:
            return ClassifierOutput(decision=Decision.SAFELY_DERIVED)
        p = self.probabilities(request, spec)
        if p.get("block", 0.0) >= self.t_block:
            return ClassifierOutput(decision=Decision.BLOCKED, confidence=p["block"],
                                    affected_fields=confirm_fields(request, spec))
        if p.get("allow", 0.0) >= self.t_allow:
            return ClassifierOutput(decision=Decision.AUTHORIZED, confidence=p["allow"])
        return ClassifierOutput(decision=Decision.CONFIRMATION_REQUIRED, confidence=1 - p.get("allow", 0.0),
                                affected_fields=confirm_fields(request, spec))
