"""govsynth parse-policy command — stub (not yet implemented)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from govsynth.cli.output import make_console


def parse_policy(
    file: Annotated[Path, typer.Argument(help="PDF or DOCX policy document")],
    program: Annotated[
        str | None, typer.Option("--program", "-p", help="Program hint: snap|wic|medicaid|...")
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Where to write threshold JSON")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """(Roadmap) Extract threshold tables from a PDF or DOCX policy document."""
    console = make_console()
    console.print(
        "[yellow]⚠  parse-policy is not yet implemented.[/yellow]\n"
        "   See docs/bring-your-own-policy.md for manual threshold JSON instructions."
    )
