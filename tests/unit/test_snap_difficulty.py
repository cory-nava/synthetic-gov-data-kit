"""Difficulty labelling must be honest: every level producible, edge cases adversarial."""

import collections

import pytest
from govsynth.generators.snap_eligibility import _OFFSETS, SNAPEligibilityGenerator
from govsynth.models.enums import Difficulty

# EDGE_CASES.md Group A. These exist because models misapply them, which is
# what ADVERSARIAL means in this kit.
EDGE_CASE_BUILDERS = [
    "_build_homeless_case",
    "_build_student_case",
    "_build_boarder_case",
    "_build_migrant_case",
    "_build_mixed_immigration_case",
    "_build_categorical_eligibility_case",
    "_build_bbce_expanded_income_case",
]


@pytest.mark.parametrize("builder_name", EDGE_CASE_BUILDERS)
def test_each_edge_case_builder_emits_adversarial(builder_name):
    import random

    generator = SNAPEligibilityGenerator(state="VA")
    case = getattr(generator, builder_name)(random.Random(0))
    assert case.difficulty == Difficulty.ADVERSARIAL, (
        f"{builder_name} is an EDGE_CASES.md Group A case -- it exists because "
        f"models misapply it -- but emits {case.difficulty}"
    )


def test_adversarial_cases_actually_appear_in_generated_output():
    generator = SNAPEligibilityGenerator(state="VA")
    cases = generator.generate(n=60, profile_strategy="edge_saturated", seed=7)
    counts = collections.Counter(c.difficulty for c in cases)
    assert counts[Difficulty.ADVERSARIAL] > 0, (
        f"no adversarial cases in 60 generated: {dict(counts)}"
    )


def test_easy_is_reachable_from_the_offset_set() -> None:
    # _classify_difficulty returns EASY only when abs(offset) > 0.30.
    assert any(abs(o) > 0.30 for o in _OFFSETS), (
        f"no offset exceeds 0.30, so Difficulty.EASY is unreachable: {_OFFSETS}"
    )


@pytest.mark.parametrize("state", ["VA", "KS"])  # BBCE and non-BBCE
def test_every_difficulty_level_is_producible(state: str) -> None:
    """The test that would have caught all three bugs at once."""
    generator = SNAPEligibilityGenerator(state=state)
    cases = generator.generate(n=200, profile_strategy="edge_saturated", seed=11)
    produced = {c.difficulty for c in cases}
    missing = set(Difficulty) - produced
    assert not missing, f"{state}: these Difficulty levels are never produced: {missing}"


@pytest.mark.parametrize("strategy", ["uniform", "realistic"])
def test_threshold_free_profiles_are_not_labelled_easy(strategy: str) -> None:
    """EASY asserts distance from a limit. Profiles sampled without an offset
    have no known distance, so the label would be a fabrication."""
    cases = SNAPEligibilityGenerator(state="VA").generate(
        n=100, profile_strategy=strategy, seed=3
    )
    assert Difficulty.EASY not in {c.difficulty for c in cases}


def test_elderly_disabled_households_are_never_easy_however_far_from_a_limit() -> None:
    """has_elderly_or_disabled changes four computations -- gross test waived,
    medical deduction, uncapped excess shelter, different asset cap -- so these
    cases are never EASY regardless of offset magnitude.
    """
    generator = SNAPEligibilityGenerator(state="KS")  # non-BBCE: asset-threshold
    cases = generator.generate(n=200, profile_strategy="edge_saturated", seed=11)
    elderly = [c for c in cases if c.scenario.has_elderly_or_disabled]
    assert elderly, "fixture assumption broken: no elderly/disabled cases generated"
    assert all(c.difficulty != Difficulty.EASY for c in elderly)


def test_generator_does_not_accept_a_distribution_it_cannot_honour():
    """difficulty_distribution was accepted, documented, and never read.

    Difficulty is derived from the profile, not requested. Accepting the
    argument implied a guarantee the generator never provided.
    """
    with pytest.raises(TypeError):
        SNAPEligibilityGenerator(state="VA", difficulty_distribution={"easy": 1.0})


def test_difficulty_is_documented_as_derived():
    doc = SNAPEligibilityGenerator.__init__.__doc__ or SNAPEligibilityGenerator.__doc__ or ""
    assert "derived" in doc.lower() or "emergent" in doc.lower(), (
        "the constructor docs should say difficulty is derived from the profile, "
        "not requested by the caller"
    )


