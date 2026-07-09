"""Integration tests for govsynth validate."""

import json
from pathlib import Path

from govsynth.cli.main import app
from govsynth.formatters.yaml_fmt import YAMLFormatter
from govsynth.pipeline import Pipeline
from typer.testing import CliRunner

runner = CliRunner()


def _generate_yaml(tmp_path: Path, n: int = 3) -> Path:
    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=n, seed=42)
    out = tmp_path / "out"
    pipeline.save(cases, out, formats=["yaml"])
    return out


def test_validate_valid_yaml_exits_zero(tmp_path: Path) -> None:
    out_dir = _generate_yaml(tmp_path)
    yaml_file = next(out_dir.glob("*.yaml"))
    result = runner.invoke(app, ["validate", str(yaml_file)])
    assert result.exit_code == 0


def test_validate_shows_valid_count(tmp_path: Path) -> None:
    out_dir = _generate_yaml(tmp_path)
    yaml_file = next(out_dir.glob("*.yaml"))
    result = runner.invoke(app, ["validate", str(yaml_file)])
    # Rich progress bar may wrap "1/1 valid" across lines in test environments
    stderr = result.stderr.replace("\n", " ")
    assert "1/1" in stderr and "valid" in stderr


def test_validate_unknown_extension_exits_two(tmp_path: Path) -> None:
    f = tmp_path / "cases.txt"
    f.write_text("garbage")
    result = runner.invoke(app, ["validate", str(f)])
    assert result.exit_code == 2


def test_validate_jsonl_structural_only(tmp_path: Path) -> None:
    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=2, seed=42)
    out = tmp_path / "out"
    pipeline.save(cases, out, formats=["jsonl"])
    jsonl_file = next(out.glob("*.jsonl"))
    result = runner.invoke(app, ["validate", str(jsonl_file)])
    assert result.exit_code == 0
    assert "structural" in result.stderr.lower() or "jsonl" in result.stderr.lower()


def test_validate_json_flag_emits_status_to_stderr(tmp_path: Path) -> None:
    out_dir = _generate_yaml(tmp_path)
    yaml_file = next(out_dir.glob("*.yaml"))
    # --quiet ensures only the JSON status line goes to stderr
    result = runner.invoke(app, ["validate", str(yaml_file), "--json", "--quiet"])
    assert result.exit_code == 0
    status = json.loads(result.stderr)
    assert status["status"] == "ok"
    assert "valid" in status


def test_validate_malformed_yaml_exits_one(tmp_path: Path) -> None:
    bad_file = tmp_path / "broken.yaml"
    bad_file.write_text("not: [a, valid, test, case")
    result = runner.invoke(app, ["validate", str(bad_file)])
    assert result.exit_code == 1
    assert "Error reading" in result.stderr


def test_validate_csv_valid_rows(tmp_path: Path) -> None:
    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=2, seed=42)
    out = tmp_path / "out"
    pipeline.save(cases, out, formats=["csv"])
    csv_file = next(out.glob("*.csv"))
    result = runner.invoke(app, ["validate", str(csv_file)])
    assert result.exit_code == 0
    assert "CSV structural check" in result.stderr


def test_validate_csv_missing_columns_exits_one(tmp_path: Path) -> None:
    csv_file = tmp_path / "cases.csv"
    csv_file.write_text("some_col,other_col\nval1,val2\n")
    result = runner.invoke(app, ["validate", str(csv_file)])
    assert result.exit_code == 1


def test_validate_yaml_with_failing_case_reports_error_and_exits_one(tmp_path: Path) -> None:
    pipeline = Pipeline.from_preset("snap.va")
    case = pipeline.generate(n=1, seed=42)[0]
    case.task.instruction = ""  # pydantic allows empty str; check_output_contract() doesn't
    yaml_file = tmp_path / "broken_case.yaml"
    yaml_file.write_text(YAMLFormatter().format_one(case))
    result = runner.invoke(app, ["validate", str(yaml_file)])
    assert result.exit_code == 1
    assert "task.instruction is empty" in result.stderr


def test_validate_jsonl_missing_keys_reports_error(tmp_path: Path) -> None:
    jsonl_file = tmp_path / "broken.jsonl"
    jsonl_file.write_text(json.dumps({"case_id": "x"}) + "\n")
    result = runner.invoke(app, ["validate", str(jsonl_file)])
    assert result.exit_code == 1
    assert "0/1" in result.stderr.replace("\n", " ")


def test_validate_forced_format_overrides_extension(tmp_path: Path) -> None:
    out_dir = _generate_yaml(tmp_path)
    yaml_file = next(out_dir.glob("*.yaml"))
    renamed = tmp_path / "cases.dat"
    renamed.write_text(yaml_file.read_text())
    result = runner.invoke(app, ["validate", str(renamed), "--format", "yaml"])
    assert result.exit_code == 0
