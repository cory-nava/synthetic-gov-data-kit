# Adding govsynth CLI Access to Claude Code and AI Apps

The `govsynth` CLI is designed to be pipe-friendly and machine-readable. This guide covers
three integration patterns:

1. **Claude Code** — allow `govsynth` commands in a CLAUDE.md or permissions config
2. **MCP server** — expose govsynth as a Model Context Protocol tool for any MCP-compatible host
3. **Python subprocess** — call the CLI from inside an AI agent or evaluation harness

---

## Pattern 1: Claude Code — Allow govsynth Commands

The simplest integration. Install the library, then tell Claude Code it can run govsynth.

### Option A: Project-level CLAUDE.md (checked into repo)

Add a section to your project's `CLAUDE.md` that explicitly permits govsynth commands and
describes what they do. Claude Code reads this file automatically when working in the repository.

```markdown
## Allowed CLI Commands

The following commands are pre-approved and do not need user confirmation:

- `govsynth list-presets [--json]` — list available presets
- `govsynth generate <preset> --n <n> --seed <seed> --format jsonl` — generate test cases
- `govsynth batch --preset <preset> ... --n <n> --output <dir>` — batch generation
- `govsynth validate <file>` — validate a generated output file
- `govsynth show <file>` — inspect a single case
- `govsynth verify-thresholds [--program <program>] [--json]` — check threshold data

Do NOT run `govsynth refresh-census-data` without user approval (makes external API calls).
```

### Option B: User-level settings (not checked in)

If you're using govsynth across multiple projects, add it to your Claude Code user settings.
Run `/allowed-tools` or use the `update-config` skill to persist permissions.

---

## Pattern 2: MCP Server — govsynth as a Tool

[Model Context Protocol (MCP)](https://modelcontextprotocol.io) lets AI hosts (Claude.ai,
Claude Code, custom apps) call external tools via a standardized JSON-RPC interface. Wrapping
govsynth as an MCP server makes it available to any MCP-compatible AI without subprocess
boilerplate.

### Minimal MCP server with `fastmcp`

```bash
pip install fastmcp synthetic-gov-data-kit
```

```python
# govsynth_mcp_server.py
"""MCP server exposing govsynth as Claude-callable tools."""

import json
import subprocess
import tempfile
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP("govsynth")


@mcp.tool()
def list_presets() -> list[dict]:
    """List all available govsynth presets with descriptions."""
    result = subprocess.run(["govsynth", "list-presets", "--json"], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@mcp.tool()
def generate_cases(
    preset: str,
    n: int = 10,
    seed: int = 42,
    profile_strategy: str = "edge_saturated",
) -> list[dict]:
    """Generate synthetic government benefits test cases.

    Args:
        preset: Preset name, e.g. 'snap.va', 'wic.national', 'medicaid.tx'
        n: Number of cases to generate (max 200 per call)
        seed: Random seed for reproducibility
        profile_strategy: 'edge_saturated', 'realistic', or 'adversarial'
    """
    if n > 200:
        raise ValueError("n must be <= 200 per call")

    result = subprocess.run(
        [
            "govsynth",
            "generate",
            preset,
            "--n",
            str(n),
            "--seed",
            str(seed),
            "--profile-strategy",
            profile_strategy,
            "--format",
            "jsonl",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


@mcp.tool()
def verify_thresholds(program: str | None = None) -> dict:
    """Check whether bundled policy threshold data is up to date.

    Args:
        program: Specific program to check ('snap', 'wic', 'medicaid'), or None for all
    """
    cmd = ["govsynth", "verify-thresholds", "--json"]
    if program:
        cmd += ["--program", program]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {"exit_code": result.returncode, "output": json.loads(result.stdout)}


@mcp.tool()
def validate_case_file(yaml_content: str) -> dict:
    """Validate a govsynth YAML case file.

    Args:
        yaml_content: The full YAML content of a test case file
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        tmp_path = f.name

    result = subprocess.run(["govsynth", "validate", tmp_path, "--json"], capture_output=True, text=True)
    Path(tmp_path).unlink(missing_ok=True)
    return {"valid": result.returncode == 0, "output": result.stdout}


if __name__ == "__main__":
    mcp.run()
```

### Run the server

```bash
python govsynth_mcp_server.py
```

### Register with Claude Code

Add to your Claude Code MCP config (`.claude/settings.json` or global settings):

```json
{
  "mcpServers": {
    "govsynth": {
      "command": "python",
      "args": ["/path/to/govsynth_mcp_server.py"]
    }
  }
}
```

Now Claude Code can call `generate_cases`, `list_presets`, `verify_thresholds`, and
`validate_case_file` as first-class tools, with type-checked inputs and structured outputs.

### What this enables in Claude Code

With the MCP server registered, you can prompt Claude Code:

```
Use the govsynth MCP tool to generate 20 SNAP Virginia edge cases with seed=42,
then write a pytest fixture file at tests/fixtures/snap_va_edge_cases.py that
loads them as parametrized test cases.
```

Claude Code will call `generate_cases(preset="snap.va", n=20, seed=42)` directly and receive
structured JSON — no file I/O or shell parsing needed.

---

## Pattern 3: Python Subprocess in an AI Agent

For embedding govsynth in a custom Python agent or evaluation harness.

### Basic wrapper

