"""Unit tests for TestCase.program validation.

Covers the relaxation added for the Catala adapter: an imported ruleset
can declare an arbitrary program (not one of the built-in KNOWN_PROGRAMS),
as long as it's a lowercase_snake_case slug -- not an arbitrary string.
"""

from __future__ import annotations

import pytest
from govsynth.models.enums import Difficulty, TaskType
from govsynth.models.rationale import PolicyCitation, RationaleTrace, ReasoningStep
from govsynth.models.test_case import ScenarioBlock, TaskBlock, TestCase
from pydantic import ValidationError


def _make_case(program: str) -> TestCase:
    return TestCase(
        case_id=f"{program}.us.eligibility.test.hh1.abc123",
        program=program,
        jurisdiction="us",
        task_type=TaskType.ELIGIBILITY,
        difficulty=Difficulty.MEDIUM,
        scenario=ScenarioBlock(
            summary="A test household.",
            household_size=1,
            monthly_gross_income=1000.0,
            state="VA",
        ),
        task=TaskBlock(instruction="Determine eligibility."),
        expected_outcome="eligible",
        expected_answer="This household is eligible.",
        rationale_trace=RationaleTrace(
            steps=[
                ReasoningStep(
                    step_number=1, title="Step 1", rule_applied="rule", computation="x",
                    result="y",
                ),
                ReasoningStep(
                    step_number=2, title="Step 2", rule_applied="rule", computation="x",
                    result="y",
                ),
            ],
            conclusion="Eligible.",
            policy_basis=[PolicyCitation(document="doc", section="sec", year=2026)],
        ),
        source_citations=["doc"],
    )


class TestKnownPrograms:
    def test_built_in_program_is_accepted(self) -> None:
        case = _make_case("snap")
        assert case.program == "snap"


class TestCustomPrograms:
    def test_custom_lowercase_snake_case_program_is_accepted(self) -> None:
        case = _make_case("my_local_benefit")
        assert case.program == "my_local_benefit"

    def test_uppercase_program_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid program"):
            _make_case("SNAP")

    def test_program_with_spaces_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid program"):
            _make_case("my program")

    def test_program_with_dots_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid program"):
            _make_case("my.program")

    def test_empty_program_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid program"):
            _make_case("")
