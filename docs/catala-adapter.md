# Catala Adapter

The Catala adapter lets you generate synthetic test cases from a
[Catala](https://catala-lang.org) ruleset instead of a hand-written
`data/thresholds/*.json` file plus a Python `is_eligible()` method. The
imported `.catala_en`/`.catala_fr` file is the **full source of truth** for
both thresholds and eligibility logic — govsynth samples a synthetic
household profile, hands it to the ruleset, and turns the ruleset's own
output (and explanation trace, when available) into a `TestCase`.

This is useful when a program's eligibility rules already exist as a
verified Catala encoding (e.g. from a legal-tech or benefits-modernization
project) and you want to generate reasoning-grounded evaluation cases
against that encoding directly, without re-deriving the thresholds by hand.

---

## Requirements

The adapter shells out to the `catala` CLI. It is **not** a bundled
dependency — install it separately via [opam](https://opam.ocaml.org)
(OCaml's package manager):

```bash
opam install catala
```

See [catala-lang.org](https://catala-lang.org) for full installation
instructions. `govsynth` only requires that a `catala` executable is
reachable on `PATH` (or that you pass an explicit `--binary`/`binary=`
path) at the time you run the adapter — no Catala-specific Python
package is needed.

> **Caveat on CLI behavior.** `govsynth/sources/catala/runtime.py` was
> implemented against Catala's documented CLI surface
> (`catala interpret <file> --scope=<Name> --format=json [--trace=<path>]`,
> `catala json-schema <file> --scope=<Name>`) but has not been exercised
> against a live `catala` binary in this environment. Output parsing is
> deliberately permissive (JSON first, human-readable `[RESULT] var = value`
> lines as a fallback) so it degrades gracefully rather than assuming one
> rigid shape. If you hit a `CatalaRuntimeError`, its `command`/`output`
> attributes show exactly what was run and what came back — run the same
> command by hand (`catala interpret --help`, `catala json-schema --help`)
> against your installed version to diagnose a flag or format mismatch, and
> please report it.

---

## How it works

```
your_ruleset.catala_en
      ↓
 CatalaRuleset.load()      ← introspects the scope's input/output schema
      ↓                      via `catala json-schema` (once)
 CatalaEligibilityGenerator ← samples synthetic profiles, runs each one
      ↓                      through `catala interpret`, builds TestCases
 Pipeline                   ← orchestrates, formats, saves
      ↓
 test_cases.yaml / .jsonl / .csv
```

1. **`CatalaRuleset.load(path, scope, program_name=...)`** locates your
   ruleset file and asks `catala json-schema` for the declared scope's
   input and output fields. This introspection happens once, not per
   generated case.
2. **Field mapping.** Each declared Catala input field is resolved to a
   `USHouseholdProfile` field by exact name match first, then by an
   explicit `field_mapping={"catala_field": "profile_field"}` override.
   Anything left unresolved raises `CatalaMappingError` naming exactly
   which fields need a mapping — the adapter never guesses a mapping, since
   a wrong guess would silently produce wrong synthetic ground truth.
3. **`CatalaEligibilityGenerator(ruleset, state=...)`** validates the field
   mapping once at construction (against a reference profile) so a bad
   mapping fails immediately, not on case 47 of 100.
4. **`generate(n, seed=...)`** samples `n` synthetic household profiles,
   runs each through `catala interpret` via the ruleset's scope, and turns
   the boolean `outcome_field` output into `expected_outcome`. If Catala
   returns an explanation trace (`--trace`), it's mapped into
   `RationaleTrace` steps; if not (or the trace can't be parsed), a
   2-step fallback trace is used instead so every case still satisfies the
   library's "rationale trace has >= 2 steps" requirement.

---

## CLI usage

```bash
govsynth generate-catala path/to/liheap.catala_en \
    --scope Eligibility \
    --program liheap \
    --output ./output/liheap/ \
    --n 100 \
    --seed 42 \
    --field-mapping monthly_income=monthly_gross_income \
    --citation "LIHEAP 42 U.S.C. 8621 et seq., encoded in liheap.catala_en" \
    --state VA
```

Key options:

| Option | Meaning |
|---|---|
| `ruleset` (positional) | Path to the `.catala_en`/`.catala_fr` file |
| `--scope` | Catala scope to run, e.g. `Eligibility` |
| `--program` | Program identifier used as `TestCase.program` and in case IDs, e.g. `liheap` |
| `--outcome-field` | Scope output field representing eligibility (default `eligible`) |
| `--field-mapping CATALA_FIELD=PROFILE_FIELD` | Repeatable; overrides for fields whose Catala name doesn't match the profile field name |
| `--citation` / `--citation-year` | Recorded on generated cases' `source_citations`/`PolicyCitation` |
| `--binary` | Path to a specific `catala` executable, if not the one on `PATH` |
| `--state`, `--n`, `--seed`, `--format` | Same as `govsynth generate` |

If `catala` isn't found on `PATH`, or the ruleset's declared inputs can't
be resolved to profile fields, the command exits with code `2` and prints
the specific missing field names or the underlying `CatalaRuntimeError`.

---

## Python API usage

```python
from govsynth.generators.catala_eligibility import CatalaEligibilityGenerator
from govsynth.sources.catala.ruleset import CatalaRuleset
from govsynth.pipeline import Pipeline

ruleset = CatalaRuleset.load(
    "path/to/liheap.catala_en",
    scope="Eligibility",
    program_name="liheap",
    field_mapping={"monthly_income": "monthly_gross_income"},
    citation="LIHEAP 42 U.S.C. 8621 et seq., encoded in liheap.catala_en",
)
generator = CatalaEligibilityGenerator(ruleset, state="VA")

pipeline = Pipeline(generator=generator)
cases = pipeline.generate(n=100, seed=42)
pipeline.save(cases, "./output/liheap/", formats=["yaml", "jsonl"])
```

Or without a `Pipeline`, directly:

```python
cases = generator.generate(n=20, seed=42)
print(cases[0].case_id)
print(cases[0].rationale_trace.to_plain_text())
```

---

## Field mapping in depth

`CatalaRuleset.build_inputs()` flattens a `USHouseholdProfile` (via
`to_scenario_fields()`, including `additional_context`) and resolves each
of the scope's declared input fields against it:

1. If `field_mapping` has an entry for the Catala field name, use the
   mapped profile field.
2. Otherwise, look for a profile field with the *exact same name* as the
   Catala field.
3. If neither resolves, the field is left unmapped.

Any unmapped fields raise `CatalaMappingError` listing exactly which
Catala input fields need a `field_mapping` entry, plus the available
profile field names to map them to. This is intentional: the adapter will
not attempt a fuzzy or best-effort name match, since an incorrect guess
would silently produce wrong synthetic ground truth — the one thing this
library's "policy accuracy matters" constraint (see `CLAUDE.md`) cannot
tolerate.

---

## `TestCase.program` for custom-imported programs

Built-in generators use one of the fixed `KNOWN_PROGRAMS` values (e.g.
`"snap"`, `"wic"`, `"medicaid"`). A Catala ruleset can encode any program,
so `TestCase.program` also accepts an arbitrary lowercase, underscore-only
identifier (e.g. `"my_local_benefit"`) that isn't in `KNOWN_PROGRAMS` —
still validated as a slug, not an arbitrary string. `--program` on the CLI
(or `program_name=` on `CatalaRuleset.load`) becomes this value.

---

## What a Catala ruleset looks like

Catala source files use a literate-programming style that interleaves the
legal text with executable law. A real, syntactically-verified example is
best found directly from the Catala project rather than reproduced here —
this adapter was built without a working `catala` installation available
in its development environment (the `opam` package index was not
reachable), so no example `.catala_en` snippet in this repo has been
compiled and checked. For real, working examples, see:

- [catala-lang.org](https://catala-lang.org) — language tour and docs
- [CatalaLang/catala-examples](https://github.com/CatalaLang/catala-examples) — full worked rulesets, including French family benefits and US-style examples

Once you have a `.catala_en` file and know which `scope` computes
eligibility, point `--scope` at it and the adapter takes care of the rest.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `CatalaNotAvailableError: 'catala' not found on PATH` | Install Catala (`opam install catala`) or pass `--binary`/`binary=` |
| `CatalaMappingError: ... declares input field(s) [...] that couldn't be resolved` | Add `--field-mapping CATALA_FIELD=PROFILE_FIELD` for each listed field |
| `CatalaRuntimeError: catala json-schema failed` / `catala interpret failed` | Run the command shown in the error by hand against your installed `catala` version — flag names or output format may differ from what this adapter assumes; see the caveat above |
| Rationale trace only has the 2-step fallback | Either `catala interpret --trace=...` produced no parseable trace for this ruleset/version, or the ruleset doesn't emit one — the fallback trace is still valid, just less detailed |
