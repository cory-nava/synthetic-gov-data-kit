"""Shared enums and constants used across the library."""

from enum import Enum


class TaskType(str, Enum):
    """The type of task a test case is evaluating."""

    ELIGIBILITY = "eligibility_determination"
    POLICY_QA = "policy_qa"
    FORM = "form_completion"
    AGENTIC = "agentic_task"
    COMPARATIVE = "comparative"


class Difficulty(str, Enum):
    """Difficulty level of a test case.

    This describes how hard the case is to reason about correctly, not which
    program, state, or profile-sampling strategy produced it.
    """

    #: Household sits clearly clear of every relevant limit (known distance
    #: from a threshold, well beyond it); the eligibility call has no
    #: ambiguity a model could plausibly get wrong.
    EASY = "easy"
    #: Either the household's distance from a threshold is unknown (profile
    #: not sampled relative to any boundary), or a real complicating factor
    #: (elderly/disabled status, BBCE state rules) makes the determination
    #: nontrivial even when not at a hard boundary.
    MEDIUM = "medium"
    #: Household sits within 1% of a named policy threshold: the correct
    #: answer depends on getting the boundary condition exactly right.
    HARD = "hard"
    #: A special-population edge case (EDGE_CASES.md Group A) that exists
    #: specifically because models tend to misapply it.
    ADVERSARIAL = "adversarial"


class ProfileStrategy(str, Enum):
    """Strategy for sampling citizen profiles during generation."""

    UNIFORM = "uniform"
    EDGE_SATURATED = "edge_saturated"
    REALISTIC = "realistic"
    ADVERSARIAL = "adversarial"
    JURISDICTION_SWEEP = "jurisdiction_sweep"
    CUSTOM = "custom"


class OutputFormat(str, Enum):
    """Supported output formats."""

    YAML = "yaml"
    JSONL = "jsonl"
    CSV = "csv"
    HF_DATASET = "hf_dataset"


class CitizenshipStatus(str, Enum):
    """Citizenship/immigration status for eligibility purposes."""

    CITIZEN = "citizen"
    QUALIFIED_ALIEN = "qualified_alien"
    NON_QUALIFIED_ALIEN = "non_qualified_alien"
    UNKNOWN = "unknown"


class Program(str, Enum):
    """Supported US government benefits programs."""

    SNAP = "snap"
    WIC = "wic"
    MEDICAID = "medicaid"
    CHIP = "chip"
    SECTION_8 = "section_8"
    LIHEAP = "liheap"
    TANF = "tanf"


# Known states + DC for validation
US_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
}

KNOWN_PROGRAMS = {p.value for p in Program}
