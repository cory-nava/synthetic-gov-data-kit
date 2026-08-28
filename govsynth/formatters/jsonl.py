"""JSONL output formatter for LLM fine-tuning.

Produces instruction-tuning style JSONL where each line is a JSON object
containing a messages array in OpenAI/Anthropic chat format.
"""

from __future__ import annotations

import json
import re
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

# Matches a fenced JSON block. Non-greedy so several blocks in one response
# yield several matches rather than one span swallowing the text between them;
# the scorer takes the last, which is the model's final answer after any
# self-correction.
ANSWER_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)


class JSONLFormatter:
    """Serializes TestCase objects to JSONL (instruction fine-tuning format).

    `include_answer_block=True` is currently SNAP-only: `_benefit_for` reads
    `scenario.additional_context["monthly_allotment"]`, a key only the SNAP
    eligibility generator populates. Formatting an eligible case from another
    program (e.g. WIC) with the flag on raises `ValueError` rather than silently
    emitting a wrong or fabricated number.
    """

    def __init__(
        self,
        include_rationale_in_answer: bool = True,
        system_prompt: str = _SYSTEM_PROMPT,
        include_answer_block: bool = False,
    ) -> None:
        self.include_rationale = include_rationale_in_answer
        self.system_prompt = system_prompt
        self.include_answer_block = include_answer_block

    def format_one(self, case: TestCase) -> dict[str, Any]:
        """Convert a TestCase to a fine-tuning message dict."""
        assistant_content = case.expected_answer
        if self.include_rationale:
            trace_text = case.rationale_trace.to_plain_text()
            assistant_content = f"{trace_text}\n\n{case.expected_answer}"

        if self.include_answer_block:
            benefit = self._benefit_for(case)
            payload = {
                "determination": case.expected_outcome,
                "monthly_benefit": benefit,
                "rules_cited": case.rationale_trace.cited_rules(),
            }
            block = json.dumps(payload, indent=2)
            assistant_content = f"{assistant_content}\n\n```json\n{block}\n```"

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

    @staticmethod
    def _benefit_for(case: TestCase) -> float | None:
        """Return the monthly allotment for an eligible case, else None.

        The generator stores the computed allotment in scenario.additional_context
        (key "monthly_allotment") for every eligible case; recomputing it here would
        duplicate the rules engine and let the two drift.
        """
        if case.expected_outcome != "eligible":
            return None
        value = case.scenario.additional_context.get("monthly_allotment")
        if value is None:
            raise ValueError(
                f"Case {case.case_id} is eligible but carries no monthly_allotment "
                "in scenario.additional_context; the answer block cannot be built."
            )
        return float(value)
