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

from datetime import datetime

from assentgate.schemas import (
    ArgumentEvidence,
    DelegatedSource,
    EvidenceSource,
    SelectionCheck,
    SourceCheck,
    ToolCall,
)

MIN_CONTAINED_LEN = 8
DELEGATED_TEXT_CHARS = 4000
NAMING_FIELDS = ("sender", "subject", "filename", "title")
# Phrases with which a user explicitly hands authority to the content of something they name
# (annotation guidelines v0.3, "explicit delegation to content").
EXPLICIT_DELEGATION = re.compile(
    r"\b(?:(?:actions?|tasks?|instructions?|steps?|requests?|things)\s+(?:specified|described|listed|"
    r"mentioned|given|requested|outlined)\s+in|do\s+what\b.{0,60}?\b(?:says|asks|requests)|"
    r"follow\s+(?:the\s+)?(?:instructions|steps|directions)|(?:mentioned|listed|named)\s+in)\b", re.I)


# Superlative picks for delegation test C: "the largest file", "the most recent email".
# Relative words ("next", "last", "first") are left out: they depend on today's date or on order.
SELECTION = re.compile(
    r"\b(largest|biggest|smallest|latest|most\s+recent|newest|oldest|earliest)\s+(?:[a-z'-]+\s+){0,2}?"
    r"(files?|documents?|emails?|messages?|events?|meetings?|appointments?)\b", re.I)
SELECTION_ORDER = {"largest": ("size", max), "biggest": ("size", max), "smallest": ("size", min),
                   "latest": ("time", max), "most recent": ("time", max), "newest": ("time", max),
                   "oldest": ("time", min), "earliest": ("time", min)}
# Record kind -> (field that marks a record of this kind, {attribute: field holding it}).
RECORD_KINDS = {
    "file": ("filename", {"size": "size", "time": "last_modified"}),
    "email": ("sender", {"time": "timestamp"}),
    "event": ("start_time", {"time": "start_time"}),
}
NOUN_KIND = {"file": "file", "document": "file", "email": "email", "message": "email",
             "event": "event", "meeting": "event", "appointment": "event"}
RECORD_ID = "id_"
# Delegation test A ("the request names the source"): fields by which a request can name a record.
# Emails by sender or subject, events by title, files by filename, contacts by name.
SOURCE_NAMING_FIELDS = ("sender", "subject", "title", "filename", "name")
# The content a user hands over under explicit delegation (guidelines v0.3), as opposed to the
# delegated record's own headers (its sender, recipients...).
DELEGATED_CONTENT_FIELDS = ("body", "content", "description")
SOURCE_DESCRIBE_CHARS = 40


def _within(record: str, outer: str) -> bool:
    """True if `record` is `outer` or nested inside it ('' is a whole single-record output)."""
    return record == outer or not outer or record.startswith(outer + ".") or record.startswith(outer + "[")


def _describe(fields: list[tuple[str, str]]) -> str:
    parts = [f"{f}={t[:SOURCE_DESCRIBE_CHARS]}" for f, t in fields if f in SOURCE_NAMING_FIELDS]
    return " ".join(parts)


