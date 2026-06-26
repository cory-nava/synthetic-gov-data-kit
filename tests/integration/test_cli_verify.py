"""Integration tests for govsynth verify-thresholds."""

import json

from govsynth.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_verify_thresholds_snap_all_verified():
    """SNAP threshold files are verified — filter to snap to avoid medicaid_cy2026.json
    which has verification_status='estimated' and will correctly exit code 1."""
    result = runner.invoke(app, ["verify-thresholds", "--program", "snap"])
    assert result.exit_code == 0


def test_verify_thresholds_default_exits_one_for_unverified():
    """Default run (no filter) finds medicaid_cy2026.json as unverified → exit 1."""
    result = runner.invoke(app, ["verify-thresholds"])
    assert result.exit_code == 1


def test_verify_thresholds_json_output_has_required_fields():
    result = runner.invoke(app, ["verify-thresholds", "--program", "snap", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "status" in data
    assert "checked" in data
    assert "unverified" in data
    assert isinstance(data["unverified"], list)


def test_verify_thresholds_filter_by_program():
    result = runner.invoke(app, ["verify-thresholds", "--program", "snap"])
    assert result.exit_code == 0


def test_verify_thresholds_invalid_program_exits_two():
    result = runner.invoke(app, ["verify-thresholds", "--program", "notaprogram"])
    assert result.exit_code == 2
