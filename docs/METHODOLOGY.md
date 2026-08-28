# Benchmark methodology

## Direct suite

The direct runner executes 18 single-shot tasks per compatible model:

- Three smoke tests for exact instruction following, short reasoning, and compact code generation
- Fourteen text mini tasks covering constrained output, mathematics, knowledge, science reasoning, commonsense, truthfulness, coding, tool selection, retrieval grounding, prompt-injection resistance, and defensive SOC reasoning
- One OCR task for models whose Ollama metadata advertises image, vision, or OCR capability

Temperature is zero and the seed is fixed at 42. The default `accuracy-first-v2` profile sends `num_predict: -1`, so there is no suite-imposed output-token ceiling. Models advertising Ollama's `thinking` capability receive the highest supported level (`max`, or `high` for GPT-OSS); unsupported models receive no thinking field. In ordinary single-arm runs, the runner does not override `num_ctx` unless an explicit `--num-ctx` value is supplied and recorded. Paired runs require either one positive explicit `--num-ctx` or `--adaptive-native-context`.

Generation is streamed. Separate reasoning and answer fragments are accumulated without truncation in JSONL, while CSV stores character/byte counts and a response preview. A hard wall-clock deadline includes model loading and generation and defaults to the allowed maximum of 1,800 seconds per task. With 18 tasks, the theoretical deadline total is nine hours per model.

Unlimited output at the suite layer is not literal unbounded execution. EOS, model stop sequences, context/runtime constraints, errors, and the 30-minute client deadline can terminate a response. Ollama's exact prompt/output token counts and generation durations arrive only in its final `done` event. If a task times out first, those counters remain blank rather than being estimated; partial response/reasoning text, sizes, chunks, wall time, timeout layer, and telemetry are retained.

By default each task unloads the model before execution and requests `keep_alive: 0s`, so timings include cold-start effects. `--no-stop` omits the forced unload/residency override for warm-run testing. A timed-out request is explicitly stopped even in warm mode; if cancellation cannot be verified, the run aborts before another task can be contaminated.

The CLI defaults to plan-only behavior. Actual model inference requires an explicit `--run` flag. Planning, task listing, unit tests, and dashboard generation are not benchmark executions.

Every core execution path publishes the same task catalog through `--list-tasks`/`--list-tests` and accepts exact printed IDs through `--test`/`--tasks`. Selection happens before model discovery, agent-runtime checks, telemetry, report creation, or inference. Unknown IDs fail closed. The three-path campaign wrapper also freezes `all-core` or the single selected ID in its campaign directory so completion markers from different task sets cannot be mixed. Paired-thinking selection may add its frozen qualification probes; use a single-arm mode when exactly one inference task is required.

## Full official standalone profiles

The default CLI path loads only the 18-task core suite. `--full-suite` (alias `--full_suite`) is the required opt-in for the newer, long-running official profiles. By itself it loads two complete, frozen official test splits: 30 AIME 2026 math problems and 198 GPQA Diamond graduate-level multiple-choice questions. With the switch present, `--task-profile aime2026` and `--task-profile gpqa-diamond` select only one split. Official profiles are rejected without the switch. Integrity is checked against `data/standard_local/manifest.json` before model discovery or inference.

AIME is graded by an anchored terminal integer answer; both ordinary integer spelling and the contest's zero-padded three-digit spelling are accepted. GPQA is graded by an anchored terminal A-D choice. Its four choices were deterministically shuffled once from the upstream record identifier and frozen in the vendored snapshot. Runtime scoring uses the existing standard-library exact-answer grader. It does not use answer substrings, an LLM judge, network access, or external packages.

These profiles keep the direct runner's streaming capture, unlimited suite-level generation (`num_predict=-1`), 1,800-second maximum deadline, telemetry, provenance, cold/warm controls, and explicit `--run` guard. They intentionally reject schema-v3 paired mode because its `simple_reasoning` and `math500_mini` qualification probes are not official AIME/GPQA items. Separate single-arm thinking-off and thinking-on runs keep the official denominator exact.

