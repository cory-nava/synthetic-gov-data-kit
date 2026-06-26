"""Fiscal year configuration and utilities.

US government programs operate on different calendars:

  Federal Fiscal Year (FFY):
    October 1 – September 30
    FFY2026 = October 1, 2025 – September 30, 2026
    Used by: SNAP, WIC, most USDA/HHS programs

  HHS Poverty Guidelines Calendar Year:
    Published each January, effective ~January 15
    The 2026 poverty guidelines were published January 2026.
    Used by: Medicaid MAGI, CHIP, ACA marketplace, WIC (as basis for 185% FPL calc)

  State Fiscal Year:
    Varies by state. Most run July 1 – June 30.
    Some Medicaid state plan amendments use state FY.

Key relationships:
  - SNAP FY2026 thresholds (effective Oct 1 2025) are calculated from the
    2025 HHS poverty guidelines (published Jan 2025).
  - Medicaid income limits for calendar year 2026 use the 2026 HHS poverty
    guidelines (published Jan 2026).
  - WIC FY2026 income eligibility guidelines use the 2025 HHS poverty
    guidelines (same basis as SNAP FY2026).

This module centralizes all fiscal year logic so threshold data files and
generators never have to reason about calendar math themselves.
"""

from __future__ import annotations

from datetime import date
from enum import Enum


class FiscalCalendar(str, Enum):
    """Which fiscal calendar a program uses."""

    FEDERAL_FY = "federal_fy"  # Oct 1 – Sep 30 (SNAP, CHIP, Section 8, LIHEAP, TANF)
    WIC_CALENDAR = "wic_calendar"  # Jul 1 – Jun 30 (WIC — its own distinct cycle)
    HHS_CALENDAR = "hhs_calendar"  # Calendar year, HHS poverty guidelines (Medicaid MAGI)
    STATE_FY = "state_fy"  # July 1 – June 30 (varies by state)


# Which calendar each program follows
PROGRAM_FISCAL_CALENDARS: dict[str, FiscalCalendar] = {
    "snap": FiscalCalendar.FEDERAL_FY,
    "wic": FiscalCalendar.WIC_CALENDAR,  # Jul 1–Jun 30, NOT Oct 1–Sep 30
    "chip": FiscalCalendar.FEDERAL_FY,
    "section_8": FiscalCalendar.FEDERAL_FY,
    "liheap": FiscalCalendar.FEDERAL_FY,
    "tanf": FiscalCalendar.FEDERAL_FY,
    "medicaid": FiscalCalendar.HHS_CALENDAR,
}

# Which HHS poverty guideline year each federal FY uses as its basis.
# SNAP/WIC FY thresholds are calculated from the previous January's FPL.
#   FY2026 (Oct 2025–Sep 2026) → built from 2025 HHS poverty guidelines
#   FY2025 (Oct 2024–Sep 2025) → built from 2024 HHS poverty guidelines
FEDERAL_FY_TO_FPL_YEAR: dict[int, int] = {
    2024: 2023,
    2025: 2024,
    2026: 2025,
    2027: 2026,
}

# Medicaid MAGI income limits use the poverty guidelines published that calendar year.
MEDICAID_CALENDAR_TO_FPL_YEAR: dict[int, int] = {
    2024: 2024,
    2025: 2025,
    2026: 2026,
}


def current_federal_fy(as_of: date | None = None) -> int:
    """Return the current federal fiscal year.

    The federal fiscal year starts October 1.
    As of March 22, 2026: we are in FY2026 (started Oct 1, 2025).

    Args:
        as_of: Date to evaluate. Defaults to today.

    Returns:
        Integer fiscal year, e.g. 2026.

    Examples:
        >>> current_federal_fy(date(2025, 9, 30))
        2025
        >>> current_federal_fy(date(2025, 10, 1))
        2026
        >>> current_federal_fy(date(2026, 3, 22))
        2026
    """
    d = as_of or date.today()
    # FY starts October 1: if month >= 10, FY = calendar year + 1
    if d.month >= 10:
        return d.year + 1
    return d.year


def federal_fy_date_range(fy: int) -> tuple[date, date]:
    """Return the start and end dates for a federal fiscal year.

    Args:
        fy: Federal fiscal year, e.g. 2026

    Returns:
        Tuple of (start_date, end_date) where:
          start = October 1 of the prior calendar year
          end   = September 30 of the given FY year
    """
    return date(fy - 1, 10, 1), date(fy, 9, 30)


def fpl_year_for_program(program: str, period_year: int) -> int:
    """Return the HHS poverty guideline year that a program uses for a given period.

    Args:
        program: Program identifier, e.g. 'snap', 'medicaid'
        period_year: The fiscal or calendar year of the program period

    Returns:
        The HHS FPL year to use for threshold calculations

    Examples:
        >>> fpl_year_for_program("snap", 2026)
        2025
        >>> fpl_year_for_program("medicaid", 2026)
        2026
    """
    calendar = PROGRAM_FISCAL_CALENDARS.get(program, FiscalCalendar.FEDERAL_FY)

    if calendar == FiscalCalendar.FEDERAL_FY:
        return FEDERAL_FY_TO_FPL_YEAR.get(period_year, period_year - 1)
    elif calendar == FiscalCalendar.WIC_CALENDAR:
        # WIC IEG year (e.g. '2026' = Jul 2025–Jun 2026) uses the FPL published that January
        # WIC 2025-2026 (effective Jul 1 2025) uses 2025 HHS FPL (published Jan 2025)
        # WIC 2026-2027 (effective Jul 1 2026) will use 2026 HHS FPL (published Jan 2026)
        # We label WIC by the start year of the IEG period: 2026 = Jul 2025–Jun 2026
        return period_year - 1
    elif calendar == FiscalCalendar.HHS_CALENDAR:
        return MEDICAID_CALENDAR_TO_FPL_YEAR.get(period_year, period_year)
    else:
        return period_year - 1  # Conservative fallback for state FY


