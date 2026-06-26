"""Preset registry — maps preset names to fully configured pipeline configs.

A preset bundles: source class + constructor args + generator class + profile strategy.
This is the primary ergonomic entry point for most users.

Usage:
    from govsynth.presets import PRESETS
    config = PRESETS["snap.va"]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class PresetConfig:
    """Configuration bundle for a Pipeline preset."""

    program: str
    source_class: str  # dotted import path
    source_kwargs: dict[str, Any]
    generator_class: str  # dotted import path
    generator_kwargs: dict[str, Any]
    profile_strategy: str = "edge_saturated"
    description: str = ""


# ---------------------------------------------------------------------------
# Preset registry
# ---------------------------------------------------------------------------

PRESETS: dict[str, PresetConfig] = {
    # ── SNAP ──────────────────────────────────────────────────────────────
    "snap.va": PresetConfig(
        program="snap",
        source_class="govsynth.sources.us.snap_bbce.SNAPBBCESource",
        source_kwargs={"fiscal_year": 2026, "state": "VA"},
        generator_class="govsynth.generators.snap_eligibility.SNAPEligibilityGenerator",
        generator_kwargs={"fiscal_year": 2026, "state": "VA"},
        profile_strategy="edge_saturated",
        description="Virginia SNAP FY2026 — BBCE (200% FPL gross, no asset test)",
    ),
    "snap.ca": PresetConfig(
        program="snap",
        source_class="govsynth.sources.us.snap_bbce.SNAPBBCESource",
        source_kwargs={"fiscal_year": 2026, "state": "CA"},
        generator_class="govsynth.generators.snap_eligibility.SNAPEligibilityGenerator",
        generator_kwargs={"fiscal_year": 2026, "state": "CA"},
        profile_strategy="edge_saturated",
        description="California SNAP FY2026 — BBCE (200% FPL gross, no asset test)",
    ),
    "snap.tx": PresetConfig(
        program="snap",
        source_class="govsynth.sources.us.snap_bbce.SNAPBBCESource",
        source_kwargs={"fiscal_year": 2026, "state": "TX"},
        generator_class="govsynth.generators.snap_eligibility.SNAPEligibilityGenerator",
        generator_kwargs={"fiscal_year": 2026, "state": "TX"},
        profile_strategy="edge_saturated",
        description="Texas SNAP FY2026 — BBCE (165% FPL gross, $5,000 asset cap)",
    ),
    "snap.md": PresetConfig(
        program="snap",
        source_class="govsynth.sources.us.snap_bbce.SNAPBBCESource",
        source_kwargs={"fiscal_year": 2026, "state": "MD"},
        generator_class="govsynth.generators.snap_eligibility.SNAPEligibilityGenerator",
        generator_kwargs={"fiscal_year": 2026, "state": "MD"},
        profile_strategy="edge_saturated",
        description="Maryland SNAP FY2026 — BBCE (200% FPL gross, no asset test)",
    ),
    # ── WIC ───────────────────────────────────────────────────────────────
    "wic.national": PresetConfig(
        program="wic",
        source_class="govsynth.sources.us.wic.WICSource",
        source_kwargs={"fiscal_year": 2026},
        generator_class="govsynth.generators.wic_eligibility.WICEligibilityGenerator",
        generator_kwargs={"fiscal_year": 2026},
        profile_strategy="edge_saturated",
        description="WIC FY2026 national income eligibility (185% FPL)",
    ),
}


def list_presets() -> None:
    """Print all available presets with descriptions."""
    print(f"\n{'Preset':<22} {'Program':<10} {'Description'}")
    print("-" * 70)
    for name, cfg in sorted(PRESETS.items()):
        print(f"{name:<22} {cfg.program:<10} {cfg.description}")
    print()
