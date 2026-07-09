"""govsynth batch command — multi-preset case generation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from govsynth.cli.output import emit_status, make_console
from govsynth.pipeline import Pipeline
from govsynth.presets import PRESETS


def batch(
    presets: Annotated[list[str], typer.Option("--preset", "-p", help="Preset name (repeatable)")],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output directory")],
    n: Annotated[int, typer.Option("--n", help="Cases per preset")] = 100,
    seed: Annotated[int | None, typer.Option(help="Base RNG seed")] = None,
    formats: Annotated[list[str] | None, typer.Option("--format", "-f", help="yaml|jsonl|csv (repeatable)")] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate cases across multiple presets and save to a directory."""
    if formats is None:
        formats = ["yaml"]
    console = make_console(quiet=quiet)

    # Validate all preset names up front — exit 2 immediately if any unknown
    unknown = [p for p in presets if p not in PRESETS]
    if unknown:
        console.print(
            f"[red]Error:[/red] Unknown preset(s): {', '.join(unknown)}. "
            f"Run 'govsynth list-presets' to see available presets."
        )
        raise typer.Exit(2)

    all_cases = []
    last_pipeline: Pipeline | None = None
    for i, preset_name in enumerate(presets):
        last_pipeline = Pipeline.from_preset(preset_name, console=console)
        preset_seed = (seed + i) if seed is not None else None
        cases = last_pipeline.generate(n=n, seed=preset_seed)
        all_cases.extend(cases)

    # Re-use the last pipeline for save — save() does not use self.generator,
    # so any pipeline instance works. This avoids constructing a bare Pipeline(generator=None).
    assert last_pipeline is not None  # guaranteed: unknown presets are rejected above
    last_pipeline.save(all_cases, output, formats=formats)

    emit_status(
        {
            "command": "batch",
            "presets": presets,
            "total_cases": len(all_cases),
            "output": str(output),
            "status": "ok",
        },
        as_json=as_json,
        console=console,
    )
