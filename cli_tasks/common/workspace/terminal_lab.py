#!/usr/bin/env python3
"""Deterministic, non-executing command and terminal-menu simulator."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCENARIO = ROOT / "scenario.json"
TRANSCRIPT = ROOT / "transcript.jsonl"


def load() -> dict:
    if not SCENARIO.is_file():
        raise SystemExit("scenario.json is missing")
    return json.loads(SCENARIO.read_text(encoding="utf-8"))


def record(kind: str, value: str, ok: bool, output: str) -> None:
    event = {
        "time": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "value": value,
        "ok": ok,
        "output": output,
    }
    with TRANSCRIPT.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")


def main(argv: list[str]) -> int:
    scenario = load()
    if not argv or argv[0] == "help":
        print("run '<command>' | menu '<choice>><choice>...' | context")
        print("Commands are exact but surrounding whitespace is ignored; nothing runs on the host.")
        return 0
    if argv[0] == "context":
        print(scenario.get("context", "No additional context."))
        return 0
    if len(argv) < 2 or argv[0] not in {"run", "menu"}:
        print("invalid simulator invocation", file=sys.stderr)
        return 2
    value = " ".join(argv[1:]).strip()
    table = scenario["commands"] if argv[0] == "run" else scenario.get("menus", {})
    output = table.get(value)
    ok = output is not None
    if not ok:
        output = "SIMULATED ERROR: command or menu path is not available on this target"
    record(argv[0], value, ok, output)
    print(output)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
