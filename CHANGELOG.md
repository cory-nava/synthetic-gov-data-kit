# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- `SNAPBBCESource` (`govsynth/sources/us/snap_bbce.py`) — faithful Broad-Based Categorical Eligibility model: per-state gross income limit (130–200% FPL, derived from the FPL table), waived/capped asset rule, with the net income test still enforced (7 CFR 273.2(j)(2)(ii))
- `data/thresholds/snap_bbce_fy2026.json` — per-state BBCE parameters for all 50 states + DC + GU/VI, sourced from the USDA FNS BBCE States Chart (August 2025), cross-checked against CBPP, with per-row verification status
- Seventh SNAP edge case — `bbce_expanded_gross_limit`: a household at 130–200% FPL gross that is eligible under BBCE but ineligible federally (plus an adversarial above-limit variant)
- `bbce_states(fiscal_year)` helper and data-backed `BBCE_STATES` constant exported from `snap_bbce`
- `docs/claude-code-integration.md` — examples of using govsynth within Claude Code agentic workflows
- `docs/cli-integration.md` — guide to adding govsynth CLI access to Claude Code and other AI apps
- `docs/open-source-health.md` — open source checklist and project health reference
- `CODE_OF_CONDUCT.md` — Contributor Covenant v2.1-based community standards
- `SECURITY.md` — vulnerability reporting policy and policy data integrity guidance
- `.github/ISSUE_TEMPLATE/` — structured templates for bug reports, feature requests, and threshold updates
- `.github/PULL_REQUEST_TEMPLATE.md` — standardized PR checklist with policy data verification steps
- 8 Jupyter notebooks covering quickstart, SNAP/WIC edge cases, realistic profiles, rationale evaluation,
  multi-state batch generation, CLI workflow, and custom generator tutorial
- `govsynth refresh-census-data` CLI command — fetches ACS 5-year estimates from Census Bureau API
- `CensusDataSource` and `CensusDistribution` — state-level ACS profile sampling distributions
- `USHouseholdProfile.random(strategy="realistic")` — Census-backed profile generation
- `--profile-strategy` / `-s` flag on `govsynth generate`
- Bundled Virginia ACS 2022 census distribution (`data/census/va.json`)
- Six SNAP special-population edge case types: homeless shelter deduction (7 CFR 273.9(c)(6)), student exclusion (7 CFR 273.5(a)+(b)), boarder income proration (7 CFR 273.1(b)(7)), migrant/seasonal income averaging (7 CFR 273.10(c)(3)), mixed immigration status HH size reduction (7 CFR 273.4(c)(3)), categorical eligibility via TANF/SSI (7 CFR 273.2(j)(2), 273.11(c))
- `EDGE_CASES.md` — documents all implemented and planned SNAP edge case types with CFR citations
- Special population fields on `USHouseholdProfile`: `is_homeless`, `student_status`, `is_boarder`, `is_migrant_worker`, `has_ineligible_members`, `ineligible_member_count`
- `is_homeless` parameter on `SNAPSource.calculate_net_income()` for homeless shelter deduction

### Changed
- `SNAPSource` reduced to the federal baseline (130% FPL gross, 100% FPL net, $3,000/$4,500 assets). The stale hardcoded `BBCE_STATES`/`STRICT_ASSET_TEST_STATES` sets were removed; BBCE is now modeled exclusively by `SNAPBBCESource`, which the SNAP generator uses for the main threshold path and the BBCE edge case. **Behavior change:** SNAP cases for BBCE states now apply the state's raised gross limit and correct asset rule (e.g. TX is now correctly BBCE at 165% FPL / $5,000 cap rather than federal strict).

### Fixed
- Corrected stale BBCE state classification: TN/UT/WY are not BBCE; TX/VA are (per FNS Aug 2025)
- `LICENSE` — added full MIT license text with copyright year and holder
- `pyproject.toml` — replaced `your-org` placeholder URLs with actual repository paths
- `CONTRIBUTING.md` — corrected clone URL placeholder
- `README.md` — corrected install instructions (source install), preset list, profile strategies,
  and batch generation Python API example

---

## [0.1.0] — 2026-03-25

### Added
- Initial release of `synthetic-gov-data-kit`
- SNAP eligibility generator (`snap.va`, `snap.ca`, `snap.tx`, `snap.md` presets)
- WIC eligibility generator (`wic.national` preset)
- Medicaid source connector (data layer only — no presets yet)
- `USHouseholdProfile` with `edge_saturated` and `uniform` profile strategies
- `RationaleTrace` data model — step-by-step policy reasoning chain with CFR citations
- Output formatters: YAML, JSONL, CSV, HuggingFace datasets
- CLI (`govsynth`) with commands: `generate`, `batch`, `list-presets`, `validate`, `show`,
  `verify-thresholds`, `parse-policy` (stub)
- `Pipeline` and `BatchPipeline` orchestration
- Verified FY2026 SNAP and WIC threshold tables (USDA FNS)
- Verified FY2025 HHS Federal Poverty Guidelines
- `docs/bring-your-own-policy.md` — guide for adding custom program connectors
