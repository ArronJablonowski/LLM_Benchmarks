# Local LLM Benchmark Suite

This repository contains the shared source for the general local-LLM benchmark workflow used on the Mac Studio, Mac Mini, and NVIDIA DGX Spark. It intentionally keeps generated results and machine-specific runtime state outside Git.

## Included tools

- `scripts/ollama_standardized_local_benchmarks.py` runs 18 direct Ollama proxy tests: three smoke tests and 15 standardized mini tasks. Text-only models skip the OCR task, leaving 17 scored tests.
- `scripts/standard_local_tasks.py` loads integrity-checked, vendored snapshots of the full AIME 2026 (30 items) and GPQA Diamond (198 items) test sets. These 228 long-running items are opt-in through `--full-suite` (or `--full_suite`) and run offline with deterministic exact-answer grading and no external judge or Python package.
- `scripts/thinking_pair_support.py` creates frozen schema-v3 treatment and qualification plans, deduplicates identical model aliases, and supplies stable campaign identifiers for resumable comparisons.
- `scripts/hermes_agent_17_test_benchmarks.py` runs the same 18 core tasks through Hermes Agent. Its OCR task passes an absolute local image path to `vision_analyze`, forces native image routing, and skips models without verified vision capability.
- `scripts/openclaw_18_test_benchmarks.py` runs the same 18 core tasks through OpenClaw. Its OCR task reads the local fixture and sends the bytes through the supported Gateway `agent` RPC attachment field; non-vision models skip it.
- `scripts/vision_benchmark_support.py` preserves the deterministic OCR PNG beside the reports and records its path, SHA-256, MIME type, byte count, transport, capability decision, and skip reason.
- `dashboard/generate_local_llm_dashboard.py` builds a host-specific HTML dashboard from the installed Ollama inventory and the latest local result CSVs.

These are deterministic local regression tests, not complete official dataset evaluations. Names such as GSM8K, MATH-500, HumanEval, and MMLU-Pro identify the style of a small proxy task; they do not mean the full dataset was executed.

The default command runs only the short 18-task `core` profile. AIME 2026 and GPQA Diamond are never included unless `--full-suite` (alias `--full_suite`) is present. With that opt-in, the `standard-local`, `aime2026`, and `gpqa-diamond` task profiles run the complete frozen test split named by the profile. Their snapshots, hashes, source URLs, and licenses are stored under `data/standard_local/`.

## Supported hosts and requirements

- macOS on Apple silicon, using `mactop` telemetry when installed
- Ubuntu/Linux on NVIDIA hardware, including DGX Spark, using `nvidia-smi` plus `/proc` telemetry
- Python 3.10 or newer; the Spark's system Python 3.12 is sufficient
- Ollama listening on `127.0.0.1:11434`, or pass `--ollama-url`
- OpenClaw only for the optional OpenClaw runner
- Hermes Agent is optional; the dashboard reads `~/.hermes/config.yaml` when available

Telemetry defaults to `auto`. The runner selects `mactop` on macOS, `nvidia-smi` on NVIDIA Linux, or a no-op backend when neither is available. Unsupported measurements remain blank rather than being recorded as zero. On DGX Spark, GPU utilization, GPU temperature, aggregate GPU power, CPU utilization, and a separately labeled host/ACPI temperature are available; CPU power, SoC temperature, and whole-system power are not exposed by the installed interfaces.

Benchmark reports remain host-local in:

- `~/.hermes/reports/ollama_benchmarks/`
- `~/.hermes/reports/hermes_agent_benchmarks/`
- `~/.hermes/reports/openclaw_benchmarks/`

## Validate the repository

```bash
python3 -m unittest discover -s tests -v
```

## List and select benchmark tests

All three core runners expose the same 18 task IDs. Listing is local and read-only: it does not contact Ollama, Hermes, or OpenClaw, start telemetry, mutate agent configuration, or create reports.

```bash
python3 scripts/ollama_standardized_local_benchmarks.py --list-tasks
python3 scripts/hermes_agent_17_test_benchmarks.py --list-tasks
python3 scripts/openclaw_18_test_benchmarks.py --list-tasks
```

