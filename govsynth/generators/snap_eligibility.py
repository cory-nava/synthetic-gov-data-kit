"""SNAP eligibility test case generator.

Generates compatible TestCase objects for SNAP eligibility determination,
including full rationale traces grounded in 7 CFR Part 273.
"""

from __future__ import annotations

import random

from govsynth.fiscal_year import DEFAULT_SNAP_FY, FiscalYearConfig
from govsynth.generators.base import Generator
from govsynth.models.enums import Difficulty, Program, TaskType
from govsynth.models.rationale import PolicyCitation, RationaleTrace, ReasoningStep
from govsynth.models.test_case import ScenarioBlock, TaskBlock, TestCase
from govsynth.profiles.us_household import USHouseholdProfile
from govsynth.reasoning.rules_engine import build_short_uid
from govsynth.sources.base import HouseholdThreshold
from govsynth.sources.us.snap import SNAPSource, get_standard_deduction
from govsynth.sources.us.snap_bbce import SNAPBBCESource

# Threshold types used in edge-saturated generation
_SNAP_THRESHOLD_TYPES = [
    "gross_income_limit",
    "net_income_limit",
    "asset_limit_general",
    "asset_limit_elderly_disabled",
]

# Offsets from a threshold, as a fraction. Values within +/-0.05 sit on the
# boundary and drive HARD/MEDIUM classification; the +/-0.35 pair produces
# households clearly clear of any limit, which is what EASY means here.
# _classify_difficulty requires abs(offset) > 0.30 for EASY, so without these
# that branch is unreachable.
_OFFSETS = [0.0, 0.01, -0.01, 0.05, -0.05, 0.35, -0.35]

_TASK_INSTRUCTION = (
    "Based on the household's situation described above, determine whether this household "
    "is eligible for SNAP (Supplemental Nutrition Assistance Program) benefits. "
    "Show your reasoning step by step, citing the specific federal regulations that apply. "
    "State your final determination (eligible or ineligible) and, if eligible, estimate "
    "the approximate monthly benefit amount."
)


