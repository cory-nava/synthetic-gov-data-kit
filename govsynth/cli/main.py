"""Top-level Typer app for govsynth CLI."""
from __future__ import annotations

import typer

app = typer.Typer(
    name="govsynth",
    help="Generate synthetic US government benefits test cases.",
    add_completion=False,
)


def _register_commands() -> None:
    from govsynth.cli.commands.batch import batch
    from govsynth.cli.commands.generate import generate
    from govsynth.cli.commands.generate_catala import generate_catala
    from govsynth.cli.commands.list_presets import app as list_presets_app
    from govsynth.cli.commands.parse_policy import parse_policy
    from govsynth.cli.commands.refresh_census import refresh_census_data
    from govsynth.cli.commands.show import show
    from govsynth.cli.commands.validate import validate
    from govsynth.cli.commands.verify import verify_thresholds

    app.add_typer(list_presets_app, name="list-presets")
    app.command("verify-thresholds")(verify_thresholds)
    app.command("generate")(generate)
    app.command("generate-catala")(generate_catala)
    app.command("batch")(batch)
    app.command("validate")(validate)
    app.command("show")(show)
    app.command("parse-policy")(parse_policy)
    app.command("refresh-census-data")(refresh_census_data)


_register_commands()


if __name__ == "__main__":
    app()
