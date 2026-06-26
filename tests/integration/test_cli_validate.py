"""Integration tests for govsynth validate."""

import json
from pathlib import Path

from govsynth.cli.main import app
from govsynth.pipeline import Pipeline
from typer.testing import CliRunner

runner = CliRunner()


def _generate_yaml(tmp_path: Path, n: int = 3) -> Path:
    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=n, seed=42)
    out = tmp_path / "out"
    pipeline.save(cases, out, formats=["yaml"])
    return out


def test_validate_valid_yaml_exits_zero(tmp_path):
    out_dir = _generate_yaml(tmp_path)
    yaml_file = next(out_dir.glob("*.yaml"))
    result = runner.invoke(app, ["validate", str(yaml_file)])
    assert result.exit_code == 0


def test_validate_shows_valid_count(tmp_path):
    out_dir = _generate_yaml(tmp_path)
    yaml_file = next(out_dir.glob("*.yaml"))
    result = runner.invoke(app, ["validate", str(yaml_file)])
    # Rich progress bar may wrap "1/1 valid" across lines in test environments
    stderr = result.stderr.replace("\n", " ")
    assert "1/1" in stderr and "valid" in stderr


def test_validate_unknown_extension_exits_two(tmp_path):
    f = tmp_path / "cases.txt"
    f.write_text("garbage")
    result = runner.invoke(app, ["validate", str(f)])
    assert result.exit_code == 2


def test_validate_jsonl_structural_only(tmp_path):
    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=2, seed=42)
    out = tmp_path / "out"
    pipeline.save(cases, out, formats=["jsonl"])
    jsonl_file = next(out.glob("*.jsonl"))
    result = runner.invoke(app, ["validate", str(jsonl_file)])
    assert result.exit_code == 0
    assert "structural" in result.stderr.lower() or "jsonl" in result.stderr.lower()


def test_validate_json_flag_emits_status_to_stderr(tmp_path):
    out_dir = _generate_yaml(tmp_path)
    yaml_file = next(out_dir.glob("*.yaml"))
    # --quiet ensures only the JSON status line goes to stderr
    result = runner.invoke(app, ["validate", str(yaml_file), "--json", "--quiet"])
    assert result.exit_code == 0
    status = json.loads(result.stderr)
    assert status["status"] == "ok"
    assert "valid" in status
