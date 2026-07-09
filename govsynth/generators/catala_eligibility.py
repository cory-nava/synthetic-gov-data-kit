"""Catala-backed eligibility test case generator.

Generates TestCase objects for an arbitrary imported Catala ruleset: the
ruleset (not a hand-written data/thresholds/*.json + Python is_eligible())
is the source of truth for both thresholds and eligibility logic. Each case
samples a synthetic household profile, runs it through the ruleset's scope
via `CatalaRuntime`, and turns the scope's outputs (and explanation trace,
when available) into a TestCase.
"""

from __future__ import annotations

import random
from typing import Any

from govsynth.generators.base import Generator
from govsynth.models.enums import Difficulty, TaskType
from govsynth.models.rationale import PolicyCitation, RationaleTrace, ReasoningStep
from govsynth.models.test_case import ScenarioBlock, TaskBlock, TestCase
from govsynth.profiles.us_household import USHouseholdProfile
from govsynth.sources.catala.ruleset import CatalaRuleset
from govsynth.sources.catala.runtime import CatalaResult

_TASK_INSTRUCTION = (
    "Based on the household's situation described above, determine whether this "
    "household is eligible for this benefit. Show your reasoning step by step, "
    "citing the applicable rule or provision at each step. State your final "
    "determination (eligible or ineligible)."
)


def _short_uid(rng: random.Random) -> str:
    """Deterministic-with-seed hex suffix for case IDs (never uuid.uuid4() --
    that would ignore `rng`'s seed and break generate(n, seed=42) reproducibility)."""
    return f"{rng.getrandbits(24):06x}"


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "eligible")
    return bool(value)


