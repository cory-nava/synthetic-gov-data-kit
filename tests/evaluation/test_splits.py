"""Tests for the leakage-guarded train/dev/test splitter.

Three independent guards are under test:

1. Whole-jurisdiction holdout -- a jurisdiction in `holdout_states` never
   appears in `train` or `dev`.
2. Disjoint case_ids across partitions.
3. Every jurisdiction present in the input is validated against the FY BBCE
   table before partitioning, seed-independently -- an off-table code (e.g.
   PR) must raise no matter which partition it would otherwise land in
   (`test_off_table_jurisdiction_raises_across_seeds`).

Plus:
  - the holdout-category labelling, which must be derived from the buckets
    that actually survive into `train` rather than from a hardcoded list of
    jurisdictions (`test_category_is_derived_from_train_not_hardcoded`);
  - determinism under a differently-ordered input, not just a repeated call
    on the same list object (`test_split_is_independent_of_input_order`);
  - no stale `holdout_category` label surviving a second call on the same
    case objects with a different holdout set
    (`test_stale_holdout_category_does_not_survive_a_second_split`).
"""

from __future__ import annotations

import json
import random

import pytest
from govsynth.evaluation.splits import HOLDOUT_JURISDICTIONS, parameter_bucket, split_cases
from govsynth.models.test_case import TestCase
from govsynth.sources.base import THRESHOLD_DIR


@pytest.fixture(scope="module")
def mixed_cases() -> list[TestCase]:
    """A fast four-state fixture for the mechanical split tests."""
    from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator

    cases: list[TestCase] = []
    for i, st in enumerate(["VA", "CA", "TX", "KS"]):
        cases += SNAPEligibilityGenerator(fiscal_year=2026, state=st).generate(n=60, seed=1000 + i)
    return cases


@pytest.fixture(scope="module")
def cases_with_off_table_jurisdiction() -> list[TestCase]:
    """mixed_cases-shaped, plus a couple of PR cases.

    PR is not in the FY2026 BBCE table (it runs NAP, not SNAP) but the
    generator itself doesn't know or care -- SNAPBBCESource silently falls
    back to federal_default for an unrecognized code, so PR cases generate
    without error. Only split_cases()'s up-front validation should reject them.
    """
    from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator

    cases: list[TestCase] = []
    for i, st in enumerate(["VA", "CA", "TX", "KS"]):
        cases += SNAPEligibilityGenerator(fiscal_year=2026, state=st).generate(n=60, seed=1000 + i)
    cases += SNAPEligibilityGenerator(fiscal_year=2026, state="PR").generate(n=2, seed=1999)
    return cases


@pytest.fixture(scope="module")
def full_cases() -> list[TestCase]:
    """Covers every jurisdiction in the FY2026 BBCE table so the categorization
    tests have real buckets to work with. Loaded via govsynth's own
    THRESHOLD_DIR (anchored to the package location, not the test runner's
    cwd) rather than a cwd-relative "data/..." path.
    """
    from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator

    table = json.loads((THRESHOLD_DIR / "snap_bbce_fy2026.json").read_text())["states"]
    cases: list[TestCase] = []
    for i, st in enumerate(sorted(table)):
        cases += SNAPEligibilityGenerator(fiscal_year=2026, state=st).generate(n=8, seed=2000 + i)
    return cases


# --- mechanical split guarantees -----------------------------------------


def test_no_case_id_appears_in_two_partitions(mixed_cases: list[TestCase]) -> None:
    s = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    ids = [{c.case_id for c in part} for part in (s.train, s.dev, s.test)]
    assert ids[0] & ids[1] == set()
    assert ids[0] & ids[2] == set()
    assert ids[1] & ids[2] == set()


def test_holdout_state_never_appears_in_train_or_dev(mixed_cases: list[TestCase]) -> None:
    s = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    assert not any(c.scenario.state == "KS" for c in s.train)
    assert not any(c.scenario.state == "KS" for c in s.dev)
    assert any(c.scenario.state == "KS" for c in s.test)


def test_test_partition_also_contains_seen_states(mixed_cases: list[TestCase]) -> None:
    # Otherwise the eval only measures transfer, and we lose the in-distribution number.
    s = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    assert {c.scenario.state for c in s.test} > {"KS"}


def test_split_is_deterministic_at_a_fixed_seed(mixed_cases: list[TestCase]) -> None:
    a = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    b = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    assert [c.case_id for c in a.train] == [c.case_id for c in b.train]
    assert [c.case_id for c in a.test] == [c.case_id for c in b.test]


