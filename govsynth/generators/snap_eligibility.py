"""SNAP eligibility test case generator.

Generates compatible TestCase objects for SNAP eligibility determination,
including full rationale traces grounded in 7 CFR Part 273.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import TypedDict

from govsynth.fiscal_year import DEFAULT_SNAP_FY, FiscalYearConfig
from govsynth.generators.base import Generator
from govsynth.models.enums import Difficulty, Program, TaskType
from govsynth.models.rationale import PolicyCitation, RationaleTrace, ReasoningStep
from govsynth.models.test_case import ScenarioBlock, TaskBlock, TestCase
from govsynth.profiles.us_household import PHRASING_STYLES, USHouseholdProfile
from govsynth.reasoning.rules_engine import build_short_uid
from govsynth.sources.base import HouseholdThreshold
from govsynth.sources.us.snap import get_standard_deduction
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


class _FiveYearBar(TypedDict):
    """The five-year-bar arm of a noncitizen case (8 U.S.C. 1613)."""

    applies: bool
    why: str


class _NoncitizenCaseBase(TypedDict):
    """One noncitizen status variant. See `_NONCITIZEN_STATUS_CASES`.

    Spelled out as a TypedDict rather than left as a bare dict literal because
    mypy --strict infers a heterogeneous literal as dict[str, object], which
    makes every `spec["category_rule"]` an `object` and fails at the point it is
    passed to `ReasoningStep(rule_applied=...)`.
    """

    key: str
    phrase: str
    category: str
    category_eligible: bool
    category_rule: str
    category_why: str
    # None for categories with no waiting period at all (Cuban/Haitian entrants,
    # COFA citizens) and for statuses that are not eligible in the first place.
    bar: _FiveYearBar | None


class _NoncitizenCase(_NoncitizenCaseBase, total=False):
    """`total=False` inheritance keeps `stale_reg_warning` genuinely optional.

    typing.NotRequired would be cleaner but is 3.11+, and this package targets
    3.10. Only the refugee and asylee cases set it -- they are the ones where
    7 CFR 273.4(a)(6)(ii) still contradicts the governing statute.
    """

    stale_reg_warning: bool


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

    This description covers _classify_difficulty only. Under 'edge_saturated',
    the special-population builders (homeless, student, boarder, migrant, mixed
    immigration status, categorical eligibility, expanded BBCE income --
    EDGE_CASES.md Group A) bypass _classify_difficulty entirely and stamp
    ADVERSARIAL directly, since those cases exist because models tend to
    misapply that specific rule, not because of any threshold distance. For a
    typical 'edge_saturated' run, roughly 20% of output is ADVERSARIAL.

    Eight of those nine builders apply to every jurisdiction. The ninth,
    _build_bbce_expanded_income_case, needs a gross-income band between the
    federal 130% FPL limit and a HIGHER state limit, which a jurisdiction that
    has not adopted BBCE (or adopted it at 130% FPL) does not have. Such a
    jurisdiction generates the other eight types instead -- see
    `supports_bbce_expanded_income`. It never borrows another jurisdiction's
    parameters to manufacture the case.
    """

    def __init__(
        self,
        fiscal_year: int = DEFAULT_SNAP_FY,
        state: str = "VA",
    ) -> None:
        self.fiscal_year = fiscal_year
        self.state = state.upper()
        # ONE source for every builder, deliberately. `SNAPBBCESource` extends
        # `SNAPSource`, so it serves the federal baseline too: for a jurisdiction
        # that has not adopted BBCE its `effective_gross_limit` IS the federal 130%
        # FPL limit and its asset cap IS the federal one, both resolved from the
        # same versioned data table.
        #
        # There is deliberately no second, federal-only source on this generator
        # any more. Six of the seven special-population builders used to decide
        # eligibility from a plain `SNAPSource` while every consumer rendered the
        # jurisdiction's *BBCE* parameters into the prompt, so in a raised-BBCE
        # state the prompt said the 3-person gross limit was $4,442 and the ground
        # truth said "INELIGIBLE: $2,901.30 > $2,888.00 (130% FPL)". A model that
        # read the stated parameters and applied them correctly was scored WRONG,
        # and a model fine-tuned on those records was scored right -- a scoring
        # channel favouring the fine-tune, on exactly the case shapes an open-book
        # eval exists to test. Measured on a 6,360-case FY2026 run: 74 cases whose
        # outcome flipped outright and 248 whose stated gross-income step was
        # wrong. `tests/unit/test_snap_ground_truth_agreement.py` now asserts the
        # invariant that would have caught it: every generated case's
        # `expected_outcome` must agree with `SNAPBBCESource.is_eligible`.
        self.bbce_source = SNAPBBCESource(fiscal_year=fiscal_year, state=state)

    @property
    def program(self) -> str:
        return "snap"

    def generate(
        self,
        n: int,
        profile_strategy: str = "edge_saturated",
        seed: int | None = None,
        special_fraction: float = 0.20,
        phrasing_style: str | None = None,
    ) -> list[TestCase]:
        """Generate n SNAP eligibility test cases.

        phrasing_style: None (default, unchanged behaviour) renders every
            threshold-boundary case's scenario through
            USHouseholdProfile.natural_language_summary -- the one fixed
            sentence template every existing caller of `generate()` already
            depends on (the SFT corpus, the committed GRPO v1 corpus, the
            Cornell eval harness). Passing "random" instead renders each
            threshold-boundary case in one of PHRASING_STYLES
            (govsynth.profiles.us_household), chosen deterministically from
            that case's own seed and index -- never from `rng`, so turning
            this on cannot change which facts get sampled, only how they are
            phrased. Passing one specific style name in PHRASING_STYLES
            renders every threshold-boundary case in that single style.
            Special-population cases (student, boarder, self-employment,
            noncitizen status, etc.) write their own bespoke prose and are
            unaffected either way -- they never call
            natural_language_summary at all.

        When profile_strategy is 'edge_saturated', 20% of cases (minimum 1 per special
        type if n >= 6) are special-population edge cases. The remainder use threshold-boundary
        profiles.

        Args:
            n: Number of cases to generate.
            profile_strategy: 'edge_saturated' | 'uniform' | 'realistic'
            seed: RNG seed for reproducibility.
            special_fraction: share of cases drawn from the special-population
                builders under 'edge_saturated'. Defaults to 0.20, which left
                the ten threshold-boundary income variants holding 74% of a
                13,760-record training set and only 12 distinct citation sets
                across the whole corpus -- 80% of rendered targets opened with
                the byte-identical line "Step 1: Check gross income limit". A
                fine-tune on that corpus learned the dominant template so
                strongly that held-out cases outside it degenerated into
                repetition loops. Raise it to broaden the reasoning-path and
                citation mix; the floor below still guarantees >= 1 case per
                available builder regardless of this value.

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
                    case = self._build_case(profile, case_seed, i, phrasing_style=phrasing_style)
                    cases.append(case)
                except Exception as exc:
                    raise RuntimeError(
                        f"random-profile case builder failed while building case {i} of {n}: {exc}"
                    ) from exc
            return cases

        # edge_saturated: two-phase split
        if not 0.0 <= special_fraction <= 1.0:
            raise ValueError(f"special_fraction must be in [0.0, 1.0], got {special_fraction!r}")
        n_special = max(0, min(int(n * special_fraction), n))
        # Guarantee >= 1 per available type once n is at least that many. Keyed on
        # the AVAILABLE builder count, not a hardcoded 7: a jurisdiction that
        # cannot support the BBCE expanded-income case has six types, and a
        # hardcoded floor would over-request one type for it.
        n_special = max(n_special, min(len(self._available_special_population_builders()), n))
        n_edge = n - n_special

        special_cases = self._build_special_population_cases(n_special, rng)

        edge_cases: list[TestCase] = []
        for i in range(n_edge):
            case_seed = rng.randint(0, 2**31) if seed is not None else None
            profile = self._sample_edge_profile(rng, case_seed)
            try:
                case = self._build_case(profile, case_seed, i, phrasing_style=phrasing_style)
                edge_cases.append(case)
            except Exception as exc:
                raise RuntimeError(
                    f"edge-saturated case builder failed while building edge case {i} of {n_edge}: {exc}"
                ) from exc

        return special_cases + edge_cases

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _special_population_builders(
        self,
    ) -> list[tuple[str, Callable[[random.Random], TestCase]]]:
        """Name/callable pairs for the 9 special-population builders.

        Extracted so tests can inspect the pairing (name matches the callable's
        __name__) without invoking any builder.
        """
        return [
            ("_build_homeless_case", self._build_homeless_case),
            ("_build_student_case", self._build_student_case),
            ("_build_boarder_case", self._build_boarder_case),
            ("_build_self_employment_case", self._build_self_employment_case),
            ("_build_migrant_case", self._build_migrant_case),
            ("_build_mixed_immigration_case", self._build_mixed_immigration_case),
            ("_build_noncitizen_status_case", self._build_noncitizen_status_case),
            ("_build_categorical_eligibility_case", self._build_categorical_eligibility_case),
            ("_build_bbce_expanded_income_case", self._build_bbce_expanded_income_case),
        ]

    @property
    def supports_bbce_expanded_income(self) -> bool:
        """Whether this jurisdiction can support a BBCE expanded-gross-limit case.

        That case's entire premise is a household whose gross income falls in the
        band between the federal 130% FPL limit and a HIGHER state limit. A
        jurisdiction that has not adopted BBCE, or has adopted it at 130% FPL, has
        no such band and therefore no case to build.

        This used to be papered over: `_bbce_source_for_case` silently swapped in
        ``SNAPBBCESource(state="CA")`` for any such jurisdiction and then set
        ``state = src.state``, rewriting `case_id`, `jurisdiction`, and
        `scenario.state` to CA. Measured on a 53-jurisdiction FY2026 run, that
        moved 636 cases: 14 jurisdictions ended up with zero
        `bbce_expanded_gross_limit` cases and CA received 162 cases instead of
        120. For a holdout experiment that is worse than missing data -- CA was a
        *held-out* jurisdiction, so the transplant loaded the holdout tier with an
        adversarial case type its trained tier-mates barely had, and the
        tier-to-tier accuracy gap absorbed a case-mix difference that has nothing
        to do with jurisdiction novelty. Skipping the type is honest; borrowing
        another jurisdiction's parameters is not.
        """
        p = self.bbce_source.bbce_params
        return p.bbce and p.gross_income_limit_pct_fpl > 130

    def _available_special_population_builders(
        self,
    ) -> list[tuple[str, Callable[[random.Random], TestCase]]]:
        """`_special_population_builders`, minus any this jurisdiction cannot honour.

        Only `_build_bbce_expanded_income_case` is ever filtered out, and only for
        a jurisdiction with no gross-income band above the federal limit (see
        `supports_bbce_expanded_income`). `_special_population_builders` itself
        stays the full canonical list so a rename is still caught by
        `test_builder_names_match_their_callables`.
        """
        builders = self._special_population_builders()
        if self.supports_bbce_expanded_income:
            return builders
        return [(name, fn) for name, fn in builders if name != "_build_bbce_expanded_income_case"]

    def _gross_limit(self, household_size: int) -> float:
        """The monthly gross income limit that actually governs in this jurisdiction.

        Single site for "which gross limit is this case judged against," so no
        builder can drift back to the federal table while a consumer renders the
        state's raised BBCE limit into the prompt (see `__init__`). Equals the
        federal 130% FPL limit for a jurisdiction that has not adopted BBCE.
        """
        return self.bbce_source.effective_gross_limit(household_size)

    def _gross_basis(self, household_size: int) -> str:
        """The FPL basis label for `_gross_limit`, for rationale/answer prose.

        Paired with `_gross_limit` so a limit and the percentage it is described by
        can never disagree. Rationale text used to hardcode "130% FPL" in the
        homeless, migrant, mixed-immigration, and categorical builders, which
        mislabelled the basis on every case in a raised-BBCE jurisdiction even
        where the outcome itself did not change -- a wrong number in a training
        target is still a wrong number.
        """
        p = self.bbce_source.bbce_params
        if p.bbce:
            return f"{p.gross_income_limit_pct_fpl}% FPL BBCE limit, {self.state}, {household_size}-person HH"
        return f"130% FPL, {household_size}-person HH"

    def _binding_gross_ceiling(
        self,
        household_size: int,
        net_income_for: Callable[[float], float],
    ) -> float:
        """The gross income at which this case shape's determination actually flips.

        A builder that samples income against the GROSS limit is anchoring on a test
        that, in a raised-BBCE jurisdiction, does not bind. At 200% FPL the gross limit
        is roughly twice the 100% FPL net limit, so income drawn near it clears the
        gross test by a mile and fails the net test by a mile -- every draw lands on the
        same side and the case type's label collapses to near-constant. Measured over
        53 jurisdictions at 400 cases each, that is what the previous fix wave did:
        `migrant_income_averaging` went from 60.4% eligible to 76.8% INELIGIBLE and
        `mixed_immigration_status_hh_size_reduction` from 56.9% eligible to 82.3%
        INELIGIBLE, with 0 eligible cases in either type across all 28 200%-FPL
        jurisdictions. The labels were arithmetically correct; two adversarial case
        types had simply become learnable by name with no counterexamples.

        So the anchor is the flip point itself, found by bisecting `is_eligible` -- the
        same source that decides the case and that the prompt is rendered from, never a
        second reimplementation of the deduction waterfall that could drift from it.
        `net_income_for` maps a candidate gross income to the net income THIS case shape
        would report for it (which deductions apply is the builder's business, not this
        method's), and the returned ceiling is the largest gross that still passes every
        income test: the gross limit itself where the gross test binds first, and a
        lower, net-test-derived figure where it does not.

        Assumes net income is non-decreasing in gross income, which holds for every
        builder that calls this (earned income only, no shelter or dependent-care
        offset that shrinks as income grows) and makes the pass/fail predicate monotone.
        Raises if the bracket does not actually bracket, rather than returning a
        silently meaningless anchor.
        """
        limit = self._gross_limit(household_size)

        def passes(gross: float) -> bool:
            eligible, _reason = self.bbce_source.is_eligible(
                household_size=household_size,
                gross_income=gross,
                net_income=net_income_for(gross),
            )
            return eligible

        if passes(limit):
            # The gross test binds first (a non-BBCE jurisdiction, or a shape whose
            # deductions keep net income under the limit right up to the gross ceiling).
            return limit
        if not passes(0.0):
            raise RuntimeError(
                f"{self.state}: a {household_size}-person household is ineligible at $0 gross income, "
                "so no income makes this case eligible and there is no boundary to sample around. "
                "The deduction structure or threshold table for this jurisdiction is wrong."
            )

        lo, hi = 0.0, limit
        while hi - lo > 0.01:  # a cent: finer than any figure a case reports
            mid = (lo + hi) / 2.0
            if passes(mid):
                lo = mid
            else:
                hi = mid
        return lo

    def _build_special_population_cases(self, n: int, rng: random.Random) -> list[TestCase]:
        """Build n special-population edge cases, cycling through the available types.

        When n is below the number of available types, cycles through the first n.
        When n is at or above it, guarantees at least one case per available type.
        """
        builders = self._available_special_population_builders()
        cases: list[TestCase] = []
        for i in range(n):
            name, builder = builders[i % len(builders)]
            try:
                case = builder(rng)
                cases.append(case)
            except Exception as exc:
                raise RuntimeError(f"special-case builder {name!r} failed while building case {i}: {exc}") from exc
        return cases

    def _build_homeless_case(self, rng: random.Random) -> TestCase:
        """Build a homeless shelter deduction edge case (7 CFR 273.9(c)(6))."""
        t = self.bbce_source.thresholds()
        assert t.extra is not None, "SNAP thresholds always populate `extra`"
        fy_config = self.bbce_source.fy_config
        hh_size = rng.randint(1, 3)
        limits = t.by_household_size(hh_size)
        gross_limit = self._gross_limit(hh_size)

        # Income: randomly placed near the GOVERNING threshold so outcome varies.
        # Sampling against the federal limit in a raised-BBCE state would put every
        # case comfortably under the state limit and the outcome would never vary.
        gross = round(rng.uniform(gross_limit * 0.60, gross_limit * 1.10), 2)

        net_income = self.bbce_source.calculate_net_income(
            gross_income=gross,
            household_size=hh_size,
            earned_income=gross,
            shelter_costs=None,  # homeless: no actual shelter costs
            is_homeless=True,
        )

        is_eligible, reason = self.bbce_source.is_eligible(
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
                    "gross_limit": gross_limit,
                    "household_size": hh_size,
                },
                computation=(
                    f"${gross:,.2f} {'<=' if gross <= gross_limit else '>'} "
                    f"${gross_limit:,.2f} ({self._gross_basis(hh_size)})"
                ),
                result="PASS" if gross <= gross_limit else "FAIL",
                is_determinative=gross > gross_limit,
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
                    "monthly_allotment": self._estimate_benefit(hh_size, net_income) if is_eligible else None,
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
        fy_config = self.bbce_source.fy_config
        hh_size = 1
        gross_limit = self._gross_limit(hh_size)

        # Income well below the GOVERNING gross limit — student is still ineligible.
        # The point of the case is that the exclusion fires before the income test,
        # so the income must clear whichever limit actually applies here; sampling
        # against the federal limit in a raised-BBCE state would still clear it, but
        # the prose would then compare against a limit the prompt never states.
        gross = round(gross_limit * rng.uniform(0.40, 0.75), 2)

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
                    ],
                    # The limit this step's note quotes, stated structurally so the
                    # agreement gate can check it against the jurisdiction's own source.
                    # This case is exempt from the OUTCOME equality (a non-financial
                    # denial under 7 CFR 273.5(a)), which is exactly why the limit it
                    # quotes needs its own check: a limit drifting to the federal table
                    # here changes no outcome and shows up only in the prose.
                    "gross_income": gross,
                    "gross_limit": gross_limit,
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
                    f"the ${gross_limit:,.2f} limit ({self._gross_basis(hh_size)}), but income "
                    "level is irrelevant — the student exclusion fires before the income test."
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
                    f"gross income (below the {hh_size}-person gross limit of ${gross_limit:,.0f}). "
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
                f"${gross_limit:,.2f} gross income limit, the student exclusion under "
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
        t = self.bbce_source.thresholds()
        fy_config = self.bbce_source.fy_config
        hh_size = rng.randint(1, 3)
        limits = t.by_household_size(hh_size)
        gross_limit = self._gross_limit(hh_size)

        # Board payment received; only profit portion counts
        board_total = round(rng.uniform(600, 1200), -1)
        board_cost = round(board_total * rng.uniform(0.55, 0.80), -1)
        board_profit = round(board_total - board_cost, 2)

        # Other income (earned wages)
        other_income = round(rng.uniform(200, gross_limit * 0.60), 2)
        countable_income = other_income + board_profit  # Only profit counts

        # Drawn ONCE and reused below. This used to be two separate rng draws --
        # one passed to `is_eligible`, a different one written into the scenario
        # the prompt describes -- so the asset figure the determination was made
        # from was not the asset figure the reader is shown. Benign only while both
        # draws sit under the applicable cap; a BBCE asset cap can be low enough
        # for two draws from this range to straddle it.
        liquid_assets = round(rng.uniform(0, 1000), -2)

        net_income = self.bbce_source.calculate_net_income(
            gross_income=countable_income,
            household_size=hh_size,
            earned_income=other_income,  # Board profit is unearned
        )

        is_eligible, reason = self.bbce_source.is_eligible(
            household_size=hh_size,
            gross_income=countable_income,
            net_income=net_income,
            liquid_assets=liquid_assets,
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
                # `countable_income` and `gross_limit` are inputs, not just prose in
                # `result`: this step's `is_determinative` is that comparison, and the
                # ground-truth agreement gate reaches a stated limit only where the step
                # states it structurally (tests/unit/test_snap_ground_truth_agreement.py).
                inputs={
                    "other_income": other_income,
                    "board_profit": board_profit,
                    "countable_income": countable_income,
                    "gross_limit": gross_limit,
                },
                computation=(
                    f"${other_income:,.2f} (wages) + ${board_profit:,.2f} (board profit) = "
                    f"${countable_income:,.2f} total countable income"
                ),
                result=(
                    f"Total gross income: ${countable_income:,.2f} vs limit "
                    f"${gross_limit:,.2f} ({self._gross_basis(hh_size)})"
                ),
                is_determinative=countable_income > gross_limit,
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
                liquid_assets=liquid_assets,
                state=self.state,
                additional_context={
                    "is_boarder": True,
                    "board_payment_total": board_total,
                    "board_cost": board_cost,
                    "boarder_income": board_profit,
                    "threshold_type": "boarder_income_proration",
                    "monthly_allotment": self._estimate_benefit(hh_size, net_income) if is_eligible else None,
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

    # Ordinary service businesses whose costs are actual and itemisable under
    # 7 CFR 273.11(b)(1). Day care, boarders, foster-care boarders and farming are
    # deliberately absent: each has its own cost-determination paragraph
    # (273.11(b)(3)(i)-(iii), 273.11(a)(1)(iii) and (a)(2)(ii)) and every one of them
    # routes through a STATE-set figure -- the TANF standard amount, a CACFP
    # reimbursement rate, or a flat percentage that must be "stated in the State's
    # SNAP manual". Generating those needs a sourced 53-jurisdiction table, which is
    # the same reason the standard utility allowance is not generated here.
    _SELF_EMPLOYMENT_ENTERPRISES = (
        ("house-cleaning service", "cleaning supplies"),
        ("lawn care and landscaping business", "seed, fertilizer and mower parts"),
        ("mobile barber business", "clippers and sanitising supplies"),
        ("handyman and small-repair business", "lumber, fasteners and tool parts"),
        ("online resale business", "inventory purchased for resale"),
        ("food cart", "ingredients and disposable serviceware"),
    )

    def _build_self_employment_case(self, rng: random.Random) -> TestCase:
        """Build a self-employment net-income case (7 CFR 273.11(a), (b)).

        Two independent traps, both arithmetic and both checkable against the
        figures the scenario states:

        1. The household's countable gross income is the NET self-employment
           income -- gross receipts less the allowable cost of producing them,
           averaged over the period the income is intended to cover
           (7 CFR 273.11(a)(1)(i), (a)(2)(i)) -- and NOT the gross receipts.
           Treating receipts as gross income overstates income by the entire
           cost base, failing the gross test on a household that passes it.
        2. Four expenses the scenario lists are not allowable costs of doing
           business under 7 CFR 273.11(b)(2): a net loss from a previous period
           (b)(2)(i), income tax set aside and commuting (b)(2)(ii), and
           depreciation (b)(2)(iii). Deducting them understates income. The reg
           supplies its own reason for (b)(2)(ii) -- those costs "are accounted
           for by the 20 percent earned income deduction specified in
           273.9(d)(2)" -- so a model that both deducts them and takes the
           earned income deduction has subtracted the same money twice.

        Only the actual-cost method is generated. 7 CFR 273.11(b)(3) offers
        actual costs or a state-set standard and supplies no federal percentage
        to fall back on, so actual costs are the only method that is identical
        in all 53 jurisdictions.
        """
        fy_config = self.bbce_source.fy_config
        hh_size = rng.randint(1, 4)

        # Anchor the NET figure on the flip point rather than the gross limit: this
        # shape's countable income is entirely earned, so in a raised-BBCE
        # jurisdiction the gross test does not bind and sampling against it would
        # collapse the label (see `_binding_gross_ceiling`).
        ceiling = self._binding_gross_ceiling(
            hh_size,
            lambda gross: self.bbce_source.calculate_net_income(
                gross_income=gross,
                household_size=hh_size,
                earned_income=gross,
            ),
        )
        net_se_monthly = round(rng.uniform(ceiling * 0.80, ceiling * 1.15), 2)

        enterprise, stock_label = self._SELF_EMPLOYMENT_ENTERPRISES[
            rng.randrange(len(self._SELF_EMPLOYMENT_ENTERPRISES))
        ]

        # Allowable costs, each naming a clause of 273.11(b)(1). Monthly figures are
        # the source of truth; the scenario reports 12-month totals so the rationale
        # has to perform the averaging in (a)(1)(i) instead of being handed the answer.
        allowable = {
            f"{stock_label} (stock and raw material)": round(net_se_monthly * rng.uniform(0.12, 0.28), 2),
            "equipment principal payments": round(net_se_monthly * rng.uniform(0.05, 0.12), 2),
            "business liability insurance premiums": round(net_se_monthly * rng.uniform(0.03, 0.07), 2),
            "taxes on income-producing property": round(net_se_monthly * rng.uniform(0.02, 0.05), 2),
        }
        allowable_monthly = round(sum(allowable.values()), 2)
        receipts_monthly = round(net_se_monthly + allowable_monthly, 2)

        # Not allowable under (b)(2). Sized so that deducting them can actually move
        # the determination -- a trap that cannot change the answer is decorative.
        disallowed = {
            "depreciation on equipment": round(net_se_monthly * rng.uniform(0.06, 0.14), 2),
            "income tax set aside": round(net_se_monthly * rng.uniform(0.05, 0.10), 2),
            "commuting between home and job sites": round(net_se_monthly * rng.uniform(0.03, 0.08), 2),
            "net loss carried over from the prior year": round(net_se_monthly * rng.uniform(0.04, 0.09), 2),
        }
        disallowed_monthly = round(sum(disallowed.values()), 2)

        months = 12
        receipts_annual = round(receipts_monthly * months, 2)
        allowable_annual = round(allowable_monthly * months, 2)
        disallowed_annual = round(disallowed_monthly * months, 2)

        # Drawn once and used for both the determination and the scenario -- see the
        # note in `_build_boarder_case`.
        liquid_assets = round(rng.uniform(0, 1500), -2)

        net_income = self.bbce_source.calculate_net_income(
            gross_income=net_se_monthly,
            household_size=hh_size,
            earned_income=net_se_monthly,
        )
        is_eligible, reason = self.bbce_source.is_eligible(
            household_size=hh_size,
            gross_income=net_se_monthly,
            net_income=net_income,
            liquid_assets=liquid_assets,
        )

        gross_limit = self._gross_limit(hh_size)
        limits = self.bbce_source.thresholds().by_household_size(hh_size)
        earned_deduction = round(net_se_monthly * 0.20, 2)

        uid = build_short_uid(rng)
        outcome = "eligible" if is_eligible else "ineligible"
        case_id = (
            f"snap.{self.state.lower()}.eligibility.self_employment_cost_of_doing_business.{outcome}.hh{hh_size}.{uid}"
        )

        allowable_annual_items = {k: round(v * months, 2) for k, v in allowable.items()}
        disallowed_annual_items = {k: round(v * months, 2) for k, v in disallowed.items()}
        allowable_lines = "; ".join(f"{k} ${v:,.2f}" for k, v in allowable_annual_items.items())
        disallowed_lines = "; ".join(f"{k} ${v:,.2f}" for k, v in disallowed_annual_items.items())

        steps = [
            ReasoningStep(
                step_number=1,
                title=("Separate allowable costs of doing business from non-allowable items (7 CFR 273.11(b))"),
                rule_applied="7 CFR 273.11(b)(1), (b)(2)",
                inputs={
                    "allowable_costs_annual": allowable_annual_items,
                    "non_allowable_items_annual": disallowed_annual_items,
                },
                computation=(
                    f"Allowable under 7 CFR 273.11(b)(1) — identifiable costs of labor, stock, "
                    f"raw material, seed and fertilizer, payments on the principal of the purchase "
                    f"price of income-producing capital assets and equipment, interest paid to "
                    f"purchase income-producing property, insurance premiums, and taxes paid on "
                    f"income-producing property: {allowable_lines}. Total allowable: "
                    f"${allowable_annual:,.2f} over {months} months. "
                    f"NOT allowable under 7 CFR 273.11(b)(2): {disallowed_lines}. A net loss from a "
                    f"previous period is barred by (b)(2)(i); income tax set aside and commuting to "
                    f'and from work are barred by (b)(2)(ii) because those expenses "are accounted '
                    f'for by the 20 percent earned income deduction specified in §273.9(d)(2)"; '
                    f"depreciation is barred by (b)(2)(iii). Total excluded from the cost offset: "
                    f"${disallowed_annual:,.2f}."
                ),
                result=(
                    f"Allowable cost of producing self-employment income: ${allowable_annual:,.2f} "
                    f"over {months} months. ${disallowed_annual:,.2f} of claimed expenses is not "
                    f"deductible."
                ),
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=2,
                title=("Average self-employment income over the period it is intended to cover (7 CFR 273.11(a))"),
                rule_applied="7 CFR 273.11(a)(1)(i), (a)(2)(i)",
                inputs={
                    "gross_receipts_annual": receipts_annual,
                    "allowable_costs_annual_total": allowable_annual,
                    "months_averaged": months,
                },
                computation=(
                    f"Under 7 CFR 273.11(a)(2)(i), add gross self-employment income, exclude the "
                    f"cost of producing it, then divide by the number of months over which the "
                    f"income is averaged: (${receipts_annual:,.2f} − ${allowable_annual:,.2f}) ÷ "
                    f"{months} = ${net_se_monthly:,.2f} per month. This is the monthly net "
                    f"self-employment income and it is the household's countable income — the "
                    f"${receipts_annual:,.2f} in gross receipts is not."
                ),
                result=f"Monthly net self-employment income: ${net_se_monthly:,.2f}",
                is_determinative=False,
            ),
            ReasoningStep(
                step_number=3,
                title="Gross income test",
                rule_applied="7 CFR 273.9(a)(1)",
                inputs={
                    "countable_gross_income": net_se_monthly,
                    "gross_limit": gross_limit,
                    "household_size": hh_size,
                },
                computation=(
                    f"${net_se_monthly:,.2f} "
                    f"{'<=' if net_se_monthly <= gross_limit else '>'} "
                    f"${gross_limit:,.2f} ({self._gross_basis(hh_size)})"
                ),
                result="PASS" if net_se_monthly <= gross_limit else "FAIL",
                is_determinative=net_se_monthly > gross_limit,
            ),
            ReasoningStep(
                step_number=4,
                title="Net income test — net self-employment income is earned income",
                rule_applied="7 CFR 273.9(d)(2), 273.11(a)(2)(i)",
                inputs={
                    "earned_income": net_se_monthly,
                    "earned_income_deduction": earned_deduction,
                    "net_income": round(net_income, 2),
                    "net_limit": limits.net_monthly,
                },
                computation=(
                    f"Per 7 CFR 273.11(a)(2)(i) the monthly net self-employment income is added to "
                    f"any other earned income to determine total monthly earned income, so the 20 "
                    f"percent earned income deduction applies: ${net_se_monthly:,.2f} × 20% = "
                    f"${earned_deduction:,.2f}. After the standard and remaining deductions, net "
                    f"income is ${net_income:,.2f} "
                    f"{'<=' if net_income <= limits.net_monthly else '>'} "
                    f"${limits.net_monthly:,.2f} (100% FPL, {hh_size}-person HH)."
                ),
                result="PASS" if net_income <= limits.net_monthly else "FAIL",
                is_determinative=net_se_monthly <= gross_limit and net_income > limits.net_monthly,
                note=(
                    "The non-allowable items in step 1 are not subtracted here either. Deducting "
                    "them and then taking the 20 percent earned income deduction would subtract "
                    "the same money twice — which is the reason 273.11(b)(2)(ii) gives for "
                    "barring them."
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
                    f"A {hh_size}-person household in {self.state} whose only income is a "
                    f"self-employed {enterprise}. Over the last {months} months the business took "
                    f"in ${receipts_annual:,.2f} in gross receipts. Business expenses over the same "
                    f"period were: {allowable_lines}. The household also reports "
                    f"{disallowed_lines}. Countable liquid assets are ${liquid_assets:,.0f}. "
                    f"No household member is elderly or disabled."
                ),
                household_size=hh_size,
                monthly_gross_income=net_se_monthly,
                # Stated, not left to be recomputed: the countable income here is
                # entirely EARNED (7 CFR 273.11(a)(2)(i)), so it carries the 20 percent
                # earned income deduction. A consumer that re-derives net income from
                # gross alone treats it as unearned, skips that deduction, and reads a
                # net figure several hundred dollars too high.
                monthly_net_income=round(net_income, 2),
                liquid_assets=liquid_assets,
                state=self.state,
                additional_context={
                    "threshold_type": "self_employment_cost_of_doing_business",
                    "enterprise": enterprise,
                    "months_averaged": months,
                    "gross_receipts_annual": receipts_annual,
                    "allowable_costs_annual": allowable_annual_items,
                    "non_allowable_items_annual": disallowed_annual_items,
                    "self_employed": True,
                    "monthly_allotment": (self._estimate_benefit(hh_size, net_income) if is_eligible else None),
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This household is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'} for SNAP. "
                f"Gross receipts of ${receipts_annual:,.2f} over {months} months less "
                f"${allowable_annual:,.2f} in allowable costs of producing self-employment income "
                f"(7 CFR 273.11(b)(1)), averaged over {months} months, gives countable monthly "
                f"income of ${net_se_monthly:,.2f}. Depreciation, income tax set aside, commuting, "
                f"and the prior-period net loss are not allowable costs of doing business "
                f"(7 CFR 273.11(b)(2)). {reason}"
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=(
                    f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. Countable income is the net "
                    f"self-employment income of ${net_se_monthly:,.2f} per month, not the "
                    f"${receipts_monthly:,.2f} per month in gross receipts, and the "
                    f"${disallowed_monthly:,.2f} per month of non-allowable items is excluded from "
                    f"the cost offset under 7 CFR 273.11(b)(2)."
                ),
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.11(a), (b)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    ),
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.9(d)(2)",
                        year=self.fiscal_year,
                        url="https://www.ecfr.gov/current/title-7/part-273",
                    ),
                ],
            ),
            variation_tags=["self_employment_cost_of_doing_business"],
            source_citations=[
                "7 CFR Part 273 (2025)",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "self_employment_cost_of_doing_business",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    # Current law after P.L. 119-21 sec 10108, enacted 2025-07-04, which rewrote
    # section 6(f) of the Food and Nutrition Act of 2008 (7 U.S.C. 2015(f)). FNS
    # directs states to apply it to applications and redeterminations processed on
    # or after 2025-11-01 -- two different dates, and conflating them is itself a
    # ground-truth bug, so every case states the one that governs its own facts.
    #
    # Only these noncitizen categories remain eligible by status: lawful permanent
    # residents, Cuban and Haitian entrants, and Compact of Free Association
    # citizens, alongside U.S. citizens and U.S. non-citizen nationals. Refugees and
    # asylees lost status-based eligibility.
    #
    # DELIBERATE TRAP: 7 CFR 273.4(a)(6)(ii) has NOT been amended. It still lists
    # refugees under INA 207 and asylees under INA 208 as eligible and exempt from
    # the five-year bar. A model reasoning from the CFR sounds authoritative and is
    # wrong -- the statute controls -- and a model reasoning from pre-2025 training
    # data reaches the same wrong answer. That is the single largest measured gap in
    # this category (66% pooled across six models, n=204), so the cases below cite
    # the statute and say what the stale regulation would have said.
    #
    # Whether an LPR is subject to the five-year bar (8 U.S.C. 1613) turns on the
    # status held BEFORE adjusting. Per FNS implementation guidance, refugees,
    # asylees, withheld-deportation, Cuban and Haitian entrants, Amerasians, COFA
    # citizens, American Indians born abroad, Hmong and Highland Laotian tribal
    # members, Iraqi and Afghan special immigrants, trafficking victims, and Afghan
    # or Ukrainian nationals paroled inside specific windows are NOT subject.
    # Conditional entrants, battered immigrants, and general one-year parolees ARE.
    # Cases 3-8 below are matched pairs on exactly that distinction: same household,
    # same income, same current status, opposite answers.
    _NONCITIZEN_STATUS_CASES: tuple[_NoncitizenCase, ...] = (
        {
            "key": "refugee_not_adjusted",
            "phrase": (
                "was admitted to the United States as a refugee under section 207 of the "
                "Immigration and Nationality Act and has not adjusted to lawful permanent "
                "resident status"
            ),
            "category": "refugee",
            "category_eligible": False,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Refugee is not one of the noncitizen categories eligible for SNAP. P.L. 119-21 "
                "sec 10108 rewrote section 6(f) of the Food and Nutrition Act of 2008 and limited "
                "eligibility to U.S. citizens, U.S. non-citizen nationals, lawful permanent "
                "residents, Cuban and Haitian entrants, and citizens of the Compact of Free "
                "Association states. Refugees and asylees lost status-based eligibility."
            ),
            "stale_reg_warning": True,
            "bar": None,
        },
        {
            "key": "asylee_not_adjusted",
            "phrase": (
                "was granted asylum under section 208 of the Immigration and Nationality Act "
                "and has not adjusted to lawful permanent resident status"
            ),
            "category": "asylee",
            "category_eligible": False,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Asylee is not one of the noncitizen categories eligible for SNAP. P.L. 119-21 "
                "sec 10108 limited eligibility to U.S. citizens, U.S. non-citizen nationals, "
                "lawful permanent residents, Cuban and Haitian entrants, and citizens of the "
                "Compact of Free Association states."
            ),
            "stale_reg_warning": True,
            "bar": None,
        },
        {
            "key": "lpr_adjusted_from_refugee",
            "phrase": (
                "entered the United States as a refugee under section 207 of the INA and "
                "adjusted to lawful permanent resident status 2 years ago"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": False,
                "why": (
                    "The five-year waiting period is keyed to the status held before adjusting to "
                    "LPR. This applicant entered as a refugee, and a refugee who adjusts to LPR is "
                    "NOT subject to the five-year waiting period, so the 2 years since adjustment "
                    "does not matter."
                ),
            },
        },
        {
            "key": "lpr_adjusted_from_asylee",
            "phrase": (
                "was granted asylum under section 208 of the INA and adjusted to lawful "
                "permanent resident status 18 months ago"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": False,
                "why": (
                    "An asylee who adjusts to LPR is not subject to the five-year waiting period, "
                    "so 18 months of LPR status is not a barrier."
                ),
            },
        },
        {
            "key": "lpr_adjusted_from_trafficking_victim",
            "phrase": (
                "was certified as a victim of a severe form of trafficking in persons and "
                "adjusted to lawful permanent resident status 1 year ago"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": False,
                "why": (
                    "A certified victim of a severe form of trafficking who adjusts to LPR is not "
                    "subject to the five-year waiting period."
                ),
            },
        },
        {
            "key": "lpr_adjusted_from_battered_immigrant",
            "phrase": (
                "held status as a battered non-citizen spouse with a petition pending and "
                "adjusted to lawful permanent resident status 2 years ago"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": True,
                "why": (
                    "Unlike a refugee or asylee, a battered non-citizen who adjusts to LPR IS "
                    "subject to the five-year waiting period. Only 2 of the required 5 years since "
                    "obtaining qualified status have elapsed, and none of the exceptions apply: the "
                    "applicant is over 18, has fewer than 40 qualifying work quarters, is not blind "
                    "or disabled, was not lawfully residing in the U.S. and aged 65 or older on "
                    "1996-08-22, and has no U.S. military connection."
                ),
            },
        },
        {
            "key": "lpr_adjusted_from_conditional_entrant",
            "phrase": (
                "was granted conditional entry under section 203(a)(7) of the INA as in effect "
                "before 1980-04-01 and adjusted to lawful permanent resident status 3 years ago"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": True,
                "why": (
                    "A conditional entrant who adjusts to LPR IS subject to the five-year waiting "
                    "period -- this is the distinction from a refugee or asylee, who is not. Only 3 "
                    "of the required 5 years have elapsed and no exception applies."
                ),
            },
        },
        {
            "key": "lpr_recent_no_exemption",
            "phrase": (
                "obtained lawful permanent resident status 3 years ago through a family-based "
                "petition, with no prior humanitarian immigration status"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": True,
                "why": (
                    "An LPR is eligible only after a five-year waiting period that begins on the "
                    "date qualified-immigrant status was obtained. Only 3 of the 5 years have "
                    "elapsed, and no exception applies: the applicant is over 18, has fewer than 40 "
                    "qualifying work quarters, is not blind or disabled, was not lawfully residing "
                    "in the U.S. and aged 65 or older on 1996-08-22, and has no U.S. military "
                    "connection."
                ),
            },
        },
        {
            "key": "lpr_recent_forty_quarters",
            "phrase": (
                "obtained lawful permanent resident status 3 years ago and has been credited "
                "with 42 qualifying work quarters under the Social Security Act"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": False,
                "why": (
                    "Only 3 of the 5 years have elapsed, but an LPR credited with 40 or more "
                    "qualifying work quarters is eligible with no waiting period. 42 quarters "
                    "exceeds the 40 required, so the waiting period does not apply."
                ),
            },
        },
        {
            "key": "lpr_recent_veteran",
            "phrase": (
                "obtained lawful permanent resident status 1 year ago and is an honorably "
                "discharged veteran of the U.S. armed forces"
            ),
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": False,
                "why": (
                    "An LPR with a U.S. military connection -- active duty other than National "
                    "Guard, or an honorable discharge not on account of immigration status -- is "
                    "eligible with no waiting period, so 1 year of LPR status is not a barrier. A "
                    "discharge 'under honorable conditions' would NOT meet this requirement; this "
                    "applicant's discharge was honorable."
                ),
            },
        },
        {
            "key": "lpr_beyond_five_years",
            "phrase": "has held lawful permanent resident status for 8 years",
            "category": "lawful permanent resident",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Lawful permanent resident is an eligible category under section 6(f) of the "
                "Food and Nutrition Act of 2008 as amended by P.L. 119-21 sec 10108."
            ),
            "bar": {
                "applies": False,
                "why": (
                    "8 years of qualified-immigrant status exceeds the five-year waiting period, so "
                    "no exception is needed."
                ),
            },
        },
        {
            "key": "cuban_haitian_entrant",
            "phrase": (
                "is a Cuban or Haitian entrant under section 501(e) of the Refugee Education "
                "Assistance Act of 1980, admitted 2 years ago"
            ),
            "category": "Cuban or Haitian entrant",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Cuban and Haitian entrants are one of the three noncitizen categories that "
                "P.L. 119-21 sec 10108 left eligible for SNAP."
            ),
            "bar": None,
        },
        {
            "key": "cofa_citizen",
            "phrase": (
                "is a citizen of the Federated States of Micronesia lawfully residing in the "
                "United States under section 141 of the Compact of Free Association, having "
                "arrived 1 year ago"
            ),
            "category": "Compact of Free Association citizen",
            "category_eligible": True,
            "category_rule": "7 U.S.C. 2015(f), as amended by P.L. 119-21 sec 10108",
            "category_why": (
                "Citizens of the Federated States of Micronesia, the Republic of the Marshall "
                "Islands and the Republic of Palau lawfully residing in the U.S. under the "
                "Compacts of Free Association are one of the three noncitizen categories that "
                "P.L. 119-21 sec 10108 left eligible for SNAP."
            ),
            "bar": None,
        },
    )

    def _build_noncitizen_status_case(self, rng: random.Random) -> TestCase:
        """Build a noncitizen status/five-year-bar case (7 U.S.C. 2015(f); 8 U.S.C. 1613).

        Citizenship and noncitizenship status was the weakest of the four measured
        QC error elements -- 66% pooled across six models over n=204 -- and the
        generator produced nothing for it beyond a single coarse mixed-status case.

        Every case is a one-person household with income comfortably inside both
        income limits, so the determination turns entirely on status. That mirrors
        `_build_student_case`: the point is that a non-financial rule decides the
        case before the income test is reached, and the income is stated so a model
        that reaches for it can be seen doing so.

        Two things make this hard in a way that is not answerable from the case
        type name:

        - The governing law changed on 2025-07-04 and 7 CFR 273.4(a)(6)(ii) still
          contradicts it, so both a model citing the current CFR and a model
          reasoning from pre-2025 knowledge get refugee and asylee cases wrong.
        - Whether an LPR faces the five-year bar depends on the status held BEFORE
          adjusting, not on the current status or the elapsed time. Refugee-to-LPR
          and battered-immigrant-to-LPR are identical on the face of the record --
          both are LPRs, both adjusted within the last five years -- and get
          opposite answers.

        Single-person households are deliberate: an ineligible member inside a
        larger household raises the income-counting election in 7 CFR
        273.11(c)(3), which `_build_mixed_immigration_case` already covers. Keeping
        the two apart stops this builder from silently generating a second, weaker
        version of that case.
        """
        fy_config = self.bbce_source.fy_config
        hh_size = 1
        spec = self._NONCITIZEN_STATUS_CASES[rng.randrange(len(self._NONCITIZEN_STATUS_CASES))]

        bar = spec["bar"]
        bar_blocks = bool(bar and bar["applies"])
        is_eligible = bool(spec["category_eligible"]) and not bar_blocks

        # Income placed well inside the flip point so the financial tests always pass
        # and status is the only thing deciding the case. Anchored on the binding
        # ceiling rather than the gross limit for the usual reason: in a raised-BBCE
        # jurisdiction the gross limit does not bind, so a fraction of it is not
        # reliably inside the net limit (see `_binding_gross_ceiling`).
        ceiling = self._binding_gross_ceiling(
            hh_size,
            lambda gross: self.bbce_source.calculate_net_income(
                gross_income=gross,
                household_size=hh_size,
                earned_income=gross,
            ),
        )
        gross = round(ceiling * rng.uniform(0.40, 0.75), 2)
        liquid_assets = round(rng.uniform(0, 1200), -2)

        net_income = self.bbce_source.calculate_net_income(
            gross_income=gross,
            household_size=hh_size,
            earned_income=gross,
        )
        gross_limit = self._gross_limit(hh_size)
        limits = self.bbce_source.thresholds().by_household_size(hh_size)

        uid = build_short_uid(rng)
        outcome = "eligible" if is_eligible else "ineligible"
        case_id = f"snap.{self.state.lower()}.eligibility.noncitizen_status_{spec['key']}.{outcome}.hh{hh_size}.{uid}"

        steps = [
            ReasoningStep(
                step_number=1,
                title="Determine whether the applicant's immigration status is an eligible category",
                rule_applied=spec["category_rule"],
                inputs={
                    "claimed_status": spec["category"],
                    "eligible_categories": [
                        "U.S. citizen",
                        "U.S. non-citizen national",
                        "lawful permanent resident",
                        "Cuban or Haitian entrant",
                        "Compact of Free Association citizen",
                    ],
                    "governing_law_effective": "2025-07-04",
                    "state_implementation_date": "2025-11-01",
                },
                computation=spec["category_why"],
                result=(
                    f"PASS — {spec['category']} is an eligible category"
                    if spec["category_eligible"]
                    else f"INELIGIBLE — {spec['category']} is not an eligible category"
                ),
                is_determinative=not spec["category_eligible"],
                note=(
                    "7 CFR 273.4(a)(6)(ii) has not been amended to match and still lists refugees "
                    "under INA 207 and asylees under INA 208 as eligible and exempt from the "
                    "five-year bar. The statute controls: the regulation is stale, not an "
                    "alternative reading."
                    if spec.get("stale_reg_warning")
                    else None
                ),
            )
        ]

        if spec["category_eligible"] and bar is not None:
            steps.append(
                ReasoningStep(
                    step_number=2,
                    title="Apply the five-year waiting period for lawful permanent residents",
                    rule_applied="8 U.S.C. 1613; 7 U.S.C. 2015(f)",
                    inputs={
                        "status_before_adjusting_to_lpr": spec["key"],
                        "exceptions_checked": [
                            "under_age_18",
                            "40_qualifying_quarters",
                            "blind_or_disabled",
                            "lawfully_residing_and_65_on_1996_08_22",
                            "us_military_connection",
                        ],
                    },
                    computation=bar["why"],
                    result=(
                        "INELIGIBLE — five-year waiting period not met and no exception applies"
                        if bar["applies"]
                        else "PASS — five-year waiting period does not bar this applicant"
                    ),
                    is_determinative=bar["applies"],
                )
            )

        if is_eligible:
            steps.append(
                ReasoningStep(
                    step_number=len(steps) + 1,
                    title="Gross income test",
                    rule_applied="7 CFR 273.9(a)(1)",
                    inputs={
                        "gross_income": gross,
                        "gross_limit": gross_limit,
                        "household_size": hh_size,
                    },
                    computation=(
                        f"${gross:,.2f} "
                        f"{'<=' if gross <= gross_limit else '>'} "
                        f"${gross_limit:,.2f} ({self._gross_basis(hh_size)})"
                    ),
                    result="PASS" if gross <= gross_limit else "FAIL",
                    is_determinative=False,
                )
            )
            steps.append(
                ReasoningStep(
                    step_number=len(steps) + 1,
                    title="Net income test",
                    rule_applied="7 CFR 273.9(a)(2)",
                    inputs={
                        "net_income": round(net_income, 2),
                        "net_limit": limits.net_monthly,
                    },
                    computation=(
                        f"${net_income:,.2f} "
                        f"{'<=' if net_income <= limits.net_monthly else '>'} "
                        f"${limits.net_monthly:,.2f} (100% FPL, {hh_size}-person HH)"
                    ),
                    result="PASS" if net_income <= limits.net_monthly else "FAIL",
                    is_determinative=False,
                )
            )

        else:
            # Every case needs a second step (TestCase requires two) and, more to the
            # point, a denial on status should show what the income test WOULD have
            # said -- otherwise a reader cannot tell whether status decided the case
            # or the household simply had too much income. Stating the governing
            # limit here also puts these cases under the same stated-limit gate as
            # every financial case.
            steps.append(
                ReasoningStep(
                    step_number=len(steps) + 1,
                    title="Income test not reached",
                    rule_applied="7 CFR 273.9(a)(1)",
                    inputs={
                        "gross_income": gross,
                        "gross_limit": gross_limit,
                        "household_size": hh_size,
                    },
                    computation=(
                        f"Not reached. The applicant is ineligible on "
                        f"{'the five-year waiting period' if bar_blocks else 'immigration status category'} "
                        f"before any income test applies. For completeness, gross income of "
                        f"${gross:,.2f} is within the ${gross_limit:,.2f} limit "
                        f"({self._gross_basis(hh_size)}), so income is not what denies this case."
                    ),
                    result="NOT REACHED — denial is on status, not income",
                    is_determinative=False,
                )
            )

        if is_eligible:
            answer_tail = (
                f"Income of ${gross:,.2f} is within the ${gross_limit:,.2f} gross limit "
                f"({self._gross_basis(hh_size)}) and net income of ${net_income:,.2f} is within the "
                f"${limits.net_monthly:,.2f} net limit."
            )
        elif not spec["category_eligible"]:
            answer_tail = (
                f"The income test is not reached: status decides the case. Income of "
                f"${gross:,.2f} would have been within the ${gross_limit:,.2f} gross limit."
            )
        else:
            answer_tail = (
                f"The income test is not reached: the five-year waiting period decides the case. "
                f"Income of ${gross:,.2f} would have been within the ${gross_limit:,.2f} gross limit."
            )

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=Difficulty.ADVERSARIAL,
            scenario=ScenarioBlock(
                summary=(
                    f"A single applicant in {self.state} applying for SNAP in November 2025 or "
                    f"later. The applicant {spec['phrase']}. Monthly gross income is "
                    f"${gross:,.2f}, all from wages, and countable liquid assets are "
                    f"${liquid_assets:,.0f}. The applicant is over 18, is not blind or disabled, "
                    f"and is not elderly."
                ),
                household_size=hh_size,
                monthly_gross_income=gross,
                monthly_net_income=round(net_income, 2),
                liquid_assets=liquid_assets,
                state=self.state,
                additional_context={
                    "threshold_type": "noncitizen_status_eligibility",
                    "noncitizen_case": spec["key"],
                    "claimed_status": spec["category"],
                    "status_category_eligible": bool(spec["category_eligible"]),
                    "five_year_bar_applies": bar_blocks,
                    "governing_law": "P.L. 119-21 sec 10108 (2025-07-04)",
                    "monthly_allotment": (self._estimate_benefit(hh_size, net_income) if is_eligible else None),
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This applicant is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'} for SNAP. "
                f"{spec['category_why']} "
                + (f"{bar['why']} " if spec["category_eligible"] and bar is not None else "")
                + answer_tail
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=(
                    f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. "
                    + (
                        f"{spec['category']} is not an eligible noncitizen category under "
                        f"7 U.S.C. 2015(f) as amended by P.L. 119-21 sec 10108."
                        if not spec["category_eligible"]
                        else (
                            "The five-year waiting period under 8 U.S.C. 1613 is not met and no exception applies."
                            if bar_blocks
                            else "Status is eligible and both income tests pass."
                        )
                    )
                ),
                policy_basis=[
                    PolicyCitation(
                        document="Public Law 119-21",
                        section="sec 10108 (amending 7 U.S.C. 2015(f))",
                        year=2025,
                        url="https://www.congress.gov/bill/119th-congress/house-bill/1/text",
                    ),
                    PolicyCitation(
                        document="8 U.S.C. 1613",
                        section="five-year limited eligibility for qualified aliens",
                        year=self.fiscal_year,
                        url="https://www.law.cornell.edu/uscode/text/8/1613",
                    ),
                ],
            ),
            variation_tags=["noncitizen_status_eligibility", spec["key"]],
            source_citations=[
                "P.L. 119-21 sec 10108 (2025-07-04)",
                "USDA FNS, SNAP Implementation of the One Big Beautiful Bill Act of 2025 — Alien SNAP Eligibility",
                f"USDA FNS SNAP Income and Resource Limits {fy_config.period_label}",
            ],
            seed=None,
            metadata={
                "generator": "SNAPEligibilityGenerator",
                "profile_strategy": "noncitizen_status_eligibility",
                "state": self.state,
                "fiscal_year": self.fiscal_year,
            },
        )

    def _build_migrant_case(self, rng: random.Random) -> TestCase:
        """Build a migrant/seasonal worker income averaging case (7 CFR 273.10(c)(3))."""
        t = self.bbce_source.thresholds()
        fy_config = self.bbce_source.fy_config
        hh_size = rng.randint(2, 4)
        limits = t.by_household_size(hh_size)
        gross_limit = self._gross_limit(hh_size)

        # Earnings are drawn around the income at which this case's determination
        # actually flips, not around the gross limit -- which in a 200%-FPL
        # jurisdiction is roughly twice the binding net limit, so every draw near it
        # lands ineligible and this case type's label goes constant. See
        # `_binding_gross_ceiling`. The 0.70-1.20 band is unchanged; only what it is a
        # band AROUND changed, which is what restores the ~60/40 label mix.
        ceiling = self._binding_gross_ceiling(
            hh_size,
            lambda gross: self.bbce_source.calculate_net_income(
                gross_income=gross,
                household_size=hh_size,
                earned_income=gross,
            ),
        )
        work_months = rng.randint(4, 8)
        seasonal_total = round(
            rng.uniform(ceiling * work_months * 0.70, ceiling * work_months * 1.20),
            2,
        )
        averaged_monthly = round(seasonal_total / work_months, 2)

        # Drawn once, used for both the determination and the scenario -- see the
        # note in `_build_boarder_case`.
        liquid_assets = round(rng.uniform(0, 800), -2)

        net_income = self.bbce_source.calculate_net_income(
            gross_income=averaged_monthly,
            household_size=hh_size,
            earned_income=averaged_monthly,
        )

        is_eligible, reason = self.bbce_source.is_eligible(
            household_size=hh_size,
            gross_income=averaged_monthly,
            net_income=net_income,
            liquid_assets=liquid_assets,
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
                inputs={"averaged_monthly": averaged_monthly, "gross_limit": gross_limit},
                computation=(
                    f"${averaged_monthly:,.2f} "
                    f"{'<=' if averaged_monthly <= gross_limit else '>'} "
                    f"${gross_limit:,.2f} ({self._gross_basis(hh_size)})"
                ),
                result="PASS" if averaged_monthly <= gross_limit else "FAIL",
                is_determinative=averaged_monthly > gross_limit,
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
                liquid_assets=liquid_assets,
                state=self.state,
                additional_context={
                    "is_migrant_worker": True,
                    "seasonal_total": seasonal_total,
                    "work_months": work_months,
                    "threshold_type": "migrant_income_averaging",
                    "monthly_allotment": self._estimate_benefit(hh_size, net_income) if is_eligible else None,
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
        """Build a mixed immigration status case (7 CFR 273.11(c)(3)).

        Ineligible members are excluded from the household SIZE used for the limit
        lookup, and this case counts their income in full.

        Counting in full is a STATE ELECTION, not the only federal rule. 7 CFR
        273.11(c)(3)(i) reads: the State agency "must count all or, at the
        discretion of the State agency, all but a pro rata share, of the
        ineligible alien's income and deductible expenses". A state may even
        split the two tests -- counting all of the income for the gross income
        test while counting all but a pro rata share for the net income test and
        the benefit level. This builder generates the count-all election and says
        so, rather than asserting a federal rule that does not exist.

        The election is only available here because the ineligible member is a
        NON-QUALIFIED alien. 273.11(c)(3)(i) does not apply to an alien in the
        (A)-(G) list -- LPRs, asylees under INA 208, refugees under INA 207,
        parolees under 212(d)(5), withheld-deportation, certain aged/blind/
        disabled, and special agricultural workers. For those, (c)(3)(ii) gives
        the state a different pair of options and full counting is not one of
        them, so a case whose ineligible member is an LPR inside the five-year
        bar cannot use this shape.

        Do not cite 7 CFR 273.4(c) for any of this: that paragraph is SPONSOR
        DEEMING, a different mechanism that attributes a sponsor's income to the
        sponsored alien. 273.11(c)(3)(v) points the other way and forbids
        counting the sponsor's income when determining an ineligible sponsored
        alien's own income.
        """
        t = self.bbce_source.thresholds()
        fy_config = self.bbce_source.fy_config
        total_members = rng.randint(3, 5)
        ineligible_count = 1
        eligible_count = total_members - ineligible_count  # HH size for limit lookup

        limits_reduced = t.by_household_size(eligible_count)
        gross_limit_reduced = self._gross_limit(eligible_count)

        # Income near the point where this case's determination flips, computed on the
        # REDUCED household size (the size every limit lookup here uses). Anchoring on
        # the reduced-size gross limit instead put every draw in a 200%-FPL
        # jurisdiction far inside the gross limit and far outside the net one, which
        # collapsed this case type to 82.3% ineligible overall and 100% ineligible
        # across all 28 such jurisdictions -- see `_binding_gross_ceiling`. The
        # 0.80-1.15 band is unchanged.
        ceiling_reduced = self._binding_gross_ceiling(
            eligible_count,
            lambda gross: self.bbce_source.calculate_net_income(
                gross_income=gross,
                household_size=eligible_count,
                earned_income=gross,
            ),
        )
        gross = round(rng.uniform(ceiling_reduced * 0.80, ceiling_reduced * 1.15), 2)

        # Drawn once, used for both the determination and the scenario -- see the
        # note in `_build_boarder_case`.
        liquid_assets = round(rng.uniform(0, 1500), -2)

        net_income = self.bbce_source.calculate_net_income(
            gross_income=gross,
            household_size=eligible_count,  # Use reduced HH size for deductions
            earned_income=gross,
        )

        is_eligible, reason = self.bbce_source.is_eligible(
            household_size=eligible_count,  # Reduced size for limit lookup
            gross_income=gross,  # Full income
            net_income=net_income,
            liquid_assets=liquid_assets,
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
                title="Identify household composition — mixed immigration status (7 CFR 273.11(c)(3))",
                rule_applied="7 CFR 273.11(c)(3)",
                inputs={
                    "total_members": total_members,
                    "ineligible_members": ineligible_count,
                    "eligible_members": eligible_count,
                    "income_election": "count_all",
                },
                computation=(
                    f"Total household members: {total_members}. Ineligible (non-qualified "
                    f"alien) members: {ineligible_count}. Under 7 CFR 273.11(c)(3), an ineligible "
                    f"alien is excluded from the household size used for the limit lookup. "
                    f"HH size for limit lookup: {total_members} − {ineligible_count} = {eligible_count}. "
                    f'Income: 273.11(c)(3)(i) requires the State agency to "count all or, at the '
                    f"discretion of the State agency, all but a pro rata share, of the ineligible "
                    f"alien's income and deductible expenses\". This jurisdiction counts all of it, "
                    f"so the full ${gross:,.2f} is tested against {eligible_count}-person limits. "
                    f"A state electing the pro rata option would instead count "
                    f"{eligible_count}/{total_members} of it."
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
                    "gross_limit": gross_limit_reduced,
                    "hh_size_for_test": eligible_count,
                },
                computation=(
                    f"Using {eligible_count}-person household limits (after excluding "
                    f"ineligible member): ${gross:,.2f} "
                    f"{'<=' if gross <= gross_limit_reduced else '>'} "
                    f"${gross_limit_reduced:,.2f} ({self._gross_basis(eligible_count)})"
                ),
                result="PASS" if gross <= gross_limit_reduced else "FAIL",
                is_determinative=gross > gross_limit_reduced,
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
                liquid_assets=liquid_assets,
                state=self.state,
                additional_context={
                    "has_ineligible_members": True,
                    "ineligible_member_count": ineligible_count,
                    "eligible_member_count": eligible_count,
                    "threshold_type": "mixed_immigration_status_hh_size_reduction",
                    # Benefit-issuance unit size: only the eligible members receive an
                    # allotment, so `eligible_count` (already the basis for the gross/net
                    # limit lookups above) is used here too, via the same drop-in helper
                    # used everywhere else (default is_categorically_eligible=True).
                    # FLAGGED, NOT RESOLVED (see the Task 1 fix report): whether the
                    # 1-2-person minimum-benefit floor should apply here at all -- and
                    # whether `eligible_count` is the right basis for it -- under 7 CFR
                    # 273.11(c)(2)/273.11(c)(3) for a size-reduced, non-categorically-linked
                    # household is a real policy question this task does not resolve with
                    # confidence. Left as the plain drop-in rather than forcing a guess.
                    "monthly_allotment": (self._estimate_benefit(eligible_count, net_income) if is_eligible else None),
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome=outcome,
            expected_answer=(
                f"This household is {'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. "
                f"Under 7 CFR 273.11(c)(3), the {ineligible_count} ineligible member is excluded from "
                f"the household size used for the limit lookup ({total_members}→{eligible_count} persons). "
                f"273.11(c)(3)(i) lets the State agency count all, or all but a pro rata share, of "
                f"that member's income; this jurisdiction counts all of it, so the household's full "
                f"income of ${gross:,.2f} is tested against {eligible_count}-person limits."
            ),
            rationale_trace=RationaleTrace(
                steps=steps,
                conclusion=(
                    f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. {reason} "
                    f"(using {eligible_count}-person limits per 7 CFR 273.11(c)(3))"
                ),
                policy_basis=[
                    PolicyCitation(
                        document="7 CFR Part 273",
                        section="7 CFR 273.11(c)(3)",
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
        fy_config = self.bbce_source.fy_config
        hh_size = rng.randint(1, 4)
        gross_limit = self._gross_limit(hh_size)

        # Income ABOVE the GOVERNING gross limit — would be ineligible without
        # categorical eligibility. The case only demonstrates that the income test
        # is skipped if the income actually exceeds the limit the prompt states; a
        # figure above the federal 130% limit but under a state's raised BBCE limit
        # demonstrates nothing, and the prose asserting it "exceeds the limit"
        # would be false.
        gross = round(gross_limit * rng.uniform(1.10, 1.40), 2)
        unearned = round(rng.uniform(200, 600), -1)  # SSI/TANF benefit

        # Categorical eligibility skips the *income test*, but the benefit amount
        # still depends on net income (7 CFR 273.10(e)) -- this case never computed
        # one before, so `estimate_monthly_benefit` had nothing to work from. Total
        # countable income is wages (earned) plus the TANF/SSI payment (unearned);
        # only the earned portion gets the 20% earned-income deduction.
        net_income = self.bbce_source.calculate_net_income(
            gross_income=gross + unearned,
            household_size=hh_size,
            earned_income=gross,
            has_elderly_or_disabled=True,
        )

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
                    "gross_limit": gross_limit,
                    "skipped": True,
                },
                computation=(
                    f"NOTE: Gross income ${gross:,.2f} exceeds the ${gross_limit:,.2f} limit "
                    f"({self._gross_basis(hh_size)}). However, the income test is not applied because "
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
                monthly_net_income=round(net_income, 2),
                liquid_assets=round(rng.uniform(0, 2000), -2),
                state=self.state,
                has_elderly_or_disabled=True,
                additional_context={
                    "tanf_or_ssi_recipient": True,
                    "unearned_income": unearned,
                    "threshold_type": "categorical_eligibility_tanf_ssi",
                    # This case type is always eligible (see expected_outcome below).
                    # Categorically eligible via TANF/SSI, so the minimum-benefit
                    # floor for 1-2 person households correctly applies here.
                    "monthly_allotment": self._estimate_benefit(hh_size, net_income),
                },
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome="eligible",
            expected_answer=(
                f"This household is ELIGIBLE for SNAP under categorical eligibility. "
                f"Although gross income of ${gross:,.2f} exceeds the ${gross_limit:,.2f} limit "
                f"({self._gross_basis(hh_size)}), "
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

    def _build_bbce_expanded_income_case(self, rng: random.Random) -> TestCase:
        """Build a BBCE expanded-gross-limit case (7 CFR 273.2(j)(2)(ii)).

        The headline reasoning test for BBCE: a household whose gross income falls
        BETWEEN the federal 130% FPL limit and the state's higher BBCE limit. Such a
        household is INELIGIBLE under federal rules but ELIGIBLE under BBCE. An
        adversarial variant places gross income ABOVE the state BBCE limit (ineligible),
        and the net income test still binds throughout.

        Raises ValueError for a jurisdiction with no such band -- see
        `supports_bbce_expanded_income` for why this fails loudly rather than
        substituting another jurisdiction's parameters. `generate()` never reaches
        this path for such a jurisdiction; `_available_special_population_builders`
        drops the builder instead.
        """
        if not self.supports_bbce_expanded_income:
            p = self.bbce_source.bbce_params
            raise ValueError(
                f"{self.state} cannot support a BBCE expanded-gross-limit case: "
                f"bbce={p.bbce}, gross_income_limit_pct_fpl={p.gross_income_limit_pct_fpl}. "
                "The case needs a gross-income band between the federal 130% FPL limit "
                "and a higher state limit. Use `supports_bbce_expanded_income` to check "
                "before calling, or call generate(), which skips this type for such a "
                "jurisdiction rather than borrowing another jurisdiction's parameters."
            )
        src = self.bbce_source
        state = src.state
        p = src.bbce_params
        fy_config = src.fy_config
        hh_size = rng.randint(1, 4)

        federal_limit = src.federal_gross_limit(hh_size)
        bbce_limit = src.effective_gross_limit(hh_size)
        limits = src.thresholds().by_household_size(hh_size)
        net_limit = limits.net_monthly
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
                    # BBCE-eligible households are categorically eligible by definition
                    # (7 CFR 273.2(j)(2)(ii) -- that is what BBCE confers), so the
                    # minimum-benefit floor for 1-2 person households correctly applies.
                    "monthly_allotment": (
                        self._estimate_benefit(hh_size, net_income, source=src) if is_eligible else None
                    ),
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

    def _estimate_benefit(
        self,
        household_size: int,
        net_income: float,
        *,
        source: SNAPBBCESource | None = None,
        is_categorically_eligible: bool = True,
    ) -> float:
        """Single site for the monthly SNAP allotment estimate across every builder.

        Delegates to ``SNAPBBCESource.estimate_monthly_benefit`` (7 CFR 273.10(e):
        max allotment minus 30% of net income, with the 1-2-person minimum-benefit
        floor) rather than reimplementing the formula. The bug this method fixes
        was exactly a one-line reimplementation (``limits.max_benefit or 0.0`` --
        the *maximum* allotment for the household size, ignoring net income
        entirely) copy-pasted into eight call sites; routing all of them through
        one method makes a future formula change (or fix) land everywhere at once.

        `source` remains overridable, but every builder now passes this
        generator's own `self.bbce_source`: the case that used to build against a
        DIFFERENT state's `SNAPBBCESource` (the removed `_bbce_source_for_case`,
        which transplanted onto CA) no longer exists, so there is no longer a
        legitimate reason for an allotment to come from another jurisdiction's
        table. The parameter is kept because a caller passing the wrong source
        here is the exact failure the old code shipped, and an explicit argument
        keeps that visible at the call site rather than implicit in a default.
        """
        src = source or self.bbce_source
        return src.estimate_monthly_benefit(
            household_size, net_income, is_categorically_eligible=is_categorically_eligible
        )

    def _build_case(
        self,
        profile: USHouseholdProfile,
        seed: int | None,
        index: int,
        *,
        phrasing_style: str | None = None,
    ) -> TestCase:
        """Build a complete TestCase from a profile. See `generate`'s docstring for `phrasing_style`."""
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

        # Estimate the monthly allotment once, via the single shared helper (7 CFR
        # 273.10(e): max allotment minus 30% of net income, not the max allotment
        # alone) -- and feed that same number into the rationale trace, the
        # expected-answer prose, and scenario.additional_context below, so all
        # three agree.
        benefit = self._estimate_benefit(profile.household_size, net_income) if is_eligible else 0.0

        # Build rationale trace
        trace = self._build_rationale_trace(profile, net_income, limits, is_eligible, fy_config, benefit)

        # Determine difficulty
        difficulty = self._classify_difficulty(profile, is_eligible)

        # Generate unique ID
        case_id = self._make_case_id(profile, is_eligible, index, seed)

        # Build scenario summary. See `generate`'s docstring for `phrasing_style`.
        # The "random" branch derives its pick from (seed, index) with plain
        # integer arithmetic, never from `hash()` (str hashing is randomized
        # per-process unless PYTHONHASHSEED is fixed, which would make the same
        # seed pick different styles across runs) and never from `rng` (which
        # would consume randomness the fact-sampling above already depends on,
        # changing sampled facts depending on whether phrasing_style is set).
        if phrasing_style is None:
            scenario_summary = profile.natural_language_summary("snap")
        elif phrasing_style == "random":
            style = PHRASING_STYLES[((seed or 0) + index) % len(PHRASING_STYLES)]
            scenario_summary = profile.natural_language_summary_styled(style, "snap")
        else:
            scenario_summary = profile.natural_language_summary_styled(phrasing_style, "snap")

        # Build expected answer
        expected_answer = self._build_expected_answer(
            profile, net_income, limits, is_eligible, reason, fy_config, benefit
        )

        # Persist the computed monthly allotment so downstream consumers (e.g. the
        # JSONL formatter's machine-checkable answer block) can read the value the
        # generator already computed instead of recomputing it. This is the exact
        # same `benefit` value used in the rationale/expected-answer text above, so
        # it cannot drift from what the case already states.
        scenario_fields = profile.to_scenario_fields()
        if is_eligible:
            scenario_fields["additional_context"] = {
                **scenario_fields["additional_context"],
                "monthly_allotment": benefit,
            }

        return TestCase(
            case_id=case_id,
            program=Program.SNAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=difficulty,
            scenario=ScenarioBlock(
                summary=scenario_summary,
                **scenario_fields,
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
        benefit: float = 0.0,
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

        # All tests passed. `benefit` is passed in by the caller (_build_case),
        # computed once via the shared _estimate_benefit helper so this text
        # agrees with scenario.additional_context["monthly_allotment"] and the
        # JSONL formatter's answer block.
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
        benefit: float = 0.0,
    ) -> str:
        t = self.bbce_source.thresholds()
        std_ded = get_standard_deduction(profile.household_size)
        earned = profile.earned_income or profile.monthly_gross_income
        earned_ded = earned * 0.20
        gross_pct = self.bbce_source.bbce_params.gross_income_limit_pct_fpl
        gross_limit = self.bbce_source.effective_gross_limit(profile.household_size)

        if is_eligible:
            # `benefit` is passed in by the caller (_build_case), computed once via
            # the shared _estimate_benefit helper -- the actual allotment (max
            # allotment minus 30% of net income, per 7 CFR 273.10(e)), not the
            # household-size maximum -- so this text agrees with
            # scenario.additional_context["monthly_allotment"] and the JSONL
            # formatter's answer block.
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
                f"(7 CFR 273.10(e): the {profile.household_size}-person maximum allotment "
                f"minus 30% of net income, subject to the minimum-benefit floor for "
                f"1-2 person households)."
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
        offset = profile.extra.get("offset_pct")
        if offset is None:
            # Sampled independently of any threshold: we do not know how far this
            # household sits from a limit, so we must not claim it is clear of one.
            return Difficulty.MEDIUM

        # No `and threshold_type` conjunct here: the two callers that populate
        # offset_pct (_build_snap_threshold_profile, _build_wic_threshold_profile
        # in us_household.py) always set threshold_type in the same extra dict,
        # so by the time offset is not None, threshold_type is never falsy.
        if abs(offset) <= 0.01:
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
        offset = profile.extra.get("offset_pct")
        outcome = "eligible" if is_eligible else "ineligible"
        hh = f"hh{profile.household_size}"
        # Combine seed and index so cases within the same batch don't collide
        # even when derived from the same per-case seed. seed=None preserves
        # non-deterministic behavior (a fresh os-random seed per call).
        uid_seed = f"{seed}-{index}" if seed is not None else None
        uid = build_short_uid(random.Random(uid_seed))
        # Mirror _build_variation_tags: when offset_pct is unset (e.g. 'uniform'
        # / 'realistic' strategies with extra == {}), we have no known distance
        # from any threshold, so omit the offset segment entirely rather than
        # defaulting to 0.0 and fabricating an "at_limit" claim.
        segments = ["snap", self.state.lower(), "eligibility", threshold]
        if offset is not None:
            segments.append(self._offset_tag(offset))
        segments.extend([outcome, hh, uid])
        return ".".join(segments)

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
