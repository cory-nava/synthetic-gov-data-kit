"""Subprocess wrapper around the `catala` CLI.

Shells out to a locally installed Catala compiler/interpreter
(https://catala-lang.org). Built against Catala's documented CLI surface:

    catala interpret <file> --scope=<Name> --format=json [--trace=<path>]
    catala json-schema <file> --scope=<Name>

IMPORTANT -- read before debugging a CatalaRuntimeError: this module was
implemented without access to a live `catala` binary. Installing Catala
requires the OCaml/opam toolchain, and `opam.ocaml.org` was not reachable
from the network this was written in (no PyPI package exists either). The
command construction and output parsing below reflect the best documented
understanding of the CLI available at the time, but were never exercised
against a real compiler.

If a CatalaRuntimeError's `command`/`output` shows the CLI rejecting a flag,
or `catala`'s actual output doesn't match what's parsed here, run the failing
command by hand (`catala --help`, `catala interpret --help`,
`catala json-schema --help`) against your installed version and adjust
`CatalaRuntime.interpret` / `CatalaRuntime.scope_schema` / the `_parse_*`
functions below to match. The parsing functions are intentionally permissive
(JSON first, human-readable `[RESULT] var = value` lines as a fallback, and a
trace that degrades to empty rather than raising) specifically because exact
output shape could not be confirmed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CatalaNotAvailableError(RuntimeError):
    """Raised when the `catala` binary can't be found on PATH."""


class CatalaRuntimeError(RuntimeError):
    """Raised when a `catala` subprocess invocation fails, times out, or
    produces output this module can't parse.

    Carries the exact command and stderr/stdout so a CLI/version mismatch
    can be diagnosed directly, instead of guessing from a generic message.
    """

    def __init__(self, message: str, command: list[str], output: str = "") -> None:
        self.command = command
        self.output = output
        rendered = " ".join(command)
        super().__init__(f"{message}\nCommand: {rendered}\nOutput: {output[:2000]}")


def find_catala_binary(binary: str | None = None) -> str:
    """Locate the catala executable, or raise CatalaNotAvailableError."""
    candidate = binary or "catala"
    resolved = shutil.which(candidate)
    if resolved is None:
        raise CatalaNotAvailableError(
            f"'{candidate}' not found on PATH. Install Catala from "
            "https://catala-lang.org (`opam install catala`), or pass an explicit "
            "`binary=` path pointing at a catala executable / wrapper script."
        )
    return resolved


@dataclass
class CatalaResult:
    """Parsed result of one `catala interpret` invocation."""

    outputs: dict[str, Any]
    trace: list[dict[str, Any]] = field(default_factory=list)
    raw_stdout: str = ""


_RESULT_LINE = re.compile(r"^\[RESULT\]\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.+?)\s*$")


def _coerce_scalar(raw: str) -> bool | int | float | str:
    """Best-effort conversion of a human-format [RESULT] value to a Python scalar."""
    text = raw.strip()
    if text in ("true", "yes"):
        return True
    if text in ("false", "no"):
        return False
    money = re.fullmatch(r"\$?(-?[\d,]+(?:\.\d+)?)", text)
    if money:
        num = money.group(1).replace(",", "")
        return float(num) if "." in num else int(num)
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text.strip('"')


def _parse_human_result_lines(stdout: str) -> dict[str, Any]:
    """Fallback parser for catala's `--format=human` `[RESULT] var = value` lines."""
    outputs: dict[str, Any] = {}
    for raw_line in stdout.splitlines():
        m = _RESULT_LINE.match(raw_line.strip())
        if m:
            outputs[m.group(1)] = _coerce_scalar(m.group(2))
    return outputs


def _parse_interpret_stdout(stdout: str, command: list[str]) -> CatalaResult:
    """Parse `catala interpret --format=json` (preferred) or human-format stdout."""
    stripped = stdout.strip()
    if stripped:
        payload: Any = None
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            # Some versions may wrap results in an envelope; unwrap common shapes.
            for key in ("results", "output", "outputs"):
                inner = payload.get(key)
                if isinstance(inner, dict):
                    return CatalaResult(outputs=inner, raw_stdout=stdout)
            return CatalaResult(outputs=payload, raw_stdout=stdout)

    # Fall back to human-readable [RESULT] lines.
    human_outputs = _parse_human_result_lines(stdout)
    if not human_outputs:
        raise CatalaRuntimeError(
            "Could not parse catala interpret output as JSON or as [RESULT] lines. "
            "This adapter may need updating for your installed catala version -- "
            "see the module docstring in govsynth/sources/catala/runtime.py.",
            command,
            stdout,
        )
    return CatalaResult(outputs=human_outputs, raw_stdout=stdout)