The selection boundary is methodological, not a claim that the other Muse Glimmer evaluations are unimportant. IFBench, SciCode, multimodal document/UI benchmarks, LLM-judge benchmarks, and agent-environment benchmarks were excluded because faithfully reproducing their official scoring requires external packages, large auxiliary assets, another model/service, or a simulator/container. A reduced imitation is not reported under an official benchmark name.

## Paired thinking protocol

`--thinking paired` is one schema-v3 campaign, not two unrelated single-arm runs. It selects models with verified Ollama thinking capability, groups identical non-empty digests so aliases are measured once, and records every alias. Non-thinking models are excluded. Temperature remains zero, generation seed remains 42, `num_predict` remains -1, and every task retains the 1,800-second ceiling.

The frozen control policy is model-specific:

| Installed-model class | Exact `think` values | Qualification and interpretation |
|---|---|---|
| Default supported thinking control, including Qwen-family models | Boolean `false`, Boolean `true` | Observable off/on toggle must be demonstrated at runtime |
| Mistral Medium 3.5 | Boolean `false`, string `"high"` | Uses the actual off/high contract; Boolean `true` is not treated as maximum thinking |
| GPT-OSS | string `"low"`, string `"high"` | Minimum/maximum reasoning levels; no disabled state and never an off/on effect |
| Foundation-Sec reasoning | Boolean `false`, Boolean `true` diagnostic | Installed template has no supported native off control; diagnostic pair is retained and later rows are omitted |
| DeepSeek-R1 distill | Boolean `false`, Boolean `true` diagnostic | Installed template has no reliable native off control; diagnostic pair is retained and later rows are omitted |
| Muse Glimmer | Boolean `false`, Boolean `true` | Off-state reasoning is hidden by parser behavior, so off effectiveness is unobservable |
| Gemma 4 | Boolean `false`, Boolean `true` | Ghost-thinking/parser behavior makes the off state unobservable |

Every plan records exact field presence, JSON payload type/value, requested/resolved/effective treatment, static control policy, and the installed-runtime evidence code. Runtime observation remains authoritative. Separate Ollama reasoning fragments and literal inline `<think>` content are both classified; empty markers do not count as a reasoning trace. Nemotron prompts containing `/think` or `/no_think` are rejected because those directives can override the requested API control.

### Adaptive native context

`--adaptive-native-context` is mutually exclusive with a positive `--num-ctx` and is available only for paired runs. Without `--run`, it displays an unresolved guarded-ascending ladder; it does not probe, stop or load a model, start telemetry, perform inference, or write reports. An explicitly approved run requires a literal loopback Ollama endpoint on the measured Linux host. This keeps `/proc/meminfo`, `/proc/vmstat`, process ownership, and NVIDIA state in the same trust boundary as the Ollama request.

Run-only calibration starts at 8,192 tokens. Each successful candidate grows deterministically—normally doubling—toward the model's advertised native limit. Once a clean buffer/swap/capacity boundary is established, the remaining pass/fail interval is refined on an 8,192-token grid. Native context is not a blind first probe: it is requested only when the same 4 GiB-buffer admission applied to every smaller candidate says it is safe. Each admitted probe uses Ollama's generate endpoint with an empty prompt solely to load the requested context, verifies the exact loaded context in `/api/ps`, and then unloads the target.

Pre-admission records the checkpoint blob plus a conservative F16 KV-cache estimate at the exact frozen Ollama parallelism. It accounts for recognized hybrid-attention layouts rather than treating every checkpoint as a conventional full-attention transformer; missing required metadata or an unknown hybrid layout fails closed. For supported models, the estimate is informational and low projected MemAvailable does not reject a candidate. The runner resolves and freezes the exact `OLLAMA_NUM_PARALLEL` value and its provenance before calibration, uses Ollama's documented default of one only when it is genuinely unset, and rejects drift before real inference.