Each row contains the task ID, benchmark family, category, and display name. Select one task with either `--test` or `--tasks`; both spellings use the printed ID. Without `--run`, each runner remains plan-only:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b --test math500_mini

python3 scripts/hermes_agent_17_test_benchmarks.py \
  --models qwen3.6:35b --test math500_mini

python3 scripts/openclaw_18_test_benchmarks.py \
  --models qwen3.6:35b --test math500_mini
```

After reviewing the one-call-per-model plan, add `--run` to the chosen runner. Multiple explicit IDs may be supplied after `--tasks`. The Direct runner rejects combining explicit task IDs with the positional `--limit-tasks` compatibility option.

The three-path wrapper can list the shared core catalog or execute one selected task sequentially across Direct, Hermes, and OpenClaw for every frozen model:

```bash
bash ops/run_standard_three_path_campaign.sh --list-tasks
bash ops/run_standard_three_path_campaign.sh --test math500_mini
```

Unlike the individual Python runners, the second wrapper command starts its campaign immediately. A selected task is frozen in `task-selection.txt`, receives a separate default campaign directory, and cannot reuse completion markers from an all-core campaign.

Official AIME 2026 and GPQA Diamond IDs remain behind the explicit full-suite switch. List or select them from their profile, for example:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --full-suite --task-profile aime2026 --list-tasks

python3 scripts/ollama_standardized_local_benchmarks.py \
  --full-suite --task-profile aime2026 --test aime2026_001
```

## Direct Ollama benchmark

Benchmark execution is deliberately guarded. Without `--run`, the command only prints its model/task plan and performs no inference, model stop, telemetry startup, or report write:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --limit-tasks 1
```

List tasks without contacting Ollama:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py --list-tasks
```

This lists the default 18-task core suite. A normal `--run` command without `--full-suite` also uses only those core tests and excludes AIME 2026 and GPQA Diamond.

### Full official offline profiles (explicit opt-in)

The standard-local profile adds the official local tests that can be graded faithfully with the Python standard library alone:

| Profile | Items | Grading | Runtime dependencies |
|---|---:|---|---|
| AIME 2026 | 30 | Exact final integer | None |
| GPQA Diamond | 198 | Exact final choice | None |
| `standard-local` combined | 228 | Both deterministic graders | None |

The questions and answers are read only from vendored JSONL. No network request, hosted judge, API, container, browser, retrieval service, or third-party Python package is used during a run. Web access was used only to acquire and verify the official upstream snapshots. GPQA choices have a deterministic, frozen order so the correct option is not always in its source position.

Review the combined plan without inference:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --full-suite \
  --models qwen3.6:35b
```

List one official suite without contacting Ollama:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --full-suite \
  --task-profile aime2026 \
  --list-tasks
```

`--full_suite` is accepted as a compatibility spelling of `--full-suite`. Official task profiles are rejected unless one of these switches is present, which prevents an old or copied `--task-profile standard-local` command from unexpectedly launching the long campaign. `--full-suite` without `--task-profile` selects the combined 228-item `standard-local` profile; use it with `--task-profile aime2026` or `--task-profile gpqa-diamond` to run only one official split.

After explicit approval, add `--run`. At the 1,800-second per-task limit, the combined profile has a theoretical maximum response budget of 114 hours per model, so review the plan carefully. AIME 2026 is CC BY-NC-SA 4.0 and therefore carries a non-commercial restriction; GPQA is CC BY 4.0. See `data/standard_local/THIRD_PARTY_NOTICES.md` before redistribution or commercial use.

Official standard-local profiles currently use single-arm execution. To compare reasoning control, run separate otherwise-identical `--thinking off` and `--thinking on` campaigns. `--thinking paired` is rejected for these profiles so its two auxiliary qualification probes cannot silently alter the official 30-, 198-, or 228-item score denominator.

Other Muse Glimmer methodology benchmarks were not relabeled or approximated. IFBench's official verifier requires several external language packages; SciCode requires a scientific execution stack; multimodal suites require substantial image assets and specialized preprocessing; judge-scored suites require another LLM; and agent suites require containers or simulators. Those do not meet this repository's standalone, no-external-dependency rule.

After reviewing the plan and explicitly approving a real benchmark, add `--run`:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --timeout 1800 \
  --num-ctx 262144 \
  --run
