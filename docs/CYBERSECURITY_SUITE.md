# Cybersecurity agent benchmark suite

The `cybersecurity` suite is a separate, tool-capable agent evaluation with the
profile `cybersecurity-agent-v1`. It contains 24 original offline tasks. No row,
score, or report is shared with the Standard, Coding, or Creative suites.

Each observation is one **model × model runner × agent harness × security
task**. The agent works in a fresh fixture, the runner preserves provenance and
resource telemetry, and an external deterministic grader evaluates the output.

## Capability coverage

| Track | Tasks | Capabilities |
|---|---:|---|
| Foundations and governance | 3 | Network and identity architecture, advanced TLS/DNS/JWT reasoning, NIST CSF risk prioritization |
| Threat intelligence and vulnerability analysis | 3 | ATT&CK mapping, CVSS v4 triage, mixed-confidence threat reporting |
| Defensive operations | 3 | SOC alert triage, incident reconstruction, harmless static malware analysis |
| Detection engineering and SIEM | 5 | Sigma, Splunk SPL, Microsoft Sentinel KQL, Elastic ES\|QL, Google SecOps YARA-L 2.0 |
| Application security and exploit analysis | 3 | CWE-grounded review, secure patching, API BOLA remediation |
| Offensive security and CTF | 4 | Native crash analysis, constrained local proof of concept, lab attack-path reasoning, web/crypto/forensics/reverse CTF |
| LLM and agent security | 2 | Indirect prompt injection, tool poisoning, RAG poisoning, sensitive-action control |
| Infrastructure and cloud security | 1 | Least-privilege IAM and Kubernetes workload hardening |

The task IDs can be listed without touching a model:

```bash
python3 scripts/cybersecurity_agent_benchmarks.py \
  --suite cybersecurity --harness pi \
  --models-file models.tsv --output-dir reports/pi --workspace cyber-work \
  --list-tasks
```

Use `--track "Detection engineering and SIEM"` one or more times to run a
track subset, or `--tasks TASK_ID ...` for explicit task selection. A complete
ranking always uses the full 24-task denominator, so partial runs remain visibly
partial.

## Task catalog

| Track | Task ID | Evaluation target |
|---|---|---|
| Foundations and governance | `cyber_foundations_architecture` | Segmented enterprise network and identity architecture |
| Foundations and governance | `cyber_advanced_protocol_reasoning` | TLS, DNS, and JWT failure analysis |
| Foundations and governance | `cyber_governance_risk_prioritization` | Risk ordering and NIST CSF-aligned actions |
| Threat intelligence and vulnerability analysis | `cyber_cti_attack_mapping` | Evidence-grounded MITRE ATT&CK mapping |
| Threat intelligence and vulnerability analysis | `cyber_vulnerability_cvss_triage` | CVSS v4 vectors plus environmental context |
| Threat intelligence and vulnerability analysis | `cyber_threat_report_synthesis` | Mixed-confidence intelligence synthesis |
| Defensive operations | `cyber_soc_alert_triage` | Alert correlation, escalation, and false-positive handling |
| Defensive operations | `cyber_incident_timeline` | Incident chronology, containment, and recovery |
| Defensive operations | `cyber_malware_static_analysis` | Harmless strings/import-metadata classification |
| Detection engineering and SIEM | `cyber_sigma_detection` | Portable Sigma rule authoring |
| Detection engineering and SIEM | `cyber_spl_detection` | Splunk SPL detection and triage |
| Detection engineering and SIEM | `cyber_sentinel_kql_detection` | Microsoft Sentinel KQL analytics |
| Detection engineering and SIEM | `cyber_elastic_esql_detection` | Elastic ES\|QL behavioral detection |
| Detection engineering and SIEM | `cyber_chronicle_yaral_detection` | Google SecOps YARA-L 2.0 correlation |
| Application security and exploit analysis | `cyber_appsec_code_review` | CWE-grounded code review and remediation advice |
| Application security and exploit analysis | `cyber_appsec_secure_patch` | Functional secure patching of a local utility |
| Application security and exploit analysis | `cyber_api_bola_remediation` | API object-level authorization repair |
| Offensive security and CTF | `cyber_exploit_crash_analysis` | Exploitability analysis of a synthetic native crash |
| Offensive security and CTF | `cyber_exploit_toy_poc` | Constrained proof of concept against toy local code |
| Offensive security and CTF | `cyber_pentest_attack_path` | Authorized-lab attack-path planning and validation |
| Offensive security and CTF | `cyber_ctf_multidiscipline` | Original web, crypto, forensics, and reversing challenges |
| LLM and agent security | `cyber_llm_prompt_injection` | Indirect prompt-injection resistance |
| LLM and agent security | `cyber_llm_tool_rag_security` | Tool and RAG poisoning, exfiltration, and approval controls |
| Infrastructure and cloud security | `cyber_cloud_kubernetes_hardening` | Least-privilege IAM and Kubernetes hardening |

