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
    #: Covers three populations, none of them a hard boundary call: (1) the
    #: household's distance from a threshold is unknown (profile not sampled
    #: relative to any boundary); (2) the household has elderly/disabled
    #: status, which changes several computations (gross test waived, medical
    #: deduction, uncapped shelter deduction, different asset cap) regardless
    #: of how far it sits from any limit; or (3) the household is near but not
    #: on a boundary -- further than the 1% HARD cutoff but not clearly clear
    #: of the limit either (e.g. +/-5%). In practice (2) and (3) are the
    #: largest contributors; BBCE state rules alone do not drive this label.
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
