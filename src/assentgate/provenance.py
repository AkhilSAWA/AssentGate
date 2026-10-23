"""Value-level provenance tagging for tool arguments.

The dispatcher records every value a tool returns (with its field path) and the
user request it was given. For each value of a later call's arguments it emits
evidence saying where that value was seen:

  - USER          the value appears in the user request
  - TOOL_OUTPUT   the value was returned by an earlier tool, either as a whole
                  field (`match="exact"`, e.g. an email's `sender`) or only
                  inside a longer text field (`match="contained"`, e.g. inside
                  an email `body`, which is where planted instructions live)
  - UNKNOWN       a sensitive value seen nowhere (only emitted for sensitive fields)

"Verified" means the dispatcher matched the value against a source it captured
itself; it is never inferred. Exact matches count for any length. Containment
only counts for values of MIN_CONTAINED_LEN or more characters, so tiny values
like "1" don't match by coincidence.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from assentgate.schemas import ArgumentEvidence, DelegatedSource, EvidenceSource, ToolCall

MIN_CONTAINED_LEN = 8
DELEGATED_TEXT_CHARS = 4000
NAMING_FIELDS = ("sender", "subject", "filename", "title")
# Phrases with which a user explicitly hands authority to the content of something they name
# (annotation guidelines v0.3, "explicit delegation to content").
EXPLICIT_DELEGATION = re.compile(
    r"\b(?:(?:actions?|tasks?|instructions?|steps?|requests?|things)\s+(?:specified|described|listed|"
    r"mentioned|given|requested|outlined)\s+in|do\s+what\b.{0,60}?\b(?:says|asks|requests)|"
    r"follow\s+(?:the\s+)?(?:instructions|steps|directions)|(?:mentioned|listed|named)\s+in)\b", re.I)


def _names_record(field: str, value: str, request_lower: str, request_words: set[str]) -> bool:
    v = value.lower().strip()
    if len(v) >= 4 and v in request_lower:
        return True
    if field == "filename":
        stem = v.rsplit(".", 1)[0]
        tokens = [t for t in re.split(r"[-_. ]+", stem) if t.isalpha() and len(t) >= 3]
        return len(tokens) >= 2 and all(t in request_words for t in tokens)
    return False
SNIPPET_CHARS = 120
MAX_OUTPUT_MATCHES = 3  # most recent matching outputs reported per value


def flat_values(value: Any) -> list[str]:
    return [v for _, v in leaves(value)]


def leaves(value: Any, path: str = "") -> list[tuple[str, str]]:
    """(field path, text) for every scalar inside a tool result."""
    if value is None:
        return []
    if hasattr(value, "model_dump"):  # pydantic models, e.g. AgentDojo tool results
        value = value.model_dump()
    if isinstance(value, dict):
        # Keys that aren't plain names (e.g. e-mail addresses as dict keys) go in brackets.
        return [leaf for k, v in value.items()
                for leaf in leaves(v, (f"{path}.{k}" if path else str(k)) if re.fullmatch(r"\w+", str(k))
                                   else f"{path}[{k!r}]")]
    if isinstance(value, (list, tuple, set)):
        return [leaf for i, v in enumerate(value) for leaf in leaves(v, f"{path}[{i}]")]
    text = str(value).strip()
    return [(path, text)] if text else []


def _unbracketed(path: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", path)


def field_name(path: str) -> str:
    """Last named key of a path: '[0].participants[2]' -> 'participants'."""
    return _unbracketed(path).split(".")[-1]


def _parent(path: str) -> str:
    """Path of the record holding a field: '[3].shared_with['a@b.c']' -> '[3]'."""
    depth = 0
    for i in range(len(path) - 1, -1, -1):
        ch = path[i]
        if ch == "]":
            depth += 1
        elif ch == "[":
            depth -= 1
        elif ch == "." and depth == 0:
            return path[:i]
    return ""


def _snippet(hay: str, start: int, length: int) -> str:
    lo, hi = max(0, start - SNIPPET_CHARS), min(len(hay), start + length + SNIPPET_CHARS)
    return ("…" if lo else "") + hay[lo:hi].replace("\n", " ") + ("…" if hi < len(hay) else "")


RECORD_FIELD_CHARS = 60
RECORD_FIELDS = 6


def _record_context(path: str, items: list[tuple[str, str]]) -> str:
    """Short sibling fields of the record holding an exact match, e.g. 'id_=13 filename=budget.xlsx',
    so a reader can judge whether this record is the one the request refers to."""
    prefix = _parent(path)
    parts = []
    for p, text in items:
        if (p == path or (prefix and not p.startswith(prefix + ".")) or "[" in p[len(prefix) + 1:]
                or len(text) > RECORD_FIELD_CHARS):
            continue
        parts.append(f"{field_name(p)}={text}")
        if len(parts) >= RECORD_FIELDS:
            break
    return " ".join(parts)


def _find(needle: str, items: list[tuple[str, str]]) -> tuple[str, str, str] | None:
    """Best match of needle among (path, text): exact field first, then containment."""
    low = needle.lower()
    contained = None
    for path, text in items:
        hay = text.lower()
        if hay == low:
            return path, "exact", _record_context(path, items)
        if contained is None and len(low) >= MIN_CONTAINED_LEN:
            i = hay.find(low)
            if i >= 0:
                contained = (path, "contained", _snippet(text, i, len(low)))
    return contained


class ProvenanceTracker:
    def __init__(self) -> None:
        # (source label, [(field path, text)]), in the order tools ran.
        self._outputs: list[tuple[str, list[tuple[str, str]]]] = []

    def delegated_sources(self, user_request: str) -> list[DelegatedSource]:
        """Records of earlier outputs that the request explicitly delegates to and names.

        Empty unless the request uses an explicit-delegation phrase (EXPLICIT_DELEGATION). A record
        counts as named when the request contains its sender/subject/title, or every word of its
        filename. One hop only: a file mentioned inside a named email is not itself delegated.
        """
        if not EXPLICIT_DELEGATION.search(user_request):
            return []
        low = user_request.lower()
        words = set(re.findall(r"[a-z0-9]+", low))
        out = []
        for label, items in self._outputs:
            records: dict[str, list[tuple[str, str]]] = {}
            for path, text in items:
                records.setdefault(_parent(path), []).append((path, text))
            for record, fields in records.items():
                named = sorted({field_name(p) for p, t in fields
                                if field_name(p) in NAMING_FIELDS and _names_record(field_name(p), t, low, words)})
                if named:
                    out.append(DelegatedSource(source=label, record=record, named_by=named,
                                               text=" ".join(t for _, t in fields)[:DELEGATED_TEXT_CHARS]))
        return out

    def seen_values(self, field_names: Iterable[str]) -> list[str]:
        """Distinct values earlier outputs returned under the given field names, most recent first."""
        names, out = set(field_names), []
        for _, items in reversed(self._outputs):
            for path, text in items:
                if field_name(path) in names and text not in out:
                    out.append(text)
        return out

    def record_output(self, tool: str, output: Any) -> None:
        label = f"{tool}#{len(self._outputs)}"
        self._outputs.append((label, leaves(output)))

    def evidence_for(
        self, call: ToolCall, user_request: str, sensitive_fields: Iterable[str] = ()
    ) -> list[ArgumentEvidence]:
        sensitive = set(sensitive_fields)
        evidence = []
        for field, arg in call.arguments.items():
            # Skip structural markers such as AgentDojo's attachment {"type": "file", ...}.
            for value in [v for p, v in leaves(arg) if field_name(p) != "type"]:
                found = False
                if _find(value, [("request", user_request)]) is not None:
                    found = True
                    evidence.append(ArgumentEvidence(field=field, source=EvidenceSource.USER,
                                                     value=value, verified=True))
                matches = 0
                for label, items in reversed(self._outputs):
                    hit = _find(value, items)
                    if hit is None:
                        continue
                    path, match, snippet = hit
                    found = True
                    evidence.append(ArgumentEvidence(
                        field=field, source=EvidenceSource.TOOL_OUTPUT, value=value, detail=label,
                        path=path, match=match, snippet=snippet or None, verified=True))
                    matches += 1
                    if matches >= MAX_OUTPUT_MATCHES:
                        break
                if not found and field in sensitive:
                    evidence.append(ArgumentEvidence(field=field, source=EvidenceSource.UNKNOWN,
                                                     value=value, verified=True))
        return evidence
