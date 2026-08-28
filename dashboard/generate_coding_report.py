#!/usr/bin/env python3
"""Generate a standalone HTML report for coding-suite evidence only."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark_tests import CODING_TASK_ORDER, suite_task_catalog

EXPECTED_TASKS = set(CODING_TASK_ORDER)
EXPECTED_WEB_TASKS = {
    task["id"] for task in suite_task_catalog("coding")
    if task["category"].startswith("web_")
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def load_rows(roots: list[Path]) -> list[dict[str, str]]:
    rows = []
    for root in roots:
        for path in sorted(root.rglob("*_coding.csv")):
            with path.open(newline="", encoding="utf-8") as stream:
                for row in csv.DictReader(stream):
                    if row.get("benchmark_suite") != "coding":
                        continue
                    row["_source"] = str(path)
                    rows.append(row)
    return rows


def number(value, default=0.0):
    try: return float(value)
    except (TypeError, ValueError): return default


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        key = (
            row.get("benchmark_profile", "unversioned"), row.get("model", "unknown"),
            row.get("model_runner", "unknown"), row.get("harness", "unknown"),
        )
        grouped[key].append(row)
    summaries = []
    for (profile, model, runner, harness), raw_items in grouped.items():
        by_task = {row.get("task_id", "unknown"): row for row in raw_items}
        items = list(by_task.values())
        passes = sum(row.get("verdict") == "pass" for row in items)
        web_items = [row for row in items if row.get("category", "").startswith("web_")]
        web_passes = sum(row.get("verdict") == "pass" for row in web_items)
        checks_passed = sum(number(row.get("checks_passed")) for row in items)
        checks_total = sum(number(row.get("checks_total")) for row in items)
        strengths = [row.get("task_id", "unknown") for row in items if row.get("verdict") == "pass"]
        failures = [row.get("task_id", "unknown") for row in items if row.get("verdict") != "pass"]
        summaries.append({
            "profile": profile, "model": model, "runner": runner, "harness": harness,
            "runner_version": items[0].get("model_runner_version", ""),
            "harness_version": items[0].get("harness_version", ""),
            "tasks": len(items), "passes": passes,
            "expected_tasks": len(EXPECTED_TASKS),
            "coverage_pct": 100 * len(set(by_task) & EXPECTED_TASKS) / len(EXPECTED_TASKS),
            "resolved_pct": 100 * passes / len(EXPECTED_TASKS),
            "web_tasks": len(web_items), "web_passes": web_passes,
            "expected_web_tasks": len(EXPECTED_WEB_TASKS),
            "web_resolved_pct": 100 * web_passes / len(EXPECTED_WEB_TASKS),
            "quality_pct": 100 * checks_passed / checks_total if checks_total else 0,
            "avg_seconds": sum(number(row.get("wall_seconds")) for row in items) / len(items) if items else 0,
            "strengths": strengths,
            "failures": failures,
        })
    return sorted(summaries, key=lambda item: (-item["coverage_pct"], -item["resolved_pct"], -item["quality_pct"], item["avg_seconds"], item["model"], item["runner"], item["harness"]))


def generate(rows: list[dict[str, str]]) -> str:
    summaries = summarize(rows)
    body = []
    for index, item in enumerate(summaries, 1):
        strengths = ", ".join(item["strengths"]) if item["strengths"] else "None resolved"
        failures = ", ".join(item["failures"]) if item["failures"] else "None"
        web_score = f"{item['web_resolved_pct']:.1f}%<small>{item['web_passes']}/{item['expected_web_tasks']} web projects</small>"
        body.append(
            "<tr>"
            f"<td>{index}</td><td><strong>{html.escape(item['model'])}</strong></td>"
            f"<td>{html.escape(item['profile'])}</td>"
            f"<td>{html.escape(item['runner'])}<small>{html.escape(item['runner_version'])}</small></td>"
            f"<td>{html.escape(item['harness'])}<small>{html.escape(item['harness_version'])}</small></td>"
            f"<td><strong>{item['coverage_pct']:.1f}%</strong><small>{item['tasks']}/{item['expected_tasks']} projects observed</small></td>"
            f"<td><strong>{item['resolved_pct']:.1f}%</strong><small>{item['passes']}/{item['expected_tasks']} projects</small></td>"
            f"<td><strong>{web_score}</strong></td>"
            f"<td><strong>{item['quality_pct']:.1f}%</strong><small>hidden quality checks</small></td>"
            f"<td>{item['avg_seconds']:.1f}s</td><td>{html.escape(strengths)}</td>"
            f"<td>{html.escape(failures)}</td></tr>"
        )
    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    data = json.dumps(summaries, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coding Agent Benchmark Report</title>
<style>
:root{{--bg:#f3f6fb;--card:#fff;--ink:#172033;--muted:#62708a;--line:#dce3ee;--accent:#087f67}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,sans-serif}}
main{{max-width:1500px;margin:28px auto;padding:0 20px}}header{{background:linear-gradient(135deg,#15213a,#174b55);color:white;padding:30px;border-radius:18px}}
h1{{margin:0 0 8px;font-size:32px}}header p{{margin:0;color:#d8e7eb}}section{{margin-top:18px;background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:auto}}
table{{width:100%;border-collapse:collapse;min-width:1450px}}th,td{{padding:14px 16px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:12px;letter-spacing:.08em;text-transform:uppercase;background:#f8fafc}}small{{display:block;color:var(--muted);margin-top:2px}}tbody tr:hover{{background:#f6fbfa}}.note{{padding:18px;color:var(--muted)}}
</style></head><body><main><header><h1>Coding Agent Benchmark Report</h1><p>Separate <code>coding</code> suite · project-level agent work · generated {html.escape(generated)}</p></header>
<section><table><thead><tr><th>Rank</th><th>Model</th><th>Profile</th><th>Model runner</th><th>Agent harness</th><th>Coverage</th><th>Projects resolved</th><th>Web projects</th><th>Quality score</th><th>Avg runtime</th><th>Strengths</th><th>Weaknesses / unresolved</th></tr></thead><tbody>{''.join(body) if body else '<tr><td colspan="12">No coding-suite CSV evidence found.</td></tr>'}</tbody></table>
<p class="note">Project resolution uses the complete current task denominator and requires every hidden functional and best-practice check to pass. Coverage prevents partial or legacy profiles from appearing equivalent to a complete campaign. Quality score exposes partial strengths without counting a project as resolved. Standard-suite rows are rejected by this generator.</p></section>
<script type="application/json" id="coding-report-data">{data}</script></main></body></html>"""


def main(argv=None) -> int:
    args = parse_args(argv); rows = load_rows(args.input_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generate(rows), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
