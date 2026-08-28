# CLAUDE.md

This file provides context for Claude Code and AI coding assistants working in this repository.

---

## Project Overview

`synthetic-gov-data-kit` is a Python library for generating structured synthetic US government
benefits data used to evaluate LLM reasoning and rationale quality. It is designed to be
layer for CivBench — the open benchmark for government
benefits data to evaluate LLM reasoning quality.

The core value proposition is **reasoning-grounded test cases**: every generated case includes
not just a question and expected answer, but a step-by-step `RationaleTrace` mapping the correct
policy reasoning chain. This enables evaluation of *how* a model reasons, not just *what* it answers.

---

## Repository Layout

```
govsynth/               Main Python package
  sources/us/           US government data connectors (SNAP, WIC, Medicaid)
  profiles/             Synthetic citizen/household profile generator (USHouseholdProfile)
  generators/           Test case generators. base.py defines the Generator ABC;
                         snap_eligibility.py / wic_eligibility.py / medicaid_eligibility.py
                         implement it. Currently eligibility-determination only — the
                         TaskType enum has policy_qa/form/agentic/comparative values for
                         future generators, but none exist yet.
  reasoning/             rules_engine.py: shared case-id and threshold-test-step builder
                         helpers, used by newer generators (Medicaid). SNAP/WIC predate
                         this module and keep their own already-verified reasoning text
                         inline rather than being retrofitted onto it.
  formatters/            Output serializers: yaml_fmt.py, jsonl.py, csv_fmt.py,
                         hf_dataset.py (requires the `[hf]` extra; datasets/huggingface_hub
                         are only imported inside it, never at package top level)
  evaluation/            Rationale scoring utilities (keyword/regex heuristics, no LLM calls)

data/seeds/us/          Bundled policy seed data (income limits, CFR excerpts)
data/thresholds/        Annually-updated threshold tables (FPL, program limits)

tests/unit/             Unit tests — one file per module
tests/integration/      Integration tests — end-to-end CLI workflow runs

notebooks/              Jupyter notebooks (quickstart, program-specific examples)
docs/                   Extended documentation
```

---

## Key Abstractions

| Class | File | Purpose |
|---|---|---|
| `DataSource` | `sources/base.py` | Abstract base for all data connectors |
| `Generator` | `generators/base.py` | Abstract base for test case generators |
| `USHouseholdProfile` | `profiles/us_household.py` | Synthetic applicant profile (plain dataclass; no separate base class exists yet — there is currently only one profile type) |
| `TestCase` | `models/test_case.py` | Core output data structure |
| `RationaleTrace` | `models/rationale.py` | Step-by-step reasoning chain |
| `Pipeline` | `pipeline.py` | High-level orchestration |
| `BatchPipeline` | `pipeline.py` | Multi-program orchestration |

---

## Data Models (Pydantic v2)

All core data models use **Pydantic v2** with strict validation. When adding fields:
- Always provide `description=` in `Field()`
- Use `Literal` types for constrained string fields
- Use `Annotated` for field-level constraints (e.g. income must be >= 0)

```python
# Correct pattern
from pydantic import BaseModel, Field
from typing import Annotated
from annotated_types import Ge


class HouseholdProfile(BaseModel):
    monthly_gross_income: Annotated[float, Ge(0)] = Field(description="Pre-deduction monthly gross income in USD")
```

---

## Policy Data

### Threshold Tables

All program threshold tables live in `data/thresholds/` as JSON, e.g. `snap_fy2026.json`.
They are loaded lazily (on first `.thresholds()` call, cached per-process) by the relevant
source connector — see `data/thresholds/snap_fy2026.json` for the real shape. Simplified:

```json
{
  "_metadata": {
    "program": "snap",
    "fiscal_year": 2026,
    "source": "USDA FNS SNAP FY2026 Cost-of-Living Adjustments Memo (August 13, 2025)",
    "cfr_reference": "7 CFR 273.9",
    "verification_status": "verified"
  },
  "asset_limit_general": 3000,
  "households_48_states_dc": {
    "1": { "gross_monthly": 1696, "net_monthly": 1305, "max_benefit": 298 },
    "2": { "gross_monthly": 2292, "net_monthly": 1763, "max_benefit": 546 }
  }
}
```

Real files also carry Alaska/Hawaii region variants (`households_alaska`,
`households_hawaii`) and per-region standard deductions — check the actual JSON before
assuming a field name.

### Policy Seeds

Policy seed documents in `data/seeds/us/` are plain text excerpts from CFR and agency handbooks,
used as grounding context when generating rationale traces. They are **not** full documents —
only the specific sections relevant to eligibility determination.

