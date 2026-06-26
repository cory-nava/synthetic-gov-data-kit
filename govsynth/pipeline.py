"""High-level Pipeline and BatchPipeline orchestration.

The Pipeline is the primary entry point for most users. It wires together
a generator, formatters, and output handling into a clean one-liner API.

Usage:
    from govsynth import Pipeline

    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=100, seed=42)
    pipeline.save(cases, "./output/", formats=["yaml", "jsonl", "csv"])
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from govsynth.models.enums import OutputFormat
from govsynth.models.test_case import TestCase
from govsynth.presets import PRESETS, PresetConfig


def _import_class(dotted_path: str) -> Any:
    """Import a class from a dotted module path."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class Pipeline:
    """Orchestrates test case generation for a single program/jurisdiction.

    Example:
        pipeline = Pipeline.from_preset("snap.va")
        cases = pipeline.generate(n=100, seed=42)
        pipeline.save(cases, "./output/snap_va/", formats=["yaml", "jsonl"])
    """

    def __init__(
        self,
        generator: Any,
        profile_strategy: str = "edge_saturated",
        console: Console | None = None,
    ) -> None:
        self.generator = generator
        self.profile_strategy = profile_strategy
        self._console = console if console is not None else Console(stderr=True)

    @classmethod
    def from_preset(
        cls,
        preset_name: str,
        profile_strategy: str | None = None,
        console: Console | None = None,
        **generator_kwargs: Any,
    ) -> Pipeline:
        """Create a Pipeline from a named preset.

        Args:
            preset_name: One of the keys in govsynth.presets.PRESETS,
                         e.g. 'snap.va', 'snap.ca', 'wic.national'
            profile_strategy: Override the preset's default strategy.
            console: Optional Rich Console instance. Defaults to a stderr console.
            **generator_kwargs: Override specific generator constructor args.
        """
        if preset_name not in PRESETS:
            available = ", ".join(sorted(PRESETS.keys()))
            raise ValueError(f"Unknown preset '{preset_name}'. Available presets: {available}")

        config: PresetConfig = PRESETS[preset_name]
        gen_class = _import_class(config.generator_class)
        kwargs = {**config.generator_kwargs, **generator_kwargs}
        generator = gen_class(**kwargs)

        strategy = profile_strategy or config.profile_strategy
        return cls(generator=generator, profile_strategy=strategy, console=console)

    def generate(self, n: int, seed: int | None = None) -> list[TestCase]:
        """Generate n test cases.

        Args:
            n: Number of cases to generate.
            seed: RNG seed for reproducibility. Use same seed to get same cases.

        Returns:
            List of validated TestCase objects.
        """
        program = getattr(self.generator, "program", "unknown")
        state = getattr(self.generator, "state", "")
        label = f"{program}.{state.lower()}" if state else program

        with Progress(
            SpinnerColumn(),
            TextColumn(f"[bold cyan]Generating {n} {label} cases..."),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self._console,
        ) as progress:
            task = progress.add_task("generate", total=n)

            # Generate in batches of 25 to update progress bar
            cases: list[TestCase] = []
            batch_size = min(25, n)
            remaining = n
            batch_seed = seed

            while remaining > 0:
                batch_n = min(batch_size, remaining)
                batch = self.generator.generate(
                    n=batch_n,
                    profile_strategy=self.profile_strategy,
                    seed=batch_seed,
                )
                cases.extend(batch)
                progress.advance(task, batch_n)
                remaining -= batch_n
                if batch_seed is not None:
                    batch_seed += 1

        invalid = [c for c in cases if not c.is_valid()]
        if invalid:
            self._console.print(
                f"[yellow]Warning:[/yellow] {len(invalid)} cases failed validation "
                f"and were excluded."
            )
            cases = [c for c in cases if c.is_valid()]

        self._console.print(f"[green]✓[/green] Generated [bold]{len(cases)}[/bold] valid cases.")
        return cases

    def save(
        self,
        cases: list[TestCase],
        output: str | Path,
        formats: list[str] | str = "yaml",
        one_file_per_case: bool = True,
    ) -> None:
        """Save cases to disk in one or more formats.

        Args:
            cases: List of TestCase objects to save.
            output: Output file path (for single-file formats) or directory.
            formats: One or more of: 'yaml', 'jsonl', 'csv', 'hf_dataset'
            one_file_per_case: For yaml, write one .yaml per case (default True).
        """
        if isinstance(formats, str):
            formats = [formats]

        output = Path(output)

        for fmt in formats:
            fmt = fmt.lower().strip()

            if fmt == OutputFormat.YAML.value:
                from govsynth.formatters.yaml_fmt import YAMLFormatter

                yaml_formatter = YAMLFormatter()
                out_dir = output if output.suffix == "" else output.parent / "yaml"
                yaml_formatter.write_many(cases, out_dir, one_file_per_case=one_file_per_case)
                self._console.print(f"[green]✓[/green] Saved YAML → {out_dir}/")

            elif fmt == OutputFormat.JSONL.value:
                from govsynth.formatters.jsonl import JSONLFormatter

                jsonl_formatter = JSONLFormatter()
                out_path = output if output.suffix == ".jsonl" else output / "cases.jsonl"
                jsonl_formatter.write(cases, out_path)
                self._console.print(f"[green]✓[/green] Saved JSONL → {out_path}")

            elif fmt == OutputFormat.CSV.value:
                from govsynth.formatters.csv_fmt import CSVFormatter

                csv_formatter = CSVFormatter()
                out_path = output if output.suffix == ".csv" else output / "cases.csv"
                csv_formatter.write(cases, out_path)
                self._console.print(f"[green]✓[/green] Saved CSV → {out_path}")

            elif fmt == OutputFormat.HF_DATASET.value:
                try:
                    from govsynth.formatters.hf_dataset import HFDatasetFormatter

                    hf_formatter = HFDatasetFormatter()
                    out_dir = output if output.suffix == "" else output.parent / "hf_dataset"
                    hf_formatter.write(cases, out_dir)
                    self._console.print(f"[green]✓[/green] Saved HF Dataset → {out_dir}/")
                except ImportError:
                    self._console.print(
                        "[yellow]Warning:[/yellow] HuggingFace datasets not installed. "
                        "Run: pip install synthetic-gov-data-kit[hf]"
                    )
            else:
                self._console.print(f"[red]Unknown format:[/red] '{fmt}' — skipped.")


