"""Integration tests for govsynth generate-catala (mocked catala subprocess)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from govsynth.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def ruleset_file(tmp_path: Path) -> Path:
    path = tmp_path / "liheap.catala_en"
    path.write_text("# a catala ruleset")
    return path


def _install_fake_catala(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "json-schema" in command:
            input_schema = {
                "type": "object",
                "properties": {"monthly_gross_income": {"type": "number"}},
            }
            output_schema = {"type": "object", "properties": {"eligible": {"type": "boolean"}}}
            return _completed(stdout=json.dumps([input_schema, output_schema]))
        return _completed(stdout=json.dumps({"eligible": True}))

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_generate_catala_writes_yaml_files(
    ruleset_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_catala(monkeypatch)
    out_dir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "generate-catala",
            str(ruleset_file),
            "--scope",
            "Eligibility",
            "--program",
            "liheap",
            "--output",
            str(out_dir),
            "--n",
            "5",
            "--seed",
            "42",
        ],
    )
    assert result.exit_code == 0, result.output
    yaml_files = list(out_dir.glob("*.yaml"))
    assert len(yaml_files) == 5


def test_generate_catala_json_status(
    ruleset_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_catala(monkeypatch)
    out_dir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "generate-catala",
            str(ruleset_file),
            "--scope",
            "Eligibility",
            "--program",
            "liheap",
            "--output",
            str(out_dir),
            "--n",
            "2",
            "--seed",
            "1",
            "--json",
            "--quiet",
        ],
    )
    assert result.exit_code == 0
    status = json.loads(result.stderr)
    assert status["status"] == "ok"
    assert status["program"] == "liheap"
    assert status["n"] == 2


def test_generate_catala_missing_binary_exits_two(
    ruleset_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = runner.invoke(
        app,
        [
            "generate-catala",
            str(ruleset_file),
            "--scope",
            "Eligibility",
            "--program",
            "liheap",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 2
    assert "not found on PATH" in result.output


def test_generate_catala_field_mapping_flag(
    ruleset_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "json-schema" in command:
            input_schema = {"type": "object", "properties": {"income": {"type": "number"}}}
            output_schema = {"type": "object", "properties": {"eligible": {"type": "boolean"}}}
            return _completed(stdout=json.dumps([input_schema, output_schema]))
        return _completed(stdout=json.dumps({"eligible": True}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    out_dir = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "generate-catala",
            str(ruleset_file),
            "--scope",
            "Eligibility",
            "--program",
            "liheap",
            "--output",
            str(out_dir),
            "--n",
            "1",
            "--field-mapping",
            "income=monthly_gross_income",
        ],
    )
    assert result.exit_code == 0, result.output


def test_generate_catala_bad_field_mapping_syntax_exits_nonzero(
    ruleset_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_catala(monkeypatch)
    result = runner.invoke(
        app,
        [
            "generate-catala",
            str(ruleset_file),
            "--scope",
            "Eligibility",
            "--program",
            "liheap",
            "--output",
            str(tmp_path / "out"),
            "--field-mapping",
            "not-a-key-value-pair",
        ],
    )
    assert result.exit_code != 0


def test_generate_catala_unmapped_field_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ruleset_path = tmp_path / "other.catala_en"
    ruleset_path.write_text("# ruleset")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        input_schema = {"type": "object", "properties": {"totally_unknown_field": {}}}
        output_schema = {"type": "object", "properties": {"eligible": {}}}
        return _completed(stdout=json.dumps([input_schema, output_schema]))

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.invoke(
        app,
        [
            "generate-catala",
            str(ruleset_path),
            "--scope",
            "Eligibility",
            "--program",
            "liheap",
            "--output",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 2
    assert "totally_unknown_field" in result.output