A resource watchdog covers both empty-prompt calibration loads and real paired inference. It permits near-full system-RAM use while retaining a 4 GiB MemAvailable buffer and allows up to 1 GiB of actual swap growth relative to the frozen campaign baseline. It cancels if either limit is exceeded. The monotonic `pswpout` counter remains telemetry rather than a permanent trigger. The runner also observes the kernel `oom_kill` counter, monitors Ollama daemon identity and health, and enforces NVIDIA compute exclusivity. This is best-effort userspace protection rather than a hard memory boundary. A non-Ollama NVIDIA compute process discovered before any request causes a global infrastructure failure. If one appears during a request, the suite cancels or stops only its benchmark target; it never stops the external process.

After unload, recovery requires the active 4 GiB MemAvailable buffer, campaign-relative swap growth at or below the frozen 1 GiB allowance, and no new OOM evidence. It deliberately does not require MemAvailable to return near the frozen campaign baseline: Linux may retain or reclaim page cache between model loads, and that accounting drift is not memory pressure while the buffer and swap invariants remain satisfied.

Admission plus a 100 ms userspace watchdog is a conservative best-effort guard, not a mathematical OOM guarantee; a single allocator can outpace polling. The methodology therefore requires a quiescent measured host, with other GPU job sources stopped or prevented from launching and concurrent Ollama clients disabled. The suite does not silently create an operating-system memory boundary or mutate external services.

Only a clean static capacity rejection or clean watchdog memory-pressure event can establish an upper context bound, and only after target unload, daemon health, and memory/swap recovery are verified. A kernel OOM, external NVIDIA process, Ollama daemon or identity failure, unload failure, recovery failure, or watchdog failure aborts the campaign globally and never authorizes a lower candidate. An unrelated Ollama resident before, during, or after calibration is likewise infrastructure contamination. Default cold paired execution requires `/api/ps` to be empty before and after every task; after a task the runner polls for at most 30 seconds only while its own target unloads and aborts immediately on an unrelated resident. It never unloads another client's model.

Calibration writes its policy, exact parallelism provenance, static estimates, resource baselines, watchdog observations, loaded-context evidence, and every ordered attempt/reason incrementally to a `.context-calibration.json` artifact. The final schema-v3 plan freezes the per-model native and resolved contexts, native-fit/adjusted-fit/no-fit outcome, complete evidence and runtime safety policy, and binds the artifact by filename and SHA-256. The plan, CSV/JSONL rows, canonical per-task JSONL `resource_guard`, Markdown report, and dashboard expose the applicable resolution and safety evidence. Both thinking treatments use the same frozen value for a model, while different models may resolve differently.

`no-fit` means no candidate at or above the guarded 8,192-token minimum was safely verified under this policy. It is not a claim that the model can never load under a different environment or policy. The model receives a terminal model-level disposition and omitted frozen-row count; no synthetic benchmark rows, failures, or scores are manufactured. A fixed positive `--num-ctx` remains available for experiments that intentionally require one uniform context across models.

### Thinking-control qualification

All models with a verified context fit first execute both treatments for `simple_reasoning`; this global primary barrier prevents one model from finishing the suite before the others' controls are checked. If an otherwise valid enabled/high arm has no observable reasoning trace, both treatments run on `math500_mini` as a fallback probe. A primary-qualified model later runs `math500_mini` once as an ordinary benchmark task; a fallback-qualified model reuses its existing fallback rows and never repeats them.

An observable clean-off/trace-on Boolean pair is eligible for causal off/on comparison. Muse and Gemma may continue under `off-control-unobservable`, but their differences are descriptive rather than causal. GPT-OSS must expose traces for both low and high to qualify its descriptive level range. A visible off-arm trace, statically unsupported native off state, absent enabled/high trace after fallback, malformed/failed qualification, or no-fit context is terminal for that model and omits only its remaining rows. The campaign continues with other models. A later off-arm leak also stops only that model before its next task pair.

