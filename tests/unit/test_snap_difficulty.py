"""Difficulty labelling must be honest: every level producible, edge cases adversarial."""

import collections

import pytest

from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator
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


def test_easy_is_reachable_from_the_offset_set():
    from govsynth.generators.snap_eligibility import _OFFSETS

    # _classify_difficulty returns EASY only when abs(offset) > 0.30.
    assert any(abs(o) > 0.30 for o in _OFFSETS), (
        f"no offset exceeds 0.30, so Difficulty.EASY is unreachable: {_OFFSETS}"
    )


def test_every_difficulty_level_is_producible():
    """The test that would have caught all three bugs at once.

    Uses a non-BBCE state (KS) rather than the VA used elsewhere in this file.
    _classify_difficulty returns Difficulty.MEDIUM whenever
    `self.bbce_source.is_bbce` is true -- a state-wide property fixed at
    generator construction -- *before* it ever looks at the offset. VA has
    adopted BBCE, so for a VA-backed generator that branch always fires and
    Difficulty.EASY can never be produced, no matter how wide _OFFSETS is.
    That gate is unrelated to the offset-set bug this test targets and is out
    of scope for this fix, so KS (bbce: false in snap_bbce_fy2026.json) is
    used here to isolate and verify the offset-widening behaviour.
    """
    generator = SNAPEligibilityGenerator(state="KS")
    cases = generator.generate(n=200, profile_strategy="edge_saturated", seed=11)
    produced = {c.difficulty for c in cases}
    missing = set(Difficulty) - produced
    assert not missing, f"these Difficulty levels are never produced: {missing}"
