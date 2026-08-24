"""Invariants on `USHouseholdProfile` itself, independent of any generator."""

import pytest
from govsynth.profiles.us_household import USHouseholdProfile


def test_age_60_plus_cannot_be_labelled_non_elderly() -> None:
    """A head aged 60+ IS an elderly member (7 CFR 271.2) -- the flag cannot say otherwise.

    Regression guard. Before this invariant, 14% of SNAP threshold profiles
    rendered prose stating an age of 60+ ("... is a 63-year-old single parent
    ...") while carrying has_elderly_or_disabled=False, so the expected
    reasoning applied the gross income test that 7 CFR 273.9(a)(2) waives for
    such households. A model fine-tuned on that corpus read the stated age,
    correctly inferred elderly status, and was scored against a label that
    contradicted the prompt.
    """
    with pytest.raises(ValueError, match="7 CFR 271.2"):
        USHouseholdProfile(
            household_size=2,
            monthly_gross_income=1000.0,
            state="WY",
            age_of_head=63,
            has_elderly_or_disabled=False,
        )

    # The invariant is one-directional: a head under 60 may still carry the
    # flag (a disabled member, or an elderly member who is not the head).
    USHouseholdProfile(
        household_size=2,
        monthly_gross_income=1000.0,
        state="WY",
        age_of_head=40,
        has_elderly_or_disabled=True,
    )


def test_no_sampling_path_produces_the_contradiction() -> None:
    """The invariant must hold for every sampled profile, not just hand-built ones.

    `at_threshold` is the path the generators actually use, so a sampling site
    that sets age and the elderly flag independently would raise here rather
    than surfacing later as a mislabelled case.
    """
    for threshold in ("gross_income_limit", "net_income_limit", "asset_limit_general"):
        for seed in range(60):
            profile = USHouseholdProfile.at_threshold(
                program="snap",
                threshold=threshold,
                state="WY",
                household_size=3,
                fiscal_year=2026,
                offset_pct=0.05,
                seed=seed,
            )
            assert not (profile.age_of_head >= 60 and not profile.has_elderly_or_disabled)
