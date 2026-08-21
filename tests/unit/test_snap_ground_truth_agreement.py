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

That covers only half of what a training target has to get right, because
`is_eligible` resolves its own limit internally. A builder -- or `_gross_limit`
itself -- drifting back to the federal table leaves the OUTCOME self-consistent
while the rationale states a limit the prompt never showed. Mutating
`_gross_limit` to return `federal_gross_limit` left all 783 tests across both
repos green while the generator emitted, for MD (200% FPL, prompt states
$4,442):

    step1: $2,919.70 > $2,888.00 (200% FPL BBCE limit, MD, 3-person HH) -> FAIL
           determinative: True
    expected_outcome: eligible
    conclusion: ELIGIBLE. Eligible under MD BBCE (200% FPL gross limit, ...)

A determinative FAIL step, a basis label naming a percentage the number does not
come from, a limit absent from the prompt, and an ELIGIBLE conclusion -- in one
training target. So two further invariants, asserted per case:

    every `step.inputs["gross_limit"]` equals
    `bbce_source.effective_gross_limit(size)` for the size the case's own limit
    lookup used, and the step's prose states both that number and the percentage
    it was derived from (`federal_gross_limit` for the deliberate
    federal-vs-BBCE contrast inside `_build_bbce_expanded_income_case`);

    no determinative FAIL step sits under an `eligible` outcome, and no
    `ineligible` outcome rests on a chain whose determinative steps all pass.

Two things this file is careful about:

- The financial facts are read off the CASE, not recomputed. `household_size` for
  the limit lookup comes from `additional_context["eligible_member_count"]` when
  present (the mixed-immigration builder tests full income against a reduced
  household size, per 7 CFR 273.11(c)(3)) and from `scenario.household_size`
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
from govsynth.models.rationale import ReasoningStep
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
        # 7 CFR 273.11(c)(3): an ineligible alien leaves the household size used for
        # the limit lookup. Whether their income is counted in full or less a pro
        # rata share is a state election under (c)(3)(i); the builder generates the
        # count-all election, which is why full income is the right basis here.
        # NOT 273.4(c), which is sponsor deeming -- a different mechanism.
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


def is_gross_test_waived(case: TestCase) -> bool:
    """Whether 7 CFR 273.9(a)(1) actually waives this case's gross income test.

    `scenario.has_elderly_or_disabled` alone is too wide a carve-out: SSI/TANF
    recipients are elderly-or-disabled by construction too --
    `_build_categorical_eligibility_case` hardcodes `has_elderly_or_disabled=True`
    because SSI recipients are elderly or disabled -- but that case's gross test
    is SKIPPED by categorical eligibility (7 CFR 273.2(j)(2)), not WAIVED under
    273.9(a)(1), and it still states a `gross_limit` in its step 2 inputs.
    Keying the carve-out on the scenario flag alone meant that if that builder
    ever stopped stating its limit, the wide predicate would wave the case
    through as if it were gross-test-waived, with nothing else in this module
    positioned to notice. Require the case type that IS the waiver
    (`asset_limit_elderly_disabled`), or a step whose result actually renders
    "WAIVED" -- every legitimate waived case renders one.
    """
    if case_type(case) == "asset_limit_elderly_disabled":
        return True
    return any(step.result == "WAIVED" for step in case.rationale_trace.steps)


# ----------------------------------------------------------------------
# The stated gross limit must be the governing one
# ----------------------------------------------------------------------

# Step-input keys that state a monthly gross-income limit, and which method on the
# jurisdiction's own BBCE source must have produced each. `federal_130pct_limit` is
# the deliberate federal-vs-BBCE contrast inside `_build_bbce_expanded_income_case`
# -- that case's whole premise is a household between the two limits, so it states
# both, and the federal one being genuinely federal is part of what it must get right.
_STATED_LIMIT_KEYS: tuple[tuple[str, str], ...] = (
    ("gross_limit", "effective"),
    ("bbce_limit", "effective"),
    ("federal_130pct_limit", "federal"),
)

