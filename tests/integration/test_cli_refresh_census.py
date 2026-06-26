"""Integration tests for govsynth refresh-census-data.

No tests make real Census API calls. Network tests are manual/opt-in only.
"""

from __future__ import annotations

from govsynth.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_dry_run_single_state_exits_zero() -> None:
    result = runner.invoke(app, ["refresh-census-data", "--state", "VA", "--dry-run"])
    assert result.exit_code == 0


def test_dry_run_no_network_calls() -> None:
    """--dry-run must not make any HTTP requests."""
    import respx

    with respx.mock(assert_all_called=False):
        result = runner.invoke(app, ["refresh-census-data", "--state", "VA", "--dry-run"])
    assert result.exit_code == 0


def test_dry_run_mentions_state() -> None:
    result = runner.invoke(app, ["refresh-census-data", "--state", "VA", "--dry-run"])
    assert result.exit_code == 0
    assert "VA" in result.output or "va" in result.output.lower()


def test_dry_run_json_exits_zero() -> None:
    result = runner.invoke(app, ["refresh-census-data", "--state", "VA", "--dry-run", "--json"])
    assert result.exit_code == 0


def test_invalid_state_exits_two() -> None:
    result = runner.invoke(app, ["refresh-census-data", "--state", "ZZ"])
    assert result.exit_code == 2


def test_invalid_state_error_message() -> None:
    result = runner.invoke(app, ["refresh-census-data", "--state", "ZZ"])
    assert "ZZ" in result.output or "error" in result.output.lower()


def test_confirmation_prompt_n_exits_zero() -> None:
    """Declining the all-states prompt exits 0 (not an error)."""
    result = runner.invoke(app, ["refresh-census-data"], input="n\n")
    assert result.exit_code == 0


def test_confirmation_prompt_shows_api_count() -> None:
    """Prompt must mention the number of API calls (~306)."""
    result = runner.invoke(app, ["refresh-census-data"], input="n\n")
    assert "306" in result.output


def test_dry_run_all_states_no_prompt() -> None:
    """--dry-run skips the confirmation prompt even for all states."""
    result = runner.invoke(app, ["refresh-census-data", "--dry-run"])
    assert result.exit_code == 0
