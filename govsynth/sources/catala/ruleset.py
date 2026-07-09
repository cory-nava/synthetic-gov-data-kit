"""Load and introspect an imported Catala ruleset.

A `CatalaRuleset` wraps one `.catala_en`/`.catala_fr` file plus the name of
the scope to run, and knows how to translate a `USHouseholdProfile` into
that scope's input JSON (via `catala json-schema` introspection) and run it
through `CatalaRuntime`.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from govsynth.sources.catala.runtime import CatalaResult, CatalaRuntime

if TYPE_CHECKING:
    from govsynth.profiles.us_household import USHouseholdProfile


class CatalaMappingError(ValueError):
    """Raised when a ruleset's declared scope inputs can't be resolved to
    profile fields -- either because no field_mapping entry was given for
    them, or the mapped profile field doesn't exist.

    Deliberately not a fuzzy/best-effort fallback: a wrong guess here would
    silently produce wrong synthetic ground truth, which is the one thing
    this library cannot get away with (see AGENTS.md/CLAUDE.md's "Policy
    accuracy matters" constraint).
    """


@dataclass
class CatalaRuleset:
    """An imported Catala ruleset, ready to be run against synthetic profiles.

    Args:
        path: Path to the `.catala_en`/`.catala_fr` (or bundled `.catala`)
            source file.
        scope: Name of the Catala scope to run, e.g. "Eligibility".
        program_name: Identifier used as `TestCase.program` and in case_ids,
            e.g. "liheap" or "my_local_benefit". Must be lowercase_snake_case
            (see `TestCase.validate_program`).
        jurisdiction: Jurisdiction segment for case_ids/TestCase.jurisdiction,
            e.g. "us" or "us.va".
        field_mapping: `{catala_input_field: profile_field}` overrides for
            input fields whose Catala name doesn't exactly match the
            corresponding `USHouseholdProfile`/`to_scenario_fields()` name.
            Fields with matching names are wired automatically; anything
            left over after that raises `CatalaMappingError` rather than
            guessing.
        outcome_field: Name of the boolean (or truthy) scope output field
            that represents the eligibility determination.
        citation: Human-readable source citation for `TestCase.source_citations`
            (e.g. "LIHEAP 42 U.S.C. 8621 et seq., encoded in liheap.catala_en").
        citation_year: Year recorded on the generated `PolicyCitation`. Defaults
            to the current year since a generic imported ruleset has no
            inherent fiscal/calendar year the way SNAP/WIC/Medicaid do.
        binary / timeout: Passed through to `CatalaRuntime`.
    """

    path: Path
    scope: str
    program_name: str
    jurisdiction: str = "us"
    field_mapping: dict[str, str] = field(default_factory=dict)
    outcome_field: str = "eligible"
    citation: str = ""
    citation_year: int = field(default_factory=lambda: datetime.date.today().year)
    binary: str | None = None
    timeout: float = 30.0
    input_schema: dict[str, Any] = field(default_factory=dict, repr=False)
    output_schema: dict[str, Any] = field(default_factory=dict, repr=False)
    _runtime: CatalaRuntime | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    @classmethod
    def load(
        cls,
        path: str | Path,
        scope: str,
        *,
        program_name: str,
        jurisdiction: str = "us",
        field_mapping: dict[str, str] | None = None,
        outcome_field: str = "eligible",
        citation: str = "",
        citation_year: int | None = None,
        binary: str | None = None,
        timeout: float = 30.0,
    ) -> CatalaRuleset:
        """Load a ruleset and introspect `scope`'s input/output schema.

        Introspection happens once at import time (this call), not per
        generated case, so `catala json-schema` is invoked exactly once
        regardless of how many synthetic cases are later generated from it.
        """
        runtime = CatalaRuntime(path, binary=binary, timeout=timeout)
        input_schema, output_schema = runtime.scope_schema(scope)
        kwargs: dict[str, Any] = dict(
            path=Path(path),
            scope=scope,
            program_name=program_name,
            jurisdiction=jurisdiction,
            field_mapping=dict(field_mapping or {}),
            outcome_field=outcome_field,
            citation=citation,
            binary=binary,
            timeout=timeout,
            input_schema=input_schema,
            output_schema=output_schema,
        )
        if citation_year is not None:
            kwargs["citation_year"] = citation_year
        ruleset = cls(**kwargs)
        ruleset._runtime = runtime
        return ruleset

    @property
    def runtime(self) -> CatalaRuntime:
        if self._runtime is None:
            self._runtime = CatalaRuntime(self.path, binary=self.binary, timeout=self.timeout)
        return self._runtime

    def input_field_names(self) -> list[str]:
        """Declared input field names for `scope`, from its JSON schema."""
        properties = self.input_schema.get("properties")
        return sorted(properties.keys()) if isinstance(properties, dict) else []

    def output_field_names(self) -> list[str]:
        """Declared output field names for `scope`, from its JSON schema."""
        properties = self.output_schema.get("properties")
        return sorted(properties.keys()) if isinstance(properties, dict) else []

    def build_inputs(self, profile: USHouseholdProfile) -> dict[str, Any]:
        """Map `profile` onto this scope's declared input fields.

        Resolution order per Catala input field: explicit `field_mapping`
        entry, then an exact-name match against the profile's flattened
        `to_scenario_fields()` output. No fuzzy/heuristic name matching --
        anything left unresolved raises `CatalaMappingError` naming exactly
        which fields need a `field_mapping` entry.
        """
        scenario_fields = profile.to_scenario_fields()
        flat: dict[str, Any] = {k: v for k, v in scenario_fields.items() if k != "additional_context"}
        flat.update(scenario_fields.get("additional_context", {}))

        declared = self.input_field_names()
        resolved: dict[str, Any] = {}
        unmapped: list[str] = []
        for catala_field in declared:
            profile_field = self.field_mapping.get(catala_field, catala_field)
            if profile_field not in flat:
                unmapped.append(catala_field)
                continue
            resolved[catala_field] = flat[profile_field]

        if unmapped:
            raise CatalaMappingError(
                f"Scope '{self.scope}' in {self.path} declares input field(s) {unmapped} "
                "that couldn't be resolved to a profile field. Pass "
                "field_mapping={{'<catala_field>': '<profile_field>'}} for these. "
                f"Available profile fields: {sorted(flat)}"
            )
        return resolved

    def run(self, profile: USHouseholdProfile, *, with_trace: bool = True) -> CatalaResult:
        """Build inputs from `profile` and run this ruleset's scope."""
        inputs = self.build_inputs(profile)
        return self.runtime.interpret(self.scope, inputs, with_trace=with_trace)
