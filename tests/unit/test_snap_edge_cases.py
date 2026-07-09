import random

from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator


def test_homeless_shelter_deduction() -> None:
    """Homeless case uses flat $198.99 deduction, not excess shelter calculation."""
    gen = SNAPEligibilityGenerator(state="VA")
    rng = random.Random(42)
    case = gen._build_homeless_case(rng)

    assert "homeless_shelter_deduction" in case.variation_tags
    assert case.is_valid()
    # Rationale must cite 7 CFR 273.9(c)(6)
    rationale_text = " ".join(step.rule_applied for step in case.rationale_trace.steps)
    assert "273.9(c)(6)" in rationale_text
    # Must NOT cite (c)(5) as the homeless deduction rule
    # (c)(5) is the excess shelter deduction which is replaced, not used)
    for step in case.rationale_trace.steps:
        if "homeless" in (step.computation or "").lower():
            assert "273.9(c)(6)" in step.rule_applied


def test_student_exclusion() -> None:
    """Student enrolled half-time is INELIGIBLE regardless of income level."""
    gen = SNAPEligibilityGenerator(state="VA")
    rng = random.Random(42)
    case = gen._build_student_case(rng)

    assert "student_exclusion" in case.variation_tags
    assert case.expected_outcome == "ineligible"
    assert case.is_valid()
    # Rationale must mention checking exceptions under 273.5(b)
    " ".join((step.computation or "") + " " + (step.note or "") for step in case.rationale_trace.steps)
    assert "273.5" in " ".join(step.rule_applied for step in case.rationale_trace.steps)
    # Income test must NOT be the determinative step — student check fires first
    determinative_steps = [s for s in case.rationale_trace.steps if s.is_determinative]
    assert len(determinative_steps) >= 1
    # First determinative step should be student check, not income limit check
    first_det = determinative_steps[0]
    assert "273.5" in first_det.rule_applied


def test_boarder_income_proration() -> None:
    """Only profit portion of board payments counts as income."""
    gen = SNAPEligibilityGenerator(state="VA")
    rng = random.Random(42)
    case = gen._build_boarder_case(rng)

    assert "boarder_income_proration" in case.variation_tags
    assert case.is_valid()
    # Profile context should show boarder flag
    ctx = case.scenario.additional_context
    assert ctx.get("is_boarder") is True or ctx.get("boarder_income") is not None


def test_migrant_income_averaging() -> None:
    """Seasonal income is averaged over work period, not current-month snapshot."""
    gen = SNAPEligibilityGenerator(state="VA")
    rng = random.Random(42)
    case = gen._build_migrant_case(rng)

    assert "migrant_income_averaging" in case.variation_tags
    assert case.is_valid()
    ctx = case.scenario.additional_context
    assert ctx.get("is_migrant_worker") is True or ctx.get("seasonal_total") is not None


def test_mixed_immigration_status_hh_size_reduction() -> None:
    """Ineligible members excluded from HH size for limit lookup; income counts in full."""
    gen = SNAPEligibilityGenerator(state="VA")
    rng = random.Random(42)
    case = gen._build_mixed_immigration_case(rng)

    assert "mixed_immigration_status_hh_size_reduction" in case.variation_tags
    assert case.is_valid()
    # Rationale must show HH size reduction (not income proration)
    full_rationale = " ".join(
        (step.computation or "") + " " + (step.note or "") for step in case.rationale_trace.steps
    ).lower()
    # Should mention size reduction
    assert "size" in full_rationale or "household size" in full_rationale
    # Should NOT say income is prorated (that's the sponsored noncitizen rule, not this one)
    assert "prorate" not in full_rationale or "income" not in full_rationale


def test_categorical_eligibility_tanf_ssi() -> None:
    """TANF/SSI recipient is ELIGIBLE even if income exceeds normal limits."""
    gen = SNAPEligibilityGenerator(state="VA")
    rng = random.Random(42)
    case = gen._build_categorical_eligibility_case(rng)

    assert "categorical_eligibility_tanf_ssi" in case.variation_tags
    assert case.expected_outcome == "eligible"
    assert case.is_valid()
    # Rationale must show income test was skipped
    full_rationale = " ".join(
        (step.computation or "") + " " + (step.note or "") for step in case.rationale_trace.steps
    ).lower()
    # Should mention categorical eligibility or income test skipped
    assert "categor" in full_rationale or "tanf" in full_rationale or "ssi" in full_rationale
    ctx = case.scenario.additional_context
    assert ctx.get("tanf_or_ssi_recipient") is True
