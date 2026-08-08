# Bring Your Own Policy Document

This guide walks through adding a new benefits program — or a custom policy — to
`synthetic-gov-data-kit` so you can generate test cases from your own policy rules.

The library has no built-in knowledge of policy thresholds. Everything comes from
structured JSON files you provide. If you can read an income limit table out of a
government document, you can generate test cases from it.

---

## How the library works (one paragraph)

A **DataSource** loads your policy thresholds from a JSON file and exposes
`is_eligible()`. A **Generator** uses that source to create synthetic household
profiles and build `TestCase` objects with rationale traces. A **Pipeline**
wires them together and handles output. You only need to write the first two.

```
your_policy.json
      ↓
 DataSource          ← reads thresholds, implements is_eligible()
      ↓
 Generator           ← builds synthetic profiles + rationale traces
      ↓
 Pipeline            ← orchestrates, formats, saves
      ↓
 test_cases.yaml / .jsonl / .csv
```

---

## Step 1 — Extract your thresholds into JSON

Find the income limits, benefit amounts, or other thresholds in your policy document
(CFR section, agency memo, state handbook, etc.) and write them into a JSON file.

Save it to `data/thresholds/{program}_{year}.json`.

### Minimal threshold file

```json
{
  "_metadata": {
    "program": "liheap",
    "fiscal_year": 2026,
    "effective_date": "2025-10-01",
    "source": "HHS LIHEAP State Median Income Tables FY2026",
    "source_url": "https://www.acf.hhs.gov/ocs/resource/liheap-im-2025-01",
    "cfr_reference": "42 USC 8624",
    "verification_status": "verified",
    "verification_note": "Values taken directly from HHS OCS LIHEAP IM 2025-01, Table 1."
  },
  "income_limit_pct_smi": 60,
  "households": {
    "1": { "gross_monthly": 2150, "max_benefit": 400 },
    "2": { "gross_monthly": 2820, "max_benefit": 500 },
    "3": { "gross_monthly": 3490, "max_benefit": 600 },
    "4": { "gross_monthly": 4160, "max_benefit": 700 },
    "5": { "gross_monthly": 4830, "max_benefit": 800 },
    "6": { "gross_monthly": 5500, "max_benefit": 900 },
    "7": { "gross_monthly": 6170, "max_benefit": 1000 },
    "8": { "gross_monthly": 6840, "max_benefit": 1100 },
    "each_additional": { "gross_monthly": 670, "max_benefit": 100 }
  }
}
```

### Threshold file conventions

| Field | Required | Notes |
|---|---|---|
| `_metadata.program` | Yes | Lowercase identifier matching your source class |
| `_metadata.fiscal_year` | Yes | Integer year |
| `_metadata.source` | Yes | Full citation for the source document |
| `_metadata.verification_status` | Yes | `"verified"` or `"estimated"` — never invent values |
| `households` | Yes | Keyed by household size (string), plus optional `"each_additional"` |
| `households.N.gross_monthly` | Yes | The income limit for household size N |
| `households.N.max_benefit` | Recommended | Maximum benefit amount for household size N |
| `households.N.net_monthly` | If applicable | Net income limit after deductions |

You can add any additional top-level keys your source connector needs
(e.g., `asset_limit_general`, `earned_income_deduction_pct`, `income_limit_pct_smi`).

### `verification_status` values

- `"verified"` — values confirmed directly from an official published source
- `"estimated"` — interpolated or approximated; add a note explaining why
- `"pending"` — not yet reviewed against the official source

**Never set `"verified"` without checking the actual document.**

---

## Step 2 — Write the DataSource connector

Create `govsynth/sources/us/{program}.py`. It must:
- Subclass `DataSource`
- Implement `fetch_thresholds()` → `ProgramThresholds`
- Implement `fetch_policy_summary()` → `str`
- Implement `is_eligible()` → `tuple[bool, str]`

### Minimal example: LIHEAP

