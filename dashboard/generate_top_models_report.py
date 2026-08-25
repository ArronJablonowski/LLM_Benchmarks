#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build the self-contained cross-system benchmark ranking report."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "data" / "top_models_report",
        help="Directory containing the Spark, Studio, and Mini consolidated CSV exports.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "top_10_models_by_system.html",
        help="Destination for the generated self-contained HTML report.",
    )
    return parser.parse_args()


ARGS = parse_args()
DATA_DIR = ARGS.data_dir.expanduser().resolve()
OUTPUT = ARGS.output.expanduser().resolve()

HOSTS = {
    "spark": {
        "name": "NVIDIA DGX Spark",
        "subtitle": "Ubuntu · GB10 unified memory",
        "paths": ["ollama_direct", "hermes_agent", "openclaw"],
        "minimum_tasks": {"ollama_direct": 18, "hermes_agent": 18, "openclaw": 18},
        "source_note": "Final 2026-08-22 standard campaign plus OCR follow-up",
    },
    "studio": {
        "name": "Mac Studio",
        "subtitle": "macOS · Apple silicon",
        "paths": ["ollama_direct", "hermes_agent", "openclaw"],
        "minimum_tasks": {"ollama_direct": 18, "hermes_agent": 18, "openclaw": 18},
        "source_note": "Final 2026-08-23 Studio campaign, including cloud and local recovery evidence",
    },
    "mini": {
        "name": "Mac Mini",
        "subtitle": "macOS · Apple silicon",
        "paths": ["ollama_direct", "hermes_agent", "openclaw"],
        "minimum_tasks": {"ollama_direct": 18, "hermes_agent": 18, "openclaw": 18},
        "source_note": "Final 2026-08-22 standard campaign plus OCR follow-up",
    },
}

PATH_LABELS = {
    "ollama_direct": "Ollama Direct",
    "hermes_agent": "Hermes Agent",
    "openclaw": "OpenClaw",
}

TASK_LABELS = {
    "exact_reply": "Exact Reply",
    "simple_reasoning": "Simple Reasoning",
    "ifeval_exact": "IFEval — Exact Format",
    "ifeval_json": "IFEval — JSON",
    "gsm8k_mini": "GSM8K",
    "math500_mini": "MATH-500",
    "mmlu_pro_security": "MMLU-Pro",
    "arc_challenge_mini": "ARC-Challenge",
    "hellaswag_mini": "HellaSwag",
    "truthfulqa_mini": "TruthfulQA",
    "coding_micro": "Coding Micro",
    "humaneval_mini": "HumanEval+",
    "mbpp_mini": "MBPP+",
    "bfcl_mini": "BFCL",
    "ragas_mini": "RAGAS / RAG",
    "cyber_soc_mini": "CyberSecEval-style",
    "prompt_injection_mini": "Prompt Injection",
    "ocrbench_mini": "OCRBench / TextVQA",
}


