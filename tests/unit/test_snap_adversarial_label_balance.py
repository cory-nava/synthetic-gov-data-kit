"""No adversarial case type may be answerable from its own name.

Every case in this kit can be arithmetically correct and still be a bad training
signal. The seven special-population builders each stamp their case type into the
scenario prose in plain English -- "a college student enrolled half-time",
"receives TANF/SSI", "takes in a boarder", "a migrant agricultural worker",
"mixed immigration status". If a case type's label is (near-)constant, a model
does not have to apply 7 CFR anything: it can read the phrase, recall the
majority label, and be right. On the adversarial slice of the FY2026 test set,
case-type -> majority-label learned from `train.jsonl` alone already scores 83.8%.

That is exactly what the previous fix wave introduced, without breaking a single
one of the 578 tests that existed at the time. Sampling income against the
GOVERNING gross limit is right for deciding a case and wrong for placing one: in a
200%-FPL jurisdiction the gross limit is roughly twice the binding 100% FPL net
limit, so every draw near it clears the gross test easily and fails the net test
easily. Measured over 53 jurisdictions at 400 cases each:

    case type                                    before            after wave
    migrant_income_averaging                     eligible 60.4%    INELIGIBLE 76.8%
    mixed_immigration_status_hh_size_reduction    eligible 56.9%    INELIGIBLE 82.3%

with ZERO eligible cases of either type across all 28 200%-FPL jurisdictions --
two adversarial case types learnable by name with no counterexample in more than
half the country. `_binding_gross_ceiling` now anchors both builders on the income
at which the determination actually flips; this file is what keeps them there.

Case types that are constant BY DESIGN are named here, not exempted silently:
`student_exclusion` is always ineligible and `categorical_eligibility_tanf_ssi`
always eligible, because "the outcome does not depend on income" IS the rule each
one exists to test. The value of those cases is in the rationale, not the label.

One case type is near-constant and is NOT constant by design:
`boarder_income_proration` is 98.9% eligible (643/650), and was 100.0% eligible
(636/636) before the wave, so this predates it and is not a regression -- it is
pinned below rather than fixed, so that whoever fixes it is told to move it into
the label-varying set. Its wages draw is anchored on `gross_limit * 0.60`, the
same anchor-on-a-test-that-does-not-bind mistake, and closing it is the single
biggest remaining reduction in the case-type shortcut floor.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest
from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator

FISCAL_YEAR = 2026
SEEDS = 100

# One jurisdiction per shape of the FY2026 BBCE table: the top of the raised band, a
# mid-band adopter with its own benefit tables, an adopter that keeps a dollar asset
# cap, and a non-adopter on the plain federal rules. A label collapse that only
# happens in raised-BBCE jurisdictions is exactly what shipped, so a single
# "representative" state is not enough.
BAND_REPRESENTATIVES = ("MD", "VI", "TX", "MO")

# Builders whose label MUST vary, with the outcome-share ceiling each has to stay
# under in every jurisdiction above. 0.85 leaves room for sampling noise at
# SEEDS=100 around the ~55-70% majorities these builders actually produce, while
# still failing loudly on the 100%/0% collapse this file exists to prevent.
MUST_VARY = (
    "_build_homeless_case",
    "_build_migrant_case",
    "_build_mixed_immigration_case",
)
MAX_MAJORITY_SHARE = 0.85

# Constant by design: the constancy IS the rule under test.
CONSTANT_BY_DESIGN = {
    "_build_student_case": "ineligible",
    "_build_categorical_eligibility_case": "eligible",
}

# Known near-constant, pre-dating the fix wave. Pinned, not blessed -- see the
# module docstring.
NEAR_CONSTANT_TODO = {"_build_boarder_case": "eligible"}


def outcomes(state: str, builder_name: str, seeds: int = SEEDS) -> Counter:
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    builder = dict(generator._special_population_builders())[builder_name]
    return Counter(builder(random.Random(seed)).expected_outcome for seed in range(seeds))


def test_every_special_population_builder_is_classified() -> None:
    """No builder may be added without deciding whether its label is allowed to be constant.

    The whole failure mode here is a case type nobody looked at. A new seventh (or
    eighth) builder that lands outside all three sets below is unclassified, and
    unclassified means unchecked.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state="MD")
    all_names = {name for name, _fn in generator._special_population_builders()}
    classified = set(MUST_VARY) | set(CONSTANT_BY_DESIGN) | set(NEAR_CONSTANT_TODO)
    # The BBCE expanded-income case has its own both-outcomes-reachable test in
    # test_snap_edge_cases.py, which asserts the stronger property that each label
    # comes from the specific mechanism it is supposed to.
    classified.add("_build_bbce_expanded_income_case")
    assert all_names == classified, (
        "unclassified special-population builder(s): "
        f"{sorted(all_names - classified)}; removed: {sorted(classified - all_names)}"
    )