Treatments are adjacent within each model/task pair, while first-arm order alternates deterministically using the recorded campaign seed. Each call retains the cold-unload policy. This reduces but does not eliminate file-cache, thermal, and order effects; performance is secondary to correctness.

The primary paired outcome is strict task correctness among applicable tasks. Behavioral grader cases provide a more granular secondary measure. Every discordant answer should receive semantic review so formatting failures are distinguished from genuine reasoning or functional-code failures. Token, wall-time, throughput, first-answer, and telemetry ratios are descriptive operational costs, never inputs to the accuracy ranking.

An off arm emitting separated reasoning or leaked inline `<think>` content is preserved as protocol-invalid scientific evidence and produces `off-control-ineffective` for that model. An enabled arm with no observed trace is reported conservatively rather than silently treated as verified reasoning. GPT-OSS low/high is always analyzed separately. One pass per treatment is an exploratory census; stronger claims require repeated, counterbalanced campaigns.

Paired campaigns write a hashed schema-v3 plan manifest plus canonical JSONL. Pair, treatment, and row IDs prevent arms or independent campaigns from being merged. Resume verifies complete inference-relevant provenance, including per-row resource-guard evidence, and recomputes separated/inline trace evidence from the full canonical response text before scheduling only missing rows that remain eligible under the derived qualification state. Adaptive resume requires the matching calibration artifact beside the plan, verifies its filename and SHA-256, retains the frozen per-model context, parallelism, calibration, and runtime-safety policy, and does not probe again. Timeouts and ordinary post-qualification model errors with a healthy recovered runtime are observations; canonical-record tampering, source/runtime/safety provenance drift, grader infrastructure failure, global infrastructure failure, or unverified timeout cancellation aborts the campaign. A protocol-invalid off-control observation is resumable when its stored evidence agrees with canonical JSONL.

## OpenClaw suite

The OpenClaw runner uses the same 18 core task IDs. It measures the complete agent path, including gateway and routing overhead, rather than only Ollama generation time. Its results must not be compared directly with direct-runner latency.

Its agent deadline defaults to and is capped at 1,800 seconds; an outer subprocess deadline adds 30 seconds of cleanup grace by default. Capability-aware `auto` thinking requests `max`, or `high` for GPT-OSS, and omits the flag for unsupported models. Before any gateway configuration mutation, the runner verifies that the installed OpenClaw CLI exposes `--thinking`. Provider-reported thinking and usage data are recorded when available.

OpenClaw does not expose Ollama `num_predict` through this CLI route. These rows therefore record `output_token_policy=gateway/model-default` with no claimed numeric limit. Full assistant text, stdout, and stderr are retained in JSONL; scalar token, timeout, response-size, and process statistics are placed in CSV when the gateway reports them.

The OCR task is applicable only when frozen/discovered model metadata advertises image, vision, or OCR capability. The exact deterministic PNG used by the direct path is written under the report directory, hashed, and sent through the Gateway `agent` RPC attachment field as base64. The text-only `openclaw agent` command is not used for that task. Fallbacks are cleared, so a different model cannot silently earn the result. Non-vision models are skipped rather than failed; external models require explicit operator declaration through `--external-vision-models`.

## Hermes Agent suite

The Hermes runner also uses the 18 core task IDs. Text calls expose only the `clarify` toolset. OCR calls expose only `vision`, pass the preserved image's absolute local path to `vision_analyze`, and temporarily force `model.supports_vision=true` plus `agent.image_input_mode=native` only for models whose source metadata already declares vision capability. Hermes therefore returns a multimodal tool result to the measured model instead of invoking its auxiliary vision model. Its original configuration is restored after the run. Capability-negative models are recorded as skips, and authenticated external models require explicit `--external-vision-models` opt-in.

All three paths record the fixture path, SHA-256, MIME type, byte count, delivery transport, native-vision requirement, capability decision, and skip reason. This proves byte identity while keeping path-specific latency comparisons descriptive.

