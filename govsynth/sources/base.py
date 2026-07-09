"""Abstract base class for all data source connectors."""

from __future__ import annotations

import copy
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent.parent / "data"
THRESHOLD_DIR = DATA_DIR / "thresholds"
SEED_DIR = DATA_DIR / "seeds"


@lru_cache(maxsize=128)
def _load_json_file_cached(path_str: str) -> dict[str, Any]:
    """Cached JSON file loader to avoid redundant I/O and parsing.

    Private: returns the *same* dict object on every call. Callers must go
    through `_load_json_file`, which hands out a deep copy, so that no
    caller can mutate the shared cached object out from under everyone
    else. If the underlying file changes on disk (e.g. via
    `govsynth refresh-census-data`), call `_load_json_file_cached.cache_clear()`
    to invalidate stale entries.
    """
    with open(path_str) as f:
        data: dict[str, Any] = json.load(f)
        return data


def _load_json_file(path_str: str) -> dict[str, Any]:
    """Load a JSON file with a process-lifetime cache keyed by path.

    Returns a deep copy of the cached data on every call so callers can
    freely read or mutate the result without corrupting the shared cache
    or leaking state between callers.
    """
    return copy.deepcopy(_load_json_file_cached(path_str))


@dataclass
class HouseholdThreshold:
    """Income and benefit limits for a specific household size."""

    household_size: int
    gross_monthly: float
    net_monthly: float
    max_benefit: float | None = None

    def is_eligible_gross(self, income: float) -> bool:
        """Return True if income is at or below the gross income limit."""
        return income <= self.gross_monthly

    def is_eligible_net(self, income: float) -> bool:
        """Return True if income is at or below the net income limit."""
        return income <= self.net_monthly


@dataclass
class ProgramThresholds:
    """Complete threshold table for a program/year/state combination."""

    program: str
    fiscal_year: int
    state: str
    source: str
    source_url: str | None
    households: dict[int, HouseholdThreshold]

    # Program-specific fields (populated by subclasses)
    asset_limit_general: float | None = None
    asset_limit_elderly_disabled: float | None = None
    earned_income_deduction_pct: float | None = None
    standard_deductions: dict[int, float] | None = None
    extra: dict[str, Any] | None = None

    def by_household_size(self, size: int) -> HouseholdThreshold:
        """Look up thresholds for a given household size.

        For sizes beyond the table maximum, falls back to the largest defined
        size plus any per-additional-person increment.
        """
        if size in self.households:
            return self.households[size]

        # Get the largest defined key and extrapolate
        max_key = max(self.households.keys())
        if size > max_key:
            raise ValueError(
                f"Household size {size} exceeds maximum defined size {max_key} for {self.program} {self.fiscal_year}"
            )
        raise KeyError(f"No threshold entry for household size {size}")


class DataSource(ABC):
    """Abstract base for all data source connectors.

    A DataSource knows how to fetch and normalize policy data for a specific
    program, year, and optionally state. All threshold values come from here —
    generators never hardcode policy numbers.
    """

    def __init__(self, year: int, state: str = "national") -> None:
        self.year = year
        self.state = state.upper() if state != "national" else "national"
        self._thresholds_cache: ProgramThresholds | None = None

    @property
    @abstractmethod
    def program(self) -> str:
        """The program identifier, e.g. 'snap'."""
        ...

    @abstractmethod
    def fetch_thresholds(self) -> ProgramThresholds:
        """Load and return the program's income/benefit threshold table."""
        ...

    @abstractmethod
    def fetch_policy_summary(self) -> str:
        """Return a plain-text summary of key eligibility rules for this program."""
        ...

    def thresholds(self) -> ProgramThresholds:
        """Cached threshold access."""
        if self._thresholds_cache is None:
            self._thresholds_cache = self.fetch_thresholds()
        return self._thresholds_cache

    def _load_threshold_json(self, filename: str) -> dict[str, Any]:
        """Load a threshold JSON file from data/thresholds/."""
        path = THRESHOLD_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Threshold file not found: {path}. Run `govsynth update-thresholds` to download latest data."
            )
        # Use cached loader to avoid redundant disk I/O and parsing
        return _load_json_file(str(path))

    def _load_seed_text(self, *path_parts: str) -> str:
        """Load a policy seed text file from data/seeds/."""
        path = SEED_DIR.joinpath(*path_parts)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")
