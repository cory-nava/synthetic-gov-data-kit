"""Shared output helpers for govsynth CLI commands.

Two patterns:
  emit_json()   — query commands (list-presets, verify-thresholds, show --json) → stdout
  emit_status() — data-producing commands (generate, batch, validate) → stderr envelope
"""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console


def make_console(quiet: bool = False) -> Console:
    """Return a stderr Console, optionally silenced for CI use."""
    return Console(stderr=True, quiet=quiet)


def emit_json(data: dict[str, Any] | list[Any]) -> None:
    """Print data as formatted JSON to stdout.

    Used by query commands: list-presets, verify-thresholds, show --json.
    """
    print(json.dumps(data, indent=2, default=str), file=sys.stdout)


def emit_status(
    data: dict[str, Any],
    *,
    as_json: bool,
    console: Console,
) -> None:
    """Print a status envelope.

    If as_json: writes JSON to stderr (for scripting/agents).
    Otherwise: writes a Rich-formatted summary to the injected console.

    Used by data-producing commands: generate, batch, validate.
    """
    if as_json:
        print(json.dumps(data, default=str), file=sys.stderr)
    else:
        status = data.get("status", "ok")
        if status == "error":
            console.print(f"[red]Error:[/red] {data.get('message', str(data))}")
        else:
            parts = [f"{k}={v}" for k, v in data.items() if k != "status"]
            console.print(f"[green]✓[/green] status={status} {' '.join(parts)}".rstrip())
