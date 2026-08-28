# Local LLM Benchmark Suite

## About

Local LLM Benchmark Suite is a reproducible evaluation toolkit for comparing
local and cloud-connected models on your own hardware. It runs the same
deterministically graded tasks through Direct Ollama, Hermes Agent, and
OpenClaw, while preserving the evidence needed to explain a result: model and
runtime provenance, harness, task outcome, timing, temperature, and memory
telemetry. Its scores are best used to compare the specific models, settings,
and host captured in a campaign—not as universal model rankings.

Run the same deterministic local-LLM benchmark suite through four paths:

- **Direct Ollama** — measures the model through Ollama's local API.
- **Hermes Agent** — measures the model through Hermes's one-shot agent path.
- **OpenClaw** — measures the model through the OpenClaw Gateway agent path.
- **COH Ollama** — measures the 17 text tasks through COH model-surface admission and its qualified local Ollama gateway.

The suite is designed for macOS on Apple silicon and NVIDIA Linux, including
the DGX Spark. It favors reproducible accuracy and evidence preservation over
throughput: each observation records its prompt, deterministic grade, runtime
provenance, response timing, temperatures, and peak memory use.

> [!IMPORTANT]
> The default **core** profile contains 18 fixed proxy tasks. Names such as
> GSM8K, MATH-500, HumanEval, and MMLU-Pro describe the style of one small
> task; they do **not** mean the complete upstream benchmark was run. Use the
> opt-in official profiles for full AIME 2026 or GPQA Diamond evaluation.

## Quick start

### 1. Check requirements

You need Python 3.10+, a running local Ollama server, and at least one
installed model. Hermes and OpenClaw are needed only for their respective
agent-path runners.

```bash
python3 --version
ollama list
curl http://127.0.0.1:11434/api/tags
```

Supported telemetry is selected automatically:

| Host | Automatic telemetry |
|---|---|
| macOS on Apple silicon | `mactop`, when installed |
| NVIDIA Linux / DGX Spark | `nvidia-smi` plus `/proc` |
| Other systems | no-op telemetry; unavailable fields remain blank |

On DGX Spark, run only on a quiet host. The suite stops if it detects unsafe
memory/swap pressure, an OOM, an unhealthy Ollama server, or unrelated NVIDIA
compute work.

### 2. Discover the tasks

This is read-only: it does not contact Ollama or an agent, start telemetry, or
write files.

```bash
python3 scripts/ollama_standardized_local_benchmarks.py --list-tasks
```

### 3. Preview a small run

Every runner is **plan-only by default**. Start with one model and one task:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --test math500_mini
```

Review the printed plan. It will show the selected model, task count, timeout,
thinking configuration, telemetry backend, and report destination.

### 4. Run the benchmark

Add `--run` only after reviewing the plan:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --test math500_mini \
  --run
```

To run the full 18-task core profile for one model, omit `--test`:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --run
```

The per-task deadline defaults to 1,800 seconds. The Direct runner cold-unloads
between tasks by default; do not add `--no-stop` unless you specifically want
to measure warm-residency behavior.

### 5. Open the results

Reports are kept host-local by default:

| Harness | Default report directory |
|---|---|
| Direct Ollama | `~/.hermes/reports/ollama_benchmarks/` |
| Hermes Agent | `~/.hermes/reports/hermes_agent_benchmarks/` |
| OpenClaw | `~/.hermes/reports/openclaw_benchmarks/` |

Each run produces a CSV for inspection, canonical JSONL evidence, and a
Markdown summary. Generate the host dashboard with:

```bash
python3 dashboard/generate_local_llm_dashboard.py
```

It writes `~/Local LLM Benchmark Dashboard.html` and model detail pages under
`~/Local LLM Model Research/` by default.

## Choose a harness

The runners use the same core task registry. Run each path separately so
their scores remain attributable to their actual transport and agent behavior.

### COH Ollama

COH's benchmark-only command must be built from the reviewed COH checkout. The
runner excludes the OCR task because the current COH benchmark surface accepts
text only, validates the frozen Ollama model digest, and requires per-response
COH capability, binding, and provenance digests.

```bash
python3 scripts/coh_ollama_benchmarks.py \
  --binary /path/to/cohollamabench \
  --model muse-glimmer:30b-mlx \
  --model-digest <64-hex-digest> \
  --output-dir ~/.hermes/reports/coh_ollama_benchmarks

# Add --run after reviewing the plan.
```

### Direct Ollama

Best for measuring the local runtime without an agent layer. The default
accuracy-first configuration requests unlimited generation (`num_predict: -1`)
and the highest supported thinking level (`high` for GPT-OSS).

```bash
# Preview
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b

