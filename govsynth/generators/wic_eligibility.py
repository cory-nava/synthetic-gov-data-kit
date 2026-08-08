"""WIC eligibility test case generator.

Generates compatible TestCase objects for WIC eligibility determination.
WIC is simpler than SNAP: one income test (185% FPL), no asset test,
categorical eligibility, and a participant category requirement.
"""

from __future__ import annotations

import random

from govsynth.fiscal_year import DEFAULT_WIC_FY
from govsynth.generators.base import Generator
from govsynth.models.enums import Difficulty, Program, TaskType
from govsynth.models.rationale import PolicyCitation, RationaleTrace, ReasoningStep
from govsynth.models.test_case import ScenarioBlock, TaskBlock, TestCase
from govsynth.profiles.us_household import USHouseholdProfile
from govsynth.reasoning.rules_engine import build_short_uid
from govsynth.sources.us.wic import WICSource

_PARTICIPANT_CATEGORIES = [
    "pregnant",
    "breastfeeding",
    "postpartum",
    "infant",
    "child_under_5",
]

_CATEGORICAL_ELIGIBILITY_PROGRAMS = ["snap", "medicaid", "tanf"]

_TASK_INSTRUCTION = (
    "Based on the situation described, determine whether this individual or household "
    "is eligible for WIC (Special Supplemental Nutrition Program for Women, Infants, "
    "and Children) benefits. Explain your reasoning step by step, citing the applicable "
    "regulations. State your final determination (income-eligible, categorically eligible, "
    "or ineligible) and note any additional requirements that must be met."
)


