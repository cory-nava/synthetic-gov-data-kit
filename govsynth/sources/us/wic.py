"""WIC (Special Supplemental Nutrition Program for Women, Infants, and Children) source.

Data sourced from:
  - Federal Register 2025-03576 (90 FR 11598), March 10, 2025 — VERIFIED
  - data/thresholds/wic_fy{year}.json

CRITICAL CALENDAR NOTE:
  WIC income eligibility guidelines run JULY 1 – JUNE 30, not the federal
  fiscal year (Oct 1–Sep 30). This is WIC's own unique cycle.

  Current period: July 1, 2025 – June 30, 2026
  This is labeled "FY2026" internally (start year convention = 2026).
  These guidelines use the 2025 HHS poverty guidelines as their basis.

  WIC 2025-2026 income limits (185% FPL, 48 states + DC):
    HH1: $2,413/month  HH2: $3,261  HH3: $4,109  HH4: $4,957
    HH5: $5,805  HH6: $6,653  HH7: $7,501  HH8: $8,349
  Source: Federal Register 2025-03576, 90 FR 11598
"""

from __future__ import annotations

from govsynth.fiscal_year import DEFAULT_WIC_FY, FiscalYearConfig
from govsynth.sources.base import DataSource, HouseholdThreshold, ProgramThresholds


class WICSource(DataSource):
    """WIC eligibility data source.

    Args:
        fiscal_year: WIC IEG year. 2026 = July 1 2025 – June 30 2026 (current).
        state: State code (income limits are national; state affects clinic access).
               Use 'AK' or 'HI' for those regions' higher limits.
    """

    def __init__(self, fiscal_year: int = DEFAULT_WIC_FY, state: str = "national") -> None:
        super().__init__(year=fiscal_year, state=state)
        self.fy_config = FiscalYearConfig.for_program("wic", fiscal_year)
        self._region = self._get_region()

    def _get_region(self) -> str:
        if self.state == "AK":
            return "alaska"
        if self.state == "HI":
            return "hawaii"
        return "48_states_dc_guam"

    @property
    def program(self) -> str:
        return "wic"

    def fetch_thresholds(self) -> ProgramThresholds:
        raw = self._load_threshold_json(self.fy_config.threshold_filename)
        region_key = f"households_{self._region}"
        raw_hh = raw[region_key]

        households: dict[int, HouseholdThreshold] = {}
        for key, val in raw_hh.items():
            if key == "each_additional":
                continue
            size = int(key)
            households[size] = HouseholdThreshold(
                household_size=size,
                gross_monthly=float(val["monthly"]),
                net_monthly=float(val["monthly"]),  # WIC uses gross only
                max_benefit=None,
            )

        meta = raw["_metadata"]
        return ProgramThresholds(
            program="wic",
            fiscal_year=self.year,
            state=self.state,
            source=meta["source"],
            source_url=meta.get("source_url"),
            households=households,
            asset_limit_general=None,  # No asset test
            extra={
                "income_limit_pct_fpl": raw["income_limit_pct_fpl"],
                "eligible_categories": raw["eligible_categories"],
                "categorical_eligibility_programs": raw["categorical_eligibility_programs"],
                "cfr_reference": meta["cfr_reference"],
                "period_label": self.fy_config.period_label,
                "fpl_basis_year": self.fy_config.fpl_year,
                "effective_start": meta["effective_start"],
                "effective_end": meta["effective_end"],
                "verification_status": meta["verification_status"],
                "region": self._region,
            },
        )

    def fetch_policy_summary(self) -> str:
        t = self.thresholds()
        extra = t.extra or {}
        return (
            f"WIC Income Eligibility Guidelines ({extra['effective_start']} to "
            f"{extra['effective_end']}, {self._region.replace('_', ' ').title()}):\n"
            f"  [CRITICAL: WIC runs July 1–June 30, not Oct 1–Sep 30]\n"
            f"  [Based on {extra['fpl_basis_year']} HHS poverty guidelines × 1.85, rounded up]\n"
            "- Income test: ≤185% FPL (7 CFR 246.7(d)(1))\n"
            "- No asset test.\n"
            "- Categorical eligibility: SNAP/Medicaid/TANF = auto income-eligible.\n"
            "- Eligible categories: pregnant, breastfeeding, postpartum women; "
            "infants; children under age 5.\n"
            "- Nutritional risk determination required at WIC clinic.\n"
            f"Source: {t.source}"
        )

    def is_eligible(
        self,
        household_size: int,
        monthly_gross_income: float,
        participant_category: str,
        is_categorically_eligible: bool = False,
    ) -> tuple[bool, str]:
        """Determine WIC eligibility. Returns (is_eligible, reason)."""
        t = self.thresholds()
        valid_cats = (t.extra or {}).get("eligible_categories", [])

        if participant_category not in valid_cats:
            return False, (
                f"Ineligible: '{participant_category}' is not a WIC-eligible category. "
                f"Valid: {valid_cats}"
            )

        if is_categorically_eligible:
            return True, "Categorically eligible via SNAP/Medicaid/TANF. Income test waived."

        limits = t.by_household_size(min(household_size, 8))
        if monthly_gross_income > limits.gross_monthly:
            extra = t.extra or {}
            return False, (
                f"Ineligible: gross income ${monthly_gross_income:,.2f} exceeds "
                f"${limits.gross_monthly:,.2f} (185% FPL, {household_size}-person HH, "
                f"{extra['effective_start']} to {extra['effective_end']})"
            )

        return True, (
            f"Income-eligible: ${monthly_gross_income:,.2f} ≤ ${limits.gross_monthly:,.2f}. "
            "Nutritional risk assessment still required."
        )
