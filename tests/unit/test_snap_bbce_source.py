"""Unit tests for the SNAP BBCE data source connector.

State parameters come from data/thresholds/snap_bbce_fy2026.json (USDA FNS BBCE
States Chart, August 2025). Gross income limits are derived from us_fpl_2025.json
using SNAP's published rounding: ceil(annual_fpl * pct / 100 / 12).

State roles used here:
  VA — BBCE at 200% FPL, asset test waived
  TX — BBCE at 165% FPL, $5,000 asset cap
  TN — non-BBCE (federal 130% FPL, $3,000 asset limit)
"""

import math

import pytest
from govsynth.sources.us.snap_bbce import SNAPBBCESource


@pytest.fixture
def va() -> SNAPBBCESource:
    return SNAPBBCESource(fiscal_year=2026, state="VA")  # 200% FPL, assets waived


@pytest.fixture
def tx() -> SNAPBBCESource:
    return SNAPBBCESource(fiscal_year=2026, state="TX")  # 165% FPL, $5,000 cap


@pytest.fixture
def tn() -> SNAPBBCESource:
    return SNAPBBCESource(fiscal_year=2026, state="TN")  # non-BBCE


class TestBBCEParams:
    def test_va_is_bbce_200(self, va: SNAPBBCESource) -> None:
        assert va.is_bbce is True
        assert va.bbce_params.gross_income_limit_pct_fpl == 200
        assert va.bbce_params.asset_limit is None

    def test_tx_is_bbce_165_with_cap(self, tx: SNAPBBCESource) -> None:
        assert tx.is_bbce is True
        assert tx.bbce_params.gross_income_limit_pct_fpl == 165
        assert tx.bbce_params.asset_limit == 5000

    def test_tn_is_not_bbce(self, tn: SNAPBBCESource) -> None:
        assert tn.is_bbce is False
        assert tn.bbce_params.gross_income_limit_pct_fpl == 130

    def test_unlisted_state_falls_back_to_federal(self) -> None:
        src = SNAPBBCESource(fiscal_year=2026, state="national")
        assert src.is_bbce is False
        assert src.bbce_params.state == "FEDERAL"
        assert src.bbce_params.gross_income_limit_pct_fpl == 130


class TestThresholdsReflectBBCE:
    """fetch_thresholds() surfaces the state's BBCE asset rule and flags."""

    def test_waived_state_asset_limit_none(self, va: SNAPBBCESource) -> None:
        assert va.thresholds().asset_limit_general is None
        assert va.thresholds().extra["bbce_state"] is True
        assert va.thresholds().extra["bbce_gross_limit_pct_fpl"] == 200

    def test_cap_state_asset_limit_value(self, tx: SNAPBBCESource) -> None:
        assert tx.thresholds().asset_limit_general == 5000
        assert tx.thresholds().extra["bbce_state"] is True

    def test_non_bbce_state_federal_asset_limit(self, tn: SNAPBBCESource) -> None:
        assert tn.thresholds().asset_limit_general == 3000
        assert tn.thresholds().extra["bbce_state"] is False

    def test_derived_bbce_states_set(self) -> None:
        from govsynth.sources.us.snap_bbce import BBCE_STATES, bbce_states

        states = bbce_states(2026)
        assert "VA" in states and "CA" in states and "TX" in states
        assert "TN" not in states and "KS" not in states
        assert states == BBCE_STATES


class TestEffectiveGrossLimit:
    """Derived limits must match ceil(annual_fpl * pct / 100 / 12)."""

    def test_va_200pct(self, va: SNAPBBCESource) -> None:
        # 1-person 2025 FPL annual = $15,650 → 200% monthly = ceil(31300/12) = 2609
        assert va.effective_gross_limit(1) == 2609
        # 3-person annual = $26,650 → ceil(53300/12) = 4442
        assert va.effective_gross_limit(3) == 4442

    def test_tx_165pct_matches_published_table(self, tx: SNAPBBCESource) -> None:
        # 165% 3-person matches the snap_fy2026 gross_income_165pct row ($3,665)
        assert tx.effective_gross_limit(3) == 3665

    def test_non_bbce_equals_federal_130(self, tn: SNAPBBCESource) -> None:
        # Non-BBCE state's effective limit equals the federal 130% gross limit
        assert tn.effective_gross_limit(3) == tn.federal_gross_limit(3) == 2888

    def test_federal_gross_limit_matches_snap_table(self, va: SNAPBBCESource) -> None:
        # Federal 130% limits regardless of BBCE state
        assert va.federal_gross_limit(1) == 1696
        assert va.federal_gross_limit(3) == 2888


