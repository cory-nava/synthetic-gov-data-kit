"""Tests for govsynth.cli.output helpers."""

import io
import json

import pytest
from govsynth.cli.output import emit_json, emit_status, make_console
from rich.console import Console


def test_make_console_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    console = make_console()
    console.print("hello")
    captured = capsys.readouterr()
    assert "hello" in captured.err
    assert captured.out == ""


def test_make_console_quiet_suppresses_output(capsys: pytest.CaptureFixture[str]) -> None:
    console = make_console(quiet=True)
    console.print("should not appear")
    captured = capsys.readouterr()
    assert captured.err == "" or "should not appear" not in captured.err


def test_emit_json_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    emit_json({"key": "value"})
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["key"] == "value"
    assert captured.err == ""


def test_emit_status_json_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    # emit_status with as_json=True writes directly to sys.stderr (not via the console).
    # Use capsys to capture the real sys.stderr; do NOT pass a custom console here.
    console = make_console()
    emit_status({"status": "ok", "n": 5}, as_json=True, console=console)
    captured = capsys.readouterr()
    data = json.loads(captured.err)
    assert data["status"] == "ok"


def test_emit_status_rich_writes_to_console_file() -> None:
    buf = io.StringIO()
    console = Console(file=buf, highlight=False)
    emit_status({"status": "ok", "command": "generate"}, as_json=False, console=console)
    output = buf.getvalue()
    assert "ok" in output
