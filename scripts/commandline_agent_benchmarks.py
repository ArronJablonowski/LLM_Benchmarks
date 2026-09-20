#!/usr/bin/env python3
"""Run the isolated Command Line suite through a tool-capable agent harness."""
from __future__ import annotations

import sys

from coding_agent_benchmarks import main


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--suite" not in argv:
        argv = ["--suite", "commandline", *argv]
    raise SystemExit(main(argv))
