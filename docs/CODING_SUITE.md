# Coding agent suite

The `coding` suite is a separate, project-level evaluation. The web-expanded
profile is `coding-agent-v2-web`. It never contributes
rows or scores to the 18-task `standard` suite. Coding campaigns write
`*_coding.csv`, `*_coding.jsonl`, isolated workspaces, and a dedicated
`coding_agent_report.html`.

## What it measures

Each observation is one **model × model runner × agent harness × project task**.
The agent receives a fresh repository, a development request, terminal and file
editing tools, and a bounded time budget. A grader outside the workspace then
runs hidden functional and engineering-quality checks.

| Task | Established methodology | Capability |
|---|---|---|
| `swe_issue_config_merge` | SWE-bench-style | Multi-file bug diagnosis, regression repair, compatibility |
| `repobench_dependency_refactor` | RepoBench-style | Repository navigation and architectural refactoring |
| `livecodebench_schedule_optimizer` | LiveCodeBench-style | Efficient implementation, validation, deterministic CLI behavior |
| `bigcodebench_log_pipeline` | BigCodeBench-style | Complex instructions, multiple APIs, streaming data processing |
| `featurebench_job_service` | FeatureBench-style | Full project construction from a specification |
| `terminalbench_release_hardening` | Terminal-Bench-style | Long-horizon terminal work, security, packaging, reproducibility |
| `web_accessible_incident_dashboard` | WebDev accessibility | Semantic HTML, responsive CSS, filters, theme/state, accessible interaction |
| `web_component_storefront` | Web components | Custom elements, state architecture, persistence, accessible cart behavior |
| `web_fullstack_kanban` | Full-stack WebDev | SQLite, WSGI APIs, HTTP concurrency, safe static serving, responsive frontend |

The fixtures are original and intentionally do not reuse `coding_micro`,
`humaneval_mini`, `mbpp_mini`, or any prompt from `standard`. The names above
describe the established benchmark methodology incorporated into each task;
scores are local-suite scores and must not be presented as official upstream
leaderboard results.

Primary references:

- [SWE-bench](https://github.com/SWE-bench/SWE-bench) for real repository issue resolution
- [RepoBench](https://github.com/Leolty/repobench) for repository-level context and completion
- [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) for broad, contamination-aware coding evaluation
- [BigCodeBench](https://github.com/bigcode-project/bigcodebench) for complex instructions and diverse function use
- [FeatureBench](https://github.com/LiberCoders/FeatureBench) for end-to-end feature development
- [Terminal-Bench](https://github.com/laude-institute/terminal-bench) for long-horizon terminal-agent tasks

## Scoring

Two metrics are reported:

- **Projects resolved:** strict pass rate. Every hidden functional and
  best-practice check must pass.
- **Quality score:** checks passed divided by checks available. This exposes
  partial strengths such as correctness with weak tests or good structure with
  an incomplete feature, without awarding a full resolution.

Checks cover behavior, edge cases, input validation, algorithmic complexity,
backward compatibility, regression tests, modularity, type-aware interfaces,
SQL and subprocess safety, documentation, packaging, reproducible output,
semantic HTML, accessibility, responsive layout, safe DOM construction,
component state, HTTP semantics, and optimistic concurrency.
Timing, timeouts, changed-file counts, test-file counts, telemetry, temperatures,
and peak memory remain separate diagnostic fields and never improve accuracy.

The three web tasks run offline without a frontend build system. Their hidden
graders execute both Python and Node tests where applicable and inspect browser
contracts such as landmarks, labels, live regions, focus treatment, responsive
breakpoints, reduced-motion behavior, safe DOM APIs, component encapsulation,
loading/error/empty states, and frontend conflict handling. This avoids adding
network or third-party package availability as a model-scoring variable.
The runner preflights Node before any inference when a web task is selected and
records its version in every web observation, preventing a missing grader
runtime from being misclassified as a model failure.

## Run it

List tasks without touching models or files:

```bash
python3 scripts/coding_agent_benchmarks.py \
  --suite coding --harness pi \
  --models-file models.tsv --output-dir reports --workspace work \
  --list-tasks
```

Preview, then execute:

```bash
python3 scripts/coding_agent_benchmarks.py \
  --suite coding --harness pi \
  --models-file models.tsv --output-dir reports/pi --workspace work

python3 scripts/coding_agent_benchmarks.py \
  --suite coding --harness pi \
  --models-file models.tsv --output-dir reports/pi --workspace work \
  --timeout 7200 --run
```

On the DGX Spark, the campaign wrapper runs the configured harness list and
automatically writes its separate report:

```bash
BENCH_CAMPAIGN_DIR="$HOME/.hermes/reports/campaigns/coding-v1" \
BENCH_MODELS_FILE="$HOME/.hermes/reports/campaigns/coding-v1/models.tsv" \
BENCH_CLI_HARNESSES="pi goose openhands" \
ops/run_coding_agent_campaign.sh
```

To compare the same frozen Ollama checkpoints through the built-in direct
workspace agent, Hermes, and OpenClaw, use the resumable project wrapper:

```bash
BENCH_CAMPAIGN_DIR="$HOME/.hermes/reports/campaigns/project-three-path" \
BENCH_MODELS_FILE="$HOME/.hermes/reports/campaigns/project-three-path/models.tsv" \
BENCH_PROJECT_SUITES="coding" \
BENCH_PROJECT_HARNESSES="ollama-direct hermes openclaw" \
ops/run_ollama_project_three_path_campaign.sh
```

`ollama-direct` is the repository's bounded workspace tool loop and is recorded
as its own harness. Hermes uses an isolated state directory and a restricted
terminal/file/code-execution tool surface. OpenClaw uses a unique benchmark
session and an explicit Ollama model override. None of these paths is relabeled
as Pi, Goose, or OpenHands.

The coding runner is plan-only unless `--run` is supplied. For Ollama it verifies
frozen digests and cold-unloads between projects. For persistent server runners
(`llama.cpp`, vLLM, and TensorRT-LLM), the wrapper records and monitors the exact
server process for the bounded campaign. All paths preserve interrupted
workspaces, use memory/swap/OOM/exclusivity guards, and resume only completed
model-task keys. Default coding timeout is two hours; the hard maximum is four.

## Report isolation

Generate or regenerate only the coding report:

```bash
python3 dashboard/generate_coding_report.py \
  --input-root "$BENCH_CAMPAIGN_DIR" \
  --output "$BENCH_CAMPAIGN_DIR/coding_agent_report.html"
```

The generator only accepts rows whose `benchmark_suite` is exactly `coding`, so
standard results cannot leak into the coding rankings. It groups incompatible
profile versions separately, ranks complete coverage ahead of partial evidence,
and reports an additional web-project resolution rate alongside the overall
project and quality scores. Use a new campaign/output directory when moving
from the six-task `coding-agent-v1` profile to `coding-agent-v2-web`.
