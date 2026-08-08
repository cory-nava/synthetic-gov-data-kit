"""Difficulty labelling for WIC must be honest, mirroring test_snap_difficulty.py.

WIC's classifier is not structurally identical to SNAP's: it has no
elderly/disabled branch (WIC eligibility doesn't turn on that status) and no
EDGE_CASES.md Group A builders, so Difficulty.ADVERSARIAL is not reachable
here. What it shares with SNAP is the sentinel-fabrication defect: a profile
sampled without a threshold offset (uniform/realistic strategies) has no known
distance from a limit, and categorical eligibility bypasses the income test
outright -- neither may be reported as EASY ("clearly clear of every limit").
"""

import collections

import pytest
from govsynth.generators.wic_eligibility import WICEligibilityGenerator
from govsynth.models.enums import Difficulty
from govsynth.profiles.us_household import USHouseholdProfile


@pytest.mark.parametrize("strategy", ["uniform", "realistic"])
def test_threshold_free_profiles_are_not_labelled_easy(strategy: str) -> None:
    """EASY asserts distance from a limit. Profiles sampled without an offset
    have no known distance, so the label would be a fabrication."""
    generator = WICEligibilityGenerator(fiscal_year=2026)
    cases = generator.generate(n=200, profile_strategy=strategy, seed=3)
    assert Difficulty.EASY not in {c.difficulty for c in cases}


def test_categorically_eligible_cases_are_never_easy() -> None:
    """Categorical eligibility bypasses the 185% FPL income test outright --
    the household's distance from that limit is never evaluated -- so these
    cases must not be reported as clearly clear of it."""
    generator = WICEligibilityGenerator(fiscal_year=2026)
    cases = generator.generate(n=300, profile_strategy="edge_saturated", seed=11)
    categorical = [c for c in cases if c.metadata["is_categorically_eligible"]]
    assert categorical, "fixture assumption broken: no categorically-eligible cases generated"
    assert all(c.difficulty != Difficulty.EASY for c in categorical)


def test_every_reachable_difficulty_level_is_producible() -> None:
    """The test that would have caught both bugs at once.

    Unlike SNAP, WIC has no elderly/disabled special-case branch and no
    EDGE_CASES.md Group A builders, so Difficulty.ADVERSARIAL is never
    produced -- that is a structural difference from SNAP, not a defect.
    EASY, MEDIUM, and HARD must all still be reachable.
    """
    generator = WICEligibilityGenerator(fiscal_year=2026)
    cases = generator.generate(n=300, profile_strategy="edge_saturated", seed=11)
    produced = {c.difficulty for c in cases}
    expected = {Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD}
    missing = expected - produced
    assert not missing, f"these Difficulty levels are never produced: {missing}"
    assert Difficulty.ADVERSARIAL not in produced, (
        "WIC has no path to ADVERSARIAL; its appearance means something changed structurally"
    )


def test_offset_at_one_percent_boundary_is_hard() -> None:
    generator = WICEligibilityGenerator(fiscal_year=2026)
    profile = USHouseholdProfile(
        household_size=3,
        monthly_gross_income=2000.0,
        state="VA",
        extra={"threshold_type": "income_limit_185pct_fpl", "offset_pct": 0.01},
    )
    assert generator._classify_difficulty(profile, is_cat=False) == Difficulty.HARD


def test_offset_none_is_medium_not_easy() -> None:
    """Direct unit check of the sentinel-fabrication fix: a profile with no
    offset_pct at all (extra == {}) must classify as MEDIUM, not fall through
    a stale default to EASY."""
    generator = WICEligibilityGenerator(fiscal_year=2026)
    profile = USHouseholdProfile(
        household_size=2,
        monthly_gross_income=1500.0,
        state="VA",
        extra={},
    )
    assert generator._classify_difficulty(profile, is_cat=False) == Difficulty.MEDIUM


def test_uniform_strategy_case_ids_never_claim_at_limit() -> None:
    """offset_pct is absent (extra == {}) for 'uniform'/'realistic' profiles,
    so there is no known distance from a threshold. _make_id must not default
    the missing offset to 0.0 and stamp 'at_limit' into the ID."""
    generator = WICEligibilityGenerator(fiscal_year=2026)
    cases = generator.generate(n=100, profile_strategy="uniform", seed=5)
    offending = [c.case_id for c in cases if "at_limit" in c.case_id]
    assert not offending, f"uniform-strategy case IDs fabricate 'at_limit' despite no known offset: {offending}"


def test_adversarial_cases_do_not_appear_in_generated_output() -> None:
    """Documents the structural difference from SNAP: WIC has no
    EDGE_CASES.md Group A builders, so ADVERSARIAL is never emitted."""
    generator = WICEligibilityGenerator(fiscal_year=2026)
    cases = generator.generate(n=200, profile_strategy="edge_saturated", seed=7)
    counts = collections.Counter(c.difficulty for c in cases)
    assert counts[Difficulty.ADVERSARIAL] == 0, f"unexpected adversarial cases: {dict(counts)}"
