"""YAML output formatter.

Serializes TestCase objects to the test case YAML schema (v1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from govsynth.models.test_case import TestCase


def _case_to_dict(case: TestCase) -> dict[str, Any]:
    """Convert a TestCase to a YAML-compatible dict."""
    return {
        "case_id": case.case_id,
        "program": case.program,
        "jurisdiction": case.jurisdiction,
        "task_type": case.task_type.value,
        "difficulty": case.difficulty.value,
        "scenario": {
            "summary": case.scenario.summary,
            "household_size": case.scenario.household_size,
            "monthly_gross_income": case.scenario.monthly_gross_income,
            "monthly_net_income": case.scenario.monthly_net_income,
            "liquid_assets": case.scenario.liquid_assets,
            "state": case.scenario.state,
            "has_elderly_or_disabled": case.scenario.has_elderly_or_disabled,
            "has_dependent_children": case.scenario.has_dependent_children,
            "citizenship_status": case.scenario.citizenship_status,
            **({"additional_context": case.scenario.additional_context} if case.scenario.additional_context else {}),
        },
        "task": {
            "instruction": case.task.instruction,
            **({"portal": case.task.portal} if case.task.portal else {}),
            **({"documents_available": case.task.documents_available} if case.task.documents_available else {}),
        },
        "expected_outcome": case.expected_outcome,
        "expected_answer": case.expected_answer,
        "rationale_trace": {
            "steps": [
                {
                    "step": s.step_number,
                    "title": s.title,
                    "rule": s.rule_applied,
                    "inputs": s.inputs,
                    "computation": s.computation,
                    "result": s.result,
                    **({"determinative": True} if s.is_determinative else {}),
                    **({"note": s.note} if s.note else {}),
                }
                for s in case.rationale_trace.steps
            ],
            "conclusion": case.rationale_trace.conclusion,
            "policy_basis": [
                {
                    "document": c.document,
                    "section": c.section,
                    "year": c.year,
                    **({"url": c.url} if c.url else {}),
                }
                for c in case.rationale_trace.policy_basis
            ],
        },
        "variation_tags": case.variation_tags,
        "source_citations": case.source_citations,
        "generated_by": case.generated_by,
        "generated_at": case.generated_at.isoformat(),
        **({"seed": case.seed} if case.seed is not None else {}),
    }


class YAMLFormatter:
    """Serializes TestCase objects to YAML format."""

    def format_one(self, case: TestCase) -> str:
        """Serialize a single TestCase to a YAML string."""
        d = _case_to_dict(case)
        return str(yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=False))

    def write_one(self, case: TestCase, path: str | Path) -> None:
        """Write a single TestCase to a YAML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.format_one(case), encoding="utf-8")

    def write_many(
        self,
        cases: list[TestCase],
        output_dir: str | Path,
        one_file_per_case: bool = True,
    ) -> None:
        """Write TestCases to a directory.

        Args:
            cases: List of TestCase objects.
            output_dir: Directory to write YAML files into.
            one_file_per_case: If True, one .yaml per case (default).
                               If False, writes a single cases.yaml with all cases.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        if one_file_per_case:
            for case in cases:
                filename = f"{case.case_id}.yaml"
                self.write_one(case, out / filename)
        else:
            all_cases = [_case_to_dict(c) for c in cases]
            (out / "cases.yaml").write_text(
                yaml.dump(all_cases, default_flow_style=False, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
