"""Unit tests for the SNAP data source connector.

All dollar values come directly from the verified
USDA FNS SNAP COLA FY2026 memo (August 13, 2025).

NOTE: SNAPSource models only the FEDERAL baseline (130% FPL gross, 100% FPL net,
      $3,000/$4,500 asset limits) regardless of state. Broad-based categorical
      eligibility (raised gross limit, waived/capped assets) is modeled separately
      by SNAPBBCESource — see test_snap_bbce_source.py.
"""

import json

import pytest
from govsynth.sources.base import THRESHOLD_DIR
from govsynth.sources.us.snap import SNAPSource, get_standard_deduction


@pytest.fixture
def va_source() -> SNAPSource:
    return SNAPSource(fiscal_year=2026, state="VA")


@pytest.fixture
def tx_source() -> SNAPSource:
    return SNAPSource(fiscal_year=2026, state="TX")


@pytest.fixture
def gu_source() -> SNAPSource:
    return SNAPSource(fiscal_year=2026, state="GU")


@pytest.fixture
def vi_source() -> SNAPSource:
    return SNAPSource(fiscal_year=2026, state="VI")


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

    def test_asset_limit_federal_baseline_all_states(self, va_source: SNAPSource, tx_source: SNAPSource) -> None:
        """SNAPSource applies the federal $3,000 general asset limit regardless of state.

        BBCE waivers/caps are layered on by SNAPBBCESource, not SNAPSource.
        """
        assert va_source.thresholds().asset_limit_general == 3000
        assert tx_source.thresholds().asset_limit_general == 3000

    def test_no_bbce_flags_in_federal_source(self, va_source: SNAPSource) -> None:
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


class TestGuamAndVirginIslandsMaxAllotments:
    """Pins every cell of the FY2026 COLA memo's Table 1 (p.4) for GU and VI.

    None of the pre-existing tests in this file instantiate a GU or VI
    source (they use VA/TX fixtures only), so before this class a transposed
    digit or a fat-fingered dollar amount in `households_guam` /
    `households_virgin_islands` would leave every test in this repo green
    while rendering the wrong number into every GU/VI training prompt and
    into the scored label via `estimate_monthly_benefit`.
    """

    def test_gu_max_allotments(self, gu_source: SNAPSource) -> None:
        expected = {1: 439, 2: 806, 3: 1157, 4: 1465, 5: 1743, 6: 2095, 7: 2315, 8: 2637}
        for size, expected_benefit in expected.items():
            assert gu_source.thresholds().by_household_size(size).max_benefit == expected_benefit

    def test_vi_max_allotments(self, vi_source: SNAPSource) -> None:
        expected = {1: 383, 2: 703, 3: 1009, 4: 1278, 5: 1521, 6: 1827, 7: 2019, 8: 2300}
        for size, expected_benefit in expected.items():
            assert vi_source.thresholds().by_household_size(size).max_benefit == expected_benefit

    def test_gu_each_additional(self) -> None:
        # "each_additional" is transcribed into the JSON per the memo's table but
        # is not wired into ProgramThresholds.by_household_size (a pre-existing gap
        # shared by every region, out of this task's scope) -- read it directly.
        raw = json.loads((THRESHOLD_DIR / "snap_fy2026.json").read_text())
        assert raw["households_guam"]["each_additional"]["max_benefit"] == 322

    def test_vi_each_additional(self) -> None:
        raw = json.loads((THRESHOLD_DIR / "snap_fy2026.json").read_text())
        assert raw["households_virgin_islands"]["each_additional"]["max_benefit"] == 281

    def test_gu_and_vi_gross_net_equal_48_states(self, gu_source: SNAPSource, vi_source: SNAPSource) -> None:
        # The memo prints one shared "48 States, D.C., Guam, Virgin Islands" income
        # column -- GU/VI must never pick up a territory-specific gross/net figure.
        base = SNAPSource(fiscal_year=2026, state="national").thresholds()
        for size in range(1, 9):
            b = base.by_household_size(size)
            for source in (gu_source, vi_source):
                hh = source.thresholds().by_household_size(size)
                assert hh.gross_monthly == b.gross_monthly
                assert hh.net_monthly == b.net_monthly


class TestStandardDeductionsAllRegions:
    """Pins every cell of the memo's Table 2 (p.6) for all five regions.

    `TestStandardDeductions` above only ever calls `get_standard_deduction`
    with no region argument, so it covers `48_states_dc` alone. This class
    covers alaska/hawaii/guam/virgin_islands too, including the one cell
    (VI, size 3) that genuinely differs from its own size-1-2 value.
    """

    @pytest.mark.parametrize(
        "region,expected",
        [
            ("48_states_dc", {1: 209, 2: 209, 3: 209, 4: 223, 5: 261, 6: 299, 7: 299, 8: 299}),
            ("alaska", {1: 358, 2: 358, 3: 358, 4: 358, 5: 358, 6: 374, 7: 374, 8: 374}),
            ("hawaii", {1: 295, 2: 295, 3: 295, 4: 295, 5: 300, 6: 344, 7: 344, 8: 344}),
            ("guam", {1: 420, 2: 420, 3: 420, 4: 445, 5: 522, 6: 598, 7: 598, 8: 598}),
            ("virgin_islands", {1: 184, 2: 184, 3: 185, 4: 223, 5: 261, 6: 299, 7: 299, 8: 299}),
        ],
    )
    def test_standard_deduction_by_region(self, region: str, expected: dict[int, int]) -> None:
        for size, expected_value in expected.items():
            assert get_standard_deduction(size, region) == expected_value

    def test_vi_size_3_differs_from_size_1_and_2(self) -> None:
        # The one cell in the whole table where a jurisdiction's size-3 value
        # diverges from its size-1-2 value ($184 vs $185) -- easy to transpose,
        # easy for a naive "1-2, 3-5, 6+" grouping refactor to silently collapse.
        assert get_standard_deduction(1, "virgin_islands") == 184
        assert get_standard_deduction(2, "virgin_islands") == 184
        assert get_standard_deduction(3, "virgin_islands") == 185