@pytest.mark.parametrize("state", BAND_REPRESENTATIVES)
@pytest.mark.parametrize("builder_name", MUST_VARY)
def test_label_varying_case_types_never_collapse_to_one_label(state: str, builder_name: str) -> None:
    counts = outcomes(state, builder_name)
    total = sum(counts.values())
    majority_label, majority_count = counts.most_common(1)[0]
    share = majority_count / total
    assert len(counts) == 2, (
        f"{state} {builder_name}: every one of {total} draws came out {majority_label!r}. "
        "The scenario prose names this case type in plain English, so a constant label makes "
        "the case answerable from its name with no reasoning at all."
    )
    assert share <= MAX_MAJORITY_SHARE, (
        f"{state} {builder_name}: {share:.1%} of {total} draws are {majority_label!r} "
        f"(limit {MAX_MAJORITY_SHARE:.0%}). Check what the income draw is anchored on -- "
        "anchoring on a test that does not bind in this jurisdiction is what collapsed "
        "migrant and mixed-immigration cases to a single label in 28 jurisdictions."
    )


@pytest.mark.parametrize("state", BAND_REPRESENTATIVES)
def test_constant_by_design_case_types_are_still_constant(state: str) -> None:
    """The two exemptions are exemptions for a stated reason, and must keep earning it."""
    for builder_name, expected in CONSTANT_BY_DESIGN.items():
        counts = outcomes(state, builder_name, seeds=20)
        assert set(counts) == {expected}, (
            f"{state} {builder_name}: expected every case to be {expected!r} (that is the rule "
            f"the case type demonstrates), got {dict(counts)}"
        )


@pytest.mark.parametrize("state", BAND_REPRESENTATIVES)
def test_the_known_near_constant_case_type_stays_pinned(state: str) -> None:
    """`boarder_income_proration` is a KNOWN shortcut, measured at 98.9% eligible.

    Pinned rather than exempted: if someone anchors its wages draw on the binding
    test the way `_build_migrant_case` now is, this test fails and says to move the
    builder into `MUST_VARY`. Pre-dates the fix wave (100.0% eligible before it), so
    it is a standing defect, not a regression -- but it is the largest single
    contributor to the case-type shortcut floor on the adversarial slice.
    """
    for builder_name, expected in NEAR_CONSTANT_TODO.items():
        counts = outcomes(state, builder_name)
        share = counts[expected] / sum(counts.values())
        assert share >= 0.90, (
            f"{state} {builder_name}: {expected!r} share is now {share:.1%}, below the pinned "
            "90%. If the income draw was re-anchored on the binding test, this is an "
            f"IMPROVEMENT -- move {builder_name!r} from NEAR_CONSTANT_TODO into MUST_VARY and "
            "update the numbers in this module's docstring."
        )


@pytest.mark.parametrize("state", BAND_REPRESENTATIVES)
def test_the_binding_ceiling_is_the_point_where_the_source_flips(state: str) -> None:
    """`_binding_gross_ceiling` must return an actual boundary of `is_eligible`.

    Characterized against the source rather than against a reimplemented formula:
    a household at the ceiling passes, a household a dollar above it fails, and the
    ceiling never exceeds the governing gross limit. In a jurisdiction where the
    gross test binds first the ceiling IS that limit, which is why this fix is a
    no-op for the 14 jurisdictions at 130% FPL.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    source = generator.bbce_source
    for household_size in (1, 2, 3, 4):
        ceiling = generator._binding_gross_ceiling(
            household_size,
            lambda gross, size=household_size: source.calculate_net_income(
                gross_income=gross, household_size=size, earned_income=gross
            ),
        )
        limit = generator._gross_limit(household_size)
        assert 0.0 < ceiling <= limit

        def eligible_at(gross: float, size: int = household_size) -> bool:
            verdict, _reason = source.is_eligible(
                household_size=size,
                gross_income=gross,
                net_income=source.calculate_net_income(gross_income=gross, household_size=size, earned_income=gross),
            )
            return verdict

        assert eligible_at(ceiling), f"{state} hh{household_size}: ${ceiling:,.2f} is not eligible"
        if ceiling < limit:
            assert not eligible_at(ceiling + 1.0), (
                f"{state} hh{household_size}: ${ceiling + 1:,.2f} is still eligible, so "
                f"${ceiling:,.2f} is not the boundary"
            )
        else:
            # The gross test binds first: one dollar over the limit must fail on it.
            assert not eligible_at(limit + 1.0)


def test_the_ceiling_is_below_the_gross_limit_exactly_where_the_net_test_binds() -> None:
    """The mechanism, stated as a test: at 200% FPL the net test is what decides.

    Without this, `_binding_gross_ceiling` could quietly degrade to `return
    self._gross_limit(...)` -- reintroducing the collapse -- and every assertion
    above about boundaries would still hold.
    """
    raised = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state="MD")  # 200% FPL
    flat = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state="MO")  # non-BBCE, 130% FPL

    for generator, expect_below in ((raised, True), (flat, False)):
        source = generator.bbce_source
        for household_size in (2, 3, 4):
            ceiling = generator._binding_gross_ceiling(
                household_size,
                lambda gross, size=household_size, src=source: src.calculate_net_income(
                    gross_income=gross, household_size=size, earned_income=gross
                ),
            )
            limit = generator._gross_limit(household_size)
            if expect_below:
                assert ceiling < limit * 0.95, (
                    f"{generator.state} hh{household_size}: ceiling ${ceiling:,.2f} is not meaningfully "
                    f"below the ${limit:,.2f} gross limit, so income is still being anchored on a test "
                    "that does not bind here"
                )
            else:
                assert ceiling == limit, (
                    f"{generator.state} hh{household_size}: the gross test binds at 130% FPL, so the "
                    f"ceiling should be the gross limit ${limit:,.2f}, got ${ceiling:,.2f}"
                )
