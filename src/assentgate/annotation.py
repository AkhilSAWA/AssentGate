"""Inter-annotator agreement for the four authorization labels."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

LABELS = ("authorized", "safely_derived", "confirmation_required", "blocked")
# ROADMAP improvement 7: authorized vs. safely_derived is the likeliest disagreement,
# so agreement is also reported with the two merged.
MERGED = {"authorized": "allowed", "safely_derived": "allowed"}


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("Need two non-empty label lists of equal length.")
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1.0:
        return 1.0  # both annotators used one identical label throughout
    return (observed - expected) / (1 - expected)


def per_class_kappa(a: Sequence[str], b: Sequence[str]) -> dict[str, float]:
    """One-vs-rest kappa for each label either annotator used."""
    used = [k for k in LABELS if k in a or k in b]
    return {k: cohen_kappa([x == k for x in a], [y == k for y in b]) for k in used}


def confusion(a: Sequence[str], b: Sequence[str]) -> dict[str, dict[str, int]]:
    counts = Counter(zip(a, b))
    return {x: {y: counts[(x, y)] for y in LABELS} for x in LABELS}


def merge_allowed(labels: Sequence[str]) -> list[str]:
    return [MERGED.get(x, x) for x in labels]