```python
# govsynth/sources/us/liheap.py
from __future__ import annotations
from govsynth.sources.base import DataSource, HouseholdThreshold, ProgramThresholds


class LIHEAPSource(DataSource):
    """LIHEAP eligibility data source.

    Args:
        fiscal_year: Federal fiscal year. Default 2026.
        state: Two-letter state code or 'national'.
    """

    def __init__(self, fiscal_year: int = 2026, state: str = "national") -> None:
        super().__init__(year=fiscal_year, state=state)
        self.fiscal_year = fiscal_year

    @property
    def program(self) -> str:
        return "liheap"

    def fetch_thresholds(self) -> ProgramThresholds:
        raw = self._load_threshold_json(f"liheap_{self.fiscal_year}.json")

        households: dict[int, HouseholdThreshold] = {}
        for key, val in raw["households"].items():
            if key == "each_additional":
                continue
            size = int(key)
            households[size] = HouseholdThreshold(
                household_size=size,
                gross_monthly=float(val["gross_monthly"]),
                net_monthly=float(val.get("net_monthly", val["gross_monthly"])),
                max_benefit=float(val.get("max_benefit", 0)),
            )

        return ProgramThresholds(
            program="liheap",
            fiscal_year=raw["_metadata"]["fiscal_year"],
            state=self.state,
            source=raw["_metadata"]["source"],
            source_url=raw["_metadata"].get("source_url"),
            households=households,
            extra={
                "income_limit_pct_smi": raw.get("income_limit_pct_smi", 60),
                "verification_status": raw["_metadata"]["verification_status"],
            },
        )

    def fetch_policy_summary(self) -> str:
        t = self.thresholds()
        return (
            f"LIHEAP Eligibility (FY{self.fiscal_year}, {self.state}):\n"
            f"- Income limit: {t.extra['income_limit_pct_smi']}% of State Median Income\n"
            f"- No asset test\n"
            f"Source: {t.source}"
        )

    def is_eligible(
        self,
        household_size: int,
        gross_income: float,
    ) -> tuple[bool, str]:
        """Determine LIHEAP eligibility. Returns (is_eligible, reason)."""
        t = self.thresholds()
        limits = t.by_household_size(min(household_size, 8))

        if gross_income > limits.gross_monthly:
            return False, (
                f"Ineligible: gross income ${gross_income:,.2f} exceeds "
                f"${limits.gross_monthly:,.2f} ({t.extra['income_limit_pct_smi']}% SMI, "
                f"{household_size}-person HH)"
            )

        return True, (
            f"Eligible: ${gross_income:,.2f} ≤ ${limits.gross_monthly:,.2f} ({t.extra['income_limit_pct_smi']}% SMI)"
        )
```

### Key methods in `DataSource` you can use

| Method | What it does |
|---|---|
| `self._load_threshold_json("filename.json")` | Loads a file from `data/thresholds/` |
| `self._load_seed_text("program", "file.txt")` | Loads a seed text from `data/seeds/` |
| `self.thresholds()` | Returns cached `ProgramThresholds` (calls `fetch_thresholds()` once) |

---

## Step 3 — Write the Generator

Create `govsynth/generators/{program}_eligibility.py`. The generator:
- Takes a `DataSource` as its source of truth
- Samples synthetic household profiles
- Builds `TestCase` objects with rationale traces

### Minimal example: LIHEAP

