"""Integration tests for govsynth show."""

import json
from pathlib import Path

from govsynth.cli.main import app
from govsynth.pipeline import Pipeline
from typer.testing import CliRunner

runner = CliRunner()


def _yaml_file(tmp_path: Path) -> tuple[Path, str]:
    """Generate a single YAML case file, return (path, case_id)."""
    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=1, seed=42)
    out = tmp_path / "out"
    pipeline.save(cases, out, formats=["yaml"])
    f = next(out.glob("*.yaml"))
    return f, cases[0].case_id


def test_show_displays_case(tmp_path):
    f, case_id = _yaml_file(tmp_path)
    result = runner.invoke(app, ["show", str(f)])
    assert result.exit_code == 0
    # Rich panel goes to stderr (make_console writes to stderr)
    assert case_id in result.stderr


def test_show_raw_flag_outputs_yaml(tmp_path):
    f, _ = _yaml_file(tmp_path)
    result = runner.invoke(app, ["show", str(f), "--raw"])
    assert result.exit_code == 0
    assert "case_id:" in result.output


def test_show_json_flag_outputs_json(tmp_path):
    f, case_id = _yaml_file(tmp_path)
    result = runner.invoke(app, ["show", str(f), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["case_id"] == case_id


def test_show_csv_format_exits_two(tmp_path):
    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=1, seed=42)
    out = tmp_path / "out"
    pipeline.save(cases, out, formats=["csv"])
    csv_file = next(out.glob("*.csv"))
    result = runner.invoke(app, ["show", str(csv_file)])
    assert result.exit_code == 2


def test_show_unknown_case_id_exits_one(tmp_path):
    f, _ = _yaml_file(tmp_path)
    result = runner.invoke(app, ["show", str(f), "does.not.exist"])
    assert result.exit_code == 1
