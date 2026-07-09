"""Census Bureau ACS data fetcher.

Network-only module -- never imported at generation time.
All Census Bureau API calls go through httpx.

Typical usage (from refresh_census.py):
    data = build_state_census_json("VA", year=2022, api_key=None)
    write_state_file("VA", data, data_dir=Path("data/census"))
"""

from __future__ import annotations

import json
import math
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx

# FIPS codes: state abbreviation -> two-digit Census FIPS code
FIPS_CODES: dict[str, str] = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "DC": "11",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}

_BASE_URL = "https://api.census.gov/data"

# B19001: 16 annual income bracket midpoints (dollars)
_INCOME_BRACKET_MIDPOINTS = [
    5000,
    12500,
    17500,
    22500,
    27500,
    32500,
    37500,
    42500,
    47500,
    52500,
    57500,
    62500,
    70000,
    87500,
    112500,
    150000,
]
_INCOME_BRACKET_LABELS = [
    "Less than $10,000",
    "$10,000 to $14,999",
    "$15,000 to $19,999",
    "$20,000 to $24,999",
    "$25,000 to $29,999",
    "$30,000 to $34,999",
    "$35,000 to $39,999",
    "$40,000 to $44,999",
    "$45,000 to $49,999",
    "$50,000 to $54,999",
    "$55,000 to $59,999",
    "$60,000 to $64,999",
    "$65,000 to $74,999",
    "$75,000 to $99,999",
    "$100,000 to $124,999",
    "$125,000 or more",
]


def fit_lognormal(buckets: list[dict[str, Any]]) -> tuple[float, float]:
    """Fit lognormal (mu, sigma) to ACS income bracket weights.

    Uses method of moments on log-transformed monthly midpoints.
    Weights are normalized so they need not sum to 1.0.

    Args:
        buckets: list of {"annual_midpoint": float, "weight": float}
                 annual_midpoint is in dollars/year.

    Returns:
        (mu, sigma) for use in random.lognormvariate(mu, sigma),
        where the distribution is over monthly income.
    """
    total = sum(b["weight"] for b in buckets)
    weights = [b["weight"] / total for b in buckets]
    log_monthly = [math.log(b["annual_midpoint"] / 12) for b in buckets]

    mu = sum(w * lm for w, lm in zip(weights, log_monthly, strict=False))
    variance = sum(w * (lm - mu) ** 2 for w, lm in zip(weights, log_monthly, strict=False))
    sigma = math.sqrt(variance)

    return round(mu, 4), round(sigma, 4)


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> list[list[str]]:
    """Make one Census API GET; return parsed JSON rows."""
    resp = client.get(url, params=params, timeout=30)
    resp.raise_for_status()
    result: list[list[str]] = resp.json()
    return result


def _safe_int(val: str) -> int:
    """Parse Census API value to int; return 0 on error or negative."""
    try:
        return max(0, int(val))
    except (ValueError, TypeError):
        return 0


def _safe_rate(numerator: int, denominator: int) -> float:
    """Return numerator/denominator rounded to 4 places; 0.0 if denom is 0."""
    return round(numerator / denominator, 4) if denominator > 0 else 0.0


def fetch_state(state: str, year: int, api_key: str | None) -> dict[str, Any]:
    """Fetch all ACS tables for one state (~6 HTTP requests).

    Args:
        state: Two-letter state abbreviation, e.g. 'VA'.
        year:  ACS vintage year, e.g. 2022.
        api_key: Census API key or None (unauthenticated: 500 req/day).

    Returns:
        Raw response dict keyed by table group name.

    Raises:
        KeyError: Unknown state abbreviation.
        httpx.HTTPStatusError: On Census API HTTP errors.
    """
    fips = FIPS_CODES[state.upper()]
    base_url = f"{_BASE_URL}/{year}/acs/acs5"
    geo = f"state:{fips}"
    base_params: dict[str, str] = {"for": geo}
    if api_key:
        base_params["key"] = api_key

    with httpx.Client() as client:
        # B19001: Household income -- 16 brackets (variables _002E through _017E)
        income_vars = ",".join(f"B19001_0{i:02d}E" for i in range(2, 18))
        income_rows = _get(client, base_url, {**base_params, "get": income_vars})

        # B17024: Ratio of income to poverty level -- key FPL buckets
        poverty_vars = "B17024_002E,B17024_003E,B17024_004E,B17024_005E,B17024_001E"
        poverty_rows = _get(client, base_url, {**base_params, "get": poverty_vars})

        # B25064: Median gross rent; B25070: Rent burden; B25003: Tenure
        housing_vars = "B25064_001E,B25070_007E,B25070_008E,B25070_009E,B25070_010E,B25070_001E,B25003_003E,B25003_001E"
        housing_rows = _get(client, base_url, {**base_params, "get": housing_vars})

        # Household size (B11016), children (B11003), age (B01001),
        # citizenship (B05001), disability (B18101)
        demo_vars = (
            "B11016_003E,B11016_004E,B11016_005E,B11016_006E,B11016_007E,"
            "B11016_010E,B11016_011E,B11016_012E,B11016_013E,B11016_001E,"
            "B11003_003E,B11003_007E,B11003_001E,"
            "B01001_001E,"
            "B01001_020E,B01001_021E,B01001_022E,B01001_023E,B01001_024E,B01001_025E,"
            "B01001_044E,B01001_045E,B01001_046E,B01001_047E,B01001_048E,B01001_049E,"
            "B05001_002E,B05001_006E,B05001_001E,"
            "B18101_004E,B18101_007E,B18101_010E,B18101_023E,B18101_026E,B18101_029E,B18101_001E"
        )
        demo_rows = _get(client, base_url, {**base_params, "get": demo_vars})

        # Income sources: SNAP (B22003), employment (B23025),
        # Social Security (B19055), SSI (B19056), public assistance (B19057)
        isrc_vars = (
            "B22003_002E,B22003_001E,"
            "B23025_002E,B23025_001E,"
            "B19055_002E,B19055_001E,"
            "B19056_002E,B19056_001E,"
            "B19057_002E,B19057_001E"
        )
        isrc_rows = _get(client, base_url, {**base_params, "get": isrc_vars})

        # Health insurance coverage -- B27001 (medicaid approximation)
        health_vars = "B27001_004E,B27001_007E,B27001_010E,B27001_013E,B27001_016E,B27001_001E"
        health_rows = _get(client, base_url, {**base_params, "get": health_vars})

    return {
        "income": income_rows,
        "poverty": poverty_rows,
        "housing": housing_rows,
        "demographics": demo_rows,
        "income_sources": isrc_rows,
        "health": health_rows,
    }