class WICEligibilityGenerator(Generator):
    """Generates WIC eligibility determination test cases.

    Args:
        fiscal_year: Federal fiscal year for WIC income guidelines. Default FY2026.
        state: State code (WIC income limits are national; state affects clinic access).
    """

    def __init__(self, fiscal_year: int = DEFAULT_WIC_FY, state: str = "national") -> None:
        self.fiscal_year = fiscal_year
        self.state = state
        self.source = WICSource(fiscal_year=fiscal_year, state=state)

    @property
    def program(self) -> str:
        return "wic"

    def generate(
        self,
        n: int,
        profile_strategy: str = "edge_saturated",
        seed: int | None = None,
    ) -> list[TestCase]:
        """Generate n WIC eligibility test cases."""
        rng = random.Random(seed)
        cases: list[TestCase] = []

        for i in range(n):
            case_seed = rng.randint(0, 2**31) if seed is not None else None
            try:
                case = self._build_case(rng, case_seed, i, profile_strategy)
                cases.append(case)
            except Exception as exc:
                print(f"  Warning: skipped WIC case {i}: {exc}")

        return cases

    def _build_case(self, rng: random.Random, seed: int | None, index: int, strategy: str) -> TestCase:
        category = rng.choice(_PARTICIPANT_CATEGORIES)
        is_cat_eligible = rng.random() < 0.20  # 20% are categorically eligible via SNAP/Medicaid

        hh_size = rng.choices([1, 2, 3, 4, 5], weights=[0.30, 0.30, 0.20, 0.15, 0.05])[0]

        if strategy == "edge_saturated":
            offset = rng.choice([0.0, 0.01, -0.01, 0.05, -0.10])
            profile = USHouseholdProfile.at_threshold(
                program="wic",
                threshold="income_limit_185pct_fpl",
                state=self.state if self.state != "national" else "VA",
                household_size=hh_size,
                fiscal_year=self.fiscal_year,
                offset_pct=offset,
                seed=seed,
            )
        else:
            profile = USHouseholdProfile.random(
                state=self.state if self.state != "national" else "VA",
                seed=seed,
            )

        t = self.source.thresholds()
        limits = t.by_household_size(min(hh_size, 8))

        is_eligible, reason = self.source.is_eligible(
            household_size=hh_size,
            monthly_gross_income=profile.monthly_gross_income,
            participant_category=category,
            is_categorically_eligible=is_cat_eligible,
        )

        trace = self._build_trace(profile, limits, category, is_cat_eligible, is_eligible)
        difficulty = self._classify_difficulty(profile, is_cat_eligible)
        case_id = self._make_id(profile, category, is_eligible, rng)
        scenario = self._build_scenario(profile, category, is_cat_eligible)
        answer = self._build_answer(profile, limits, category, is_cat_eligible, is_eligible)
        tags = self._build_tags(profile, category, is_cat_eligible)

        return TestCase(
            case_id=case_id,
            program=Program.WIC.value,
            jurisdiction=f"us.{(self.state if self.state != 'national' else 'national').lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=difficulty,
            scenario=ScenarioBlock(
                summary=scenario,
                **{k: v for k, v in profile.to_scenario_fields().items()},
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome="eligible" if is_eligible else "ineligible",
            expected_answer=answer,
            rationale_trace=trace,
            variation_tags=tags,
            source_citations=[
                "7 CFR Part 246 (WIC Program Regulations)",
                f"USDA FNS WIC Income Eligibility Guidelines {self.source.fy_config.period_label}",
            ],
            seed=seed,
            metadata={
                "generator": "WICEligibilityGenerator",
                "participant_category": category,
                "is_categorically_eligible": is_cat_eligible,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_trace(
        self,
        profile: USHouseholdProfile,
        limits: object,
        category: str,
        is_cat_eligible: bool,
        is_eligible: bool,
    ) -> RationaleTrace:
        steps: list[ReasoningStep] = []
        fy = self.source.fy_config

        # Step 1: Participant category check
        valid_cats = ["pregnant", "breastfeeding", "postpartum", "infant", "child_under_5"]
        cat_valid = category in valid_cats
        steps.append(
            ReasoningStep(
                step_number=1,
                title="Check participant category eligibility",
                rule_applied="7 CFR 246.7(a)",
                inputs={"participant_category": category, "valid_categories": valid_cats},
                computation=f"Category '{category}' {'is' if cat_valid else 'is NOT'} a WIC-eligible category.",
                result="PASS" if cat_valid else "FAIL — not a WIC-eligible category",
                is_determinative=not cat_valid,
            )
        )

        if not cat_valid:
            return RationaleTrace(
                steps=steps,
                conclusion=f"INELIGIBLE: '{category}' is not a WIC-eligible participant category.",
                policy_basis=[
                    PolicyCitation(document="7 CFR Part 246", section="7 CFR 246.7(a)", year=self.fiscal_year)
                ],
            )

        # Step 2: Categorical eligibility check
        steps.append(
            ReasoningStep(
                step_number=2,
                title="Check categorical eligibility",
                rule_applied="7 CFR 246.7(d)(2)",
                inputs={"receives_snap_medicaid_tanf": is_cat_eligible},
                computation=(
                    "Household receives SNAP/Medicaid/TANF — income test automatically satisfied."
                    if is_cat_eligible
                    else "Household does not receive SNAP/Medicaid/TANF — must pass income test."
                ),
                result="CATEGORICALLY ELIGIBLE — income test waived" if is_cat_eligible else "Must pass income test",
                is_determinative=False,
            )
        )

        if not is_cat_eligible:
            # Step 3: Income test
            income_pass = profile.monthly_gross_income <= getattr(limits, "gross_monthly", 9999)
            steps.append(
                ReasoningStep(
                    step_number=3,
                    title="Apply 185% FPL income test",
                    rule_applied="7 CFR 246.7(d)(1)",
                    inputs={
                        "monthly_gross_income": profile.monthly_gross_income,
                        "income_limit_185pct_fpl": getattr(limits, "gross_monthly", 0),
                        "household_size": profile.household_size,
                        "period": fy.period_label,
                    },
                    computation=(
                        f"${profile.monthly_gross_income:,.2f} "
                        f"{'≤' if income_pass else '>'} "
                        f"${getattr(limits, 'gross_monthly', 0):,.2f} "
                        f"(185% FPL, {profile.household_size}-person HH, {fy.period_label})"
                    ),
                    result="PASS" if income_pass else "FAIL — exceeds 185% FPL income limit",
                    is_determinative=not income_pass,
                )
            )

        # Step 4: Nutritional risk reminder (always required)
        steps.append(
            ReasoningStep(
                step_number=len(steps) + 1,
                title="Nutritional risk determination",
                rule_applied="7 CFR 246.7(e)",
                inputs={},
                computation=(
                    "Income eligibility alone does not confer WIC enrollment. "
                    "A nutritional risk assessment by WIC clinic staff is required."
                ),
                result="Nutritional risk assessment REQUIRED at WIC clinic",
                is_determinative=False,
                note=("This step is always required and is outside the scope of income eligibility determination."),
            )
        )

        if is_eligible:
            eligibility_basis = (
                "categorically eligible via SNAP/Medicaid/TANF"
                if is_cat_eligible
                else f"income ${profile.monthly_gross_income:,.2f} passes 185% FPL test"
            )
            conclusion = (
                f"{'INCOME-ELIGIBLE' if is_eligible else 'INELIGIBLE'} for WIC "
                f"({eligibility_basis}). Nutritional risk assessment still required."
            )
        else:
            conclusion = (
                f"INELIGIBLE for WIC. Monthly gross income ${profile.monthly_gross_income:,.2f} "
                f"exceeds ${getattr(limits, 'gross_monthly', 0):,.2f} (185% FPL limit, {fy.period_label})."
            )

        return RationaleTrace(
            steps=steps,
            conclusion=conclusion,
            policy_basis=[
                PolicyCitation(
                    document="7 CFR Part 246",
                    section="7 CFR 246.7",
                    year=self.fiscal_year,
                    url="https://www.ecfr.gov/current/title-7/part-246",
                ),
                PolicyCitation(
                    document=f"USDA FNS WIC Income Eligibility Guidelines {fy.period_label}",
                    section="Income Eligibility Table",
                    year=self.fiscal_year,
                    url="https://www.fns.usda.gov/wic/eligibility",
                ),
            ],
        )

    def _build_scenario(self, profile: USHouseholdProfile, category: str, is_cat: bool) -> str:
        cat_desc = {
            "pregnant": "is pregnant",
            "breastfeeding": "is breastfeeding an infant",
            "postpartum": "recently gave birth (postpartum, not breastfeeding)",
            "infant": "has a newborn infant",
            "child_under_5": "has a child under age 5",
        }.get(category, f"is a '{category}' WIC applicant")

        base = profile.natural_language_summary("wic")
        cat_note = f" {profile.head_of_household_name} {cat_desc}."
        cat_program = "SNAP" if hash(profile.head_of_household_name) % 2 == 0 else "Medicaid"
        cat_elig_note = f" The household currently receives {cat_program} benefits." if is_cat else ""
        return base + cat_note + cat_elig_note

    def _build_answer(
        self,
        profile: USHouseholdProfile,
        limits: object,
        category: str,
        is_cat: bool,
        is_eligible: bool,
    ) -> str:
        fy = self.source.fy_config
        limit = getattr(limits, "gross_monthly", 0)
        if is_eligible:
            how = (
                "categorically eligible (receives SNAP/Medicaid/TANF — income test waived)"
                if is_cat
                else f"income-eligible (${profile.monthly_gross_income:,.2f}/month ≤ ${limit:,.2f} at 185% FPL)"
            )
            return (
                f"This individual is WIC-ELIGIBLE ({fy.period_label}). They are {how}. "
                f"The participant category '{category}' is a valid WIC category. "
                f"Note: income eligibility does not guarantee enrollment — a nutritional risk "
                f"assessment must be completed at a local WIC clinic (7 CFR 246.7(e))."
            )
        return (
            f"This individual is INELIGIBLE for WIC ({fy.period_label}). "
            f"Monthly gross income ${profile.monthly_gross_income:,.2f} exceeds the "
            f"185% FPL income limit of ${limit:,.2f} for a {profile.household_size}-person household. "
            f"They do not receive SNAP, Medicaid, or TANF to qualify via categorical eligibility."
        )

    def _classify_difficulty(self, profile: USHouseholdProfile, is_cat: bool) -> Difficulty:
        if is_cat:
            # Categorical eligibility bypasses the 185% FPL income test outright --
            # the household's distance from that limit was never evaluated. That is
            # not "clearly clear of every limit" (EASY); it is the same "computation
            # changed regardless of distance" population SNAP labels MEDIUM for
            # elderly/disabled households (see Difficulty.MEDIUM's docstring).
            return Difficulty.MEDIUM
        offset = profile.extra.get("offset_pct")
        if offset is None:
            # Sampled independently of any threshold: we do not know how far this
            # household sits from a limit, so we must not claim it is clear of one.
            return Difficulty.MEDIUM
        if abs(offset) <= 0.01:
            return Difficulty.HARD
        if abs(offset) <= 0.05:
            return Difficulty.MEDIUM
        return Difficulty.EASY

    def _make_id(self, profile: USHouseholdProfile, category: str, is_eligible: bool, rng: random.Random) -> str:
        # offset_pct is absent (extra == {}) for 'uniform'/'realistic' profiles, so
        # there is no known distance from a threshold. Defaulting it to 0.0 would
        # fabricate an "at_limit" claim the profile does not know to be true --
        # mirror _build_tags below and omit the segment entirely instead.
        offset = profile.extra.get("offset_pct")
        outcome = "eligible" if is_eligible else "ineligible"
        uid = build_short_uid(rng)
        state = (self.state if self.state != "national" else "national").lower()
        segments = ["wic", state, "eligibility", category]
        if offset is not None:
            offset_tag = "at_limit" if offset == 0.0 else ("above_limit" if offset > 0 else "below_limit")
            segments.append(offset_tag)
        segments.extend([outcome, f"hh{profile.household_size}", uid])
        return ".".join(segments)

    def _build_tags(self, profile: USHouseholdProfile, category: str, is_cat: bool) -> list[str]:
        tags = [category, "income_test_185pct_fpl"]
        if is_cat:
            tags.append("categorical_eligibility")
        offset = profile.extra.get("offset_pct", None)
        if offset is not None:
            tags.append("at_limit" if offset == 0.0 else ("above_limit" if offset > 0 else "below_limit"))
        return tags
