"""Every generated case's `expected_outcome` must agree with the source a consumer renders.

This is the invariant whose absence let a whole class of ground-truth bug ship
undetected. Six of the seven special-population builders decided eligibility from
a plain `SNAPSource` -- the federal 130% FPL table -- while every consumer of this
kit renders a jurisdiction's *BBCE* parameters into the prompt. In a raised-BBCE
state the prompt therefore stated a 3-person gross limit of $4,442 while the
ground truth said "INELIGIBLE: gross income $2,901.30 exceeds $2,888.00 (130%
FPL)". A model reading the stated parameters and applying them correctly was
scored WRONG; a model fine-tuned on those records was scored right.

Measured on a 53-jurisdiction, 6,360-case FY2026 run before the fix: 74 cases
whose OUTCOME flipped, and 248 whose stated gross-income step was computed
against a limit the prompt never showed. Nothing in the kit asserted otherwise.

The invariant, stated once:

    for every generated case, `expected_outcome` must equal the outcome
    `SNAPBBCESource(state=case.scenario.state).is_eligible(...)` returns when
    handed the case's own stated financial facts.

Two things this file is careful about:

- The financial facts are read off the CASE, not recomputed. `household_size` for
  the limit lookup comes from `additional_context["eligible_member_count"]` when
  present (the mixed-immigration builder tests full income against a reduced
  household size, per 7 CFR 273.4(c)(3)) and from `scenario.household_size`
  otherwise. If a builder ever writes a scenario whose stated facts do not
  produce its own stated outcome, that is precisely the bug being hunted.
- One case type is genuinely NOT decided by the financial waterfall:
  `student_exclusion` (7 CFR 273.5(a)) is a non-financial denial that fires
  BEFORE the income test. Exempting it silently would be a loophole big enough
  to hide a regression in, so it is exempted by name and given its own, stronger
  assertion: the case must be ineligible AND must pass the financial test, which
  is the entire premise of the case type.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest
from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator
from govsynth.models.test_case import TestCase
from govsynth.sources.base import THRESHOLD_DIR
from govsynth.sources.us.snap import SNAPSource
from govsynth.sources.us.snap_bbce import SNAPBBCESource

FISCAL_YEAR = 2026

# Case types whose determinative rule is not the financial waterfall. Exempted
# from the equality assertion and covered by their own test below instead.
NON_FINANCIAL_OUTCOME_TYPES = frozenset({"student_exclusion"})


def all_jurisdictions(fiscal_year: int = FISCAL_YEAR) -> list[str]:
    """Every jurisdiction in the FY BBCE table, so no state is exempt from the check."""
    table = json.loads((THRESHOLD_DIR / f"snap_bbce_fy{fiscal_year}.json").read_text(encoding="utf-8"))
    return sorted(table["states"])


def financial_facts(case: TestCase) -> dict[str, Any]:
    """The eligibility inputs a case ITSELF states, as `is_eligible` keyword arguments.

    Read off the case rather than recomputed: the whole point is to catch a case
    whose stated facts do not support its stated outcome.
    """
    context = case.scenario.additional_context or {}
    return {
        # 7 CFR 273.4(c)(3): ineligible members leave the household size used for
        # the limit lookup while their income still counts in full.
        "household_size": context.get("eligible_member_count", case.scenario.household_size),
        "gross_income": case.scenario.monthly_gross_income,
        "net_income": case.scenario.monthly_net_income,
        "liquid_assets": case.scenario.liquid_assets or 0.0,
        "has_elderly_or_disabled": bool(case.scenario.has_elderly_or_disabled),
        # 7 CFR 273.2(j)(2): a TANF/SSI household is categorically eligible and the
        # income test is skipped. NOT keyed on `bbce_state`, which the BBCE
        # expanded-income case sets while still being decided by the raised gross
        # limit rather than by categorical eligibility.
        "is_categorically_eligible": bool(context.get("tanf_or_ssi_recipient", False)),
    }


def bbce_outcome(case: TestCase, fiscal_year: int = FISCAL_YEAR) -> tuple[str, str]:
    """`(outcome, reason)` from the BBCE-aware source, on the case's own stated facts."""
    source = SNAPBBCESource(fiscal_year=fiscal_year, state=case.scenario.state)
    eligible, reason = source.is_eligible(**financial_facts(case))
    return ("eligible" if eligible else "ineligible"), reason