def test_constructor_docstring_documents_exactly_its_parameters():
    """A docstring naming a parameter the constructor does not accept is the
    same defect as a label naming data it does not contain.
    """
    import inspect

    signature = inspect.signature(SNAPEligibilityGenerator.__init__)
    actual = {p for p in signature.parameters if p != "self"}

    doc = SNAPEligibilityGenerator.__doc__ or ""
    args_block = doc.split("Args:", 1)[1] if "Args:" in doc else ""
    documented = {
        line.strip().split(":", 1)[0].strip()
        for line in args_block.splitlines()
        if line.strip() and ":" in line and line.startswith(" " * 8)
    }
    documented = {d for d in documented if d.isidentifier()}

    assert documented == actual, (
        f"documented but absent: {documented - actual}; "
        f"present but undocumented: {actual - documented}"
    )


def test_a_failing_edge_case_builder_is_not_silently_swallowed(monkeypatch):
    import random

    generator = SNAPEligibilityGenerator(state="VA")

    def boom(_rng):
        raise ValueError("builder exploded")

    monkeypatch.setattr(generator, "_build_homeless_case", boom)

    with pytest.raises(RuntimeError, match="homeless"):
        generator._build_special_population_cases(7, random.Random(0))


def test_builder_names_match_their_callables():
    """The builders list carries names as strings parallel to the methods.

    A rename would leave the string stale and nothing else would notice.
    """
    generator = SNAPEligibilityGenerator(state="VA")
    # Reach the list without invoking any builder.
    for name, builder in generator._special_population_builders():
        assert name == builder.__name__


def test_random_profile_case_failure_is_not_silently_swallowed(monkeypatch):
    """generate() with a non-edge-saturated strategy calls _build_case in a
    loop. A failure there must raise, not be print-and-skipped -- a caller
    reducing the requested n silently is exactly the defect this branch
    exists to eliminate.
    """
    generator = SNAPEligibilityGenerator(state="VA")

    def boom(profile, seed, index):
        raise ValueError("case builder exploded")

    monkeypatch.setattr(generator, "_build_case", boom)

    with pytest.raises(RuntimeError, match="random-profile"):
        generator.generate(n=3, profile_strategy="uniform", seed=1)


def test_edge_saturated_regular_case_failure_is_not_silently_swallowed(monkeypatch):
    """generate() with profile_strategy='edge_saturated' builds special-
    population cases first, then regular edge-boundary cases via _build_case.
    A failure in that second loop must raise too, with a message that says
    'edge-saturated' rather than 'random-profile' so the two paths stay
    distinguishable in a traceback.
    """
    generator = SNAPEligibilityGenerator(state="VA")

    def boom(profile, seed, index):
        raise ValueError("case builder exploded")

    monkeypatch.setattr(generator, "_build_case", boom)

    # n=10 with the 20%-special-case split forces n_special=7, n_edge=3, so
    # the regular edge-case loop (which calls _build_case) is reached.
    with pytest.raises(RuntimeError, match="edge-saturated"):
        generator.generate(n=10, profile_strategy="edge_saturated", seed=1)


def test_uniform_strategy_case_ids_never_claim_at_limit():
    """offset_pct is absent (extra == {}) for 'uniform'/'realistic' profiles,
    so there is no known distance from a threshold. _make_case_id must not
    default the missing offset to 0.0 and stamp 'at_limit' into the ID --
    that would assert something the profile does not know, contradicting
    _build_variation_tags, which correctly emits no offset tag at all.
    """
    generator = SNAPEligibilityGenerator(state="VA")
    cases = generator.generate(n=50, profile_strategy="uniform", seed=5)
    offending = [c.case_id for c in cases if "at_limit" in c.case_id]
    assert not offending, (
        f"uniform-strategy case IDs fabricate 'at_limit' despite no known offset: {offending}"
    )


@pytest.mark.parametrize("offset", [0.01, -0.01])
def test_offset_at_one_percent_boundary_is_hard(offset: float) -> None:
    """HARD's defining boundary is abs(offset) <= 0.01. A ±0.01 case must
    still be HARD; tightening the comparison to '<' would silently exclude
    the exact values the boundary is named after.
    """
    from govsynth.profiles.us_household import USHouseholdProfile

    generator = SNAPEligibilityGenerator(state="VA")
    profile = USHouseholdProfile(
        household_size=3,
        monthly_gross_income=2000.0,
        state="VA",
        extra={"threshold_type": "gross_income_limit", "offset_pct": offset},
    )
    assert generator._classify_difficulty(profile, is_eligible=True) == Difficulty.HARD


@pytest.mark.parametrize(
    "offset,expected_tag",
    [
        (0.35, "well_above_limit"),
        (-0.35, "well_below_limit"),
    ],
)
def test_offset_tag_buckets_far_from_limit_separately(offset: float, expected_tag: str) -> None:
    """_offset_tag must distinguish a household clearly clear of a limit
    (e.g. +/-0.35, which is what makes EASY reachable) from one merely above
    or below it. Collapsing the two would let a consumer filtering case IDs
    for boundary cases silently pull in far-from-limit ones too.
    """
    assert SNAPEligibilityGenerator._offset_tag(offset) == expected_tag
