"""govsynth generate-catala command — generate synthetic data from an imported
Catala ruleset instead of a built-in preset.

Requires the `catala` CLI on PATH. See docs/catala-adapter.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from govsynth.cli.output import emit_status, make_console
from govsynth.generators.catala_eligibility import CatalaEligibilityGenerator
from govsynth.pipeline import Pipeline
from govsynth.sources.catala.ruleset import CatalaMappingError, CatalaRuleset
from govsynth.sources.catala.runtime import CatalaNotAvailableError, CatalaRuntimeError


def _parse_field_mapping(pairs: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise typer.BadParameter(
                f"--field-mapping expects CATALA_FIELD=PROFILE_FIELD, got '{pair}'"
            )
        catala_field, profile_field = pair.split("=", 1)
        mapping[catala_field.strip()] = profile_field.strip()
    return mapping


def generate_catala(
    ruleset: Annotated[
        Path, typer.Argument(help="Path to a .catala_en/.catala_fr ruleset file")
    ],
    scope: Annotated[str, typer.Option("--scope", help="Catala scope to run")],
    program: Annotated[
        str, typer.Option("--program", help="Program identifier, e.g. 'liheap'")
    ],
    output: Annotated[Path, typer.Option("--output", "-o", help="Output directory")],
    jurisdiction: Annotated[
        str, typer.Option("--jurisdiction", help="Jurisdiction prefix, e.g. 'us'")
    ] = "us",
    outcome_field: Annotated[
        str, typer.Option("--outcome-field", help="Scope output field representing eligibility")
    ] = "eligible",
    field_mapping: Annotated[
        list[str] | None,
        typer.Option(
            "--field-mapping",
            help="CATALA_FIELD=PROFILE_FIELD override for fields with mismatched names "
            "(repeatable)",
        ),
    ] = None,
    citation: Annotated[
        str, typer.Option("--citation", help="Source citation recorded on generated cases")
    ] = "",
    citation_year: Annotated[
        int | None, typer.Option("--citation-year", help="Year for the citation (default: now)")
    ] = None,
    binary: Annotated[
        str | None, typer.Option("--binary", help="Path to the catala executable")
    ] = None,
    state: Annotated[
        str, typer.Option("--state", help="State code recorded on generated scenarios")
    ] = "VA",
    n: Annotated[int, typer.Option("--n", "-n", help="Number of cases")] = 100,
    seed: Annotated[int | None, typer.Option(help="RNG seed")] = None,
    formats: Annotated[
        list[str] | None, typer.Option("--format", "-f", help="yaml|jsonl|csv (repeatable)")
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate synthetic test cases from an imported Catala ruleset."""
    if formats is None:
        formats = ["yaml"]
    console = make_console(quiet=quiet)

    try:
        loaded_ruleset = CatalaRuleset.load(
            ruleset,
            scope,
            program_name=program,
            jurisdiction=jurisdiction,
            field_mapping=_parse_field_mapping(field_mapping or []),
            outcome_field=outcome_field,
            citation=citation,
            citation_year=citation_year,
            binary=binary,
        )
        generator = CatalaEligibilityGenerator(loaded_ruleset, state=state)
    except (CatalaNotAvailableError, CatalaRuntimeError, CatalaMappingError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(2) from e

    pipeline = Pipeline(generator=generator, console=console)
    cases = pipeline.generate(n=n, seed=seed)
    pipeline.save(cases, output, formats=formats)

    emit_status(
        {
            "command": "generate-catala",
            "ruleset": str(ruleset),
            "scope": scope,
            "program": program,
            "n": len(cases),
            "output": str(output),
            "status": "ok",
        },
        as_json=as_json,
        console=console,
    )