# Same, for `scenario.additional_context` -- these two are rendered into the prompt a
# consumer builds, so a wrong value there is wrong in the question as well as the answer.
_STATED_CONTEXT_LIMIT_KEYS: tuple[tuple[str, str], ...] = (
    ("bbce_gross_limit", "effective"),
    ("federal_130pct_limit", "federal"),
)

_CENTS = 0.005


def stated_limit_household_size(case: TestCase) -> int:
    """The household size this case's own gross-limit lookup used.

    Same rule as `financial_facts`: `eligible_member_count` when the builder tested
    full income against a reduced household size (7 CFR 273.11(c)(3)), else the
    scenario's household size.
    """
    context = case.scenario.additional_context or {}
    return int(context.get("eligible_member_count", case.scenario.household_size))


def gross_limit_problems(case: TestCase, fiscal_year: int = FISCAL_YEAR) -> list[str]:
    """Every way `case` states a gross limit that is not the governing one.

    Checks the VALUE (against the jurisdiction's own source) and, separately, the
    BASIS LABEL in the prose, because the two can drift independently:
    `_gross_limit` and `_gross_basis` are different methods, so a limit can be
    right while the percentage the prose attributes it to is wrong, or vice versa.
    A step that states a limit must therefore also state that number in its own
    text and name the percentage the number actually comes from -- and, unless the
    jurisdiction really is at 130% FPL, must not claim "130% FPL" anywhere in that
    step. (The BBCE expanded-income case's step 2 names the federal 130% limit
    deliberately; it carries `bbce_limit`/`federal_130pct_limit`, not
    `gross_limit`, so the label rule does not apply to it.)

    Returned as a list rather than asserted inline so the mutation-reproduction
    test below can feed this function a deliberately corrupted case.
    """
    source = SNAPBBCESource(fiscal_year=fiscal_year, state=case.scenario.state)
    size = stated_limit_household_size(case)
    expected = {
        "effective": source.effective_gross_limit(size),
        "federal": source.federal_gross_limit(size),
    }
    pct = source.bbce_params.gross_income_limit_pct_fpl
    problems: list[str] = []

    for step in case.rationale_trace.steps:
        inputs = step.inputs or {}
        for key, basis in _STATED_LIMIT_KEYS:
            if key not in inputs:
                continue
            stated = float(inputs[key])
            if abs(stated - expected[basis]) > _CENTS:
                problems.append(
                    f"{case.case_id}: step {step.step_number} states {key}=${stated:,.2f} but the "
                    f"{basis} limit for a {size}-person household in {case.scenario.state} is "
                    f"${expected[basis]:,.2f}"
                )
        if "gross_limit" not in inputs:
            continue
        governing = expected["effective"]
        text = " ".join(part for part in (step.computation, step.result, step.note) if part)
        if f"${governing:,.2f}" not in text:
            problems.append(
                f"{case.case_id}: step {step.step_number} is judged against ${governing:,.2f} but "
                f"never states that number, so a reader cannot check it: {text[:160]!r}"
            )
        if f"{pct}% FPL" not in text:
            problems.append(
                f"{case.case_id}: step {step.step_number} states a ${governing:,.2f} limit without "
                f"naming the {pct}% FPL basis it comes from: {text[:160]!r}"
            )
        if pct != 130 and "130% FPL" in text:
            problems.append(
                f"{case.case_id}: step {step.step_number} attributes its limit to '130% FPL' in "
                f"{case.scenario.state}, whose governing basis is {pct}% FPL: {text[:160]!r}"
            )

    context = case.scenario.additional_context or {}
    for key, basis in _STATED_CONTEXT_LIMIT_KEYS:
        if key not in context:
            continue
        stated = float(context[key])
        if abs(stated - expected[basis]) > _CENTS:
            problems.append(
                f"{case.case_id}: additional_context[{key!r}]=${stated:,.2f} but the {basis} limit "
                f"for a {size}-person household in {case.scenario.state} is ${expected[basis]:,.2f}"
            )
    return problems