class SNAPEligibilityGenerator(Generator):
    """Generates SNAP eligibility determination test cases.

    Each case includes:
      - A synthetic household scenario
      - The eligibility determination task
      - The expected outcome and answer
      - A full rationale trace (7 CFR Part 273)

    Args:
        fiscal_year: Federal fiscal year for thresholds. Default: FY2026.
        state: State code. Controls BBCE asset test rules.

    Difficulty is not a caller-supplied target: it is derived from each generated
    household profile, in a fixed order of precedence. A profile sampled without a
    threshold offset has no known distance from a limit and falls back to MEDIUM.
    Otherwise, sitting on the boundary (within 1% of a threshold) is checked first
    and yields HARD -- even for an elderly/disabled household, since that check
    runs before the special-population check. Only once the on-threshold check has
    passed does elderly/disabled status force MEDIUM, regardless of how far from a
    limit the household actually sits. A household with neither property that is
    comfortably clear of every limit (more than 30% away) is EASY; every other case
    is MEDIUM. The resulting mix of difficulty levels in the output is emergent, not
    requested.
    """

    def __init__(
        self,
        fiscal_year: int = DEFAULT_SNAP_FY,
        state: str = "VA",
    ) -> None:
        self.fiscal_year = fiscal_year
        self.state = state.upper()
        # Federal baseline, used by the special-population builders (which are framed
        # around federal rules). The main threshold path and the BBCE builder use the
        # BBCE-aware source so per-state gross/asset rules apply.
        self.source = SNAPSource(fiscal_year=fiscal_year, state=state)
        self.bbce_source = SNAPBBCESource(fiscal_year=fiscal_year, state=state)

    @property
    def program(self) -> str:
        return "snap"

    def generate(
        self,
        n: int,
        profile_strategy: str = "edge_saturated",
        seed: int | None = None,
    ) -> list[TestCase]:
        """Generate n SNAP eligibility test cases.

        When profile_strategy is 'edge_saturated', 20% of cases (minimum 1 per special
        type if n >= 6) are special-population edge cases. The remainder use threshold-boundary
        profiles.

        Args:
            n: Number of cases to generate.
            profile_strategy: 'edge_saturated' | 'uniform' | 'realistic'
            seed: RNG seed for reproducibility.

        Returns:
            List of TestCase objects.
        """
        rng = random.Random(seed)

        if profile_strategy != "edge_saturated":
            # Non-edge-saturated strategies: use existing random profile path
            cases: list[TestCase] = []
            for i in range(n):
                case_seed = rng.randint(0, 2**31) if seed is not None else None
                profile = USHouseholdProfile.random(state=self.state, seed=case_seed, strategy=profile_strategy)
                try:
                    case = self._build_case(profile, case_seed, i)
                    cases.append(case)
                except Exception as exc:
                    print(f"  Warning: skipped case {i} due to error: {exc}")
            return cases

        # edge_saturated: two-phase split
        n_special = max(0, min(int(n * 0.20), n))
        n_special = max(n_special, min(7, n))  # guarantee >= 1 per type if n >= 7
        n_edge = n - n_special

        special_cases = self._build_special_population_cases(n_special, rng)

        edge_cases: list[TestCase] = []
        for i in range(n_edge):
            case_seed = rng.randint(0, 2**31) if seed is not None else None
            profile = self._sample_edge_profile(rng, case_seed)
            try:
                case = self._build_case(profile, case_seed, i)
                edge_cases.append(case)
            except Exception as exc:
                print(f"  Warning: skipped edge case {i} due to error: {exc}")

        return special_cases + edge_cases

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_special_population_cases(self, n: int, rng: random.Random) -> list[TestCase]:
        """Build n special-population edge cases, cycling through 7 types.

        When n < 7, cycles through first n types. When n >= 7, guarantees at least
        one case per type.
        """
        builders = [
            ("_build_homeless_case", self._build_homeless_case),
            ("_build_student_case", self._build_student_case),
            ("_build_boarder_case", self._build_boarder_case),
            ("_build_migrant_case", self._build_migrant_case),
            ("_build_mixed_immigration_case", self._build_mixed_immigration_case),
            ("_build_categorical_eligibility_case", self._build_categorical_eligibility_case),
            ("_build_bbce_expanded_income_case", self._build_bbce_expanded_income_case),
        ]
        cases: list[TestCase] = []
        for i in range(n):
            name, builder = builders[i % len(builders)]
            try:
                case = builder(rng)
                cases.append(case)
            except Exception as exc:
                raise RuntimeError(
                    f"special-case builder {name!r} failed while building case {i}: {exc}"
                ) from exc
        return cases

    def _build_homeless_case(self, rng: random.Random) -> TestCase:
        """Build a homeless shelter deduction edge case (7 CFR 273.9(c)(6))."""
        t = self.source.thresholds()
        assert t.extra is not None, "SNAP thresholds always populate `extra`"
        fy_config = self.source.fy_config
        hh_size = rng.randint(1, 3)
        limits = t.by_household_size(hh_size)

        # Income: randomly placed near the threshold so outcome varies
        gross = round(rng.uniform(limits.gross_monthly * 0.60, limits.gross_monthly * 1.10), 2)

        net_income = self.source.calculate_net_income(
            gross_income=gross,
            household_size=hh_size,
            earned_income=gross,
            shelter_costs=None,  # homeless: no actual shelter costs
            is_homeless=True,
        )

        is_eligible, reason = self.source.is_eligible(
            household_size=hh_size,
            gross_income=gross,
            net_income=net_income,
            liquid_assets=0.0,
        )

        homeless_ded = t.extra["homeless_shelter_deduction"]
        uid = build_short_uid(rng)
        outcome = "eligible" if is_eligible else "ineligible"
        case_id = f"snap.{self.state.lower()}.eligibility.homeless_shelter_deduction.{outcome}.hh{hh_size}.{uid}"

        std_ded = get_standard_deduction(hh_size)
        earned_ded = gross * 0.20
        after_earned = gross - earned_ded
        after_standard = after_earned - std_ded

        steps = [
            ReasoningStep(
                step_number=1,
                title="Check gross income limit",
                rule_applied="7 CFR 273.9(a)(1)",
                inputs={
                    "gross_income": gross,
                    "gross_limit": limits.gross_monthly,
                    "household_size": hh_size,
                },
                computation=(
                    f"${gross:,.2f} {'<=' if gross <= limits.gross_monthly else '>'} "
                    f"${limits.gross_monthly:,.2f} (130% FPL, {hh_size}-person HH)"
                ),
                result="PASS" if gross <= limits.gross_monthly else "FAIL",
                is_determinative=gross > limits.gross_monthly,
            ),
            ReasoningStep(
                step_number=2,
                title="Apply standard deductions (earned income + standard)",
                rule_applied="7 CFR 273.9(c)(1),(c)(2)",
                inputs={"earned_deduction": earned_ded, "standard_deduction": std_ded},
                computation=(
                    f"${gross:,.2f} − ${earned_ded:,.2f} (20% earned) − "
                    f"${std_ded:,.0f} (standard) = ${after_standard:,.2f}"
                ),
                result=f"After earned + standard deductions: ${after_standard:,.2f}",
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=3,
                title="Apply homeless shelter deduction (flat $198.99)",
                rule_applied="7 CFR 273.9(c)(6)",
                inputs={"homeless_shelter_deduction": homeless_ded},
                computation=(
                    f"Household is homeless — apply flat homeless shelter deduction of ${homeless_ded:,.2f} "
                    f"INSTEAD OF excess shelter deduction (7 CFR 273.9(c)(6)). These two deductions are "
                    f"mutually exclusive. Net income: ${after_standard:,.2f} − "
                    f"${homeless_ded:,.2f} = ${net_income:,.2f}"
                ),
                result=f"Net income after homeless deduction: ${net_income:,.2f}",
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=4,
                title="Check net income limit",
                rule_applied="7 CFR 273.9(a)(2)",
                inputs={"net_income": round(net_income, 2), "net_limit": limits.net_monthly},
                computation=(
                    f"${net_income:,.2f} {'<=' if net_income <= limits.net_monthly else '>'} "
                    f"${limits.net_monthly:,.2f} (100% FPL, {hh_size}-person HH)"
                ),
                result="PASS" if net_income <= limits.net_monthly else "FAIL",
                is_determinative=net_income > limits.net_monthly,
            ),
        ]

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A {hh_size}-person homeless household in {self.state} with "
                    f"${gross:,.0f}/month gross income. The household has no fixed address."
                ),
                household_size=hh_size,
                monthly_gross_income=gross,
                monthly_net_income=round(net_income, 2),
                liquid_assets=0.0,
                state=self.state,
                additional_context={
                    "is_homeless": True,
                    "threshold_type": "homeless_shelter_deduction",
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This household is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'} for SNAP. "
                f"As a homeless household, the flat $198.99 homeless shelter deduction (7 CFR 273.9(c)(6)) "
                f"is applied INSTEAD OF the excess shelter deduction — these are mutually exclusive. "
                f"Net income after deductions: ${net_income:,.2f}."
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. {reason}",
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.9(c)(6)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    )
                ],
            ),
            variation_tags=["homeless_shelter_deduction"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "homeless_shelter_deduction",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_student_case(self, rng: random.Random) -> TestCase:
        """Build a student exclusion edge case (7 CFR 273.5(a),(b)).

        Income is deliberately set BELOW the gross limit to demonstrate that
        the student exclusion fires regardless of income level.
        """
        t = self.source.thresholds()
        fy_config = self.source.fy_config
        hh_size = 1
        limits = t.by_household_size(hh_size)

        # Income well below the gross limit — student is still ineligible
        gross = round(limits.gross_monthly * rng.uniform(0.40, 0.75), 2)

        uid = build_short_uid(rng)
        case_id = f"snap.{self.state.lower()}.eligibility.student_exclusion.ineligible.hh{hh_size}.{uid}"

        steps = [
            ReasoningStep(
                step_number=1,
                title="Check student status (7 CFR 273.5(a))",
                rule_applied="7 CFR 273.5(a)",
                inputs={"student_status": "enrolled_half_time", "household_size": hh_size},
                computation=(
                    "Applicant is enrolled at least half-time at an institution of higher education. "
                    "Under 7 CFR 273.5(a), such students are ineligible for SNAP unless they meet "
                    "one of the exceptions listed in 7 CFR 273.5(b)."
                ),
                result="Student flag: TRIGGERED — must check exceptions",
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=2,
                title="Check 7 CFR 273.5(b) exceptions",
                rule_applied="7 CFR 273.5(b)",
                inputs={
                    "exceptions_checked": [
                        "20hr_work",
                        "single_parent_under6",
                        "tanf",
                        "work_study",
                    ]
                },
                computation=(
                    "Exceptions checked: (1) Working 20+ hours/week — NO. "
                    "(2) Single parent with dependent child under age 6 — NO. "
                    "(3) Receiving TANF — NO. "
                    "(4) Participating in state/federal work-study — NO. "
                    "No exception applies."
                ),
                result="INELIGIBLE — student exclusion applies, no exception met",
                is_determinative=True,
                note=(
                    f"Income test not reached. Note: gross income ${gross:,.2f} is below "
                    f"the ${limits.gross_monthly:,.2f} limit, but income level is irrelevant — "
                    "the student exclusion fires before the income test."
                ),
            ),
        ]

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A college student enrolled half-time in {self.state} with ${gross:,.0f}/month "
                    f"gross income (below the {hh_size}-person gross limit of ${limits.gross_monthly:,.0f}). "
                    f"The student works part-time but fewer than 20 hours/week, has no dependent children, "
                    f"does not receive TANF, and is not enrolled in work-study."
                ),
                household_size=hh_size,
                monthly_gross_income=gross,
                liquid_assets=round(rng.uniform(0, 500), -2),
                state=self.state,
                additional_context={
                    "student_status": "enrolled_half_time",
                    "threshold_type": "student_exclusion",
                    "tanf_recipient": False,
                    "work_study": False,
                    "hours_worked_per_week": rng.randint(5, 15),
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome="ineligible",
            expected_answer=(
                f"This household is INELIGIBLE for SNAP. "
                f"Although the applicant's gross income of ${gross:,.2f} is below the "
                f"${limits.gross_monthly:,.2f} gross income limit, the student exclusion under "
                f"7 CFR 273.5(a) applies. The applicant is enrolled at least half-time and does not "
                f"meet any of the exceptions under 7 CFR 273.5(b). The income test is not reached."
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=(
                    "INELIGIBLE. Student exclusion (7 CFR 273.5(a)) applies — "
                    "no 273.5(b) exception met. Income test not reached."
                ),
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.5(a),(b)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    )
                ],
            ),
            variation_tags=["student_exclusion"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "student_exclusion",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_boarder_case(self, rng: random.Random) -> TestCase:
        """Build a boarder/lodger income proration case (7 CFR 273.1(b)(7))."""
        t = self.source.thresholds()
        fy_config = self.source.fy_config
        hh_size = rng.randint(1, 3)
        limits = t.by_household_size(hh_size)

        # Board payment received; only profit portion counts
        board_total = round(rng.uniform(600, 1200), -1)
        board_cost = round(board_total * rng.uniform(0.55, 0.80), -1)
        board_profit = round(board_total - board_cost, 2)

        # Other income (earned wages)
        other_income = round(rng.uniform(200, limits.gross_monthly * 0.60), 2)
        countable_income = other_income + board_profit  # Only profit counts

        net_income = self.source.calculate_net_income(
            gross_income=countable_income,
            household_size=hh_size,
            earned_income=other_income,  # Board profit is unearned
        )

        is_eligible, reason = self.source.is_eligible(
            household_size=hh_size,
            gross_income=countable_income,
            net_income=net_income,
            liquid_assets=round(rng.uniform(0, 1000), -2),
        )

        uid = build_short_uid(rng)
        outcome = "eligible" if is_eligible else "ineligible"
        case_id = f"snap.{self.state.lower()}.eligibility.boarder_income_proration.{outcome}.hh{hh_size}.{uid}"

        steps = [
            ReasoningStep(
                step_number=1,
                title="Determine countable income from board payments (7 CFR 273.1(b)(7))",
                rule_applied="7 CFR 273.1(b)(7)",
                inputs={"board_payment_received": board_total, "actual_cost_of_board": board_cost},
                computation=(
                    f"Board payment received: ${board_total:,.2f}. Actual cost of providing "
                    f"food/shelter: ${board_cost:,.2f}. Profit (countable income): "
                    f"${board_total:,.2f} − ${board_cost:,.2f} = ${board_profit:,.2f}. "
                    f"Only the profit portion counts as income under 7 CFR 273.1(b)(7)."
                ),
                result=f"Countable boarder income: ${board_profit:,.2f} (profit only)",
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=2,
                title="Calculate total countable gross income",
                rule_applied="7 CFR 273.9(a)(1)",
                inputs={"other_income": other_income, "board_profit": board_profit},
                computation=(
                    f"${other_income:,.2f} (wages) + ${board_profit:,.2f} (board profit) = "
                    f"${countable_income:,.2f} total countable income"
                ),
                result=f"Total gross income: ${countable_income:,.2f} vs limit ${limits.gross_monthly:,.2f}",
                is_determinative=countable_income > limits.gross_monthly,
            ),
            ReasoningStep(
                step_number=3,
                title="Net income after deductions",
                rule_applied="7 CFR 273.9(c)(1),(c)(2)",
                inputs={"gross_income": countable_income, "net_income": round(net_income, 2)},
                computation=f"Net income: ${net_income:,.2f} vs limit ${limits.net_monthly:,.2f}",
                result="PASS" if net_income <= limits.net_monthly else "FAIL",
                is_determinative=net_income > limits.net_monthly,
            ),
        ]

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A {hh_size}-person household in {self.state} earns ${other_income:,.0f}/month in wages "
                    f"and takes in a boarder who pays ${board_total:,.0f}/month. The actual cost of providing "
                    f"food and lodging is ${board_cost:,.0f}/month, leaving a profit of ${board_profit:,.0f}/month."
                ),
                household_size=hh_size,
                monthly_gross_income=countable_income,
                monthly_net_income=round(net_income, 2),
                liquid_assets=round(rng.uniform(0, 1000), -2),
                state=self.state,
                additional_context={
                    "is_boarder": True,
                    "board_payment_total": board_total,
                    "board_cost": board_cost,
                    "boarder_income": board_profit,
                    "threshold_type": "boarder_income_proration",
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This household is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. "
                f"Under 7 CFR 273.1(b)(7), only the profit portion of board payments counts as income. "
                f"Of the ${board_total:,.2f} received, ${board_cost:,.2f} covers actual costs, leaving "
                f"${board_profit:,.2f} as countable income. Total countable gross: ${countable_income:,.2f}."
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. {reason}",
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.1(b)(7)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    )
                ],
            ),
            variation_tags=["boarder_income_proration"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "boarder_income_proration",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_migrant_case(self, rng: random.Random) -> TestCase:
        """Build a migrant/seasonal worker income averaging case (7 CFR 273.10(c)(3))."""
        t = self.source.thresholds()
        fy_config = self.source.fy_config
        hh_size = rng.randint(2, 4)
        limits = t.by_household_size(hh_size)

        work_months = rng.randint(4, 8)
        seasonal_total = round(
            rng.uniform(limits.gross_monthly * work_months * 0.70, limits.gross_monthly * work_months * 1.20),
            2,
        )
        averaged_monthly = round(seasonal_total / work_months, 2)

        net_income = self.source.calculate_net_income(
            gross_income=averaged_monthly,
            household_size=hh_size,
            earned_income=averaged_monthly,
        )

        is_eligible, reason = self.source.is_eligible(
            household_size=hh_size,
            gross_income=averaged_monthly,
            net_income=net_income,
            liquid_assets=round(rng.uniform(0, 800), -2),
        )

        uid = build_short_uid(rng)
        outcome = "eligible" if is_eligible else "ineligible"
        case_id = f"snap.{self.state.lower()}.eligibility.migrant_income_averaging.{outcome}.hh{hh_size}.{uid}"

        steps = [
            ReasoningStep(
                step_number=1,
                title="Determine income averaging period (7 CFR 273.10(c)(3))",
                rule_applied="7 CFR 273.10(c)(3)",
                inputs={"seasonal_total": seasonal_total, "work_months": work_months},
                computation=(
                    f"Applicant is a migrant/seasonal worker. Total seasonal earnings: ${seasonal_total:,.2f} "
                    f"over {work_months} months. Per 7 CFR 273.10(c)(3), income is averaged over the "
                    f"work period: ${seasonal_total:,.2f} ÷ {work_months} months = ${averaged_monthly:,.2f}/month."
                ),
                result=f"Averaged monthly income: ${averaged_monthly:,.2f}",
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=2,
                title="Apply averaged income to gross income test",
                rule_applied="7 CFR 273.9(a)(1)",
                inputs={"averaged_monthly": averaged_monthly, "gross_limit": limits.gross_monthly},
                computation=(
                    f"${averaged_monthly:,.2f} "
                    f"{'<=' if averaged_monthly <= limits.gross_monthly else '>'} "
                    f"${limits.gross_monthly:,.2f} (130% FPL, {hh_size}-person HH)"
                ),
                result="PASS" if averaged_monthly <= limits.gross_monthly else "FAIL",
                is_determinative=averaged_monthly > limits.gross_monthly,
            ),
            ReasoningStep(
                step_number=3,
                title="Net income test",
                rule_applied="7 CFR 273.9(a)(2)",
                inputs={"net_income": round(net_income, 2), "net_limit": limits.net_monthly},
                computation=(
                    f"${net_income:,.2f} {'<=' if net_income <= limits.net_monthly else '>'} ${limits.net_monthly:,.2f}"
                ),
                result="PASS" if net_income <= limits.net_monthly else "FAIL",
                is_determinative=net_income > limits.net_monthly,
            ),
        ]

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A {hh_size}-person household in {self.state} with a migrant agricultural worker. "
                    f"The worker earns ${seasonal_total:,.0f} over a {work_months}-month seasonal work period, "
                    f"averaging ${averaged_monthly:,.0f}/month."
                ),
                household_size=hh_size,
                monthly_gross_income=averaged_monthly,
                monthly_net_income=round(net_income, 2),
                liquid_assets=round(rng.uniform(0, 800), -2),
                state=self.state,
                additional_context={
                    "is_migrant_worker": True,
                    "seasonal_total": seasonal_total,
                    "work_months": work_months,
                    "threshold_type": "migrant_income_averaging",
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This household is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. "
                f"Under 7 CFR 273.10(c)(3), seasonal/migrant income is averaged over the work period. "
                f"${seasonal_total:,.2f} ÷ {work_months} months = ${averaged_monthly:,.2f}/month averaged income."
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. {reason}",
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.10(c)(3)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    )
                ],
            ),
            variation_tags=["migrant_income_averaging"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "migrant_income_averaging",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_mixed_immigration_case(self, rng: random.Random) -> TestCase:
        """Build a mixed immigration status case (7 CFR 273.4(c)(3)).

        Ineligible members are excluded from household SIZE for limit lookup,
        but their income still counts in full.
        """
        t = self.source.thresholds()
        fy_config = self.source.fy_config
        total_members = rng.randint(3, 5)
        ineligible_count = 1
        eligible_count = total_members - ineligible_count  # HH size for limit lookup

        limits_reduced = t.by_household_size(eligible_count)

        # Income near the reduced-size limit to make the case interesting
        gross = round(rng.uniform(limits_reduced.gross_monthly * 0.80, limits_reduced.gross_monthly * 1.15), 2)

        net_income = self.source.calculate_net_income(
            gross_income=gross,
            household_size=eligible_count,  # Use reduced HH size for deductions
            earned_income=gross,
        )

        is_eligible, reason = self.source.is_eligible(
            household_size=eligible_count,  # Reduced size for limit lookup
            gross_income=gross,  # Full income
            net_income=net_income,
            liquid_assets=round(rng.uniform(0, 1500), -2),
        )

        uid = build_short_uid(rng)
        outcome = "eligible" if is_eligible else "ineligible"
        case_id = (
            f"snap.{self.state.lower()}.eligibility."
            f"mixed_immigration_status_hh_size_reduction.{outcome}.hh{total_members}.{uid}"
        )

        steps = [
            ReasoningStep(
                step_number=1,
                title="Identify household composition — mixed immigration status (7 CFR 273.4(c)(3))",
                rule_applied="7 CFR 273.4(c)(3)",
                inputs={
                    "total_members": total_members,
                    "ineligible_members": ineligible_count,
                    "eligible_members": eligible_count,
                },
                computation=(
                    f"Total household members: {total_members}. Ineligible (non-qualified "
                    f"alien) members: {ineligible_count}. Under 7 CFR 273.4(c)(3), ineligible "
                    f"members are excluded from household size for limit lookup. "
                    f"HH size for limit lookup: {total_members} − {ineligible_count} = {eligible_count}. "
                    f"NOTE: Their income still counts in full — this is NOT income proration "
                    f"(income proration applies only to sponsored noncitizens under 7 CFR 273.11(c)(3))."
                ),
                result=(
                    f"HH size for limit lookup: {eligible_count} (reduced from {total_members}). "
                    f"Income counted: full ${gross:,.2f}."
                ),
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=2,
                title="Apply gross income test using reduced household size",
                rule_applied="7 CFR 273.9(a)(1)",
                inputs={
                    "gross_income": gross,
                    "gross_limit": limits_reduced.gross_monthly,
                    "hh_size_for_test": eligible_count,
                },
                computation=(
                    f"Using {eligible_count}-person household limits (after excluding "
                    f"ineligible member): ${gross:,.2f} "
                    f"{'<=' if gross <= limits_reduced.gross_monthly else '>'} "
                    f"${limits_reduced.gross_monthly:,.2f} (130% FPL)"
                ),
                result="PASS" if gross <= limits_reduced.gross_monthly else "FAIL",
                is_determinative=gross > limits_reduced.gross_monthly,
            ),
            ReasoningStep(
                step_number=3,
                title="Net income test",
                rule_applied="7 CFR 273.9(a)(2)",
                inputs={
                    "net_income": round(net_income, 2),
                    "net_limit": limits_reduced.net_monthly,
                },
                computation=(
                    f"${net_income:,.2f} "
                    f"{'<=' if net_income <= limits_reduced.net_monthly else '>'} "
                    f"${limits_reduced.net_monthly:,.2f} (100% FPL, {eligible_count}-person HH)"
                ),
                result="PASS" if net_income <= limits_reduced.net_monthly else "FAIL",
                is_determinative=net_income > limits_reduced.net_monthly,
            ),
        ]

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A {total_members}-person household in {self.state} with mixed immigration status. "
                    f"{ineligible_count} household member is a non-qualified alien (ineligible for SNAP). "
                    f"Total household gross income is ${gross:,.0f}/month (all members combined)."
                ),
                household_size=total_members,
                monthly_gross_income=gross,
                monthly_net_income=round(net_income, 2),
                liquid_assets=round(rng.uniform(0, 1500), -2),
                state=self.state,
                additional_context={
                    "has_ineligible_members": True,
                    "ineligible_member_count": ineligible_count,
                    "eligible_member_count": eligible_count,
                    "threshold_type": "mixed_immigration_status_hh_size_reduction",
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This household is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. "
                f"Under 7 CFR 273.4(c)(3), the {ineligible_count} ineligible member is excluded from "
                f"household size for limit lookup ({total_members}→{eligible_count} persons), but their income "
                f"counts in full. The household's full income of ${gross:,.2f} is tested against "
                f"{eligible_count}-person limits."
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=(
                    f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. {reason} "
                    f"(using {eligible_count}-person limits per 7 CFR 273.4(c)(3))"
                ),
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.4(c)(3)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    )
                ],
            ),
            variation_tags=["mixed_immigration_status_hh_size_reduction"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "mixed_immigration_status_hh_size_reduction",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_categorical_eligibility_case(self, rng: random.Random) -> TestCase:
        """Build a categorical eligibility (TANF/SSI) case (7 CFR 273.2(j)(2), 273.11(c)).

        Income is set ABOVE the normal gross limit to demonstrate that the income test is skipped.
        """
        t = self.source.thresholds()
        fy_config = self.source.fy_config
        hh_size = rng.randint(1, 4)
        limits = t.by_household_size(hh_size)

        # Income ABOVE the gross limit — would be ineligible without categorical eligibility
        gross = round(limits.gross_monthly * rng.uniform(1.10, 1.40), 2)
        unearned = round(rng.uniform(200, 600), -1)  # SSI/TANF benefit

        uid = build_short_uid(rng)
        case_id = f"snap.{self.state.lower()}.eligibility.categorical_eligibility_tanf_ssi.eligible.hh{hh_size}.{uid}"

        steps = [
            ReasoningStep(
                step_number=1,
                title="Check categorical eligibility (7 CFR 273.2(j)(2))",
                rule_applied="7 CFR 273.2(j)(2)",
                inputs={"tanf_or_ssi_recipient": True, "unearned_income": unearned},
                computation=(
                    f"Household receives TANF/SSI benefits (${unearned:,.0f}/month). "
                    f"Under 7 CFR 273.2(j)(2), households receiving TANF cash assistance are categorically "
                    f"eligible for SNAP. SSI recipients are categorically eligible under 7 CFR 273.11(c). "
                    f"Categorical eligibility means the income test is SKIPPED ENTIRELY."
                ),
                result="CATEGORICALLY ELIGIBLE — income test skipped",
                is_determinative=True,
            ),
            ReasoningStep(
                step_number=2,
                title="Income test — skipped due to categorical eligibility",
                rule_applied="7 CFR 273.2(j)(2)",
                inputs={
                    "gross_income": gross,
                    "gross_limit": limits.gross_monthly,
                    "skipped": True,
                },
                computation=(
                    f"NOTE: Gross income ${gross:,.2f} exceeds the ${limits.gross_monthly:,.2f} limit "
                    f"(130% FPL for {hh_size}-person HH). However, the income test is not applied because "
                    f"the household is categorically eligible. This is a common model error — running the "
                    f"income test after categorical eligibility is established incorrectly returns INELIGIBLE."
                ),
                result="SKIPPED — categorical eligibility overrides income test",
                is_determinative=False,
            ),
        ]

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A {hh_size}-person household in {self.state} with ${gross:,.0f}/month gross income "
                    f"(above the normal limit) and ${unearned:,.0f}/month in TANF/SSI benefits."
                ),
                household_size=hh_size,
                monthly_gross_income=gross,
                liquid_assets=round(rng.uniform(0, 2000), -2),
                state=self.state,
                has_elderly_or_disabled=True,
                additional_context={
                    "tanf_or_ssi_recipient": True,
                    "unearned_income": unearned,
                    "threshold_type": "categorical_eligibility_tanf_ssi",
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome="eligible",
            expected_answer=(
                f"This household is ELIGIBLE for SNAP under categorical eligibility. "
                f"Although gross income of ${gross:,.2f} exceeds the ${limits.gross_monthly:,.2f} limit, "
                f"the household receives TANF/SSI benefits. Under 7 CFR 273.2(j)(2) and 7 CFR 273.11(c), "
                f"these recipients are categorically eligible — the income test is skipped entirely."
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=(
                    "ELIGIBLE. Household is categorically eligible via TANF/SSI "
                    "(7 CFR 273.2(j)(2), 273.11(c)) — income test skipped."
                ),
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.2(j)(2)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    ),
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.11(c)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    ),
                ],
            ),
            variation_tags=["categorical_eligibility_tanf_ssi"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "categorical_eligibility_tanf_ssi",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _bbce_source_for_case(self) -> SNAPBBCESource:
        """Return a BBCE source with a raised gross limit for case construction.

        Uses the generator's own state when it is BBCE with a limit above 130% FPL;
        otherwise falls back to a representative 200%-FPL BBCE state (CA) so the
        expanded-income case is always meaningful.
        """
        if self.bbce_source.is_bbce and self.bbce_source.bbce_params.gross_income_limit_pct_fpl > 130:
            return self.bbce_source
        return SNAPBBCESource(fiscal_year=self.fiscal_year, state="CA")

    def _build_bbce_expanded_income_case(self, rng: random.Random) -> TestCase:
        """Build a BBCE expanded-gross-limit case (7 CFR 273.2(j)(2)(ii)).

        The headline reasoning test for BBCE: a household whose gross income falls
        BETWEEN the federal 130% FPL limit and the state's higher BBCE limit. Such a
        household is INELIGIBLE under federal rules but ELIGIBLE under BBCE. An
        adversarial variant places gross income ABOVE the state BBCE limit (ineligible),
        and the net income test still binds throughout.
        """
        src = self._bbce_source_for_case()
        state = src.state
        p = src.bbce_params
        fy_config = src.fy_config
        hh_size = rng.randint(1, 4)

        federal_limit = src.federal_gross_limit(hh_size)
        bbce_limit = src.effective_gross_limit(hh_size)
        net_limit = src.thresholds().by_household_size(hh_size).net_monthly
        pct = p.gross_income_limit_pct_fpl

        # ~70% eligible in-band cases, ~30% adversarial above-limit cases.
        adversarial = rng.random() >= 0.70

        if adversarial:
            # Gross above the state BBCE limit — ineligible on the gross test.
            gross = round(bbce_limit * rng.uniform(1.03, 1.15), 2)
            dependent_care = 0.0
            net_income = src.calculate_net_income(gross_income=gross, household_size=hh_size, earned_income=gross)
        else:
            # Gross strictly between the federal 130% limit and the state BBCE limit.
            gross = round(rng.uniform(federal_limit + 1.0, bbce_limit - 1.0), 2)
            # Households above 130% FPL gross typically only qualify because high
            # dependent-care/shelter costs pull net income under the 100% FPL limit.
            net_before = src.calculate_net_income(gross_income=gross, household_size=hh_size, earned_income=gross)
            target_net = round(net_limit * rng.uniform(0.85, 0.95), 2)
            dependent_care = round(max(0.0, net_before - target_net), 2)
            net_income = src.calculate_net_income(
                gross_income=gross,
                household_size=hh_size,
                earned_income=gross,
                dependent_care=dependent_care,
            )

        # Assets: waived states ignore; capped states stay within cap.
        if p.asset_limit is None:
            liquid_assets = round(rng.uniform(0, 8000), -2)
        else:
            liquid_assets = round(rng.uniform(0, p.asset_limit * 0.8), -2)

        is_eligible, reason = src.is_eligible(
            household_size=hh_size,
            gross_income=gross,
            net_income=net_income,
            liquid_assets=liquid_assets,
            has_elderly_or_disabled=False,
        )
        outcome = "eligible" if is_eligible else "ineligible"

        uid = build_short_uid(rng)
        case_id = f"snap.{state.lower()}.eligibility.bbce_expanded_gross_limit.{outcome}.hh{hh_size}.{uid}"

        asset_rule_text = (
            "the asset test is waived" if p.asset_limit is None else f"a ${p.asset_limit:,.0f} BBCE asset cap applies"
        )

        steps = [
            ReasoningStep(
                step_number=1,
                title="Establish broad-based categorical eligibility (7 CFR 273.2(j)(2)(ii))",
                rule_applied="7 CFR 273.2(j)(2)(ii)",
                inputs={
                    "state": state,
                    "bbce_gross_limit_pct_fpl": pct,
                    "conferring_benefit": p.conferring_benefit,
                },
                computation=(
                    f"{state} has adopted broad-based categorical eligibility (BBCE). The household "
                    f"receives a non-cash TANF/MOE-funded benefit or service, conferring categorical "
                    f"eligibility. Under BBCE, {state} raises the gross income limit to {pct}% FPL "
                    f"(vs. the federal 130%), and {asset_rule_text}."
                ),
                result=f"BBCE applies — gross income limit raised to {pct}% FPL",
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=2,
                title="Apply the raised BBCE gross income limit",
                rule_applied="7 CFR 273.2(j)(2)(ii)",
                inputs={
                    "gross_income": gross,
                    "federal_130pct_limit": federal_limit,
                    "bbce_limit": bbce_limit,
                    "household_size": hh_size,
                },
                computation=(
                    f"Federal 130% FPL limit: ${federal_limit:,.2f} — gross income ${gross:,.2f} "
                    f"{'EXCEEDS' if gross > federal_limit else 'is within'} this, so the household "
                    f"would be {'INELIGIBLE under federal rules' if gross > federal_limit else 'federally eligible'}. "
                    f"BBCE {pct}% FPL limit: ${bbce_limit:,.2f} — gross income ${gross:,.2f} "
                    f"{'<=' if gross <= bbce_limit else '>'} ${bbce_limit:,.2f}."
                ),
                result="PASS" if gross <= bbce_limit else "FAIL — exceeds BBCE gross limit",
                is_determinative=gross > bbce_limit,
                note=(
                    "Common model error: applying the federal 130% limit in a BBCE state. The state's "
                    "raised limit governs."
                ),
            ),
            ReasoningStep(
                step_number=3,
                title="Apply the net income test (still binds under BBCE)",
                rule_applied="7 CFR 273.9(a)(2)",
                inputs={
                    "net_income": round(net_income, 2),
                    "net_limit": net_limit,
                    "dependent_care_deduction": dependent_care,
                },
                computation=(
                    f"BBCE raises the GROSS limit but does NOT waive the net income test. "
                    f"After deductions (including ${dependent_care:,.2f} dependent care), net income "
                    f"${net_income:,.2f} {'<=' if net_income <= net_limit else '>'} ${net_limit:,.2f} "
                    f"(100% FPL, {hh_size}-person HH)."
                ),
                result="PASS" if net_income <= net_limit else "FAIL — exceeds net income limit",
                is_determinative=(gross <= bbce_limit and net_income > net_limit),
            ),
            ReasoningStep(
                step_number=4,
                title="Apply the BBCE asset rule",
                rule_applied="7 CFR 273.8",
                inputs={"liquid_assets": liquid_assets, "asset_limit": p.asset_limit},
                computation=(
                    f"{state} BBCE: {asset_rule_text}. "
                    + (
                        "Assets are not tested."
                        if p.asset_limit is None
                        else (
                            f"Assets ${liquid_assets:,.2f} "
                            f"{'<=' if liquid_assets <= p.asset_limit else '>'} "
                            f"${p.asset_limit:,.2f}."
                        )
                    )
                ),
                result="WAIVED"
                if p.asset_limit is None
                else ("PASS" if liquid_assets <= p.asset_limit else "FAIL — exceeds asset cap"),
                is_determinative=False,
            ),
        ]

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A {hh_size}-person household in {state} with ${gross:,.0f}/month gross income "
                    f"(above the federal 130% FPL limit of ${federal_limit:,.0f}). {state} has adopted "
                    f"broad-based categorical eligibility, raising the gross income limit to {pct}% FPL "
                    f"(${bbce_limit:,.0f}). "
                    + (
                        f"The household pays ${dependent_care:,.0f}/month in dependent care."
                        if dependent_care > 0
                        else "The household has no dependent-care or shelter deductions."
                    )
                ),
                household_size=hh_size,
                monthly_gross_income=gross,
                monthly_net_income=round(net_income, 2),
                liquid_assets=liquid_assets,
                state=state,
                additional_context={
                    "bbce_state": True,
                    "bbce_gross_limit_pct_fpl": pct,
                    "federal_130pct_limit": federal_limit,
                    "bbce_gross_limit": bbce_limit,
                    "dependent_care": dependent_care,
                    "threshold_type": "bbce_expanded_gross_limit",
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This household is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'} for SNAP. "
                f"{state} has adopted broad-based categorical eligibility (7 CFR 273.2(j)(2)(ii)), "
                f"raising the gross income limit from the federal 130% FPL (${federal_limit:,.2f}) to "
                f"{pct}% FPL (${bbce_limit:,.2f}). Gross income ${gross:,.2f} "
                + (
                    f"is within the BBCE limit, and net income ${net_income:,.2f} is within the "
                    f"${net_limit:,.2f} net limit (the net income test still applies under BBCE)."
                    if is_eligible
                    else f"exceeds the {pct}% FPL BBCE limit of ${bbce_limit:,.2f}."
                )
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. {reason}",
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.2(j)(2)(ii)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    ),
                    PolicyCitation(
                        document="USDA FNS SNAP Broad-Based Categorical Eligibility States Chart",
                        section="State BBCE options (August 2025)",
                        year=self.fiscal_year,
                        url="https://www.fns.usda.gov/snap/broad-based-categorical-eligibility",
                    ),
                ],
            ),
            variation_tags=["bbce_expanded_gross_limit", "bbce_state"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                "USDA FNS SNAP BBCE States Chart (August 2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "bbce_expanded_gross_limit",
                "state": state,
                "fiscal_year": self.fiscal_year,
                "bbce_gross_limit_pct_fpl": pct,
            },
        )

    def _sample_edge_profile(self, rng: random.Random, seed: int | None) -> USHouseholdProfile:
        """Sample a profile using edge-saturated strategy."""
        hh_size = rng.choices([1, 2, 3, 4, 5, 6], weights=[0.15, 0.25, 0.25, 0.20, 0.10, 0.05])[0]
        # Asset-limit thresholds are irrelevant when the asset test is waived (BBCE states
        # with no asset cap). BBCE states that keep a dollar cap still have a meaningful
        # asset boundary, so retain asset thresholds for them.
        asset_test_waived = self.bbce_source.is_bbce and self.bbce_source.bbce_params.asset_limit is None
        available_thresholds = (
            [t for t in _SNAP_THRESHOLD_TYPES if "asset" not in t] if asset_test_waived else _SNAP_THRESHOLD_TYPES
        )
        threshold = rng.choice(available_thresholds)
        offset = rng.choice(_OFFSETS)

        return USHouseholdProfile.at_threshold(
            program="snap",
            threshold=threshold,
            state=self.state,
            household_size=hh_size,
            fiscal_year=self.fiscal_year,
            offset_pct=offset,
            seed=seed,
        )

    def _build_case(self, profile: USHouseholdProfile, seed: int | None, index: int) -> TestCase:
        """Build a complete TestCase from a profile."""
        t = self.bbce_source.thresholds()
        fy_config = self.bbce_source.fy_config
        limits = t.by_household_size(min(profile.household_size, 8))

        # Compute net income if not already set
        net_income = profile.monthly_net_income
        if net_income is None:
            net_income = self.bbce_source.calculate_net_income(
                gross_income=profile.monthly_gross_income,
                household_size=profile.household_size,
                earned_income=profile.earned_income,
                shelter_costs=profile.shelter_costs,
                has_elderly_or_disabled=profile.has_elderly_or_disabled,
            )
            profile.monthly_net_income = round(net_income, 2)

        # Determine eligibility
        is_eligible, reason = self.bbce_source.is_eligible(
            household_size=profile.household_size,
            gross_income=profile.monthly_gross_income,
            net_income=net_income,
            liquid_assets=profile.liquid_assets,
            has_elderly_or_disabled=profile.has_elderly_or_disabled,
        )

        # Build rationale trace
        trace = self._build_rationale_trace(profile, net_income, limits, is_eligible, fy_config)

        # Determine difficulty
        difficulty = self._classify_difficulty(profile, is_eligible)

        # Generate unique ID
        case_id = self._make_case_id(profile, is_eligible, index, seed)

        # Build scenario summary
        scenario_summary = profile.natural_language_summary("snap")

        # Build expected answer
        expected_answer = self._build_expected_answer(profile, net_income, limits, is_eligible, reason, fy_config)

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=difficulty,
            scenario=ScenarioBlock(
                summary=scenario_summary,
                **{k: v for k, v in profile.to_scenario_fields().items()},
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome="eligible" if is_eligible else "ineligible",
            expected_answer=expected_answer,
            rationale_trace=trace,
            variation_tags=self._build_variation_tags(profile),
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=seed,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": profile.extra.get("threshold_type", "random"),
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_rationale_trace(
        self,
        profile: USHouseholdProfile,
        net_income: float,
        limits: HouseholdThreshold,
        is_eligible: bool,
        fy_config: FiscalYearConfig,
    ) -> RationaleTrace:
        """Construct the step-by-step reasoning chain for SNAP eligibility."""
        steps: list[ReasoningStep] = []
        std_ded = get_standard_deduction(profile.household_size)
        t = self.bbce_source.thresholds()
        bbce = self.bbce_source.is_bbce
        gross_pct = self.bbce_source.bbce_params.gross_income_limit_pct_fpl
        gross_limit = self.bbce_source.effective_gross_limit(profile.household_size)
        gross_basis = f"{gross_pct}% FPL BBCE limit, {self.state}" if bbce else "130% FPL"

        step_n = 1

        # Step 1: Gross income test (skip for elderly/disabled)
        if not profile.has_elderly_or_disabled:
            gross_pass = profile.monthly_gross_income <= gross_limit
            steps.append(
                ReasoningStep(
                    step_number=step_n,
                    title="Check gross income limit",
                    rule_applied="7 CFR 273.9(a)(1)",
                    inputs={
                        "household_size": profile.household_size,
                        "gross_income": profile.monthly_gross_income,
                        "gross_limit": gross_limit,
                        "pct_fpl": f"{gross_pct}%",
                        "period": fy_config.period_label,
                    },
                    computation=(
                        f"${profile.monthly_gross_income:,.2f} "
                        f"{'<=' if gross_pass else '>'} "
                        f"${gross_limit:,.2f} "
                        f"({gross_basis} for {profile.household_size}-person HH, {fy_config.period_label})"
                    ),
                    result="PASS" if gross_pass else "FAIL — exceeds gross income limit",
                    is_determinative=not gross_pass,
                    note=(
                        f"{self.state} has adopted broad-based categorical eligibility, raising the "
                        f"gross income limit to {gross_pct}% FPL (vs. the federal 130%)."
                    )
                    if bbce and gross_pct > 130
                    else None,
                )
            )
            step_n += 1
            if not gross_pass:
                steps.append(
                    ReasoningStep(
                        step_number=step_n,
                        title="Eligibility determination",
                        rule_applied="7 CFR 273.9(a)(1)",
                        inputs={},
                        computation=(
                            f"Gross income test failed — net income and asset tests are not reached. "
                            f"${profile.monthly_gross_income:,.2f} > ${gross_limit:,.2f} ({gross_basis})."
                        ),
                        result="INELIGIBLE",
                        is_determinative=True,
                    )
                )
                return RationaleTrace(
                    steps=steps,
                    conclusion=f"INELIGIBLE. Gross income ${profile.monthly_gross_income:,.2f} exceeds "
                    f"the ${gross_limit:,.2f} limit ({gross_basis}, {fy_config.period_label}).",
                    policy_basis=[
                        PolicyCitation(
                            document="7 CFR Part 273",
                            section="7 CFR 273.9(a)(1)",
                            year=self.fiscal_year,
                            url="https://www.ecfr.gov/current/title-7/part-273",
                        )
                    ],
                )
        else:
            steps.append(
                ReasoningStep(
                    step_number=step_n,
                    title="Gross income test — waived for elderly/disabled household",
                    rule_applied="7 CFR 273.9(a)(1)",
                    inputs={"has_elderly_or_disabled": True},
                    computation="Household contains elderly (60+) or disabled member — gross income test is waived.",
                    result="WAIVED",
                    is_determinative=False,
                )
            )
            step_n += 1

        # Step 2: Earned income deduction
        earned = profile.earned_income or profile.monthly_gross_income
        earned_ded = earned * (t.earned_income_deduction_pct or 20) / 100
        after_earned = profile.monthly_gross_income - earned_ded
        steps.append(
            ReasoningStep(
                step_number=step_n,
                title="Apply earned income deduction (20%)",
                rule_applied="7 CFR 273.9(c)(1)",
                inputs={"earned_income": earned, "deduction_rate": "20%"},
                computation=f"${earned:,.2f} × 20% = ${earned_ded:,.2f} deduction → ${after_earned:,.2f}",
                result=f"Income after earned deduction: ${after_earned:,.2f}",
                is_determinative=False,
            )
        )
        step_n += 1

        # Step 3: Standard deduction
        after_standard = after_earned - std_ded
        steps.append(
            ReasoningStep(
                step_number=step_n,
                title="Apply standard deduction",
                rule_applied="7 CFR 273.9(c)(2)",
                inputs={"household_size": profile.household_size, "standard_deduction": std_ded},
                computation=f"${after_earned:,.2f} − ${std_ded:,.0f} = ${after_standard:,.2f}",
                result=f"Income after standard deduction: ${after_standard:,.2f}",
                is_determinative=False,
            )
        )
        step_n += 1

        # Step 4: Net income test
        net_pass = net_income <= limits.net_monthly
        steps.append(
            ReasoningStep(
                step_number=step_n,
                title="Check net income limit",
                rule_applied="7 CFR 273.9(a)(2)",
                inputs={
                    "net_income": round(net_income, 2),
                    "net_limit": limits.net_monthly,
                    "pct_fpl": "100%",
                },
                computation=(
                    f"Net income ${net_income:,.2f} "
                    f"{'<=' if net_pass else '>'} "
                    f"${limits.net_monthly:,.2f} "
                    f"(100% FPL for {profile.household_size}-person HH)"
                ),
                result="PASS" if net_pass else "FAIL — exceeds net income limit",
                is_determinative=not net_pass,
            )
        )
        step_n += 1
        if not net_pass:
            return RationaleTrace(
                steps=steps,
                conclusion=f"INELIGIBLE. Net income ${net_income:,.2f} exceeds "
                f"the ${limits.net_monthly:,.2f} limit (100% FPL).",
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.9(a)(2)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    )
                ],
            )

        # Step 5: Asset test — waived (BBCE), a BBCE cap, or the federal limit.
        asset_limit_val = (
            t.asset_limit_elderly_disabled if (profile.has_elderly_or_disabled and not bbce) else t.asset_limit_general
        )
        if asset_limit_val is None:
            steps.append(
                ReasoningStep(
                    step_number=step_n,
                    title="Asset test — waived (broad-based categorical eligibility state)",
                    rule_applied="7 CFR 273.8(a)",
                    inputs={"state": self.state, "bbce": True},
                    computation=f"{self.state} has adopted broad-based categorical eligibility — asset test is waived.",
                    result="WAIVED",
                    is_determinative=False,
                    note="BBCE states may remove or relax the asset test for most or all households.",
                )
            )
        else:
            asset_pass = profile.liquid_assets <= asset_limit_val
            cap_kind = "BBCE asset cap" if bbce else "asset limit"
            steps.append(
                ReasoningStep(
                    step_number=step_n,
                    title=f"Check {cap_kind}",
                    rule_applied="7 CFR 273.8(b)(1)" if not profile.has_elderly_or_disabled else "7 CFR 273.8(b)(2)",
                    inputs={
                        "liquid_assets": profile.liquid_assets,
                        "asset_limit": asset_limit_val,
                        "elderly_disabled": profile.has_elderly_or_disabled,
                        "bbce_cap": bbce,
                    },
                    computation=(
                        f"${profile.liquid_assets:,.2f} "
                        f"{'<=' if asset_pass else '>'} "
                        f"${asset_limit_val:,.2f}" + (f" ({self.state} BBCE asset cap)" if bbce else "")
                    ),
                    result="PASS" if asset_pass else f"FAIL — exceeds {cap_kind}",
                    is_determinative=not asset_pass,
                )
            )
            if not asset_pass:
                return RationaleTrace(
                    steps=steps,
                    conclusion=f"INELIGIBLE. Assets ${profile.liquid_assets:,.2f} exceed "
                    f"the ${asset_limit_val:,.2f} {cap_kind}.",
                    policy_basis=[
                        PolicyCitation(
                            document="7 CFR Part 273",
                            section="7 CFR 273.8(b)",
                            year=self.fiscal_year,
                        )
                    ],
                )

        # All tests passed
        benefit = limits.max_benefit or 0.0
        gross_note = (
            "gross income (waived for elderly/disabled), " if profile.has_elderly_or_disabled else "gross income, "
        )
        if asset_limit_val is None:
            asset_note = "assets (BBCE — waived)."
        else:
            cap_kind = "BBCE cap" if bbce else "limit"
            asset_note = f"assets (${profile.liquid_assets:,.2f} ≤ ${asset_limit_val:,.2f} {cap_kind})."
        return RationaleTrace(
            steps=steps,
            conclusion=(
                f"ELIGIBLE. All tests passed: {gross_note}"
                f"net income (${net_income:,.2f} ≤ ${limits.net_monthly:,.2f}), "
                f"{asset_note} "
                f"Estimated monthly benefit: ~${benefit:,.0f}."
            ),
            policy_basis=[
                PolicyCitation(
                    document="7 CFR Part 273",
                    section="7 CFR 273.9",
                    year=self.fiscal_year,
                    url="https://www.ecfr.gov/current/title-7/part-273",
                ),
                PolicyCitation(
                    document=f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
                    section="Income and Allotment Table",
                    year=self.fiscal_year,
                    url="https://www.fns.usda.gov/snap/recipient/eligibility",
                ),
            ],
        )

    def _build_expected_answer(
        self,
        profile: USHouseholdProfile,
        net_income: float,
        limits: HouseholdThreshold,
        is_eligible: bool,
        reason: str,
        fy_config: FiscalYearConfig,
    ) -> str:
        t = self.bbce_source.thresholds()
        std_ded = get_standard_deduction(profile.household_size)
        earned = profile.earned_income or profile.monthly_gross_income
        earned_ded = earned * 0.20
        gross_pct = self.bbce_source.bbce_params.gross_income_limit_pct_fpl
        gross_limit = self.bbce_source.effective_gross_limit(profile.household_size)

        if is_eligible:
            benefit = limits.max_benefit or 0.0
            asset_str = "N/A (BBCE — waived)" if t.asset_limit_general is None else f"${t.asset_limit_general:,.0f}"
            gross_result = (
                "(waived — elderly/disabled household)"
                if profile.has_elderly_or_disabled
                else f"≤ ${gross_limit:,.2f} ({gross_pct}% FPL) — PASS"
            )
            return (
                f"This household is ELIGIBLE for SNAP benefits ({fy_config.period_label}).\n\n"
                f"Gross income test: ${profile.monthly_gross_income:,.2f} {gross_result}.\n"
                f"Net income: ${profile.monthly_gross_income:,.2f} − ${earned_ded:,.2f} (20% earned deduction) − "
                f"${std_ded:,.0f} (standard deduction) = ${net_income:,.2f} ≤ ${limits.net_monthly:,.2f} — PASS.\n"
                f"Assets: ${profile.liquid_assets:,.2f} ≤ {asset_str} — PASS.\n\n"
                f"Estimated monthly benefit: approximately ${benefit:,.0f} "
                f"(maximum for {profile.household_size}-person household, subject to net income calculation)."
            )
        else:
            asset_result = "N/A (BBCE waived)" if t.asset_limit_general is None else f"${t.asset_limit_general:,.2f}"
            return (
                f"This household is INELIGIBLE for SNAP benefits ({fy_config.period_label}).\n\n"
                f"Reason: {reason}\n\n"
                f"Applicable limits for a {profile.household_size}-person household: "
                f"Gross ${gross_limit:,.2f}/month ({gross_pct}% FPL), "
                f"Net ${limits.net_monthly:,.2f}/month (100% FPL), "
                f"Assets {asset_result} (general)."
            )

    def _classify_difficulty(self, profile: USHouseholdProfile, is_eligible: bool) -> Difficulty:
        threshold_type = profile.extra.get("threshold_type", "")
        offset = profile.extra.get("offset_pct")
        if offset is None:
            # Sampled independently of any threshold: we do not know how far this
            # household sits from a limit, so we must not claim it is clear of one.
            return Difficulty.MEDIUM

        if abs(offset) <= 0.01 and threshold_type:
            return Difficulty.HARD
        # Elderly/disabled changes four computations (gross test waived, medical
        # deduction, uncapped excess shelter, different asset cap) no matter how far
        # the household sits from a limit. It is a per-household property, unlike
        # is_bbce, which is constant per generator and is already a variation tag.
        if profile.has_elderly_or_disabled:
            return Difficulty.MEDIUM
        if abs(offset) > 0.30:
            return Difficulty.EASY
        return Difficulty.MEDIUM

    def _make_case_id(self, profile: USHouseholdProfile, is_eligible: bool, index: int, seed: int | None) -> str:
        threshold = profile.extra.get("threshold_type", "general")
        offset = profile.extra.get("offset_pct", 0.0)
        offset_tag = self._offset_tag(offset)
        outcome = "eligible" if is_eligible else "ineligible"
        hh = f"hh{profile.household_size}"
        # Combine seed and index so cases within the same batch don't collide
        # even when derived from the same per-case seed. seed=None preserves
        # non-deterministic behavior (a fresh os-random seed per call).
        uid_seed = f"{seed}-{index}" if seed is not None else None
        uid = build_short_uid(random.Random(uid_seed))
        return f"snap.{self.state.lower()}.eligibility.{threshold}.{offset_tag}.{outcome}.{hh}.{uid}"

    @staticmethod
    def _offset_tag(offset: float) -> str:
        """Bucket an offset_pct for case IDs / variation tags.

        Buckets by both sign and magnitude: a 1% offset and a 35% offset both
        have offset > 0, but only the former is actually "at the boundary."
        Collapsing them to the same "above_limit"/"below_limit" tag would let
        a consumer filtering for boundary cases silently pull in far-from-limit
        ones too (see the widened _OFFSETS set, which now includes +/-0.35).
        """
        if offset == 0.0:
            return "at_limit"
        if offset > 0.30:
            return "well_above_limit"
        if offset < -0.30:
            return "well_below_limit"
        return "above_limit" if offset > 0 else "below_limit"

    def _build_variation_tags(self, profile: USHouseholdProfile) -> list[str]:
        tags: list[str] = []
        threshold = profile.extra.get("threshold_type", "")
        offset = profile.extra.get("offset_pct", None)

        if threshold:
            tags.append(threshold)
        if offset is not None:
            tags.append(self._offset_tag(offset))
        if profile.has_elderly_or_disabled:
            tags.append("elderly_or_disabled")
            tags.append("gross_income_test_waived")
        if self.bbce_source.is_bbce:
            tags.append("bbce_state")
            if self.bbce_source.bbce_params.asset_limit is None:
                tags.append("asset_test_waived")
            else:
                tags.append("asset_cap")
        if profile.household_size == 1:
            tags.append("single_person_household")
        elif profile.has_dependent_children:
            tags.append("family_with_children")

        return tags
