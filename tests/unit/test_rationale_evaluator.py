"""Unit tests for RationaleEvaluator."""

import pytest
from govsynth.evaluation.rationale_evaluator import RationaleEvaluator, RationaleScore
from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator
from govsynth.models.test_case import TestCase


@pytest.fixture(scope="module")
def eligible_case() -> TestCase:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=20, seed=1)
    for case in cases:
        if case.expected_outcome == "eligible":
            return case
    raise AssertionError("No eligible case generated for seed=1; adjust seed")


@pytest.fixture(scope="module")
def ineligible_case() -> TestCase:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    cases = gen.generate(n=20, seed=1)
    for case in cases:
        if case.expected_outcome == "ineligible":
            return case
    raise AssertionError("No ineligible case generated for seed=1; adjust seed")


class TestRationaleEvaluatorScore:
    def test_perfect_model_output_scores_highly(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        model_output = eligible_case.rationale_trace.to_plain_text() + "\n" + eligible_case.expected_answer
        score = evaluator.score(eligible_case, model_output)
        assert score.overall > 0.8
        assert score.conclusion_correct == 1.0
        assert score.passed()

    def test_empty_output_scores_poorly(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        score = evaluator.score(eligible_case, "")
        assert score.overall < 0.5
        assert not score.passed()

    def test_wrong_conclusion_is_penalized(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        score = evaluator.score(eligible_case, "This household is ineligible and does not qualify.")
        assert score.conclusion_correct == 0.0
        assert score.predicted_outcome == "ineligible"

    def test_hedged_conclusion_scores_half(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        score = evaluator.score(eligible_case, "This household may be eligible but is also ineligible depending on...")
        assert score.conclusion_correct == 0.5
        assert score.predicted_outcome == "ambiguous"

    def test_ineligible_case_correct_conclusion(self, ineligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        score = evaluator.score(ineligible_case, "This household is ineligible; income exceeds the limit.")
        assert score.conclusion_correct == 1.0

    def test_step_coverage_reports_covered_and_missed(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        score = evaluator.score(eligible_case, eligible_case.rationale_trace.to_plain_text())
        assert score.steps_covered
        assert len(score.steps_covered) + len(score.steps_missed) == len(eligible_case.rationale_trace.steps)

    def test_rule_accuracy_detects_cited_cfr_sections(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        rules = eligible_case.rationale_trace.cited_rules()
        model_output = " ".join(rules)
        score = evaluator.score(eligible_case, model_output)
        assert score.rule_accuracy == 1.0
        assert set(score.rules_cited) == set(rules)


class TestRationaleEvaluatorWeights:
    def test_weights_must_sum_to_one(self) -> None:
        with pytest.raises(AssertionError):
            RationaleEvaluator(step_weight=0.5, rule_weight=0.5, conclusion_weight=0.5)

    def test_custom_weights_are_applied(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator(step_weight=1.0, rule_weight=0.0, conclusion_weight=0.0)
        score = evaluator.score(eligible_case, eligible_case.rationale_trace.to_plain_text())
        assert score.overall == pytest.approx(score.step_coverage, abs=0.001)


class TestScoreBatch:
    def test_score_batch_matches_pairwise_scoring(self, eligible_case: TestCase, ineligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        cases = [eligible_case, ineligible_case]
        outputs = ["eligible", "ineligible"]
        scores = evaluator.score_batch(cases, outputs)
        assert len(scores) == 2
        assert scores[0].case_id == eligible_case.case_id
        assert scores[1].case_id == ineligible_case.case_id

    def test_score_batch_mismatched_lengths_raises(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        with pytest.raises(AssertionError):
            evaluator.score_batch([eligible_case], ["one", "two"])


class TestSummaryStats:
    def test_empty_scores_returns_empty_dict(self) -> None:
        evaluator = RationaleEvaluator()
        assert evaluator.summary_stats([]) == {}

    def test_summary_stats_computes_means_and_pass_rate(self, eligible_case: TestCase) -> None:
        evaluator = RationaleEvaluator()
        high = evaluator.score(eligible_case, eligible_case.rationale_trace.to_plain_text())
        low = evaluator.score(eligible_case, "")
        stats = evaluator.summary_stats([high, low])
        assert stats["n"] == 2
        assert stats["mean_overall"] == pytest.approx((high.overall + low.overall) / 2)
        assert 0.0 <= stats["pass_rate"] <= 1.0


class TestRationaleScore:
    def test_passed_uses_default_threshold(self) -> None:
        assert RationaleScore(case_id="x", overall=0.7).passed()
        assert not RationaleScore(case_id="x", overall=0.69).passed()

    def test_str_includes_case_id_and_status(self) -> None:
        score = RationaleScore(case_id="snap.va.eligibility.foo", overall=0.9)
        text = str(score)
        assert "snap.va.eligibility.foo" in text
        assert "PASS" in text
