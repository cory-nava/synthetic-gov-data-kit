"""Unit tests for the preset registry.

Every preset must be a fully working configuration: its source_class and
generator_class must import, and running it end-to-end through Pipeline
must produce valid cases. This is the regression guard against presets
that reference renamed/moved classes (see govsynth/presets.py's dead
`source_class` field, which nothing else in the codebase consumes).
"""

import importlib

import pytest
from govsynth import Pipeline
from govsynth.presets import PRESETS, list_presets


@pytest.mark.parametrize("preset_name", sorted(PRESETS.keys()))
def test_source_class_is_importable(preset_name: str) -> None:
    config = PRESETS[preset_name]
    module_path, class_name = config.source_class.rsplit(".", 1)
    module = importlib.import_module(module_path)
    assert hasattr(module, class_name)


@pytest.mark.parametrize("preset_name", sorted(PRESETS.keys()))
def test_generator_class_is_importable(preset_name: str) -> None:
    config = PRESETS[preset_name]
    module_path, class_name = config.generator_class.rsplit(".", 1)
    module = importlib.import_module(module_path)
    assert hasattr(module, class_name)


@pytest.mark.parametrize("preset_name", sorted(PRESETS.keys()))
def test_preset_generates_valid_cases_end_to_end(preset_name: str) -> None:
    pipeline = Pipeline.from_preset(preset_name)
    cases = pipeline.generate(n=3, seed=42)
    assert len(cases) > 0
    for case in cases:
        assert case.is_valid()
        assert case.program == PRESETS[preset_name].program


def test_all_presets_have_a_description() -> None:
    for name, config in PRESETS.items():
        assert config.description, f"preset '{name}' has no description"


def test_list_presets_does_not_raise(capsys: pytest.CaptureFixture[str]) -> None:
    list_presets()
    captured = capsys.readouterr()
    for name in PRESETS:
        assert name in captured.out
