# SNAP Edge Cases

This file documents real-world SNAP eligibility edge cases that LLMs commonly misapply. Each case
identifies the relevant CFR citation, the policy rule that models frequently get wrong, the correct
expected outcome, and a concrete example. Cases in Group A are implemented in the current release.
Cases in Group B are planned for a future release once the required modeling infrastructure exists.

---

## Group A — Implemented in this release

### 1. Homeless shelter deduction

**CFR citation:** 7 CFR 273.9(c)(6)

**Policy rule:** Homeless households receive a flat **$198.99/month** deduction in place of the
excess shelter deduction. These two deductions are mutually exclusive — the homeless deduction is
not an uncapped version of the excess shelter deduction. A household qualifying for the homeless
deduction does not also receive an excess shelter calculation.

**Expected outcome:** Varies by income after the flat deduction is applied.

**Why models fail:** Models apply the standard excess shelter deduction calculation (actual shelter
costs minus 50% of net income) instead of substituting the flat homeless deduction, often resulting
in a different (usually lower) deduction amount.

**Example:** A single-member homeless household has $900/month gross income. After the standard
deductions the net income is $600. The homeless deduction of $198.99 brings net income to $401.01.
The excess shelter formula must not be applied — $198.99 is the final deduction for shelter costs.

---

### 2. Student exclusion

**CFR citation:** 7 CFR 273.5(a), 7 CFR 273.5(b)

**Policy rule:** A student enrolled at least half-time at an institution of higher education is
**ineligible for SNAP** unless they meet one of the statutory exceptions in 273.5(b): working 20 or
more hours per week, caring for a dependent child under age 6, receiving TANF, or participating in
a state or federally funded work-study program. The student status check fires **before** the income
test — income level is irrelevant to this determination.

**Expected outcome:** INELIGIBLE for students who do not meet a 273.5(b) exception, regardless of
income.

**Why models fail:** Models skip the student status check and proceed directly to the income test.
A student with income below the gross limit incorrectly passes as eligible.

**Example:** A 20-year-old enrolled half-time at a community college earns $800/month part-time
(under 20 hours/week) and lives alone. Gross income is well below the 1-person limit. Despite
being income-eligible on paper, the household is **ineligible** because the student does not meet
any exception under 273.5(b).

---

### 3. Boarder/lodger income

**CFR citation:** 7 CFR 273.1(b)(7)

**Policy rule:** Only the **profit portion** of board payments counts as income. Profit is defined
as total board payment minus the actual cost of food and housing provided to the boarder. If the
household receives $800/month for room and board and the actual food and housing costs attributable
to the boarder are $600/month, only **$200** counts as income.

**Expected outcome:** Varies — households are often eligible when only the profit is counted rather
than the full payment.

**Why models fail:** Models count the full board payment as household income instead of netting out
the cost of services provided, inflating income and potentially causing an incorrect ineligibility
finding.

**Example:** A household receives $800/month from a boarder. The household spends $300 on
additional groceries and $300 on the boarder's share of rent and utilities, totaling $600 in actual
costs. Only $200 (the profit) is countable income for SNAP purposes.

---

### 4. Migrant/seasonal worker income averaging

**CFR citation:** 7 CFR 273.10(c)(3)

**Policy rule:** For seasonal or migrant workers, income is **averaged over the anticipated work
period** rather than taken as a monthly snapshot. A worker who earns $12,000 over a 4-month season
has an averaged monthly income of $3,000 — even during months when they are between jobs and
currently earning $0.

**Expected outcome:** Varies — income averaging may make a household eligible during peak earning
months or ineligible during off-season months compared to a snapshot approach.

**Why models fail:** Models use current-month income as reported, treating an off-season $0 income
month as the relevant figure and returning an incorrect eligibility finding.

**Example:** A migrant farmworker earns $15,000 over a 5-month harvest season (May–September) and
earns nothing October–April. Applying in November, the household's countable income is
$15,000 / 5 = $3,000/month, not $0/month.

---

### 5. Mixed immigration status

**CFR citation:** 7 CFR 273.4(c)(3)

**Policy rule:** Ineligible (non-qualified alien) household members are **excluded from household
size** when looking up the applicable income limit, but their **income counts in full**. A 4-person
household with one ineligible member uses the **3-person** income limit, but all four people's
income is included in the income test.

**Expected outcome:** Varies — using tighter (smaller household) limits while counting full income
can cause ineligibility even when a same-size fully eligible household would qualify.

**Why models fail:** Models either (a) prorate the ineligible member's income — which is the rule
for sponsored noncitizens under 273.11(c)(3), not for non-qualified aliens — or (b) include the
ineligible member in the household size when looking up limits, producing an incorrect result in
either direction.

