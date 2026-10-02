# Command Line agent suite

The `commandline` suite measures whether a tool-capable LLM can select, order,
and interpret command-line operations across operating systems and terminal
appliances. It is isolated from Standard, Coding, Creative, and Cybersecurity
scores. The default profile is `commandline-agent-v2-standard-20`; the explicit
`--full-suite` profile is `commandline-agent-v2-full-120`.

## Coverage and progression

The standard profile contains 20 hand-authored cornerstone tasks ordered from
easy to expert. Passing `--full-suite` selects all 120 tasks, also ordered
globally from easy to expert:

| Level | Coverage |
|---|---|
| Easy | Linux Bash diagnostics, macOS `zsh`/`diskutil`/`pmset`, Windows CMD, PowerShell services, CIM/WMI inventory |
| Medium | SSH jump-host triage, Ubuntu/systemd, RHEL/firewalld, Alpine/OpenRC, four-level custom terminal menus |
| Hard | Mock pfSense interfaces/NAT/IPsec, mock OpenWrt network/fw4, Linux/macOS/Windows live incident response |
| Expert | Cross-platform lateral-movement investigation and a multi-firewall pfSense/OpenWrt outage |

Twenty scenarios are individually hand-authored cornerstone labs. A reviewed,
deterministic catalog generator expands them with 100 additional tasks across
ten tracks, ten levels per track: Linux, macOS, CMD.exe, PowerShell/WMI,
SSH/network operations, Linux distributions, nested terminal menus, pfSense,
OpenWrt, and cross-platform incident response. Generated descriptors are
checked into the repository and validated exactly like hand-authored tasks.

Tasks evaluate command syntax, evidence interpretation, ordered workflows,
menu state tracking, least-privilege remediation, and avoidance of destructive
shortcuts. Incident-response labs require volatility-first collection and
evidence preservation before containment.

## Safe appliance simulation

No submitted command is executed on the benchmark host. Each fresh workspace
contains `terminal_lab.py`, which maps exact commands and complete menu paths to
deterministic mock output and records an auditable transcript. The mock pfSense
and OpenWrt tasks reproduce relevant console concepts, command surfaces, and
multi-level navigation without copying proprietary UI text or requiring a live
firewall. They are compatibility simulations, not official vendor benchmarks.

The agent must finish with `answer.json`, containing its ordered commands,
evidence-backed findings, safe actions, and menu path. A hidden grader verifies
both that the required operations were actually exercised in the simulator and
that the submitted conclusions contain the decisive evidence.

## Run it

List or preview tasks without starting a model:

```bash
python3 scripts/commandline_agent_benchmarks.py \
  --harness pi \
  --models-file models.tsv --output-dir reports/cli \
  --workspace work --list-tasks

# Explicitly list all 120 tasks.
python3 scripts/commandline_agent_benchmarks.py \
  --harness pi --models-file models.tsv --output-dir reports/cli \
  --workspace work --full-suite --list-tasks

python3 scripts/commandline_agent_benchmarks.py \
  --harness pi \
  --models-file models.tsv --output-dir reports/cli \
  --workspace work --tasks cli_linux_basics
```

Execute a reviewed campaign:

```bash
BENCH_CAMPAIGN_DIR="$HOME/.hermes/reports/campaigns/commandline-v1" \
BENCH_MODELS_FILE="$HOME/.hermes/reports/campaigns/commandline-v1/models.tsv" \
BENCH_CLI_HARNESSES="pi goose openhands" \
ops/run_commandline_agent_campaign.sh
```

Set `BENCH_FULL_SUITE=1` for the 120-task profile. Without it, campaign
wrappers and direct runner invocations use the standard 20-task profile.

The runner is plan-only unless `--run` is supplied. Every model-task workspace
is fresh, interrupted work is preserved under a recovery name, frozen model
digests are verified, and the existing memory, swap, OOM, temperature, and
model-residency guards remain active.

The same profile can run through the direct workspace agent, Hermes, and
OpenClaw with `BENCH_PROJECT_SUITES="commandline"` and
`ops/run_ollama_project_three_path_campaign.sh`.

## DarwinRouter campaign recovery

`darwinrouter_commandline_campaign.py --campaign-dir PATH` resumes the preserved
manifest and result files. Each model/task case receives at most two attempts;
a retry uses `retries/MODEL/TASK/attempt-2` and its own workspace. Original JSONL
rows and transcripts remain unchanged. Infrastructure failures receive no
quality feedback. An exhausted case is recorded for final investigation and
independent cases continue; three consecutive exhausted cases stop the run.
Grader errors, ambiguous interrupted launches, ownership conflicts, changed
host/config hashes, and feedback mismatches stop before another inference.
Missing feedback is repaired and independently read back without rerunning a
valid task. Counts distinguish completed cases, valid grades, total attempts,
and infrastructure attempts.