def threshold_file_label(program: str, period_year: int) -> str:
    """Return the threshold file label for display/citation purposes.

    Examples:
        >>> threshold_file_label("snap", 2026)
        'FY2026'
        >>> threshold_file_label("medicaid", 2026)
        'CY2026'
    """
    calendar = PROGRAM_FISCAL_CALENDARS.get(program, FiscalCalendar.FEDERAL_FY)
    if calendar == FiscalCalendar.FEDERAL_FY:
        prefix = "FY"
    elif calendar == FiscalCalendar.WIC_CALENDAR:
        prefix = "FY"  # WIC files use FY label by convention (fy2026 = Jul 2025–Jun 2026)
    else:
        prefix = "CY"
    return f"{prefix}{period_year}"


class FiscalYearConfig:
    """Resolved fiscal year configuration for a specific program + period.

    This is the single object that knows everything about what thresholds
    apply for a given program, what year to load, and how to cite it.

    Usage:
        config = FiscalYearConfig.for_program("snap", 2026)
        print(config.threshold_file)   # "snap_fy2026.json"
        print(config.fpl_year)         # 2025
        print(config.period_label)     # "FY2026"
        print(config.effective_range)  # (date(2025,10,1), date(2026,9,30))
    """

    def __init__(
        self,
        program: str,
        period_year: int,
        calendar: FiscalCalendar,
        fpl_year: int,
    ) -> None:
        self.program = program
        self.period_year = period_year
        self.calendar = calendar
        self.fpl_year = fpl_year

    @classmethod
    def for_program(cls, program: str, period_year: int | None = None) -> FiscalYearConfig:
        """Create a FiscalYearConfig for the given program and period.

        Args:
            program: Program identifier, e.g. 'snap'
            period_year: The FY or CY year. Defaults to current applicable year.
        """
        calendar = PROGRAM_FISCAL_CALENDARS.get(program, FiscalCalendar.FEDERAL_FY)

        if period_year is None:
            if calendar == FiscalCalendar.FEDERAL_FY:
                period_year = current_federal_fy()
            else:
                period_year = date.today().year

        fpl_year = fpl_year_for_program(program, period_year)
        return cls(program=program, period_year=period_year, calendar=calendar, fpl_year=fpl_year)

    @property
    def period_label(self) -> str:
        """Human-readable period label, e.g. 'FY2026' or 'CY2026'."""
        return threshold_file_label(self.program, self.period_year)

    @property
    def threshold_filename(self) -> str:
        """Filename for the bundled threshold JSON, e.g. 'snap_fy2026.json'."""
        label = self.period_label.lower()  # 'fy2026' or 'cy2026'
        return f"{self.program}_{label}.json"

    @property
    def fpl_filename(self) -> str:
        """Filename for the FPL guidelines used as basis, e.g. 'us_fpl_2025.json'."""
        return f"us_fpl_{self.fpl_year}.json"

    @property
    def effective_range(self) -> tuple[date, date] | None:
        """Start/end dates for federal FY programs. None for calendar-year programs."""
        if self.calendar == FiscalCalendar.FEDERAL_FY:
            return federal_fy_date_range(self.period_year)
        return None

    def citation_prefix(self) -> str:
        """Return a citation-ready period description."""
        if self.calendar == FiscalCalendar.FEDERAL_FY:
            start, end = federal_fy_date_range(self.period_year)
            return (
                f"{self.period_label} (effective {start.strftime('%B %d, %Y')} – "
                f"{end.strftime('%B %d, %Y')})"
            )
        return f"{self.period_label} (effective January 2026)"

    def __repr__(self) -> str:
        return (
            f"FiscalYearConfig(program={self.program!r}, period={self.period_label}, "
            f"fpl_year={self.fpl_year}, calendar={self.calendar.value})"
        )


# Convenience: the default period for each program as of today (March 2026)
DEFAULT_SNAP_FY = 2026  # FY2026: Oct 1 2025 – Sep 30 2026 (uses 2025 HHS FPL)
DEFAULT_WIC_FY = 2026  # WIC IEG 2025-2026: Jul 1 2025 – Jun 30 2026 (uses 2025 HHS FPL)
DEFAULT_MEDICAID_CY = 2026  # CY2026: Jan 1–Dec 31 2026 (uses 2026 HHS FPL)
DEFAULT_FPL_YEAR_SNAP = 2025  # HHS FPL year underlying SNAP FY2026 and WIC 2025-2026
DEFAULT_FPL_YEAR_MEDICAID = 2026  # HHS FPL year underlying Medicaid CY2026
