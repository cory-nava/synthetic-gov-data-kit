"""Integration tests for govsynth batch."""

import json
from pathlib import Path

from govsynth.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_batch_generates_cases_for_all_presets(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "batch",
            "--preset",
            "snap.va",
            "--preset",
            "wic.national",
            "--n",
            "2",
            "--seed",
            "42",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    yaml_files = list(tmp_path.glob("*.yaml"))
    assert len(yaml_files) >= 2  # at least 1 per preset


def test_batch_unknown_preset_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "batch",
            "--preset",
            "snap.nowhere",
            "--preset",
            "snap.va",
            "--n",
            "2",
            "--output",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 2
    yaml_files = list(tmp_path.glob("*.yaml"))
    assert len(yaml_files) == 0  # no partial runs


def test_batch_requires_output() -> None:
    """batch without --output should fail (Typer required param)."""
    result = runner.invoke(
        app,
        [
            "batch",
            "--preset",
            "snap.va",
            "--n",
            "2",
        ],
    )
    assert result.exit_code != 0


def test_batch_json_status(tmp_path: Path) -> None:
    # --quiet ensures only the JSON status line goes to stderr
    result = runner.invoke(
        app,
        [
            "batch",
            "--preset",
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