def federal_outcome(case: TestCase, fiscal_year: int = FISCAL_YEAR) -> str:
    """The outcome the removed federal-only path would have produced, for contrast."""
    source = SNAPSource(fiscal_year=fiscal_year, state=case.scenario.state)
    eligible, _reason = source.is_eligible(**financial_facts(case))
    return "eligible" if eligible else "ineligible"


def case_type(case: TestCase) -> str:
    return (case.scenario.additional_context or {}).get("threshold_type", "unknown")


@pytest.mark.parametrize("state", all_jurisdictions())
def test_expected_outcome_agrees_with_the_bbce_source(state: str) -> None:
    """Every jurisdiction, every builder path: stated facts must produce the stated outcome.

    Parametrized over all 53 jurisdictions rather than a hand-picked
    "representative" few: the bug this replaces was invisible in exactly the
    jurisdictions a small sample would have skipped (it never fires in a non-BBCE
    state, where the federal and BBCE limits coincide), and picking samples is how
    it stayed hidden.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    cases = generator.generate(n=40, profile_strategy="edge_saturated", seed=20260810)

    mismatches = []
    for case in cases:
        if case_type(case) in NON_FINANCIAL_OUTCOME_TYPES:
            continue
        outcome, reason = bbce_outcome(case)
        if outcome != case.expected_outcome:
            mismatches.append(
                f"{case.case_id}: expected_outcome={case.expected_outcome!r} but "
                f"SNAPBBCESource({state}).is_eligible says {outcome!r} ({reason})"
            )

    assert not mismatches, (
        f"{len(mismatches)} of {len(cases)} {state} cases state an outcome the "
        "jurisdiction's own BBCE parameters do not support -- a model that read the "
        "rendered parameter block and applied it correctly would be scored wrong on "
        "each of them:\n  " + "\n  ".join(mismatches[:10])
    )


@pytest.mark.parametrize("state", all_jurisdictions())
def test_every_case_is_attributed_to_its_own_jurisdiction(state: str) -> None:
    """No case may be attributed to a jurisdiction other than the generator's own.

    `_bbce_source_for_case` used to silently rewrite `scenario.state`, `case_id`,
    and `jurisdiction` to CA for any jurisdiction that could not support a
    BBCE expanded-income case. For a whole-jurisdiction-holdout experiment that
    moves cases across the train/test boundary and reweights the holdout tier.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    cases = generator.generate(n=40, profile_strategy="edge_saturated", seed=20260810)

    assert len(cases) == 40, f"asked for 40 {state} cases, got {len(cases)}"
    wrong = [
        c.case_id
        for c in cases
        if c.scenario.state != state
        or c.jurisdiction != f"us.{state.lower()}"
        or not c.case_id.startswith(f"snap.{state.lower()}.")
    ]
    assert not wrong, f"{len(wrong)} {state} cases are attributed elsewhere: {wrong[:5]}"


def test_student_exclusion_cases_are_ineligible_despite_passing_the_financial_test() -> None:
    """The one exempted case type, held to a stronger assertion than equality.

    `student_exclusion` is ineligible for a non-financial reason (7 CFR 273.5(a)),
    so it cannot be checked against `is_eligible`. But its premise is that the
    exclusion fires BEFORE the income test -- so the financial test must actually
    PASS. A student case that was financially ineligible anyway would be testing
    nothing, and would let the exemption above hide a real regression.
    """
    checked = 0
    for state in ("VA", "AL", "CA", "GU", "VI"):
        generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
        for seed in range(15):
            case = generator._build_student_case(random.Random(seed))
            assert case.expected_outcome == "ineligible", case.case_id
            source = SNAPBBCESource(fiscal_year=FISCAL_YEAR, state=state)
            gross_limit = source.effective_gross_limit(case.scenario.household_size)
            assert case.scenario.monthly_gross_income <= gross_limit, (
                f"{case.case_id}: gross income ${case.scenario.monthly_gross_income:,.2f} exceeds "
                f"the governing ${gross_limit:,.2f} limit, so this case does not demonstrate "
                "that the student exclusion fires before the income test"
            )
            checked += 1
    assert checked == 75


