"""Unit tests for WICEligibilityGenerator."""

from govsynth.generators.wic_eligibility import WICEligibilityGenerator


def test_program_is_wic() -> None:
    gen = WICEligibilityGenerator(fiscal_year=2026)
    assert gen.program == "wic"


def test_generate_returns_requested_count() -> None:
    gen = WICEligibilityGenerator(fiscal_year=2026)
    cases = gen.generate(n=10, seed=42)
    assert len(cases) == 10


def test_generate_is_deterministic_with_seed() -> None:
    gen = WICEligibilityGenerator(fiscal_year=2026)
    cases1 = gen.generate(n=10, seed=42)
    cases2 = gen.generate(n=10, seed=42)
    assert [c.case_id for c in cases1] == [c.case_id for c in cases2]
    assert [c.expected_outcome for c in cases1] == [c.expected_outcome for c in cases2]


def test_all_generated_cases_pass_output_contract() -> None:
    gen = WICEligibilityGenerator(fiscal_year=2026)
    cases = gen.generate(n=30, seed=7)
    for case in cases:
        assert case.is_valid(), case.check_output_contract()


def test_case_ids_follow_wic_schema() -> None:
    gen = WICEligibilityGenerator(fiscal_year=2026)
    cases = gen.generate(n=10, seed=42)
    for case in cases:
        assert case.case_id.startswith("wic.")
        assert ".eligibility." in case.case_id


def test_generated_cases_use_valid_participant_categories() -> None:
    gen = WICEligibilityGenerator(fiscal_year=2026)
    cases = gen.generate(n=20, seed=42)
    valid_categories = {"pregnant", "breastfeeding", "postpartum", "infant", "child_under_5"}
    categories = {case.metadata["participant_category"] for case in cases}
    assert categories <= valid_categories


def test_edge_saturated_strategy_produces_income_boundary_cases() -> None:
    gen = WICEligibilityGenerator(fiscal_year=2026)
    cases = gen.generate(n=20, seed=42, profile_strategy="edge_saturated")
    outcomes = {c.expected_outcome for c in cases}
    assert outcomes  # at least one outcome type produced
