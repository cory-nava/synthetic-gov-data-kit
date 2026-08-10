"""Train / dev / test partitioning for fine-tuning experiments.

Three independent guards, none of which trust the others:

1. Whole-jurisdiction holdout. Every case from a holdout jurisdiction goes to
   `test` and is removed from consideration for `train` and `dev`.

2. Disjoint case_ids. Enforced by construction -- each case is assigned to
   exactly one partition -- and asserted before returning, because "by
   construction" is what every leaked benchmark also claimed.

3. Every jurisdiction present in `cases` is validated against the FY BBCE
   table up front, before any partitioning happens -- not only the
   jurisdictions that happen to land in `train` or `test`. Categorization
   only ever looks at `train` and `test`, so without this a jurisdiction that
   isn't in the table (e.g. PR) could land entirely in `dev` at some seeds and
   pass silently, while landing in `train` at other seeds and raising. A
   safety check that only fires at some seeds is worse than one that never
   fires: it is green in CI and silent in a production run that happens to
   shuffle differently.

Holding a jurisdiction out is not by itself an interesting test: if some other
jurisdiction in training shares its exact BBCE parameter combination, the model
only has to not key on the state's name. The sharper question is whether it can
apply a parameter combination it has never seen at all -- a 175% FPL gross limit,
a $25,000 asset limit -- and those two questions have different answers.

"Parameter combination" (see `parameter_bucket`) means BBCE gross-limit/asset-cap
*and* region: two jurisdictions can share the former while running on distinct
allotment schedules, standard deductions, shelter caps, and minimum benefits
(Guam and Illinois both sit at 165% FPL/no asset cap, but Guam's benefit-side
numbers are Guam's own). Without region in the bucket, Guam's holdout would
silently collapse into the first kind of question -- unfamiliar name only --
even though its allotment table is genuinely unseen.

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
    """Return the (region, BBCE parameter) combination for a jurisdiction.

    Two jurisdictions with the same bucket are, as far as the eligibility
    waterfall is concerned, the same problem wearing a different name:
    ``(region, "non_bbce")`` for a jurisdiction that has not adopted BBCE, or
    ``(region, gross_income_limit_pct_fpl, asset_limit)`` for one that has.

    Region is part of the bucket, not just the BBCE parameters, because two
    jurisdictions can share a gross-income-limit/asset-limit combination while
    running on entirely different allotment schedules, standard deductions,
    excess shelter caps, and minimum benefits. Guam and Illinois are both
    (165% FPL gross, no asset cap) BBCE, but Guam has its own max allotment,
    standard deduction, and shelter cap (see data/thresholds/snap_fy2026.json)
    -- collapsing them into one bucket would make Guam's holdout test whether
    the model can recognize an unfamiliar state code, not whether it can apply
    parameters it has genuinely never seen. Keying on region as well makes
    that a real unseen-parameter probe instead of an unseen-jurisdiction one.

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
    source = SNAPBBCESource(fiscal_year=fiscal_year, state=code)
    params = source.bbce_params
    if params.state == "FEDERAL":
        raise ValueError(
            f"{code!r} is not in the FY{fiscal_year} BBCE table; SNAPBBCESource fell "
            "back to federal_default. Puerto Rico runs the Nutrition Assistance "
            "Program, not SNAP, and must not appear in either partition."
        )
    extra = source.thresholds().extra
    region = extra["region"] if extra else "48_states_dc"
    if not params.bbce:
        return (region, "non_bbce")
    return (region, params.gross_income_limit_pct_fpl, params.asset_limit)


