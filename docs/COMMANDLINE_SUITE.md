# Command Line agent suite

The `commandline` suite measures whether a tool-capable LLM can select, order,
and interpret command-line operations across operating systems and terminal
appliances. It is isolated from Standard, Coding, Creative, and Cybersecurity
scores and uses the versioned profile `commandline-agent-v1`.

## Coverage and progression

The initial profile contains 20 original tasks ordered from easy to expert:

| Level | Coverage |
|---|---|
| Easy | Linux Bash diagnostics, macOS `zsh`/`diskutil`/`pmset`, Windows CMD, PowerShell services, CIM/WMI inventory |
| Medium | SSH jump-host triage, Ubuntu/systemd, RHEL/firewalld, Alpine/OpenRC, four-level custom terminal menus |
| Hard | Mock pfSense interfaces/NAT/IPsec, mock OpenWrt network/fw4, Linux/macOS/Windows live incident response |
| Expert | Cross-platform lateral-movement investigation and a multi-firewall pfSense/OpenWrt outage |

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
python3 scripts/cli_agent_benchmarks.py \
  --suite commandline --harness pi \
  --models-file models.tsv --output-dir reports/cli \
  --workspace work --list-tasks

python3 scripts/cli_agent_benchmarks.py \
  --suite commandline --harness pi \
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

The runner is plan-only unless `--run` is supplied. Every model-task workspace
is fresh, interrupted work is preserved under a recovery name, frozen model
digests are verified, and the existing memory, swap, OOM, temperature, and
model-residency guards remain active.

The same profile can run through the direct workspace agent, Hermes, and
OpenClaw with `BENCH_PROJECT_SUITES="commandline"` and
`ops/run_ollama_project_three_path_campaign.sh`.