```python
# govsynth/generators/liheap_eligibility.py
from __future__ import annotations
import random
from govsynth.models.enums import Difficulty, Program, TaskType
from govsynth.models.rationale import PolicyCitation, RationaleTrace, ReasoningStep
from govsynth.models.test_case import ScenarioBlock, TaskBlock, TestCase
from govsynth.profiles.us_household import USHouseholdProfile
from govsynth.sources.us.liheap import LIHEAPSource


_TASK_INSTRUCTION = (
    "Based on the household's situation described above, determine whether this household "
    "is eligible for LIHEAP (Low Income Home Energy Assistance Program) benefits. "
    "Show your reasoning step by step, citing the applicable regulations. "
    "State your final determination (eligible or ineligible)."
)


class LIHEAPEligibilityGenerator:
    """Generates LIHEAP eligibility test cases."""

    def __init__(self, fiscal_year: int = 2026, state: str = "national") -> None:
        self.fiscal_year = fiscal_year
        self.state = state.upper()
        self.source = LIHEAPSource(fiscal_year=fiscal_year, state=state)

    def generate(self, n: int, seed: int | None = None) -> list[TestCase]:
        rng = random.Random(seed)
        cases = []
        for i in range(n):
            case_seed = rng.randint(0, 2**31) if seed is not None else None
            profile = self._sample_profile(rng, case_seed)
            try:
                cases.append(self._build_case(profile, case_seed, i))
            except Exception as exc:
                print(f"  Warning: skipped case {i}: {exc}")
        return cases

    def _sample_profile(self, rng: random.Random, seed: int | None) -> USHouseholdProfile:
        hh_size = rng.choices([1, 2, 3, 4, 5], weights=[0.25, 0.25, 0.25, 0.15, 0.10])[0]
        t = self.source.thresholds()
        limits = t.by_household_size(min(hh_size, 8))
        # Sample income around the threshold
        offset = rng.choice([-0.05, -0.01, 0.0, 0.01, 0.05])
        gross = round(limits.gross_monthly * (1 + offset), 2)
        return USHouseholdProfile(
            household_size=hh_size,
            monthly_gross_income=gross,
            state=self.state if self.state != "NATIONAL" else "VA",
            liquid_assets=0.0,
            extra={"offset_pct": offset, "threshold_type": "income_limit"},
        )

    def _build_case(self, profile: USHouseholdProfile, seed: int | None, index: int) -> TestCase:
        t = self.source.thresholds()
        limits = t.by_household_size(min(profile.household_size, 8))
        is_eligible, reason = self.source.is_eligible(
            household_size=profile.household_size,
            gross_income=profile.monthly_gross_income,
        )
        trace = self._build_trace(profile, limits, is_eligible)
        offset = profile.extra.get("offset_pct", 0.0)
        difficulty = Difficulty.HARD if abs(offset) <= 0.01 else Difficulty.EASY
        case_id = (
            f"liheap.us.{self.state.lower()}.eligibility.income_limit"
            f".{'at' if offset == 0 else 'above' if offset > 0 else 'below'}_limit"
            f".{'eligible' if is_eligible else 'ineligible'}"
            f".hh{profile.household_size}.{index:04d}"
        )
        return TestCase(
            case_id=case_id,
            program=Program.LIHEAP.value,
            jurisdiction=f"us.{self.state.lower()}",
            task_type=TaskType.ELIGIBILITY,
            difficulty=difficulty,
            scenario=ScenarioBlock(
                summary=profile.natural_language_summary("liheap"),
                **{k: v for k, v in profile.to_scenario_fields().items()},
            ),
            task=TaskBlock(instruction=_TASK_INSTRUCTION),
            expected_outcome="eligible" if is_eligible else "ineligible",
            expected_answer=reason,
            rationale_trace=trace,
            variation_tags=["income_limit", f"hh{profile.household_size}"],
            source_citations=[t.source],
            seed=seed,
        )

    def _build_trace(self, profile, limits, is_eligible) -> RationaleTrace:
        t = self.source.thresholds()
        income_pass = profile.monthly_gross_income <= limits.gross_monthly
        steps = [
            ReasoningStep(
                step_number=1,
                title="Check income limit",
                rule_applied="42 USC 8624(b)(2)",
                inputs={
                    "gross_income": profile.monthly_gross_income,
                    "income_limit": limits.gross_monthly,
                    "pct_smi": t.extra.get("income_limit_pct_smi", 60),
                },
                computation=(
                    f"${profile.monthly_gross_income:,.2f} "
                    f"{'<=' if income_pass else '>'} "
                    f"${limits.gross_monthly:,.2f} "
                    f"({t.extra.get('income_limit_pct_smi', 60)}% SMI)"
                ),
                result="PASS" if income_pass else "FAIL",
                is_determinative=not income_pass,
            ),
            ReasoningStep(
                step_number=2,
                title="Eligibility determination",
                rule_applied="42 USC 8624",
                inputs={},
                computation="All tests evaluated.",
                result="ELIGIBLE" if is_eligible else "INELIGIBLE",
                is_determinative=True,
            ),
        ]
        return RationaleTrace(
            steps=steps,
            conclusion=(
                f"{'ELIGIBLE' if is_eligible else 'INELIGIBLE'}. "
                f"Gross income ${profile.monthly_gross_income:,.2f} "
                f"{'does not exceed' if is_eligible else 'exceeds'} "
                f"the ${limits.gross_monthly:,.2f} limit."
            ),
            policy_basis=[
                PolicyCitation(
                    document="42 USC 8624 — LIHEAP State Plan Requirements",
                    section="42 USC 8624(b)(2)",
                    year=self.fiscal_year,
                )
            ],
        )
```

