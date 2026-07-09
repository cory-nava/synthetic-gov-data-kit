"""Unit tests for MedicaidEligibilityGenerator."""

from govsynth.generators.medicaid_eligibility import MedicaidEligibilityGenerator


def test_program_is_medicaid() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    assert gen.program == "medicaid"


def test_generate_returns_requested_count() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    cases = gen.generate(n=10, seed=42)
    assert len(cases) == 10


def test_generate_is_deterministic_with_seed() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    cases1 = gen.generate(n=15, seed=42)
    cases2 = gen.generate(n=15, seed=42)
    assert [c.case_id for c in cases1] == [c.case_id for c in cases2]
    assert [c.expected_outcome for c in cases1] == [c.expected_outcome for c in cases2]


def test_all_generated_cases_pass_output_contract() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    cases = gen.generate(n=30, seed=7)
    for case in cases:
        assert case.is_valid(), case.check_output_contract()


def test_case_ids_follow_medicaid_state_schema() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    cases = gen.generate(n=10, seed=42)
    for case in cases:
        assert case.case_id.startswith("medicaid.va.eligibility.")


def test_non_expansion_state_produces_coverage_gap_cases() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="TX")
    cases = gen.generate(n=40, seed=42)
    descriptors = {tag for case in cases for tag in case.variation_tags}
    assert "non_expansion_coverage_gap" in descriptors
    gap_cases = [c for c in cases if "non_expansion_coverage_gap" in c.variation_tags]
    assert all(c.expected_outcome == "ineligible" for c in gap_cases)


def test_expansion_state_produces_both_outcomes_across_seeds() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    cases = gen.generate(n=60, seed=1)
    outcomes = {c.expected_outcome for c in cases}
    assert outcomes == {"eligible", "ineligible"}


def test_each_case_has_applicant_type_in_metadata() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    cases = gen.generate(n=10, seed=42)
    valid_types = {"adult", "pregnant", "child_0_18", "parent_caretaker"}
    for case in cases:
        assert case.metadata["applicant_type"] in valid_types


def test_rationale_trace_cites_42_cfr_435() -> None:
    gen = MedicaidEligibilityGenerator(calendar_year=2026, state="VA")
    cases = gen.generate(n=5, seed=42)
    for case in cases:
        citations = [str(c) for c in case.rationale_trace.policy_basis]
        assert any("42 CFR 435" in c for c in citations)
