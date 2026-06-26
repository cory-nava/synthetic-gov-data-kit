"""Tests that Pipeline and BatchPipeline respect an injected Console instance."""

import io

from govsynth.pipeline import BatchPipeline, Pipeline
from rich.console import Console


def _capturing_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, highlight=False), buf


def test_pipeline_uses_injected_console():
    """Pipeline.generate() output goes to the injected console, not the module-level one."""
    console, buf = _capturing_console()
    pipeline = Pipeline.from_preset("snap.va", console=console)
    pipeline.generate(n=2, seed=42)
    output = buf.getvalue()
    assert "Generated" in output


def test_batch_pipeline_uses_injected_console():
    """BatchPipeline.generate() output goes to the injected console."""
    console, buf = _capturing_console()
    batch = BatchPipeline.from_presets(["snap.va"], console=console)
    batch.generate(n_per_pipeline=2, seed=42)
    output = buf.getvalue()
    assert "Batch complete" in output


def test_pipeline_default_console_does_not_write_to_stdout(capsys):
    """When no console is passed, Pipeline output does not go to stdout."""
    pipeline = Pipeline.from_preset("snap.va")
    pipeline.generate(n=2, seed=42)
    captured = capsys.readouterr()
    # All Rich output should go to stderr, not contaminate stdout
    assert captured.out == ""
