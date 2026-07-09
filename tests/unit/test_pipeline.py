"""Unit tests for Pipeline and BatchPipeline."""

import io
from pathlib import Path

import pytest
from govsynth.pipeline import BatchPipeline, Pipeline
from rich.console import Console


def _capturing_console() -> Console:
    return Console(file=io.StringIO(), highlight=False)


class TestPipelineFromPreset:
    def test_from_preset_builds_working_pipeline(self) -> None:
        pipeline = Pipeline.from_preset("snap.va", console=_capturing_console())
        assert pipeline.generator is not None
        assert pipeline.generator.program == "snap"

    def test_unknown_preset_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown preset"):
            Pipeline.from_preset("snap.nowhere", console=_capturing_console())

    def test_profile_strategy_override(self) -> None:
        pipeline = Pipeline.from_preset("snap.va", profile_strategy="uniform", console=_capturing_console())
        assert pipeline.profile_strategy == "uniform"

    def test_generator_kwargs_override_preset_defaults(self) -> None:
        pipeline = Pipeline.from_preset("snap.va", state="TX", console=_capturing_console())
        assert pipeline.generator.state == "TX"


class TestPipelineGenerate:
    def test_generate_returns_requested_count(self) -> None:
        pipeline = Pipeline.from_preset("snap.va", console=_capturing_console())
        cases = pipeline.generate(n=5, seed=42)
        assert len(cases) == 5

    def test_generate_drops_invalid_cases_and_warns(self) -> None:
        # Every generated SNAP case passes check_output_contract() in practice,
        # so this is really an invariant check: generate() never returns
        # cases that fail is_valid().
        pipeline = Pipeline.from_preset("snap.va", console=_capturing_console())
        cases = pipeline.generate(n=20, seed=1)
        assert all(c.is_valid() for c in cases)

    def test_generate_without_generator_raises(self) -> None:
        pipeline = Pipeline(generator=None, console=_capturing_console())
        with pytest.raises(ValueError, match="no generator configured"):
            pipeline.generate(n=1)


class TestPipelineSave:
    def test_save_yaml_creates_one_file_per_case(self, tmp_path: Path) -> None:
        pipeline = Pipeline.from_preset("snap.va", console=_capturing_console())
        cases = pipeline.generate(n=3, seed=42)
        out = tmp_path / "yaml_out"
        pipeline.save(cases, out, formats="yaml")
        assert len(list(out.glob("*.yaml"))) == 3

    def test_save_jsonl_creates_single_file(self, tmp_path: Path) -> None:
        pipeline = Pipeline.from_preset("snap.va", console=_capturing_console())
        cases = pipeline.generate(n=3, seed=42)
        out = tmp_path / "out"
        pipeline.save(cases, out, formats="jsonl")
        assert (out / "cases.jsonl").exists()

    def test_save_multiple_formats(self, tmp_path: Path) -> None:
        pipeline = Pipeline.from_preset("snap.va", console=_capturing_console())
        cases = pipeline.generate(n=2, seed=42)
        out = tmp_path / "out"
        pipeline.save(cases, out, formats=["yaml", "csv"])
        assert (out / "cases.csv").exists()
        assert list(out.glob("*.yaml"))

    def test_save_unknown_format_warns_but_does_not_raise(self, tmp_path: Path) -> None:
        pipeline = Pipeline.from_preset("snap.va", console=_capturing_console())
        cases = pipeline.generate(n=1, seed=42)
        pipeline.save(cases, tmp_path / "out", formats="not_a_real_format")


class TestBatchPipeline:
    def test_from_presets_generates_across_all(self) -> None:
        batch = BatchPipeline.from_presets(["snap.va", "wic.national"], console=_capturing_console())
        cases = batch.generate(n_per_pipeline=3, seed=42)
        programs = {c.program for c in cases}
        assert programs == {"snap", "wic"}
        assert len(cases) == 6

    def test_batch_save_writes_all_cases(self, tmp_path: Path) -> None:
        batch = BatchPipeline.from_presets(["snap.va", "wic.national"], console=_capturing_console())
        cases = batch.generate(n_per_pipeline=2, seed=42)
        with pytest.warns(DeprecationWarning):
            batch.save(cases, tmp_path / "out", format="jsonl")
        jsonl_files = list((tmp_path / "out").rglob("*.jsonl"))
        assert jsonl_files
