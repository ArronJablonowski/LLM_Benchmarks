from __future__ import annotations

import argparse
import json
import sys

from .optimizer import optimize


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="JSON input path; defaults to stdin")
    args = parser.parse_args()
    stream = open(args.input, encoding="utf-8") if args.input else sys.stdin
    try:
        result = optimize(json.load(stream))
    finally:
        if args.input:
            stream.close()
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
