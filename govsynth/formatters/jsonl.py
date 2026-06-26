"""JSONL output formatter for LLM fine-tuning.

Produces instruction-tuning style JSONL where each line is a JSON object
containing a messages array in OpenAI/Anthropic chat format.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from govsynth.models.test_case import TestCase

_SYSTEM_PROMPT = (
    "You are a knowledgeable and accurate US government benefits eligibility specialist. "
    "When determining eligibility, you apply the correct federal regulations step by step, "
    "cite specific CFR sections, and show your complete reasoning before stating your conclusion. "
    "You are accurate, clear, and never guess at policy thresholds — you cite the applicable "
    "federal fiscal year tables."
)


class JSONLFormatter:
    """Serializes TestCase objects to JSONL (instruction fine-tuning format)."""

    def __init__(
        self,
        include_rationale_in_answer: bool = True,
        system_prompt: str = _SYSTEM_PROMPT,
    ) -> None:
        self.include_rationale = include_rationale_in_answer
        self.system_prompt = system_prompt

    def format_one(self, case: TestCase) -> dict[str, Any]:
        """Convert a TestCase to a fine-tuning message dict."""
        assistant_content = case.expected_answer
        if self.include_rationale:
            trace_text = case.rationale_trace.to_plain_text()
            assistant_content = f"{trace_text}\n\n{case.expected_answer}"

        return {
            "case_id": case.case_id,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": case.scenario.summary + "\n\n" + case.task.instruction},
                {"role": "assistant", "content": assistant_content},
            ],
            "metadata": {
                "program": case.program,
                "jurisdiction": case.jurisdiction,
                "expected_outcome": case.expected_outcome,
                "difficulty": case.difficulty.value,
                "variation_tags": case.variation_tags,
            },
        }

    def write(self, cases: list[TestCase], path: str | Path) -> None:
        """Write all cases to a JSONL file (one JSON object per line)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for case in cases:
                f.write(json.dumps(self.format_one(case), ensure_ascii=False) + "\n")