```python
# govsynth_client.py
"""Thin wrapper around govsynth CLI for use in Python agents."""

import json
import subprocess
from typing import Any


def list_presets() -> list[dict[str, Any]]:
    """Return all registered presets as a list of dicts."""
    result = subprocess.run(["govsynth", "list-presets", "--json"], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def generate(
    preset: str,
    n: int = 10,
    seed: int = 42,
    profile_strategy: str = "edge_saturated",
    fmt: str = "jsonl",
) -> list[dict[str, Any]]:
    """Generate n cases for the given preset, returning parsed dicts."""
    result = subprocess.run(
        [
            "govsynth",
            "generate",
            preset,
            "--n",
            str(n),
            "--seed",
            str(seed),
            "--profile-strategy",
            profile_strategy,
            "--format",
            fmt,
            "--quiet",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    if fmt == "jsonl":
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return json.loads(result.stdout)


def verify_thresholds(program: str | None = None) -> bool:
    """Return True if all (or the given program's) thresholds are verified."""
    cmd = ["govsynth", "verify-thresholds", "--json"]
    if program:
        cmd += ["--program", program]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0
```

### Using the wrapper in an agent loop

```python
from govsynth_client import generate, verify_thresholds
from anthropic import Anthropic

client = Anthropic()


def run_eval(preset: str, n: int = 20, seed: int = 42) -> dict:
    """Generate cases and evaluate Claude against them."""
    cases = generate(preset, n=n, seed=seed, profile_strategy="edge_saturated")

    results = []
    for case in cases:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": f"{case['scenario']['summary']}\n\n{case['task']['instruction']}"}],
        )
        answer = response.content[0].text
        expected = case["expected_outcome"]

        # Simple exact-match check; use RationaleEvaluator for deeper scoring
        correct = expected.lower() in answer.lower()
        results.append(
            {
                "case_id": case["case_id"],
                "expected": expected,
                "correct": correct,
            }
        )

    accuracy = sum(r["correct"] for r in results) / len(results)
    return {"accuracy": accuracy, "n": len(results), "results": results}


if __name__ == "__main__":
    report = run_eval("snap.va", n=20, seed=42)
    print(f"Accuracy: {report['accuracy']:.1%} over {report['n']} cases")
```

---

## Pattern 4: Claude API Tool Use

Register govsynth as a tool definition in a Claude API call, so Claude can decide when to
generate test cases as part of a larger task.

```python
import json
import subprocess
from anthropic import Anthropic

client = Anthropic()

TOOLS = [
    {
        "name": "generate_test_cases",
        "description": (
            "Generate synthetic US government benefits test cases with policy-grounded "
            "rationale traces. Use this when you need realistic eligibility scenarios "
            "to evaluate, test, or demonstrate reasoning about SNAP, WIC, or Medicaid."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "description": "Preset name, e.g. 'snap.va', 'snap.ca', 'wic.national'",
                },
                "n": {
                    "type": "integer",
                    "description": "Number of cases to generate (1–50)",
                    "minimum": 1,
                    "maximum": 50,
                },
                "profile_strategy": {
                    "type": "string",
                    "enum": ["edge_saturated", "realistic", "adversarial"],
                    "description": "How to sample household profiles",
                },
                "seed": {
                    "type": "integer",
                    "description": "Random seed for reproducibility",
                },
            },
            "required": ["preset"],
        },
    }
]


def handle_tool_call(name: str, inputs: dict) -> str:
    if name == "generate_test_cases":
        cmd = [
            "govsynth",
            "generate",
            inputs["preset"],
            "--n",
            str(inputs.get("n", 5)),
            "--seed",
            str(inputs.get("seed", 42)),
            "--profile-strategy",
            inputs.get("profile_strategy", "edge_saturated"),
            "--format",
            "jsonl",
            "--quiet",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        cases = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        # Return a compact summary so it fits in context
        return json.dumps(
            [
                {
                    "case_id": c["case_id"],
                    "outcome": c["expected_outcome"],
                    "difficulty": c["difficulty"],
                    "household_size": c["scenario"]["household_size"],
                    "monthly_gross_income": c["scenario"]["monthly_gross_income"],
                }
                for c in cases
            ]
        )
    raise ValueError(f"Unknown tool: {name}")


def run_agentic_loop(user_message: str) -> str:
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return response.content[0].text

        # Handle tool use
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = handle_tool_call(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})


# Example usage
answer = run_agentic_loop("Generate 5 SNAP Virginia test cases and summarize what makes the hard ones difficult.")
print(answer)
```

---

## CLI Output Conventions

All `govsynth` commands follow these conventions, making them safe for scripting and agentic use:

| Stream | Content |
|--------|---------|
| stdout | Data output (JSONL, YAML, JSON) |
| stderr | Human-readable progress, warnings, Rich formatting |

| Flag | Effect |
|------|--------|
| `--json` | Machine-readable JSON output on stdout (available on most commands) |
| `--quiet` | Suppress stderr progress output |
| `--format jsonl` | JSONL to stdout instead of YAML files |

Exit codes:
- `0` — success
- `1` — validation failure or unverified thresholds found
- `2` — invalid arguments

This makes govsynth composable:

```bash
# Pipe directly to jq
govsynth generate snap.va --n 10 --seed 42 --format jsonl --quiet | jq '.case_id'

# Use exit code in CI
govsynth verify-thresholds && echo "All thresholds verified" || exit 1

# Chain with other tools
govsynth batch --preset snap.va --preset wic.national --n 50 --output ./suite/ \
  && python evaluate.py ./suite/
```
