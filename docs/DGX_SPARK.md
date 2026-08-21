# DGX Spark usage

The direct Ollama runner and dashboard support NVIDIA DGX Spark on its Ubuntu-based DGX OS. The optional OpenClaw runner is not a Hermes Agent benchmark and is not required on the Spark.

## Inference-free preflight

These checks inspect software and hardware only. They do not load a model or send a generation request:

```bash
python3 --version
ollama --version
curl -fsS http://127.0.0.1:11434/api/version
nvidia-smi --query-gpu=name,utilization.gpu,temperature.gpu,power.draw \
  --format=csv,noheader,nounits
```

From the repository, print a benchmark plan without running it:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --limit-tasks 1
```

The plan-only default does not call `/api/generate`, stop or load a model, start telemetry, or create a report directory. With adaptive context it displays the unresolved guarded-ascending ladder beginning at 8,192 tokens but does not execute an empty-prompt load probe. `--list-tasks` is even more isolated and does not contact Ollama.

To review the complete standalone official AIME 2026 and GPQA Diamond task inventory on the Spark without contacting Ollama:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --task-profile standard-local \
  --list-tasks
```

This prints 228 vendored items. Their runtime requires no internet, external judge, container, or third-party Python dependency. Official profiles use single-arm execution; run matched `--thinking off` and `--thinking on` campaigns when both controls are needed. Paired mode is intentionally rejected so its qualification probes cannot change the official score denominator.

## Telemetry on Spark

With `--telemetry auto`, Linux selects `nvidia-smi` and records:

- System CPU utilization from `/proc/stat`
- GPU utilization from `nvidia-smi`
- GPU temperature from `nvidia-smi`
- GPU power draw from `nvidia-smi`
- Highest readable host/ACPI thermal-zone temperature from `/sys/class/thermal`

The ACPI thermal zones do not identify themselves as a trustworthy SoC sensor, so that value is recorded as host temperature and the SoC field remains blank. The GB10 uses unified system memory, so `nvidia-smi` VRAM totals are not meaningful and are not used. CPU power and whole-system power are unavailable through the installed interfaces and remain blank. GPU power must not be interpreted as total Spark power.

## Explicit real execution

A real benchmark requires the additional `--run` flag. Use it only after reviewing the plan and receiving explicit permission:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --models qwen3.6:35b \
  --timeout 1800 \
  --num-ctx 262144 \
  --run
```

Reports are written beneath `~/.hermes/reports/ollama_benchmarks/` unless `--output-dir` is provided. Results remain on the host that generated them.

The default profile uses Ollama streaming, `num_predict: -1`, capability-aware maximum thinking, accuracy-first behavioral/strict-schema graders, and a hard 30-minute response deadline for each task. Single-arm mode leaves context at the runtime/model default unless `--num-ctx` is supplied. Paired mode requires either a positive explicit `--num-ctx` or `--adaptive-native-context`; the latter safely calibrates upward from 8,192 tokens and requests native context only after it passes pre-admission. The full 18-task deadline budget is nine hours per model treatment, although normally completed generations end much sooner. On timeout, partial response and reasoning text plus size/timing/telemetry fields are recorded; exact Ollama token counters remain blank unless a final `done` event was received.

Behavioral coding grading uses restricted subprocess execution, not a secure OS sandbox. Only trusted local-model benchmark output should be evaluated. Full diagnostics are retained in JSONL and bounded grader summaries are written to CSV.

Use the canonical `--thinking auto|off|on|low|medium|high|max|paired` control only when a comparison requires an override. The default `auto` requests `max` for capable models and `high` for GPT-OSS. `--no-stop` enables a distinct warm-residency experiment; the guarded Spark census should retain default cold mode, in which every task is unloaded and empty residency is verified.

## Paired thinking census

Use paired mode to compare every installed, unique thinking checkpoint under controlled reasoning settings:

```bash
python3 scripts/ollama_standardized_local_benchmarks.py \
  --thinking paired \
  --adaptive-native-context \
  --timeout 1800 \
  --telemetry nvidia-smi
