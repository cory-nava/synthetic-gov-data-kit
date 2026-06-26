"""Rationale trace data structures.

A RationaleTrace captures the step-by-step reasoning chain that a correct model
should follow when answering a test case. This enables evaluation of
*how* a model reasons, not just *what* it answers.
"""

from typing import Any

from pydantic import BaseModel, Field


class PolicyCitation(BaseModel):
    """A citation to a specific policy document or regulation."""

    document: str = Field(description="Name of the regulation or handbook")
    section: str = Field(description="Specific section, e.g. '7 CFR 273.9(a)(1)'")
    year: int = Field(description="Fiscal or calendar year this citation applies to")
    url: str | None = Field(default=None, description="URL to authoritative source")

    def __str__(self) -> str:
        return f"{self.document}, {self.section} ({self.year})"


class ReasoningStep(BaseModel):
    """A single step in a policy reasoning chain."""

    step_number: int = Field(description="1-based step index")
    title: str = Field(description="Short label for this step, e.g. 'Check gross income limit'")
    rule_applied: str = Field(
        description="The specific policy rule or CFR section applied at this step"
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value pairs of values fed into this step",
    )
    computation: str = Field(description="What was compared or calculated, in plain language")
    result: str = Field(description="Outcome of this step, e.g. 'PASS' or 'net_income = $1,651'")
    is_determinative: bool = Field(
        default=False,
        description="If True, this step alone decides the final outcome",
    )
    note: str | None = Field(
        default=None,
        description="Optional clarification or edge case note",
    )


class RationaleTrace(BaseModel):
    """The complete reasoning chain for a test case.

    Each TestCase carries a RationaleTrace that documents the correct policy
    reasoning path — every rule invoked, every threshold compared, every
    deduction applied. This is the ground truth against which model rationales
    are scored.
    """

    steps: list[ReasoningStep] = Field(description="Ordered list of reasoning steps")
    conclusion: str = Field(description="Final conclusion summarizing all steps and the outcome")
    policy_basis: list[PolicyCitation] = Field(
        default_factory=list,
        description="Policy documents and regulations that ground this trace",
    )

    def step_count(self) -> int:
        """Return the number of reasoning steps."""
        return len(self.steps)

    def determinative_steps(self) -> list[ReasoningStep]:
        """Return steps that are individually determinative of the outcome."""
        return [s for s in self.steps if s.is_determinative]

    def cited_rules(self) -> list[str]:
        """Return all unique CFR/rule citations across all steps."""
        return list(dict.fromkeys(s.rule_applied for s in self.steps))

    def to_plain_text(self) -> str:
        """Render the trace as readable plain text."""
        lines = []
        for step in self.steps:
            lines.append(f"Step {step.step_number}: {step.title}")
            lines.append(f"  Rule: {step.rule_applied}")
            lines.append(f"  Computation: {step.computation}")
            lines.append(f"  Result: {step.result}")
            if step.note:
                lines.append(f"  Note: {step.note}")
            lines.append("")
        lines.append(f"Conclusion: {self.conclusion}")
        return "\n".join(lines)