def split_cases(
    cases: Sequence[TestCase],
    *,
    holdout_states: Iterable[str] = HOLDOUT_JURISDICTIONS,
    dev_fraction: float = 0.1,
    seed: int = 0,
    fiscal_year: int = DEFAULT_SNAP_FY,
) -> Split:
    """Partition `cases` into train / dev / test, honoring a whole-jurisdiction holdout.

    `cases` is sorted by `case_id` up front (so every downstream partition is a
    pure function of content, not of caller-supplied order -- see
    test_split_is_independent_of_input_order), then every case from a
    `holdout_states` jurisdiction goes to `test` and never to `train` or
    `dev`. The remainder is shuffled with `seed`, then split: `dev_fraction`
    of it becomes `dev`, another `dev_fraction` becomes an in-distribution
    slice of `test` (otherwise `test` would only measure cross-jurisdiction
    transfer and there would be no in-distribution number to compare it
    against), and what's left is `train`.

    Raises ValueError if a holdout state is not present in `cases`, if
    `dev_fraction` is outside [0, 1), or if any jurisdiction present in
    `cases` -- in *any* partition, not only `test` -- is not in the FY table
    (see parameter_bucket). That last check runs seed-independently over every
    present jurisdiction before partitioning, specifically so that an
    off-table jurisdiction (e.g. PR) landing in `dev` at some seeds and in
    `train`/`test` at others can't produce a seed-dependent pass/fail.

    Mutates `cases`: sets `case.metadata["holdout_category"]` on every case
    that ends up in `test`, and clears that key (if present from an earlier
    call) on every case in `train` or `dev`. Safe to call repeatedly on the
    same list with different `holdout_states`/`seed` -- each call leaves
    every case's label consistent with that call's own result, not a stale
    label from a previous call.
    """
    holdout = {s.upper() for s in holdout_states}
    present = {c.scenario.state.upper() for c in cases}
    missing = holdout - present
    if missing:
        raise ValueError(f"holdout_states {sorted(missing)} appear in no case; states present: {sorted(present)}")
    if not 0.0 <= dev_fraction < 1.0:
        raise ValueError(f"dev_fraction must be in [0, 1), got {dev_fraction}")

    # Validate every jurisdiction actually present, up front, before any
    # partitioning happens -- not just the ones that happen to land in `test`.
    # Categorization below only ever calls parameter_bucket() on `train` and
    # `test` states; without this pass, a not-in-the-table code (e.g. PR) that
    # lands entirely in `dev` at a given seed would sail through with no
    # error, because `dev` membership is never checked against the BBCE table.
    # That is a seed-dependent hole: green in CI, silent in a production run
    # that happens to shuffle differently. Doing this here makes the guard
    # unconditional -- it fires the same way regardless of which partition an
    # off-table jurisdiction's cases end up in.
    for code in sorted(present):
        parameter_bucket(code, fiscal_year=fiscal_year)

    # Sort once, up front, before splitting into the holdout slice and the
    # remainder: generator output order is not guaranteed stable across runs,
    # and filtering an unsorted `cases` would make the holdout slice of
    # `test` order-dependent even though only `remainder` gets an explicit
    # shuffle below -- a list comprehension over `cases` preserves whatever
    # order `cases` arrived in. Sorting first means every downstream list
    # (the holdout slice and the shuffled remainder) is a pure function of
    # content, not of caller-supplied order. See
    # test_split_is_independent_of_input_order.
    cases_sorted = sorted(cases, key=lambda c: c.case_id)
    test = [c for c in cases_sorted if c.scenario.state.upper() in holdout]
    remainder = [c for c in cases_sorted if c.scenario.state.upper() not in holdout]

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
    #
    # NOTE ON MUTATION: `cases` may be reused across multiple split_cases()
    # calls (e.g. a seed sweep, or comparing holdout sets). Every case in
    # `train` and `dev` has any pre-existing `holdout_category` cleared here,
    # so a case that was labelled `in_distribution` by a call with one holdout
    # set and now lands in train/dev under a different holdout set does not
    # keep carrying that stale label. Only `test` cases carry the key; absence
    # of the key is itself meaningful (this case is not part of `test`).
    trained_states = {c.scenario.state.upper() for c in train}
    trained_buckets = {parameter_bucket(code, fiscal_year=fiscal_year) for code in trained_states}
    for case in train:
        case.metadata.pop("holdout_category", None)
    for case in dev:
        case.metadata.pop("holdout_category", None)
    for case in test:
        code = case.scenario.state.upper()
        if code in trained_states:
            category = "in_distribution"
        elif parameter_bucket(code, fiscal_year=fiscal_year) in trained_buckets:
            category = "unseen_jurisdiction_seen_pattern"
        else:
            category = "unseen_jurisdiction_unseen_pattern"
        case.metadata["holdout_category"] = category

    # Non-holdout jurisdictions that nonetheless have no case in `train` (e.g.
    # an unlucky sample at a small n). `code in trained_states` doesn't
    # distinguish these from a deliberate holdout, so any test case from one
    # is labelled `unseen_jurisdiction_*` and would silently inflate the
    # generalization denominator. Recorded here so that's visible rather than
    # invisible; the current fixtures never trigger this (verified across
    # several seeds at n=8), but a smaller n in the future could.
    non_holdout_present = present - holdout
    states_absent_from_train = sorted(non_holdout_present - trained_states)

    manifest = {
        "counts": {"train": len(train), "dev": len(dev), "test": len(test)},
        "holdout_states": sorted(holdout),
        "dev_fraction": dev_fraction,
        "seed": seed,
        "fiscal_year": fiscal_year,
        "trained_buckets": sorted(str(b) for b in trained_buckets),
        "states_absent_from_train": states_absent_from_train,
        "test_by_category": dict(Counter(c.metadata["holdout_category"] for c in test)),
        "test_by_jurisdiction": dict(Counter(c.scenario.state.upper() for c in test)),
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
