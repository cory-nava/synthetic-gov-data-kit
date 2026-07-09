"""Integration tests for govsynth refresh-census-data.

No tests make real Census API calls. The fetch/write step is exercised by
monkeypatching `build_state_census_json`/`write_state_file` at their source
module -- `refresh_census_data` imports them locally on each call, so
patching `govsynth.sources.us.census_fetcher` is picked up per-invocation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
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


def _fake_data(state: str) -> dict[str, Any]:
    return {"_metadata": {"state": state}}


class TestFetchAndWrite:
    def test_successful_single_state_refresh_exits_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "govsynth.sources.us.census_fetcher.build_state_census_json",
            lambda state, year, api_key: _fake_data(state),
        )
        monkeypatch.setattr(
            "govsynth.sources.us.census_fetcher.write_state_file",
            lambda state, data, data_dir: tmp_path / f"{state.lower()}.json",
        )
        result = runner.invoke(app, ["refresh-census-data", "--state", "VA"])
        assert result.exit_code == 0
        assert "VA" in result.output

    def test_successful_refresh_emits_json_status(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "govsynth.sources.us.census_fetcher.build_state_census_json",
            lambda state, year, api_key: _fake_data(state),
        )
        monkeypatch.setattr(
            "govsynth.sources.us.census_fetcher.write_state_file",
            lambda state, data, data_dir: tmp_path / f"{state.lower()}.json",
        )
        result = runner.invoke(app, ["refresh-census-data", "--state", "VA", "--json"])
        assert result.exit_code == 0
        assert '"status": "ok"' in result.stderr

    def test_persistent_http_error_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(state: str, year: int, api_key: str | None) -> dict[str, Any]:
            request = httpx.Request("GET", "https://api.census.gov/data")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("server error", request=request, response=response)

        monkeypatch.setattr("govsynth.sources.us.census_fetcher.build_state_census_json", _raise)
        result = runner.invoke(app, ["refresh-census-data", "--state", "VA"])
        assert result.exit_code == 1
        assert "VA" in result.output

    def test_rate_limit_retries_and_succeeds(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        calls = {"n": 0}

        def _fetch(state: str, year: int, api_key: str | None) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                request = httpx.Request("GET", "https://api.census.gov/data")
                response = httpx.Response(429, request=request)
                raise httpx.HTTPStatusError("rate limited", request=request, response=response)
            return _fake_data(state)

        monkeypatch.setattr("govsynth.sources.us.census_fetcher.build_state_census_json", _fetch)
        monkeypatch.setattr(
            "govsynth.sources.us.census_fetcher.write_state_file",
            lambda state, data, data_dir: tmp_path / f"{state.lower()}.json",
        )
        monkeypatch.setattr("govsynth.cli.commands.refresh_census.time.sleep", lambda s: None)
        result = runner.invoke(app, ["refresh-census-data", "--state", "VA"])
        assert result.exit_code == 0
        assert calls["n"] == 2

    def test_generic_exception_is_caught_and_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(state: str, year: int, api_key: str | None) -> dict[str, Any]:
            raise RuntimeError("boom")

        monkeypatch.setattr("govsynth.sources.us.census_fetcher.build_state_census_json", _raise)
        result = runner.invoke(app, ["refresh-census-data", "--state", "VA"])
        assert result.exit_code == 1
        assert "boom" in result.output
