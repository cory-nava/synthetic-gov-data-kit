"""SNAP (Supplemental Nutrition Assistance Program) data source connector.

Data sourced from:
  - USDA FNS SNAP FY2026 COLA Memo (August 13, 2025) — VERIFIED
  - data/thresholds/snap_fy{fiscal_year}.json

FISCAL YEAR:
  SNAP runs on the federal fiscal year: October 1 – September 30.
  FY2026 = Oct 1, 2025 – Sep 30, 2026 (current as of March 2026).
  FY2026 thresholds use the 2025 HHS poverty guidelines as their basis.
"""

from __future__ import annotations

from functools import lru_cache

from govsynth.fiscal_year import DEFAULT_SNAP_FY, FiscalYearConfig
from govsynth.sources.base import DataSource, HouseholdThreshold, ProgramThresholds

# States with broad-based categorical eligibility (BBCE) — no/relaxed asset test.
# Source: USDA FNS State Options Report, FY2025 (updated periodically)
BBCE_STATES: set[str] = {
    "AK",
    "CA",
    "CO",
    "CT",
    "DC",
    "DE",
    "FL",
    "GA",
    "HI",
    "IL",
    "IN",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OR",
    "PA",
    "RI",
    "SC",
    "TN",
    "UT",
    "VT",
    "VA",
    "WA",
    "WI",
    "WY",
}

# States that maintain the strict federal asset test
STRICT_ASSET_TEST_STATES: set[str] = {
    "TX",
    "MO",
    "SD",
    "WV",
    "KS",
    "AZ",
    "MS",
    "AL",
    "AR",
    "ID",
    "OK",
}

# Alaska regions for allotment purposes
ALASKA_RURAL1 = {
    "Bethel",
    "Dillingham",
    "Nome",
    "Kodiak Island",
    "Lake and Peninsula",
    "Bristol Bay",
    "Aleutians West",
    "Aleutians East",
}


@lru_cache(maxsize=128)
def get_standard_deduction(household_size: int, region: str = "48_states_dc") -> float:
    """Return the FY2026 standard deduction for a given household size and region.

    Source: USDA FNS SNAP COLA FY2026 Memo, p.6.
    """
    tables = {
        "48_states_dc": {1: 209, 2: 209, 3: 209, 4: 223, 5: 261},
        "alaska": {1: 358, 2: 358, 3: 358, 4: 358, 5: 358},
        "hawaii": {1: 295, 2: 295, 3: 295, 4: 295, 5: 300},
    }
    six_plus = {"48_states_dc": 299, "alaska": 374, "hawaii": 344}
    t = tables.get(region, tables["48_states_dc"])
    if household_size >= 6:
        return float(six_plus.get(region, 299))
    return float(t.get(min(household_size, 5), 209))


def _region_for_state(state: str) -> str:
    if state == "AK":
        return "alaska"
    if state == "HI":
        return "hawaii"
    return "48_states_dc"