**Important**: Never embed PII or real applicant data in seed files. Seeds contain only
policy rules, thresholds, and regulatory citations.

---

## Test Case IDs

Case IDs follow this schema:
```
{program}.{jurisdiction}.{task_type}.{variation_descriptor}[.{disambiguator}]
```

Examples:
- `snap.va.eligibility.gross_income_at_limit.hh3`
- `snap.ca.eligibility.categorical_eligibility_override`
- `wic.national.eligibility.pregnant_income_185pct_fpl`
- `medicaid.tx.eligibility.non_expansion_coverage_gap`

Rules:
- All lowercase, dot-separated
- Jurisdiction uses ISO-style codes: `us.va`, `us.ca`, `us.tx`, or just `va` for US states
- `task_type` must be one of: `eligibility`, `policy_qa`, `form`, `agentic`, `comparative`
- Descriptor should be human-readable and specific enough to be self-documenting

---

## Adding a New Program

To add support for a new US benefits program (e.g., LIHEAP):

1. **Add threshold data**: `data/thresholds/liheap_2025.json`
2. **Add seed policy docs**: `data/seeds/us/liheap/` (CFR + agency handbook excerpts)
3. **Create source connector**: `govsynth/sources/us/liheap.py` extending `DataSource`
4. **Create generator**: `govsynth/generators/liheap_eligibility.py` extending `Generator`
   (`govsynth/generators/base.py`); reuse `govsynth/reasoning/rules_engine.py`'s
   `build_case_id`/`build_short_uid`/`build_threshold_test_step` helpers for the case_id
   and common "compare amount to threshold" reasoning steps
5. **Register presets** in `govsynth/presets.py`
6. **Add unit tests**: `tests/unit/test_liheap_source.py` and
   `tests/unit/test_liheap_eligibility_generator.py` (include a determinism test —
   `generate(n, seed=42)` twice, assert identical `case_id`s)
7. **Document in** `docs/programs/liheap.md`

See `govsynth/generators/medicaid_eligibility.py` for the current reference example of a
generator built with the `Generator` ABC and `rules_engine` helpers from scratch.

---

## Running Tests

```bash
# All tests
pytest

# Unit only (faster)
pytest tests/unit/

# Single test file
pytest tests/unit/test_snap_source.py -v

# With coverage report
pytest --cov=govsynth --cov-report=html
```

---

## Code Style

- **Formatter**: `ruff format` (line length 120 — wider than the common 100 because
  generators build long natural-language rationale/citation f-strings that read worse
  split across lines; notebooks/ are excluded from lint scope entirely)
- **Linter**: `ruff check` (see `pyproject.toml` for rules)
- **Type checker**: `mypy --strict`
- CI (`.github/workflows/ci.yml`) runs all of the above plus `pytest` on every PR across
  Python 3.10–3.12 — nothing here is aspirational, it's enforced
- All public functions and classes must have docstrings
- Use Google-style docstrings

```bash
# Format + lint
ruff format . && ruff check .

# Type check
mypy govsynth/
```

---

## Important Constraints

1. **No real PII ever** — all profiles are synthetic. The `Faker` library is used for names/addresses.
2. **Policy accuracy matters** — threshold values must match the actual CFR/FNS tables for the given fiscal year. Always cite the source regulation.
3. **Deterministic with seeds** — all random generation must accept a `seed: int | None` parameter and use it, including case_id suffixes. Use `reasoning.rules_engine.build_short_uid(rng)`, never `uuid.uuid4()`, for a case_id's random suffix — `uuid.uuid4()` ignores the seed and silently breaks `generate(n, seed=42)` reproducibility. Tests should use `seed=42` and, for any generator, include a determinism test that calls `generate()` twice with the same seed and asserts identical `case_id`s.
4. **Output validity** — generated YAML must be well-formed and all required fields must be present.
5. **No LLM calls in generation** — the core library generates cases from policy rules, not by calling an LLM. LLM calls are only in optional enrichment utilities (clearly marked).

---

## Common Patterns

### Loading threshold data
```python
from govsynth.sources.us.snap import SNAPSource

source = SNAPSource(fiscal_year=2026, state="VA")
thresholds = source.fetch_thresholds()
limit = thresholds.by_household_size(3)
```

### Generating edge cases
```python
from govsynth.profiles.us_household import USHouseholdProfile

profile = USHouseholdProfile.at_threshold(
    program="snap", threshold="gross_income_limit", state="VA", household_size=3, offset_pct=0.0
)
```

### Running a pipeline
```python
from govsynth import Pipeline

pipeline = Pipeline.from_preset("snap.va")
cases = pipeline.generate(n=100, seed=42)
pipeline.save(cases, "./output/", formats=["yaml", "jsonl"])
```