def _parse_trace(raw_trace: str) -> list[dict[str, Any]]:
    """Best-effort parse of catala's `--trace` output into a list of event dicts.

    The exact trace JSON schema wasn't independently verified (see module
    docstring); this accepts a bare JSON array of events, newline-delimited
    JSON objects, or otherwise returns an empty list rather than raising --
    a missing/unparseable trace degrades to a shorter RationaleTrace, not a
    hard failure, since the trace is an enhancement over the base outcome.
    """
    text = raw_trace.strip()
    if not text:
        return []
    try:
        parsed: Any = json.loads(text)
        if isinstance(parsed, list):
            return [e for e in parsed if isinstance(e, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass

    events: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


class CatalaRuntime:
    """Runs `catala` subcommands against one ruleset file on disk."""

    def __init__(
        self,
        ruleset_path: str | Path,
        binary: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.ruleset_path = Path(ruleset_path)
        if not self.ruleset_path.exists():
            raise FileNotFoundError(f"Catala ruleset not found: {self.ruleset_path}")
        self.binary = find_catala_binary(binary)
        self.timeout = timeout

    def _run(self, args: list[str], stdin_payload: str | None = None) -> subprocess.CompletedProcess[str]:
        command = [self.binary, *args]
        try:
            return subprocess.run(
                command,
                input=stdin_payload,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CatalaRuntimeError(f"catala subprocess timed out after {self.timeout}s", command) from exc
        except OSError as exc:
            raise CatalaRuntimeError(f"Failed to execute catala: {exc}", command) from exc

    def scope_schema(self, scope: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return (input_schema, output_schema) for `scope` via `catala json-schema`."""
        args = ["json-schema", str(self.ruleset_path), f"--scope={scope}"]
        result = self._run(args)
        command = [self.binary, *args]
        if result.returncode != 0:
            raise CatalaRuntimeError("catala json-schema failed", command, result.stderr)
        try:
            schemas: Any = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CatalaRuntimeError(
                "Could not parse catala json-schema output as JSON", command, result.stdout
            ) from exc
        if not (isinstance(schemas, list) and len(schemas) == 2):
            raise CatalaRuntimeError(
                "Expected catala json-schema to return a 2-element [input, output] array",
                command,
                result.stdout,
            )
        input_schema, output_schema = schemas
        if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
            raise CatalaRuntimeError(
                "catala json-schema returned non-object input/output schemas",
                command,
                result.stdout,
            )
        return input_schema, output_schema

    def interpret(
        self,
        scope: str,
        inputs: dict[str, Any] | None = None,
        *,
        with_trace: bool = False,
    ) -> CatalaResult:
        """Run `scope` with `inputs` (fed as JSON on stdin).

        Returns the scope's outputs, plus a best-effort explanation trace
        (rule applications) when `with_trace=True`. The trace is written to
        a temp file via `--trace=<path>` rather than a bare `--trace` flag,
        since Catala's docs indicate a bare `--trace` flag writes to stdout
        -- which would otherwise collide with the `--format=json` results
        also written to stdout.
        """
        args = ["interpret", str(self.ruleset_path), f"--scope={scope}", "--format=json"]
        stdin_payload = json.dumps(inputs) if inputs else None

        if not with_trace:
            result = self._run(args, stdin_payload=stdin_payload)
            command = [self.binary, *args]
            if result.returncode != 0:
                raise CatalaRuntimeError("catala interpret failed", command, result.stderr)
            return _parse_interpret_stdout(result.stdout, command)

        with tempfile.NamedTemporaryFile(suffix=".trace.json", delete=False) as tmp:
            trace_path = Path(tmp.name)
        try:
            traced_args = [*args, f"--trace={trace_path}"]
            result = self._run(traced_args, stdin_payload=stdin_payload)
            command = [self.binary, *traced_args]
            if result.returncode != 0:
                raise CatalaRuntimeError("catala interpret failed", command, result.stderr)
            parsed = _parse_interpret_stdout(result.stdout, command)
            if trace_path.exists():
                parsed.trace = _parse_trace(trace_path.read_text(encoding="utf-8"))
            return parsed
        finally:
            trace_path.unlink(missing_ok=True)