# ----------------------------------------------------------------------
# A determinative step must point the same way as the outcome
# ----------------------------------------------------------------------

_FAILING_RESULT_PREFIXES = ("FAIL", "INELIGIBLE")

# Limit key -> the input keys a builder may compare against it. Needed because a
# step can fail on its own numbers while its `result` string says nothing about
# failing: `_build_boarder_case`'s gross step renders "Total gross income: $X vs
# limit $Y", never the word FAIL, and is determinative exactly when X > Y.
_LIMIT_TO_VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "gross_limit": ("gross_income", "countable_income", "averaged_monthly"),
    "bbce_limit": ("gross_income",),
    "net_limit": ("net_income",),
    "asset_limit": ("liquid_assets",),
}


def step_states_a_failure(step: ReasoningStep) -> bool:
    """Whether `step` says, in words or in its own numbers, that a test was failed."""
    if step.result.strip().upper().startswith(_FAILING_RESULT_PREFIXES):
        return True
    inputs = step.inputs or {}
    for limit_key, value_keys in _LIMIT_TO_VALUE_KEYS.items():
        limit = inputs.get(limit_key)
        if limit is None:
            continue
        for value_key in value_keys:
            value = inputs.get(value_key)
            if value is not None and float(value) > float(limit) + _CENTS:
                return True
    return False


def determinative_problems(case: TestCase) -> list[str]:
    """Determinative steps that contradict the case's own outcome.

    Two directions, both of which produce a self-contradictory training target:

    - a determinative FAIL under an `eligible` outcome -- the MD example in this
      module's docstring, where the chain a model is trained to reproduce denies
      the household and the conclusion grants it;
    - an `ineligible` outcome whose determinative steps all pass -- the denial then
      rests on something the visible chain never shows, so the target teaches a
      conclusion its own reasoning does not support.
    """
    failures = [step for step in case.rationale_trace.steps if step.is_determinative and step_states_a_failure(step)]
    if case.expected_outcome == "eligible" and failures:
        return [
            f"{case.case_id}: outcome is 'eligible' but step {step.step_number} "
            f"({step.title!r}) is determinative and FAILS -- {step.computation[:140]!r}"
            for step in failures
        ]
    if case.expected_outcome == "ineligible" and not failures:
        determinative = [s.step_number for s in case.rationale_trace.steps if s.is_determinative]
        return [
            f"{case.case_id}: outcome is 'ineligible' but no determinative step states a failure "
            f"(determinative steps: {determinative or 'none'}) -- the denial rests on something "
            "the rationale never shows"
        ]
    return []


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
def test_every_stated_gross_limit_is_the_governing_one(state: str) -> None:
    """The limit a case reasons FROM must be the limit its jurisdiction actually applies.

    `test_expected_outcome_agrees_with_the_bbce_source` cannot catch this on its
    own: `is_eligible` resolves its own limit, so a builder reasoning against the
    federal table still produces an outcome that agrees with the source whenever
    the two limits happen to land on the same side of the household's income. What
    ships in that case is a training target whose stated arithmetic is wrong and
    whose stated basis names a percentage the number did not come from -- see this
    module's docstring for the measured MD example, which left 783 tests green.

    Every case type is in scope here, `student_exclusion` included: it is exempt
    from the outcome equality (a non-financial denial) but it still quotes a gross
    limit in its prose, and quoting the wrong one is the same defect.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    cases = generator.generate(n=40, profile_strategy="edge_saturated", seed=20260810)

    problems = [problem for case in cases for problem in gross_limit_problems(case)]
    assert not problems, (
        f"{len(problems)} stated-limit defects across {len(cases)} {state} cases -- each is a "
        "training target whose arithmetic is computed against a limit the prompt never showed, "
        "or whose basis label names a percentage the number does not come from:\n  " + "\n  ".join(problems[:10])
    )


@pytest.mark.parametrize("state", all_jurisdictions())
def test_no_determinative_step_contradicts_its_own_outcome(state: str) -> None:
    """A determinative FAIL under `eligible` (or an all-PASS chain under `ineligible`).

    The second half of the same MD example: the mutated generator emitted a case
    whose step 1 was a determinative FAIL and whose conclusion was ELIGIBLE. Both
    halves of that record are wrong and nothing asserted otherwise, because the
    outcome still agreed with the source that produced it.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    cases = generator.generate(n=40, profile_strategy="edge_saturated", seed=20260810)

    problems = [problem for case in cases for problem in determinative_problems(case)]
    assert not problems, (
        f"{len(problems)} of {len(cases)} {state} cases carry a rationale that contradicts their "
        "own outcome:\n  " + "\n  ".join(problems[:10])
    )


