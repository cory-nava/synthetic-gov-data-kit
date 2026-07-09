"""Unit tests for the Census Bureau ACS data fetcher.

Network-only module: all Census API calls are mocked with respx so these
tests never hit the network.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx
from govsynth.sources.us.census_fetcher import (
    FIPS_CODES,
    _get,
    _safe_int,
    _safe_rate,
    build_state_census_json,
    fetch_state,
    write_state_file,
)


class TestSafeHelpers:
    def test_safe_int_parses_valid_string(self) -> None:
        assert _safe_int("42") == 42

    def test_safe_int_negative_clamps_to_zero(self) -> None:
        assert _safe_int("-5") == 0

    def test_safe_int_non_numeric_returns_zero(self) -> None:
        assert _safe_int("null") == 0

    def test_safe_rate_normal(self) -> None:
        assert _safe_rate(25, 100) == 0.25

    def test_safe_rate_zero_denominator(self) -> None:
        assert _safe_rate(25, 0) == 0.0


class TestGet:
    @respx.mock
    def test_get_returns_parsed_json_rows(self) -> None:
        url = "https://api.census.gov/data/2022/acs/acs5"
        respx.get(url).mock(return_value=httpx.Response(200, json=[["NAME", "B01001_001E"], ["Virginia", "8600000"]]))
        with httpx.Client() as client:
            rows = _get(client, url, {"get": "B01001_001E", "for": "state:51"})
        assert rows == [["NAME", "B01001_001E"], ["Virginia", "8600000"]]

    @respx.mock
    def test_get_raises_on_http_error(self) -> None:
        url = "https://api.census.gov/data/2022/acs/acs5"
        respx.get(url).mock(return_value=httpx.Response(500))
        with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
            _get(client, url, {"get": "B01001_001E", "for": "state:51"})


class TestFetchState:
    @respx.mock
    def test_fetch_state_makes_six_requests_and_returns_all_tables(self) -> None:
        url = "https://api.census.gov/data/2022/acs/acs5"
        route = respx.get(url).mock(return_value=httpx.Response(200, json=[["header"], ["1"]]))
        result = fetch_state("VA", 2022, api_key=None)
        assert route.call_count == 6
        assert set(result.keys()) == {
            "income",
            "poverty",
            "housing",
            "demographics",
            "income_sources",
            "health",
        }

    def test_fetch_state_unknown_state_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            fetch_state("ZZ", 2022, api_key=None)

    def test_all_state_fips_codes_are_two_digits(self) -> None:
        for code in FIPS_CODES.values():
            assert len(code) == 2 and code.isdigit()


def _table_row(header: list[str], values: dict[str, str]) -> list[list[str]]:
    """Build a [header, value] Census API row pair for the given var->value map."""
    row = [values.get(h, "0") for h in header]
    return [header, row]


@pytest.fixture
def raw_census_response() -> dict[str, list[list[str]]]:
    income_header = [f"B19001_0{i:02d}E" for i in range(2, 18)]
    income_values = {h: "100" for h in income_header}

    poverty_header = ["B17024_002E", "B17024_003E", "B17024_004E", "B17024_005E", "B17024_001E"]
    poverty_values = {
        "B17024_002E": "500",
        "B17024_003E": "1000",
        "B17024_004E": "1500",
        "B17024_005E": "2500",
        "B17024_001E": "10000",
    }

    housing_header = [
        "B25064_001E",
        "B25070_007E",
        "B25070_008E",
        "B25070_009E",
        "B25070_010E",
        "B25070_001E",
        "B25003_003E",
        "B25003_001E",
    ]
    housing_values = {
        "B25064_001E": "1450",
        "B25070_007E": "100",
        "B25070_008E": "100",
        "B25070_009E": "100",
        "B25070_010E": "100",
        "B25070_001E": "2000",
        "B25003_003E": "3000",
        "B25003_001E": "8000",
    }

    demo_header = [
        "B11016_003E",
        "B11016_004E",
        "B11016_005E",
        "B11016_006E",
        "B11016_007E",
        "B11016_010E",
        "B11016_011E",
        "B11016_012E",
        "B11016_013E",
        "B11016_001E",
        "B11003_003E",
        "B11003_007E",
        "B11003_001E",
        "B01001_001E",
        "B01001_020E",
        "B01001_021E",
        "B01001_022E",
        "B01001_023E",
        "B01001_024E",
        "B01001_025E",
        "B01001_044E",
        "B01001_045E",
        "B01001_046E",
        "B01001_047E",
        "B01001_048E",
        "B01001_049E",
        "B05001_002E",
        "B05001_006E",
        "B05001_001E",
        "B18101_004E",
        "B18101_007E",
        "B18101_010E",
        "B18101_023E",
        "B18101_026E",
        "B18101_029E",
        "B18101_001E",
    ]
    demo_values = {h: "200" for h in demo_header}
    demo_values["B01001_001E"] = "8600000"
    demo_values["B05001_001E"] = "8600000"
    demo_values["B18101_001E"] = "8600000"
    demo_values["B11016_001E"] = "3200000"
    demo_values["B11003_001E"] = "1500"

    isrc_header = [
        "B22003_002E",
        "B22003_001E",
        "B23025_002E",
        "B23025_001E",
        "B19055_002E",
        "B19055_001E",
        "B19056_002E",
        "B19056_001E",
        "B19057_002E",
        "B19057_001E",
    ]
    isrc_values = {
        "B22003_002E": "250000",
        "B22003_001E": "3200000",
        "B23025_002E": "4500000",
        "B23025_001E": "7000000",
        "B19055_002E": "1200000",
        "B19055_001E": "3200000",
        "B19056_002E": "150000",
        "B19056_001E": "3200000",
        "B19057_002E": "80000",
        "B19057_001E": "3200000",
    }

    health_header = ["B27001_004E", "B27001_007E", "B27001_010E", "B27001_013E", "B27001_016E", "B27001_001E"]
    health_values = {
        "B27001_004E": "50000",
        "B27001_007E": "50000",
        "B27001_010E": "50000",
        "B27001_013E": "50000",
        "B27001_016E": "50000",
        "B27001_001E": "8600000",
    }

    return {
        "income": _table_row(income_header, income_values),
        "poverty": _table_row(poverty_header, poverty_values),
        "housing": _table_row(housing_header, housing_values),
        "demographics": _table_row(demo_header, demo_values),
        "income_sources": _table_row(isrc_header, isrc_values),
        "health": _table_row(health_header, health_values),
    }


class TestBuildStateCensusJson:
    def test_builds_expected_top_level_shape(self, raw_census_response: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("govsynth.sources.us.census_fetcher.fetch_state", lambda *a, **kw: raw_census_response)
        data = build_state_census_json("VA", year=2022, api_key=None)
        assert data["_metadata"]["state"] == "VA"
        assert data["_metadata"]["acs_vintage"] == 2022
        assert set(data.keys()) == {
            "_metadata",
            "income",
            "housing",
            "household_size",
            "demographics",
            "income_sources",
            "program_participation",
        }

    def test_household_size_weights_sum_to_one(
        self, raw_census_response: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("govsynth.sources.us.census_fetcher.fetch_state", lambda *a, **kw: raw_census_response)
        data = build_state_census_json("VA", year=2022, api_key=None)
        weights = data["household_size"]["weights"]
        assert len(weights) == 6
        assert sum(weights) == pytest.approx(1.0, abs=0.001)

    def test_rates_are_between_zero_and_one(self, raw_census_response: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("govsynth.sources.us.census_fetcher.fetch_state", lambda *a, **kw: raw_census_response)
        data = build_state_census_json("VA", year=2022, api_key=None)
        for key in ("pct_citizen", "pct_noncitizen_eligible", "pct_with_children"):
            assert 0.0 <= data["demographics"][key] <= 1.0
        for key in ("labor_force_participation_rate", "pct_social_security"):
            assert 0.0 <= data["income_sources"][key] <= 1.0

    def test_output_matches_govsynth_census_data_source_schema(
        self, raw_census_response: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The built dict must be loadable by CensusDataSource._parse."""
        from govsynth.sources.us.census import CensusDataSource

        monkeypatch.setattr("govsynth.sources.us.census_fetcher.fetch_state", lambda *a, **kw: raw_census_response)
        data = build_state_census_json("VA", year=2022, api_key=None)
        write_state_file("VA", data, tmp_path)
        dist = CensusDataSource("VA", data_dir=tmp_path).load()
        assert dist is not None
        assert dist.state == "VA"


class TestWriteStateFile:
    def test_writes_valid_json_and_returns_path(self, tmp_path: Path) -> None:
        data = {"_metadata": {"state": "VA"}}
        path = write_state_file("VA", data, tmp_path)
        assert path == tmp_path / "va.json"
        assert json.loads(path.read_text()) == data

    def test_does_not_leave_tmp_file_behind(self, tmp_path: Path) -> None:
        write_state_file("VA", {"a": 1}, tmp_path)
        assert not (tmp_path / "va.json.tmp").exists()

    def test_creates_missing_directory(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "nested" / "census"
        write_state_file("TX", {"a": 1}, target_dir)
        assert (target_dir / "tx.json").exists()
