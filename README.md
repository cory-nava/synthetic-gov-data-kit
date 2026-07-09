# synthetic-gov-data-kit

> Generate structured synthetic US government benefits data for LLM reasoning evaluation.



[![CI](https://github.com/ctrimm/synthetic-gov-data-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/ctrimm/synthetic-gov-data-kit/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)


---

## What It Does

`synthetic-gov-data-kit` generates realistic, policy-grounded test cases for evaluating how well
LLMs reason about US government benefits programs. Every case includes:

- A **synthetic citizen scenario** (household, income, assets, circumstances)
- A **task instruction** (determine eligibility, answer a policy question, etc.)
- The **expected answer** and outcome
- A **step-by-step rationale trace** — the correct reasoning chain, citing actual CFR sections

This last piece is the key differentiator. You can evaluate not just *what* a model answers,
but *whether it reasons correctly* — did it apply the right rules, in the right order, with
the right computations?

## Use Cases

| Who | What they use it for |
|-----|----------------------|
| **AI researchers** | Benchmark LLMs on structured policy reasoning; measure rationale quality, not just answer accuracy |
| **Civic tech teams** | Generate acceptance test suites before shipping a benefits chatbot or eligibility screener |
| **Government agencies** | Stress-test AI tools against edge cases (income at threshold, elderly households, asset tests) before procurement or deployment |
| **ML engineers** | Create fine-tuning or RLHF datasets grounded in real policy rules, without touching real applicant data |
| **Policy analysts** | Verify that an AI system applies the correct CFR rules in the correct order — not just gets the right answer by coincidence |

See [docs/claude-code-integration.md](docs/claude-code-integration.md) for worked examples
with Claude Code.

## Supported Programs (v0.1)

| Program | Agency | Presets | Status |
|---|---|---|---|
| [SNAP (Food Stamps)](docs/programs/snap.md) | USDA/FNS | `snap.va`, `snap.ca`, `snap.tx`, `snap.md` | Stable |
| WIC | USDA/FNS | `wic.national` | Stable |
| Medicaid | CMS/HHS | — | In progress |

## Quickstart

**Install from source** (PyPI release coming soon):

```bash
git clone https://github.com/ctrimm/synthetic-gov-data-kit
cd synthetic-gov-data-kit
pip install -e ".[dev]"
```

**Python API:**

```python
from govsynth import Pipeline

# Generate 100 SNAP eligibility test cases for Virginia
pipeline = Pipeline.from_preset("snap.va")
cases = pipeline.generate(n=100, seed=42)

# Save to multiple formats
pipeline.save(cases, "./output/", formats=["yaml", "jsonl"])

print(cases[0].case_id)
# snap.va.eligibility.gross_income_at_limit.hh3

print(cases[0].expected_outcome)
# eligible

print(cases[0].rationale_trace.steps[0].rule_applied)
# 7 CFR 273.9(a)(1)
```

**CLI:**

```bash
# List available presets
govsynth list-presets

# Generate 50 SNAP/Virginia cases to a directory
govsynth generate snap.va --n 50 --seed 42 --output ./output/

# Use a specific profile strategy
govsynth generate snap.va --n 100 --seed 42 --profile-strategy realistic --output ./output/

# Stream JSONL directly to stdout (pipe-friendly)
govsynth generate snap.va --n 100 --seed 42 --format jsonl | jq '.case_id'

# Generate across multiple presets at once
govsynth batch --preset snap.va --preset wic.national --n 100 --output ./output/

# Validate generated files
govsynth validate ./output/snap.va.eligibility.gross_income_at_limit.hh3.yaml

# Inspect a single case
govsynth show ./output/snap.va.eligibility.gross_income_at_limit.hh3.yaml

# Check that bundled policy thresholds are up to date
govsynth verify-thresholds --program snap
```

## Core Concepts

### Presets

Presets bundle a data source, generator config, and profile strategy into one name:

```python
Pipeline.from_preset("snap.va")     # Virginia SNAP FY2026 — strict asset test
Pipeline.from_preset("snap.ca")     # California SNAP FY2026 — BBCE (no asset test)
Pipeline.from_preset("snap.tx")     # Texas SNAP FY2026 — strict asset test
Pipeline.from_preset("snap.md")     # Maryland SNAP FY2026 — BBCE
Pipeline.from_preset("wic.national")  # WIC FY2026 national (185% FPL)
```

Run `govsynth list-presets` to see all currently registered presets.

### Profile Strategies

Control how synthetic citizen profiles are sampled:

```python
pipeline = Pipeline.from_preset("snap.va", profile_strategy="edge_saturated")
# 60%+ of cases land at income/asset threshold boundaries — where models fail most

pipeline = Pipeline.from_preset("snap.va", profile_strategy="realistic")
# Sampled from Census ACS state distributions — representative of real applicants
# Requires census data: run `govsynth refresh-census-data --state VA` first

pipeline = Pipeline.from_preset("snap.va", profile_strategy="uniform")
# National-level approximations, no census data required
```

Census data for Virginia is pre-bundled. For other states:
```bash
govsynth refresh-census-data --state TX   # fetch one state (~6 API calls)
govsynth refresh-census-data --all        # fetch all 50 states + DC (~306 API calls)
```

### Batch Generation

**CLI (recommended):**

```bash
govsynth batch \
  --preset snap.va --preset snap.ca --preset snap.tx \
  --preset wic.national \
  --n 150 --seed 42 \
  --output ./suite-v1/
```

**Python API:**

```python
from govsynth import BatchPipeline

batch = BatchPipeline.from_presets(["snap.va", "snap.ca", "snap.tx", "wic.national"])
cases = batch.generate(n_per_pipeline=150, seed=42)
batch.save(cases, "./suite-v1/", formats=["yaml", "jsonl"])
```

### Output Formats

```python
pipeline.save(cases, "./output/", formats=["yaml"])    # One .yaml file per case
pipeline.save(cases, "./output/", formats=["jsonl"])   # Fine-tuning (messages format)
pipeline.save(cases, "./output/", formats=["csv"])     # Review/inspection
pipeline.save(cases, "./output/", formats=["yaml", "jsonl", "csv"])  # All three
```

## Example Output

```yaml
case_id: snap.va.eligibility.gross_income_at_limit.hh3
program: snap
jurisdiction: us.va
task_type: eligibility
difficulty: hard

scenario:
  summary: >
    Maria is a 34-year-old single parent in Arlington, VA with two children (ages 5 and 8).
    Her gross monthly income from part-time employment is $2,311. She has $1,800 in savings.
    No household member is elderly or disabled. She is a US citizen.
  household_size: 3
  monthly_gross_income: 2311
  state: VA

task:
  instruction: >
    Determine whether this household is eligible for SNAP benefits.
    Show your reasoning step by step, citing relevant regulations.

expected_outcome: eligible
expected_answer: >
  This household is eligible for SNAP. At $2,311/month gross income, the household is
  exactly at (not over) the 130% FPL gross income limit for a 3-person household in FY2025.
  After the 20% earned income deduction and $198 standard deduction, net income is $1,651,
  below the $1,776 net limit. Assets of $1,800 are below the $2,750 limit. Estimated
  monthly benefit: approximately $478.

rationale_trace:
  steps:
    - step: 1
      rule: "7 CFR 273.9(a)(1)"
      description: Identify gross income limit for household size
      computation: "3-person household → $2,311/month (130% FPL, FY2025)"
      result: gross_limit = $2,311
    - step: 2
      rule: "7 CFR 273.9(a)(1)"
      description: Compare gross income to limit
      computation: "$2,311 <= $2,311 — AT limit, passes gross test"
      result: PASS
    - step: 3
      rule: "7 CFR 273.9(c)(1),(c)(2)"
      description: Calculate net income after deductions
      computation: "2311 - (2311 × 0.20) - 198 = $1,651"
      result: net_income = $1,651
    - step: 4
      rule: "7 CFR 273.9(a)(2)"
      description: Compare net income to net limit
      computation: "$1,651 <= $1,776 — PASS"
      result: PASS
    - step: 5
      rule: "7 CFR 273.8(b)(1)"
      description: Check asset limit
      computation: "$1,800 <= $2,750 — PASS"
      result: PASS
  conclusion: "ELIGIBLE. All three tests passed: gross income, net income, assets."
  policy_basis:
    - "7 CFR Part 273 (2025)"
    - "FNS SNAP Income and Resource Limits FY2025"

variation_tags: [income_threshold, gross_income_at_limit, single_parent]
difficulty: hard
```

## CLI Reference

| Command | Description |
|---|---|
| `govsynth list-presets [--json]` | List all registered presets |
| `govsynth generate <preset> [--profile-strategy <s>]` | Generate cases for one preset |
| `govsynth batch --preset ... --output` | Generate across multiple presets |
| `govsynth validate <file>` | Validate a generated output file |
| `govsynth show <file> [case_id]` | Pretty-print a single case |
| `govsynth verify-thresholds [--program]` | Check bundled threshold data is up to date |
| `govsynth refresh-census-data [--state] [--all]` | Fetch ACS Census distributions from Census Bureau API |
| `govsynth parse-policy <file>` | *(coming soon)* Extract thresholds from a policy PDF |

All commands support `--json` for machine-readable output and are safe to use in CI pipelines and agentic workflows. Rich progress and status output always goes to **stderr**; data always goes to **stdout**.

```bash
# Machine-readable output — safe for scripting
govsynth list-presets --json
govsynth verify-thresholds --json
govsynth generate snap.va --n 10 --seed 42 --format jsonl --quiet

# CI usage: exit code 1 if any threshold files are unverified
govsynth verify-thresholds && echo "All thresholds verified"
```

## Notebooks

![Jupyter notebooks screenshot showing SNAP edge case analysis](docs/jupyter_notebooks_screenshot.png)

| Notebook | What it covers |
|---|---|
| [`01_quickstart.ipynb`](notebooks/01_quickstart.ipynb) | End-to-end intro: generate, inspect, export, score |
| [`02_snap_edge_cases.ipynb`](notebooks/02_snap_edge_cases.ipynb) | Deep dive into SNAP threshold boundaries |
| [`03_wic_eligibility.ipynb`](notebooks/03_wic_eligibility.ipynb) | WIC basics: 185% FPL, no asset test, categorical eligibility |
| [`04_wic_edge_cases.ipynb`](notebooks/04_wic_edge_cases.ipynb) | WIC boundary sweep: income offsets, household size sensitivity |
| [`05_realistic_profiles.ipynb`](notebooks/05_realistic_profiles.ipynb) | Census ACS distributions vs. uniform sampling |
| [`06_rationale_evaluation.ipynb`](notebooks/06_rationale_evaluation.ipynb) | Scoring LLM reasoning with `RationaleEvaluator` |
| [`07_batch_pipeline.ipynb`](notebooks/07_batch_pipeline.ipynb) | Building a full CivBench multi-program test suite |
| [`08_multi_state_batch.ipynb`](notebooks/08_multi_state_batch.ipynb) | Batch generation across states with HuggingFace export |
| [`09_cli_workflow.ipynb`](notebooks/09_cli_workflow.ipynb) | Full CLI workflow: generate → validate → show |
| [`10_custom_generator.ipynb`](notebooks/10_custom_generator.ipynb) | Adding a new program (LIHEAP example) |

## Policy Data Sources

All threshold values and policy rules are sourced from official US government publications:

- **SNAP**: [7 CFR Part 273](https://www.ecfr.gov/current/title-7/part-273) + FNS SNAP Handbook
- **WIC**: [7 CFR Part 246](https://www.ecfr.gov/current/title-7/part-246) + FNS WIC Policy
- **Medicaid**: [42 CFR Part 435](https://www.ecfr.gov/current/title-42/part-435) + CMS eligibility guidance
- **Federal Poverty Guidelines**: [HHS ASPE](https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines)

All generated profiles are entirely synthetic. No real applicant data is used or represented.

## Roadmap

- **Policy document parser** — `govsynth parse-policy` stub is available; full PDF/DOCX ingestion coming soon
- **Additional programs** — LIHEAP, CHIP, Section 8/HCV, TANF, SSI/SSDI
- **More task types** — policy Q&A, form completion, agentic multi-step, comparative cross-program
- **More state presets** — currently VA, CA, TX, MD for SNAP; WIC national only
- **Schema validation** — formal JSON schema (`test_case_v1.schema.yaml`) + `validate_against_schema()` utility
- **Evaluation rubrics** — structured per-difficulty scoring rubrics for `RationaleEvaluator`

---

## Bring Your Own Policy

Want to generate test cases from a policy document that isn't built in yet?
See **[docs/bring-your-own-policy.md](docs/bring-your-own-policy.md)** for a
step-by-step walkthrough: threshold JSON format, writing a DataSource connector,
writing a Generator, and registering a preset.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Policy accuracy is paramount — please cite sources
for any threshold values you add or modify.

## License

MIT. See [LICENSE](LICENSE).