class TestStandardDeductionUnknownRegionRaises:
    def test_unknown_region_raises(self) -> None:
        # Before this task, an unrecognized region silently fell back to the
        # 48-states-and-DC table (`tables.get(region, tables["48_states_dc"])`)
        # -- exactly the bug that made GU/VI silently wrong prior to this
        # fiscal year's data addition. A future region added to
        # `_region_for_state` without a matching table entry here must fail
        # loudly instead of quietly regressing the same way.
        with pytest.raises(ValueError, match="unrecognized region"):
            get_standard_deduction(1, "puerto_rico")


class TestStandardDeductionJsonPythonAgreement:
    """Cross-checks the JSON's `standard_deductions_*` tables against the
    hardcoded Python tables in `get_standard_deduction`.

    `SNAPSource.fetch_thresholds` builds `standard_deductions` entirely from
    `get_standard_deduction`'s own hardcoded dicts -- nothing in the runtime
    path reads the JSON's `standard_deductions_*` keys (the only reader is a
    48-states-only display line in `scripts/verify_thresholds.py`). That JSON
    is the artifact carrying `_metadata.verification_status` and the memo
    citation, so a human auditing provenance signs off on numbers the model
    never actually sees unless something pins the two together.
    """

    @pytest.mark.parametrize("region", ["48_states_dc", "alaska", "hawaii", "guam", "virgin_islands"])
    def test_json_matches_python_for_every_size(self, region: str) -> None:
        raw = json.loads((THRESHOLD_DIR / "snap_fy2026.json").read_text())
        table = raw[f"standard_deductions_{region}"]
        for size in range(1, 9):
            key = str(size) if size < 6 else "6+"
            assert table[key] == get_standard_deduction(size, region), (
                f"{region} size {size}: JSON has {table[key]}, "
                f"get_standard_deduction returns {get_standard_deduction(size, region)}"
            )


class TestExcessShelterCapsAllRegions:
    @pytest.mark.parametrize(
        "state,expected_cap",
        [("KS", 744), ("AK", 1189), ("HI", 1003), ("GU", 873), ("VI", 586)],
    )
    def test_shelter_cap_by_region(self, state: str, expected_cap: float) -> None:
        source = SNAPSource(fiscal_year=2026, state=state)
        assert source.thresholds().extra["excess_shelter_cap"] == expected_cap


class TestMinimumBenefitsAllRegions:
    """Pins the memo's Table (p.5) minimum-benefit-by-region fix.

    Before this task, `fetch_thresholds` hardcoded
    `raw.get("minimum_benefit_48_states_dc", 24)` for every region, so Alaska
    and Hawaii (and, once added, Guam and the Virgin Islands) all silently
    floored 1-2 person households at the 48-states minimum instead of their
    own. `test_snap_bbce_source.py::TestMinimumBenefitFloor` only ever
    exercises VA (48_states_dc), so it could not catch this.
    """

    @pytest.mark.parametrize(
        "state,expected_minimum",
        [("KS", 24), ("AK", 31), ("HI", 41), ("GU", 35), ("VI", 31)],
    )
    def test_minimum_benefit_by_region(self, state: str, expected_minimum: float) -> None:
        source = SNAPSource(fiscal_year=2026, state=state)
        assert source.thresholds().extra["minimum_benefit"] == expected_minimum


class TestNetIncomeCalculation:
    def test_basic_all_earned(self, va_source: SNAPSource) -> None:
        # $2,000 gross, all earned, HH3
        # 20% ded = $400 → $1,600; std ded $209 → $1,391
        net = va_source.calculate_net_income(gross_income=2000, household_size=3, earned_income=2000)
        assert net == pytest.approx(1391.0, rel=0.01)

    def test_net_income_zero_floor(self, va_source: SNAPSource) -> None:
        net = va_source.calculate_net_income(gross_income=100, household_size=3, shelter_costs=5000)
        assert net >= 0.0

    def test_shelter_cap_at_744(self, va_source: SNAPSource) -> None:
        # Generate two cases where excess shelter differs but both exceed cap
        net_a = va_source.calculate_net_income(gross_income=2000, household_size=3, shelter_costs=3000)
        net_b = va_source.calculate_net_income(gross_income=2000, household_size=3, shelter_costs=2500)
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
