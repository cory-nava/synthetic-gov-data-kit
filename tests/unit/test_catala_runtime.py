"""Unit tests for the CatalaRuntime subprocess wrapper.

No real `catala` binary is available in CI/dev (see runtime.py's module
docstring), so every test here mocks `subprocess.run` and `shutil.which`
and asserts on the command construction + output parsing instead.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from govsynth.sources.catala.runtime import (
    CatalaNotAvailableError,
    CatalaRuntime,
    CatalaRuntimeError,
    _coerce_scalar,
    _parse_human_result_lines,
    _parse_interpret_stdout,
    _parse_trace,
    find_catala_binary,
)


@pytest.fixture
def ruleset_file(tmp_path: Path) -> Path:
    path = tmp_path / "program.catala_en"
    path.write_text("# a catala ruleset")
    return path


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestFindCatalaBinary:
    def test_raises_when_not_on_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(CatalaNotAvailableError, match="not found on PATH"):
            find_catala_binary()

    def test_returns_resolved_path_when_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
        assert find_catala_binary() == "/usr/bin/catala"

    def test_respects_explicit_binary_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: f"/opt/{name}")
        assert find_catala_binary("catala-dev") == "/opt/catala-dev"


class TestCatalaRuntimeConstruction:
    def test_missing_ruleset_file_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        with pytest.raises(FileNotFoundError):
            CatalaRuntime(tmp_path / "does_not_exist.catala_en")

    def test_missing_binary_raises(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(CatalaNotAvailableError):
            CatalaRuntime(ruleset_file)


class TestScopeSchema:
    def test_parses_two_element_array(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        input_schema = {"type": "object", "properties": {"income": {"type": "number"}}}
        output_schema = {"type": "object", "properties": {"eligible": {"type": "boolean"}}}

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert "json-schema" in command
            assert str(ruleset_file) in command
            assert "--scope=Eligibility" in command
            return _completed(stdout=json.dumps([input_schema, output_schema]))

        monkeypatch.setattr(subprocess, "run", fake_run)
        runtime = CatalaRuntime(ruleset_file)
        got_input, got_output = runtime.scope_schema("Eligibility")
        assert got_input == input_schema
        assert got_output == output_schema

    def test_nonzero_exit_raises_with_stderr(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _completed(returncode=1, stderr="Scope 'X' not found"),
        )
        runtime = CatalaRuntime(ruleset_file)
        with pytest.raises(CatalaRuntimeError, match="Scope 'X' not found"):
            runtime.scope_schema("X")

    def test_invalid_json_raises(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout="not json"))
        runtime = CatalaRuntime(ruleset_file)
        with pytest.raises(CatalaRuntimeError, match="Could not parse"):
            runtime.scope_schema("X")

    def test_wrong_shape_raises(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout=json.dumps({})))
        runtime = CatalaRuntime(ruleset_file)
        with pytest.raises(CatalaRuntimeError, match="2-element"):
            runtime.scope_schema("X")


class TestInterpret:
    def test_json_output_parsed_directly(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _completed(stdout=json.dumps({"eligible": True, "amount": 42})),
        )
        runtime = CatalaRuntime(ruleset_file)
        result = runtime.interpret("Eligibility", {"income": 1000})
        assert result.outputs == {"eligible": True, "amount": 42}
        assert result.trace == []

    def test_inputs_sent_as_json_on_stdin(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        captured: dict[str, Any] = {}

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["input"] = kwargs.get("input")
            return _completed(stdout=json.dumps({"eligible": False}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        runtime = CatalaRuntime(ruleset_file)
        runtime.interpret("Eligibility", {"income": 5000, "household_size": 2})
        assert json.loads(captured["input"]) == {"income": 5000, "household_size": 2}

    def test_no_inputs_sends_no_stdin(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        captured: dict[str, Any] = {}

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured["input"] = kwargs.get("input")
            return _completed(stdout=json.dumps({"eligible": False}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        CatalaRuntime(ruleset_file).interpret("Eligibility", None)
        assert captured["input"] is None

    def test_envelope_unwrapped(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: _completed(stdout=json.dumps({"results": {"eligible": True}})),
        )
        result = CatalaRuntime(ruleset_file).interpret("Eligibility", {})
        assert result.outputs == {"eligible": True}

    def test_human_format_fallback(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        human_stdout = "[RESULT] eligible = true\n[RESULT] benefit_amount = $123.50\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout=human_stdout))
        result = CatalaRuntime(ruleset_file).interpret("Eligibility", {})
        assert result.outputs == {"eligible": True, "benefit_amount": 123.50}

    def test_unparseable_output_raises(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(stdout="garbage\n"))
        with pytest.raises(CatalaRuntimeError, match="Could not parse"):
            CatalaRuntime(ruleset_file).interpret("Eligibility", {})

    def test_nonzero_exit_raises(self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **kw: _completed(returncode=1, stderr="boom")
        )
        with pytest.raises(CatalaRuntimeError, match="boom"):
            CatalaRuntime(ruleset_file).interpret("Eligibility", {})

    def test_timeout_raises_catala_runtime_error(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

        def fake_run(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="catala", timeout=1)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(CatalaRuntimeError, match="timed out"):
            CatalaRuntime(ruleset_file, timeout=1).interpret("Eligibility", {})

    def test_missing_executable_raises_catala_runtime_error(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")

        def fake_run(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
            raise OSError("No such file or directory")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(CatalaRuntimeError, match="Failed to execute catala"):
            CatalaRuntime(ruleset_file).interpret("Eligibility", {})

    def test_with_trace_writes_and_reads_trace_file(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        trace_events = [{"variable": "income_test", "value": True}]

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            trace_arg = next(a for a in command if a.startswith("--trace="))
            trace_path = Path(trace_arg.split("=", 1)[1])
            trace_path.write_text(json.dumps(trace_events))
            return _completed(stdout=json.dumps({"eligible": True}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = CatalaRuntime(ruleset_file).interpret("Eligibility", {}, with_trace=True)
        assert result.outputs == {"eligible": True}
        assert result.trace == trace_events

    def test_with_trace_cleans_up_temp_file(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        seen_paths: list[Path] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            trace_arg = next(a for a in command if a.startswith("--trace="))
            seen_paths.append(Path(trace_arg.split("=", 1)[1]))
            return _completed(stdout=json.dumps({"eligible": True}))

        monkeypatch.setattr(subprocess, "run", fake_run)
        CatalaRuntime(ruleset_file).interpret("Eligibility", {}, with_trace=True)
        assert not seen_paths[0].exists()

    def test_with_trace_missing_file_leaves_empty_trace(
        self, ruleset_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulates a catala version that doesn't honor --trace=<path> at all.
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/catala")
        monkeypatch.setattr(
            subprocess, "run", lambda *a, **kw: _completed(stdout=json.dumps({"eligible": True}))
        )
        result = CatalaRuntime(ruleset_file).interpret("Eligibility", {}, with_trace=True)
        assert result.trace == []


class TestCoerceScalar:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("true", True),
            ("false", False),
            ("yes", True),
            ("no", False),
            ("42", 42),
            ("3.14", 3.14),
            ("$1,234.56", 1234.56),
            ("$500", 500),
            ('"a string"', "a string"),
        ],
    )
    def test_coerce_scalar(self, raw: str, expected: object) -> None:
        assert _coerce_scalar(raw) == expected


class TestParseHumanResultLines:
    def test_parses_multiple_result_lines(self) -> None:
        stdout = "some banner text\n[RESULT] eligible = true\n[RESULT] amount = 250\nnoise\n"
        assert _parse_human_result_lines(stdout) == {"eligible": True, "amount": 250}

    def test_no_result_lines_returns_empty(self) -> None:
        assert _parse_human_result_lines("nothing interesting here") == {}


class TestParseInterpretStdout:
    def test_empty_stdout_falls_through_to_error(self) -> None:
        with pytest.raises(CatalaRuntimeError):
            _parse_interpret_stdout("", ["catala"])


class TestParseTrace:
    def test_empty_string_returns_empty_list(self) -> None:
        assert _parse_trace("") == []

    def test_json_array_of_objects(self) -> None:
        events = [{"rule": "a"}, {"rule": "b"}]
        assert _parse_trace(json.dumps(events)) == events

    def test_single_json_object(self) -> None:
        assert _parse_trace(json.dumps({"rule": "a"})) == [{"rule": "a"}]

    def test_ndjson(self) -> None:
        text = '{"rule": "a"}\n{"rule": "b"}\n'
        assert _parse_trace(text) == [{"rule": "a"}, {"rule": "b"}]

    def test_garbage_returns_empty_list_not_raise(self) -> None:
        assert _parse_trace("not json at all, just prose") == []

    def test_json_array_of_scalars_filters_them_out(self) -> None:
        assert _parse_trace(json.dumps([1, 2, "x"])) == []
