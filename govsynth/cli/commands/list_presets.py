"""govsynth list-presets command."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from govsynth.cli.output import emit_json, make_console
from govsynth.presets import PRESETS

app = typer.Typer(help="List all available generation presets.")


@app.callback(invoke_without_command=True)
def list_presets(
    as_json: Annotated[bool, typer.Option("--json", help="Emit JSON to stdout")] = False,
) -> None:
    """List all registered presets with program, state, and profile strategy."""
    presets_data = []
    for name, cfg in sorted(PRESETS.items()):
        state = cfg.generator_kwargs.get("state", "national")
        presets_data.append(
            {
                "preset": name,
                "program": cfg.program,
                "state": state,
                "strategy": cfg.profile_strategy,
                "description": cfg.description,
            }
        )

    if as_json:
        emit_json(presets_data)
        return

    console = make_console()
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Preset")
    table.add_column("Program")
    table.add_column("State")
    table.add_column("Strategy")
    table.add_column("Description")

    for item in presets_data:
        table.add_row(
            item["preset"],
            item["program"],
            item["state"],
            item["strategy"],
            item["description"],
        )

    console.print(table)
