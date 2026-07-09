"""Census ACS distribution loader.

Loads data/census/<state>.json into a typed CensusDistribution.
No network calls -- pure deserialization.

Not a DataSource subclass: census distributions are profile-sampling
statistics, not program eligibility rules. Lives in sources/us/ for
colocation with other US data modules.
"""

from __future__ import annotations

import copy
import json
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_CENSUS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "census"


@lru_cache(maxsize=64)
def _load_census_json_cached(path_str: str) -> dict[str, Any]:
    """Cached JSON loader for census data.

    Private: returns the same dict object on every call. Use
    `_load_census_json` instead, which hands back a deep copy so callers
    can't corrupt the shared cache. After rewriting a census file
    in-process (e.g. via `refresh-census-data`), call
    `_load_census_json_cached.cache_clear()` to avoid serving stale data.
    """
    with open(path_str, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
        return data


def _load_census_json(path_str: str) -> dict[str, Any]:
    """Load census JSON with a process-lifetime cache keyed by path.

    Returns a deep copy so callers can't mutate the shared cache.
    """
    return copy.deepcopy(_load_census_json_cached(path_str))


@dataclass
class CensusDistribution:
    """Typed Census ACS distribution for one state or national."""

    state: str
    income_mu: float
    income_sigma: float
    fpl_buckets: list[dict[str, Any]]
    household_size_weights: list[float]
    pct_with_children: float
    pct_elderly_or_disabled: float
    pct_citizen: float
    pct_noncitizen_eligible: float
    labor_force_participation_rate: float
    pct_social_security: float
    pct_ssi: float
    pct_public_assistance: float
    snap_receipt_rate: float
    medicaid_coverage_rate: float
    median_gross_rent_monthly: float
    pct_renter: float
    age_mu: float
    age_sigma: float


class CensusDataSource:
    """Load state-level ACS distributions from a local JSON file.

    Args:
        state: Two-letter state code (case-insensitive) or 'national'.
        data_dir: Override the default data/census/ directory (for tests).
    """

    def __init__(self, state: str, data_dir: Path | None = None) -> None:
        self.state = state.lower()
        self._data_dir = data_dir if data_dir is not None else _CENSUS_DIR

    def load(self) -> CensusDistribution | None:
        """Deserialize the census JSON for this state.

        Returns:
            CensusDistribution, or None if no data file exists at all.

        Side effects:
            Emits UserWarning when the state file is absent and falls back
            to national.json. Silent when national.json is also absent.
        """
        path = self._data_dir / f"{self.state}.json"
        if not path.exists():
            warnings.warn(
                f"No census data for '{self.state.upper()}', falling back to national distribution",
                UserWarning,
                stacklevel=2,
            )
            path = self._data_dir / "national.json"
            if not path.exists():
                return None

        data = _load_census_json(str(path))

        return _parse(data)


def _parse(data: dict[str, Any]) -> CensusDistribution:
    """Deserialize a census JSON dict into a CensusDistribution."""
    inc = data["income"]
    hsg = data["housing"]
    hh = data["household_size"]
    dem = data["demographics"]
    isrc = data["income_sources"]
    pp = data["program_participation"]

    return CensusDistribution(
        state=data["_metadata"]["state"],
        income_mu=inc["monthly_lognormal"]["mu"],
        income_sigma=inc["monthly_lognormal"]["sigma"],
        fpl_buckets=inc["fpl_buckets"],
        household_size_weights=hh["weights"],
        pct_with_children=dem["pct_with_children"],
        pct_elderly_or_disabled=dem["pct_elderly_or_disabled"],
        pct_citizen=dem["pct_citizen"],
        pct_noncitizen_eligible=dem["pct_noncitizen_eligible"],
        labor_force_participation_rate=isrc["labor_force_participation_rate"],
        pct_social_security=isrc["pct_social_security"],
        pct_ssi=isrc["pct_ssi"],
        pct_public_assistance=isrc["pct_public_assistance"],
        snap_receipt_rate=pp["snap_receipt_rate"],
        medicaid_coverage_rate=pp["medicaid_coverage_rate"],
        median_gross_rent_monthly=hsg["median_gross_rent_monthly"],
        pct_renter=hsg["pct_renter"],
        age_mu=dem["age"]["mu"],
        age_sigma=dem["age"]["sigma"],
    )