def text_value(row, *keys):
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def float_value(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_value(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def treatment_name(row):
    explicit = text_value(row, "treatment_key")
    if explicit:
        return explicit
    thinking = text_value(
        row,
        "thinking_effective",
        "hermes_reasoning_requested",
        "thinking_resolved",
        "thinking_requested",
        "thinking_mode",
    )
    if thinking and thinking not in {"unsupported", "none", "false"}:
        return f"thinking-{thinking}"
    return "default"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path):
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def invalid_observation(row):
    combined = " ".join(
        (row.get(key) or "")
        for key in (
            "error",
            "grader_error",
            "assistant_text_preview",
            "response_preview",
            "termination_reason",
            "protocol_error",
        )
    ).lower()
    return (
        "context overflow: prompt too large" in combined
        or "resource_pressure" in combined
        or "out of memory" in combined
        or "oom" == (row.get("termination_reason") or "").lower()
    )


def candidate_from_rows(host_key, path_key, source, model, treatment, rows):
    by_task = {}
    for row in rows:
        task_id = text_value(row, "task_id", "task_name")
        prior = by_task.get(task_id)
        freshness = (
            text_value(row, "run_id"),
            text_value(row, "export_source_mtime_utc"),
        )
        prior_freshness = (
            text_value(prior or {}, "run_id"),
            text_value(prior or {}, "export_source_mtime_utc"),
        )
        if task_id and (prior is None or freshness >= prior_freshness):
            by_task[task_id] = row
    task_rows = list(by_task.values())
    required = HOSTS[host_key]["minimum_tasks"][path_key]
    if len(task_rows) < required or any(invalid_observation(row) for row in task_rows):
        return None

    scored = task_rows
    passes = sum((row.get("verdict") or "").lower() == "pass" for row in scored)
    failed_tasks = sorted(
        {
            text_value(row, "task_id", "task_name")
            for row in scored
            if (row.get("verdict") or "").lower() != "pass"
        }
    )
    strict_accuracy = passes / len(scored)

    granular_passed = 0
    granular_total = 0
    for row in scored:
        gp = int_value(row.get("grader_tests_passed"))
        gt = int_value(row.get("grader_tests_total"))
        if (row.get("verdict") or "").lower() == "pass" and gp is not None and gt and gt > 0:
            granular_passed += gp
            granular_total += gt
        else:
            granular_passed += int((row.get("verdict") or "").lower() == "pass")
            granular_total += 1

    wall_values = [float_value(row.get("wall_seconds")) for row in scored]
    wall_values = [value for value in wall_values if value is not None]
    temp_values = []
    for row in scored:
        row_temperatures = [
            float_value(row.get(key))
            for key in (
                "max_gpu_temp_c",
                "max_cpu_temp_c",
                "max_soc_temp_c",
                "max_host_temp_c",
            )
        ]
        temp_values.extend(value for value in row_temperatures if value is not None)
    mtime = max(text_value(row, "export_source_mtime_utc") for row in task_rows)

    return {
        "host": host_key,
        "path": path_key,
        "source": source,
        "source_mtime": mtime,
        "model": model,
        "treatment": treatment,
        "cloud": any((row.get("export_cloud_model") or "").lower() == "true" for row in task_rows),
        "task_count": len(task_rows),
        "scored_tasks": len(scored),
        "passes": passes,
        "failed_tasks": failed_tasks,
        "strict_accuracy": strict_accuracy,
        "granular_passed": granular_passed,
        "granular_total": granular_total,
        "granular_accuracy": granular_passed / granular_total if granular_total else strict_accuracy,
        "avg_wall_seconds": sum(wall_values) / len(wall_values) if wall_values else None,
        "max_temp_c": max(temp_values) if temp_values else None,
    }


def select_latest_best_candidates(host_key, rows):
    grouped = defaultdict(list)
    for row in rows:
        path_key = row.get("export_benchmark_path")
        model = text_value(row, "model")
        source = text_value(row, "export_source_relative_path", "export_source_file")
        if path_key in HOSTS[host_key]["paths"] and model and source:
            grouped[(path_key, source, model, treatment_name(row))].append(row)

    candidates = []
    for (path_key, source, model, treatment), group_rows in grouped.items():
        candidate = candidate_from_rows(host_key, path_key, source, model, treatment, group_rows)
        if candidate:
            candidates.append(candidate)

    by_model_path = defaultdict(list)
    for candidate in candidates:
        by_model_path[(candidate["model"], candidate["path"])].append(candidate)

    selected = {}
    for key, options in by_model_path.items():
        latest_mtime = max(option["source_mtime"] for option in options)
        latest = [option for option in options if option["source_mtime"] == latest_mtime]
        latest.sort(
            key=lambda option: (
                option["strict_accuracy"],
                option["granular_accuracy"],
                option["task_count"],
                -(option["avg_wall_seconds"] or 10**9),
            ),
            reverse=True,
        )
        selected[key] = latest[0]
    return selected, candidates


def build_rankings(host_key, rows):
    selected, candidates = select_latest_best_candidates(host_key, rows)
    required_paths = HOSTS[host_key]["paths"]
    models = sorted({model for model, _ in selected})
    ranked = []
    incomplete = []

    def summarize_model(model, path_results, partial, model_required_paths, cloud):
        results = [result for result in path_results.values() if result]
        if not results:
            return None
        overall = sum(result["strict_accuracy"] for result in results) / len(results)
        granular_passed = sum(result["granular_passed"] for result in results)
        granular_total = sum(result["granular_total"] for result in results)
        scored_tasks = sum(result["scored_tasks"] for result in results)
        passes = sum(result["passes"] for result in results)
        wall_weight = sum(
            result["avg_wall_seconds"] * result["scored_tasks"]
            for result in results
            if result["avg_wall_seconds"] is not None
        )
        wall_count = sum(
            result["scored_tasks"]
            for result in results
            if result["avg_wall_seconds"] is not None
        )
        temps = [result["max_temp_c"] for result in results if result["max_temp_c"] is not None]
        return {
            "model": model,
            "overall_accuracy": overall,
            "granular_accuracy": granular_passed / granular_total if granular_total else overall,
            "passes": passes,
            "scored_tasks": scored_tasks,
            "avg_wall_seconds": wall_weight / wall_count if wall_count else None,
            "max_temp_c": max(temps) if temps else None,
            "paths": path_results,
            "partial": partial,
            "cloud": cloud,
            "missing_paths": [
                path_key
                for path_key in model_required_paths
                if not path_results.get(path_key)
            ],
        }

    for model in models:
        path_results = {path_key: selected.get((model, path_key)) for path_key in required_paths}
        cloud = any(result and result.get("cloud") for result in path_results.values())
        model_required_paths = (
            ["hermes_agent", "openclaw"]
            if cloud
            else required_paths
        )
        missing = [path_key for path_key in model_required_paths if not path_results.get(path_key)]
        if missing:
            incomplete.append(
                summarize_model(
                    model,
                    path_results,
                    partial=True,
                    model_required_paths=model_required_paths,
                    cloud=cloud,
                )
            )
            continue
        ranked.append(
            summarize_model(
                model,
                path_results,
                partial=False,
                model_required_paths=model_required_paths,
                cloud=cloud,
            )
        )

    ranked.sort(
        key=lambda item: (
            item["overall_accuracy"],
            item["granular_accuracy"],
            item["scored_tasks"],
            -(item["avg_wall_seconds"] or 10**9),
        ),
        reverse=True,
    )
    incomplete = [item for item in incomplete if item]
    incomplete.sort(
        key=lambda item: (
            len(required_paths) - len(item["missing_paths"]),
            item["overall_accuracy"],
            item["granular_accuracy"],
            item["scored_tasks"],
        ),
        reverse=True,
    )
    combined = ranked + incomplete
    return combined, {
        "qualified_models": sum(not item["cloud"] for item in ranked),
        "cloud_models": sum(item["cloud"] for item in combined),
        "incomplete_models": sum(not item["cloud"] for item in incomplete),
        "local_observed_models": sum(not item["cloud"] for item in combined),
        "observed_models": len(ranked) + len(incomplete),
        "valid_candidates": len(candidates),
    }


def pct(value):
    return f"{value * 100:.1f}%"


def seconds(value):
    if value is None:
        return "—"
    return f"{value:.1f}s"


def temperature(value):
    if value is None:
        return "—"
    return f"{value:.0f}°C"


def path_cell(result):
    if not result:
        return '<span class="na">—</span>'
    treatment = html.escape(result["treatment"].replace("thinking-", ""))
    return (
        f'<div class="path-score">{pct(result["strict_accuracy"])}</div>'
        f'<div class="path-meta">{result["passes"]}/{result["scored_tasks"]} · {treatment}</div>'
    )


def failure_cell(host_key, path_results):
    groups = []
    failure_count = 0
    for path_key in HOSTS[host_key]["paths"]:
        result = path_results.get(path_key)
        failed = result.get("failed_tasks", []) if result else []
        if not failed:
            continue
        failure_count += len(failed)
        labels = "".join(
            f'<li>{html.escape(TASK_LABELS.get(task_id, task_id))}</li>'
            for task_id in failed
        )
        groups.append(
            f'<div class="failure-group"><strong>{html.escape(PATH_LABELS[path_key])}</strong><ul>{labels}</ul></div>'
        )
    if not groups:
        return '<span class="all-pass">None</span>'
    noun = "failure" if failure_count == 1 else "failures"
    return (
        f'<details class="failures"><summary>{failure_count} {noun}</summary>'
        f'<div class="failure-list">{"".join(groups)}</div></details>'
    )


def ranking_table(host_key, ranking):
    host = HOSTS[host_key]
    path_headers = "".join(f'<th>{html.escape(PATH_LABELS[path_key])}</th>' for path_key in host["paths"])
    rows = []
    ordered = sorted(
        ranking,
        key=lambda item: (item.get("cloud", False), item.get("partial", False)),
    )
    qualified_rank = 0
    for item in ordered:
        partial = item.get("partial", False)
        cloud = item.get("cloud", False)
        if not partial and not cloud:
            qualified_rank += 1
        hidden = cloud or partial or qualified_rank > 10
        row_classes = []
        if hidden:
            row_classes.append("verbose-only")
        if partial:
            row_classes.append("partial-row")
        class_attribute = f' class="{" ".join(row_classes)}"' if row_classes else ""
        badge = "gold" if qualified_rank == 1 else "silver" if qualified_rank == 2 else "bronze" if qualified_rank == 3 else ""
        rank_label = "P" if partial else "C" if cloud else str(qualified_rank)
        model_badges = []
        if cloud:
            model_badges.append('<span class="cloud-badge">Cloud</span>')
        if partial:
            model_badges.append('<span class="partial-badge">Partial</span>')
        model_badge = "".join(model_badges)
        overall_meta = "available paths only" if partial else f'granular {pct(item["granular_accuracy"])}'
        path_cells = "".join(f'<td>{path_cell(item["paths"].get(path_key))}</td>' for path_key in host["paths"])
        row_attributes = (
            f' data-model="{html.escape(item["model"], quote=True)}"'
            f' data-cloud="{str(cloud).lower()}"'
            f' data-partial="{str(partial).lower()}"'
            f' data-score="{item["overall_accuracy"]:.12f}"'
            f' data-granular="{item["granular_accuracy"]:.12f}"'
            f' data-scored="{item["scored_tasks"]}"'
            f' data-wall="{item["avg_wall_seconds"] if item["avg_wall_seconds"] is not None else 1e12}"'
        )
        rows.append(
            f"<tr{class_attribute}{row_attributes}{' hidden' if hidden else ''}>"
            f'<td><span class="rank {badge}">{rank_label}</span></td>'
            f'<td class="model"><span>{html.escape(item["model"])}</span>{model_badge}</td>'
            f'<td><strong class="overall">{pct(item["overall_accuracy"])}</strong><div class="path-meta">{overall_meta}</div></td>'
            f"{path_cells}"
            f'<td><strong>{item["passes"]}/{item["scored_tasks"]}</strong></td>'
            f'<td>{failure_cell(host_key, item["paths"])}</td>'
            f'<td>{seconds(item["avg_wall_seconds"])}</td>'
            f'<td>{temperature(item["max_temp_c"])}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th>Rank</th><th>Model</th><th>Overall</th>'
        f'{path_headers}<th>Strict passes</th><th>Failed tests</th><th>Avg response</th><th>Peak temp</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
    )


rankings = {}
metadata = {}
provenance = {}
for host_key in HOSTS:
    input_path = DATA_DIR / f"{host_key}_benchmark_results.csv"
    with input_path.open(newline="", encoding="utf-8", errors="replace") as handle:
        source_rows = list(csv.DictReader(handle))
    provenance[host_key] = {
        "kind": "consolidated-export",
        "row_count": len(source_rows),
        "export": display_path(input_path),
        "export_sha256": sha256_file(input_path),
        "source_files": [],
    }
    rankings[host_key], metadata[host_key] = build_rankings(host_key, source_rows)

generated = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
sections = []
for host_key, host in HOSTS.items():
    ranking = rankings[host_key]
    best = next(
        (
            item
            for item in ranking
            if not item.get("cloud", False) and not item.get("partial", False)
        ),
        None,
    )
    meta = metadata[host_key]
    cloud_note = (
        f' {meta["cloud_models"]} cloud models are available when Cloud mode is enabled.'
        if meta["cloud_models"]
        else ""
    )
    sections.append(
        f'<section class="system" id="{host_key}">'
        '<div class="section-head">'
        '<div>'
        f'<div class="eyebrow">{html.escape(host["subtitle"])}</div>'
        f'<h2>{html.escape(host["name"])}</h2>'
        f'<p>Top 10 shown by default from {meta["qualified_models"]} fully qualified local models. Verbose mode shows all {meta["local_observed_models"]} observed local models, including {meta["incomplete_models"]} partial or incompatible runs.{cloud_note} {html.escape(host["source_note"])}</p>'
        '</div>'
        + (f'<div class="winner"><span>Accuracy leader</span><strong class="winner-model">{html.escape(best["model"])}</strong><b class="winner-score">{pct(best["overall_accuracy"])}</b></div>' if best else "")
        + '</div>'
        + ranking_table(host_key, ranking)
        + '</section>'
    )

data_json = json.dumps(rankings, separators=(",", ":")).replace("</", "<\\/")
document = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Accuracy-first local LLM benchmark rankings, including Direct, Hermes, OpenClaw, and OCR coverage.">
<title>Local LLM Benchmark — Top 10 by System</title>
<style>
:root {{ --ink:#172033; --muted:#687386; --paper:#f4f6fa; --card:#fff; --line:#dfe4ec; --navy:#132a4a; --blue:#2164d8; --cyan:#32b7c8; --green:#168266; --gold:#d59a20; --shadow:0 16px 45px rgba(30,47,74,.09); }}
* {{ box-sizing:border-box; }}
html {{ scroll-behavior:smooth; }}
body {{ margin:0; background:var(--paper); color:var(--ink); font:14px/1.5 Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
.hero {{ background:linear-gradient(130deg,#0d213d 0%,#173d67 58%,#146579 100%); color:white; padding:54px 24px 42px; }}
.container {{ width:min(1480px,calc(100% - 32px)); margin:0 auto; }}
.hero-grid {{ display:grid; grid-template-columns:1.5fr .8fr; gap:36px; align-items:end; }}
.kicker,.eyebrow {{ font-size:11px; text-transform:uppercase; letter-spacing:.14em; font-weight:750; }}
.kicker {{ color:#8ce3ec; }}
h1 {{ margin:9px 0 12px; font-size:clamp(30px,4vw,52px); line-height:1.06; letter-spacing:-.035em; }}
.lede {{ margin:0; color:#d9e6f4; max-width:820px; font-size:16px; }}
.hero-note {{ background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.18); padding:18px 20px; border-radius:14px; backdrop-filter:blur(8px); }}
.hero-note strong {{ display:block; font-size:14px; margin-bottom:5px; }}
.hero-note span {{ color:#d8e8f3; font-size:12px; }}
.jump {{ display:flex; gap:10px; flex-wrap:wrap; padding:20px 0 2px; }}
.jump a,.report-toggle {{ color:#dae8f6; text-decoration:none; border:1px solid rgba(255,255,255,.22); border-radius:999px; padding:7px 12px; font:inherit; font-size:12px; line-height:1.5; }}
.report-toggle {{ cursor:pointer; background:rgba(255,255,255,.08); font-weight:750; }}
.jump a:hover,.report-toggle:hover {{ background:rgba(255,255,255,.12); }}
.report-toggle[aria-pressed="true"] {{ color:#082d37; background:#8ce3ec; border-color:#8ce3ec; }}
.report-toggle:focus-visible {{ outline:3px solid #fff; outline-offset:2px; }}
.sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }}
main {{ padding:28px 0 50px; }}
.system {{ background:var(--card); border:1px solid var(--line); border-radius:20px; box-shadow:var(--shadow); margin:0 0 24px; overflow:hidden; }}
.section-head {{ padding:25px 27px 20px; display:flex; gap:24px; align-items:flex-end; justify-content:space-between; border-bottom:1px solid var(--line); }}
.eyebrow {{ color:var(--blue); }}
h2 {{ margin:4px 0 2px; font-size:25px; letter-spacing:-.02em; }}
.section-head p {{ color:var(--muted); margin:0; }}
.winner {{ min-width:260px; display:grid; grid-template-columns:1fr auto; gap:1px 15px; padding:12px 15px; background:#edf8f6; border:1px solid #cbe8e1; border-radius:12px; }}
.winner span {{ grid-column:1/-1; color:var(--green); text-transform:uppercase; letter-spacing:.1em; font-size:10px; font-weight:800; }}
.winner strong {{ overflow-wrap:anywhere; }}
.winner b {{ color:var(--green); font-size:18px; }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; min-width:1060px; border-collapse:separate; border-spacing:0; }}
th {{ padding:11px 13px; background:#f8fafc; color:#657085; font-size:10px; text-align:left; text-transform:uppercase; letter-spacing:.075em; border-bottom:1px solid var(--line); white-space:nowrap; }}
td {{ padding:13px; border-bottom:1px solid #edf0f5; vertical-align:middle; }}
tbody tr:last-child td {{ border-bottom:0; }}
tbody tr:hover {{ background:#f9fbff; }}
tr[hidden] {{ display:none; }}
.partial-row {{ background:#fffaf0; }}
.partial-row:hover {{ background:#fff5df; }}
.rank {{ width:27px; height:27px; border-radius:9px; display:inline-grid; place-items:center; background:#edf1f6; font-weight:800; color:#526174; }}
.rank.gold {{ background:#fff2c7; color:#8c6300; }} .rank.silver {{ background:#e8eef4; color:#53677a; }} .rank.bronze {{ background:#f8e4d7; color:#8c5331; }}
.model {{ font-weight:700; min-width:250px; max-width:350px; overflow-wrap:anywhere; }}
.partial-badge,.cloud-badge {{ display:inline-block; margin-left:7px; border-radius:999px; padding:2px 6px; font-size:9px; font-weight:800; text-transform:uppercase; letter-spacing:.06em; vertical-align:middle; }}
.partial-badge {{ color:#8c6300; background:#fff2c7; }}
.cloud-badge {{ color:#245b9e; background:#e8f1ff; }}
.overall {{ color:var(--green); font-size:16px; }}
.path-score {{ font-weight:750; }}
.path-meta {{ font-size:10px; color:var(--muted); white-space:nowrap; margin-top:1px; }}
.na {{ color:#a5adba; }}
.all-pass {{ display:inline-block; color:var(--green); background:#edf8f6; border:1px solid #cbe8e1; border-radius:999px; padding:3px 8px; font-size:10px; font-weight:750; }}
.failures {{ min-width:125px; }}
.failures summary {{ cursor:pointer; color:#a94a42; font-size:10px; font-weight:800; white-space:nowrap; }}
.failures summary:hover {{ color:#7f2923; }}
.failure-list {{ margin-top:8px; padding:8px 9px; background:#fff7f5; border:1px solid #f0d7d1; border-radius:8px; min-width:190px; }}
.failure-group + .failure-group {{ border-top:1px solid #edd8d3; margin-top:7px; padding-top:7px; }}
.failure-group strong {{ color:#82443e; font-size:9px; text-transform:uppercase; letter-spacing:.06em; }}
.failure-group ul {{ margin:4px 0 0; padding-left:16px; color:#5f4a48; font-size:10px; }}
.test-suite {{ background:#fff; border:1px solid var(--line); border-radius:20px; box-shadow:var(--shadow); padding:26px; margin:0 0 24px; }}
.test-head {{ display:flex; align-items:end; justify-content:space-between; gap:28px; margin-bottom:18px; }}
.test-head h3 {{ margin:4px 0 2px; font-size:23px; letter-spacing:-.02em; }}
.test-head p {{ margin:0; color:var(--muted); max-width:780px; }}
.coverage-strip {{ display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }}
.coverage-strip span {{ border:1px solid #cddbf0; color:#345b8d; background:#f3f7fd; border-radius:999px; padding:6px 10px; font-size:11px; font-weight:700; white-space:nowrap; }}
.test-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
.test-card {{ background:#f8fafc; border:1px solid #e3e8f0; border-radius:14px; padding:15px 16px; }}
.test-card h4 {{ margin:0 0 5px; font-size:14px; }}
.test-card p {{ margin:0 0 10px; color:var(--muted); font-size:12px; }}
.tags {{ display:flex; flex-wrap:wrap; gap:5px; }}
.tag {{ background:#e9eef6; color:#536278; border-radius:6px; padding:3px 6px; font-size:9px; font-weight:750; letter-spacing:.02em; }}
.catalog-title {{ display:flex; align-items:center; gap:9px; margin:22px 0 10px; font-size:15px; }}
.catalog-title:first-of-type {{ margin-top:18px; }}
.catalog-title span {{ background:#e8f1ff; color:#245b9e; border-radius:999px; padding:4px 8px; font-size:9px; text-transform:uppercase; letter-spacing:.08em; }}
.benchmark-table {{ border:1px solid #e0e6ef; border-radius:13px; overflow-x:auto; }}
.benchmark-table table {{ min-width:980px; }}
.benchmark-table th {{ background:#f2f6fb; }}
.benchmark-table td {{ padding:10px 12px; font-size:11px; vertical-align:top; }}
.benchmark-table td:first-child {{ width:190px; }}
.benchmark-name {{ display:block; color:var(--navy); font-weight:800; font-size:12px; }}
.benchmark-type {{ display:inline-block; margin-top:3px; color:#276a7a; background:#e8f7f8; border-radius:5px; padding:2px 5px; font-size:8px; font-weight:800; letter-spacing:.05em; text-transform:uppercase; }}
.score-rule {{ color:#4d5b70; }}
.custom-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
.custom-check {{ border:1px solid #e3e8f0; background:#fafbfd; border-radius:12px; padding:13px; }}
.custom-check strong {{ display:block; margin-bottom:4px; }}
.custom-check p {{ color:var(--muted); margin:0; font-size:11px; }}
.suite-note {{ margin:14px 0 0; padding:11px 13px; border-left:3px solid var(--cyan); background:#f0fafb; color:#526978; font-size:11px; }}
.method {{ background:#fff; border:1px solid var(--line); border-radius:16px; padding:22px 24px; color:var(--muted); }}
.method h3 {{ color:var(--ink); margin:0 0 8px; }}
.method-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; }}
.method b {{ color:var(--ink); display:block; margin-bottom:3px; }}
footer {{ color:#788397; padding:24px 0 38px; font-size:12px; }}
@media (max-width:820px) {{ .hero-grid,.method-grid,.test-grid,.custom-grid {{ grid-template-columns:1fr; }} .section-head,.test-head {{ align-items:stretch; flex-direction:column; }} .coverage-strip {{ justify-content:flex-start; }} .winner {{ width:100%; }} .container {{ width:min(100% - 20px,1480px); }} .hero {{ padding-top:38px; }} }}
@media print {{ body {{ background:#fff; }} .hero {{ padding:28px 0; print-color-adjust:exact; }} .container {{ width:100%; }} .system {{ box-shadow:none; break-before:page; }} .system:first-child {{ break-before:auto; }} .jump {{ display:none; }} .table-wrap {{ overflow:visible; }} table {{ min-width:0; font-size:9px; }} td,th {{ padding:7px; }} .model {{ min-width:150px; }} }}
</style>
</head>
<body>
<header class="hero">
  <div class="container hero-grid">
    <div>
      <div class="kicker">Accuracy-first local model evaluation</div>
      <h1>Top 10 Models by System</h1>
      <p class="lede">A portable, audit-friendly ranking of models tested through Ollama Direct, Hermes Agent, and OpenClaw, with OCR coverage included in the completed DGX Spark and Mac Mini campaigns. Performance is ranked on correctness—not generation speed.</p>
      <nav class="jump" aria-label="Report controls and section navigation"><a href="#spark">DGX Spark</a><a href="#studio">Mac Studio</a><a href="#mini">Mac Mini</a><a href="#tests">Tests</a><a href="#method">Methodology</a><button class="report-toggle" id="verbose-toggle" type="button" aria-pressed="false">Verbose: Off</button><button class="report-toggle" id="cloud-toggle" type="button" aria-pressed="false">Cloud: Off</button><span class="sr-only" id="report-status" aria-live="polite">Local top 10 models shown for each system.</span></nav>
    </div>
    <div class="hero-note"><strong>Generated {html.escape(generated)}</strong><span>Self-contained HTML · safe to email, archive, or open offline in any modern browser.</span></div>
  </div>
</header>
<main class="container">
  {''.join(sections)}
  <section class="test-suite" id="tests">
    <div class="test-head">
      <div><div class="eyebrow">Benchmark coverage</div><h3>What the models were tested on</h3><p>The suite checks whether each model can follow exact instructions, reason correctly, write working code, use agent tools, retrieve supplied facts, resist unsafe instructions, and—where supported—read an image.</p></div>
      <div class="coverage-strip"><span>Current campaigns: 17 core + OCR</span><span>Direct · Hermes · OpenClaw</span><span>Historical Studio retained</span></div>
    </div>
    <h4 class="catalog-title">Standardized benchmark families represented <span>13 families</span></h4>
    <div class="benchmark-table">
      <table aria-label="Standardized benchmark proxy catalog">
        <thead><tr><th>Standardized benchmark</th><th>What it measures</th><th>Local task used</th><th>How this suite scores it</th></tr></thead>
        <tbody>
          <tr><td><span class="benchmark-name">IFEval</span><span class="benchmark-type">Instruction following</span></td><td>Whether a model obeys explicit output constraints instead of merely giving a semantically related answer.</td><td><strong>ifeval_exact</strong> requires an exact response; <strong>ifeval_json</strong> requires compact JSON with the exact schema and value types.</td><td class="score-rule">Strict string equality or strict JSON parsing. Extra prose, whitespace outside strings, duplicate keys, or extra fields fail.</td></tr>
          <tr><td><span class="benchmark-name">GSM8K</span><span class="benchmark-type">Grade-school math</span></td><td>Multi-step arithmetic reasoning expressed through a short word problem.</td><td><strong>gsm8k_mini</strong> asks for the correct count after following a compact scenario.</td><td class="score-rule">The response must finish with one exact <strong>FINAL</strong> answer marker containing the expected number.</td></tr>
          <tr><td><span class="benchmark-name">MATH-500</span><span class="benchmark-type">Competition math</span></td><td>Symbolic and algebraic reasoning beyond basic arithmetic.</td><td><strong>math500_mini</strong> presents a short algebra problem and asks the model to solve for the unknown.</td><td class="score-rule">Exact terminal answer marker; incomplete reasoning or a correct value in the wrong final format does not pass strict scoring.</td></tr>
          <tr><td><span class="benchmark-name">MMLU-Pro</span><span class="benchmark-type">Professional knowledge</span></td><td>Domain knowledge and discrimination between plausible multiple-choice answers.</td><td><strong>mmlu_pro_security</strong> uses a professional cybersecurity mitigation question.</td><td class="score-rule">The selected option must appear in one unambiguous terminal <strong>FINAL</strong> marker.</td></tr>
          <tr><td><span class="benchmark-name">ARC-Challenge</span><span class="benchmark-type">Science reasoning</span></td><td>Grade-school science knowledge combined with causal reasoning.</td><td><strong>arc_challenge_mini</strong> asks the model to choose the best explanation for an observed physical effect.</td><td class="score-rule">Exact multiple-choice terminal answer; explanations alone do not count without the required final selection.</td></tr>
          <tr><td><span class="benchmark-name">HellaSwag</span><span class="benchmark-type">Commonsense completion</span></td><td>Choosing the most plausible continuation of an everyday situation.</td><td><strong>hellaswag_mini</strong> provides a scenario and several possible next events.</td><td class="score-rule">Exact terminal selection of the expected continuation.</td></tr>
          <tr><td><span class="benchmark-name">TruthfulQA</span><span class="benchmark-type">Truthfulness</span></td><td>Resistance to common misconceptions and confidently stated false premises.</td><td><strong>truthfulqa_mini</strong> asks a misconception-sensitive factual question.</td><td class="score-rule">The response must end with the expected truthful answer in the required marker.</td></tr>
          <tr><td><span class="benchmark-name">HumanEval+</span><span class="benchmark-type">Code generation</span></td><td>Whether generated Python implements a function correctly across normal, boundary, and malformed inputs.</td><td><strong>humaneval_mini</strong> asks for an IPv4 classification function.</td><td class="score-rule">Restricted execution against 25 behavioral cases. Unsafe code, exceptions, invalid-address acceptance, or incorrect return types fail cases.</td></tr>
          <tr><td><span class="benchmark-name">MBPP+</span><span class="benchmark-type">Code generation</span></td><td>Practical Python implementation from a concise natural-language specification.</td><td><strong>mbpp_mini</strong> asks for unique valid IPv4 extraction from log lines.</td><td class="score-rule">Restricted execution against 9 cases covering duplicates, invalid addresses, IPv6 noise, and token boundaries.</td></tr>
          <tr><td><span class="benchmark-name">BFCL</span><span class="benchmark-type">Function calling</span></td><td>Producing the correct tool name and structured arguments for an agent action.</td><td><strong>bfcl_mini</strong> requests a security block action for a specific source IP.</td><td class="score-rule">Strict JSON only: exact tool, exact argument keys, correct IP, and a reason that identifies both SSH and brute force.</td></tr>
          <tr><td><span class="benchmark-name">RAGAS / RAG</span><span class="benchmark-type">Grounded retrieval</span></td><td>Answering from supplied context without replacing it with unrelated prior knowledge.</td><td><strong>ragas_mini</strong> provides a short network passage containing the required address.</td><td class="score-rule">The exact grounded fact must appear in the terminal answer marker.</td></tr>
          <tr><td><span class="benchmark-name">CyberSecEval-style</span><span class="benchmark-type">Security operations</span></td><td>Interpreting security telemetry and selecting a proportionate defensive action.</td><td><strong>cyber_soc_mini</strong> asks for an incident classification and immediate SOC action.</td><td class="score-rule">Compact JSON with the exact classification and action enums; descriptive but nonconforming prose fails strict scoring.</td></tr>
          <tr><td><span class="benchmark-name">OCRBench / TextVQA</span><span class="benchmark-type">Vision & OCR</span></td><td>Reading exact text from an image rather than inferring approximate content.</td><td><strong>ocrbench_mini</strong> supplies a generated text image to compatible vision models.</td><td class="score-rule">Exact terminal transcription. Character substitutions, missing spaces, or joined words fail. Direct Ollama only when vision is supported.</td></tr>
        </tbody>
      </table>
    </div>
    <h4 class="catalog-title">Suite-specific operational checks <span>Custom controls</span></h4>
    <div class="custom-grid">
      <article class="custom-check"><strong>Exact Reply smoke test</strong><p>Confirms the model and runtime can return one required token sequence without commentary or formatting drift.</p></article>
      <article class="custom-check"><strong>Simple Reasoning smoke test</strong><p>Checks a short reasoning chain and exact final-answer compliance before the more specialized tasks.</p></article>
      <article class="custom-check"><strong>Coding Micro</strong><p>Runs a compact Python task through 25 behavioral cases to catch exceptions, unsafe constructs, and boundary errors.</p></article>
      <article class="custom-check"><strong>Prompt Injection</strong><p>Tests whether the model ignores a conflicting instruction and retrieves the authorized fact from the trusted context.</p></article>
    </div>
    <p class="suite-note"><strong>Important scope note:</strong> The standardized names above identify the benchmark families and skills represented. This suite uses one compact, deterministic proxy task per family; it does <em>not</em> run the complete official datasets or reproduce official leaderboard scores. The final Spark and Mini campaigns contain 17 core tasks plus OCR in each harness. Unsupported OCR, task errors, timeouts, and content mismatches remain visible and count as non-passes; they are not silently removed from the denominator. The retained Studio results use their historical task coverage.</p>
  </section>
  <section class="method" id="method">
    <h3>How the ranking works</h3>
    <div class="method-grid">
      <div><b>Complete paths only</b>Models must have a complete observation set for every required local harness on that host. Terminally incomplete, context-overflow, resource-pressure, and OOM paths are excluded.</div>
      <div><b>Latest valid run</b>The newest complete source is selected for each model and path. When thinking treatments coexist, the most accurate treatment is used and displayed.</div>
      <div><b>Equal path weighting</b>Local overall accuracy averages Direct, Hermes, and OpenClaw. Optional cloud rankings average their two available agent harnesses, Hermes and OpenClaw. Each current harness has 18 equally weighted tasks, including OCR; skips and execution failures score zero.</div>
      <div><b>Speed is informational</b>Average response time and peak recorded temperature are shown for review but never affect rank.</div>
    </div>
  </section>
</main>
<footer class="container">Source: final DGX Spark, Mac Studio, and Mac Mini campaigns completed 2026-08-23. Rankings retain file-level provenance in the accompanying CSV exports and verification manifest.</footer>
<script type="application/json" id="ranking-data">{data_json}</script>
<script>
(() => {{
  const verboseButton = document.getElementById("verbose-toggle");
  const cloudButton = document.getElementById("cloud-toggle");
  const status = document.getElementById("report-status");
  const state = {{ verbose: false, cloud: false }};
  const number = (row, key, fallback = 0) => {{
    const value = Number(row.dataset[key]);
    return Number.isFinite(value) ? value : fallback;
  }};
  const compareRows = (left, right) => {{
    const leftPartial = left.dataset.partial === "true";
    const rightPartial = right.dataset.partial === "true";
    if (leftPartial !== rightPartial) return leftPartial ? 1 : -1;
    return (
      number(right, "score") - number(left, "score") ||
      number(right, "granular") - number(left, "granular") ||
      number(right, "scored") - number(left, "scored") ||
      number(left, "wall", 1e12) - number(right, "wall", 1e12) ||
      left.dataset.model.localeCompare(right.dataset.model)
    );
  }};
  const updateReport = () => {{
    document.body.classList.toggle("verbose", state.verbose);
    document.body.classList.toggle("cloud", state.cloud);
    verboseButton.setAttribute("aria-pressed", String(state.verbose));
    cloudButton.setAttribute("aria-pressed", String(state.cloud));
    verboseButton.textContent = state.verbose ? "Verbose: On" : "Verbose: Off";
    cloudButton.textContent = state.cloud ? "Cloud: On" : "Cloud: Off";

    let visibleRows = 0;
    let includedRows = 0;
    document.querySelectorAll("section.system").forEach((section) => {{
      const tbody = section.querySelector("tbody");
      const rows = Array.from(tbody.querySelectorAll("tr[data-model]"));
      const included = rows
        .filter((row) => state.cloud || row.dataset.cloud !== "true")
        .sort(compareRows);
      const excluded = rows.filter((row) => !included.includes(row));
      [...included, ...excluded].forEach((row) => tbody.appendChild(row));
      includedRows += included.length;

      let rank = 0;
      included.forEach((row) => {{
        const partial = row.dataset.partial === "true";
        if (!partial) rank += 1;
        const show = state.verbose || (!partial && rank <= 10);
        row.hidden = !show;
        if (show) visibleRows += 1;
        const badge = row.querySelector(".rank");
        badge.textContent = partial ? "P" : String(rank);
        badge.className = `rank ${{!partial && rank === 1 ? "gold" : !partial && rank === 2 ? "silver" : !partial && rank === 3 ? "bronze" : ""}}`;
      }});
      excluded.forEach((row) => {{ row.hidden = true; }});

      const winner = included.find((row) => row.dataset.partial !== "true");
      if (winner) {{
        section.querySelector(".winner-model").textContent = winner.dataset.model;
        section.querySelector(".winner-score").textContent = `${{(number(winner, "score") * 100).toFixed(1)}}%`;
      }}
    }});
    status.textContent = `${{state.cloud ? "Local and cloud" : "Local-only"}} rankings; ${{visibleRows}} of ${{includedRows}} included model rows shown${{state.verbose ? " in verbose mode" : " across the top 10 tables"}}.`;
  }};
  verboseButton.addEventListener("click", () => {{
    state.verbose = !state.verbose;
    updateReport();
  }});
  cloudButton.addEventListener("click", () => {{
    state.cloud = !state.cloud;
    updateReport();
  }});
  updateReport();
}})();
</script>
</body>
</html>
'''

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(document, encoding="utf-8")
manifest = {
    "generated": generated,
    "report": display_path(OUTPUT),
    "report_sha256": sha256_file(OUTPUT),
    "hosts": provenance,
    "metadata": metadata,
}
manifest_path = OUTPUT.with_suffix(".manifest.json")
manifest_path.write_text(
    json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps({
    "output": str(OUTPUT),
    "manifest": str(manifest_path),
    "rankings": {host: [{"rank": i + 1, "model": item["model"], "overall_accuracy": round(item["overall_accuracy"], 6)} for i, item in enumerate(items)] for host, items in rankings.items()},
    "metadata": metadata,
}, indent=2))
