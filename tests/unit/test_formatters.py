"""Unit tests for the YAML, JSONL, CSV, and HuggingFace dataset formatters."""

import csv
import json
from pathlib import Path

import pytest
import yaml
from govsynth.formatters.csv_fmt import CSVFormatter
from govsynth.formatters.jsonl import JSONLFormatter
from govsynth.formatters.yaml_fmt import YAMLFormatter
from govsynth.generators.snap_eligibility import SNAPEligibilityGenerator


@pytest.fixture(scope="module")
def cases() -> list:
    gen = SNAPEligibilityGenerator(fiscal_year=2026, state="VA")
    return gen.generate(n=5, seed=42)


class TestYAMLFormatter:
    def test_format_one_round_trips_case_id(self, cases: list) -> None:
        fmt = YAMLFormatter()
        text = fmt.format_one(cases[0])
        parsed = yaml.safe_load(text)
        assert parsed["case_id"] == cases[0].case_id

    def test_write_one_creates_readable_yaml_file(self, cases: list, tmp_path: Path) -> None:
        fmt = YAMLFormatter()
        out = tmp_path / "case.yaml"
        fmt.write_one(cases[0], out)
        assert out.exists()
        parsed = yaml.safe_load(out.read_text())
        assert parsed["case_id"] == cases[0].case_id
        assert parsed["rationale_trace"]["steps"]

    def test_write_many_one_file_per_case(self, cases: list, tmp_path: Path) -> None:
        fmt = YAMLFormatter()
        fmt.write_many(cases, tmp_path, one_file_per_case=True)
        yaml_files = list(tmp_path.glob("*.yaml"))
        assert len(yaml_files) == len(cases)

    def test_write_many_single_file(self, cases: list, tmp_path: Path) -> None:
        fmt = YAMLFormatter()
        fmt.write_many(cases, tmp_path, one_file_per_case=False)
        combined = tmp_path / "cases.yaml"
        assert combined.exists()
        parsed = yaml.safe_load(combined.read_text())
        assert len(parsed) == len(cases)


class TestJSONLFormatter:
    def test_format_one_has_chat_messages(self, cases: list) -> None:
        fmt = JSONLFormatter()
        row = fmt.format_one(cases[0])
        roles = [m["role"] for m in row["messages"]]
        assert roles == ["system", "user", "assistant"]
        assert row["case_id"] == cases[0].case_id

    def test_include_rationale_in_answer_appends_trace_text(self, cases: list) -> None:
        with_rationale = JSONLFormatter(include_rationale_in_answer=True).format_one(cases[0])
        without_rationale = JSONLFormatter(include_rationale_in_answer=False).format_one(cases[0])
        assistant_with = with_rationale["messages"][-1]["content"]
        assistant_without = without_rationale["messages"][-1]["content"]
        assert len(assistant_with) > len(assistant_without)
        assert assistant_without == cases[0].expected_answer

    def test_write_produces_one_json_object_per_line(self, cases: list, tmp_path: Path) -> None:
        fmt = JSONLFormatter()
        out = tmp_path / "cases.jsonl"
        fmt.write(cases, out)
        lines = out.read_text().strip().split("\n")
        assert len(lines) == len(cases)
        for line in lines:
            row = json.loads(line)
            assert "case_id" in row
            assert "messages" in row


class TestCSVFormatter:
    def test_format_row_has_all_columns(self, cases: list) -> None:
        fmt = CSVFormatter()
        row = fmt.format_row(cases[0])
        assert set(row.keys()) == set(fmt.COLUMNS)

    def test_variation_tags_are_pipe_joined(self, cases: list) -> None:
        fmt = CSVFormatter()
        row = fmt.format_row(cases[0])
        assert isinstance(row["variation_tags"], str)
        if cases[0].variation_tags:
            assert "|".join(cases[0].variation_tags) == row["variation_tags"]

    def test_write_produces_valid_csv_with_header(self, cases: list, tmp_path: Path) -> None:
        fmt = CSVFormatter()
        out = tmp_path / "cases.csv"
        fmt.write(cases, out)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(cases)
        assert rows[0]["case_id"] == cases[0].case_id


class TestHFDatasetFormatter:
    def test_to_dataset_creates_train_val_test_splits(self, cases: list) -> None:
        pytest.importorskip("datasets")
        from govsynth.formatters.hf_dataset import HFDatasetFormatter

        fmt = HFDatasetFormatter()
        ds = fmt.to_dataset(cases)
        assert set(ds.keys()) == {"train", "validation", "test"}
        total = sum(len(split) for split in ds.values())
        assert total == len(cases)

    def test_row_contains_flattened_fields(self, cases: list) -> None:
        pytest.importorskip("datasets")
        from govsynth.formatters.hf_dataset import _case_to_hf_row

        row = _case_to_hf_row(cases[0])
        assert row["case_id"] == cases[0].case_id
        assert row["program"] == "snap"
        assert json.loads(row["rationale_trace"])["conclusion"]

    def test_write_saves_to_disk(self, cases: list, tmp_path: Path) -> None:
        pytest.importorskip("datasets")
        from govsynth.formatters.hf_dataset import HFDatasetFormatter

        fmt = HFDatasetFormatter()
        out = tmp_path / "hf_ds"
        fmt.write(cases, out)
        assert out.exists()
        assert any(out.iterdir())

    def test_missing_datasets_dependency_raises_helpful_import_error(
        self, cases: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "datasets":
                raise ImportError("No module named 'datasets'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        from govsynth.formatters.hf_dataset import HFDatasetFormatter

        with pytest.raises(ImportError, match="pip install synthetic-gov-data-kit\\[hf\\]"):
            HFDatasetFormatter().to_dataset(cases)
