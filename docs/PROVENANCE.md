# Source provenance

The repository was assembled on August 14, 2026 from the newest general-purpose benchmark components found across the two Macs.

Canonical source inputs:

- Direct Ollama runner: Mac Mini, modified June 22, 2026; SHA-256 `dbb840a3214e0eefb7d4f44dcca8895b27b97783d2239676368e23fc78182391`
- OpenClaw runner: Mac Mini, modified June 22, 2026; SHA-256 `96f1fe8ae33d5dfadf9654ed90e3c065c839448283f9a8b6448d7c03679a094b`
- Dashboard base: Mac Mini, modified June 23, 2026; SHA-256 `fcede9f7b72b213deaa23e720407c07e5c3ad4e800c7b9b1a7f62c51beacc0fa`

Portability and safety changes made during synchronization:

- Frozen full official AIME 2026 (30 items) and GPQA Diamond (198 items) snapshots acquired from their upstream public distributions on August 21, 2026, normalized to JSONL, and bound by SHA-256 in `data/standard_local/manifest.json`
- Standard-library-only official task loader with fail-closed file hash/count validation, deterministic frozen GPQA option ordering, anchored exact-answer grading, and no benchmark-runtime network or external-judge dependency
- Separate standard-local task profiles that preserve official item denominators and reject auxiliary paired-thinking qualification probes; reasoning-control comparisons use separate single-arm campaigns

- Dynamic Mac Studio/Mac Mini/NVIDIA DGX Spark dashboard labels
- Installed-model filtering from the newer Studio dashboard behavior
- Partial CSV merging by model and task, with newer task rows winning
- Unified 18-test telemetry support without double-counting legacy smoke CSVs
- Cross-platform telemetry (`mactop` on macOS; `nvidia-smi` plus `/proc` on NVIDIA Linux)
- Linux system discovery through DMI, `lscpu`, `/proc/meminfo`, and `nvidia-smi`
- Host, OS, architecture, telemetry, Ollama-version, model-digest, suite-version, and run-ID provenance
- Partial-history merging constrained to the newest compatible host/model/runtime provenance cohort
- Plan-only CLI defaults with an explicit `--run` execution guard
- Full-capability direct profile with `num_predict: -1`, streamed response/reasoning capture, and a 1,800-second hard per-task deadline
- Capability-aware maximum thinking (`max`, or `high` for GPT-OSS), with explicit requested/resolved/effective provenance
- Complete JSONL response/reasoning retention and scalar token, timeout, first-output, termination, character/byte, and stream-chunk statistics
- Exact Ollama usage counters restricted to completed final events; partial timeout output retained without fabricated token counts
- OpenClaw 1,800-second agent deadline plus bounded cleanup grace, full stdout/stderr retention, and honest gateway-default output policy
- Capability-gated agent-path OCR using one preserved, hashed PNG: Hermes receives an absolute local path through native-only `vision_analyze`, while OpenClaw receives the same bytes through the Gateway `agent` RPC attachment field
- Explicit vision provenance and conservative skips; Hermes auxiliary vision fallback cannot earn OCR credit, OpenClaw fallbacks remain cleared, and external vision models require operator opt-in
- Documentation-only IP addresses in public-safe benchmark prompts
- Capture and restoration of the initial OpenClaw model and fallback list
- Configurable non-macOS OpenClaw gateway restart command
- Accuracy-first behavioral graders for Python tasks, including malformed-input and boundary tests
- Strict whole-response JSON/schema grading and objective defensive-cyber classification/action scoring
- Restricted isolated-mode grader workers with AST/import/builtin screening, resource limits, and deadlines
- Explicit, reportable `num_ctx` pinning plus opt-in per-model `--adaptive-native-context` for paired campaigns
- Run-only Linux/loopback calibration that starts at 8,192 tokens, grows deterministically toward the advertised native limit, admits native only when safe, and refines a clean pass/fail boundary on the 8,192-token grid
- Strict static checkpoint-blob plus conservative F16 KV-cache admission at frozen exact Ollama parallelism, including explicit known hybrid-attention layouts and fail-closed handling for missing or unknown estimator evidence
- Near-full-RAM policy with a 4 GiB MemAvailable buffer; crossing the buffer or any campaign-relative actual swap-use growth triggers cancellation, `pswpout` remains telemetry, and OOM evidence remains a hard infrastructure failure
- Post-unload recovery requires the active 4 GiB MemAvailable buffer, zero campaign-relative swap growth, and no new OOM evidence. It does not require returning to the campaign's initial MemAvailable because Linux page-cache accounting legitimately moves between model loads.
- Calibration and real-inference watchdogs for memory pressure, kernel OOM events, exact Ollama ownership/parallelism, exclusive Ollama residency, and NVIDIA compute exclusivity; external compute processes and unrelated resident models are never stopped
- Global infrastructure aborts for OOM, external compute activity, daemon/identity, unload, recovery, or watchdog failures; only clean capacity pressure followed by verified recovery can establish a smaller context
- Incrementally persisted calibration policy, estimates, baselines, attempts, loaded-context observations, watchdog evidence, and reasons, bound by filename/SHA-256 into the frozen plan and propagated to result rows, canonical JSONL resource guards, Markdown, and dashboard output
- No synthetic result rows for `no-fit`; it means no candidate at or above 8,192 tokens was safely verified under the guarded policy, and terminal disposition plus omitted frozen work remain manifest-level facts
- Runner/grader source hashes and per-row grading diagnostics
- Schema-v3 paired-thinking policy with model-specific exact controls: ordinary Boolean off/on, Mistral Boolean-off/string-high, GPT-OSS low/high, diagnostic unsupported-off probes for Foundation/DeepSeek, and explicitly unobservable Muse/Gemma off states
- Primary `simple_reasoning` and conditional `math500_mini` fallback qualification, separated/inline trace classification, model-scoped terminal omissions, and campaign continuation for unaffected models
- Hashed schema-v3 plan manifests plus experiment, pair, treatment, and row identifiers that prevent cross-arm or cross-campaign merging
- Canonical-JSONL resume with exact tag-to-digest, endpoint, host, telemetry, residency, model/runtime/source/task/context/calibration/safety/parallelism/qualification/generation, resource-guard, trace-evidence, and per-row work-item validation plus dynamically eligible missing-row continuation without recalibration
- Paired dashboard summaries retaining both treatments, qualification and context-calibration status, causal deltas only for observable valid controls, descriptive GPT-OSS/unobservable comparisons, correctness-first rankings, and descriptive speed/token/telemetry costs
- Generated results, dashboards, logs, caches, machine state, and specialized project benchmarks excluded
