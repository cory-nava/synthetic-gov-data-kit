"""CSV output formatter — primarily for human review and inspection."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from govsynth.models.test_case import TestCase


class CSVFormatter:
    """Serializes TestCase objects to CSV for review and inspection."""

    # Columns in the output CSV
    COLUMNS = [
        "case_id",
        "program",
        "jurisdiction",
        "task_type",
        "difficulty",
        "state",
        "household_size",
        "monthly_gross_income",
        "monthly_net_income",
        "liquid_assets",
        "has_elderly_or_disabled",
        "expected_outcome",
        "rationale_steps",
        "variation_tags",
        "source_citations",
        "scenario_summary",
        "expected_answer",
    ]

    def format_row(self, case: TestCase) -> dict[str, Any]:
        return {
            "case_id": case.case_id,
            "program": case.program,
            "jurisdiction": case.jurisdiction,
            "task_type": case.task_type.value,
            "difficulty": case.difficulty.value,
            "state": case.scenario.state,
            "household_size": case.scenario.household_size,
            "monthly_gross_income": case.scenario.monthly_gross_income,
            "monthly_net_income": case.scenario.monthly_net_income or "",
            "liquid_assets": case.scenario.liquid_assets,
            "has_elderly_or_disabled": case.scenario.has_elderly_or_disabled,
            "expected_outcome": case.expected_outcome,
            "rationale_steps": case.rationale_trace.step_count(),
            "variation_tags": "|".join(case.variation_tags),
            "source_citations": "|".join(case.source_citations),
            "scenario_summary": case.scenario.summary,
            "expected_answer": case.expected_answer,
        }

    def write(self, cases: list[TestCase], path: str | Path) -> None:
        """Write all cases to a CSV file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
            writer.writeheader()
            for case in cases:
                writer.writerow(self.format_row(case))
