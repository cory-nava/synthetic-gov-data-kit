"""Train / dev / test partitioning for fine-tuning experiments.

Two independent guards against leakage:

1. Whole-jurisdiction holdout. Every case from a holdout jurisdiction goes to
   `test` and is removed from consideration for `train` and `dev`.

2. Disjoint case_ids. Enforced by construction -- each case is assigned to
   exactly one partition -- and asserted before returning, because "by
   construction" is what every leaked benchmark also claimed.

Holding a jurisdiction out is not by itself an interesting test: if some other
jurisdiction in training shares its exact BBCE parameter combination, the model
only has to not key on the state's name. The sharper question is whether it can
apply a parameter combination it has never seen at all -- a 175% FPL gross limit,
a $25,000 asset limit -- and those two questions have different answers.

So each test case is labelled with which question it answers:

  in_distribution                       jurisdiction appears in train
  unseen_jurisdiction_seen_pattern      jurisdiction held out, bucket still in train
  unseen_jurisdiction_unseen_pattern    jurisdiction held out, bucket empty in train

The label is derived from the buckets actually present in `train`, never from a
hardcoded list. Change the holdout set and the labels follow; that is what
test_category_is_derived_from_train_not_hardcoded pins down.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from govsynth.fiscal_year import DEFAULT_SNAP_FY
from govsynth.models.test_case import TestCase
from govsynth.sources.us.snap_bbce import SNAPBBCESource

# The ten jurisdictions held out of training entirely. See the plan's Holdout
# design section for why each was chosen. TX is deliberately absent: it is the
# only remaining trained example of BBCE-with-an-asset-limit, and removing it
# would test extrapolation (no example of that shape at all) rather than
# generalization (an unseen jurisdiction sharing a trained pattern, or an
# unseen jurisdiction whose pattern is also unseen).
HOLDOUT_JURISDICTIONS: frozenset[str] = frozenset({"KS", "WY", "ID", "IA", "NE", "AR", "GU", "VI", "CA", "NV"})


@dataclass(frozen=True)
class Split:
    """The result of partitioning a case list for a fine-tuning experiment."""

    train: list[TestCase]
    dev: list[TestCase]
    test: list[TestCase]
    manifest: dict[str, Any]


def parameter_bucket(state: str, *, fiscal_year: int = DEFAULT_SNAP_FY) -> tuple[Any, ...]:
    """Return the BBCE parameter combination for a jurisdiction.

    Two jurisdictions with the same bucket are, as far as the eligibility
    waterfall is concerned, the same problem wearing a different name:
    ``("non_bbce",)`` for a jurisdiction that has not adopted BBCE, or
    ``(gross_income_limit_pct_fpl, asset_limit)`` for one that has.

    Raises ValueError for a jurisdiction the FY table does not cover --
    notably PR, which runs the Nutrition Assistance Program (a block grant)
    rather than SNAP, and must never enter either partition.

    ``SNAPBBCESource._resolve_bbce_params`` resolves an unrecognized state code
    to ``federal_default`` and stamps the result ``state == "FEDERAL"`` rather
    than raising. That silent fallback is convenient for the generator (every
    state code produces *something*) and dangerous here: without this check PR
    would pass through as a plausible-looking non-BBCE jurisdiction instead of
    being rejected. Detect the fallback explicitly.
    """
    code = state.upper()
    params = SNAPBBCESource(fiscal_year=fiscal_year, state=code).bbce_params
    if params.state == "FEDERAL":
        raise ValueError(
            f"{code!r} is not in the FY{fiscal_year} BBCE table; SNAPBBCESource fell "
            "back to federal_default. Puerto Rico runs the Nutrition Assistance "
            "Program, not SNAP, and must not appear in either partition."
        )
    if not params.bbce:
        return ("non_bbce",)
    return (params.gross_income_limit_pct_fpl, params.asset_limit)


def split_cases(
    cases: Sequence[TestCase],
    *,
    holdout_states: Iterable[str] = HOLDOUT_JURISDICTIONS,
    dev_fraction: float = 0.1,
    seed: int = 0,
    fiscal_year: int = DEFAULT_SNAP_FY,
) -> Split:
    """Partition `cases` into train / dev / test, honoring a whole-jurisdiction holdout.

    Every case from a `holdout_states` jurisdiction goes to `test` and never to
    `train` or `dev`. The remaining cases are sorted by `case_id` (so ordering
    is a pure function of content, not of generation order) and shuffled with
    `seed`, then split: `dev_fraction` of the remainder becomes `dev`, another
    `dev_fraction` becomes an in-distribution slice of `test` (otherwise `test`
    would only measure cross-jurisdiction transfer and there would be no
    in-distribution number to compare it against), and what's left is `train`.

    Raises ValueError if a holdout state is not present in `cases`, or if
    `dev_fraction` is outside [0, 1).
    """
    holdout = {s.upper() for s in holdout_states}
    present = {c.scenario.state.upper() for c in cases}
    missing = holdout - present
    if missing:
        raise ValueError(f"holdout_states {sorted(missing)} appear in no case; states present: {sorted(present)}")
    if not 0.0 <= dev_fraction < 1.0:
        raise ValueError(f"dev_fraction must be in [0, 1), got {dev_fraction}")

    test = [c for c in cases if c.scenario.state.upper() in holdout]
    remainder = [c for c in cases if c.scenario.state.upper() not in holdout]

    # Sort before shuffling: generator output order is not guaranteed stable
    # across runs, and an unsorted input would make `seed` insufficient to
    # reproduce the split.
    remainder = sorted(remainder, key=lambda c: c.case_id)
    rng = random.Random(seed)
    rng.shuffle(remainder)

    n_dev = int(len(remainder) * dev_fraction)
    n_test_seen = int(len(remainder) * dev_fraction)
    dev = remainder[:n_dev]
    test += remainder[n_dev : n_dev + n_test_seen]
    train = remainder[n_dev + n_test_seen :]

    _assert_disjoint(train, dev, test)

    # Categorize AFTER the split is final: the label depends on which buckets
    # actually survived into train, not on which jurisdictions we intended to
    # hold out. This is what keeps the labels honest if the holdout set changes.
    trained_states = {c.scenario.state.upper() for c in train}
    trained_buckets = {parameter_bucket(code, fiscal_year=fiscal_year) for code in trained_states}
    for case in test:
        code = case.scenario.state.upper()
        if code in trained_states:
            category = "in_distribution"
        elif parameter_bucket(code, fiscal_year=fiscal_year) in trained_buckets:
            category = "unseen_jurisdiction_seen_pattern"
        else:
            category = "unseen_jurisdiction_unseen_pattern"
        case.metadata["holdout_category"] = category

    manifest = {
        "counts": {"train": len(train), "dev": len(dev), "test": len(test)},
        "holdout_states": sorted(holdout),
        "dev_fraction": dev_fraction,
        "seed": seed,
        "fiscal_year": fiscal_year,
        "trained_buckets": sorted(str(b) for b in trained_buckets),
        "test_by_category": dict(Counter(c.metadata["holdout_category"] for c in test)),
        "test_by_jurisdiction": dict(Counter(c.scenario.state for c in test)),
        "case_ids": {
            "train": sorted(c.case_id for c in train),
            "dev": sorted(c.case_id for c in dev),
            "test": sorted(c.case_id for c in test),
        },
    }
    return Split(train=train, dev=dev, test=test, manifest=manifest)


def _assert_disjoint(*partitions: Sequence[TestCase]) -> None:
    """Raise AssertionError if any case_id appears in more than one partition."""
    seen: dict[str, int] = {}
    for index, part in enumerate(partitions):
        for case in part:
            prior = seen.get(case.case_id)
            if prior is not None:
                raise AssertionError(f"case_id {case.case_id!r} appears in partition {prior} and partition {index}")
            seen[case.case_id] = index
