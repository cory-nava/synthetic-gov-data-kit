#!/usr/bin/env python3
"""Threshold verification and update helper.

Usage:
    python scripts/verify_thresholds.py          # Check all current thresholds
    python scripts/verify_thresholds.py --list   # List all threshold files + status

This script checks threshold files for verification status and prints
links to official sources for manual verification.

It does NOT auto-update thresholds — policy numbers require human verification
against official government publications. Run this before any CivBench release.
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "thresholds"

VERIFICATION_URLS = {
    "snap": "https://www.fns.usda.gov/snap/allotment/cola",
    "wic": "https://www.fns.usda.gov/wic/wic-income-eligibility-guidelines",
    "medicaid": "https://www.kff.org/medicaid/state-indicator/medicaid-income-eligibility-limits/",
    "us_fpl": "https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines",
}


def check_all() -> None:
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        print("No threshold files found in data/thresholds/")
        return

    all_verified = True
    print(f"\n{'File':<40} {'Status':<12} {'Period':<10} {'Program'}")
    print("-" * 80)

    for path in files:
        with open(path) as f:
            data = json.load(f)

        meta = data.get("_metadata", {})
        status = meta.get("verification_status", "unknown")
        period = meta.get("period", meta.get("year", "?"))
        program = meta.get("program", meta.get("type", path.stem.split("_")[0]))

        icon = "✅" if status == "verified" else "⚠️ "
        print(f"{icon} {path.name:<38} {status:<12} {str(period):<10} {program}")

        if status != "verified":
            all_verified = False
            url = VERIFICATION_URLS.get(program, "")
            if url:
                print(f"   → Verify at: {url}")

    print()
    if all_verified:
        print("✅ All threshold files are verified.")
    else:
        print("⚠️  Some threshold files need verification before use in production evals.")
        print("   See source URLs above. After verifying, update verification_status to 'verified'.")
    print()


def show_snap_fy2026_summary() -> None:
    """Print a quick summary of the SNAP FY2026 table for spot-checking."""
    path = DATA_DIR / "snap_fy2026.json"
    if not path.exists():
        print("snap_fy2026.json not found")
        return

    with open(path) as f:
        data = json.load(f)

    hh = data["households_48_states_dc"]
    print("\nSNAP FY2026 — 48 States + DC (USDA FNS COLA Memo, Aug 13 2025)")
    print(f"{'HH Size':<10} {'Gross (130% FPL)':<20} {'Net (100% FPL)':<18} {'Max Benefit'}")
    print("-" * 65)
    for size in ["1", "2", "3", "4", "5", "6", "7", "8"]:
        row = hh[size]
        print(
            f"{size:<10} ${row['gross_monthly']:>8,.0f}/month      "
            f"${row['net_monthly']:>8,.0f}/month   ${row['max_benefit']:>6,.0f}"
        )
    ea = hh["each_additional"]
    print(
        f"{'Each add.':<10} +${ea['gross_monthly']:>7,.0f}/month      "
        f"+${ea['net_monthly']:>7,.0f}/month  +${ea['max_benefit']:>5,.0f}"
    )
    print(
        f"\nAsset limits: ${data['asset_limit_general']:,} general / "
        f"${data['asset_limit_elderly_disabled']:,} elderly+disabled"
    )
    print(f"Standard deduction (HH 1-3): ${data['standard_deductions_48_states_dc']['1']}/month")
    print(f"Excess shelter cap: ${data['excess_shelter_deduction_cap_48_states_dc']}/month")
    print(f"Homeless shelter deduction: ${data['homeless_shelter_deduction']}/month")
    print(f"Minimum benefit: ${data['minimum_benefit_48_states_dc']}/month")
    print(f"\nVerification: {data['_metadata']['verification_status'].upper()}")
    print(f"Source: {data['_metadata']['source']}\n")


if __name__ == "__main__":
    if "--list" in sys.argv or len(sys.argv) == 1:
        check_all()
    if "--snap" in sys.argv or len(sys.argv) == 1:
        show_snap_fy2026_summary()
