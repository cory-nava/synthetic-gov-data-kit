---
name: Bug report
about: Something isn't working correctly
labels: bug
---

## Describe the bug

A clear description of what is wrong.

## To reproduce

```python
# Minimal code to reproduce the issue
from govsynth import Pipeline

pipeline = Pipeline.from_preset("snap.va")
# ...
```

Or CLI:

```bash
govsynth generate snap.va --n 10 --seed 42
```

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened. Include the full error traceback if applicable.

## Environment

- OS:
- Python version:
- `synthetic-gov-data-kit` version (run `pip show synthetic-gov-data-kit`):
- Install method: `pip install` / `pip install -e .` / other

## Is this a policy data issue?

- [ ] Yes — a threshold value, income limit, or benefit amount appears to be incorrect
  - If yes, please include a link to the authoritative source (CFR section, FNS memo, etc.)
- [ ] No — this is a code/library issue
