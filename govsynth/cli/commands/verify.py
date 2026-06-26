"""govsynth verify-thresholds command.

Checks _metadata.verification_status in all bundled threshold JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from govsynth.cli.output import emit_json, make_console

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "thresholds"

_VERIFICATION_URLS: dict[str, str] = {
    "snap": "https://www.fns.usda.gov/snap/allotment/cola",
    "wic": "https://www.fns.usda.gov/wic/wic-income-eligibility-guidelines",
    "medicaid": "https://www.kff.org/medicaid/state-indicator/medicaid-income-eligibility-limits/",
    "us_fpl": "https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines",
}

_PROGRAM_NORMALIZATION: dict[str, str] = {
    "hhs_poverty_guidelines": "us_fpl",
}

_VALID_PROGRAMS = set(_VERIFICATION_URLS.keys())


def _get_program_key(meta: dict[str, Any]) -> str:
    """Extract and normalize the program key from file metadata."""
    raw: str = meta.get("program", meta.get("type", "unknown"))
    return _PROGRAM_NORMALIZATION.get(raw, raw)


def verify_thresholds(
    program: Annotated[
        str | None,
        typer.Option("--program", "-p", help="Filter: snap | wic | medicaid | us_fpl"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check verification status of bundled threshold JSON files."""
    console = make_console()

    if program is not None and program not in _VALID_PROGRAMS:
        console.print(
            f"[red]Error:[/red] Unknown program '{program}'. "
            f"Valid: {', '.join(sorted(_VALID_PROGRAMS))}"
        )
        raise typer.Exit(2)

    files = sorted(_DATA_DIR.glob("*.json"))
    checked = []
    unverified = []

    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("_metadata", {})
        prog_key = _get_program_key(meta)

        if program is not None and prog_key != program:
            continue

        status = meta.get("verification_status", "unknown")
        checked.append(path.name)

        if status != "verified":
            unverified.append(
                {
                    "file": path.name,
                    "verification_status": status,
                    "verify_url": _VERIFICATION_URLS.get(prog_key, ""),
                }
            )

    result = {
        "status": "ok" if not unverified else "needs_verification",
        "checked": len(checked),
        "unverified": unverified,
    }

    if as_json:
        emit_json(result)
    else:
        icon = "✅" if not unverified else "⚠️ "
        console.print(f"{icon} Checked {len(checked)} files — {len(unverified)} need verification.")
        for u in unverified:
            console.print(f"  [yellow]{u['file']}[/yellow]: {u['verification_status']}")
            if u["verify_url"]:
                console.print(f"   → Verify at: {u['verify_url']}")

    if unverified:
        raise typer.Exit(1)