def _sort_key(attribute: str, text: str):
    if attribute == "size":
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


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

    def _records(self, kind: str) -> dict[str, dict[str, str]]:
        """Records of one kind seen in earlier outputs, by id (the latest copy wins)."""
        marker, _ = RECORD_KINDS[kind]
        out: dict[str, dict[str, str]] = {}
        for _, items in self._outputs:
            grouped: dict[str, dict[str, str]] = {}
            for path, text in items:
                grouped.setdefault(_parent(path), {})[field_name(path)] = text
            for fields in grouped.values():
                if RECORD_ID in fields and marker in fields:
                    out[fields[RECORD_ID]] = fields
        return out

    def selection_checks(self, call: ToolCall, user_request: str,
                         sensitive_fields: Iterable[str] = ()) -> list[SelectionCheck]:
        """For each superlative pick in the request ("the largest file"), the record that rule selects
        among the records seen so far, compared with the record id a sensitive argument uses.

        Only ids of seen records of the same kind are checked; values from elsewhere are left to the
        provenance evidence. A rule over seen records only: if the agent never listed everything, the
        "largest seen" may not be the largest that exists.
        """
        checks = []
        sensitive = set(sensitive_fields)
        for m in SELECTION.finditer(user_request):
            word = re.sub(r"\s+", " ", m.group(1).lower())
            noun = m.group(2).lower().rstrip("s")
            kind = NOUN_KIND[noun]
            attribute, pick = SELECTION_ORDER[word]
            attr_field = RECORD_KINDS[kind][1].get(attribute)
            if attr_field is None:  # e.g. "largest email": no size field to rank by
                continue
            records = self._records(kind)
            keyed = {rid: _sort_key(attribute, f.get(attr_field, "")) for rid, f in records.items()}
            keyed = {rid: k for rid, k in keyed.items() if k is not None}
            if not keyed:
                continue
            best = pick(keyed.values())
            expected = sorted(rid for rid, k in keyed.items() if k == best)
            phrase = f"{word} {noun}"
            for field, arg in call.arguments.items():
                if field not in sensitive:
                    continue
                for _, value in leaves(arg):
                    if value not in keyed:
                        continue
                    detail = (f"{attr_field} of '{value}' = {records[value][attr_field]}; {word} seen = "
                              f"{records[expected[0]][attr_field]} ({', '.join(repr(e) for e in expected)})")
                    checks.append(SelectionCheck(field=field, value=value, phrase=phrase,
                                                 expected=expected, detail=detail))
        return checks

    def delegated_content(self, delegated: Iterable[DelegatedSource]) -> list[str]:
        """Free text (body, content, description) of the records a request explicitly delegates to."""
        out = []
        for ds in delegated:
            for label, items in self._outputs:
                if label == ds.source:
                    out += [t for p, t in items
                            if field_name(p) in DELEGATED_CONTENT_FIELDS and _within(_parent(p), ds.record)]
        return out

    def source_checks(self, call: ToolCall, user_request: str, recipient_fields: Iterable[str] = (),
                      delegated: Iterable[DelegatedSource] = ()) -> list[SourceCheck]:
        """Delegation test A for recipient values that are a whole field of an earlier output.

        For each such address, every record holding it (in any earlier output) and which of those
        records the request names: one of its SOURCE_NAMING_FIELDS matches the request the same way
        explicit delegation does (`_names_record`), or matches the content of a record the request
        explicitly delegates to (one hop, guidelines v0.3). A record nested in a named record (an
        event attached to a named email) counts as named. Values found nowhere as a whole field get no
        check; the provenance evidence covers them.
        """
        fields = set(recipient_fields)
        text = " ".join([user_request, *self.delegated_content(delegated)]).lower()
        words = set(re.findall(r"[a-z0-9]+", text))
        records_by_output = []
        for label, items in self._outputs:
            grouped: dict[str, list[tuple[str, str]]] = {}
            for path, t in items:
                grouped.setdefault(_parent(path), []).append((field_name(path), t))
            named = {r: sorted({f for f, t in fs if f in SOURCE_NAMING_FIELDS and _names_record(f, t, text, words)})
                     for r, fs in grouped.items()}
            records_by_output.append((label, grouped, {r: n for r, n in named.items() if n}))
        checks = []
        for field, arg in call.arguments.items():
            if field not in fields:
                continue
            for _, value in leaves(arg):
                low = value.lower()
                found, named_in = [], []
                for label, grouped, named in records_by_output:
                    for record, fs in grouped.items():
                        if not any(t.lower() == low for _, t in fs):
                            continue
                        where = f"{label}{record}"
                        found.append(f"{where} ({_describe(fs)})" if _describe(fs) else where)
                        hits = sorted({f for outer, n in named.items() if _within(record, outer) for f in n})
                        if hits:
                            named_in.append(f"{where} (named by {', '.join(hits)})")
                if found:
                    checks.append(SourceCheck(field=field, value=value, found_in=found, named=named_in))
        return checks

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
