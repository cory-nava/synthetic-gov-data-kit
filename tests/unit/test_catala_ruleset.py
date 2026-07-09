"""Unit tests for CatalaRuleset (introspection + profile-to-input mapping)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from govsynth.profiles.us_household import USHouseholdProfile
from govsynth.sources.catala.ruleset import CatalaMappingError, CatalaRuleset


@pytest.fixture
def ruleset_file(tmp_path: Path) -> Path:
    path = tmp_path / "program.catala_en"
    path.write_text("# a catala ruleset")
    return path


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _mock_schema(
    monkeypatch: pytest.MonkeyPatch, input_props: dict[str, Any], output_props: dict[str, Any]
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
    input_schema = {"type": "object", "properties": input_props}
    output_schema = {"type": "object", "properties": output_props}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "json-schema" in command:
            return _completed(stdout=json.dumps([input_schema, output_schema]))
        return _completed(stdout=json.dumps({"eligible": True}))

    monkeypatch.setattr(subprocess, "run", fake_run)


class TestLoad:
    def test_load_introspects_schema_once(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        call_count = {"n": 0}
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            call_count["n"] += 1
            return _completed(
                stdout=json.dumps(
                    [
                        {"type": "object", "properties": {"household_size": {"type": "integer"}}},
                        {"type": "object", "properties": {"eligible": {"type": "boolean"}}},
                    ]
                )
            )

        monkeypatch.setattr(subprocess, "run", fake_run)
        ruleset = CatalaRuleset.load(ruleset_file, "Eligibility", program_name="liheap")
        assert call_count["n"] == 1
        assert ruleset.input_field_names() == ["household_size"]
        assert ruleset.output_field_names() == ["eligible"]

    def test_default_citation_year_is_current_year(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import datetime

        _mock_schema(monkeypatch, {}, {"eligible": {"type": "boolean"}})
        ruleset = CatalaRuleset.load(ruleset_file, "Eligibility", program_name="liheap")
        assert ruleset.citation_year == datetime.date.today().year

    def test_explicit_citation_year_respected(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_schema(monkeypatch, {}, {"eligible": {"type": "boolean"}})
        ruleset = CatalaRuleset.load(
            ruleset_file, "Eligibility", program_name="liheap", citation_year=2019
        )
        assert ruleset.citation_year == 2019


class TestBuildInputs:
    def test_exact_name_match_is_automatic(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_schema(
            monkeypatch,
            {"household_size": {"type": "integer"}, "monthly_gross_income": {"type": "number"}},
            {"eligible": {"type": "boolean"}},
        )
        ruleset = CatalaRuleset.load(ruleset_file, "Eligibility", program_name="liheap")
        profile = USHouseholdProfile.random(state="VA", seed=1)
        inputs = ruleset.build_inputs(profile)
        assert inputs == {
            "household_size": profile.household_size,
            "monthly_gross_income": profile.monthly_gross_income,
        }

    def test_field_mapping_override(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_schema(monkeypatch, {"income": {"type": "number"}}, {"eligible": {"type": "boolean"}})
        ruleset = CatalaRuleset.load(
            ruleset_file,
            "Eligibility",
            program_name="liheap",
            field_mapping={"income": "monthly_gross_income"},
        )
        profile = USHouseholdProfile.random(state="VA", seed=1)
        inputs = ruleset.build_inputs(profile)
        assert inputs == {"income": profile.monthly_gross_income}

    def test_nested_additional_context_fields_are_reachable(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_schema(monkeypatch, {"earned_income": {}}, {"eligible": {}})
        ruleset = CatalaRuleset.load(ruleset_file, "Eligibility", program_name="liheap")
        profile = USHouseholdProfile.random(state="VA", seed=1)
        inputs = ruleset.build_inputs(profile)
        assert inputs == {"earned_income": profile.earned_income}

    def test_unmapped_field_raises_with_field_name_and_available_list(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_schema(monkeypatch, {"nonexistent_field": {}}, {"eligible": {}})
        ruleset = CatalaRuleset.load(ruleset_file, "Eligibility", program_name="liheap")
        profile = USHouseholdProfile.random(state="VA", seed=1)
        with pytest.raises(CatalaMappingError, match="nonexistent_field"):
            ruleset.build_inputs(profile)

    def test_no_input_fields_returns_empty_dict(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_schema(monkeypatch, {}, {"eligible": {}})
        ruleset = CatalaRuleset.load(ruleset_file, "Eligibility", program_name="liheap")
        profile = USHouseholdProfile.random(state="VA", seed=1)
        assert ruleset.build_inputs(profile) == {}


class TestRun:
    def test_run_builds_inputs_and_interprets(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_schema(
            monkeypatch, {"household_size": {"type": "integer"}}, {"eligible": {"type": "boolean"}}
        )
        ruleset = CatalaRuleset.load(ruleset_file, "Eligibility", program_name="liheap")
        profile = USHouseholdProfile.random(state="VA", seed=1)
        result = ruleset.run(profile, with_trace=False)
        assert result.outputs == {"eligible": True}
