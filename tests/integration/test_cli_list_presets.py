"""Integration tests for govsynth list-presets."""

import json

from govsynth.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_list_presets_exits_zero():
    result = runner.invoke(app, ["list-presets"])
    assert result.exit_code == 0


def test_list_presets_shows_all_presets():
    result = runner.invoke(app, ["list-presets"])
    for preset in ["snap.va", "snap.ca", "snap.tx", "snap.md", "wic.national"]:
        assert preset in result.output


def test_list_presets_json_is_valid_list():
    result = runner.invoke(app, ["list-presets", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 5


def test_list_presets_json_has_required_fields():
    result = runner.invoke(app, ["list-presets", "--json"])
    data = json.loads(result.output)
    for item in data:
        assert "preset" in item
        assert "program" in item
        assert "state" in item
        assert "strategy" in item
        assert "description" in item