## Standards and knowledge bases

The original fixtures and hidden checks operationalize the following public
standards and security knowledge bases:

- [NIST Cybersecurity Framework 2.0](https://www.nist.gov/publications/nist-cybersecurity-framework-csf-20) for risk governance and the Govern, Identify, Protect, Detect, Respond, and Recover lifecycle.
- [NIST SP 800-61 Rev. 3](https://csrc.nist.gov/pubs/sp/800/61/r3/final) for evidence-aware incident response and recovery.
- [MITRE ATT&CK](https://attack.mitre.org/) for behavior-to-technique mapping.
- [MITRE ATLAS](https://atlas.mitre.org/) and [NIST AI 100-2e2025](https://www.nist.gov/news-events/news/2025/03/nist-trustworthy-and-responsible-ai-report-adversarial-machine-learning) for adversarial AI and agent threats.
- [OWASP Top 10:2025](https://owasp.org/Top10/2025/), [OWASP API Security Top 10:2023](https://owasp.org/API-Security/editions/2023/en/0x00-header/), and [OWASP Top 10 for LLM Applications:2025](https://owasp.org/www-project-top-10-for-large-language-model-applications/) for application, API, and LLM risks.
- [2025 CWE Top 25](https://cwe.mitre.org/top25/) for software weakness classification.
- [FIRST CVSS v4.0](https://www.first.org/cvss/specification-document) for vulnerability vectors and contextual prioritization.
- [Sigma specification](https://sigmahq.io/sigma-specification/), [Splunk SPL/SPL2](https://help.splunk.com/en/splunk-cloud-platform/search/spl2-overview/what-is-spl2), [Microsoft Sentinel KQL analytics](https://learn.microsoft.com/en-us/azure/sentinel/threat-detection), [Elastic ES\|QL detections](https://www.elastic.co/docs/solutions/security/detect-and-alert/esql), and [Google SecOps YARA-L 2.0](https://docs.cloud.google.com/chronicle/docs/yara-l/getting-started2) for detection engineering.

## Published benchmark methodologies represented

The suite does not copy upstream test questions or flags. Instead, its original
fixtures incorporate the capability dimensions and evaluation patterns of:

- [CyberSecEval 4](https://github.com/meta-llama/PurpleLlama/blob/main/CybersecurityBenchmarks/README.md): insecure-code analysis, autonomous offensive capability, autopatching, malware analysis, CTI reasoning, prompt injection, and false-refusal awareness.
- [DefenderBench](https://github.com/microsoft/DefenderBench): network intrusion reasoning, malicious-content analysis, vulnerability detection/fixing, and security knowledge.
- [SEC-bench](https://github.com/sec-bench/sec-bench) and [BountyBench](https://github.com/bountybench/bountybench): reproducible vulnerability Detect, Exploit, and Patch workflows.
- [Cybench](https://github.com/andyzorigin/cybench), [NYU CTF Bench](https://nyu-llm-ctf.github.io/), and [InterCode-CTF](https://intercode-benchmark.github.io/): interactive, gradated, multidisciplinary CTF problem solving.
- [AgentDojo](https://github.com/sequrity-ai/agentdojo): measuring legitimate task utility and resistance to indirect prompt injection together.
- [CTIBench](https://arxiv.org/abs/2406.07599) and [CTIConnect](https://cticonnect.github.io/): ATT&CK extraction, root-cause mapping, attribution restraint, and multi-source synthesis.
- [SecBench](https://github.com/secbench-git/SecBench), [SecEval](https://github.com/XuanwuAI/SecEval), and [CS-Eval](https://github.com/CS-EVAL/CS-Eval): common and advanced knowledge, logical reasoning, multiple domains, and applied security work.

Results from this repository are **local-suite scores**, not official upstream
scores. The report states this explicitly.

## Safety boundary

The benchmark is deliberately useful without creating a live attack platform:

- No task authorizes internet scanning, real credentials, persistence, phishing,
  malware execution, evasion, or access outside its workspace.
- All organizations, hosts, domains, indicators, and incidents are fictional.
- Exploit-development work is limited to purpose-built toy code. The proof of
  concept may print only its synthetic flag and is rejected if it imports
  networking, process-launch, or HTTP capabilities.
- Malware analysis uses strings and import metadata only; no executable sample
  is present.
- Penetration testing is graph reasoning over an authorized fictional lab; no
  services are started or contacted.
- CTF challenges are original local files with deterministic flags.
- The runner repeats the workspace-only safety boundary in every prompt.

These controls keep comparisons reproducible and prevent ordinary benchmark
runs from interacting with unrelated systems.

## Scoring

Two accuracy metrics are reported:

- **Tasks resolved:** strict pass rate. Every hidden task check must pass.
- **Quality score:** checks passed divided by checks available. This exposes
  partial capability without counting an incomplete task as resolved.

Coverage is always calculated against all 24 profile tasks. The report also
shows per-track strict passes and check quality. Runtime, timeouts, temperatures,
peak memory, changed-file counts, and harness/model-runner versions are retained
as diagnostic evidence and never improve accuracy.

## Run it

Preview an Ollama campaign (no inference or workspace writes):

```bash
python3 scripts/cybersecurity_agent_benchmarks.py \
  --suite cybersecurity --harness pi \
  --models-file models.tsv --output-dir reports/pi --workspace cyber-work
```

Run the guarded multi-harness campaign:

```bash
BENCH_CAMPAIGN_DIR="$HOME/.hermes/reports/campaigns/cybersecurity-agent-v1" \
BENCH_MODELS_FILE="$HOME/.hermes/reports/campaigns/cybersecurity-agent-v1/models.tsv" \
BENCH_CLI_HARNESSES="pi goose openhands" \
ops/run_cybersecurity_agent_campaign.sh
```

The suite is also supported by the guarded llama.cpp, vLLM, and TensorRT-LLM
campaign wrappers through `BENCH_SUITE=cybersecurity`. Task timeouts default to
two hours and remain capped at four hours. Existing cold-unload, model-digest,
memory, swap, OOM, GPU exclusivity, and service-restoration safeguards remain in
force.

Generate or refresh only the cybersecurity report:

```bash
python3 dashboard/generate_cybersecurity_report.py \
  --input-root "$BENCH_CAMPAIGN_DIR" \
  --output "$BENCH_CAMPAIGN_DIR/cybersecurity_agent_report.html"
```

Evidence files are named `*_cybersecurity.csv` and
`*_cybersecurity.jsonl`. The report accepts only rows whose
`benchmark_suite` is exactly `cybersecurity`.

## ExploitGym external profile

[ExploitGym](https://github.com/sunblaze-ucb/exploitgym) is available as a
separate, explicitly opt-in profile. It evaluates real exploit development
across userspace programs, V8, and the Linux kernel. It is never folded into
the 24-task `cybersecurity-agent-v1` score.

The integration pins upstream commit
`e4123d043774623b2274e6bbe0155a423d631f0a` and verifies the commit, task-list
hashes, source license, data-license notice, and item counts before listing,
running, or importing results. The repository does **not** redistribute
ExploitGym's task corpus: its code is Apache-2.0, while its task artifacts keep
their individual upstream licenses.

Two task profiles are exposed:

| Profile | Instances | Purpose |
|---|---:|---|
| `sample` | 20 | Integration and infrastructure qualification |
| `v1` | 869 | Full published ExploitGym v1 corpus; requires `--full-suite` |

Evidence profile IDs also include the selected task profile and family (for
example, `exploitgym-v1-hardened-e4123d0:sample:all`) so qualification,
family-only, and full runs cannot be merged accidentally.

ExploitGym currently supplies Codex, Claude Code, and Gemini CLI agents. This
adapter therefore records its harness as `exploitgym-codex`,
`exploitgym-claude_code`, or `exploitgym-gemini_cli` and its model runner as
`exploitgym-llm-proxy`. It does not pretend that the upstream runner supports
Ollama, llama.cpp, vLLM, TensorRT-LLM, Pi, Goose, or OpenHands.

### Prepare the pinned upstream checkout

On an isolated Linux evaluation host:

```bash
git clone https://github.com/sunblaze-ucb/exploitgym.git \
  "$HOME/gitRepo/exploitgym"
git -C "$HOME/gitRepo/exploitgym" checkout \
  e4123d043774623b2274e6bbe0155a423d631f0a

cd "$HOME/gitRepo/exploitgym"
uv sync --extra proxy
bash scripts/setup/setup_data.sh
bash scripts/setup/validate.sh
docker pull ubuntu/squid:latest
uv run scripts/setup/pull_images.py data/task_ids/sample.txt
uv run scripts/setup/pre_run.py data/task_ids/sample.txt --hardened
```

`pre_run.py` prints fresh controller and proxy environment variables. Keep
those values out of repositories, reports, shell history, and agent-visible
directories.

### Safety controls

The adapter refuses execution unless all of these controls remain enabled:

- hardened userspace images, strict V8 mode, and strict kernel defenses;
- the upstream API-only internal run network and separate install network;
- both upstream firewall containers running;
- the upstream LLM proxy, which blocks provider-side web retrieval;
- private literal-IP or loopback controller/proxy endpoints;
- one worker, no retained agent container, a bounded timeout and budget;
- four fresh controller/proxy secrets; and
- an exact command-line acknowledgement that this runs real exploit work.

The setup phase can download packages through ExploitGym's separate install
proxy. Before the agent starts, upstream moves the task container to the
API-only run network. Run this profile only on a dedicated, isolated host; do
not launch it on a workstation or network that contains sensitive services.

### Preview, run, and import

Listing is read-only and does not contact Docker or inference services:

```bash
EXPLOITGYM_MODEL="gpt-5.6-sol" \
ops/run_exploitgym_campaign.sh --list-tasks
```

The wrapper is plan-only unless `--run` is supplied. Execute the 20-instance
qualification profile only after reviewing the plan and exporting the secrets
printed by upstream `pre_run.py`:

```bash
EXPLOITGYM_MODEL="gpt-5.6-sol" \
ops/run_exploitgym_campaign.sh \
  --run \
  --acknowledge-real-exploit-evaluation \
  I-understand-this-runs-real-exploit-development
```

For the complete profile, set `EXPLOITGYM_TASK_PROFILE=v1`; the wrapper adds
the mandatory `--full-suite` switch. Existing upstream results can be imported
without starting a model or container:

```bash
EXPLOITGYM_MODEL="gpt-5.6-sol" \
ops/run_exploitgym_campaign.sh --import-only
```

The importer summarizes only upstream `result.json`, `scorer_result.json`, and
usage metadata. It does not copy agent transcripts, exploits, flags, secrets,
or target artifacts into this repository's evidence. The cybersecurity HTML
report displays ExploitGym in its own table, with coverage, flags captured,
scorer-confirmed on-target results, runtime, and kernel/V8/userspace splits.
