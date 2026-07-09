# AGENTS.md

Guidelines for AI agents (Claude Code, Cursor, Copilot Workspace, etc.) working in this repo.

Every file, class, and field named below was verified against the actual codebase (not
aspirational). If something here stops matching reality, fix this file in the same PR —
a wrong AGENTS.md is worse than none, because agents will confidently write code against
modules that don't exist.

---

## What This Repo Does

Generates structured synthetic US government benefits test cases for LLM evaluation.
The output feeds directly into CivBench.

**Primary concern when making changes**: policy accuracy. An incorrect income threshold
or miscited CFR section produces test cases that will silently evaluate models against
wrong ground truth. Always verify threshold values against `data/thresholds/` before
generating or modifying cases.

---

## Repo Map for Agents

```
govsynth/models/           ← Start here. Core data structures (TestCase, RationaleTrace).
govsynth/sources/us/       ← Data connectors. One file per program (snap.py, wic.py, medicaid.py).
govsynth/profiles/         ← Profile generators. us_household.py is the main one.
govsynth/generators/       ← Test case builders. base.py defines the Generator ABC;
                              snap_eligibility.py / wic_eligibility.py / medicaid_eligibility.py
                              implement it.
govsynth/reasoning/        ← rules_engine.py: shared case-id and threshold-test-step
                              helpers. New generators should use these; SNAP/WIC predate
                              them and keep bespoke, already-verified reasoning text.
govsynth/formatters/       ← Output serialization: yaml_fmt.py, jsonl.py, csv_fmt.py,
                              hf_dataset.py (requires the `[hf]` extra). No shared base
                              class — each formatter's write signature differs slightly.
govsynth/pipeline.py       ← High-level API. Most users start here (Pipeline, BatchPipeline).
govsynth/presets.py        ← Preset registry. Maps names like "snap.va" to config.
govsynth/fiscal_year.py    ← FY/CY period config per program (DEFAULT_SNAP_FY, etc.).
data/thresholds/           ← Source of truth for all income/asset limits.
data/seeds/us/             ← Policy text excerpts used in rationale generation.
tests/unit/                ← Test coverage. Match file to module name.
tests/integration/         ← End-to-end CLI workflow tests.
```

---

## Agent Task Playbooks

### Adding a new US program

**Files to create/modify (in order):**

1. `data/thresholds/{program}_{year}.json` — threshold table
2. `data/seeds/us/{program}/` — policy text excerpts
3. `govsynth/sources/us/{program}.py` — source connector, extends `DataSource`
   (`govsynth/sources/base.py`)
4. `govsynth/generators/{program}_eligibility.py` — extends `Generator`
   (`govsynth/generators/base.py`); use `govsynth.reasoning.rules_engine`'s
   `build_case_id`/`build_short_uid`/`build_threshold_test_step` for the id and
   common reasoning-step shapes instead of hand-rolling them
5. `govsynth/presets.py` — register presets (e.g. `"{program}.{state}"`)
6. `tests/unit/test_{program}_source.py` and
   `tests/unit/test_{program}_eligibility_generator.py` — unit tests
7. `docs/programs/{program}.md` — program documentation

**Verification:** After adding, run:
```bash
python -c "from govsynth import Pipeline; p = Pipeline.from_preset('{program}.{state}'); cases = p.generate(n=5, seed=42); print(cases[0].case_id, cases[0].is_valid())"
```

---

### Updating annual thresholds

Federal poverty levels and program income limits update each October (federal fiscal year)
for SNAP, each January (calendar year) for Medicaid, and each July 1 for WIC.

1. Update `data/thresholds/us_fpl_{new_year}.json`
2. Update relevant program threshold files (`snap_fy{year}.json`, `wic_fy{year}.json`,
   `medicaid_cy{year}.json`)
3. Update the `DEFAULT_{PROGRAM}_FY` / `DEFAULT_MEDICAID_CY` constants in
   `govsynth/fiscal_year.py`
4. Run `pytest tests/unit/test_snap_source.py tests/unit/test_wic_source.py
   tests/unit/test_medicaid_source.py` to verify threshold lookups still pass
5. Update `data/thresholds/README.md` with new source citations
6. Run `python scripts/verify_thresholds.py` (or `govsynth verify-thresholds`) to confirm
   `verification_status` fields are accurate

**Source URLs:**
- FPL: https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines
- SNAP: https://www.fns.usda.gov/snap/recipient/eligibility
- WIC: https://www.fns.usda.gov/wic/eligibility
- Medicaid: https://www.medicaid.gov/medicaid/eligibility

---

### Adding a new output format

1. Create `govsynth/formatters/{format_name}.py` with a class implementing at minimum
   `write(cases: list[TestCase], path) -> None` (see `csv_fmt.py` for the simplest example)
2. Register the class in `govsynth/formatters/__init__.py`
3. Add `OutputFormat.{FORMAT_NAME}` in `govsynth/models/enums.py` and a matching branch in
   `Pipeline.save()` (`govsynth/pipeline.py`)
4. Add tests in `tests/unit/test_formatters.py`

---

### Adding edge case variations

