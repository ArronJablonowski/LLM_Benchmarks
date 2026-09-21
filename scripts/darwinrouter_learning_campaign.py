#!/usr/bin/env python3
"""Evaluate local DarwinRouter models independently, then validate auto routing."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True, help="Configured model pairs as DarwinRouter-ID=Ollama-model")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:7788")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--tasks", nargs="*")
    parser.add_argument("--skip-auto-validation", action="store_true")
    args = parser.parse_args()

    runner = Path(__file__).with_name("darwinrouter_standard_benchmarks.py")
    pairs = []
    for value in args.models:
        model, separator, ollama = value.partition("=")
        if not separator or not model or not ollama or model == "auto":
            raise SystemExit("--models entries must use DarwinRouter-ID=Ollama-model")
        pairs.append((model, ollama))
    if len({model for model, _ in pairs}) != len(pairs):
        raise SystemExit("--models contains duplicate DarwinRouter IDs")
    campaign = pairs if args.skip_auto_validation else pairs + [("auto", "")]
    for index, (model, ollama) in enumerate(campaign, 1):
        phase = "held-out-auto" if model == "auto" else "local-model"
        print(f"Campaign [{index}/{len(campaign)}] {phase}: {model}", flush=True)
        command = [
            sys.executable, str(runner), "--run", "--base-url", args.base_url,
            "--model", model, "--timeout", str(args.timeout),
            "--output-dir", str(args.output_dir / model),
        ]
        if args.tasks is not None:
            command.extend(["--tasks", *args.tasks])
        # Clear every campaign model before every task. Ollama can retain the
        # previous model after a route completes; unloading only the incoming
        # model makes host admission correctly reject the combined footprint.
        for unload in [name for _, name in pairs]:
            command.extend(["--ollama-model", unload])
        completed = subprocess.run(command, check=False)
        if completed.returncode:
            print(f"Campaign paused after {model} exited {completed.returncode}; rerun resumes completed evidence.", file=sys.stderr)
            return completed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
