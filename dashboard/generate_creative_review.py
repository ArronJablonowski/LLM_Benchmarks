#!/usr/bin/env python3
"""Generate a human-only review workspace for creative-suite artifacts."""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def load_records(roots: list[Path]) -> list[dict]:
    records = []
    for root in roots:
        for path in sorted(root.rglob("*_creative.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("row", {}).get("benchmark_suite") != "creative":
                    continue
                record["_source"] = str(path)
                records.append(record)
    return records


def link(path: Path, label: str) -> str:
    if not path.exists():
        return f"<span class=\"missing\">{html.escape(label)} (missing)</span>"
    return f'<a href="{html.escape(path.resolve().as_uri(), quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(label)}</a>'


def observation_card(record: dict) -> str:
    row = record["row"]
    key = "|".join((row.get("benchmark_profile", ""), row.get("model", ""), row.get("model_runner", ""), row.get("harness", ""), row.get("task_id", "")))
    workspace = Path(row.get("workspace", "."))
    preview = workspace / row.get("preview_entry", "")
    artifacts = record.get("artifacts", [])
    if not preview.is_file():
        preferred = [
            name for name in artifacts
            if Path(name).suffix.lower() in {".png", ".webp", ".jpg", ".jpeg", ".gif", ".svg", ".html"}
        ]
        if preferred:
            preview = workspace / preferred[0]
    changed = set(record.get("changed_artifacts", []))
    artifact_links = []
    for name in artifacts:
        marker = " <small>changed</small>" if name in changed else ""
        artifact_links.append(f"<li>{link(workspace / name, name)}{marker}</li>")
    dimensions = record.get("review_dimensions", [])
    controls = []
    for dimension in dimensions:
        field = hashlib_key(dimension)
        options = '<option value="">Not scored</option>' + "".join(f'<option value="{score}">{score}</option>' for score in range(1, 11))
        controls.append(f'<label>{html.escape(dimension)}<select data-score="{field}" aria-label="{html.escape(dimension, quote=True)} score">{options}</select></label>')
    status_class = "good" if row.get("status") == "submitted" else "warn"
    return f"""
<article class="submission" data-key="{html.escape(key, quote=True)}" data-model="{html.escape(row.get('model',''), quote=True)}" data-task="{html.escape(row.get('task_id',''), quote=True)}">
  <header><div><p class="eyebrow">{html.escape(row.get('creative_medium','creative work'))}</p><h2>{html.escape(row.get('task_name', row.get('task_id','unknown')))}</h2><p><strong>{html.escape(row.get('model','unknown'))}</strong> · {html.escape(row.get('model_runner','unknown'))} · {html.escape(row.get('harness','unknown'))}</p></div><span class="status {status_class}">{html.escape(row.get('status','unknown'))}</span></header>
  <div class="facts"><span>{html.escape(row.get('benchmark_profile','unversioned'))}</span><span>{html.escape(str(row.get('wall_seconds','')))}s</span><span>{len(artifacts)} artifacts</span><span>{len(changed)} changed</span></div>
  <div class="actions">{link(workspace, 'Open artifact folder')} {link(preview, 'Open suggested preview')}</div>
  <details><summary>Artifact inventory</summary><ul>{''.join(artifact_links) if artifact_links else '<li>No artifacts recorded</li>'}</ul></details>
  <section class="rubric"><h3>Human review</h3><div class="scores">{''.join(controls)}</div>
    <div class="review-meta"><label>Review state<select data-review-state><option value="needs-review">Needs review</option><option value="in-review">In review</option><option value="reviewed">Reviewed</option></select></label><label class="favorite"><input type="checkbox" data-favorite> Shortlist</label><output data-average>Human average: —</output></div>
    <label>Reviewer notes<textarea data-notes rows="5" placeholder="What is distinctive, effective, weak, derivative, or especially polished?"></textarea></label>
  </section>
</article>"""


def hashlib_key(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def generate(records: list[dict]) -> str:
    generated = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    cards = "".join(observation_card(record) for record in records)
    manifest = json.dumps(
        [{"row": record["row"], "review_dimensions": record.get("review_dimensions", []), "artifacts": record.get("artifacts", [])} for record in records],
        separators=(",", ":"), ensure_ascii=False,
    ).replace("</", "<\\/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Creative Benchmark · Human Review</title>
<style>
:root{{--bg:#efece6;--paper:#fffdf9;--ink:#171717;--muted:#68635c;--line:#d8d1c7;--accent:#5a38c9;--good:#176b45;--warn:#9b4b18}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,sans-serif}}main{{max-width:1500px;margin:auto;padding:28px}}.hero{{padding:34px;border-radius:22px;background:#171717;color:white}}h1{{font-size:clamp(30px,5vw,58px);margin:0 0 8px}}.hero p{{max-width:850px;color:#d7d2c9}}.warning{{padding:14px 16px;margin-top:18px;border:1px solid #e3ad58;background:#fff4db;color:#67410c;border-radius:12px}}.toolbar{{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:22px 0}}label{{display:grid;gap:5px;font-weight:650}}select,textarea,button{{font:inherit;border:1px solid var(--line);border-radius:9px;background:white;padding:9px}}button{{cursor:pointer;background:var(--accent);color:white;border:0}}.submission{{background:var(--paper);border:1px solid var(--line);border-radius:18px;padding:22px;margin:18px 0;box-shadow:0 8px 25px #382c1710}}.submission>header{{display:flex;justify-content:space-between;gap:18px}}h2{{margin:2px 0;font-size:24px}}.eyebrow{{margin:0;color:var(--accent);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.1em}}.status{{height:max-content;border-radius:999px;padding:5px 10px;font-size:12px;font-weight:800}}.good{{background:#dff5ea;color:var(--good)}}.warn{{background:#ffe8d7;color:var(--warn)}}.facts,.actions{{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}}.facts span{{background:#eee9e1;border-radius:999px;padding:5px 9px;color:var(--muted)}}.actions a{{font-weight:750}}.missing{{color:var(--warn)}}details{{border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:10px 0}}.rubric{{margin-top:18px}}.scores{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}.review-meta{{display:flex;flex-wrap:wrap;align-items:end;gap:20px;margin:16px 0}}.favorite{{display:flex;align-items:center}}textarea{{width:100%;resize:vertical}}small{{color:var(--muted)}}footer{{color:var(--muted);padding:24px 0}}@media(max-width:650px){{main{{padding:14px}}.submission>header{{display:block}}}}
</style></head><body><main><section class="hero"><p class="eyebrow">Human-only evaluation</p><h1>Creative Benchmark Review</h1><p>Review original artifacts across models, runners, and agent harnesses. All scores and notes in this report come from a human reviewer; the benchmark assigns no automated aesthetic verdict.</p><p>Generated {html.escape(generated)} · {len(records)} submissions</p></section>
<p class="warning"><strong>Safety:</strong> model-generated HTML and JavaScript are untrusted. This report never embeds or executes them automatically. Open interactive artifacts in an isolated browser profile or local sandbox.</p>
<section class="toolbar"><label>Model filter<select id="model-filter"><option value="">All models</option></select></label><label>Task filter<select id="task-filter"><option value="">All tasks</option></select></label><button id="export" type="button">Export human reviews</button><label>Import reviews<input id="import" type="file" accept="application/json"></label></section>
<section id="submissions">{cards if cards else '<p>No creative-suite evidence found.</p>'}</section><footer>Review data is stored in this browser's local storage until exported. No human ratings are written into benchmark evidence automatically.</footer>
<script type="application/json" id="creative-manifest">{manifest}</script><script>
const storageKey='creative-human-reviews:v1:'+location.pathname;let reviews=JSON.parse(localStorage.getItem(storageKey)||'{{}}');
const cards=[...document.querySelectorAll('.submission')];const modelFilter=document.querySelector('#model-filter');const taskFilter=document.querySelector('#task-filter');
for(const value of [...new Set(cards.map(c=>c.dataset.model))].sort()) modelFilter.add(new Option(value,value));for(const value of [...new Set(cards.map(c=>c.dataset.task))].sort()) taskFilter.add(new Option(value,value));
function current(card){{const scores={{}};card.querySelectorAll('[data-score]').forEach(input=>{{if(input.value)scores[input.dataset.score]=Number(input.value)}});return{{scores,reviewState:card.querySelector('[data-review-state]').value,favorite:card.querySelector('[data-favorite]').checked,notes:card.querySelector('[data-notes]').value}}}}
function average(card){{const values=Object.values(current(card).scores);card.querySelector('[data-average]').textContent='Human average: '+(values.length?(values.reduce((a,b)=>a+b,0)/values.length).toFixed(1):'—')}}
function save(card){{reviews[card.dataset.key]=current(card);localStorage.setItem(storageKey,JSON.stringify(reviews));average(card)}}
for(const card of cards){{const saved=reviews[card.dataset.key]||{{}};card.querySelectorAll('[data-score]').forEach(input=>input.value=(saved.scores||{{}})[input.dataset.score]||'');card.querySelector('[data-review-state]').value=saved.reviewState||'needs-review';card.querySelector('[data-favorite]').checked=Boolean(saved.favorite);card.querySelector('[data-notes]').value=saved.notes||'';card.addEventListener('input',()=>save(card));card.addEventListener('change',()=>save(card));average(card)}}
function filter(){{for(const card of cards)card.hidden=Boolean((modelFilter.value&&card.dataset.model!==modelFilter.value)||(taskFilter.value&&card.dataset.task!==taskFilter.value))}}modelFilter.addEventListener('change',filter);taskFilter.addEventListener('change',filter);
document.querySelector('#export').addEventListener('click',()=>{{const payload={{schema:'creative-human-review-v1',exportedAt:new Date().toISOString(),report:location.pathname,reviews}};const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{{type:'application/json'}}));const a=document.createElement('a');a.href=url;a.download='creative-human-reviews.json';a.click();URL.revokeObjectURL(url)}});
document.querySelector('#import').addEventListener('change',async event=>{{const payload=JSON.parse(await event.target.files[0].text());if(payload.schema!=='creative-human-review-v1')throw new Error('Unsupported review schema');reviews=payload.reviews||{{}};localStorage.setItem(storageKey,JSON.stringify(reviews));location.reload()}});
</script></main></body></html>"""


def main(argv=None) -> int:
    args = parse_args(argv); records = load_records(args.input_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generate(records), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
