"""Unit tests for CatalaEligibilityGenerator (mocked catala subprocess end-to-end)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from govsynth.generators.catala_eligibility import CatalaEligibilityGenerator
from govsynth.sources.catala.ruleset import CatalaMappingError, CatalaRuleset

_INPUT_PROPS = {"household_size": {"type": "integer"}, "monthly_gross_income": {"type": "number"}}
_OUTPUT_PROPS = {"eligible": {"type": "boolean"}, "benefit_amount": {"type": "number"}}


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def ruleset_file(tmp_path: Path) -> Path:
    path = tmp_path / "liheap.catala_en"
    path.write_text("# a catala ruleset")
    return path


def _install_fake_catala(
    monkeypatch: pytest.MonkeyPatch,
    *,
    income_cutoff: float = 2000.0,
    trace_events: list[dict[str, Any]] | None = None,
) -> None:
    """Eligible iff monthly_gross_income < income_cutoff; optionally emits a trace."""
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "json-schema" in command:
            input_schema = {"type": "object", "properties": _INPUT_PROPS}
            output_schema = {"type": "object", "properties": _OUTPUT_PROPS}
            return _completed(stdout=json.dumps([input_schema, output_schema]))

        stdin_payload = kwargs.get("input")
        inputs = json.loads(stdin_payload) if stdin_payload else {}
        eligible = inputs.get("monthly_gross_income", 0) < income_cutoff
        outputs = {"eligible": eligible, "benefit_amount": 150.0 if eligible else 0.0}

        if trace_events is not None:
            trace_arg = next((a for a in command if a.startswith("--trace=")), None)
            if trace_arg:
                Path(trace_arg.split("=", 1)[1]).write_text(json.dumps(trace_events))

        return _completed(stdout=json.dumps(outputs))

    monkeypatch.setattr(subprocess, "run", fake_run)


def _load_ruleset(ruleset_file: Path, **overrides: object) -> CatalaRuleset:
    kwargs: dict[str, Any] = dict(
        program_name="liheap",
        jurisdiction="us",
        outcome_field="eligible",
        citation="LIHEAP test ruleset",
    )
    kwargs.update(overrides)
    return CatalaRuleset.load(ruleset_file, "Eligibility", **kwargs)


class TestConstruction:
    def test_program_property(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_catala(monkeypatch)
        ruleset = _load_ruleset(ruleset_file)
        gen = CatalaEligibilityGenerator(ruleset, state="VA")
        assert gen.program == "liheap"

    def test_bad_mapping_fails_fast_at_construction(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if "json-schema" in command:
                schema = {"type": "object", "properties": {"nonexistent_field": {}}}
                return _completed(stdout=json.dumps([schema, {"type": "object", "properties": {}}]))
            return _completed(stdout=json.dumps({}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        ruleset = _load_ruleset(ruleset_file)
        with pytest.raises(CatalaMappingError):
            CatalaEligibilityGenerator(ruleset, state="VA")


class TestGenerate:
    def test_generate_returns_requested_count(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_catala(monkeypatch)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        cases = gen.generate(n=8, seed=42)
        assert len(cases) == 8

    def test_all_generated_cases_are_valid(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_catala(monkeypatch)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        cases = gen.generate(n=15, seed=7)
        for case in cases:
            assert case.is_valid(), case.validate()

    def test_generate_is_deterministic_with_seed(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_catala(monkeypatch)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        cases1 = gen.generate(n=10, seed=42)
        cases2 = gen.generate(n=10, seed=42)
        assert [c.case_id for c in cases1] == [c.case_id for c in cases2]
        assert [c.expected_outcome for c in cases1] == [c.expected_outcome for c in cases2]

    def test_case_ids_use_ruleset_program_name(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_catala(monkeypatch)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        cases = gen.generate(n=5, seed=1)
        for case in cases:
            assert case.case_id.startswith("liheap.va.eligibility.catala_ruleset.")
            assert case.program == "liheap"

    def test_outcome_matches_fake_catala_income_cutoff(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_catala(monkeypatch, income_cutoff=999999)  # everyone eligible
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        cases = gen.generate(n=10, seed=1)
        assert all(c.expected_outcome == "eligible" for c in cases)

    def test_source_citations_use_ruleset_citation(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_catala(monkeypatch)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        case = gen.generate(n=1, seed=1)[0]
        assert case.source_citations == ["LIHEAP test ruleset"]

    def test_metadata_carries_catala_outputs(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_catala(monkeypatch)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        case = gen.generate(n=1, seed=1)[0]
        assert "eligible" in case.metadata["catala_outputs"]
        assert case.metadata["catala_scope"] == "Eligibility"

    def test_trace_events_are_mapped_to_reasoning_steps(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trace_events = [
            {"variable": "income_test", "value": True, "justification": "income < cutoff"},
            {"variable": "eligible", "value": True, "justification": "income_test passed"},
            {"variable": "amount", "value": 150.0, "justification": "flat benefit"},
        ]
        _install_fake_catala(monkeypatch, income_cutoff=999999, trace_events=trace_events)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        case = gen.generate(n=1, seed=1)[0]
        assert len(case.rationale_trace.steps) == 3
        assert case.rationale_trace.steps[0].title == "income_test"

    def test_empty_trace_falls_back_to_two_synthesized_steps(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_catala(monkeypatch, trace_events=[])
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")
        case = gen.generate(n=1, seed=1)[0]
        assert len(case.rationale_trace.steps) == 2
        assert case.rationale_trace.steps[1].is_determinative

    def test_per_case_runtime_failure_is_skipped_not_raised(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_catala(monkeypatch)
        gen = CatalaEligibilityGenerator(_load_ruleset(ruleset_file), state="VA")

        call_count = {"n": 0}
        real_run = gen.ruleset.run

        def flaky_run(profile: object, **kwargs: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated catala crash")
            return real_run(profile, **kwargs)

        monkeypatch.setattr(gen.ruleset, "run", flaky_run)
        cases = gen.generate(n=5, seed=1)
        assert len(cases) == 4  # one silently skipped, not a hard failure

    def test_custom_outcome_field(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if "json-schema" in command:
                schema = {"type": "object", "properties": {"household_size": {"type": "integer"}}}
                out_schema = {"type": "object", "properties": {"is_approved": {"type": "boolean"}}}
                return _completed(stdout=json.dumps([schema, out_schema]))
            return _completed(stdout=json.dumps({"is_approved": True}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        ruleset = _load_ruleset(ruleset_file, outcome_field="is_approved")
        gen = CatalaEligibilityGenerator(ruleset, state="VA")
        case = gen.generate(n=1, seed=1)[0]
        assert case.expected_outcome == "eligible"