def test_different_seeds_produce_different_dev_membership(mixed_cases: list[TestCase]) -> None:
    a = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    b = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=8)
    assert {c.case_id for c in a.dev} != {c.case_id for c in b.dev}


def test_manifest_counts_match_partition_lengths(mixed_cases: list[TestCase]) -> None:
    s = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    assert s.manifest["counts"] == {"train": len(s.train), "dev": len(s.dev), "test": len(s.test)}
    assert sum(s.manifest["counts"].values()) == len(mixed_cases)


def test_every_difficulty_present_in_test_partition(mixed_cases: list[TestCase]) -> None:
    # A test set missing HARD or ADVERSARIAL cases would flatter every model.
    s = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    train_levels = {c.difficulty for c in mixed_cases}
    assert {c.difficulty for c in s.test} == train_levels


def test_unknown_holdout_state_raises(mixed_cases: list[TestCase]) -> None:
    with pytest.raises(ValueError, match="ZZ"):
        split_cases(mixed_cases, holdout_states={"ZZ"}, dev_fraction=0.1, seed=7)


def test_split_is_independent_of_input_order(mixed_cases: list[TestCase]) -> None:
    # Guards the pre-shuffle sort: without it, `seed` reproduces a split only
    # as long as the caller's input list happens to arrive in the same order
    # every time -- true of a module-scoped fixture reused within one pytest
    # session, but not a property split_cases can rely on from any caller.
    a = split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    shuffled = list(mixed_cases)
    random.Random(99).shuffle(shuffled)
    b = split_cases(shuffled, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    assert [c.case_id for c in a.train] == [c.case_id for c in b.train]
    assert [c.case_id for c in a.test] == [c.case_id for c in b.test]


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 42, 99])
def test_off_table_jurisdiction_raises_across_seeds(
    cases_with_off_table_jurisdiction: list[TestCase], seed: int
) -> None:
    # PR must be rejected regardless of which partition it would otherwise
    # land in. Before the up-front validation guard, PR's fate depended on
    # where the shuffle happened to place it: some seeds put both PR cases in
    # `dev`, which was never checked against the BBCE table, so split_cases
    # returned normally instead of raising. Sweeping seeds pins that shut.
    with pytest.raises(ValueError, match="PR"):
        split_cases(cases_with_off_table_jurisdiction, holdout_states={"KS"}, dev_fraction=0.1, seed=seed)


def test_stale_holdout_category_does_not_survive_a_second_split(mixed_cases: list[TestCase]) -> None:
    # split_cases mutates the TestCase objects it's given. Calling it again on
    # the same list with a different holdout set must not leave any case
    # carrying a category label computed for the *previous* call.
    split_cases(mixed_cases, holdout_states={"KS"}, dev_fraction=0.1, seed=7)
    s = split_cases(mixed_cases, holdout_states={"CA"}, dev_fraction=0.1, seed=7)
    for case in s.train:
        assert "holdout_category" not in case.metadata
    for case in s.dev:
        assert "holdout_category" not in case.metadata
    for case in s.test:
        assert case.metadata["holdout_category"] in {
            "in_distribution",
            "unseen_jurisdiction_seen_pattern",
            "unseen_jurisdiction_unseen_pattern",
        }


# --- holdout categorization -----------------------------------------------


def test_parameter_bucket_separates_bbce_from_non_bbce() -> None:
    assert parameter_bucket("KS", fiscal_year=2026) == ("48_states_dc", "non_bbce")
    assert parameter_bucket("CA", fiscal_year=2026) == ("48_states_dc", 200, None)
    assert parameter_bucket("NE", fiscal_year=2026) == ("48_states_dc", 165, 25000)


def test_jurisdictions_sharing_a_bucket_compare_equal() -> None:
    # AL and GA are both (130% FPL gross, no asset cap) BBCE in the 48-states region --
    # a genuinely same-bucket-same-region pair, unlike GU/IL (see
    # test_gu_becomes_unseen_pattern_once_region_is_part_of_the_bucket below).
    assert parameter_bucket("AL", fiscal_year=2026) == parameter_bucket("GA", fiscal_year=2026)


def test_gu_and_il_no_longer_share_a_bucket() -> None:
    # Before region was part of the bucket, GU's block was byte-identical to IL's BBCE
    # parameters (both 165% FPL / no asset cap), so holding GU out tested nothing but
    # whether the model recognizes an unfamiliar state code. Guam's allotment, standard
    # deduction, and shelter cap are its own (see data/thresholds/snap_fy2026.json), so
    # the two must now bucket separately.
    assert parameter_bucket("GU", fiscal_year=2026) != parameter_bucket("IL", fiscal_year=2026)


