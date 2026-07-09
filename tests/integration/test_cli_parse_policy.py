"""Integration tests for govsynth parse-policy (stub)."""

from pathlib import Path

from govsynth.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_parse_policy_exits_zero(tmp_path: Path) -> None:
    """Stub exits 0 — does not break scripts that probe the command."""
    fake_pdf = tmp_path / "policy.pdf"
    fake_pdf.write_bytes(b"fake")
    result = runner.invoke(app, ["parse-policy", str(fake_pdf)])
    assert result.exit_code == 0


def test_parse_policy_prints_helpful_message(tmp_path: Path) -> None:
    fake_pdf = tmp_path / "policy.pdf"
    fake_pdf.write_bytes(b"fake")
    result = runner.invoke(app, ["parse-policy", str(fake_pdf)])
    assert "not yet implemented" in result.stderr.lower()
    assert "bring-your-own-policy" in result.stderr


def test_parse_policy_json_flag_accepted(tmp_path: Path) -> None:
    """--json accepted silently on stub."""
    fake_pdf = tmp_path / "policy.pdf"
    fake_pdf.write_bytes(b"fake")
    result = runner.invoke(app, ["parse-policy", str(fake_pdf), "--json"])
    assert result.exit_code == 0
