# Offline GitHub CLI benchmark suite

The `github` suite is separate from the current Command Line campaign. It is
not started automatically and does not reuse any existing campaign evidence.
The frozen September 24, 2026 command inventory comes from the official
[GitHub CLI reference](https://cli.github.com/manual/gh_help_reference): 198
built-in leaf commands across 35 command families, plus 10 multi-step action
workflows. See `scripts/benchmark_tests/github/coverage.json` for the exact
command list, source, snapshot date, and scope. The generator is
`scripts/generate_github_suite.py`; ordinary runs use the checked-in JSON and
never fetch documentation.

Each task uses a fictional `demo/project` fixture and an exact-command Python
simulator. The simulator never executes `gh`, invokes a shell, accesses a
GitHub account, or calls the network. Even delete, merge, secret, and auth
actions are simulated. The agent must exercise commands in the lab, then
submit an `answer.json` containing ordered commands, findings grounded in lab
output, and a safe action summary. Workflow tasks additionally enforce command
order in simulator state. The runner strips common GitHub credential variables
from the agent environment. This is a benchmark guard, not a network sandbox;
run untrusted agents with external network isolation if stronger containment is
required.

The scope is the built-in `gh` command surface in the frozen reference. It
does not claim to enumerate arbitrary REST/GraphQL endpoints, third-party
extensions, Git's own commands, every web UI operation, or future `gh`
releases. The `gh api` tasks cover REST and GraphQL entry points; workflow
tasks cover representative end-to-end GitHub actions.

Preview without running a model:

```bash
python3 scripts/github_agent_benchmarks.py --harness pi \
  --models-file /path/to/models.tsv \
  --output-dir /path/to/new-github-campaign/pi \
  --workspace /path/to/new-github-campaign/workspace \
  --list-tasks
```

For an intentional new campaign, use `ops/run_github_agent_campaign.sh` with
`BENCH_CAMPAIGN_DIR` pointing to a new, empty directory and a frozen
`models.tsv`. Do not point it at the running Command Line campaign. The
wrapper inherits the existing resource guards and records separate
`*_github.jsonl` and CSV files. `--full-suite` is not applicable: all frozen
GitHub tasks are selected by default. Use `--tasks` for a small smoke run.

To refresh the frozen command surface deliberately:

```bash
python3 scripts/generate_github_suite.py --refresh-reference
python3 -m unittest tests.test_github_suite -v
```

Review the resulting coverage diff before a new campaign; changing the
snapshot changes the benchmark profile and should use a new evidence directory.