class SNAPSource(DataSource):
    """SNAP eligibility data source.

    Args:
        fiscal_year: Federal fiscal year (Oct 1–Sep 30). Default FY2026.
        state: Two-letter state code or 'national'.
    """

    def __init__(self, fiscal_year: int = DEFAULT_SNAP_FY, state: str = "national") -> None:
        super().__init__(year=fiscal_year, state=state)
        self.fy_config = FiscalYearConfig.for_program("snap", fiscal_year)
        self._region = _region_for_state(self.state)

    @property
    def program(self) -> str:
        return "snap"

    def fetch_thresholds(self) -> ProgramThresholds:
        raw = self._load_threshold_json(self.fy_config.threshold_filename)

        region_key = f"households_{self._region}"
        raw_hh = raw[region_key]

        households: dict[int, HouseholdThreshold] = {}
        for key, val in raw_hh.items():
            if key == "each_additional":
                continue
            size = int(key)
            # Alaska has per-region allotments; use urban as default
            max_b = val.get("max_benefit") or val.get("max_benefit_urban") or 0.0
            households[size] = HouseholdThreshold(
                household_size=size,
                gross_monthly=float(val["gross_monthly"]),
                net_monthly=float(val["net_monthly"]),
                max_benefit=float(max_b),
            )

        # Asset limits — may be waived for BBCE states
        if self.state != "national" and self.state in BBCE_STATES:
            asset_limit: float | None = None  # BBCE waives the asset test
        else:
            asset_limit = float(raw["asset_limit_general"])

        std_deductions = {size: get_standard_deduction(size, self._region) for size in range(1, 9)}

        shelter_key = f"excess_shelter_deduction_cap_{self._region}"
        shelter_cap = float(raw.get(shelter_key, raw.get("excess_shelter_deduction_cap_48_states_dc", 744)))

        return ProgramThresholds(
            program="snap",
            fiscal_year=raw["_metadata"]["fiscal_year"],
            state=self.state,
            source=raw["_metadata"]["source"],
            source_url=raw["_metadata"].get("source_url"),
            households=households,
            asset_limit_general=asset_limit,
            asset_limit_elderly_disabled=float(raw["asset_limit_elderly_disabled"]),
            earned_income_deduction_pct=float(raw["earned_income_deduction_pct"]),
            standard_deductions=std_deductions,
            extra={
                "bbce_state": self.state in BBCE_STATES,
                "strict_asset_test": self.state in STRICT_ASSET_TEST_STATES,
                "excess_shelter_cap": shelter_cap,
                "homeless_shelter_deduction": float(raw["homeless_shelter_deduction"]),
                "minimum_benefit": float(raw.get("minimum_benefit_48_states_dc", 24)),
                "cfr_reference": raw["_metadata"]["cfr_reference"],
                "region": self._region,
                "verification_status": raw["_metadata"]["verification_status"],
            },
        )

    def fetch_policy_summary(self) -> str:
        t = self.thresholds()
        assert t.extra is not None, "SNAP thresholds always populate `extra`"
        bbce = t.extra and t.extra.get("bbce_state", False)
        asset_note = (
            f"No asset test ({self.state} has broad-based categorical eligibility)."
            if bbce
            else f"Asset limits: ${t.asset_limit_general:,.0f} general, "
            f"${t.asset_limit_elderly_disabled:,.0f} elderly/disabled (60+ or disabled)."
        )
        return (
            f"SNAP Eligibility Rules ({self.fy_config.period_label}, {self.state}):\n"
            f"  [Based on {self.fy_config.fpl_year} HHS poverty guidelines]\n"
            f"- Gross income test: ≤130% FPL (7 CFR 273.9(a)(1))\n"
            f"- Net income test: ≤100% FPL (7 CFR 273.9(a)(2))\n"
            f"- Deductions: 20% earned income + standard deduction + allowable shelter/utility (7 CFR 273.9(c))\n"
            f"- {asset_note}\n"
            f"- Elderly/disabled: exempt from gross income test; $4,500 asset limit.\n"
            f"- Standard deduction (HH 1-3): $209/month; HH4: $223; HH5: $261; HH6+: $299.\n"
            f"- Excess shelter cap: ${t.extra['excess_shelter_cap']:,.0f}/month.\n"
            f"Source: {t.source}"
        )

    def calculate_net_income(
        self,
        gross_income: float,
        household_size: int,
        earned_income: float | None = None,
        shelter_costs: float | None = None,
        dependent_care: float | None = None,
        medical_expenses: float = 0.0,
        has_elderly_or_disabled: bool = False,
        is_homeless: bool = False,
    ) -> float:
        """Apply 7 CFR 273.9(c) deductions to arrive at SNAP net income."""
        if is_homeless and shelter_costs and shelter_costs > 0:
            raise ValueError("is_homeless and shelter_costs > 0 are mutually exclusive")

        t = self.thresholds()
        assert t.extra is not None, "SNAP thresholds always populate `extra`"
        earned = earned_income if earned_income is not None else gross_income

        # (c)(1) Earned income deduction — 20%
        earned_ded = earned * (t.earned_income_deduction_pct or 20) / 100.0
        after_earned = gross_income - earned_ded

        # (c)(2) Standard deduction
        std_ded = get_standard_deduction(household_size, self._region)
        after_standard = after_earned - std_ded

        # (c)(3) Dependent care deduction
        after_dep_care = after_standard - (dependent_care or 0.0)

        # (c)(4) Medical expense deduction for elderly/disabled (over $35 threshold)
        medical_ded = max(0.0, medical_expenses - 35) if has_elderly_or_disabled else 0.0
        after_medical = after_dep_care - medical_ded

        # (c)(5) Shelter deduction — homeless flat deduction or excess shelter calculation
        # Per 7 CFR 273.9(c)(6), these two paths are mutually exclusive.
        if is_homeless:
            shelter_ded = float(t.extra["homeless_shelter_deduction"])
        else:
            shelter_ded = 0.0
            if shelter_costs:
                shelter_cap = float(t.extra["excess_shelter_cap"]) if t.extra else 744.0
                half_income = max(0.0, after_medical) * 0.5
                raw_excess = max(0.0, shelter_costs - half_income)
                shelter_ded = raw_excess if has_elderly_or_disabled else min(raw_excess, shelter_cap)

        return max(0.0, after_medical - shelter_ded)

    def is_eligible(
        self,
        household_size: int,
        gross_income: float,
        net_income: float | None = None,
        liquid_assets: float = 0.0,
        has_elderly_or_disabled: bool = False,
        is_categorically_eligible: bool = False,
    ) -> tuple[bool, str]:
        """Determine SNAP eligibility. Returns (is_eligible, reason)."""
        if is_categorically_eligible:
            return True, "Categorically eligible (receives TANF/SSI or state BBCE program)"

        t = self.thresholds()
        limits = t.by_household_size(min(household_size, 8))

        # Gross income test — waived for elderly/disabled
        if not has_elderly_or_disabled and gross_income > limits.gross_monthly:
            return False, (
                f"Ineligible: gross income ${gross_income:,.2f} exceeds "
                f"${limits.gross_monthly:,.2f} (130% FPL, {self.fy_config.period_label}, "
                f"{household_size}-person HH)"
            )

        # Net income test
        eff_net = net_income if net_income is not None else gross_income
        if eff_net > limits.net_monthly:
            return False, (
                f"Ineligible: net income ${eff_net:,.2f} exceeds "
                f"${limits.net_monthly:,.2f} (100% FPL, {household_size}-person HH)"
            )

        # Asset test — may be waived by BBCE
        if t.asset_limit_general is not None:
            asset_cap = t.asset_limit_elderly_disabled if has_elderly_or_disabled else t.asset_limit_general
            if asset_cap and liquid_assets > asset_cap:
                return False, (f"Ineligible: assets ${liquid_assets:,.2f} exceed ${asset_cap:,.2f} limit")

        return True, "Eligible: all tests passed (gross income, net income, assets)"
