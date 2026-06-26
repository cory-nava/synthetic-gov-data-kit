"""Unit tests for CensusDataSource and CensusDistribution."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
from govsynth.sources.us.census import CensusDataSource, CensusDistribution

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "census_va.json"


@pytest.fixture
def census_dir(tmp_path: Path) -> Path:
    """Temp directory seeded with the VA fixture."""
    (tmp_path / "va.json").write_text(_FIXTURE.read_text())
    return tmp_path


class TestCensusDataSourceLoad:
    def test_loads_fixture_returns_distribution(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert isinstance(dist, CensusDistribution)

    def test_state_field_is_uppercase(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert dist.state == "VA"

    def test_income_fields(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert dist.income_mu == pytest.approx(8.1)
        assert dist.income_sigma == pytest.approx(0.65)
        assert isinstance(dist.fpl_buckets, list)
        assert len(dist.fpl_buckets) == 4

    def test_household_size_weights(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert len(dist.household_size_weights) == 6
        assert abs(sum(dist.household_size_weights) - 1.0) < 0.01

    def test_housing_fields(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert dist.median_gross_rent_monthly == pytest.approx(1450)
        assert dist.pct_renter == pytest.approx(0.36)

    def test_demographics_fields(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert dist.pct_with_children == pytest.approx(0.32)
        assert dist.pct_elderly_or_disabled == pytest.approx(0.18)
        assert dist.pct_citizen == pytest.approx(0.87)
        assert dist.pct_noncitizen_eligible == pytest.approx(0.05)
        assert dist.age_mu == pytest.approx(42)
        assert dist.age_sigma == pytest.approx(15)

    def test_income_sources_fields(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert dist.labor_force_participation_rate == pytest.approx(0.67)
        assert dist.pct_social_security == pytest.approx(0.19)
        assert dist.pct_ssi == pytest.approx(0.03)
        assert dist.pct_public_assistance == pytest.approx(0.02)

    def test_program_participation_fields(self, census_dir: Path) -> None:
        dist = CensusDataSource("VA", data_dir=census_dir).load()
        assert dist is not None
        assert dist.snap_receipt_rate == pytest.approx(0.08)
        assert dist.medicaid_coverage_rate == pytest.approx(0.18)

    def test_case_insensitive_state(self, census_dir: Path) -> None:
        dist = CensusDataSource("va", data_dir=census_dir).load()
        assert dist is not None

    def test_missing_state_emits_warning(self, census_dir: Path) -> None:
        # census_dir contains only va.json (no national.json), so load() returns None
        # after warning. See test_missing_state_falls_back_to_national for the case
        # where national.json exists.
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = CensusDataSource("ZZ", data_dir=census_dir).load()
        assert result is None
        assert any("No census data for 'ZZ'" in str(warning.message) for warning in w)

    def test_missing_state_falls_back_to_national(self, tmp_path: Path) -> None:
        """If state file missing but national.json present, return national dist + warn."""
        data = json.loads(_FIXTURE.read_text())
        data["_metadata"]["state"] = "national"
        (tmp_path / "national.json").write_text(json.dumps(data))

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = CensusDataSource("MD", data_dir=tmp_path).load()

        assert result is not None
        assert any("No census data for 'MD'" in str(warning.message) for warning in w)

    def test_national_missing_returns_none(self, tmp_path: Path) -> None:
        """No state file, no national.json -> None (no exception)."""
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = CensusDataSource("TX", data_dir=tmp_path).load()
        assert result is None


class TestFitLognormal:
    def test_single_bracket_zero_sigma(self) -> None:
        """All weight on one bracket -> sigma = 0."""
        import math

        from govsynth.sources.us.census_fetcher import fit_lognormal

        buckets = [{"annual_midpoint": 60000, "weight": 1.0}]
        mu, sigma = fit_lognormal(buckets)
        assert mu == pytest.approx(math.log(60000 / 12), abs=0.01)
        assert sigma == pytest.approx(0.0, abs=0.001)

    def test_two_brackets_symmetric(self) -> None:
        """Equal weight on two brackets -> mu is the mean of their log-monthly values."""
        import math

        from govsynth.sources.us.census_fetcher import fit_lognormal

        buckets = [
            {"annual_midpoint": 12000, "weight": 0.5},
            {"annual_midpoint": 60000, "weight": 0.5},
        ]
        mu, sigma = fit_lognormal(buckets)
        expected_mu = (math.log(12000 / 12) + math.log(60000 / 12)) / 2
        assert mu == pytest.approx(expected_mu, abs=0.01)
        assert sigma > 0

    def test_normalizes_unscaled_weights(self) -> None:
        """Weights that don't sum to 1.0 are normalized before fitting."""
        from govsynth.sources.us.census_fetcher import fit_lognormal

        buckets_normalized = [
            {"annual_midpoint": 12000, "weight": 0.5},
            {"annual_midpoint": 60000, "weight": 0.5},
        ]
        buckets_raw = [
            {"annual_midpoint": 12000, "weight": 100},
            {"annual_midpoint": 60000, "weight": 100},
        ]
        mu1, sigma1 = fit_lognormal(buckets_normalized)
        mu2, sigma2 = fit_lognormal(buckets_raw)
        assert mu1 == pytest.approx(mu2, abs=0.001)
        assert sigma1 == pytest.approx(sigma2, abs=0.001)

    def test_returns_monthly_scale(self) -> None:
        """mu is monthly (annual / 12), not annual."""
        import math

        from govsynth.sources.us.census_fetcher import fit_lognormal

        buckets = [{"annual_midpoint": 60000, "weight": 1.0}]
        mu, _ = fit_lognormal(buckets)
        # monthly midpoint = 60000/12 = 5000; log(5000) ≈ 8.517
        assert mu == pytest.approx(math.log(5000), abs=0.01)


