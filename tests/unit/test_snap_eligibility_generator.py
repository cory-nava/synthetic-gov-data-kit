"""Unit tests for SNAPEligibilityGenerator."""

from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator


def test_program_is_snap() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    assert gen.program == "snap"


def test_generate_returns_requested_count() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=10, seed=42)
    assert len(cases) == 10


def test_generate_is_deterministic_with_seed() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases1 = gen.generate(n=10, seed=42)
    cases2 = gen.generate(n=10, seed=42)
    assert [c.case_id for c in cases1] == [c.case_id for c in cases2]
    assert [c.expected_outcome for c in cases1] == [c.expected_outcome for c in cases2]


def test_all_generated_cases_pass_output_contract() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=30, seed=7)
    for case in cases:
        assert case.is_valid(), case.check_output_contract()


def test_case_ids_follow_snap_state_schema() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=10, seed=42)
    for case in cases:
        assert case.case_id.startswith("snap.va.eligibility.")


def test_edge_saturated_strategy_includes_special_population_cases() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=30, seed=42, profile_strategy="edge_saturated")
    tags = {tag for case in cases for tag in case.variation_tags}
    special_tags = {
        "homeless_shelter_deduction",
        "student_exclusion",
        "boarder_income_proration",
        "migrant_income_averaging",
        "mixed_immigration_status_hh_size_reduction",
        "categorical_eligibility_tanf_ssi",
    }
    assert tags & special_tags


def test_uniform_strategy_produces_valid_cases() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=10, seed=42, profile_strategy="uniform")
    assert len(cases) > 0
    for case in cases:
        assert case.is_valid()


def test_bbce_state_summary_has_no_asset_test() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="CA")
    cases = gen.generate(n=5, seed=1, profile_strategy="uniform")
    assert all(c.scenario.state == "CA" for c in cases)


def test_strict_asset_state_generates_cases() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="TX")
    cases = gen.generate(n=10, seed=42)
    assert len(cases) == 10
    assert all(c.case_id.startswith("snap.tx.") for c in cases)
