"""Value-level provenance tagging for tool arguments.

The dispatcher records every value a tool returns and the user request it
was given. When a later argument reuses one of those values, the argument gets
`verified=True` evidence naming the source. "Verified" means the value
matched a source the dispatcher captured itself; it is never inferred.

Exact matches count for any length. Containment (an address inside an email
body) only counts for values of MIN_CONTAINED_LEN or more characters, so tiny
values like "1" don't match by coincidence.
"""

from __future__ import annotations

from typing import Any

from assentgate.schemas import ArgumentEvidence, EvidenceSource, ToolCall

MIN_CONTAINED_LEN = 8


def flat_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [s for v in value.values() for s in flat_values(v)]
    if isinstance(value, (list, tuple, set)):
        return [s for v in value for s in flat_values(v)]
    text = str(value).strip()
    return [text] if text else []


def _matches(value: str, source_values: list[str]) -> bool:
    needle = value.lower()
    for candidate in source_values:
        hay = candidate.lower()
        if needle == hay or (len(needle) >= MIN_CONTAINED_LEN and needle in hay):
            return True
    return False


class ProvenanceTracker:
    def __init__(self) -> None:
        # (source label, flattened values), in the order tools ran.
        self._outputs: list[tuple[str, list[str]]] = []

    def record_output(self, tool: str, output: Any) -> None:
        label = f"{tool}#{len(self._outputs)}"
        self._outputs.append((label, flat_values(output)))

    def evidence_for(self, call: ToolCall, user_request: str) -> list[ArgumentEvidence]:
        evidence = []
        for field, arg in call.arguments.items():
            values = flat_values(arg)
            if not values:
                continue
            if all(_matches(v, [user_request]) for v in values):
                evidence.append(
                    ArgumentEvidence(field=field, source=EvidenceSource.USER, verified=True)
                )
            for label, output_values in self._outputs:
                if any(_matches(v, output_values) for v in values):
                    evidence.append(
                        ArgumentEvidence(
                            field=field,
                            source=EvidenceSource.TOOL_OUTPUT,
                            detail=label,
                            verified=True,
                        )
                    )
        return evidence