```

Select or disable telemetry explicitly when needed:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --telemetry nvidia-smi
```

The default `accuracy-first-v2` profile removes the suite's per-task output ceilings by sending Ollama `num_predict: -1`. It streams both reasoning and answer text, requests maximum thinking from models that advertise the capability (`high` for GPT-OSS), and enforces a hard 1,800-second wall-clock deadline per task. Models without a thinking capability receive no `think` field. In single-arm mode the suite does not change `num_ctx` unless `--num-ctx` is supplied. Paired mode instead requires either a positive explicit `--num-ctx` or `--adaptive-native-context`; both arms of one model always use that model's same frozen value.

Use `--thinking off|on|low|medium|high|max` only for an explicitly different single-arm comparison; `--think` remains an alias for `--thinking max`. With 18 tasks, the maximum response time is nine hours per model treatment. EOS, model stop conditions, context limits, runtime failures, and the 30-minute deadline can still end generation even though the suite imposes no output-token cap.

### Paired thinking campaign

`--thinking paired` selects only installed models whose verified Ollama metadata advertises `thinking`, collapses tags with the same non-empty digest, and places both treatments in one frozen experiment. Schema v3 uses the exact installed-model control contract rather than assuming every advertised thinking capability implements the same Boolean toggle:

| Model family | Exact `think` payloads | Interpretation |
|---|---|---|
| Most qualified thinking models | JSON `false` / JSON `true` | Candidate off/on comparison; runtime qualification remains authoritative |
| Mistral Medium 3.5 | JSON `false` / JSON string `"high"` | Off/high comparison; Boolean `true` does not request the intended maximum level |
| GPT-OSS | JSON string `"low"` / JSON string `"high"` | Minimum/maximum level comparison; no disabled arm and no causal off/on claim |
| Foundation-Sec reasoning and DeepSeek-R1 distill | JSON `false` / JSON `true` diagnostic probe | Installed packages do not provide a supported native off state; the diagnostic rows are retained, then remaining work is omitted |
| Muse Glimmer and Gemma 4 | JSON `false` / JSON `true` | Parser/renderer behavior makes the off state unobservable; completed pairs are descriptive, not causal |

For the largest context that can be verified safely on the current host, select adaptive native context. The following command reviews an unresolved guarded-ascending ladder without inference or report writes:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --thinking paired \
  --adaptive-native-context \
  --timeout 1800 \
  --telemetry nvidia-smi
