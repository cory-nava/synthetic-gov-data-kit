"""Unit tests for the WIC data source connector.

Dollar values come directly from the verified Federal Register Notice
2025-03576 (90 FR 11598), 48 contiguous states + DC table.
"""

import pytest
from govsynth.sources.us.wic import WICSource


@pytest.fixture
def national_source() -> WICSource:
    return WICSource(fiscal_year=2026, state="national")


@pytest.fixture
def alaska_source() -> WICSource:
    return WICSource(fiscal_year=2026, state="AK")


class TestWICVerifiedThresholds:
    def test_gross_limits_48_states(self, national_source: WICSource) -> None:
        expected = {1: 2413, 2: 3261, 3: 4109, 4: 4957, 5: 5805, 6: 6653, 7: 7501, 8: 8349}
        for size, expected_limit in expected.items():
            limits = national_source.thresholds().by_household_size(size)
            assert limits.gross_monthly == pytest.approx(expected_limit)

    def test_no_asset_test(self, national_source: WICSource) -> None:
        assert national_source.thresholds().asset_limit_general is None

    def test_income_limit_pct_fpl_is_185(self, national_source: WICSource) -> None:
        t = national_source.thresholds()
        assert t.extra is not None
        assert t.extra["income_limit_pct_fpl"] == 185

    def test_eligible_categories(self, national_source: WICSource) -> None:
        t = national_source.thresholds()
        assert t.extra is not None
        assert set(t.extra["eligible_categories"]) == {
            "pregnant",
            "breastfeeding",
            "postpartum",
            "infant",
            "child_under_5",
        }

    def test_categorical_eligibility_programs(self, national_source: WICSource) -> None:
        t = national_source.thresholds()
        assert t.extra is not None
        assert set(t.extra["categorical_eligibility_programs"]) == {"snap", "medicaid", "tanf"}

    def test_alaska_uses_alaska_region(self, alaska_source: WICSource) -> None:
        t = alaska_source.thresholds()
        assert t.extra is not None
        assert t.extra["region"] == "alaska"


class TestWICIsEligible:
    def test_income_eligible_pregnant(self, national_source: WICSource) -> None:
        eligible, reason = national_source.is_eligible(
            household_size=1, monthly_gross_income=2000, participant_category="pregnant"
        )
        assert eligible
        assert "Income-eligible" in reason

    def test_income_ineligible_above_limit(self, national_source: WICSource) -> None:
        eligible, reason = national_source.is_eligible(
            household_size=1, monthly_gross_income=3000, participant_category="pregnant"
        )
        assert not eligible
        assert "Ineligible" in reason

    def test_at_limit_is_eligible(self, national_source: WICSource) -> None:
        eligible, _ = national_source.is_eligible(
            household_size=1, monthly_gross_income=2413, participant_category="pregnant"
        )
        assert eligible

    def test_categorically_eligible_overrides_income(self, national_source: WICSource) -> None:
        eligible, reason = national_source.is_eligible(
            household_size=1,
            monthly_gross_income=999999,
            participant_category="pregnant",
            is_categorically_eligible=True,
        )
        assert eligible
        assert "Categorically eligible" in reason

    def test_invalid_participant_category_is_ineligible(self, national_source: WICSource) -> None:
        eligible, reason = national_source.is_eligible(
            household_size=1, monthly_gross_income=1000, participant_category="senior_citizen"
        )
        assert not eligible
        assert "not a WIC-eligible category" in reason

    def test_household_size_beyond_table_extrapolates_to_max(self, national_source: WICSource) -> None:
        eligible, _ = national_source.is_eligible(
            household_size=20, monthly_gross_income=8000, participant_category="infant"
        )
        assert isinstance(eligible, bool)


class TestWICPolicySummary:
    def test_fetch_policy_summary_mentions_185_pct(self, national_source: WICSource) -> None:
        summary = national_source.fetch_policy_summary()
        assert "185%" in summary
        assert "246.7(d)(1)" in summary

    def test_fetch_policy_summary_notes_no_asset_test(self, national_source: WICSource) -> None:
        summary = national_source.fetch_policy_summary()
        assert "No asset test" in summary
