"""Abstract base class for all test case generators."""

from __future__ import annotations

from abc import ABC, abstractmethod

from govsynth.models.test_case import TestCase


class Generator(ABC):
    """Abstract base for all test case generators.

    A Generator wraps a `DataSource` and a profile-sampling strategy to
    produce a batch of CivBench-compatible `TestCase` objects for one
    program/jurisdiction, each with a populated `RationaleTrace`.
    """

    @property
    @abstractmethod
    def program(self) -> str:
        """The program identifier, e.g. 'snap'."""
        ...

    @abstractmethod
    def generate(
        self,
        n: int,
        profile_strategy: str = "edge_saturated",
        seed: int | None = None,
    ) -> list[TestCase]:
        """Generate n test cases.

        Args:
            n: Number of cases to generate.
            profile_strategy: Profile-sampling strategy, e.g. 'edge_saturated',
                'realistic', 'uniform', or 'adversarial'.
            seed: RNG seed for reproducibility.
        """
        ...
