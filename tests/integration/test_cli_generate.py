"""Integration tests for govsynth generate."""

import json

from govsynth.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_generate_to_directory_creates_yaml_files(tmp_path):
    # Use n=10 to avoid flakiness — Pipeline may drop invalid cases,
    # so we just assert at least one file was created rather than an exact count.
    result = runner.invoke(
        app,
        [
            "generate",
            "snap.va",
            "--n",
            "10",
            "--seed",
            "42",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    yaml_files = list(tmp_path.glob("*.yaml"))
    assert len(yaml_files) >= 1


def test_generate_unknown_preset_exits_two():
    result = runner.invoke(app, ["generate", "snap.nowhere"])
    assert result.exit_code == 2


def test_generate_to_stdout_jsonl(tmp_path):
    """Without --output, cases stream to stdout as JSONL (--quiet keeps stderr clean)."""
    result = runner.invoke(
        app,
        [
            "generate",
            "snap.va",
            "--n",
            "2",
            "--seed",
            "42",
            "--format",
            "jsonl",
            "--quiet",
        ],
    )
    assert result.exit_code == 0
    lines = [ln for ln in result.output.strip().split("\n") if ln]
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert "case_id" in first


def test_generate_to_stdout_yaml(tmp_path):
    """Without --output, YAML cases stream with --- separators."""
    result = runner.invoke(
        app,
        [
            "generate",
            "snap.va",
            "--n",
            "2",
            "--seed",
            "42",
            "--format",
            "yaml",
        ],
    )
    assert result.exit_code == 0
    assert "---" in result.output
    assert "case_id" in result.output


def test_generate_multiple_formats(tmp_path):
    result = runner.invoke(
        app,
        [
            "generate",
            "snap.va",
            "--n",
            "3",
            "--seed",
            "42",
            "--output",
            str(tmp_path),
            "--format",
            "yaml",
            "--format",
            "jsonl",
        ],
    )
    assert result.exit_code == 0
    assert list(tmp_path.glob("*.yaml"))
    assert list(tmp_path.glob("*.jsonl"))


def test_generate_json_status_flag(tmp_path):
    # --quiet ensures only the JSON status line goes to stderr (no Rich progress bars)
    result = runner.invoke(
        app,
        [
            "generate",
            "snap.va",
            "--n",
            "2",
            "--seed",
            "42",
            "--output",
            str(tmp_path),
            "--json",
            "--quiet",
        ],
    )
    assert result.exit_code == 0
    status = json.loads(result.stderr)
    assert status["status"] == "ok"
    assert status["n"] == 2


def test_generate_quiet_flag_suppresses_rich_output(tmp_path):
    """--quiet produces no Rich progress or summary on stderr."""
    result = runner.invoke(
        app,
        [
            "generate",
            "snap.va",
            "--n",
            "2",
            "--seed",
            "42",
            "--output",
            str(tmp_path),
            "--quiet",
        ],
    )
    assert result.exit_code == 0
    assert result.stderr == ""
