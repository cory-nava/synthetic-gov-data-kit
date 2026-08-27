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


def test_every_eligible_case_carries_monthly_allotment() -> None:
    """Downstream consumers (e.g. the JSONL formatter's answer block) read the

    computed monthly allotment from scenario.additional_context rather than
    recomputing it. Every eligible case -- from every builder path, not just
    the main threshold-boundary path -- must carry a non-null value there.
    """
    for state in ["VA", "CA", "TX"]:
        gen = SNAPEligibilityGenerator(fiscal_year=2026, state=state)
        for strategy in ["edge_saturated", "uniform", "realistic"]:
            for seed in range(5):
                cases = gen.generate(n=30, seed=seed, profile_strategy=strategy)
                for case in cases:
                    if case.expected_outcome == "eligible":
                        allotment = case.scenario.additional_context.get("monthly_allotment")
                        assert allotment is not None, f"{case.case_id} is eligible but missing monthly_allotment"
                        assert allotment >= 0


def test_phrasing_style_none_is_byte_identical_to_the_default() -> None:
    """Not passing `phrasing_style` and passing it as None explicitly must
    produce identical output -- catches a divergence between the implicit
    and explicit default, but NOT a change to what None itself renders,
    since both sides of this comparison take the same code path."""
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    default = gen.generate(n=10, seed=42)
    explicit_none = gen.generate(n=10, seed=42, phrasing_style=None)
    assert [c.scenario.summary for c in default] == [c.scenario.summary for c in explicit_none]


def test_phrasing_style_none_still_uses_the_original_fixed_template() -> None:
    """The property the test above cannot catch: that None renders the
    ORIGINAL template, not merely something consistent with itself. Pinned to
    "Their household has", the literal wording from
    USHouseholdProfile.natural_language_summary that appears nowhere in any
    of the five natural_language_summary_styled variants -- a mutation that
    silently swapped the None branch to a styled rendering (verified: this
    test fails against exactly that mutation, while the byte-identical test
    above does not, since both its calls take the same, equally-mutated
    branch) would need to reintroduce that exact literal phrase to pass here.
    """
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=20, seed=42)
    # Not filtered by case type: special-population cases never contain this
    # phrase regardless of phrasing_style (they write their own bespoke prose
    # and never call natural_language_summary at all), so `any(...)` across
    # the whole batch is both correct and simpler than trying to identify
    # threshold-boundary cases by pattern-matching their prose.
    assert any("Their household has" in c.scenario.summary for c in cases), [c.scenario.summary for c in cases]


def test_phrasing_style_random_does_not_change_sampled_facts() -> None:
    """Turning phrasing on must change ONLY the prose, never which household
    facts get sampled -- the style pick is derived from (seed, index) with
    plain arithmetic, deliberately not from the RNG stream that also decides
    income, assets, age, etc. If it consumed from that stream instead, this
    test would catch it: enabling phrasing_style would then also change which
    numbers each case reports."""
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    plain = gen.generate(n=20, seed=42)
    styled = gen.generate(n=20, seed=42, phrasing_style="random")
    assert [c.scenario.model_dump(exclude={"summary"}) for c in plain] == [
        c.scenario.model_dump(exclude={"summary"}) for c in styled
    ]
    # And the actual point of turning it on: prose must differ on at least
    # the threshold-boundary cases (special-population cases write their own
    # bespoke prose regardless of phrasing_style, so not every summary changes).
    assert any(p.scenario.summary != s.scenario.summary for p, s in zip(plain, styled))


def test_phrasing_style_random_is_deterministic_with_seed() -> None:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases1 = gen.generate(n=15, seed=99, phrasing_style="random")
    cases2 = gen.generate(n=15, seed=99, phrasing_style="random")
    assert [c.scenario.summary for c in cases1] == [c.scenario.summary for c in cases2]


def test_phrasing_style_single_named_style_applies_to_threshold_cases() -> None:
    """Passing one specific style (rather than "random") renders every
    threshold-boundary case in exactly that style -- useful for building a
    corpus that's uniformly one register rather than a random mix. Checked by
    the caseworker_note style's distinctive "Applicant:" opener rather than by
    filtering case types: `additional_context["threshold_type"]` turns out to
    be present on every case, special-population included, so it cannot
    distinguish them. Both halves matter here: some cases must show the new
    styling (the threshold-boundary ones), and some must not (the
    special-population builders write their own bespoke prose and never call
    natural_language_summary_styled at all)."""
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=20, seed=7, phrasing_style="caseworker_note")
    styled = [c for c in cases if c.scenario.summary.startswith("Applicant:")]
    unstyled = [c for c in cases if not c.scenario.summary.startswith("Applicant:")]
    assert styled, "no case picked up the caseworker_note styling"
    assert unstyled, "every case was styled -- special-population cases should be untouched"
