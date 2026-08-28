"""Rationale trace evaluator.

Scores a model's free-text reasoning output against the ground-truth
RationaleTrace from a TestCase. This is the key differentiator of
synthetic-gov-data-kit: it enables evaluation of HOW a model reasons,
not just WHAT it answers.

Four dimensions are scored:
  1. step_coverage     — did the model cover the key reasoning steps?
  2. rule_accuracy     — did it cite the correct CFR/policy rules?
  3. conclusion_correct— did it reach the correct final outcome?
  4. overall           — weighted composite score

Usage:
    from govsynth.evaluation import RationaleEvaluator

    evaluator = RationaleEvaluator()
    score = evaluator.score(case, model_output="The household is eligible because...")
    print(score.overall)       # 0.85
    print(score.rule_accuracy) # 1.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from govsynth.models.rationale import RationaleTrace
from govsynth.models.test_case import TestCase


@dataclass
class RationaleScore:
    """Scores for a single model output evaluated against a ground-truth trace."""

    case_id: str

    # Component scores (0.0 – 1.0)
    step_coverage: float = 0.0
    rule_accuracy: float = 0.0
    conclusion_correct: float = 0.0

    # Derived
    overall: float = 0.0

    # Diagnostics
    steps_covered: list[str] = field(default_factory=list)
    steps_missed: list[str] = field(default_factory=list)
    rules_cited: list[str] = field(default_factory=list)
    rules_missing: list[str] = field(default_factory=list)
    predicted_outcome: str = ""
    expected_outcome: str = ""

    def passed(self, threshold: float = 0.7) -> bool:
        return self.overall >= threshold

    def __str__(self) -> str:
        status = "PASS" if self.passed() else "FAIL"
        return (
            f"RationaleScore({self.case_id}) [{status}]\n"
            f"  overall={self.overall:.2f}  "
            f"step_coverage={self.step_coverage:.2f}  "
            f"rule_accuracy={self.rule_accuracy:.2f}  "
            f"conclusion={self.conclusion_correct:.2f}\n"
            f"  predicted_outcome='{self.predicted_outcome}'  "
            f"expected='{self.expected_outcome}'"
        )


class RationaleEvaluator:
    """Scores model outputs against ground-truth rationale traces.

    Args:
        step_weight: Weight for step coverage in overall score.
        rule_weight: Weight for rule accuracy.
        conclusion_weight: Weight for conclusion correctness.
    """

    def __init__(
        self,
        step_weight: float = 0.35,
        rule_weight: float = 0.30,
        conclusion_weight: float = 0.35,
    ) -> None:
        assert abs(step_weight + rule_weight + conclusion_weight - 1.0) < 0.001, "Weights must sum to 1.0"
        self.step_weight = step_weight
        self.rule_weight = rule_weight
        self.conclusion_weight = conclusion_weight

    def score(self, case: TestCase, model_output: str) -> RationaleScore:
        """Score a model's free-text output against the case's ground-truth trace.

        Args:
            case: The TestCase with ground-truth rationale_trace and expected_outcome.
            model_output: The model's complete text response.

        Returns:
            RationaleScore with component and overall scores.
        """
        output_lower = model_output.lower()
        trace = case.rationale_trace

        step_cov, covered, missed = self._score_step_coverage(trace, output_lower)
        rule_acc, cited, missing = self._score_rule_accuracy(trace, output_lower)
        conclusion, predicted = self._score_conclusion(case.expected_outcome, output_lower)

        overall = self.step_weight * step_cov + self.rule_weight * rule_acc + self.conclusion_weight * conclusion

        return RationaleScore(
            case_id=case.case_id,
            step_coverage=round(step_cov, 3),
            rule_accuracy=round(rule_acc, 3),
            conclusion_correct=round(conclusion, 3),
            overall=round(overall, 3),
            steps_covered=covered,
            steps_missed=missed,
            rules_cited=cited,
            rules_missing=missing,
            predicted_outcome=predicted,
            expected_outcome=case.expected_outcome,
        )

    def score_batch(self, cases: list[TestCase], model_outputs: list[str]) -> list[RationaleScore]:
        """Score a batch of (case, output) pairs."""
        assert len(cases) == len(model_outputs), "cases and outputs must have same length"
        return [self.score(c, o) for c, o in zip(cases, model_outputs, strict=True)]

    def summary_stats(self, scores: list[RationaleScore]) -> dict[str, Any]:
        """Compute aggregate stats over a list of scores."""
        if not scores:
            return {}
        n = len(scores)
        return {
            "n": n,
            "pass_rate": sum(1 for s in scores if s.passed()) / n,
            "mean_overall": sum(s.overall for s in scores) / n,
            "mean_step_coverage": sum(s.step_coverage for s in scores) / n,
            "mean_rule_accuracy": sum(s.rule_accuracy for s in scores) / n,
            "mean_conclusion_correct": sum(s.conclusion_correct for s in scores) / n,
            "conclusion_accuracy": sum(s.conclusion_correct for s in scores) / n,
        }

    # ------------------------------------------------------------------
    # Component scorers
    # ------------------------------------------------------------------

    def _score_step_coverage(self, trace: RationaleTrace, output_lower: str) -> tuple[float, list[str], list[str]]:
        """Check whether the model's output covers the key reasoning steps."""
        covered: list[str] = []
        missed: list[str] = []

        for step in trace.steps:
            # Extract key numeric signals from the step's computation
            keywords = self._extract_step_keywords(step.computation, step.title)
            if any(kw in output_lower for kw in keywords):
                covered.append(step.title)
            else:
                missed.append(step.title)

        score = len(covered) / len(trace.steps) if trace.steps else 0.0
        return score, covered, missed

    def _score_rule_accuracy(self, trace: RationaleTrace, output_lower: str) -> tuple[float, list[str], list[str]]:
        """Check whether the model cited the correct CFR/policy rules."""
        all_rules = trace.cited_rules()
        if not all_rules:
            return 1.0, [], []

        cited: list[str] = []
        missing: list[str] = []

        for rule in all_rules:
            # Normalize: "7 CFR 273.9(a)(1)" → look for "273.9" or "cfr 273"
            rule_lower = rule.lower()
            # Extract the CFR part number (e.g., "273.9")
            cfr_match = re.search(r"(\d+\.\d+)", rule_lower)
            if cfr_match:
                part = cfr_match.group(1)
                if part in output_lower or rule_lower in output_lower:
                    cited.append(rule)
                else:
                    missing.append(rule)
            else:
                # Fallback: check if any 5-char substring of rule appears in output
                if any(rule_lower[i : i + 6] in output_lower for i in range(len(rule_lower) - 5)):
                    cited.append(rule)
                else:
                    missing.append(rule)

        score = len(cited) / len(all_rules) if all_rules else 1.0
        return score, cited, missing

    def _score_conclusion(self, expected_outcome: str, output_lower: str) -> tuple[float, str]:
        """Check whether the model reached the correct conclusion."""
        expected = expected_outcome.lower().strip()

        # Look for clear eligibility signal words
        eligible_signals = [
            "eligible",
            "qualifies",
            "qualify",
            "approved",
            "meets the requirements",
        ]
        ineligible_signals = [
            "ineligible",
            "does not qualify",
            "not eligible",
            "denied",
            "exceeds",
            "over the limit",
            "too high",
        ]

        # Word-boundary match: "eligible" is a substring of "ineligible", so a
        # naive `sig in output_lower` check would treat every "ineligible"
        # mention as also asserting "eligible", collapsing correct
        # conclusions into false "ambiguous" scores.
        found_ineligible = any(re.search(rf"\b{re.escape(sig)}\b", output_lower) for sig in ineligible_signals)

        # Strip matched negated phrases (e.g. "does not qualify", "not eligible")
        # before checking eligible_signals, so a bare positive word swallowed
        # inside a negated phrase (e.g. "qualify" inside "does not qualify")
        # doesn't also register as a positive signal.
        stripped = output_lower
        for sig in ineligible_signals:
            stripped = re.sub(rf"\b{re.escape(sig)}\b", " ", stripped)
        found_eligible = any(re.search(rf"\b{re.escape(sig)}\b", stripped) for sig in eligible_signals)

        if expected == "eligible":
            if found_eligible and not found_ineligible:
                return 1.0, "eligible"
            elif found_eligible and found_ineligible:
                return 0.5, "ambiguous"  # Model hedged
            else:
                return 0.0, "ineligible"

        elif expected == "ineligible":
            if found_ineligible and not found_eligible:
                return 1.0, "ineligible"
            elif found_ineligible and found_eligible:
                return 0.5, "ambiguous"
            else:
                return 0.0, "eligible"

        return 0.5, "unknown"

    def _extract_step_keywords(self, computation: str, title: str) -> list[str]:
        """Extract searchable keywords from a reasoning step."""
        keywords: list[str] = []

        # Dollar amounts (e.g., "$2,888" → "2,888" and "2888")
        for m in re.finditer(r"\$?([\d,]+(?:\.\d+)?)", computation):
            val = m.group(1).replace(",", "")
            if len(val) >= 3:  # Skip tiny numbers
                keywords.append(val)
                keywords.append(m.group(1))  # with comma

        # Percentages
        for m in re.finditer(r"(\d+)%", computation):
            keywords.append(m.group(0).lower())

        # Key terms from title
        title_words = title.lower().split()
        keywords.extend([w for w in title_words if len(w) > 4])

        return keywords
