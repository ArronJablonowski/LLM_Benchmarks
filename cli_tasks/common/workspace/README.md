# Offline command-line lab

This workspace is a deterministic simulation. It never executes the submitted
operating-system, SSH, firewall, or router command on the host.

Use `python3 terminal_lab.py help` for the interface. Probe the simulated system
with `python3 terminal_lab.py run '<command>'`, and navigate menu appliances with
`python3 terminal_lab.py menu '<choice>><choice>...'`. Every interaction is
recorded in `transcript.jsonl`.

Finish by creating `answer.json`:

```json
{
  "commands": ["commands in the order used"],
  "findings": ["concise evidence-backed findings"],
  "actions": ["safe remediation or administration actions"],
  "menu_path": ["menu choices in order, when applicable"]
}
```

Do not run equivalent commands against the real benchmark host.
