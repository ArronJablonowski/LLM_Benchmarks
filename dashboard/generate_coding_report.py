#!/usr/bin/env python3
"""Generate a standalone HTML report for coding-suite evidence only."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
from collections import defaultdict
from pathlib import Path


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
        key = (row.get("model", "unknown"), row.get("model_runner", "unknown"), row.get("harness", "unknown"))
        grouped[key].append(row)
    summaries = []
    for (model, runner, harness), items in grouped.items():
        passes = sum(row.get("verdict") == "pass" for row in items)
        checks_passed = sum(number(row.get("checks_passed")) for row in items)
        checks_total = sum(number(row.get("checks_total")) for row in items)
        strengths = [row.get("task_id", "unknown") for row in items if row.get("verdict") == "pass"]
        failures = [row.get("task_id", "unknown") for row in items if row.get("verdict") != "pass"]
        summaries.append({
            "model": model, "runner": runner, "harness": harness,
            "runner_version": items[0].get("model_runner_version", ""),
            "harness_version": items[0].get("harness_version", ""),
            "tasks": len(items), "passes": passes,
            "resolved_pct": 100 * passes / len(items) if items else 0,
            "quality_pct": 100 * checks_passed / checks_total if checks_total else 0,
            "avg_seconds": sum(number(row.get("wall_seconds")) for row in items) / len(items) if items else 0,
            "strengths": strengths,
            "failures": failures,
        })
    return sorted(summaries, key=lambda item: (-item["resolved_pct"], -item["quality_pct"], item["avg_seconds"], item["model"], item["runner"], item["harness"]))


def generate(rows: list[dict[str, str]]) -> str:
    summaries = summarize(rows)
    body = []
    for index, item in enumerate(summaries, 1):
        strengths = ", ".join(item["strengths"]) if item["strengths"] else "None resolved"
        failures = ", ".join(item["failures"]) if item["failures"] else "None"
        body.append(
            "<tr>"
            f"<td>{index}</td><td><strong>{html.escape(item['model'])}</strong></td>"
            f"<td>{html.escape(item['runner'])}<small>{html.escape(item['runner_version'])}</small></td>"
            f"<td>{html.escape(item['harness'])}<small>{html.escape(item['harness_version'])}</small></td>"
            f"<td><strong>{item['resolved_pct']:.1f}%</strong><small>{item['passes']}/{item['tasks']} projects</small></td>"
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
table{{width:100%;border-collapse:collapse;min-width:1250px}}th,td{{padding:14px 16px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:12px;letter-spacing:.08em;text-transform:uppercase;background:#f8fafc}}small{{display:block;color:var(--muted);margin-top:2px}}tbody tr:hover{{background:#f6fbfa}}.note{{padding:18px;color:var(--muted)}}
</style></head><body><main><header><h1>Coding Agent Benchmark Report</h1><p>Separate <code>coding</code> suite · project-level agent work · generated {html.escape(generated)}</p></header>
<section><table><thead><tr><th>Rank</th><th>Model</th><th>Model runner</th><th>Agent harness</th><th>Projects resolved</th><th>Quality score</th><th>Avg runtime</th><th>Strengths</th><th>Weaknesses / unresolved</th></tr></thead><tbody>{''.join(body) if body else '<tr><td colspan="9">No coding-suite CSV evidence found.</td></tr>'}</tbody></table>
<p class="note">Project resolution requires every hidden functional and best-practice check to pass. Quality score exposes partial strengths without counting a project as resolved. Standard-suite rows are rejected by this generator.</p></section>
<script type="application/json" id="coding-report-data">{data}</script></main></body></html>"""


def main(argv=None) -> int:
    args = parse_args(argv); rows = load_rows(args.input_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generate(rows), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