```

This command is plan-only until `--run` is explicitly added. It queries metadata to show the intended models, treatments, and unresolved calibration ladders, but does not load a model, calibrate context, send a generation request, start telemetry, or write reports. Paired mode excludes non-thinking models and deduplicates aliases sharing an Ollama digest.

After explicit approval, add `--run`. Adaptive execution requires a loopback Ollama URL on the measured Linux host so `/proc/meminfo`, `/proc/vmstat`, process identity, and NVIDIA state all describe the machine receiving the request. Calibration begins at 8,192 tokens. Successful candidates grow upward deterministically toward the advertised native limit; after a clean buffer/swap/capacity boundary, the pass/fail interval is refined on an 8,192-token grid. Native context is never the first probe and is requested only after the same 4 GiB-buffer admission applied to every smaller candidate succeeds.

Before each empty-prompt load request, the runner records an estimate of the checkpoint blob plus a conservative F16 KV cache using the exact `OLLAMA_NUM_PARALLEL` value resolved from the frozen Ollama daemon. Known hybrid-attention layouts—including the installed Qwen 3.5, Nemotron-H, Gemma 4, and Muse Glimmer families—have explicit estimator policies. Missing metadata or an unknown hybrid layout still fails closed because the run cannot preserve trustworthy context provenance. For supported models, admission permits RAM use down to a 4 GiB projected MemAvailable buffer. The same frozen parallelism and policy are rechecked before real paired inference.

A watchdog covers both calibration loads and real paired inference. Models may use system RAM down to the 4 GiB buffer; crossing that buffer or the first campaign-relative increase in actual swap use triggers cancellation. The monotonic `pswpout` counter remains telemetry rather than a permanent trigger. It also watches the kernel `oom_kill` counter and checks NVIDIA compute ownership. This userspace polling is best effort and cannot guarantee that a sudden allocation will never reach the kernel OOM path. A non-Ollama NVIDIA compute process present before a request causes an immediate global infrastructure failure; if one appears during a request, the watchdog cancels/stops only the benchmark target and never stops the external process. Stop or disable ComfyUI and every other GPU job source for the entire campaign, unless you can positively guarantee that they cannot launch work. An idle web service is not sufficient because it may become active between checks. The runner never kills those external processes; it aborts the benchmark when one is detected. Keep Hermes from issuing Ollama requests for the same reason.

This is conservative best-effort protection, not a hard OOM guarantee. A large allocation can outrun the 100 ms userspace watchdog; hard containment would require an operating-system memory boundary that this suite does not silently create. Use a quiescent or dedicated Spark for the campaign.

Calibration and default cold paired execution also require exclusive Ollama residency. `/api/ps` must contain no unrelated model before, during, or after calibration and must be empty before and after every task. During the post-task check, the runner polls for at most 30 seconds only while its target unloads; an unrelated resident causes immediate abort and is never stopped.

Only a clean candidate-capacity rejection or clean watchdog pressure event may bound the resolved context below native, and only after unload, daemon health, and memory/swap recovery are verified. A kernel OOM, external process, Ollama daemon/identity failure, unload failure, recovery failure, or watchdog failure aborts the entire campaign and never authorizes another candidate. `no-fit` means the guarded policy could not safely verify even its 8,192-token minimum; it is not proof that the model is intrinsically unable to run.

The highest safely verified fit is frozen independently for each model and used unchanged in both treatments. An incrementally persisted `.context-calibration.json` retains safety-policy constants, exact parallelism provenance, admission estimates, resource baselines, watchdog observations, attempts, loaded-context evidence, and adjustment/failure reasons. The final plan records the same resolution and policy and binds the artifact filename and SHA-256. CSV/JSONL rows mirror the context evidence, canonical JSONL retains a per-task `resource_guard` object, and Markdown/dashboard results expose the resolved context and safety outcome. Resume reuses the frozen values without recalibrating, so preserve the calibration artifact beside the plan. A no-fit model is terminally omitted without synthetic rows or scores. Use a positive `--num-ctx` instead when one fixed context is intentional; it is mutually exclusive with `--adaptive-native-context`.

Schema v3 uses model-specific treatment contracts:

| Model family | Requested arms | Reporting rule |
|---|---|---|
| Most supported thinking models | Boolean `false` / Boolean `true` | Eligible for causal off/on comparison only after trace qualification |
| Mistral Medium 3.5 | Boolean `false` / string `"high"` | Off/high; Boolean `true` is not used as the maximum treatment |
| GPT-OSS | string `"low"` / string `"high"` | Descriptive level range; GPT-OSS has no disabled arm |
| Foundation-Sec reasoning and DeepSeek-R1 distill | Boolean `false` / Boolean `true` diagnostic | Native off is unsupported; preserve the diagnostic pair and omit later work |
| Muse Glimmer and Gemma 4 | Boolean `false` / Boolean `true` | Off behavior is not observable through the installed parser/renderer; results are descriptive |

The runner executes `simple_reasoning` under both arms for every model with a verified context fit before full benchmarking. If the enabled/high trace is not observed, `math500_mini` becomes the fallback qualification pair. A visible off-arm trace, unsupported off state, unverified enabled/high state, failed probe, or no-fit context terminally omits only that model's remaining rows; the rest of the campaign continues. The plan and results record qualification status, reason, separated/inline trace evidence, and omitted work. Speed, token, and telemetry differences are descriptive operational data; correctness and grader-case accuracy remain the ranking criteria.

The paired plan alternates arm order per task and records a frozen schema-v3 manifest, canonical JSONL, exact model tags/digests and aliases, request payloads, qualification and runtime-safety policies, per-model context calibration and parallelism, source hashes, IDs, endpoint/host identity, telemetry settings, and cold/warm residency controls. To resume after interruption, provide the original `.plan.json`, the same context-mode flag, timeout, endpoint, telemetry options, residency options, `--thinking paired`, and `--run`. For an adaptive campaign, that means retaining `--adaptive-native-context`; it selects the manifest's frozen values rather than probing again. Resume verifies the calibration artifact hash, every retained canonical record, and its per-task resource-guard evidence, then recomputes reasoning traces from full JSONL text. It rejects model, runtime, source, task, context, safety/control-policy, parallelism, or generation-setting mismatches and schedules only missing, still-eligible row IDs. Protocol-invalid diagnostic evidence remains valid scientific input when its stored fields match that canonical text.

## Dashboard

Dashboard generation reads Ollama inventory and existing CSVs; it does not run a benchmark:

```bash
python3 dashboard/generate_local_llm_dashboard.py
```

Linux hardware discovery uses DMI, `lscpu`, `/proc/meminfo`, filesystem capacity, and `nvidia-smi`. A DGX product name is displayed as `NVIDIA DGX Spark`. Set `LLM_BENCHMARK_OUTPUT_DIR` or `LLM_BENCHMARK_HOST_LABEL` to override the output location or display label.