@pytest.mark.parametrize("state", ("MD", "AL", "TX", "GU", "AK", "VI"))
def test_only_gross_test_waived_cases_may_state_no_gross_limit(state: str) -> None:
    """A case that states no limit is invisible to the check above -- so that must be rare and named.

    `test_every_stated_gross_limit_is_the_governing_one` can only check limits a
    case actually states. A builder that stopped putting its limit in
    `step.inputs` would silently leave the gate, exactly the way six builders
    silently left the BBCE source. The only legitimate reason to state no gross
    limit is that the gross income test does not apply: 7 CFR 273.9(a)(1) waives
    it for a household with an elderly or disabled member, and those cases render
    a "WAIVED" step with no limit in it. Note that "elderly or disabled" is NOT,
    by itself, the admissible-exemption set: `categorical_eligibility_tanf_ssi`
    cases are elderly-or-disabled too (SSI recipients qualify) but are exempted
    from the income test by categorical eligibility, not by the 273.9(a)(1)
    waiver, and they still state a `gross_limit` -- see `is_gross_test_waived`.

    Those cases only arise in a jurisdiction that keeps a dollar asset cap:
    `_sample_edge_profile` drops both asset thresholds where the BBCE asset test is
    waived, and the elderly/disabled profile comes from the
    `asset_limit_elderly_disabled` threshold. So the "this test is not vacuous"
    half is asserted only for the jurisdictions that can produce the shape at all.
    """
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    cases = generator.generate(n=200, profile_strategy="edge_saturated", seed=1)

    unchecked = [
        case
        for case in cases
        if not any(
            key in (step.inputs or {}) for step in case.rationale_trace.steps for key, _basis in _STATED_LIMIT_KEYS
        )
    ]
    not_waived = [c.case_id for c in unchecked if not is_gross_test_waived(c)]
    assert not not_waived, (
        f"{len(not_waived)} of {len(cases)} {state} cases state no gross limit anywhere in their "
        "rationale inputs and are not gross-test-waived, so nothing checks which limit they "
        f"reasoned from: {not_waived[:5]}"
    )
    keeps_an_asset_cap = generator.bbce_source.bbce_params.asset_limit is not None
    if keeps_an_asset_cap:
        assert unchecked, (
            f"no {state} case is gross-test-waived at all, though {state} keeps a dollar asset cap "
            "and so should sample elderly/disabled profiles -- this test would be vacuous there"
        )


