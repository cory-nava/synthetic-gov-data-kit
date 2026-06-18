"""Unit tests for the SNAP data source connector.

All dollar values come directly from the verified
USDA FNS SNAP COLA FY2026 memo (August 13, 2025).

NOTE: SNAPSource models only the FEDERAL baseline (130% FPL gross, 100% FPL net,
      $3,000/$4,500 asset limits) regardless of state. Broad-based categorical
      eligibility (raised gross limit, waived/capped assets) is modeled separately
      by SNAPBBCESource — see test_snap_bbce_source.py.
"""

import pytest
from govsynth.sources.us.snap import SNAPSource, get_standard_deduction


@pytest.fixture
def va_source() -> SNAPSource:
    return SNAPSource(fiscal_year=2026, state="VA")


@pytest.fixture
def tx_source() -> SNAPSource:
    return SNAPSource(fiscal_year=2026, state="TX")


class TestSNAPVerifiedThresholds:
    """All values from official USDA FNS SNAP COLA FY2026 memo."""

    def test_gross_limits(self, va_source: SNAPSource) -> None:
        expected = {1: 1696, 2: 2292, 3: 2888, 4: 3483, 5: 4079, 6: 4675, 7: 5271, 8: 5867}
        for size, expected_gross in expected.items():
            assert va_source.thresholds().by_household_size(size).gross_monthly == expected_gross

    def test_net_limits(self, va_source: SNAPSource) -> None:
        expected = {1: 1305, 2: 1763, 3: 2221, 4: 2680, 5: 3138, 6: 3596, 7: 4055, 8: 4513}
        for size, expected_net in expected.items():
            assert va_source.thresholds().by_household_size(size).net_monthly == expected_net

    def test_max_benefits(self, va_source: SNAPSource) -> None:
        expected = {1: 298, 2: 546, 3: 785, 4: 994, 5: 1183, 6: 1421, 7: 1571, 8: 1789}
        for size, expected_benefit in expected.items():
            assert va_source.thresholds().by_household_size(size).max_benefit == expected_benefit

    def test_asset_limit_federal_baseline_all_states(
        self, va_source: SNAPSource, tx_source: SNAPSource
    ) -> None:
        """SNAPSource applies the federal $3,000 general asset limit regardless of state.

        BBCE waivers/caps are layered on by SNAPBBCESource, not SNAPSource.
        """
        assert va_source.thresholds().asset_limit_general == 3000
        assert tx_source.thresholds().asset_limit_general == 3000

    def test_no_bbce_flags_in_federal_source(self, va_source):
        """SNAPSource no longer carries BBCE classification in extra."""
        extra = va_source.thresholds().extra
        assert "bbce_state" not in extra
        assert "strict_asset_test" not in extra

    def test_asset_limit_elderly_disabled(self, va_source: SNAPSource) -> None:
        assert va_source.thresholds().asset_limit_elderly_disabled == 4500

    def test_excess_shelter_cap(self, va_source: SNAPSource) -> None:
        assert va_source.thresholds().extra["excess_shelter_cap"] == 744

    def test_homeless_shelter_deduction(self, va_source: SNAPSource) -> None:
        assert va_source.thresholds().extra["homeless_shelter_deduction"] == pytest.approx(198.99)

    def test_verification_status_is_verified(self, va_source: SNAPSource) -> None:
        assert va_source.thresholds().extra["verification_status"] == "verified"


class TestStandardDeductions:
    def test_hh1_to_3(self) -> None:
        for size in [1, 2, 3]:
            assert get_standard_deduction(size) == 209

    def test_hh4(self) -> None:
        assert get_standard_deduction(4) == 223

    def test_hh5(self) -> None:
        assert get_standard_deduction(5) == 261

    def test_hh6_plus(self) -> None:
        for size in [6, 7, 8, 10]:
            assert get_standard_deduction(size) == 299


class TestNetIncomeCalculation:
    def test_basic_all_earned(self, va_source: SNAPSource) -> None:
        # $2,000 gross, all earned, HH3
        # 20% ded = $400 → $1,600; std ded $209 → $1,391
        net = va_source.calculate_net_income(
            gross_income=2000, household_size=3, earned_income=2000
        )
        assert net == pytest.approx(1391.0, rel=0.01)

    def test_net_income_zero_floor(self, va_source: SNAPSource) -> None:
        net = va_source.calculate_net_income(gross_income=100, household_size=3, shelter_costs=5000)
        assert net >= 0.0

    def test_shelter_cap_at_744(self, va_source: SNAPSource) -> None:
        # Generate two cases where excess shelter differs but both exceed cap
        net_a = va_source.calculate_net_income(
            gross_income=2000, household_size=3, shelter_costs=3000
        )
        net_b = va_source.calculate_net_income(
            gross_income=2000, household_size=3, shelter_costs=2500
        )
        # Both shelter amounts exceed cap, so net should be the same
        assert net_a == net_b

    def test_no_shelter_cap_for_elderly(self, va_source: SNAPSource) -> None:
        net_regular = va_source.calculate_net_income(gross_income=2000, household_size=3, shelter_costs=3000)
        net_elderly = va_source.calculate_net_income(
            gross_income=2000, household_size=3, shelter_costs=3000, has_elderly_or_disabled=True
        )
        # Elderly gets more deduction (no cap)
        assert net_elderly < net_regular


class TestEligibilityDetermination:
    def test_clearly_eligible(self, va_source: SNAPSource) -> None:
        eligible, _ = va_source.is_eligible(household_size=3, gross_income=1500, net_income=1000, liquid_assets=500)
        assert eligible

    def test_at_gross_limit_is_eligible(self, va_source: SNAPSource) -> None:
        eligible, _ = va_source.is_eligible(household_size=3, gross_income=2888, net_income=1800, liquid_assets=500)
        assert eligible

    def test_above_gross_limit_is_ineligible(self, va_source: SNAPSource) -> None:
        eligible, reason = va_source.is_eligible(household_size=3, gross_income=2889, net_income=1800)
        assert not eligible
        assert "gross income" in reason.lower()

    def test_elderly_exempt_from_gross(self, va_source: SNAPSource) -> None:
        eligible, _ = va_source.is_eligible(
            household_size=2,
            gross_income=9999,
            net_income=1500,
            liquid_assets=1000,
            has_elderly_or_disabled=True,
        )
        assert eligible

    def test_fails_asset_limit_strict_state(self, tx_source: SNAPSource) -> None:
        eligible, reason = tx_source.is_eligible(
            household_size=3, gross_income=1500, net_income=1100, liquid_assets=3001
        )
        assert not eligible
        assert "asset" in reason.lower()

    def test_categorical_eligibility_overrides_all(self, va_source: SNAPSource) -> None:
        eligible, reason = va_source.is_eligible(household_size=1, gross_income=99999, is_categorically_eligible=True)
        assert eligible
        assert "categorically" in reason.lower()


class TestFiscalYearConfig:
    def test_fy2026_period_label(self) -> None:
        source = SNAPSource(fiscal_year=2026, state="VA")
        assert source.fy_config.period_label == "FY2026"

    def test_threshold_filename(self) -> None:
        source = SNAPSource(fiscal_year=2026, state="VA")
        assert source.fy_config.threshold_filename == "snap_fy2026.json"

    def test_fpl_basis_year_is_2025(self) -> None:
        """FY2026 SNAP uses 2025 HHS poverty guidelines."""
        source = SNAPSource(fiscal_year=2026, state="VA")
        assert source.fy_config.fpl_year == 2025
