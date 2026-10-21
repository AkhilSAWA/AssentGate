import pytest

from assentgate.annotation import cohen_kappa, confusion, merge_allowed, per_class_kappa


def test_perfect_agreement_is_one():
    labels = ["authorized", "blocked", "safely_derived", "blocked"]
    assert cohen_kappa(labels, labels) == 1.0


def test_textbook_kappa():
    # Classic 2x2 example: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> kappa = 0.4
    a = ["yes"] * 25 + ["no"] * 25
    b = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.4)


def test_chance_level_agreement_is_zero():
    a = ["authorized", "authorized", "blocked", "blocked"]
    b = ["authorized", "blocked", "authorized", "blocked"]
    assert cohen_kappa(a, b) == pytest.approx(0.0)


def test_merging_allowed_classes_removes_that_disagreement():
    a = ["authorized", "safely_derived", "blocked"]
    b = ["safely_derived", "authorized", "blocked"]
    assert cohen_kappa(a, b) < 0.5
    assert cohen_kappa(merge_allowed(a), merge_allowed(b)) == 1.0


def test_per_class_and_confusion():
    a = ["blocked", "blocked", "authorized"]
    b = ["blocked", "authorized", "authorized"]
    assert set(per_class_kappa(a, b)) == {"authorized", "blocked"}
    m = confusion(a, b)
    assert m["blocked"]["blocked"] == 1 and m["blocked"]["authorized"] == 1


def test_mismatched_lengths_rejected():
    with pytest.raises(ValueError):
        cohen_kappa(["a"], ["a", "b"])