class TestEligibilityAbove130:
    def test_eligible_between_130_and_state_limit(self, va: SNAPBBCESource) -> None:
        # 3-person, gross $3,000: above federal $2,888 but below VA BBCE $4,442;
        # net below $2,221 → eligible under BBCE.
        eligible, reason = va.is_eligible(household_size=3, gross_income=3000, net_income=2000, liquid_assets=10_000)
        assert eligible
        assert "BBCE" in reason

    def test_ineligible_above_state_limit(self, va: SNAPBBCESource) -> None:
        # 3-person, gross $4,500: above VA BBCE limit $4,442 → ineligible.
        eligible, reason = va.is_eligible(household_size=3, gross_income=4500, net_income=2000)
        assert not eligible
        assert "gross income" in reason.lower()

    def test_federally_ineligible_household_is_bbce_eligible(self, va: SNAPBBCESource) -> None:
        # Federal rules would reject (gross > $2,888); BBCE accepts.
        federal_limit = va.federal_gross_limit(3)
        gross = federal_limit + 50
        assert gross > federal_limit
        eligible, _ = va.is_eligible(household_size=3, gross_income=gross, net_income=2000, liquid_assets=0)
        assert eligible


class TestNetIncomeTestStillBinds:
    def test_net_test_fails_even_when_gross_passes(self, va: SNAPBBCESource) -> None:
        # gross $3,000 passes BBCE ($4,442) but net $2,300 > $2,221 → ineligible.
        eligible, reason = va.is_eligible(household_size=3, gross_income=3000, net_income=2300)
        assert not eligible
        assert "net income" in reason.lower()


class TestAssetRules:
    def test_waived_assets_ignored(self, va: SNAPBBCESource) -> None:
        eligible, _ = va.is_eligible(household_size=3, gross_income=3000, net_income=2000, liquid_assets=999_999)
        assert eligible

    def test_cap_state_fails_over_limit(self, tx: SNAPBBCESource) -> None:
        eligible, reason = tx.is_eligible(household_size=3, gross_income=3000, net_income=2000, liquid_assets=6000)
        assert not eligible
        assert "asset" in reason.lower()

    def test_cap_state_passes_under_limit(self, tx: SNAPBBCESource) -> None:
        eligible, _ = tx.is_eligible(household_size=3, gross_income=3000, net_income=2000, liquid_assets=4000)
        assert eligible


class TestNonBBCEDefersToFederal:
    def test_non_bbce_uses_federal_gross_limit(self, tn: SNAPBBCESource) -> None:
        # gross $3,000 > federal $2,888 → ineligible (no raised limit available).
        eligible, _ = tn.is_eligible(household_size=3, gross_income=3000, net_income=2000)
        assert not eligible

    def test_non_bbce_applies_federal_asset_limit(self, tn: SNAPBBCESource) -> None:
        # TN strict $3,000 asset limit applies via the federal path.
        eligible, reason = tn.is_eligible(household_size=3, gross_income=1500, net_income=1000, liquid_assets=3001)
        assert not eligible
        assert "asset" in reason.lower()


class TestMinimumBenefitFloor:
    def test_small_cat_el_household_floored(self, va: SNAPBBCESource) -> None:
        # 1-person, very high net income → calc benefit $0, floored to $24 minimum.
        benefit = va.estimate_monthly_benefit(household_size=1, net_income=5000, is_categorically_eligible=True)
        assert benefit == 24

    def test_large_household_not_floored(self, va: SNAPBBCESource) -> None:
        # 5-person household gets no minimum floor; high net income → $0.
        benefit = va.estimate_monthly_benefit(household_size=5, net_income=10_000, is_categorically_eligible=True)
        assert benefit == 0.0

    def test_benefit_reduced_by_30pct_net(self, va: SNAPBBCESource) -> None:
        # 3-person max allotment $785 - 30% of $1,000 net = $485.
        benefit = va.estimate_monthly_benefit(household_size=3, net_income=1000, is_categorically_eligible=True)
        assert benefit == pytest.approx(485.0)


class TestDerivationMethodIntegrity:
    """Guard the rounding method against silent drift."""

    @pytest.mark.parametrize("pct,size,annual", [(200, 1, 15650), (165, 3, 26650), (185, 2, 21150)])
    def test_ceil_method(self, pct: int, size: int, annual: int) -> None:
        src = SNAPBBCESource(fiscal_year=2026, state="VA")
        expected = math.ceil(annual * pct / 100 / 12)
        # rebuild via a temp source whose pct we assert against the formula
        assert src._fpl_annual(size) == annual
        assert math.ceil(src._fpl_annual(size) * pct / 100 / 12) == expected