---

## Step 4 — Register a preset

Add your program to `govsynth/presets.py`:

```python
"liheap.national": PresetConfig(
    program="liheap",
    source_class="govsynth.sources.us.liheap.LIHEAPSource",
    source_kwargs={"fiscal_year": 2026},
    generator_class="govsynth.generators.liheap_eligibility.LIHEAPEligibilityGenerator",
    generator_kwargs={"fiscal_year": 2026, "state": "national"},
    profile_strategy="edge_saturated",
    description="LIHEAP FY2026 national income eligibility (60% SMI)",
),
```

Also add `LIHEAP = "liheap"` to the `Program` enum in `govsynth/models/enums.py`
if it isn't already there (it is — it's listed but not yet wired up).

---

## Step 5 — Generate test cases

```python
from govsynth import Pipeline

pipeline = Pipeline.from_preset("liheap.national")
cases = pipeline.generate(n=50, seed=42)
pipeline.save(cases, "./output/liheap/", formats=["yaml", "jsonl", "csv"])

print(f"Generated {len(cases)} cases")
print(cases[0].case_id)
print(cases[0].rationale_trace.to_plain_text())
```

Or directly without a preset:

```python
from govsynth.generators.liheap_eligibility import LIHEAPEligibilityGenerator

gen = LIHEAPEligibilityGenerator(fiscal_year=2026, state="VA")
cases = gen.generate(n=20, seed=42)
```

---

## What counts as a "policy document"?

Any official source that defines eligibility rules and thresholds:

| Source type | Examples |
|---|---|
| Code of Federal Regulations | 7 CFR Part 273 (SNAP), 42 USC 8624 (LIHEAP) |
| Agency COLA/threshold memos | USDA FNS SNAP COLA Memo (annual, August) |
| Federal Register notices | WIC Income Eligibility Guidelines (annual, March) |
| State agency handbooks | Virginia DSS SNAP Policy Manual |
| State plan documents | State TANF plans, LIHEAP state plans |

You need at minimum: **an income (or asset) limit table keyed by household size**
and **a citation to its official source**.

---

## Optional: Add seed policy text

If you want rationale traces to reference the actual regulatory language, add
excerpts from the policy document to `data/seeds/us/{program}/`. These are plain
text files — not full documents, just the specific sections relevant to eligibility.

```
data/seeds/us/liheap/
  eligibility_rules.txt     ← key CFR/USC sections
  income_limits_fy2026.txt  ← the threshold table as text
```

Load them in your source connector:

```python
def fetch_policy_summary(self) -> str:
    seed_text = self._load_seed_text("us", "liheap", "eligibility_rules.txt")
    return seed_text or "LIHEAP: income ≤ 60% SMI."
```

---

## Checklist

- [ ] Threshold JSON in `data/thresholds/` with `verification_status` set correctly
- [ ] `source_url` present in `_metadata` pointing to the official document
- [ ] `DataSource` subclass with `fetch_thresholds()`, `fetch_policy_summary()`, `is_eligible()`
- [ ] Generator with `generate()`, `_build_case()`, `_build_trace()`
- [ ] Rationale trace has **at least 2 steps** (validation will reject fewer)
- [ ] `source_citations` has **at least 1 entry** (validation will reject empty)
- [ ] Preset registered in `govsynth/presets.py`
- [ ] Program added to `Program` enum if new
- [ ] `python scripts/verify_thresholds.py` shows no unverified entries
- [ ] Unit test in `tests/unit/test_{program}_source.py`