class BatchPipeline:
    """Runs multiple Pipelines and aggregates results.

    Example:
        batch = BatchPipeline.from_presets(["snap.va", "snap.ca", "snap.tx"])
        cases = batch.generate(n_per_pipeline=100, seed=42)
        batch.save(cases, "./output-suite/", format="yaml")
    """

    def __init__(self, pipelines: list[Pipeline], console: Console | None = None) -> None:
        self.pipelines = pipelines
        self._console = console if console is not None else Console(stderr=True)

    @classmethod
    def from_presets(
        cls, preset_names: list[str], console: Console | None = None, **kwargs: Any
    ) -> BatchPipeline:
        """Create a BatchPipeline from a list of preset names."""
        pipelines = [Pipeline.from_preset(name, console=console, **kwargs) for name in preset_names]
        return cls(pipelines, console=console)

    def generate(self, n_per_pipeline: int, seed: int | None = None) -> list[TestCase]:
        """Generate cases from all pipelines.

        Args:
            n_per_pipeline: Number of cases per pipeline.
            seed: Base seed. Each pipeline gets seed + i for reproducibility.

        Returns:
            Flat list of all generated cases across all pipelines.
        """
        all_cases: list[TestCase] = []
        for i, pipeline in enumerate(self.pipelines):
            pipeline_seed = (seed + i) if seed is not None else None
            cases = pipeline.generate(n=n_per_pipeline, seed=pipeline_seed)
            all_cases.extend(cases)

        self._console.print(
            f"\n[bold green]Batch complete:[/bold green] "
            f"{len(all_cases)} total cases from {len(self.pipelines)} pipelines."
        )
        return all_cases

    def save(self, cases: list[TestCase], output_dir: str | Path, format: str = "yaml") -> None:
        """Deprecated: use Pipeline.save() directly. Will be removed in v0.2."""
        import warnings

        warnings.warn(
            "BatchPipeline.save() is deprecated; use Pipeline.save() directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        p = Pipeline(generator=None, console=self._console)
        p.save(cases, output_dir, formats=[format])
