"""Tests for Medicaid FPL data loading and the shared JSON-cache helpers."""

from __future__ import annotations

import json
from pathlib import Path

from govsynth.sources.base import _load_json_file, _load_json_file_cached
from govsynth.sources.us.medicaid import MedicaidSource, _load_fpl_json, _load_fpl_json_cached


def test_medicaid_caching() -> None:
    source = MedicaidSource(calendar_year=2026, state="VA")
    # This should exercise fetch_thresholds and cached FPL loading
    thresholds = source.fetch_thresholds()
    assert thresholds.program == "medicaid"
    assert thresholds.state == "VA"

    # This should exercise get_income_limit and cached FPL loading
    limit = source.get_income_limit("adult")
    assert limit is not None

    # This should exercise is_eligible and cached FPL loading
    eligible, reason = source.is_eligible(1, 1000, "adult")
    assert isinstance(eligible, bool)


class TestCachedLoadersReturnIndependentCopies:
    """Mutating a loader's return value must never corrupt the shared cache."""

    def test_load_json_file_returns_independent_copies(self, tmp_path: Path) -> None:
        path = tmp_path / "sample.json"
        path.write_text(json.dumps({"a": 1, "nested": {"b": 2}}))

        first = _load_json_file(str(path))
        first["a"] = 999
        first["nested"]["b"] = 999
        first["new_key"] = "should not leak"

        second = _load_json_file(str(path))
        assert second == {"a": 1, "nested": {"b": 2}}

        # The private cached loader must also be unaffected.
        assert _load_json_file_cached(str(path)) == {"a": 1, "nested": {"b": 2}}

    def test_load_fpl_json_returns_independent_copies(self, tmp_path: Path) -> None:
        path = tmp_path / "fpl.json"
        path.write_text(json.dumps({"regions": {"contiguous_48_dc": {}}}))

        first = _load_fpl_json(str(path))
        first["regions"]["contiguous_48_dc"]["injected"] = True

        second = _load_fpl_json(str(path))
        assert "injected" not in second["regions"]["contiguous_48_dc"]
        assert "injected" not in _load_fpl_json_cached(str(path))["regions"]["contiguous_48_dc"]


class TestCacheInvalidation:
    """The private cached loader must be clearable so a file rewritten
    mid-process (e.g. `govsynth refresh-census-data`) is re-read rather
    than served stale."""

    def test_cache_clear_picks_up_rewritten_file(self, tmp_path: Path) -> None:
        path = tmp_path / "refreshable.json"
        path.write_text(json.dumps({"version": 1}))

        assert _load_json_file(str(path)) == {"version": 1}

        path.write_text(json.dumps({"version": 2}))
        # Without invalidation the stale cached value would still win.
        assert _load_json_file(str(path)) == {"version": 1}

        _load_json_file_cached.cache_clear()
        assert _load_json_file(str(path)) == {"version": 2}