# Execute with automatic host telemetry
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --telemetry auto \
  --run
```

Useful options:

```bash
# Test several named tasks
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --tasks gsm8k_mini math500_mini humaneval_mini \
  --run

# Use a fixed context length for a controlled comparison
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --num-ctx 32768 \
  --run

# Choose telemetry explicitly (useful on a DGX Spark)
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --telemetry nvidia-smi \
  --run
```

### Hermes Agent

Use this to measure the same task set through Hermes Agent. The suite preserves
and restores the Hermes state it changes. OCR is sent through Hermes's native
vision path only for models with verified vision capability.

```bash
# Preview
python3 scripts/hermes_agent_17_test_benchmarks.py \
  --models qwen3.6:35b

# Execute
python3 scripts/hermes_agent_17_test_benchmarks.py \
  --models qwen3.6:35b \
  --run
```

To benchmark an authenticated cloud model, make the provider explicit:

```bash
python3 scripts/hermes_agent_17_test_benchmarks.py \
  --external-models gpt-5.6-sol \
  --provider openai \
  --run
```

Only list an external model in `--external-vision-models` after confirming it
accepts native image input; otherwise the OCR task is safely recorded as a
skip.

### OpenClaw

Use this to measure the same task set through the Gateway. A real run
temporarily changes OpenClaw's selected model and clears fallbacks; the runner
captures and restores the starting state at completion.

```bash
# Preview
python3 scripts/openclaw_18_test_benchmarks.py \
  --models qwen3.6:35b

# Execute
python3 scripts/openclaw_18_test_benchmarks.py \
  --models qwen3.6:35b \
  --thinking medium \
  --run
```

On macOS, gateway restart uses `launchctl`. On Linux, pass the exact managed
restart command; the suite never guesses a service name:

```bash
python3 scripts/openclaw_18_test_benchmarks.py \
  --models qwen3.6:35b \
  --gateway-restart-command 'systemctl --user restart openclaw-gateway.service' \
  --run
```

## Core benchmark catalog

The core profile is a set of deterministic, one-file-per-test components in
[`scripts/benchmark_tests/core/`](scripts/benchmark_tests/core/). Every task is
graded locally; the core suite never uses an LLM as a judge.

| Task ID | Family | What it tests | Evaluation |
|---|---|---|---|
| `exact_reply` | Smoke | Exact-output instruction following | Exact token match |
| `simple_reasoning` | Smoke | Short arithmetic reasoning | Required `FINAL:` number |
| `coding_micro` | Smoke | Compact RFC1918 IPv4 Python implementation | Behavioral Python tests + line limit |
| `ifeval_exact` | IFEval | Exact constrained response | Exact token match |
| `ifeval_json` | IFEval | Compact valid JSON with required fields | Strict JSON schema/value check |
| `gsm8k_mini` | GSM8K | Grade-school percentage arithmetic | Required `FINAL:` number |
| `math500_mini` | MATH-500 | Algebra and answer formatting | Required `FINAL:` number |
| `mmlu_pro_security` | MMLU-Pro | Security knowledge | Required `FINAL:` multiple-choice letter |
| `arc_challenge_mini` | ARC-Challenge | Physical-science reasoning | Required `FINAL:` multiple-choice letter |
| `hellaswag_mini` | HellaSwag | Commonsense continuation | Required `FINAL:` multiple-choice letter |
| `truthfulqa_mini` | TruthfulQA | Truthful uncertainty about VPN anonymity | Required `FINAL: yes` or `FINAL: no` |
| `humaneval_mini` | HumanEval+ | Python IPv4-private-address function generation | Behavioral Python tests + line limit |
| `mbpp_mini` | MBPP+ | Python extraction/counting of unique IPv4 addresses | Behavioral Python tests |
| `bfcl_mini` | BFCL | Tool/function selection with exact arguments | Strict compact JSON schema/value check |
| `ragas_mini` | RAGAS/RAG | Answering only from supplied context | Required grounded `FINAL:` value |
| `prompt_injection_mini` | Prompt Injection | Resisting hostile retrieved instructions | Required answer from trusted fact |
| `cyber_soc_mini` | CyberSecEval-style | Defensive SOC classification and action | Strict JSON classification/action check |
| `ocrbench_mini` | OCRBench/TextVQA | Text transcription from a fixed image | Exact `FINAL:` transcription; non-vision models skip |

The first three tests are fast smoke diagnostics. The remaining 15 make up the
standardized mini suite. Scores represent these fixed prompts and graders, not
the full upstream benchmark datasets.

List task IDs from any runner:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py --list-tasks
python3 scripts/hermes_agent_17_test_benchmarks.py --list-tasks
python3 scripts/openclaw_18_test_benchmarks.py --list-tasks
```

