#!/usr/bin/env python3
"""Generate a standalone HTML report for cybersecurity-suite evidence only."""
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
from benchmark_tests import CYBERSECURITY_TASK_ORDER, suite_task_catalog  # noqa: E402


EXPECTED_TASKS = set(CYBERSECURITY_TASK_ORDER)
TASKS = {task["id"]: task for task in suite_task_catalog("cybersecurity")}
EXPECTED_BY_TRACK = defaultdict(set)
for task in TASKS.values(): EXPECTED_BY_TRACK[task["track"]].add(task["id"])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def load_rows(roots: list[Path]) -> list[dict[str, str]]:
    rows = []
    for root in roots:
        for path in sorted(root.rglob("*_cybersecurity.csv")):
            with path.open(newline="", encoding="utf-8") as stream:
                for row in csv.DictReader(stream):
                    if row.get("benchmark_suite") != "cybersecurity": continue
                    row["_source"] = str(path); rows.append(row)
    return rows


def number(value, default=0.0):
    try: return float(value)
    except (TypeError, ValueError): return default


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        key = (row.get("benchmark_profile", "unversioned"), row.get("model", "unknown"), row.get("model_runner", "unknown"), row.get("harness", "unknown"))
        grouped[key].append(row)
    summaries = []
    for (profile, model, runner, harness), raw_items in grouped.items():
        by_task = {
            row.get("task_id", "unknown"): row for row in raw_items
            if row.get("task_id") in EXPECTED_TASKS
        }
        items = list(by_task.values())
        if not items:
            continue
        passes = sum(row.get("verdict") == "pass" for row in items)
        checks_passed = sum(number(row.get("checks_passed")) for row in items)
        checks_total = sum(number(row.get("checks_total")) for row in items)
        tracks = {}
        for track, expected in EXPECTED_BY_TRACK.items():
            observed = [row for row in items if row.get("task_id") in expected]
            track_passes = sum(row.get("verdict") == "pass" for row in observed)
            track_checks = sum(number(row.get("checks_passed")) for row in observed)
            track_total = sum(number(row.get("checks_total")) for row in observed)
            tracks[track] = {"observed": len(observed), "expected": len(expected), "passes": track_passes, "quality": 100 * track_checks / track_total if track_total else 0}
        summaries.append({
            "profile": profile, "model": model, "runner": runner, "harness": harness,
            "runner_version": items[0].get("model_runner_version", ""), "harness_version": items[0].get("harness_version", ""),
            "tasks": len(items), "passes": passes, "expected_tasks": len(EXPECTED_TASKS),
            "coverage_pct": 100 * len(set(by_task) & EXPECTED_TASKS) / len(EXPECTED_TASKS),
            "resolved_pct": 100 * passes / len(EXPECTED_TASKS),
            "quality_pct": 100 * checks_passed / checks_total if checks_total else 0,
            "avg_seconds": sum(number(row.get("wall_seconds")) for row in items) / len(items) if items else 0,
            "tracks": tracks,
            "strengths": [row.get("task_id", "unknown") for row in items if row.get("verdict") == "pass"],
            "failures": [row.get("task_id", "unknown") for row in items if row.get("verdict") != "pass"],
        })
    return sorted(summaries, key=lambda item: (-item["coverage_pct"], -item["resolved_pct"], -item["quality_pct"], item["avg_seconds"], item["model"], item["runner"], item["harness"]))


def summarize_exploitgym(rows):
    grouped = defaultdict(list)
    for row in rows:
        if not row.get("benchmark_profile", "").startswith("exploitgym-"):
            continue
        key = (
            row.get("benchmark_profile", "unversioned"), row.get("model", "unknown"),
            row.get("model_runner", "unknown"), row.get("harness", "unknown"),
        )
        grouped[key].append(row)
    summaries = []
    for (profile, model, runner, harness), raw_items in grouped.items():
        by_task = {row.get("task_id", "unknown"): row for row in raw_items}
        items = list(by_task.values())
        if not items:
            continue
        expected = max(
            len(items),
            max(int(number(row.get("profile_task_count"), len(items))) for row in items),
        )
        flags = sum(row.get("flag_captured") == "true" for row in items)
        on_target = sum(row.get("on_target") == "true" for row in items)
        judged = sum(row.get("on_target") in {"true", "false"} for row in items)
        families = {}
        for family in ("kernel", "v8", "user"):
            family_rows = [row for row in items if row.get("task_family") == family]
            if family_rows:
                families[family] = {
                    "observed": len(family_rows),
                    "flags": sum(row.get("flag_captured") == "true" for row in family_rows),
                }
        summaries.append({
            "profile": profile, "model": model, "runner": runner, "harness": harness,
            "tasks": len(items), "expected_tasks": expected,
            "coverage_pct": 100 * len(items) / expected if expected else 0,
            "flags": flags, "flag_pct": 100 * flags / expected if expected else 0,
            "on_target": on_target, "judged": judged,
            "avg_seconds": sum(number(row.get("wall_seconds")) for row in items) / len(items),
            "families": families,
        })
    return sorted(
        summaries,
        key=lambda item: (
            -item["coverage_pct"], -item["flag_pct"], -item["on_target"],
            item["avg_seconds"], item["model"], item["harness"],
        ),
    )


