"""Tests for govsynth.cli.readers — file deserialization."""

from pathlib import Path

import pytest
from govsynth.cli.readers import detect_format, read_yaml


def test_detect_format_yaml():
    assert detect_format(Path("output.yaml")) == "yaml"
    assert detect_format(Path("output.yml")) == "yaml"


def test_detect_format_jsonl():
    assert detect_format(Path("cases.jsonl")) == "jsonl"


def test_detect_format_csv():
    assert detect_format(Path("cases.csv")) == "csv"


def test_detect_format_force_overrides_extension():
    assert detect_format(Path("cases.txt"), force="yaml") == "yaml"


def test_detect_format_unknown_extension_raises():
    with pytest.raises(SystemExit) as exc_info:
        detect_format(Path("cases.txt"))
    assert exc_info.value.code == 2


def test_read_yaml_returns_test_cases(tmp_path):
    """read_yaml can round-trip a YAML file written by YAMLFormatter."""
    from govsynth.pipeline import Pipeline

    pipeline = Pipeline.from_preset("snap.va")
    cases = pipeline.generate(n=2, seed=42)
    pipeline.save(cases, tmp_path / "out", formats=["yaml"])

    yaml_files = list((tmp_path / "out").glob("*.yaml"))
    assert len(yaml_files) == 2

    loaded = read_yaml(yaml_files[0])
    assert len(loaded) == 1
    original_ids = {c.case_id for c in cases}
    assert loaded[0].case_id in original_ids
    # Confirm full round-trip: the deserialized case must pass schema validation
    assert loaded[0].is_valid(), f"Round-tripped case failed validation: {loaded[0].validate()}"
