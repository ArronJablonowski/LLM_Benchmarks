# Creative benchmark suite

The `creative` suite is an artifact-production benchmark designed to show
creative range and design judgment. Its profile is `creative-human-v1`.
Automated code records execution evidence and artifact provenance only. It does
not assign correctness, quality, aesthetic, or creativity scores.

## Creative briefs

| Task | Medium | Human review emphasis |
|---|---|---|
| `creative_brand_launch_site` | Original website design | Art direction, composition, typography, interaction, responsiveness |
| `creative_key_art_campaign` | Image generation or original SVG art | Concept, impact, composition, color/texture, campaign adaptability |
| `creative_threejs_dreamscape` | Three.js generative animation | Space, choreography, lighting/materials, interaction, atmosphere |
| `creative_scroll_motion_story` | Scroll-driven visual narrative | Story, pacing, transitions, emotional impact, reduced motion |
| `creative_nextjs_motion_experience` | Next.js App Router + Motion | Art direction, shared-layout transitions, gesture feedback, responsive polish |
| `creative_microinteraction_lab` | UI motion system | Inventiveness, coherence, feedback, tactile quality, accessibility |

All briefs and seed files are original. They discourage copying existing brands,
campaigns, artists, templates, and stock demos. The image task asks the harness
to use an image-generation capability when available and permits an original SVG
fallback so text-only local harnesses can still produce reviewable visual work.

The Next.js seed pins Next.js 16.3.3, React 19.2.8, and Motion 13.1.1 for a
reproducible dependency contract. It evaluates source artifacts even when the
benchmark host intentionally does not install frontend dependencies during
inference. Package references: [Next.js on npm](https://www.npmjs.com/package/next),
[React on npm](https://www.npmjs.com/package/react), and
[Motion on npm](https://www.npmjs.com/package/motion). Three.js work follows the
[official Three.js module guidance](https://threejs.org/manual/#en/fundamentals).

## Human-review protocol

Each observation is one model × model runner × agent harness × creative brief.
The dedicated report presents the task-specific review dimensions as unscored
fields. A reviewer deliberately assigns 1–10 ratings, writes notes, chooses a
review state, and can shortlist work. Any displayed average is calculated only
from those human-entered ratings.

For credible comparisons:

1. Randomize or conceal model identity during the first review pass when
   practical.
2. Use at least two reviewers and preserve their exports separately.
3. Review the live artifact at common desktop and mobile sizes.
4. Check reduced-motion behavior for animated briefs.
5. Separate “the concept is original” from “the implementation is polished.”
6. Record missing or broken work in notes rather than inventing an automated
   failure score.

The report stores review state in browser local storage and exports a
`creative-human-review-v1` JSON document. It never writes human ratings back
into raw benchmark evidence automatically.

## Run it

List the briefs without touching models or workspaces:

```bash
python3 scripts/creative_agent_benchmarks.py \
  --suite creative --harness pi \
  --models-file models.tsv --output-dir reports/pi --workspace creative-work \
  --list-tasks
```

Preview, then execute an Ollama campaign:

```bash
BENCH_CAMPAIGN_DIR="$HOME/.hermes/reports/campaigns/creative-human-v1" \
BENCH_MODELS_FILE="$HOME/.hermes/reports/campaigns/creative-human-v1/models.tsv" \
BENCH_CLI_HARNESSES="pi goose openhands" \
ops/run_creative_agent_campaign.sh
```

The same `BENCH_SUITE=creative` selection is supported by the guarded
llama.cpp, vLLM, and TensorRT-LLM campaign wrappers. Task timeouts default to two
hours and remain capped at four hours.

The direct-Ollama, Hermes→Ollama, and OpenClaw→Ollama comparison uses the same
guarded wrapper as Coding while preserving a separate Creative report:

```bash
BENCH_CAMPAIGN_DIR="$HOME/.hermes/reports/campaigns/project-three-path" \
BENCH_MODELS_FILE="$HOME/.hermes/reports/campaigns/project-three-path/models.tsv" \
BENCH_PROJECT_SUITES="creative" \
BENCH_PROJECT_HARNESSES="ollama-direct hermes openclaw" \
ops/run_ollama_project_three_path_campaign.sh
```

Generate or refresh the review workspace:

```bash
python3 dashboard/generate_creative_review.py \
  --input-root "$BENCH_CAMPAIGN_DIR" \
  --output "$BENCH_CAMPAIGN_DIR/creative_human_review.html"
```

## Safety and isolation

Creative artifacts are untrusted model output. The report never embeds or
executes generated HTML or JavaScript automatically. Review interactive work in
an isolated browser profile or local sandbox. The suite retains frozen model
provenance, runner/harness versions, responses, workspace paths, artifact
inventories, timings, temperatures, peak memory, timeouts, and safety errors.

Files are named `*_creative.csv` and `*_creative.jsonl`. The creative report
only reads records whose `benchmark_suite` is exactly `creative`; Standard and
Coding evidence cannot enter the human-review workspace.
