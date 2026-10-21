import importlib.util
from pathlib import Path

from assentgate.annotation import LABELS
from assentgate.workspace import WORKSPACE_TOOLS

SPECS = {t.name: t for t in WORKSPACE_TOOLS}
PATH = Path(__file__).resolve().parents[1] / "scripts" / "make_benign_ambiguous.py"
_spec = importlib.util.spec_from_file_location("make_benign_ambiguous", PATH)
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)


def test_records_are_valid_calls_with_consistent_labels():
    rows = list(builder.records())
    assert len({r["record_id"] for r in rows}) == len(rows)
    for r in rows:
        spec = SPECS[r["tool"]]
        assert set(spec.required_args) <= set(r["args"]) <= set(spec.required_args) | set(spec.optional_args)
        assert r["label"] in LABELS and r["needs_review"]
        assert set(r["evidence"]) <= set(r["args"])
        assert set(r["unresolved_fields"]) | set(r["prohibited_fields"]) <= set(r["args"])
        if r["label"] == "confirmation_required":
            assert r["unresolved_fields"]
        if r["label"] == "blocked":
            assert r["prohibited_fields"]


def test_each_scenario_stays_in_one_split():
    splits = {}
    for r in builder.records():
        splits.setdefault(r["group_id"], set()).add(r["split"])
    assert all(len(s) == 1 for s in splits.values())


def test_every_scenario_has_a_delegated_variant_needing_no_prompt():
    for scenario, variants in builder.SCENARIOS.items():
        delegated = [v for v in variants if v[0] == "delegated"]
        assert delegated and delegated[0][6] in ("authorized", "safely_derived"), scenario
