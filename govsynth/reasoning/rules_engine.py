"""Shared reasoning-trace construction helpers.

`govsynth/generators/*.py` each build a `RationaleTrace` out of the same
handful of recurring shapes: a short random case-id suffix, and a
"compare a dollar amount against a threshold" reasoning step. This module
factors those out so a new program generator (see
`govsynth/generators/medicaid_eligibility.py`) can reuse them instead of
re-deriving the PASS/FAIL and `is_determinative` bookkeeping from scratch.

Existing SNAP/WIC edge-case builders are intentionally left as-is: their
reasoning steps carry bespoke, edge-case-specific narrative text that a
generic helper can't reproduce without losing precision, and rewriting
already-verified policy text is riskier than the duplication it would
remove.
"""

from __future__ import annotations

import random

from govsynth.models.rationale import ReasoningStep


def build_short_uid(rng: random.Random) -> str:
    """Return a short case-id suffix, e.g. 'a3f9c2', drawn from `rng`.

    Takes the generator's own seeded `random.Random` instance rather than
    `uuid.uuid4()` so that `generate(n, seed=42)` is fully reproducible,
    including case_ids -- not just the policy values inside each case.
    """
    return f"{rng.getrandbits(24):06x}"


def build_case_id(
    program: str,
    jurisdiction: str,
    descriptor: str,
    outcome: str,
    household_size: int,
    uid: str,
    task_type: str = "eligibility",
) -> str:
    """Build a `{program}.{jurisdiction}.{task_type}.{descriptor}.{outcome}.hh{size}.{uid}`
    case_id, matching the schema documented in CLAUDE.md.

    `uid` must come from `build_short_uid(rng)` so the id stays reproducible
    under a given seed.
    """
    return f"{program}.{jurisdiction.lower()}.{task_type}.{descriptor}.{outcome}.hh{household_size}.{uid}"


def build_threshold_test_step(
    *,
    step_number: int,
    title: str,
    rule_applied: str,
    amount: float,
    amount_key: str,
    limit: float,
    limit_key: str,
    context_note: str = "",
    note: str | None = None,
) -> ReasoningStep:
    """Build a ReasoningStep comparing a dollar amount against a threshold.

    This is the single most common shape of SNAP/WIC/Medicaid reasoning
    step: "is $X <= $Y?" Handles the computation string, PASS/FAIL result,
    and `is_determinative` flag consistently.

    Args:
        amount: The value being tested, e.g. monthly income.
        amount_key: Key under which `amount` is recorded in `inputs`.
        limit: The threshold `amount` is compared against.
        limit_key: Key under which `limit` is recorded in `inputs`.
        context_note: Optional parenthetical appended to the computation
            string, e.g. "130% FPL, 3-person HH".
        note: Optional free-text clarification carried on the step.
    """
    passed = amount <= limit
    comparator = "<=" if passed else ">"
    suffix = f" ({context_note})" if context_note else ""
    return ReasoningStep(
        step_number=step_number,
        title=title,
        rule_applied=rule_applied,
        inputs={amount_key: amount, limit_key: limit},
        computation=f"${amount:,.2f} {comparator} ${limit:,.2f}{suffix}",
        result="PASS" if passed else "FAIL",
        is_determinative=not passed,
        note=note,
    )
