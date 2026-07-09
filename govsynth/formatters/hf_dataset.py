"""HuggingFace datasets formatter.

Produces a datasets.DatasetDict compatible with push_to_hub().
Requires: pip install synthetic-gov-data-kit[hf]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from govsynth.models.test_case import TestCase

if TYPE_CHECKING:
    from datasets import DatasetDict


def _case_to_hf_row(case: TestCase) -> dict[str, Any]:
    """Convert a TestCase to a flat HF dataset row."""
    return {
        "case_id": case.case_id,
        "program": case.program,
        "jurisdiction": case.jurisdiction,
        "task_type": case.task_type.value,
        "difficulty": case.difficulty.value,
        "state": case.scenario.state,
        "household_size": case.scenario.household_size,
        "monthly_gross_income": case.scenario.monthly_gross_income,
        "monthly_net_income": case.scenario.monthly_net_income or -1.0,
        "liquid_assets": case.scenario.liquid_assets,
        "has_elderly_or_disabled": case.scenario.has_elderly_or_disabled,
        "citizenship_status": case.scenario.citizenship_status,
        "scenario": case.scenario.summary,
        "question": case.task.instruction,
        "expected_outcome": case.expected_outcome,
        "expected_answer": case.expected_answer,
        "rationale_trace": json.dumps(
            {
                "steps": [
                    {
                        "step": s.step_number,
                        "title": s.title,
                        "rule": s.rule_applied,
                        "computation": s.computation,
                        "result": s.result,
                    }
                    for s in case.rationale_trace.steps
                ],
                "conclusion": case.rationale_trace.conclusion,
            }
        ),
        "variation_tags": case.variation_tags,
        "source_citations": case.source_citations,
        "seed": case.seed or -1,
    }


class HFDatasetFormatter:
    """Serializes TestCase objects to a HuggingFace DatasetDict.

    Requires the `datasets` library: pip install synthetic-gov-data-kit[hf]

    Usage:
        formatter = HFDatasetFormatter(split_ratios={"train": 0.8, "test": 0.2})
        ds = formatter.to_dataset(cases)
        ds.push_to_hub("your-org/govsynth-snap-fy2026")
    """

    def __init__(
        self,
        split_ratios: dict[str, float] | None = None,
    ) -> None:
        self.split_ratios = split_ratios or {"train": 0.7, "validation": 0.15, "test": 0.15}

    def to_dataset(self, cases: list[TestCase]) -> DatasetDict:
        """Convert cases to a HuggingFace DatasetDict with train/val/test splits."""
        try:
            from datasets import Dataset, DatasetDict
        except ImportError as err:
            raise ImportError(
                "HuggingFace datasets not installed. Run: pip install synthetic-gov-data-kit[hf]"
            ) from err

        rows = [_case_to_hf_row(c) for c in cases]
        full = Dataset.from_list(rows)

        # Split into train/val/test
        ratios = self.split_ratios
        n = len(full)
        n_train = int(n * ratios.get("train", 0.7))
        n_val = int(n * ratios.get("validation", 0.15))

        splits: dict[str, Dataset] = {}
        splits["train"] = full.select(range(n_train))
        splits["validation"] = full.select(range(n_train, n_train + n_val))
        splits["test"] = full.select(range(n_train + n_val, n))

        return DatasetDict(splits)

    def write(self, cases: list[TestCase], output_dir: str | Path) -> None:
        """Save the dataset to disk in Arrow format."""
        ds = self.to_dataset(cases)
        ds.save_to_disk(str(output_dir))

    def push_to_hub(self, cases: list[TestCase], repo_id: str, **kwargs: object) -> None:
        """Push cases directly to the HuggingFace Hub."""
        ds = self.to_dataset(cases)
        ds.push_to_hub(repo_id, **kwargs)