def test_seen_pattern_holdout_is_labelled_seen_pattern(full_cases: list[TestCase]) -> None:
    # KS shares ("48_states_dc", "non_bbce") with MO/MS/SD/TN/UT, all of which stay
    # in training, so holding KS out is a seen-jurisdiction/seen-pattern probe.
    s = split_cases(full_cases, holdout_states=HOLDOUT_JURISDICTIONS, dev_fraction=0.1, seed=7, fiscal_year=2026)
    ks = [c for c in s.test if c.scenario.state == "KS"]
    assert ks, "no KS cases reached the test partition"
    assert {c.metadata["holdout_category"] for c in ks} == {"unseen_jurisdiction_seen_pattern"}


def test_unseen_pattern_holdout_is_labelled_unseen_pattern(full_cases: list[TestCase]) -> None:
    # VI is the only ("virgin_islands", 175, None) jurisdiction, so holding it out
    # empties the bucket.
    s = split_cases(full_cases, holdout_states=HOLDOUT_JURISDICTIONS, dev_fraction=0.1, seed=7, fiscal_year=2026)
    vi = [c for c in s.test if c.scenario.state == "VI"]
    assert vi
    assert {c.metadata["holdout_category"] for c in vi} == {"unseen_jurisdiction_unseen_pattern"}


def test_gu_becomes_unseen_pattern_once_region_is_part_of_the_bucket(full_cases: list[TestCase]) -> None:
    # Before region was part of the bucket, GU shared (165, None) with IL and was
    # labelled unseen_jurisdiction_seen_pattern -- understating it, since GU's own
    # allotment/deduction/shelter-cap numbers were never actually seen in training.
    # With region in the bucket, ("guam", 165, None) is unique, so GU must be
    # unseen_jurisdiction_unseen_pattern even though IL stays in training.
    s = split_cases(full_cases, holdout_states=HOLDOUT_JURISDICTIONS, dev_fraction=0.1, seed=7, fiscal_year=2026)
    gu = [c for c in s.test if c.scenario.state == "GU"]
    assert gu, "no GU cases reached the test partition"
    assert {c.metadata["holdout_category"] for c in gu} == {"unseen_jurisdiction_unseen_pattern"}


def test_category_is_derived_from_train_not_hardcoded(full_cases: list[TestCase]) -> None:
    # Hold out IN as well as ID: ("48_states_dc", 130, 5000) is now empty in
    # training (ID was its only other holdout member; IN was its only trained
    # member), so ID must flip from seen-pattern to unseen-pattern without anyone
    # editing a list.
    s = split_cases(
        full_cases,
        holdout_states=HOLDOUT_JURISDICTIONS | {"IN"},
        dev_fraction=0.1,
        seed=7,
        fiscal_year=2026,
    )
    idaho = [c for c in s.test if c.scenario.state == "ID"]
    assert {c.metadata["holdout_category"] for c in idaho} == {"unseen_jurisdiction_unseen_pattern"}


def test_seen_jurisdictions_also_reach_test_as_in_distribution(full_cases: list[TestCase]) -> None:
    s = split_cases(full_cases, holdout_states=HOLDOUT_JURISDICTIONS, dev_fraction=0.1, seed=7, fiscal_year=2026)
    assert any(c.metadata["holdout_category"] == "in_distribution" for c in s.test)


def test_all_three_categories_are_populated(full_cases: list[TestCase]) -> None:
    # If any category is empty the report has nothing to compare and the whole
    # generalization claim rests on a missing number.
    s = split_cases(full_cases, holdout_states=HOLDOUT_JURISDICTIONS, dev_fraction=0.1, seed=7, fiscal_year=2026)
    cats = {c.metadata["holdout_category"] for c in s.test}
    assert cats == {
        "in_distribution",
        "unseen_jurisdiction_seen_pattern",
        "unseen_jurisdiction_unseen_pattern",
    }


def test_default_holdout_set_matches_the_plan() -> None:
    expected = frozenset({"KS", "WY", "ID", "IA", "NE", "AR", "GU", "VI", "CA", "NV"})
    assert expected == HOLDOUT_JURISDICTIONS


def test_texas_is_not_held_out() -> None:
    # TX is the only trained example of BBCE-with-an-asset-limit. Holding it out
    # would leave the model no example of that shape at all.
    assert "TX" not in HOLDOUT_JURISDICTIONS


def test_puerto_rico_is_not_a_jurisdiction() -> None:
    # PR runs NAP, not SNAP. It must never appear in either partition.
    with pytest.raises(ValueError):
        parameter_bucket("PR", fiscal_year=2026)