```

Only after explicit approval, add `--run`. Adaptive execution is restricted to a loopback Ollama endpoint on the measured Linux host. Calibration starts at 8,192 tokens and grows upward toward the advertised native context. Before any empty-prompt load request, admission records a checkpoint-blob plus conservative F16 KV-cache estimate using the exact frozen Ollama parallelism and recognized attention layout. Known hybrid layouts are accounted for explicitly; missing or unknown architecture evidence fails closed. For supported models the estimate is informational: each larger candidate is attempted after the smaller candidate succeeds, and the last pass/fail interval is refined at 8,192-token steps.

The near-full-RAM policy keeps only a 4 GiB MemAvailable buffer. Static admission and the live watchdog enforce that buffer, and any campaign-relative growth in actual swap use also triggers cancellation; the monotonic `pswpout` counter remains recorded telemetry rather than a permanent trigger. Kernel OOM kills, Ollama ownership/parallelism drift, and NVIDIA compute exclusivity remain hard failures. This is a best-effort userspace watchdog, not a mathematical OOM guarantee. A non-Ollama NVIDIA compute process fails before a request when already present, or cancels the target if it appears during a request; the suite never stops that external process. In default cold mode, `/api/ps` must be empty before and after every task. The runner waits up to 30 seconds only for its target to unload and aborts immediately if an unrelated resident model appears.

These checks are conservative best-effort safeguards, not a hard OOM guarantee: a large allocation can outpace a 100 ms userspace watchdog. Run the campaign on a quiescent Spark, with ComfyUI and other GPU job sources stopped or prevented from launching and Hermes prevented from issuing concurrent Ollama requests. Hard containment would require an operating-system memory boundary, which the suite does not silently create or mutate.

Only clean capacity pressure may leave a model below native context, and only after the target unloads, Ollama remains healthy, and memory/swap recovery is verified. An OOM event, external compute activity, daemon/identity failure, unload failure, recovery failure, or watchdog failure is a global infrastructure abort and never authorizes a smaller context. `no-fit` means no 8,192-token-or-larger candidate was safely verified under this frozen policy; it is not a claim that the model can never run with a different policy or environment.

Native-fit, adjusted-fit, and no-fit outcomes retain static estimates, resource baselines, watchdog observations, exact parallelism provenance, every attempt, and adjustment/failure reasons in an incrementally written `.context-calibration.json` artifact. Each successful per-model value is frozen across both arms, and the final plan binds that artifact by filename and SHA-256. The plan, CSV/JSONL rows, canonical JSONL resource-guard object, Markdown result, and dashboard expose the applicable calibration and runtime-guard provenance. A no-fit model receives a model-scoped terminal disposition and omitted-work count; the suite does not invent benchmark rows for it.

You can still request a fixed context with a positive `--num-ctx` instead of `--adaptive-native-context`; the two options are mutually exclusive. The fixed value is applied to every selected model and both arms, so choose one that all selected checkpoints can actually load.

Schema v3 first runs the `simple_reasoning` pair for every model with a verified context fit as a global qualification phase. When the enabled/high arm exposes no observable trace, it runs the `math500_mini` pair as a fallback probe. Observable Boolean toggles qualify for off/on analysis; GPT-OSS can qualify only for descriptive low/high analysis; Muse/Gemma can continue with an explicitly unobservable off control. An off-arm trace, unsupported native off state, unverified enabled/high state, or inconclusive probe terminally omits only that model's remaining work. Other models continue. Ordinary benchmark timeouts and model errors are recorded as observations; only campaign-wide provenance, grader, infrastructure, or unverified-cancellation failures stop the campaign.

Within each task the runner alternates treatment order, cold-unloads between calls, records exact request payload types, and writes one campaign plan plus CSV, canonical JSONL, and Markdown reports; adaptive mode also writes the calibration artifact described above. The plan records model digests and aliases, source hashes, task IDs, per-model context calibration, generation settings, Ollama version, qualification policy, campaign seed, pair/treatment/row IDs, and a plan hash.

Resume an interrupted campaign only with its frozen manifest and identical options:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --thinking paired \
  --adaptive-native-context \
  --timeout 1800 \
  --resume-plan ~/.hermes/reports/ollama_benchmarks/ollama_standardized_local_benchmark_TIMESTAMP.plan.json \
  --run
```

Resume validates model/runtime/source/task provenance, treats JSONL as canonical, rebuilds CSV from completed rows, and executes only dynamically eligible missing row IDs. A schema-v3 adaptive manifest freezes the resolved context, guarded-ascending policy, exact Ollama parallelism and provenance, and complete calibration evidence for each model; resume uses those values and does not recalibrate. Keep the matching `.context-calibration.json` beside the plan: resume verifies its frozen filename and SHA-256 before continuing. The manifest also freezes the exact tag-to-digest mapping, Ollama endpoint and version, host identity, telemetry backend and interval, qualification/control policy, and cold/warm residency controls. Every retained JSONL row is checked against its planned model, task, treatment, payload, source hashes, resource-guard evidence, trace evidence, and canonical grading metadata. Protocol-invalid diagnostic evidence is preserved when it matches the canonical full text. A normal model error with a healthy recovered runtime is a completed observation; source, digest, context, safety-policy, runtime, or plan mismatches and infrastructure-safety events are rejected rather than silently mixed.

The direct runner deliberately cold-starts models and sends `keep_alive: 0s` unless `--no-stop` is supplied. `--no-stop` now leaves Ollama's normal residency policy intact for warm-run testing.

CSV and JSONL reports record the requested/resolved thinking level, control qualification and omission status, separated/inline reasoning evidence, requested/native context, calibration status/attempts/adjustment reason, resource-guard policy/evidence, source hashes, timeout and termination state, first streamed output and first answer timing, Ollama's final prompt/output token counts and durations, response/reasoning sizes, stream chunks, grading diagnostics, and telemetry. JSONL retains complete response, thinking text, complete calibration evidence, the canonical per-task resource-guard object, and full grading details. If the client deadline arrives before Ollama's final event, exact token and duration counters are unavailable; the partial text, byte/character counts, wall time, timeout state, and available guard evidence are still preserved.