class CatalaEligibilityGenerator(Generator):
    """Generates eligibility determination test cases from an imported Catala ruleset.

    Args:
        ruleset: A loaded `CatalaRuleset` (see `CatalaRuleset.load`).
        state: State code recorded on generated scenarios/case_ids. Purely
            descriptive metadata here -- the ruleset itself decides whether/how
            state affects the outcome, govsynth doesn't special-case it.
        difficulty: Difficulty label applied to every generated case (Catala
            rulesets aren't threshold-boundary-tuned the way the built-in
            SNAP/WIC/Medicaid edge-saturated profiles are, so there's no
            principled way to vary this automatically).
        profile_strategy_default: `profile_strategy` used when `generate()`
            isn't given one explicitly.

    Raises:
        CatalaMappingError: at construction time, if the ruleset's declared
            scope inputs can't be resolved against a reference profile's
            fields -- fails fast rather than raising the same error on every
            one of `n` generated cases.
    """

    def __init__(
        self,
        ruleset: CatalaRuleset,
        state: str = "VA",
        difficulty: Difficulty = Difficulty.MEDIUM,
        profile_strategy_default: str = "uniform",
    ) -> None:
        self.ruleset = ruleset
        self.state = state.upper()
        self.difficulty = difficulty
        self.profile_strategy_default = profile_strategy_default

        # Fail fast: validate the field mapping once against a reference
        # profile instead of discovering it's incomplete on every case.
        reference_profile = USHouseholdProfile.random(state=self.state, seed=0)
        self.ruleset.build_inputs(reference_profile)

    @property
    def program(self) -> str:
        return self.ruleset.program_name

    def generate(
        self,
        n: int,
        profile_strategy: str | None = None,
        seed: int | None = None,
    ) -> list[TestCase]:
        """Generate n test cases by running sampled profiles through the ruleset."""
        strategy = profile_strategy or self.profile_strategy_default
        rng = random.Random(seed)
        cases: list[TestCase] = []

        for i in range(n):
            case_seed = rng.randint(0, 2**31) if seed is not None else None
            try:
                profile = USHouseholdProfile.random(state=self.state, seed=case_seed, strategy=strategy)
                case = self._build_case(profile, rng, case_seed)
                cases.append(case)
            except Exception as exc:
                print(f"  Warning: skipped Catala case {i} ({self.ruleset.scope}): {exc}")

        return cases

    def _build_case(self, profile: USHouseholdProfile, rng: random.Random, seed: int | None) -> TestCase:
        result = self.ruleset.run(profile, with_trace=True)
        is_eligible = _coerce_bool(result.outputs.get(self.ruleset.outcome_field))
        outcome = "eligible" if is_eligible else "ineligible"

        uid = _short_uid(rng)
        case_id = (
            f"{self.program}.{self.jurisdiction_slug}.eligibility."
            f"catala_ruleset.{outcome}.hh{profile.household_size}.{uid}"
        )

        trace = self._build_rationale_trace(profile, result)
        answer = self._build_expected_answer(is_eligible, result)

        return TestCase(
            case_id=case_id,
            program=self.program,
            jurisdiction=f"{self.ruleset.jurisdiction}.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=self.difficulty,
            scenario=ScenarioBlock(
                summary=profile.natural_language_summary(self.program),
                **{k: v for k, v in profile.to_scenario_fields().items()},
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=answer,
            rationale_trace=trace,
            variation_tags=["catala_import", self.ruleset.scope],
            source_citations=[
                self.ruleset.citation or f"Catala ruleset: {self.ruleset.path.name}, scope '{self.ruleset.scope}'"
            ],
            seed=seed,
            metadata={
                "generator": "CatalaEligibilityGenerator",
                "catala_ruleset_path": str(self.ruleset.path),
                "catala_scope": self.ruleset.scope,
                "catala_outputs": result.outputs,
                "state": self.state,
            },
        )

    @property
    def jurisdiction_slug(self) -> str:
        return self.state.lower()

    def _build_rationale_trace(self, profile: USHouseholdProfile, result: CatalaResult) -> RationaleTrace:
        citation = self.ruleset.citation or f"Catala ruleset {self.ruleset.path.name}, scope '{self.ruleset.scope}'"
        policy_basis = [PolicyCitation(document=citation, section=self.ruleset.scope, year=self.ruleset.citation_year)]

        steps = self._trace_to_steps(result.trace)
        if len(steps) < 2:
            # Guaranteed->=2-step fallback so the pydantic model_validator on
            # TestCase (rationale_trace must have >= 2 steps) is always
            # satisfied, even with no usable trace from `catala interpret`.
            steps = [
                ReasoningStep(
                    step_number=1,
                    title=f"Evaluate Catala scope '{self.ruleset.scope}'",
                    rule_applied=citation,
                    inputs=self.ruleset.build_inputs(profile),
                    computation=(
                        f"Ran scope '{self.ruleset.scope}' from {self.ruleset.path.name} with the household's inputs."
                    ),
                    result="Computation completed",
                    is_determinative=False,
                ),
                ReasoningStep(
                    step_number=2,
                    title=f"Read '{self.ruleset.outcome_field}' output",
                    rule_applied=citation,
                    inputs={"outputs": result.outputs},
                    computation=(f"Scope output '{self.ruleset.outcome_field}' determines eligibility."),
                    result=str(result.outputs.get(self.ruleset.outcome_field)),
                    is_determinative=True,
                ),
            ]

        is_eligible = _coerce_bool(result.outputs.get(self.ruleset.outcome_field))
        conclusion = f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'} per Catala scope '{self.ruleset.scope}'."
        return RationaleTrace(steps=steps, conclusion=conclusion, policy_basis=policy_basis)

    def _trace_to_steps(self, trace_events: list[dict[str, Any]]) -> list[ReasoningStep]:
        """Best-effort mapping of catala's explanation trace into ReasoningSteps.

        The trace event schema wasn't independently verified against a real
        `catala` binary (see runtime.py's module docstring), so this reads
        several plausible key names defensively rather than assuming one
        fixed shape, and simply produces fewer steps if the shape doesn't
        match -- the >=2-step fallback in `_build_rationale_trace` covers
        the rest.
        """
        steps: list[ReasoningStep] = []
        for i, event in enumerate(trace_events, start=1):
            title = (
                event.get("variable")
                or event.get("rule")
                or event.get("name")
                or event.get("event_type")
                or f"Rule application {i}"
            )
            rule_applied = str(
                event.get("rule") or event.get("position") or event.get("justification") or self.ruleset.scope
            )
            value = event.get("value", event.get("result"))
            computation = str(event.get("justification") or event.get("expression") or title)
            steps.append(
                ReasoningStep(
                    step_number=i,
                    title=str(title)[:200],
                    rule_applied=rule_applied[:200],
                    inputs={k: v for k, v in event.items() if k not in ("value", "result")},
                    computation=computation,
                    result=str(value) if value is not None else "",
                    is_determinative=False,
                )
            )
        return steps

    def _build_expected_answer(self, is_eligible: bool, result: CatalaResult) -> str:
        label = "ELIGIBLE" if is_eligible else "INELIGIBLE"
        extra_fields = {k: v for k, v in result.outputs.items() if k != self.ruleset.outcome_field}
        extra_note = f" Additional computed values: {extra_fields}." if extra_fields else ""
        return (
            f"This household is {label} according to the imported Catala ruleset "
            f"'{self.ruleset.path.name}' (scope '{self.ruleset.scope}').{extra_note}"
        )