**Example:** A household of 4 includes one non-qualified alien. The household's combined gross
income is $2,500/month. The applicable limit is the **3-person** gross monthly limit ($2,311 in
FY2025), not the 4-person limit ($2,790). The full $2,500 is counted. The household is ineligible
because $2,500 exceeds the 3-person limit.

---

### 6. Categorical eligibility (TANF/SSI)

**CFR citation:** 7 CFR 273.2(j)(2); 7 CFR 273.11(c)

**Policy rule:** Households containing a TANF recipient are **categorically eligible** for SNAP —
the income and resource tests are skipped entirely. SSI recipients are categorically eligible under
273.11(c). A household that would otherwise fail the gross income test is still **eligible** if any
member receives TANF or SSI.

**Expected outcome:** ELIGIBLE — income test is not applied.

**Why models fail:** Models run the income test regardless of TANF/SSI status and return ineligible
for above-limit households, ignoring the categorical eligibility override.

**Example:** A 3-person household has gross monthly income of $3,200 — above the FY2025 3-person
gross limit of $2,311. One household member receives TANF. Because categorical eligibility applies,
the income test is bypassed and the household is **eligible** for SNAP.

---

### 7. Broad-based categorical eligibility (BBCE) — expanded gross income limit

**CFR citation:** 7 CFR 273.2(j)(2)(ii)

**Policy rule:** A state that has adopted BBCE confers categorical eligibility on households that
receive a **non-cash TANF/MOE-funded benefit or service** (e.g. an informational brochure or
referral). Adopting BBCE lets the state **raise the gross income limit from the federal 130% FPL up
to the statutory ceiling of 200% FPL** (common values: 130, 165, 185, 200) and **eliminate or raise
the asset test**. Crucially, BBCE raises the **gross** limit but does **not** waive the **net income
test** — the household must still have net income ≤ 100% FPL to receive a benefit. The applicable
gross limit is the **state's** BBCE percentage, not the federal 130%.

**Expected outcome:** A household with gross income **between 130% FPL and the state's BBCE limit**
that also has net income ≤ 100% FPL is **ELIGIBLE** — even though it would be **INELIGIBLE** under
the federal gross test. A household with gross income **above** the state's BBCE limit is
**INELIGIBLE**.

**Why models fail:** Models apply the federal 130% FPL gross limit in a BBCE state and incorrectly
reject a household that the state's higher limit would accept — or, conversely, assume BBCE waives
the net income test and incorrectly accept a household whose net income exceeds 100% FPL.

**Example:** A 3-person household in Virginia (BBCE at 200% FPL) earns $3,000/month gross — above the
federal 130% limit of $2,888 but below Virginia's 200% limit of $4,442. After a $600/month
dependent-care deduction its net income is below the $2,221 net limit. The household is **eligible**
under BBCE. The same household earning $4,500/month gross would exceed Virginia's BBCE limit and be
**ineligible**.

**Data note:** Per-state BBCE parameters live in `data/thresholds/snap_bbce_fy2026.json` (sourced
from the USDA FNS BBCE States Chart, August 2025). This case — and the SNAP generator's main
threshold path — are generated via the `SNAPBBCESource` connector
(`govsynth/sources/us/snap_bbce.py`), which derives each state's gross limit from the bundled FPL
table. `SNAPSource` models only the federal baseline; BBCE classification is single-sourced from the
data table (exported as `bbce_states(fiscal_year)` / `BBCE_STATES`).

---

## Group B — Planned (not yet implemented)

### 1. Fluctuating/irregular income

**CFR citation:** 7 CFR 273.10(c)

**Status:** Planned — requires temporal scenario model

**Description:** When a household's income fluctuates significantly from month to month (e.g.,
gig economy workers, commission-based earnings, sporadic self-employment), income must be averaged
over a representative period. The complexity is that determining what constitutes a "representative
period" requires caseworker judgment informed by the household's income history — it is not a
simple formula. Implementing this case requires a model that can generate realistic multi-month
income histories and apply the averaging determination rules.

---

### 2. Mid-certification household composition change

**CFR citation:** 7 CFR 273.12

**Status:** Planned — requires temporal scenario model

**Description:** When a household adds or loses a member during an active certification period, the
household is required to report the change and the eligibility determination must be updated. This
case requires modeling an event sequence: initial certification, a mid-period composition change
event, and a re-determination using updated household size and income. The test must verify that
the correct limits (based on the new household size) are applied from the correct effective date.

---

### 3. Expiring categorical eligibility

**CFR citation:** 7 CFR 273.2(j)(2)

**Status:** Planned — requires temporal scenario model

**Description:** A household certified under categorical eligibility because a member receives TANF
may have the TANF benefit expire before the SNAP certification period ends. When TANF ends, the
categorical eligibility basis is removed and the household must be re-evaluated against the standard
income and resource tests. This case requires a time-bounded eligibility state model that can
represent the transition from categorically eligible to subject to standard rules at a specific
point in time.
