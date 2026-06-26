"""Medicaid data source connector.

Data sourced from:
  - 42 CFR Part 435 (Medicaid eligibility)
  - data/thresholds/medicaid_cy{year}.json (bundled)
  - CMS / KFF state expansion status

CALENDAR NOTE:
  Medicaid uses MAGI (Modified Adjusted Gross Income) measured against HHS
  poverty guidelines published each January. Medicaid follows the CALENDAR
  YEAR, not the federal fiscal year. CY2026 uses the 2026 HHS poverty guidelines.

MAGI vs SNAP NET INCOME:
  Medicaid income is NOT the same calculation as SNAP net income.
  MAGI adds back certain deductions (e.g., student loan interest) and
  excludes others (e.g., SNAP standard deductions do NOT apply).
  For Medicaid, income = gross income minus a 5% FPL disregard.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from govsynth.fiscal_year import DEFAULT_MEDICAID_CY, FiscalYearConfig
from govsynth.sources.base import DataSource, HouseholdThreshold, ProgramThresholds

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "thresholds"


@lru_cache(maxsize=16)
def _load_fpl_json(path_str: str) -> dict[str, Any]:
    """Cached loader for FPL guidelines JSON."""
    with open(path_str) as f:
        return cast("dict[str, Any]", json.load(f))


class MedicaidSource(DataSource):
    """Medicaid eligibility data source.

    Args:
        calendar_year: Calendar year for income limits. Default CY2026.
        state: Two-letter state code. Determines expansion vs. non-expansion rules.

    Usage:
        source = MedicaidSource(calendar_year=2026, state="VA")
        eligible, reason = source.is_eligible(
            household_size=1, monthly_magi=1500, applicant_type="adult"
        )
    """

    def __init__(self, calendar_year: int = DEFAULT_MEDICAID_CY, state: str = "VA") -> None:
        super().__init__(year=calendar_year, state=state)
        self.fy_config = FiscalYearConfig.for_program("medicaid", calendar_year)
        self._raw: dict[str, Any] | None = None

    @property
    def program(self) -> str:
        return "medicaid"

    def _load_raw(self) -> dict[str, Any]:
        if self._raw is None:
            self._raw = self._load_threshold_json(self.fy_config.threshold_filename)
        return self._raw

    def is_expansion_state(self) -> bool:
        """Return True if this state has adopted ACA Medicaid expansion."""
        raw = self._load_raw()
        return self.state in raw.get("expansion_states", [])

    def fetch_thresholds(self) -> ProgramThresholds:
        raw = self._load_raw()
        expansion = self.is_expansion_state()

        # For expansion states: adults covered up to 138% FPL
        # Build a synthetic threshold table from FPL percentages
        # Using the 2026 FPL from us_fpl_2026.json
        fpl_file = _DATA_DIR / f"us_fpl_{self.fy_config.fpl_year}.json"
        fpl_data = _load_fpl_json(str(fpl_file))

        fpl_by_size = fpl_data["regions"]["contiguous_48_dc"]["by_household_size"]

        households: dict[int, HouseholdThreshold] = {}
        for size_str, vals in fpl_by_size.items():
            size = int(size_str)
            monthly_fpl = float(vals["monthly"])

            if expansion:
                # 138% FPL — applies to adults. 5% disregard built into 138%
                income_limit = monthly_fpl * 1.38
            else:
                # Non-expansion: use parent/caretaker limit as conservative default
                state_limits = raw.get("non_expansion_income_limits_pct_fpl", {})
                parent_pct = state_limits.get("parents_caretaker_relative", {}).get(self.state, 0)
                income_limit = monthly_fpl * (parent_pct / 100.0)

            households[size] = HouseholdThreshold(
                household_size=size,
                gross_monthly=income_limit,
                net_monthly=income_limit,  # Medicaid uses MAGI, no net deductions
                max_benefit=None,
            )

        return ProgramThresholds(
            program="medicaid",
            fiscal_year=self.year,
            state=self.state,
            source=raw["_metadata"]["source"],
            source_url=raw["_metadata"].get("source_url"),
            households=households,
            extra={
                "expansion_state": expansion,
                "income_methodology": raw.get("income_methodology", "MAGI"),
                "expansion_income_limit_pct_fpl": raw.get("expansion_income_limit_pct_fpl", 138),
                "cfr_reference": raw["_metadata"]["cfr_reference"],
                "calendar_year": self.year,
                "fpl_year": self.fy_config.fpl_year,
            },
        )

    def fetch_policy_summary(self) -> str:
        expansion = self.is_expansion_state()
        status = (
            "expansion state (covers adults to 138% FPL)" if expansion else "non-expansion state"
        )
        adults_line = (
            "- Adults: covered up to 138% FPL under ACA expansion."
            if expansion
            else "- Adults without children: NO coverage (coverage gap)."
        )
        return (
            f"Medicaid Eligibility Rules (CY{self.year}, {self.state} — {status}):\n"
            f"  [Based on {self.fy_config.fpl_year} HHS poverty guidelines]\n"
            "- Income methodology: MAGI (Modified Adjusted Gross Income) (42 CFR 435.603)\n"
            "- Income ≠ SNAP net income: MAGI has different rules, no SNAP deductions apply.\n"
            f"{adults_line}\n"
            "- Children/pregnant women: higher limits in all states.\n"
            "- No asset test for MAGI-based Medicaid.\n"
            f"Source: {self._load_raw()['_metadata']['source']}"
        )

    def get_income_limit(self, applicant_type: str) -> float | None:
        """Return monthly MAGI income limit for a given applicant type.

        Args:
            applicant_type: One of 'adult', 'pregnant', 'child_0_1', 'child_1_5',
                            'child_6_18', 'parent_caretaker'

        Returns:
            Monthly income limit in dollars, or None if no coverage.
        """
        raw = self._load_raw()
        fpl_file = _DATA_DIR / f"us_fpl_{self.fy_config.fpl_year}.json"
        fpl_data = _load_fpl_json(str(fpl_file))
        monthly_fpl_1 = float(
            fpl_data["regions"]["contiguous_48_dc"]["by_household_size"]["1"]["monthly"]
        )

        expansion = self.is_expansion_state()
        non_exp = raw.get("non_expansion_income_limits_pct_fpl", {})

        if applicant_type == "adult":
            if expansion:
                return monthly_fpl_1 * 1.38
            return None  # Coverage gap in non-expansion states

        elif applicant_type in ("pregnant", "postpartum"):
            pct = (
                non_exp.get("pregnant_women_pct_fpl", {}).get(self.state, 200)
                if not expansion
                else 200
            )
            return monthly_fpl_1 * (pct / 100.0)

        elif applicant_type in ("child_0_18", "child"):
            pct = (
                non_exp.get("children_0_18_pct_fpl", {}).get(self.state, 200)
                if not expansion
                else 317
            )
            return monthly_fpl_1 * (pct / 100.0)

        elif applicant_type == "parent_caretaker":
            if expansion:
                return monthly_fpl_1 * 1.38
            pct = non_exp.get("parents_caretaker_relative", {}).get(self.state, 0)
            return monthly_fpl_1 * (pct / 100.0) if pct > 0 else None

        return None

    def is_eligible(
        self,
        household_size: int,
        monthly_magi: float,
        applicant_type: str = "adult",
    ) -> tuple[bool, str]:
        """Determine Medicaid eligibility and return (is_eligible, reason).

        Args:
            household_size: Household size for income limit lookup.
            monthly_magi: Monthly MAGI income.
            applicant_type: 'adult', 'pregnant', 'child', 'parent_caretaker'
        """
        fpl_file = _DATA_DIR / f"us_fpl_{self.fy_config.fpl_year}.json"
        fpl_data = _load_fpl_json(str(fpl_file))
        sizes = fpl_data["regions"]["contiguous_48_dc"]["by_household_size"]
        key = str(min(household_size, 8))
        monthly_fpl = float(sizes[key]["monthly"])

        income_limit = self.get_income_limit(applicant_type)

        if income_limit is None:
            return False, (
                f"Ineligible: {self.state} has not expanded Medicaid. "
                f"Adults without dependents fall in the coverage gap — "
                f"income too high for traditional Medicaid, too low for ACA marketplace subsidies."
            )

        if monthly_magi <= income_limit:
            pct = round((monthly_magi / monthly_fpl) * 100)
            return True, (
                f"Eligible: MAGI ${monthly_magi:,.2f} ≤ ${income_limit:,.2f} income limit "
                f"({pct}% FPL, CY{self.year}, {self.state})"
            )

        return False, (
            f"Ineligible: MAGI ${monthly_magi:,.2f} exceeds ${income_limit:,.2f} "
            f"income limit for {applicant_type} in {self.state} (CY{self.year})"
        )
