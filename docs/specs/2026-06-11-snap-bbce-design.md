# SNAP Broad-Based Categorical Eligibility (BBCE) — Design Spec

**Date:** 2026-06-11
**Branch:** `feat/snap-bbce-model`
**Status:** Approved for implementation

---

## Problem

SNAP Broad-Based Categorical Eligibility (BBCE) is already referenced in the codebase, but it is
modeled **incompletely**: `govsynth/sources/us/snap.py` treats BBCE as a binary *asset-test waiver*
(`BBCE_STATES` set → `asset_limit_general = None`), while still applying the federal **130% FPL gross
income test**.

Real BBCE does more (verified against the [FNS BBCE chart, Aug 2025](https://www.fns.usda.gov/snap/broad-based-categorical-eligibility)
and [CBPP](https://www.cbpp.org/research/food-assistance/snaps-broad-based-categorical-eligibility-supports-working-families-and-0)):

1. **Raises the gross income limit** — states set it from 130% up to **200% FPL** (statutory ceiling).
   Common values: 130, 160, 165, 175, 185, 200.
2. **Eliminates or raises the asset test** — most states waive it; five impose a `$` cap.
3. Confers eligibility via receipt of a **non-cash TANF/MOE-funded benefit or service**.
4. The **net income test (≤100% FPL) still applies** for benefit issuance (1–2 person categorically
   eligible households still receive at least the minimum benefit).

**Consequence of the current model:** a household at, e.g., 160% FPL in a 200%-limit state is
**ELIGIBLE**, but the current code marks it **INELIGIBLE** on the gross test. The most valuable BBCE
reasoning case cannot be generated today. This is a correctness gap, not just a missing feature.

Additionally, the hardcoded `BBCE_STATES` / `STRICT_ASSET_TEST_STATES` sets are **stale**: they list
TN, UT, WY as BBCE (they are not) and treat TX as strict (TX is actually BBCE at 165% with a $5,000
asset cap).

---

## Goals

- Model BBCE faithfully: per-state gross income limit, asset rule, and the still-binding net test.
- Keep the implementation **isolated** — a dedicated connector, not folded into `SNAPSource`.
- Add a high-value reasoning edge case: eligible-above-130%-FPL (+ adversarial above-state-limit).
- Source all state parameters from versioned `data/`, per the project's data convention.

## Non-Goals (deferred)

- **Temporal cases** (expiring BBCE, mid-certification composition change) — require a multi-period
  scenario abstraction the codebase lacks. Tracked as Group B in `EDGE_CASES.md`; own future spec.
- **Migrating the existing `SNAPSource` BBCE path** to the data table — deliberately out of scope to
  keep blast radius small. Documented as a known follow-up (see "Known follow-ups").

---

## Architecture

Follows the existing `data/thresholds/*.json` → source-connector pattern.

### 1. Data file — `data/thresholds/snap_bbce_fy2026.json`

Per-state BBCE parameters, sourced from the FNS BBCE chart (Aug 2025), cross-checked against CBPP.

```jsonc
{
  "_metadata": {
    "program": "snap",
    "fiscal_year": 2026,
    "source": "USDA FNS SNAP BBCE States Chart (August 2025), cross-checked vs CBPP",
    "source_url": "https://www.fns.usda.gov/snap/broad-based-categorical-eligibility",
    "cfr_reference": "7 CFR 273.2(j)(2)(ii)",
    "statutory_ceiling_pct_fpl": 200,
    "verification_status": "verified",
    "verification_note": "Per-state values from FNS BBCE States Chart Aug 2025..."
  },
  "states": {
    "CA": { "bbce": true, "gross_income_limit_pct_fpl": 200, "asset_limit": null,
            "net_income_test_applies": true,
            "conferring_benefit": "CalFresh TANF/MOE-funded informational service/referral",
            "applies_to": "all households",
            "verification_status": "verified",
            "source_note": "FNS BBCE chart Aug 2025" },
    "TX": { "bbce": true, "gross_income_limit_pct_fpl": 165, "asset_limit": 5000, ... },
    "TN": { "bbce": false, "gross_income_limit_pct_fpl": 130, "asset_limit": 3000, ... }
    // ... all 50 states + DC; GU/VI optional
  },
  "federal_default": { "bbce": false, "gross_income_limit_pct_fpl": 130,
                       "asset_limit": 3000, "net_income_test_applies": true }
}
```

- All 50 states + DC present. Unlisted jurisdictions fall back to `federal_default`.
- `asset_limit`: `null` = test waived; a number = `$` cap; non-BBCE = 3000 (federal general).
- Two-tier states (AR, NY) encode the headline (broader) tier; `source_note` records the second tier.

### 2. Connector — `govsynth/sources/us/snap_bbce.py`

```python
class SNAPBBCESource(SNAPSource):
    """SNAP eligibility WITH Broad-Based Categorical Eligibility applied.

    Extends SNAPSource (reusing its income-limit table, deductions, and net-income
    calculation) and layers the state-specific BBCE rules from
    data/thresholds/snap_bbce_{fy}.json on top. SNAPSource itself is left unchanged.
    """
```

Responsibilities:
- Load the BBCE row for `self.state` (or `federal_default`).
- `is_bbce` / `bbce_params` accessors.
- `effective_gross_limit(household_size)` — derived precisely from the FPL annual figure using
  SNAP's published rounding: `ceil(annual_fpl * pct / 100 / 12)`, region-aware (48/AK/HI). This
  matches the existing `gross_income_165pct` row (`ceil(15650*1.65/12) = 2152`).
- `is_eligible(...)` override: apply the **raised** gross limit, **still apply** the 100% net test,
  apply the state asset rule (waived/cap), and floor 1–2 person categorically-eligible households at
  the minimum benefit.
- Reuses `SNAPSource.calculate_net_income`, `get_standard_deduction`, base threshold loading.

`snap.py` is **not modified** (isolation requirement).

### 3. Edge case — `_build_bbce_expanded_income_case` (7th special-population type)

Added to the builder rotation in `generators/snap_eligibility.py`. Uses a `SNAPBBCESource` for the
generator's state (only when that state is BBCE with a limit > 130%; otherwise picks a representative
BBCE state, e.g. CA, so the case is always meaningful).

- **Primary:** household gross income placed **between 130% and the state BBCE limit** → **ELIGIBLE**
  (would be INELIGIBLE federally). Net income kept ≤ 100% FPL so the household truly qualifies.
- **Adversarial sibling:** gross **above** the state BBCE limit → **INELIGIBLE**.
- `RationaleTrace`: (1) identify BBCE conferral via TANF/MOE non-cash benefit, (2) apply raised gross
  limit — show federal 130% would fail but state limit passes, (3) net income test (still binds),
  (4) asset rule (waived or `$` cap). Cites `7 CFR 273.2(j)(2)(ii)`.
- Tagged `bbce_expanded_gross_limit`.

### 4. Folded-in cheap items

- **Minimum-benefit floor**: 1–2 person categorically eligible households receive at least
  `minimum_benefit` ($24) even when the calculated benefit rounds to $0.
- **`$`-cap asset states**: handled by the `asset_limit` number path in `is_eligible`.

### 5. Supporting updates

- `data/seeds/us/snap/eligibility_rules_fy2026.txt`: expand the BBCE section (130–200% range,
  conferral mechanism, net test still binds).
- `EDGE_CASES.md` + `docs/programs/snap.md`: document the new case and corrected BBCE semantics.
- `presets.py`: fix `snap.ca`/`snap.md` descriptions; add a `$`-asset-cap example (`snap.tx` note or
  a new BBCE preset).

---

## Testing

- `tests/unit/test_snap_bbce_source.py`: effective gross limit math (200%/165% vs FPL),
  asset rule (waived vs `$` cap), federal fallback for non-BBCE/unlisted states, net test still binds
  above 100% FPL, min-benefit floor. `seed=42`.
- `tests/unit/test_snap_edge_cases.py`: add the 7th case — eligible 130–200% band + adversarial
  above-limit; assert `bbce_expanded_gross_limit` tag, rationale cites `273.2(j)(2)`, and the trace
  shows the federal-130%-would-fail contrast.
- Existing `SNAPSource` tests must remain green (snap.py unchanged).

## Data-quality / verification

- Every state row carries `verification_status` + `source_note`. All values currently `verified`
  against the FNS Aug 2025 chart.
- `net_income_test_applies` set `true` everywhere; CBPP notes BBCE *technically* permits states to
  raise/eliminate the net limit, but it is near-universally applied — recorded as a modeling
  assumption in the data file notes.

## Known follow-ups (out of scope here)

1. Migrate `SNAPSource.BBCE_STATES`/`STRICT_ASSET_TEST_STATES` to read the new data file, retiring the
   stale hardcoded sets (fixes TX/TN/UT/WY misclassification in the base connector).
2. Temporal scenario model → unlocks the three Group B cases.
3. Enrich `conferring_benefit` with exact per-state program names.