class TestRealisticProfile:
    """Tests for USHouseholdProfile.random(strategy='realistic')."""

    def test_returns_profile_with_census_data(
        self, monkeypatch: pytest.MonkeyPatch, census_dir: Path
    ) -> None:
        """With census data present, returns a USHouseholdProfile."""
        monkeypatch.setattr("govsynth.sources.us.census._CENSUS_DIR", census_dir)
        from govsynth.profiles.us_household import USHouseholdProfile

        profile = USHouseholdProfile.random(state="VA", seed=42, strategy="realistic")
        assert profile is not None
        assert 1 <= profile.household_size <= 6
        assert profile.monthly_gross_income > 0
        assert profile.state == "VA"

    def test_realistic_is_deterministic(
        self, monkeypatch: pytest.MonkeyPatch, census_dir: Path
    ) -> None:
        """Same seed -> same profile."""
        monkeypatch.setattr("govsynth.sources.us.census._CENSUS_DIR", census_dir)
        from govsynth.profiles.us_household import USHouseholdProfile

        p1 = USHouseholdProfile.random(state="VA", seed=99, strategy="realistic")
        p2 = USHouseholdProfile.random(state="VA", seed=99, strategy="realistic")
        assert p1.household_size == p2.household_size
        assert p1.monthly_gross_income == p2.monthly_gross_income

    def test_realistic_fallback_when_no_census_data(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With no data files, falls back gracefully (no exception)."""
        monkeypatch.setattr("govsynth.sources.us.census._CENSUS_DIR", tmp_path)
        import warnings

        from govsynth.profiles.us_household import USHouseholdProfile

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            profile = USHouseholdProfile.random(state="VA", seed=42, strategy="realistic")
        assert profile is not None
        assert profile.monthly_gross_income > 0

    def test_uniform_strategy_unchanged(self) -> None:
        """The existing uniform path still works (regression guard)."""
        from govsynth.profiles.us_household import USHouseholdProfile

        profile = USHouseholdProfile.random(state="VA", seed=42, strategy="uniform")
        assert profile is not None
        assert 1 <= profile.household_size <= 6