def generate(rows: list[dict[str, str]]) -> str:
    summaries = summarize(rows); exploitgym = summarize_exploitgym(rows); body = []
    for index, item in enumerate(summaries, 1):
        track_cells = "".join(
            f"<span><b>{html.escape(track)}</b> {values['passes']}/{values['expected']} · {values['quality']:.0f}% checks</span>"
            for track, values in item["tracks"].items()
        )
        body.append(
            "<tr>" f"<td>{index}</td><td><strong>{html.escape(item['model'])}</strong></td>"
            f"<td>{html.escape(item['profile'])}</td>"
            f"<td>{html.escape(item['runner'])}<small>{html.escape(item['runner_version'])}</small></td>"
            f"<td>{html.escape(item['harness'])}<small>{html.escape(item['harness_version'])}</small></td>"
            f"<td><strong>{item['coverage_pct']:.1f}%</strong><small>{item['tasks']}/{item['expected_tasks']} tasks</small></td>"
            f"<td><strong>{item['resolved_pct']:.1f}%</strong><small>{item['passes']}/{item['expected_tasks']} strict passes</small></td>"
            f"<td><strong>{item['quality_pct']:.1f}%</strong><small>deterministic checks</small></td>"
            f"<td>{item['avg_seconds']:.1f}s</td><td><div class=tracks>{track_cells}</div></td>"
            f"<td>{html.escape(', '.join(item['failures']) if item['failures'] else 'None')}</td></tr>"
        )
    exploitgym_body = []
    for index, item in enumerate(exploitgym, 1):
        family_cells = "".join(
            f"<span><b>{html.escape(family)}</b> {values['flags']}/{values['observed']} flags</span>"
            for family, values in item["families"].items()
        )
        exploitgym_body.append(
            "<tr>" f"<td>{index}</td><td><strong>{html.escape(item['model'])}</strong></td>"
            f"<td>{html.escape(item['profile'])}</td><td>{html.escape(item['runner'])}</td>"
            f"<td>{html.escape(item['harness'])}</td>"
            f"<td><strong>{item['coverage_pct']:.1f}%</strong><small>{item['tasks']}/{item['expected_tasks']} tasks</small></td>"
            f"<td><strong>{item['flag_pct']:.1f}%</strong><small>{item['flags']}/{item['expected_tasks']} flags</small></td>"
            f"<td>{item['on_target']}/{item['judged']}<small>scorer-confirmed</small></td>"
            f"<td>{item['avg_seconds']:.1f}s</td><td><div class=tracks>{family_cells}</div></td></tr>"
        )
    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    data = json.dumps({"local": summaries, "exploitgym": exploitgym}, separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cybersecurity Agent Benchmark Report</title><style>
:root{{--bg:#071018;--card:#101d27;--ink:#eef7f5;--muted:#9eb4b8;--line:#263945;--accent:#55e6b1;--warn:#ffbd66}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at top right,#143542,var(--bg) 45%);color:var(--ink);font:14px/1.5 system-ui,-apple-system,sans-serif}}main{{max-width:1700px;margin:28px auto;padding:0 20px}}header{{padding:32px;border:1px solid var(--line);border-radius:18px;background:#0c1821dd}}h1{{margin:0 0 8px;font-size:34px}}header p,.note,small{{color:var(--muted)}}section{{margin-top:18px;border:1px solid var(--line);border-radius:16px;overflow:auto;background:var(--card)}}table{{width:100%;border-collapse:collapse;min-width:1580px}}th,td{{padding:14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--accent);font-size:11px;letter-spacing:.08em;text-transform:uppercase;background:#0b1720}}small{{display:block}}.tracks{{display:grid;gap:5px;min-width:300px}}.tracks span{{display:block;color:var(--muted)}}.tracks b{{color:var(--ink)}}.note{{padding:18px}}code{{color:var(--accent)}}
</style></head><body><main><header><h1>Cybersecurity Agent Benchmark Report</h1><p>Separate <code>cybersecurity</code> suite · original local profile plus isolated published profiles · generated {html.escape(generated)}</p></header><section><h2>Original 24-task local profile</h2><table><thead><tr><th>Rank</th><th>Model</th><th>Profile</th><th>Model runner</th><th>Agent harness</th><th>Coverage</th><th>Tasks resolved</th><th>Quality score</th><th>Avg runtime</th><th>Track results</th><th>Unresolved</th></tr></thead><tbody>{''.join(body) if body else '<tr><td colspan="11">No local cybersecurity-profile evidence found.</td></tr>'}</tbody></table><p class="note">Strict resolution requires every deterministic task check to pass. Coverage uses the complete 24-task denominator, preventing partial campaigns from ranking like complete runs. These local scores are not official MITRE, NIST, OWASP, CyberSecEval, Cybench, or other upstream leaderboard results.</p></section><section><h2>ExploitGym external profile</h2><table><thead><tr><th>Rank</th><th>Model</th><th>Profile</th><th>Model runner</th><th>Agent</th><th>Coverage</th><th>Flags captured</th><th>On-target</th><th>Avg runtime</th><th>Family results</th></tr></thead><tbody>{''.join(exploitgym_body) if exploitgym_body else '<tr><td colspan="10">No ExploitGym evidence found.</td></tr>'}</tbody></table><p class="note">ExploitGym remains a pinned external benchmark. Flag capture and scorer-confirmed on-target exploitation are reported separately, and its scores are never merged with the original local profile.</p></section><script type="application/json" id="cybersecurity-report-data">{data}</script></main></body></html>"""


def main(argv=None) -> int:
    args = parse_args(argv); rows = load_rows(args.input_root)
    args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(generate(rows), encoding="utf-8")
    print(args.output); return 0


if __name__ == "__main__": raise SystemExit(main())
