"""Core TestCase data structure — the primary output of synthetic-gov-data-kit.

A TestCase is the unit of evaluation. It contains:
  - The citizen scenario (who is asking)
  - The task (what they need to know)
  - The expected answer and outcome
  - A step-by-step rationale trace (how to reason correctly)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from govsynth.models.enums import Difficulty, TaskType
from govsynth.models.rationale import RationaleTrace


class ScenarioBlock(BaseModel):
    """The citizen situation presented to the model."""

    summary: str = Field(description="Natural language description of the citizen's situation")
    household_size: int = Field(ge=1, le=20, description="Number of people in the household")
    monthly_gross_income: float = Field(ge=0, description="Monthly gross income in USD")
    monthly_net_income: float | None = Field(default=None, description="Monthly net income after deductions in USD")
    liquid_assets: float = Field(default=0.0, ge=0, description="Liquid assets in USD")
    state: str = Field(description="Two-letter US state code, e.g. 'VA'")
    has_elderly_or_disabled: bool = Field(
        default=False,
        description="Whether any household member is elderly (60+) or disabled",
    )
    has_dependent_children: bool = Field(default=False)
    citizenship_status: str = Field(default="citizen")
    additional_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Program-specific additional fields",
    )


class TaskBlock(BaseModel):
    """What the model is asked to do."""

    instruction: str = Field(description="The task instruction presented to the model")
    portal: str | None = Field(
        default=None,
        description="For agentic tasks: the portal or system to interact with",
    )
    documents_available: list[str] = Field(
        default_factory=list,
        description="For form/agentic tasks: list of available document types",
    )


class TestCase(BaseModel):
    """The primary output unit of synthetic-gov-data-kit.

    Compatible with test case schema v1.
    """

    case_id: str = Field(description="Unique structured case identifier")
    program: str = Field(description="Benefits program, e.g. 'snap'")
    jurisdiction: str = Field(description="Jurisdiction string, e.g. 'us.va'")
    task_type: TaskType = Field(description="The type of task being evaluated")
    difficulty: Difficulty = Field(description="Difficulty level of this case")

    scenario: ScenarioBlock = Field(description="The citizen situation")
    task: TaskBlock = Field(description="The task instruction")

    expected_outcome: str = Field(description="Short outcome label, e.g. 'eligible' or 'ineligible'")
    expected_answer: str = Field(description="Full natural language expected answer")

    rationale_trace: RationaleTrace = Field(description="Step-by-step correct reasoning chain")

    variation_tags: list[str] = Field(
        default_factory=list,
        description="Tags describing edge case dimensions, e.g. ['income_threshold', 'asset_test']",
    )
    source_citations: list[str] = Field(
        default_factory=list,
        description="Policy document citations grounding this case",
    )

    # Generation metadata
    generated_by: str = Field(default="synthetic-gov-data-kit@0.1.0")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    seed: int | None = Field(default=None, description="RNG seed used for generation")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary generation metadata",
    )

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, v: str) -> str:
        """Enforce the case ID format: lowercase, dot-separated segments."""
        pattern = r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid case_id '{v}'. "
                "Must be lowercase dot-separated, e.g. 'snap.va.eligibility.gross_income_at_limit'"
            )
        return v

    @field_validator("program")
    @classmethod
    def validate_program(cls, v: str) -> str:
        from govsynth.models.enums import KNOWN_PROGRAMS

        if v in KNOWN_PROGRAMS:
            return v
        # Escape hatch for programs sourced from an imported ruleset (e.g. the
        # Catala adapter) that aren't one of the built-in KNOWN_PROGRAMS.
        # Still must be a lowercase_snake_case slug, not an arbitrary string.
        if re.fullmatch(r"[a-z0-9_]+", v):
            return v
        raise ValueError(
            f"Invalid program '{v}'. Must be one of {KNOWN_PROGRAMS} or a "
            "lowercase_snake_case identifier for a custom-imported program."
        )

    @model_validator(mode="after")
    def validate_rationale_has_steps(self) -> TestCase:
        if len(self.rationale_trace.steps) < 2:
            raise ValueError("RationaleTrace must have at least 2 steps to be compatible")
        return self

    @model_validator(mode="after")
    def validate_has_citations(self) -> TestCase:
        if not self.source_citations:
            raise ValueError("TestCase must have at least one source citation")
        return self

    def check_output_contract(self) -> list[str]:
        """Run compatibility checks. Returns list of error strings (empty = valid).

        Named to avoid shadowing pydantic's inherited `BaseModel.validate`
        classmethod, which has an unrelated signature and purpose.
        """
        errors: list[str] = []

        if not self.case_id:
            errors.append("case_id is empty")
        if not self.scenario.summary:
            errors.append("scenario.summary is empty")
        if not self.task.instruction:
            errors.append("task.instruction is empty")
        if not self.expected_outcome:
            errors.append("expected_outcome is empty")
        if not self.expected_answer:
            errors.append("expected_answer is empty")
        if len(self.rationale_trace.steps) < 2:
            errors.append("rationale_trace must have >= 2 steps")
        if not self.source_citations:
            errors.append("source_citations must have >= 1 entry")

        return errors

    def is_valid(self) -> bool:
        """Quick boolean validity check."""
        return len(self.check_output_contract()) == 0

    def short_repr(self) -> str:
        """One-line summary for logging."""
        return (
            f"TestCase({self.case_id}, {self.difficulty.value}, "
            f"outcome={self.expected_outcome}, steps={self.rationale_trace.step_count()})"
        )
