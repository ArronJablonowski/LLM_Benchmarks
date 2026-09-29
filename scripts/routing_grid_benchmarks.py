#!/usr/bin/env python3
"""List/export progressive routing-card tasks or assess an existing submission.

Offline only: never launches models, executes external APIs or writes feedback.
Use an explicitly modality-capable campaign adapter for future inference.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from routing_grid_suite import CATEGORIES, assess, load_catalog, model_payload, strict_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", choices=CATEGORIES)
    parser.add_argument("--task")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--list-tasks", action="store_true")
    modes.add_argument(
        "--export", type=Path, help="Create fresh model-visible task directories without oracles"
    )
    modes.add_argument("--response", type=Path, help="Assess a saved response; requires --task")
    parser.add_argument(
        "--workspace", type=Path, help="Generated artifact directory for media tasks"
    )
    parser.add_argument("--review", type=Path, help="Evaluator-supplied hash-bound human review")
    parser.add_argument(
        "--status",
        default="completed",
        choices=[
            "completed",
            "disqualified",
            "timeout",
            "provider_error",
            "context_overflow",
            "admission_error",
            "stream_error",
        ],
    )
    args = parser.parse_args(argv)
    tasks = load_catalog()
    if args.category:
        tasks = [t for t in tasks if t["category"] == args.category]
    if args.task:
        tasks = [t for t in tasks if t["id"] == args.task]
        if not tasks:
            parser.error("unknown task in selected category")
    if args.response:
        if len(tasks) != 1 or not args.task:
            parser.error("--response requires one explicit --task")
        if args.response.stat().st_size > 65536:
            parser.error("response exceeds 64 KiB")
        review = strict_json(args.review.read_text()) if args.review else None
        result = assess(
            tasks[0],
            args.response.read_text(),
            status=args.status,
            workspace=args.workspace,
            review=review,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["verdict"] in {"pass", "needs_review"} else 1
    if args.review or args.workspace or args.status != "completed":
        parser.error("--review/--workspace/--status require --response")
    if args.export:
        if args.export.exists():
            parser.error("export destination must not already exist")
        args.export.mkdir(parents=True)
        for task in tasks:
            directory = args.export / task["id"]
            directory.mkdir()
            (directory / "task.json").write_text(
                json.dumps(model_payload(task), ensure_ascii=False, indent=2) + "\n"
            )
            for asset in task["assets"]:
                target = directory / asset["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(task["_root"]) / asset["path"], target)
        print(
            f"Exported {len(tasks)} model-visible tasks to {args.export}; evaluator oracles excluded."
        )
    else:
        for task in tasks:
            print(
                f"{task['id']}\tL{task['level']}\t{task['name']}\t{task['domain']}/{task['evidence_profile']}\t{task['_oracle']['kind']}"
            )
        if not args.list_tasks:
            print("Offline plan only. No models dispatched; no routing feedback written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