A reviewed provider incident can hold one model through `manifest.model_holds`
while independent direct models continue. Each hold names its reason, explicit
resume condition, campaign-local audit file and that file's SHA256; the audit
must identify the same model with status `needs_provider_investigation`.
The supervisor validates all holds before dispatch, verifies existing completed
results even in held phases, and records pending cases in `model-deferrals.jsonl`.
A hold creates no workspace, attempt, grade or exclusion. Automatic routing
waits until these direct cases are settled. The final status is
`waiting_model_recovery` while a hold leaves pending work. Release the hold only
after reviewing recovery evidence; resumption preserves completed cases and
remaining retry slots. Exhausted cases still require separate hash-bound
infrastructure dispositions and never receive a third attempt.

The SDK simulator exposes native `kind=help` and explains that unsupported
commands are unavailable in the fixed simulation. Recovery guidance contains
no findings or command output. Multi-step menu paths are saved as audited
individual steps for the existing grader; the original full path remains in
the transcript. The 32-turn and per-attempt wall-time limits remain unchanged.
These adapter changes must be recorded as a new harness revision when comparing
results with earlier runs.

The common CLI grader accepts any order for independent Linux/macOS/Windows
baseline inventory queries, while preserving order requirements for recovery,
incident-response and menu workflows. A WMI inventory task does not require
inventing a remediation action. Missing observations, unexecuted commands and
destructive actions still fail. Reviewed regrades are appended separately to
`grader-revisions.jsonl`, bound to the hash of the original canonical record;
this never promotes a host/infrastructure failure into a quality grade. Durable
feedback corrections use DarwinRouter's expected-ID revision command so the
previous judgment remains in history.

Diagnostic grading also accepts explicit task-scoped equivalents such as a
firewalld service being active, a PostgreSQL service name without `.service`,
zero states described as "no states", and "LAN zone" versus "zone LAN". Finding
matches start at a token boundary so "20 states" does not satisfy "0 states".
Read-only diagnostic commands may be reordered; service recovery and the four
incident-response workflows retain their sequencing requirements. Diagnostic-
only tasks (Windows inventory, SSH triage, console navigation, NAT inspection)
may report no remediation, while tasks requesting a recovery plan still require
one. Menu navigation is audited independently; its labels need not be repeated
as findings. Missing or wrong observations remain failures. These are common
grader corrections, not model improvements; regrade preserved work and revise
feedback through the audited expected-ID mechanism.

Equivalent reported observations also cover a battery service recommendation,
the simulator's successful DNS answer, the specific HTTPS NAT target with a
missing WAN pass rule, the rejected guest-service flow, and an encoded PowerShell
command described in prose. Compound equivalents require all relevant facts;
wrong destinations, ports, addresses and missing execution evidence still fail.
These additions do not waive missing recovery outcomes or persistence settings.

The PowerShell service fixture now declares a simulated command effect: a
successful Spooler restart changes subsequent status queries to Running. The
standalone CLI replays successful effects from its workspace transcript; the
DarwinRouter adapter keeps isolated state after appending the operation audit.
Failed operations do not apply effects, repeated restarts are idempotent, and a
new workspace retains the original Stopped state. Record this fixture/host
revision separately from older runs; preserve any contradictory old transcripts.

If a completed grade is invalidated by a fixture defect, append a hash-bound
`evidence-invalidations.jsonl` classification rather than modifying the original
row. Such a result becomes infrastructure evidence, never a pass. Hold the
campaign through `manifest.maintenance_hold` until any previously written quality
feedback is withdrawn through an audited expected-head correction. An invalidated
attempt still consumes its original retry slot; this does not authorize a third
attempt or removal of the original execution history.

For an explicitly reviewed stream investigation, `scripts/ollama_wire_diagnostic.py`
can relay a single configured Ollama model through a temporary loopback endpoint.
It issues no inference itself, leaves request/response bodies unchanged, forwards
only to local port 11434, and records at most 17 MiB of each chat response with
exclusive files and byte/hash metadata. It does not log request bodies or headers.
Use it only with synthetic benchmark data in an isolated attempt, a separate
configuration copy, recorded hashes, no other active inference, and the normal
attempt/deadline limits. A diagnostic consumes the scheduled case's attempt; it
is not permission to repeat exhausted work. Preserve wire evidence privately and
stop the relay after the attempt. Normal campaign configuration stays unchanged.
