#!/usr/bin/env python3
"""Offline, exact-command GitHub CLI simulator. Never shells out or connects online."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCENARIO = ROOT / "scenario.json"
TRANSCRIPT = ROOT / "transcript.jsonl"
STATE = ROOT / ".github-lab-state.json"


def main(argv: list[str]) -> int:
    if not SCENARIO.is_file():
        print("scenario.json is missing", file=sys.stderr)
        return 2
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    if not argv or argv[0] == "help":
        print("run '<gh command>' | context")
        print("All commands are exact, simulated, and offline. No host command is executed.")
        return 0
    if argv == ["context"]:
        print(scenario.get("context", "No additional context."))
        return 0
    if len(argv) < 2 or argv[0] != "run":
        print("invalid simulator invocation", file=sys.stderr)
        return 2
    command = " ".join(argv[1:]).strip()
    output = scenario["commands"].get(command)
    ok = output is not None
    steps = scenario.get("steps", [])
    progress = json.loads(STATE.read_text(encoding="utf-8"))["progress"] if STATE.is_file() else 0
    if steps and (progress >= len(steps) or command != steps[progress]):
        output = "SIMULATED ERROR: workflow precondition or ordering not satisfied"
        ok = False
    if not ok:
        output = output or "SIMULATED ERROR: command is not available in this fixture"
    elif steps:
        STATE.write_text(json.dumps({"progress": progress + 1}) + "\n", encoding="utf-8")
    event = {
        "time": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "ok": ok,
        "output": output,
    }
    with TRANSCRIPT.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