def test_the_stated_limit_check_rejects_the_federal_limit_regression() -> None:
    """`gross_limit_problems` must actually fire when a limit drifts to the federal table.

    Reproduces, on a real case, the exact mutation that left 783 tests green:
    `_gross_limit` returning `federal_gross_limit`. Rather than monkeypatching the
    generator (which would also change the outcome, and so be caught by the
    equality test instead), the corruption is applied to the shipped artefact --
    the step input and the prose figure -- which is precisely the state a
    same-outcome drift leaves behind, and the one nothing used to check.
    """
    state = "MD"  # 200% FPL: the federal and governing limits differ at every size
    source = SNAPBBCESource(fiscal_year=FISCAL_YEAR, state=state)
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)

    corrupted = 0
    for seed in range(10):
        case = generator._build_homeless_case(random.Random(seed))
        assert not gross_limit_problems(case), case.case_id

        size = stated_limit_household_size(case)
        federal = source.federal_gross_limit(size)
        governing = source.effective_gross_limit(size)
        assert federal < governing, f"{state} size {size}: federal and BBCE limits must differ here"

        step = case.rationale_trace.steps[0]
        step.inputs["gross_limit"] = federal
        step.computation = step.computation.replace(f"${governing:,.2f}", f"${federal:,.2f}")
        problems = gross_limit_problems(case)
        assert problems, (
            f"{case.case_id}: gross_limit was swapped for the federal ${federal:,.2f} and the "
            "stated-limit check did not notice -- it has no teeth"
        )
        assert any("federal" not in p and "states gross_limit" in p for p in problems), problems
        corrupted += 1
    assert corrupted == 10


def test_the_stated_limit_check_rejects_a_basis_label_that_drifts_on_its_own() -> None:
    """The VALUE and the LABEL are produced by different methods and can drift apart.

    `_gross_limit` and `_gross_basis` are separate: a mutation to the label alone
    leaves every limit value correct and every outcome correct, and mislabels the
    basis in every raised-BBCE jurisdiction -- which is what the pre-fix generator
    actually did (it hardcoded "130% FPL" in four builders). This asserts the
    label half of the check independently of the value half.
    """
    state = "MD"
    source = SNAPBBCESource(fiscal_year=FISCAL_YEAR, state=state)
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state=state)
    assert source.bbce_params.gross_income_limit_pct_fpl == 200

    case = generator._build_migrant_case(random.Random(0))
    assert not gross_limit_problems(case), case.case_id

    step = next(s for s in case.rationale_trace.steps if "gross_limit" in (s.inputs or {}))
    step.computation = step.computation.replace(f"200% FPL BBCE limit, {state}", f"130% FPL, {state}")
    problems = gross_limit_problems(case)
    assert problems, (
        f"{case.case_id}: the basis label was relabelled to 130% FPL while the limit stayed at "
        f"MD's 200% FPL figure, and the check did not notice"
    )
    assert any("130% FPL" in p for p in problems), problems


def test_the_determinative_check_rejects_a_fail_under_an_eligible_outcome() -> None:
    """`determinative_problems` must fire on the MD shape from the module docstring."""
    generator = SNAPEligibilityGenerator(fiscal_year=FISCAL_YEAR, state="MD")
    eligible = [c for c in (generator._build_homeless_case(random.Random(s)) for s in range(30))]
    case = next(c for c in eligible if c.expected_outcome == "eligible")
    assert not determinative_problems(case), case.case_id

    step = case.rationale_trace.steps[0]  # the gross-income step
    step.result = "FAIL"
    step.is_determinative = True
    problems = determinative_problems(case)
    assert problems, f"{case.case_id}: a determinative FAIL under an ELIGIBLE outcome went unnoticed"
    assert "determinative" in problems[0]

    ineligible = next(c for c in eligible if c.expected_outcome == "ineligible")
    assert not determinative_problems(ineligible), ineligible.case_id
    for step in ineligible.rationale_trace.steps:
        step.is_determinative = False
    assert determinative_problems(ineligible), (
        f"{ineligible.case_id}: an INELIGIBLE outcome with no determinative failure anywhere in "
        "its chain went unnoticed"
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
    """A jurisdiction with no band above 130% FPL generates the other seven types.

    It must NOT borrow another jurisdiction's parameters to manufacture the
    eighth -- see `SNAPEligibilityGenerator.supports_bbce_expanded_income`.
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
        assert len(generator._available_special_population_builders()) == 7, code
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