## Accuracy-first grading

Exact-answer tasks use exact or required final-answer checks. JSON instruction and tool tasks parse the entire response as JSON, enforce exact top-level schemas, compare value types as well as values, and reject Markdown or surrounding prose. The defensive-cyber task uses enumerated classification/action choices so both decisions can be objectively checked.

The three coding tasks use behavior rather than source-code substrings. `is_private_ipv4` is tested across all RFC1918 boundaries, adjacent public ranges, other non-RFC1918 special ranges, malformed octets, malformed shapes, and exception paths. `count_unique_ips` is tested for duplicates, multiple addresses per line, invalid octets, IPv4 mixed with IPv6, and empty input. Boolean and integer result types are checked strictly.

Candidate code may be plain Python or one complete Python code fence. It is parsed, screened against a restricted syntax/import/builtin policy, and evaluated in a short-lived `python -I -S -B` worker with a temporary working directory, resource limits, and a hard grader deadline. This restricted execution is not an OS security boundary and must not be used for arbitrary hostile code. Grading begins after inference telemetry is captured, so grader work does not contaminate model wall time or GPU/CPU telemetry.

Each task is still sampled once by default. These small proxy tasks are useful for local regression, functional correctness, formatting compliance, tool routing, and rough performance measurements, but are not statistically rigorous or substitutes for full official datasets. Repeated, counterbalanced runs are required before making strong claims about small accuracy differences.

## COH Ollama text path

The COH runner executes the 17 non-image core tasks through COH's durable
model-surface projection, inference admission seal, qualified provider gateway,
and strict loopback Ollama adapter. Each row binds the frozen model digest and
records COH capability, surface-binding, response-provenance, and token-usage
evidence. The local benchmark qualification is ephemeral and test-only; it is
not evidence of production qualification or independent security approval.

## Cross-host comparison

Keep result histories separate by host. Before comparing performance, align:

- exact model tag and digest
- quantization
- context configuration
- benchmark profile, output-token policy, and response deadline
- requested and resolved thinking level
- Ollama and runner versions
- telemetry availability
- telemetry backend (`mactop`, `nvidia-smi`, or none)
- cold-start versus warm-start behavior

Every new result row records the host, host label, operating system, architecture, suite version, telemetry backend, Ollama version, model digest, and run ID. Older CSVs remain readable but may not contain this provenance.

Paired campaigns use a schema-v3 frozen plan. In addition to task and treatment identity, it binds the qualification/trace policy, exact model tag and digest, Ollama endpoint/version, host identity, telemetry backend/interval, source hashes, generation settings, and cold/warm residency policy. Explicit-context plans freeze the common request; adaptive plans also freeze each model's native metadata, resolved context, adjustment reason, exact Ollama parallelism, resource-safety policy, complete calibration evidence, and calibration-artifact hash. Resume accepts only canonical JSONL records whose planned work item, resource guard, trace evidence, and grading metadata match that manifest; CSV is derived from those records rather than trusted as a resume source.

## Telemetry field availability

The normalized CSV schema is shared across platforms, but the underlying sensors differ:

| Field group | Apple silicon/macOS | NVIDIA Linux/DGX Spark |
|---|---|---|
| CPU utilization | `mactop` | `/proc/stat` |
| GPU utilization | `mactop` | `nvidia-smi` |
| GPU temperature | `mactop` | `nvidia-smi` |
| SoC temperature | `mactop` | Unavailable; left blank |
| Host/ACPI temperature | Unavailable; left blank | Highest readable Linux thermal zone |
| CPU/GPU/system power | `mactop`, where exposed | GPU power only through `nvidia-smi` |
| Total-system power | `mactop`, where exposed | Unavailable; left blank |

Blank means unavailable or unsampled. It must not be interpreted as zero. Cross-host thermal and power measurements are useful operational context, but they are not sensor-equivalent laboratory measurements.