def test_the_agreement_check_rejects_the_federal_limit_regression() -> None:
    """The check above must actually FAIL when ground truth comes from the federal table.

    Without this, `test_expected_outcome_agrees_with_the_bbce_source` could be
    vacuously green -- e.g. if the two sources happened to agree on every case it
    ever looked at. Here a raised-BBCE jurisdiction is searched for a case where
    the federal and BBCE paths genuinely disagree, and the federal answer is
    asserted to be the one the check rejects.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state="CA")  # 200% FPL, assets waived
    divergent = []
    for seed in range(60):
        case = generator._build_homeless_case(random.Random(seed))
        bbce, _reason = bbce_outcome(case)
        federal = federal_outcome(case)
        if bbce != federal:
            divergent.append((case, bbce, federal))

    assert divergent, (
        "no CA homeless case in 60 seeds distinguishes the federal 130% FPL limit from "
        "CA's 200% FPL BBCE limit -- the regression test cannot prove the check has teeth"
    )

    for case, bbce, federal in divergent:
        # The live generator agrees with the BBCE source...
        assert case.expected_outcome == bbce, case.case_id
        # ...and the federal answer, which the old code would have stamped here, is
        # exactly what the agreement check rejects.
        assert case.expected_outcome != federal, case.case_id


def test_jurisdictions_without_a_raised_gross_limit_skip_the_bbce_expanded_case() -> None:
    """A jurisdiction with no band above 130% FPL generates the other six types.

    It must NOT borrow another jurisdiction's parameters to manufacture the
    seventh -- see `SNAPEligibilityGenerator.supports_bbce_expanded_income`.
    """
    non_bbce_or_flat = [
        code
        for code in all_jurisdictions()
        if not SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=code).supports_bbce_expanded_income
    ]
    # The FY2026 table's 7 non-adopters plus its 7 adopters that stayed at 130% FPL.
    assert len(non_bbce_or_flat) == 14, non_bbce_or_flat

    for code in non_bbce_or_flat:
        generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=code)
        cases = generator.generate(n=40, profile_strategy="edge_saturated", seed=20260810)
        assert not [c for c in cases if "bbce_expanded_gross_limit" in c.variation_tags], code
        assert len(generator._available_special_population_builders()) == 6, code
        with pytest.raises(ValueError, match="cannot support a BBCE expanded-gross-limit case"):
            generator._build_bbce_expanded_income_case(random.Random(0))


def test_asset_figure_in_the_scenario_is_the_one_the_determination_used() -> None:
    """The assets the prompt describes must be the assets the outcome was decided from.

    Three builders used to draw `liquid_assets` twice -- once for `is_eligible`,
    once for the `ScenarioBlock` a reader sees. Benign only while both draws
    happened to sit under the applicable cap. Asserted here by re-deciding each
    case from its own stated assets, which is exactly what
    `test_expected_outcome_agrees_with_the_bbce_source` does; this test pins the
    specific builders so a reintroduced second draw names itself.
    """
    for state in ("VA", "TX", "AL", "AR", "NE"):
        generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
        for seed in range(20):
            for builder in (
                generator._build_boarder_case,
                generator._build_migrant_case,
                generator._build_mixed_immigration_case,
            ):
                case = builder(random.Random(seed))
                outcome, reason = bbce_outcome(case)
                assert outcome == case.expected_outcome, (
                    f"{case.case_id}: outcome {case.expected_outcome!r} was not decided from the "
                    f"assets (${case.scenario.liquid_assets:,.2f}) the scenario states -- {reason}"
                )
