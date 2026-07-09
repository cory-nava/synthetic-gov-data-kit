# Repo Remediation Todo

Tracking list for the repo review recommendations. Check items off as they land.
See conversation / commit history for full rationale on each item.

## Docs / agent-guidance drift
- [x] Rewrite `AGENTS.md` so every file/class/field it references actually exists
      (civbench_id -> case_id, rules_engine.py, EdgeCaseFactory, config.py,
      formatters/base.py, civbench_schema_v1.yaml, etc.)
- [x] Cross-check `CLAUDE.md`'s "Key Abstractions" table against reality now
      that `reasoning/rules_engine.py` and `generators/base.py` are real —
      also fixed a wrong constructor kwarg in a copy-paste code example
      (`SNAPSource(year=...)` -> `fiscal_year=...`) and an invented
      threshold-JSON schema example

## CI / tooling
- [x] Add `.github/workflows/ci.yml`: ruff check, ruff format --check, mypy,
      pytest (with coverage) on push + PR, matrix across py3.10/3.11/3.12;
      validated the exact recipe end-to-end in a fresh venv before trusting it
- [x] Fix all `ruff check` errors (439 baseline -> 0; bumped line-length
      100->120 with rationale, excluded notebooks/ from lint scope)
- [x] Fix all `mypy govsynth/` errors (38 baseline -> 0)
- [x] Add CI badge to README

## Correctness fixes
- [x] Fix `lru_cache` JSON loaders returning shared mutable dicts
      (base.py, census.py, medicaid.py) — return copies, add cache
      invalidation on `refresh-census-data` writes
- [x] Add regression tests for cache copy-safety and invalidation
      (tests/unit/test_medicaid_caching.py)
- [x] Rename `TestCase.validate()` -> `check_output_contract()` to avoid
      shadowing pydantic's `BaseModel.validate` classmethod
- [x] Fix `pipeline.py Pipeline.save()` formatter variable reuse (mypy
      assignment errors across format branches)

## Reasoning abstraction (interop)
- [x] Build `govsynth/reasoning/rules_engine.py` with shared case-id and
      threshold-test-step builder helpers
- [x] Build `govsynth/generators/base.py` — `Generator` ABC every generator
      now implements (also closes the CLAUDE.md abstraction-table gap and
      fixed a real bug: SNAPEligibilityGenerator had no `.program`, so
      Pipeline's progress label silently showed "unknown")
- Decision: did NOT migrate SNAP/WIC's existing bespoke reasoning-step text
  onto the new helpers — those files are already tested and their narrative
  strings are edge-case-specific; forcing them through a generic helper
  would risk policy-accuracy regressions for no real benefit. New programs
  (Medicaid) use the shared helpers from day one instead.

## Program coverage
- [x] Add `govsynth/generators/medicaid_eligibility.py`
- [x] Register `medicaid.va` / `medicaid.tx` presets (expansion vs
      non-expansion contrast)
- [x] Add Medicaid generator tests

## Test backfill
- [x] `tests/unit/test_wic_source.py`
- [x] `tests/unit/test_medicaid_source.py` (beyond the caching smoke test)
- [x] `tests/unit/test_snap_eligibility_generator.py` — caught a real bug,
      see "Bonus fixes" below
- [x] `tests/unit/test_wic_eligibility_generator.py`
- [x] `tests/unit/test_medicaid_eligibility_generator.py`
- [x] `tests/unit/test_formatters.py` (yaml/jsonl/csv/hf_dataset)
- [x] `tests/unit/test_pipeline.py`
- [x] `tests/unit/test_presets.py`
- [x] `tests/unit/test_rationale_evaluator.py` — caught a second real bug,
      see "Bonus fixes" below
- [x] `tests/unit/test_census_fetcher.py` (new; respx-mocked, 17% -> 99%)
- [x] Raise `refresh_census.py` CLI coverage (40% -> 94%)
- [x] Raise `validate.py` CLI coverage (65% -> 100%)

Coverage: 76% -> 95% overall (2080 statements, 112 missed). Tests: 109 -> 263.

### Bonus fixes found via the new tests
- [x] SNAP/WIC/Medicaid generators built each case_id's random suffix with
      `uuid.uuid4()` instead of the generator's seeded `random.Random`, so
      `generate(n, seed=42)` was **not actually reproducible** — violates
      CLAUDE.md's "Deterministic with seeds" constraint. Fixed by routing
      the suffix through `reasoning/rules_engine.py:build_short_uid(rng)`
      everywhere case_ids are built.
- [x] `RationaleEvaluator._score_conclusion` used plain substring checks;
      since "eligible" is a substring of "ineligible" (and "qualify" a
      substring of "does not qualify"), any output that clearly said
      "ineligible" was scored as an ambiguous 0.5 instead of a correct 1.0
      or wrong 0.0. Fixed with word-boundary regex matching that also
      strips negated phrases before checking positive signals.

## Misc
- [x] Surface `parse-policy` as an explicit stub in `--help` output — turned
      out already done (`"""(Roadmap) ..."""` docstring shows in both
      `govsynth --help` and `govsynth parse-policy --help`); verified, no
      change needed
- [x] Update `CHANGELOG.md`

## Verification
- [x] `ruff check .` clean
- [x] `mypy govsynth/` clean
- [x] `pytest` all green (263 passed), 95% coverage
- [x] Commit + push to `claude/repo-review-recommendations-45mtz7`

## Known follow-ups not done in this pass
- `PresetConfig.source_class` is declared and populated for every preset but never
  consumed anywhere (`Pipeline.from_preset` only uses `generator_class`) — dead metadata,
  left as-is since removing it is unrelated cleanup beyond this pass's scope.
- No shared `formatters/base.py` — each formatter's `write(...)` signature differs
  slightly (YAML has `write_one`/`write_many`, others just `write`). Documented as
  intentional in AGENTS.md/CLAUDE.md rather than forcing a premature abstraction.
