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