Coding tasks use behavioral test cases rather than source-code keywords. Candidate Python is syntax-screened and run in a short-lived isolated-mode interpreter with restricted imports/builtins, process resource limits, and a grader deadline. This is defense-in-depth for trusted local-model output, not a secure OS sandbox for hostile code. Structured-output tasks require whole-response JSON and exact schemas; the defensive-cyber task requires both the correct classification and action.

See [DGX Spark usage](docs/DGX_SPARK.md) for the inference-free preflight and Spark-specific telemetry details.

## OpenClaw benchmark

Review the plan before running it:

```bash
python3 scripts/openclaw_18_test_benchmarks.py \
  --models qwen3.6:35b
```

Add `--run` only after explicit approval. A real run temporarily changes OpenClaw's default model, clears fallbacks during each model test, and restarts the gateway. The synchronized version captures the initial default and fallback list and restores both at the end.

The OpenClaw runner also defaults to a 1,800-second agent deadline, with a 30-second outer-process cleanup allowance. It requests the highest supported thinking level for capable models after verifying that the installed OpenClaw CLI supports `--thinking`. OpenClaw does not expose Ollama's `num_predict` through this path, so its output policy is honestly recorded as `gateway/model-default`; only the direct Ollama runner guarantees `num_predict: -1`.

For OCR, the ordinary `openclaw agent` CLI is not used because it has no image flag. The runner reads the preserved local PNG, base64-encodes those exact bytes, and calls the Gateway's supported `agent` RPC with an image attachment. Fallbacks remain cleared. Local models run OCR only when Ollama metadata advertises `image`, `vision`, or `ocr`; other models receive a recorded skip. For an authenticated external model, opt in only after verifying native image input by listing it in both `--external-models` and `--external-vision-models`.

On macOS the gateway restart defaults to `launchctl`. Linux users must provide the exact service command through `--gateway-restart-command`; the suite does not guess a service name. OpenClaw and Hermes are distinct measured agent paths and must not be relabeled as one another.

## Hermes Agent benchmark

The Hermes runner uses the same 18-task core list. Text tasks expose only the `clarify` toolset. For OCR, it exposes only the `vision` toolset and instructs `vision_analyze` to load the preserved PNG by absolute local path. Before a verified vision model runs, the runner temporarily sets `model.supports_vision=true` and `agent.image_input_mode=native`; this makes Hermes attach pixels to the model under test and prevents its auxiliary vision model from receiving benchmark credit. The original Hermes configuration is backed up and restored. Models lacking declared vision support are recorded as skipped. External models require the explicit `--external-vision-models` opt-in.

## Dashboard

```bash
python3 dashboard/generate_local_llm_dashboard.py
```

By default this writes:

- `~/Local LLM Benchmark Dashboard.html`
- model detail pages beneath `~/Local LLM Model Research/`

Set `LLM_BENCHMARK_HOST_LABEL` to override the automatically detected host label.
Set `LLM_BENCHMARK_OUTPUT_DIR` to write the dashboard and detail pages somewhere other than your home directory.

For paired campaigns, the dashboard retains both treatments under one model, displays qualification and context-calibration status, and ranks eligible results by strict correctness and grader-case accuracy. Causal off/on deltas are limited to observable qualified toggles; unobservable or invalid controls are unranked, and GPT-OSS low/high differences are explicitly descriptive. Wall time, token use, throughput, and telemetry remain operational descriptors rather than components of the accuracy ranking. Legacy single-arm CSVs remain readable.

## Source synchronization policy

Only this repository's source and documentation should be synchronized between machines. Do not copy `.hermes/reports`, generated dashboards, or model-detail pages between hosts: hardware results must remain attributable to the machine that produced them.

The canonical Git remote is [ArronJablonowski/LLM_Benchmarks](https://github.com/ArronJablonowski/LLM_Benchmarks). Review dataset notices and repository visibility before redistributing results or changing licensing.
