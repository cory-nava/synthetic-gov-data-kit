"""govsynth show command — pretty-print a single case from an output file."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from govsynth.cli.output import emit_json, make_console
from govsynth.cli.readers import detect_format, read_yaml
from govsynth.formatters.yaml_fmt import YAMLFormatter


def show(
    file: Annotated[Path, typer.Argument(help="Output file (.yaml only for full display)")],
    case_id: Annotated[str | None, typer.Argument(help="Case ID; shows first case if omitted")] = None,
    raw: Annotated[bool, typer.Option("--raw", help="Print raw YAML to stdout")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print case as JSON to stdout")] = False,
    format_: Annotated[str | None, typer.Option("--format", "-f", help="Force format detection")] = None,
) -> None:
    """Pretty-print a single case from a YAML output file."""
    console = make_console()
    fmt = detect_format(file, force=format_)

    if fmt in ("jsonl", "csv"):
        console.print(
            f"[red]Error:[/red] 'show' only supports YAML files. "
            f"'{file.name}' is {fmt.upper()} (lossy format — cannot reconstruct full case)."
        )
        raise typer.Exit(2)

    try:
        cases = read_yaml(file)
    except Exception as e:
        console.print(f"[red]Error reading {file.name}:[/red] {e}")
        raise typer.Exit(1) from e

    if not cases:
        console.print(f"[red]No cases found in {file.name}[/red]")
        raise typer.Exit(1)

    if case_id is not None:
        matched = [c for c in cases if c.case_id == case_id]
        if not matched:
            console.print(f"[red]Case '{case_id}' not found in {file.name}[/red]")
            raise typer.Exit(1)
        case = matched[0]
    else:
        case = cases[0]

    if as_json:
        emit_json(json.loads(case.model_dump_json()))
        return

    fmt_obj = YAMLFormatter()
    yaml_text = fmt_obj.format_one(case)

    if raw:
        sys.stdout.write(yaml_text)
        return

    # Rich pretty-print to stderr console
    from rich.panel import Panel
    from rich.syntax import Syntax

    content = Syntax(yaml_text, "yaml", theme="monokai", line_numbers=False)
    console.print(Panel(content, title=f"[bold]{case.case_id}[/bold]", expand=False))
