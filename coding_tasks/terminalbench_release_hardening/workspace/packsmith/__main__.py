import argparse
import json

from .archive import build_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    print(json.dumps(build_archive(args.source, args.output, args.verify, args.dry_run), sort_keys=True))


if __name__ == "__main__":
    main()
