import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts" / "train_classifier.py"
_spec = importlib.util.spec_from_file_location("train_classifier", PATH)
train = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(train)


def test_groups_never_split_across_folds():
    groups = [f"g{i % 7}" for i in range(50)]
    folds = train.group_folds(groups, k=3, seed=1)
    fold_of = {}
    for g, f in zip(groups, folds):
        assert fold_of.setdefault(g, f) == f
    assert set(folds) == {0, 1, 2}


def test_folds_are_reproducible_and_vary_by_seed():
    groups = [f"g{i}" for i in range(20)]
    assert train.group_folds(groups, 5, 0) == train.group_folds(groups, 5, 0)
    assert train.group_folds(groups, 5, 0) != train.group_folds(groups, 5, 1)


def test_summary_skips_metrics_missing_from_a_fold():
    s = train.summarise_folds([{"macro_f1": 0.2, "recall_blocked": 1.0, "n": 3}, {"macro_f1": 0.4, "n": 5}])
    assert s["macro_f1"]["mean"] == pytest.approx(0.3)
    assert s["recall_blocked"]["folds"] == 1
    assert "n" not in s


def test_report_recall_only_for_present_classes():
    m = train.report("t", [0, 0, 3], [0, 2, 1], verbose=False)
    assert m["recall_authorized"] == pytest.approx(0.5)
    assert m["recall_blocked"] == 0.0
    assert "recall_safely_derived" not in m
