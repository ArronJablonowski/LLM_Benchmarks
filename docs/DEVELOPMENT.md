# Development checks

The repository's test and lint commands are offline: they do not contact
Ollama, Hermes, OpenClaw, cloud providers, or a benchmark host.  They only
validate repository source, fixtures, and deterministic grading behavior.

Install the optional local tools in an isolated environment, then run:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install pytest ruff black mypy
python -m pytest -q
python -m ruff check scripts dashboard tests
python -m mypy
```

The same test, lint, and component-registry type-check commands run in GitHub
Actions.  Ruff initially checks import/execution errors and records narrow
per-file exceptions for known legacy findings.  New or extracted modules are
checked by default; remove an exception when its corresponding code is
refactored.

Black's configuration is included for new code and focused refactors, but the
existing repository is intentionally not reformatted wholesale:

```bash
python -m black --check path/to/new_or_refactored_module.py
```

Do not run benchmark commands as part of these checks.  Real benchmark
execution is opt-in and requires `--run`; see the root README for the runner
CLI contracts and campaign safety rules.
