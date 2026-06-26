"""govsynth refresh-census-data command."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from govsynth.cli.output import make_console

_DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "census"

_ALL_STATES = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
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
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
]


def refresh_census_data(
    state: Annotated[
        str | None,
        typer.Option("--state", "-s", help="Single state code, e.g. VA"),
    ] = None,
    year: Annotated[int, typer.Option("--year", help="ACS vintage year")] = 2022,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Fetch ACS Census data and update data/census/ JSON files."""
    console = make_console()
    api_key: str | None = os.environ.get("CENSUS_API_KEY")

    if state is not None and state.upper() not in _ALL_STATES:
        console.print(
            f"[red]Error:[/red] Unknown state '{state}'. Use a two-letter US state/DC code."
        )
        raise typer.Exit(2)

    states = [state.upper()] if state else list(_ALL_STATES)

    # Confirmation prompt for all-states (non-dry-run only)
    if not state and not dry_run:
        n_calls = len(states) * 6
        key_note = (
            "CENSUS_API_KEY not set — unauthenticated limit is 500 req/day."
            if not api_key
            else "CENSUS_API_KEY is set."
        )
        console.print(
            f"[yellow]Warning:[/yellow] This will make ~{n_calls} API calls "
            f"({len(states)} states/DC × 6 table groups).\n{key_note}"
        )
        if not typer.confirm("Continue?", default=False):
            raise typer.Exit(0)

    if dry_run:
        for s in states:
            out_path = str(_DATA_DIR / f"{s.lower()}.json")
            msg: dict[str, Any] = {
                "command": "refresh-census-data",
                "state": s,
                "status": "dry_run",
                "would_write": out_path,
            }
            if as_json:
                print(json.dumps(msg), file=sys.stderr)
            else:
                console.print(f"[dim]dry-run[/dim]  {s} → {out_path}")
        return

    from govsynth.sources.us.census_fetcher import build_state_census_json, write_state_file

    ok: list[str] = []
    failed: list[str] = []

    for s in states:
        try:
            data = build_state_census_json(s, year=year, api_key=api_key)
            path = write_state_file(s, data, _DATA_DIR)
            ok.append(s)
            msg = {
                "command": "refresh-census-data",
                "state": s,
                "status": "ok",
                "output_file": str(path),
            }
            if as_json:
                print(json.dumps(msg), file=sys.stderr)
            else:
                console.print(f"[green]✓[/green] {s} → {path}")

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                console.print(f"[yellow]Rate limited on {s}, retrying in 10s...[/yellow]")
                time.sleep(10)
                try:
                    data = build_state_census_json(s, year=year, api_key=api_key)
                    path = write_state_file(s, data, _DATA_DIR)
                    ok.append(s)
                    if as_json:
                        print(
                            json.dumps(
                                {
                                    "command": "refresh-census-data",
                                    "state": s,
                                    "status": "ok",
                                    "output_file": str(path),
                                }
                            ),
                            file=sys.stderr,
                        )
                    else:
                        console.print(f"[green]✓[/green] {s} → {path} (retry)")
                    continue
                except Exception:
                    pass
            failed.append(s)
            err: dict[str, Any] = {
                "command": "refresh-census-data",
                "state": s,
                "status": "error",
                "error": f"HTTP {exc.response.status_code}",
            }
            if as_json:
                print(json.dumps(err), file=sys.stderr)
            else:
                console.print(f"[red]✗[/red] {s}: HTTP {exc.response.status_code}")

        except Exception as exc:  # noqa: BLE001
            failed.append(s)
            err = {
                "command": "refresh-census-data",
                "state": s,
                "status": "error",
                "error": str(exc)[:200],
            }
            if as_json:
                print(json.dumps(err), file=sys.stderr)
            else:
                console.print(f"[red]✗[/red] {s}: {exc}")

    # Summary line
    status = "ok" if not failed else ("partial" if ok else "error")
    summary: dict[str, Any] = {
        "command": "refresh-census-data",
        "summary": {"total": len(states), "ok": len(ok), "failed": len(failed)},
        "status": status,
    }
    if as_json:
        print(json.dumps(summary), file=sys.stderr)
    else:
        if failed:
            console.print(
                f"[yellow]Warning:[/yellow] {len(failed)} state(s) failed: {', '.join(failed)}"
            )
        console.print(f"[green]Done:[/green] {len(ok)}/{len(states)} states refreshed.")

    if failed and not ok:
        raise typer.Exit(1)