SNAP's edge-case builders live directly in `govsynth/generators/snap_eligibility.py`
(`_build_homeless_case`, `_build_student_case`, `_build_boarder_case`,
`_build_migrant_case`, `_build_mixed_immigration_case`,
`_build_categorical_eligibility_case`), registered in
`SNAPEligibilityGenerator._build_special_population_cases`. Threshold-boundary profiles
(at/above/below a limit) are built by `USHouseholdProfile.at_threshold(...)`
(`govsynth/profiles/us_household.py`), which currently supports `program="snap"` and
`program="wic"` only — extending it to a new program means adding a branch there.

To add a new SNAP special-population case type:
1. Add a `_build_{name}_case(self, rng: random.Random) -> TestCase` method to
   `SNAPEligibilityGenerator`
2. Register it in the `builders` list in `_build_special_population_cases`
3. Add a test in `tests/unit/test_snap_eligibility_generator.py`
4. Document it in `EDGE_CASES.md`

---

## Agent Rules

### Never do these

- **Never fabricate policy thresholds.** Always load from `data/thresholds/` or fetch from
  official source URLs. Do not hardcode income limits in generator logic.
- **Never add PII to seeds or test fixtures.** All names/SSNs/addresses must use Faker.
- **Never skip rationale traces.** Every `TestCase` must have a populated `rationale_trace`
  with at least 2 steps — this is enforced by a pydantic `model_validator` on `TestCase`
  (`govsynth/models/test_case.py`), so a case missing one will fail to construct at all.
- **Never change the `case_id` format** (`{program}.{jurisdiction}.{task_type}.{descriptor}.
  {outcome}.hh{size}.{uid}`, see `CLAUDE.md`'s "Test Case IDs" section) without checking
  every place that parses or asserts on it (`TestCase.validate_case_id`, `docs/`, notebooks).
- **Never build a case_id's random suffix from `uuid.uuid4()`.** Use
  `reasoning.rules_engine.build_short_uid(rng)` with the generator's own seeded
  `random.Random` instance — `uuid.uuid4()` ignores the seed entirely, which silently breaks
  `generate(n, seed=42)` reproducibility (this exact bug shipped in SNAP/WIC's original
  case-id builders and was only caught by later determinism tests).

### Always do these

- **Cite the CFR/regulation section** for every `ReasoningStep.rule_applied` field.
- **Use `seed=42` in tests** to ensure deterministic output, and add a determinism test
  (`generate(n, seed=42)` twice, assert identical `case_id`s) for any new generator —
  this is the test that catches the `uuid.uuid4()` mistake above.
- **Run `ruff check . && ruff format --check . && mypy govsynth/ && pytest`** before
  committing — this is exactly what `.github/workflows/ci.yml` runs on every PR.
- **Keep threshold logic in source connectors**, not in generators. Generators call
  `source.fetch_thresholds()` / `source.is_eligible()` — they do not hardcode values.
- **Never mutate a dict returned from a `_load_*_json` cache loader in `sources/base.py`,
  `sources/us/census.py`, or `sources/us/medicaid.py`.** The public loaders already return
  a deep copy per call specifically so callers can't corrupt the shared `lru_cache`, but
  don't add a new cached loader that skips the copy step.

---

## Output Contract

Every `TestCase` produced by this library must satisfy:

```python
assert case.case_id != ""
assert case.program in KNOWN_PROGRAMS
assert case.jurisdiction != ""
assert case.scenario.summary != ""
assert case.task.instruction != ""
assert case.expected_outcome != ""
assert case.expected_answer != ""
assert len(case.rationale_trace.steps) >= 2
assert len(case.source_citations) >= 1
```

`TestCase.check_output_contract()` runs these checks and returns a list of error strings
(empty = valid); `TestCase.is_valid()` is the boolean convenience wrapper. Always call one
of these before serializing. (Named `check_output_contract`, not `validate`, specifically
to avoid shadowing pydantic's own inherited `BaseModel.validate` classmethod.)

`Pipeline.generate()` already calls `is_valid()` on every case and drops/warns on failures,
so cases coming out of `Pipeline.generate()` are guaranteed valid; this matters mainly for
custom generators or hand-built `TestCase` objects.

---

## Testing Conventions

```
tests/unit/test_{module_name}.py     — mirrors govsynth/{module_name}.py
tests/integration/test_cli_{cmd}.py  — end-to-end CLI workflow tests (via CliRunner)
```

Every new public function needs at least:
- One happy-path test
- One edge case test (boundary condition)
- One invalid input test (raises expected exception)

```python
# Naming convention
def test_{function}_{condition}() -> None:
    ...

# Examples
def test_snap_threshold_household_size_3() -> None:
def test_snap_threshold_at_gross_income_limit() -> None:
def test_generate_is_deterministic_with_seed() -> None:
```

Network-touching code (`govsynth/sources/us/census_fetcher.py`,
`govsynth/cli/commands/refresh_census.py`) is tested with `respx` (already a `[dev]`
dependency) — never make real HTTP calls in tests.

---

## Dependency Policy

- **Core dependencies** (in `[dependencies]`): must work without any optional extras
- **HF features**: gated behind `[hf]` extra; `datasets` and `huggingface_hub` are only
  imported inside `HFDatasetFormatter` methods and under `TYPE_CHECKING`, never at module
  top level, so the core package works without them installed
- **No LLM SDKs in core**: no LLM provider SDKs are core dependencies. `RationaleEvaluator`
  (`govsynth/evaluation/rationale_evaluator.py`) scores model output with keyword/regex
  heuristics, not an LLM call — if you add LLM-assisted enrichment, keep it in a clearly
  optional module that isn't imported by default
