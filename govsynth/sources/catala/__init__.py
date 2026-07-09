"""Catala ruleset adapter.

Lets a Catala ruleset (https://catala-lang.org) be imported and used as the
source of both eligibility logic and thresholds for synthetic data
generation, instead of the hand-written data/thresholds/*.json + Python
is_eligible() pattern used by the built-in SNAP/WIC/Medicaid sources.

Requires the `catala` CLI on PATH. See runtime.py's module docstring for
important caveats about how this was implemented and validated.
"""

from govsynth.sources.catala.ruleset import CatalaMappingError, CatalaRuleset
from govsynth.sources.catala.runtime import (
    CatalaNotAvailableError,
    CatalaResult,
    CatalaRuntime,
    CatalaRuntimeError,
)

__all__ = [
    "CatalaMappingError",
    "CatalaNotAvailableError",
    "CatalaResult",
    "CatalaRuleset",
    "CatalaRuntime",
    "CatalaRuntimeError",
]
