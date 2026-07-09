"""Unit tests for the Medicaid data source connector.

VA is a bundled expansion state; TX is a bundled non-expansion state
(data/thresholds/medicaid_cy2026.json), giving a real expansion/coverage-gap
contrast to test against.
"""

import pytest
from govsynth.sources.us.medicaid import MedicaidSource

_FPL_1_MONTHLY_2026 = 1330.0


@pytest.fixture
def va_source() -> MedicaidSource:
    return MedicaidSource(calendar_year=2026, state="VA")


@pytest.fixture
def tx_source() -> MedicaidSource:
    return MedicaidSource(calendar_year=2026, state="TX")


class TestExpansionStatus:
    def test_va_is_expansion_state(self, va_source: MedicaidSource) -> None:
        assert va_source.is_expansion_state() is True

    def test_tx_is_not_expansion_state(self, tx_source: MedicaidSource) -> None:
        assert tx_source.is_expansion_state() is False


class TestFetchThresholds:
    def test_program_is_medicaid(self, va_source: MedicaidSource) -> None:
        assert va_source.thresholds().program == "medicaid"

    def test_no_asset_test(self, va_source: MedicaidSource) -> None:
        t = va_source.thresholds()
        assert t.extra is not None
        assert t.extra["income_methodology"] == "MAGI"

    def test_expansion_state_household_1_limit_is_138_pct_fpl(self, va_source: MedicaidSource) -> None:
        limit = va_source.thresholds().by_household_size(1)
        assert limit.gross_monthly == pytest.approx(_FPL_1_MONTHLY_2026 * 1.38)

    def test_non_expansion_state_uses_parent_caretaker_pct(self, tx_source: MedicaidSource) -> None:
        # TX parents_caretaker_relative = 15% FPL per the bundled data.
        limit = tx_source.thresholds().by_household_size(1)
        assert limit.gross_monthly == pytest.approx(_FPL_1_MONTHLY_2026 * 0.15)


class TestGetIncomeLimit:
    def test_adult_expansion_state_is_138_pct_fpl(self, va_source: MedicaidSource) -> None:
        limit = va_source.get_income_limit("adult")
        assert limit == pytest.approx(_FPL_1_MONTHLY_2026 * 1.38)

    def test_adult_non_expansion_state_has_no_limit(self, tx_source: MedicaidSource) -> None:
        assert tx_source.get_income_limit("adult") is None

    def test_pregnant_non_expansion_uses_state_pct(self, tx_source: MedicaidSource) -> None:
        # TX pregnant_women_pct_fpl = 198% per the bundled data.
        limit = tx_source.get_income_limit("pregnant")
        assert limit == pytest.approx(_FPL_1_MONTHLY_2026 * 1.98)

    def test_pregnant_expansion_state_uses_200_pct(self, va_source: MedicaidSource) -> None:
        limit = va_source.get_income_limit("pregnant")
        assert limit == pytest.approx(_FPL_1_MONTHLY_2026 * 2.00)

    def test_parent_caretaker_expansion_state_is_138_pct(self, va_source: MedicaidSource) -> None:
        limit = va_source.get_income_limit("parent_caretaker")
        assert limit == pytest.approx(_FPL_1_MONTHLY_2026 * 1.38)

    def test_parent_caretaker_non_expansion_state_uses_state_pct(self, tx_source: MedicaidSource) -> None:
        # TX parents_caretaker_relative = 15% per the bundled data.
        limit = tx_source.get_income_limit("parent_caretaker")
        assert limit == pytest.approx(_FPL_1_MONTHLY_2026 * 0.15)

    def test_unknown_applicant_type_returns_none(self, va_source: MedicaidSource) -> None:
        assert va_source.get_income_limit("not_a_real_type") is None


class TestIsEligible:
    def test_expansion_state_adult_under_limit_is_eligible(self, va_source: MedicaidSource) -> None:
        eligible, reason = va_source.is_eligible(1, 1000, "adult")
        assert eligible
        assert "Eligible" in reason

    def test_expansion_state_adult_over_limit_is_ineligible(self, va_source: MedicaidSource) -> None:
        eligible, reason = va_source.is_eligible(1, 5000, "adult")
        assert not eligible

    def test_non_expansion_state_adult_is_always_ineligible_coverage_gap(self, tx_source: MedicaidSource) -> None:
        eligible, reason = tx_source.is_eligible(1, 500, "adult")
        assert not eligible
        assert "coverage gap" in reason.lower()

    def test_non_expansion_state_pregnant_under_limit_is_eligible(self, tx_source: MedicaidSource) -> None:
        eligible, _ = tx_source.is_eligible(1, 1000, "pregnant")
        assert eligible


class TestFetchPolicySummary:
    def test_expansion_state_summary_mentions_138_pct(self, va_source: MedicaidSource) -> None:
        summary = va_source.fetch_policy_summary()
        assert "138%" in summary
        assert "expansion state" in summary

    def test_non_expansion_state_summary_mentions_coverage_gap(self, tx_source: MedicaidSource) -> None:
        summary = tx_source.fetch_policy_summary()
        assert "coverage gap" in summary
        assert "non-expansion state" in summary
