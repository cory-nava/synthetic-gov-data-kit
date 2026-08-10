"""Tests for the machine-checkable answer block in the JSONL formatter."""

import json

import pytest
from govsynth.formatters.jsonl import ANSWER_BLOCK_RE, JSONLFormatter
from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator
from govsynth.models.test_case import TestCase


@pytest.fixture(scope="module")
def _cases() -> list[TestCase]:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    return gen.generate(n=40, seed=20260810)


@pytest.fixture
def snap_case(_cases: list[TestCase]) -> TestCase:
    return next(c for c in _cases if c.expected_outcome == "eligible")


@pytest.fixture
def ineligible_snap_case(_cases: list[TestCase]) -> TestCase:
    return next(c for c in _cases if c.expected_outcome == "ineligible")


def test_answer_block_absent_by_default(snap_case: TestCase) -> None:
    rec = JSONLFormatter().format_one(snap_case)
    assert "```json" not in rec["messages"][-1]["content"]


def test_answer_block_is_parseable(snap_case: TestCase) -> None:
    rec = JSONLFormatter(include_answer_block=True).format_one(snap_case)
    content = rec["messages"][-1]["content"]
    match = ANSWER_BLOCK_RE.search(content)
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload["determination"] == snap_case.expected_outcome
    assert set(payload) == {"determination", "monthly_benefit", "rules_cited"}


def test_answer_block_benefit_is_null_when_ineligible(ineligible_snap_case: TestCase) -> None:
    rec = JSONLFormatter(include_answer_block=True).format_one(ineligible_snap_case)
    payload = json.loads(ANSWER_BLOCK_RE.search(rec["messages"][-1]["content"]).group(1))
    assert payload["monthly_benefit"] is None


def test_answer_block_rules_match_trace(snap_case: TestCase) -> None:
    rec = JSONLFormatter(include_answer_block=True).format_one(snap_case)
    payload = json.loads(ANSWER_BLOCK_RE.search(rec["messages"][-1]["content"]).group(1))
    assert payload["rules_cited"] == snap_case.rationale_trace.cited_rules()


def test_answer_block_is_last_thing_in_assistant_turn(snap_case: TestCase) -> None:
    rec = JSONLFormatter(include_answer_block=True).format_one(snap_case)
    content = rec["messages"][-1]["content"].rstrip()
    assert content.endswith("```")


def test_regex_tolerates_prose_after_the_block() -> None:
    # A model may keep talking after emitting the block; the scorer must still find it.
    text = 'blah\n```json\n{"determination": "eligible"}\n```\nThanks for asking!'
    assert json.loads(ANSWER_BLOCK_RE.search(text).group(1))["determination"] == "eligible"


def test_regex_takes_the_last_block_when_several_appear() -> None:
    text = (
        '```json\n{"determination": "ineligible"}\n```\nOn reflection:\n```json\n{"determination": "eligible"}\n```\n'
    )
    matches = ANSWER_BLOCK_RE.findall(text)
    assert json.loads(matches[-1])["determination"] == "eligible"