See [`docs/BENCHMARK_COMPONENTS.md`](docs/BENCHMARK_COMPONENTS.md) to review,
add, or replace a core component.

## Full official offline profiles

The default core profile is short. Full official evaluations are explicitly
opt-in and use vendored, integrity-checked snapshots with deterministic local
grading and no runtime network dependency.

| Profile | Items | Grading |
|---|---:|---|
| `aime2026` | 30 | Exact final integer |
| `gpqa-diamond` | 198 | Exact final choice |
| `standard-local` | 228 | Combined AIME + GPQA |

Preview an official profile before executing it:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --full-suite \
  --task-profile aime2026 \
  --models qwen3.6:35b
```

Run it only after reviewing the estimate:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --full-suite \
  --task-profile aime2026 \
  --models qwen3.6:35b \
  --run
```

The 228-item combined profile can theoretically consume 114 hours of response
time per model at the 1,800-second limit. Official profiles are single-arm;
paired thinking is intentionally rejected so qualification probes cannot alter
their score denominators. Review licenses in
[`data/standard_local/THIRD_PARTY_NOTICES.md`](data/standard_local/THIRD_PARTY_NOTICES.md)
before redistribution or commercial use.

## Results, telemetry, and reproducibility

Every benchmark row includes:

- task and grading outcome, including deterministic grader diagnostics;
- model tag/digest, host, harness, runner/grader hashes, and policy settings;
- wall time, first output/answer timing, token counts, and throughput when the
  backend exposes them;
- utilization, temperatures, power, peak host-memory bytes/percent, and—on
  NVIDIA—aggregate peak GPU memory bytes when those sensors are available;
- timeout, error, residency, vision-capability, and safety-guard evidence.

CSV is convenient for analysis; JSONL is the canonical evidence record and
retains complete responses, reasoning text when provided, and full guard and
grading details. Existing data with unavailable sensors stays blank rather
than being represented as zero.

The checked-in cross-system ranking is available at
[`docs/top_10_models_by_system.html`](docs/top_10_models_by_system.html).
It shows the top ten by default; **Verbose** reveals all observed models and
**Cloud** includes or excludes cloud runs while recalculating ranks.

Regenerate that report from its curated inputs with:

```bash
python3 dashboard/generate_top_models_report.py
```

## Advanced: paired thinking comparisons

Use paired mode only when you want a controlled reasoning-level comparison for
local models that advertise thinking capability. It freezes a manifest,
deduplicates aliases by digest, qualifies the model's supported thinking
control, alternates treatment order, and preserves every partial observation.

```bash
# Preview a guarded DGX Spark paired campaign
python3 scripts/ollama_standardized_local_benchmarks.py \
  --thinking paired \
  --adaptive-native-context \
  --telemetry nvidia-smi

# Execute after reviewing the plan
python3 scripts/ollama_standardized_local_benchmarks.py \
  --thinking paired \
  --adaptive-native-context \
  --telemetry nvidia-smi \
  --run
```

Paired mode requires a positive `--num-ctx` or `--adaptive-native-context`.
Adaptive calibration is restricted to loopback Ollama on Linux and only lowers
context after clean capacity pressure with verified recovery; an OOM, swap
growth beyond the frozen 1 GiB allowance, unhealthy runtime, or external GPU
work is a hard stop.

Resume only with the original frozen plan and matching calibration artifact:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --thinking paired \
  --adaptive-native-context \
  --resume-plan ~/.hermes/reports/ollama_benchmarks/ollama_standardized_local_benchmark_TIMESTAMP.plan.json \
  --run
```

The resume process validates source, task, model-digest, context, runtime,
telemetry, and safety provenance before scheduling only unfinished work.

## Validate and contribute

Run the repository test suite:

```bash
python3 -m unittest discover -s tests -v
```

Only synchronize source and documentation between systems. Keep raw
`.hermes/reports`, generated host dashboards, and model-detail pages local so
each hardware result remains attributable to its originating machine.

For Spark-specific preflight and telemetry details, see
[`docs/DGX_SPARK.md`](docs/DGX_SPARK.md). The canonical remote is
[ArronJablonowski/LLM_Benchmarks](https://github.com/ArronJablonowski/LLM_Benchmarks).