def _idx(header: list[str], var: str) -> int:
    """Return column index for a Census variable name."""
    return header.index(var)


def build_state_census_json(state: str, year: int, api_key: str | None) -> dict[str, Any]:
    """Fetch ACS data for one state and build the census JSON dict.

    Args:
        state: Two-letter state abbreviation.
        year:  ACS vintage year.
        api_key: Census API key or None.

    Returns:
        Dict matching the schema in data/census/<state>.json.
    """
    raw = fetch_state(state, year, api_key)

    # -- Income --
    inc_h, inc_v = raw["income"][0], raw["income"][1]
    bracket_counts = [_safe_int(inc_v[_idx(inc_h, f"B19001_0{i:02d}E")]) for i in range(2, 18)]
    total_hh_inc = sum(bracket_counts) or 1
    buckets_raw = [
        {
            "label": _INCOME_BRACKET_LABELS[i],
            "annual_midpoint": _INCOME_BRACKET_MIDPOINTS[i],
            "weight": round(c / total_hh_inc, 4),
        }
        for i, c in enumerate(bracket_counts)
    ]
    mu, sigma = fit_lognormal(buckets_raw)

    # -- FPL buckets --
    pov_h, pov_v = raw["poverty"][0], raw["poverty"][1]
    pop_ratio = _safe_int(pov_v[_idx(pov_h, "B17024_001E")]) or 1
    fpl_buckets = [
        {
            "fpl_pct": 0.50,
            "weight": _safe_rate(_safe_int(pov_v[_idx(pov_h, "B17024_002E")]), pop_ratio),
        },
        {
            "fpl_pct": 1.00,
            "weight": _safe_rate(_safe_int(pov_v[_idx(pov_h, "B17024_003E")]), pop_ratio),
        },
        {
            "fpl_pct": 1.30,
            "weight": _safe_rate(_safe_int(pov_v[_idx(pov_h, "B17024_004E")]), pop_ratio),
        },
        {
            "fpl_pct": 1.85,
            "weight": _safe_rate(_safe_int(pov_v[_idx(pov_h, "B17024_005E")]), pop_ratio),
        },
    ]

    # -- Housing --
    hsg_h, hsg_v = raw["housing"][0], raw["housing"][1]
    median_rent = _safe_int(hsg_v[_idx(hsg_h, "B25064_001E")])
    burden_high = sum(_safe_int(hsg_v[_idx(hsg_h, f"B25070_0{i:02d}E")]) for i in [7, 8, 9, 10])
    burden_total = _safe_int(hsg_v[_idx(hsg_h, "B25070_001E")]) or 1
    renter_occ = _safe_int(hsg_v[_idx(hsg_h, "B25003_003E")])
    total_occ = _safe_int(hsg_v[_idx(hsg_h, "B25003_001E")]) or 1

    # -- Demographics --
    dem_h, dem_v = raw["demographics"][0], raw["demographics"][1]

    def hh_c(var: str) -> int:
        return _safe_int(dem_v[_idx(dem_h, var)])

    # Household size 1-6:
    # 1 = nonfamily 1-person (B11016_010E)
    # 2 = family-2 (B11016_003E) + nonfamily-2 (B11016_011E)
    # 3 = family-3 (B11016_004E) + nonfamily-3 (B11016_012E)
    # 4 = family-4 (B11016_005E) + nonfamily-4+ (B11016_013E, grouped)
    # 5 = family-5 (B11016_006E)
    # 6 = family-6 (B11016_007E)
    raw_hh = [
        hh_c("B11016_010E"),
        hh_c("B11016_003E") + hh_c("B11016_011E"),
        hh_c("B11016_004E") + hh_c("B11016_012E"),
        hh_c("B11016_005E") + hh_c("B11016_013E"),
        hh_c("B11016_006E"),
        hh_c("B11016_007E"),
    ]
    hh_total = sum(raw_hh) or 1
    hh_weights = [round(c / hh_total, 4) for c in raw_hh]
    # Re-normalize to exactly 1.0 by adjusting last element
    hh_weights[-1] = round(hh_weights[-1] + (1.0 - sum(hh_weights)), 4)

    # Children
    children = hh_c("B11003_003E") + hh_c("B11003_007E")
    fam_total = hh_c("B11003_001E") or 1

    # Elderly/disabled (65+ from B01001 + disabled from B18101)
    elderly_male = sum(hh_c(f"B01001_0{i:02d}E") for i in range(20, 26))
    elderly_female = sum(hh_c(f"B01001_0{i:02d}E") for i in range(44, 50))
    total_pop = hh_c("B01001_001E") or 1
    disabled = sum(hh_c(f"B18101_0{i:02d}E") for i in [4, 7, 10, 23, 26, 29])
    disabled_total = hh_c("B18101_001E") or 1
    pct_eld_dis = min(
        _safe_rate(elderly_male + elderly_female + disabled, total_pop + disabled_total),
        0.40,
    )

    # Citizenship
    native_citizen = hh_c("B05001_002E")
    noncit_eligible = hh_c("B05001_006E")
    pop_b05 = hh_c("B05001_001E") or 1

    # Age: national approximate (ACS median ~38-42; use 40 with sigma 15)
    age_mu = 40.0
    age_sigma = 15.0

    # -- Income sources --
    isrc_h, isrc_v = raw["income_sources"][0], raw["income_sources"][1]

    def ir(var: str) -> int:
        return _safe_int(isrc_v[_idx(isrc_h, var)])

    snap_recv = ir("B22003_002E")
    snap_total = ir("B22003_001E") or 1
    employed = ir("B23025_002E")
    labor_total = ir("B23025_001E") or 1
    ss_recv = ir("B19055_002E")
    ss_total = ir("B19055_001E") or 1
    ssi_recv = ir("B19056_002E")
    ssi_total = ir("B19056_001E") or 1
    pa_recv = ir("B19057_002E")
    pa_total = ir("B19057_001E") or 1

    # -- Health / Medicaid approx --
    hlth_h, hlth_v = raw["health"][0], raw["health"][1]

    def hr(var: str) -> int:
        return _safe_int(hlth_v[_idx(hlth_h, var)])

    insured = sum(hr(f"B27001_0{i:02d}E") for i in [4, 7, 10, 13, 16])
    hlth_total = hr("B27001_001E") or 1

    return {
        "_metadata": {
            "state": state.upper(),
            "acs_vintage": year,
            "acs_survey": "acs5",
            "fetch_date": date.today().isoformat(),
            "tables": [
                "B19001",
                "B17024",
                "B25064",
                "B25070",
                "B25003",
                "B11016",
                "B11003",
                "B01001",
                "B05001",
                "B18101",
                "B22003",
                "B27001",
                "B23025",
                "B19055",
                "B19056",
                "B19057",
            ],
        },
        "income": {
            "monthly_lognormal": {"mu": mu, "sigma": sigma},
            "fpl_buckets": fpl_buckets,
            "buckets_raw": buckets_raw,
        },
        "housing": {
            "median_gross_rent_monthly": median_rent,
            "pct_renter": _safe_rate(renter_occ, total_occ),
            "rent_burden_pct": _safe_rate(burden_high, burden_total),
        },
        "household_size": {
            "weights": hh_weights,
        },
        "demographics": {
            "pct_with_children": _safe_rate(children, fam_total),
            "pct_elderly_or_disabled": pct_eld_dis,
            "pct_citizen": _safe_rate(native_citizen, pop_b05),
            "pct_noncitizen_eligible": _safe_rate(noncit_eligible, pop_b05),
            "age": {"mu": age_mu, "sigma": age_sigma},
        },
        "income_sources": {
            "labor_force_participation_rate": _safe_rate(employed, labor_total),
            "pct_social_security": _safe_rate(ss_recv, ss_total),
            "pct_ssi": _safe_rate(ssi_recv, ssi_total),
            "pct_public_assistance": _safe_rate(pa_recv, pa_total),
        },
        "program_participation": {
            "snap_receipt_rate": _safe_rate(snap_recv, snap_total),
            "medicaid_coverage_rate": _safe_rate(insured, hlth_total),
        },
    }


def write_state_file(state: str, data: dict[str, Any], data_dir: Path) -> Path:
    """Write state census JSON atomically (tmp file -> os.replace).

    Args:
        state: Two-letter state code.
        data:  Dict from build_state_census_json.
        data_dir: Target directory (created if absent).

    Returns:
        Path to the written file.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{state.lower()}.json"
    target = data_dir / filename
    tmp = data_dir / f"{filename}.tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    os.replace(tmp, target)
    return target
