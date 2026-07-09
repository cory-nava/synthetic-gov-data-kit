"""synthetic-gov-data-kit — govsynth package.

Generate structured synthetic US government benefits data for LLM reasoning evaluation.
Python library for generating structured synthetic US government benefits data.

Quick start:
    from govsynth import Pipeline

    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=100, seed=42)
    pipeline.save(cases, "./output/", formats=["yaml", "jsonl", "csv"])
"""

from govsynth.evaluation import RationaleEvaluator, RationaleScore
from govsynth.fiscal_year import (
    DEFAULT_MEDICAID_CY,
    DEFAULT_SNAP_FY,
    DEFAULT_WIC_FY,
    FiscalYearConfig,
    current_federal_fy,
    fpl_year_for_program,
)
from govsynth.models import (
    Difficulty,
    OutputFormat,
    PolicyCitation,
    ProfileStrategy,
    Program,
    RationaleTrace,
    ReasoningStep,
    ScenarioBlock,
    TaskBlock,
    TaskType,
    TestCase,
)
from govsynth.pipeline import BatchPipeline, Pipeline
from govsynth.presets import PRESETS, list_presets

__version__ = "0.1.0"

__all__ = [
    # Top-level API
    "Pipeline",
    "BatchPipeline",
    "PRESETS",
    "list_presets",
    # Models
    "TestCase",
    "ScenarioBlock",
    "TaskBlock",
    "RationaleTrace",
    "ReasoningStep",
    "PolicyCitation",
    # Enums
    "Difficulty",
    "OutputFormat",
    "ProfileStrategy",
    "Program",
    "TaskType",
    # Fiscal year utilities
    "FiscalYearConfig",
    "current_federal_fy",
    "fpl_year_for_program",
    "DEFAULT_SNAP_FY",
    "DEFAULT_WIC_FY",
    "DEFAULT_MEDICAID_CY",
    # Evaluation
    "RationaleEvaluator",
    "RationaleScore",
]
