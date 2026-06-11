"""SNAP Broad-Based Categorical Eligibility (BBCE) data source connector.

BBCE lets a state confer SNAP categorical eligibility on households that receive a
non-cash TANF/MOE-funded benefit or service (e.g. an informational brochure or
referral). Adopting BBCE allows a state to:

  1. Raise the gross income limit from the federal 130% FPL up to the statutory
     ceiling of 200% FPL (states pick a value in that range).
  2. Eliminate or raise the asset/resource test.

The household must still pass the net income test (<=100% FPL) to receive a benefit,
except that 1-2 person categorically eligible households receive at least the
minimum benefit.

This connector EXTENDS :class:`SNAPSource` — reusing its federal income-limit table,
deductions, and net-income calculation — and layers the state-specific BBCE rules
from ``data/thresholds/snap_bbce_{fy}.json`` on top. ``SNAPSource`` itself is left
unchanged, so this addition is isolated.

Data sourced from:
  - USDA FNS SNAP BBCE States Chart (August 2025), cross-checked against CBPP.
  - data/thresholds/snap_bbce_fy{fiscal_year}.json
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from govsynth.fiscal_year import DEFAULT_SNAP_FY
from govsynth.sources.base import THRESHOLD_DIR, _load_json_file
from govsynth.sources.us.snap import SNAPSource, _region_for_state

# Maps the SNAP region key (from snap._region_for_state) to the FPL file region key.
_SNAP_REGION_TO_FPL_REGION: dict[str, str] = {
    "48_states_dc": "contiguous_48_dc",
    "alaska": "alaska",
    "hawaii": "hawaii",
}


@dataclass(frozen=True)
class BBCEParams:
    """Resolved BBCE parameters for a single jurisdiction.

    Attributes:
        state: Two-letter code, or 'FEDERAL' for the default fallback.
        bbce: Whether the state has adopted broad-based categorical eligibility.
        gross_income_limit_pct_fpl: Gross income ceiling as a percent of FPL (130-200).
        asset_limit: Liquid-asset cap in USD, or None if the asset test is waived.
        net_income_test_applies: Whether the 100% FPL net income test still binds.
        conferring_benefit: The TANF/MOE benefit/service that confers eligibility.
        applies_to: Which households the BBCE option covers.
        verification_status: 'verified' or 'unverified'.
        source_note: Provenance / caveats for this row.
    """

    state: str
    bbce: bool
    gross_income_limit_pct_fpl: int
    asset_limit: float | None
    net_income_test_applies: bool
    conferring_benefit: str | None
    applies_to: str
    verification_status: str
    source_note: str


def _bbce_filename(fiscal_year: int) -> str:
    """Return the BBCE data filename for a fiscal year, e.g. 'snap_bbce_fy2026.json'."""
    return f"snap_bbce_fy{fiscal_year}.json"


@lru_cache(maxsize=8)
def _load_bbce_table(fiscal_year: int) -> dict[str, Any]:
    """Load and cache the BBCE state table for a fiscal year."""
    path = THRESHOLD_DIR / _bbce_filename(fiscal_year)
    if not path.exists():
        raise FileNotFoundError(
            f"BBCE data file not found: {path}. Expected {_bbce_filename(fiscal_year)} "
            "in data/thresholds/."
        )
    return _load_json_file(str(path))


def _params_from_row(state: str, row: dict[str, Any]) -> BBCEParams:
    """Build a :class:`BBCEParams` from a raw JSON row."""
    return BBCEParams(
        state=state,
        bbce=bool(row["bbce"]),
        gross_income_limit_pct_fpl=int(row["gross_income_limit_pct_fpl"]),
        asset_limit=(None if row["asset_limit"] is None else float(row["asset_limit"])),
        net_income_test_applies=bool(row["net_income_test_applies"]),
        conferring_benefit=row.get("conferring_benefit"),
        applies_to=row.get("applies_to", ""),
        verification_status=row.get("verification_status", "unverified"),
        source_note=row.get("source_note", ""),
    )


class SNAPBBCESource(SNAPSource):
    """SNAP eligibility WITH Broad-Based Categorical Eligibility applied.

    Args:
        fiscal_year: Federal fiscal year (Oct 1 - Sep 30). Default FY2026.
        state: Two-letter state code or 'national'. Drives the BBCE rules applied.

    For a non-BBCE state (or 'national'), this source behaves like the federal rules
    and ``is_eligible`` defers to :class:`SNAPSource`.
    """

    def __init__(self, fiscal_year: int = DEFAULT_SNAP_FY, state: str = "national") -> None:
        super().__init__(fiscal_year=fiscal_year, state=state)
        self._bbce_params = self._resolve_bbce_params()

    # ------------------------------------------------------------------
    # BBCE parameter access
    # ------------------------------------------------------------------

    def _resolve_bbce_params(self) -> BBCEParams:
        table = _load_bbce_table(self.year)
        row = table["states"].get(self.state)
        if row is None:
            return _params_from_row("FEDERAL", table["federal_default"])
        return _params_from_row(self.state, row)

    @property
    def bbce_params(self) -> BBCEParams:
        """The resolved BBCE parameters for this source's state."""
        return self._bbce_params

    @property
    def is_bbce(self) -> bool:
        """True if this source's state has adopted BBCE."""
        return self._bbce_params.bbce

    # ------------------------------------------------------------------
    # Gross income limit derivation
    # ------------------------------------------------------------------

    def _fpl_annual(self, household_size: int) -> float:
        """Annual FPL for the household size, in this source's region.

        Uses the bundled HHS poverty guidelines (us_fpl_{fpl_year}.json) so the BBCE
        gross limit is derived from the same basis SNAP uses, rather than scaled.
        """
        fpl_raw = _load_json_file(str(THRESHOLD_DIR / f"us_fpl_{self.fy_config.fpl_year}.json"))
        region_key = _SNAP_REGION_TO_FPL_REGION[_region_for_state(self.state)]
        region = fpl_raw["regions"][region_key]
        by_size = region["by_household_size"]
        if household_size <= 8:
            return float(by_size[str(household_size)]["annual"])
        # Extrapolate beyond size 8 with the per-additional-person increment.
        base = float(by_size["8"]["annual"])
        return base + (household_size - 8) * float(region["per_additional_person_annual"])

    def effective_gross_limit(self, household_size: int) -> float:
        """Monthly gross income limit under this state's BBCE option.

        Derived from the annual FPL using SNAP's published rounding,
        ``ceil(annual_fpl * pct / 100 / 12)`` — the same method that yields the
        federal 130% limits. For a non-BBCE state this equals the federal 130% limit.
        """
        pct = self._bbce_params.gross_income_limit_pct_fpl
        annual = self._fpl_annual(household_size)
        return float(math.ceil(annual * pct / 100.0 / 12.0))

    def federal_gross_limit(self, household_size: int) -> float:
        """The federal 130% FPL monthly gross limit (for contrast in rationales)."""
        annual = self._fpl_annual(household_size)
        return float(math.ceil(annual * 130 / 100.0 / 12.0))

    # ------------------------------------------------------------------
    # Benefit estimate (with minimum-benefit floor)
    # ------------------------------------------------------------------

    def estimate_monthly_benefit(
        self,
        household_size: int,
        net_income: float,
        is_categorically_eligible: bool = True,
    ) -> float:
        """Estimate the monthly SNAP allotment.

        Benefit = max allotment - 30% of net income, floored at $0. For 1-2 person
        categorically eligible households the result is floored at the minimum benefit
        even when the calculation rounds to $0.
        """
        t = self.thresholds()
        limits = t.by_household_size(min(household_size, 8))
        max_allotment = limits.max_benefit or 0.0
        benefit = max(0.0, max_allotment - 0.30 * max(0.0, net_income))
        if is_categorically_eligible and household_size <= 2:
            minimum = float((t.extra or {}).get("minimum_benefit", 24))
            benefit = max(benefit, minimum)
        return round(benefit, 2)

    # ------------------------------------------------------------------
    # Eligibility determination
    # ------------------------------------------------------------------

    def _asset_cap(self, has_elderly_or_disabled: bool) -> float | None:
        """Resolve the applicable liquid-asset cap.

        BBCE states use the state's BBCE asset rule for all households (None = waived,
        or a dollar cap). Non-BBCE states use the federal limits: $4,500 for households
        with an elderly/disabled member, otherwise the federal general limit.
        """
        p = self._bbce_params
        if p.bbce:
            return p.asset_limit
        if has_elderly_or_disabled:
            return self.thresholds().asset_limit_elderly_disabled
        return p.asset_limit  # federal general limit ($3,000) from the data row

    def is_eligible(
        self,
        household_size: int,
        gross_income: float,
        net_income: float | None = None,
        liquid_assets: float = 0.0,
        has_elderly_or_disabled: bool = False,
        is_categorically_eligible: bool = False,
    ) -> tuple[bool, str]:
        """Determine SNAP eligibility under this state's BBCE rules.

        Returns (is_eligible, reason). This connector is authoritative from its own
        data file: for a non-BBCE state the effective gross limit equals the federal
        130% FPL limit and federal asset limits apply, so the same code path serves
        both cases without relying on SNAPSource's (legacy) state classification.
        """
        if is_categorically_eligible:
            return True, "Categorically eligible (receives TANF/SSI or state BBCE program)"

        p = self._bbce_params
        limits = self.thresholds().by_household_size(min(household_size, 8))

        # Gross income test — uses the (possibly raised) limit; waived for elderly/disabled.
        if not has_elderly_or_disabled:
            gross_limit = self.effective_gross_limit(household_size)
            if gross_income > gross_limit:
                basis = (
                    f"{p.gross_income_limit_pct_fpl}% FPL BBCE limit, {self.state}"
                    if p.bbce
                    else "130% FPL"
                )
                return False, (
                    f"Ineligible: gross income ${gross_income:,.2f} exceeds "
                    f"${gross_limit:,.2f} ({basis}, {household_size}-person HH)"
                )

        # Net income test — still binds (always for non-BBCE; near-universally under BBCE).
        if p.net_income_test_applies:
            eff_net = net_income if net_income is not None else gross_income
            if eff_net > limits.net_monthly:
                return False, (
                    f"Ineligible: net income ${eff_net:,.2f} exceeds "
                    f"${limits.net_monthly:,.2f} (100% FPL, {household_size}-person HH)"
                )

        # Asset test — waived (None) or capped.
        cap = self._asset_cap(has_elderly_or_disabled)
        if cap is not None and liquid_assets > cap:
            kind = "BBCE asset cap" if p.bbce else "asset limit"
            return False, (
                f"Ineligible: assets ${liquid_assets:,.2f} exceed the ${cap:,.2f} "
                f"{kind} ({self.state})"
            )

        if p.bbce:
            asset_note = (
                "asset test waived"
                if p.asset_limit is None
                else f"assets within ${p.asset_limit:,.0f} BBCE cap"
            )
            return True, (
                f"Eligible under {self.state} BBCE ({p.gross_income_limit_pct_fpl}% FPL gross "
                f"limit, net income test passed, {asset_note})"
            )
        return True, "Eligible: all tests passed (gross income, net income, assets)"
