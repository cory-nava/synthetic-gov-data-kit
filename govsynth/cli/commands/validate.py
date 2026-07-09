"""govsynth validate command — validate output files against TestCase schema."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from govsynth.cli.output import emit_status, make_console
from govsynth.cli.readers import detect_format, read_csv_raw, read_jsonl_raw, read_yaml


def validate(
    file: Annotated[Path, typer.Argument(help="Output file to validate (.yaml/.jsonl/.csv)")],
    format_: Annotated[str | None, typer.Option("--format", "-f", help="Force format detection")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate a generated output file against the TestCase schema."""
    console = make_console(quiet=quiet)
    fmt = detect_format(file, force=format_)

    valid_count = 0
    total_count = 0
    errors: list[str] = []

    if fmt == "yaml":
        try:
            cases = read_yaml(file)
        except Exception as e:
            console.print(f"[red]Error reading {file.name}:[/red] {e}")
            raise typer.Exit(1) from e

        total_count = len(cases)
        for case in cases:
            errs = case.check_output_contract()
            if errs:
                errors.append(f"{case.case_id}: {'; '.join(errs)}")
            else:
                valid_count += 1

        console.print(f"Validating {file.name} ... {valid_count}/{total_count} valid")
        for err in errors:
            console.print(f"  [red]✗[/red] {err}")

    elif fmt == "jsonl":
        rows = read_jsonl_raw(file)
        total_count = len(rows)
        required_keys = {"case_id", "messages", "metadata"}
        for row in rows:
            if required_keys.issubset(row.keys()):
                valid_count += 1
            else:
                missing = required_keys - row.keys()
                errors.append(f"row missing keys: {missing}")
        console.print(f"Validating {file.name} (JSONL structural check) ... {valid_count}/{total_count} valid")

    elif fmt == "csv":
        rows = read_csv_raw(file)
        total_count = len(rows)
        required_cols = {"case_id", "program", "expected_outcome"}
        for row in rows:
            if required_cols.issubset(row.keys()):
                valid_count += 1
            else:
                missing = required_cols - row.keys()
                errors.append(f"row missing columns: {missing}")
        console.print(f"Validating {file.name} (CSV structural check) ... {valid_count}/{total_count} valid")

    status_data = {
        "status": "ok" if not errors else "invalid",
        "file": str(file),
        "format": fmt,
        "valid": valid_count,
        "total": total_count,
        "errors": errors,
    }

    emit_status(status_data, as_json=as_json, console=console)

    if errors:
        raise typer.Exit(1)
