# Progressive routing-grid benchmarks v1

This pack adds **60 original cases: ten categories × six progressively harder
levels**. It supplements existing suites; it does not replace their fixtures,
change historical results, launch models or write routing feedback.

## Coverage decision

The 2026-09-29 routing-grid evidence audit found measured evidence on Coding,
CLI and terminal, OCR/document vision, and General use. Those cards retain their
existing suites. The six recently added specialist cards had no measurements;
Writing and Creative showed zero-sample priors, while Image and Video generation
had no eligible configured generators. All ten receive new category-specific
cases here. Existing Standard reasoning/RAG/tool-call mini proxies and Creative
web/design briefs do not establish coverage of these new domains and profiles.

| Routing card | Domain | New cases | Progression |
|---|---|---:|---|
| Research & document understanding | `research` | 6 | Cited lookup → supersession → joined evidence → conflicting authority → injected source instructions → missing final evidence |
| Data analysis & databases | `data_analysis` | 6 | SQL filter → aggregation/join → refund fanout → tied rankings → deduplicated latest events → cumulative reconciliation |
| Reasoning & planning | `reasoning` | 6 | Arithmetic → precedence → assignments → constrained optimum → infeasibility → robust contingent policy |
| Workflow & API automation | `workflow` | 6 | Create → versioned update → pagination → idempotent retry → conflict recovery → compensation after failure |
| Translation & multilingual | `translation` | 6 | Short Spanish notice → French placeholders → German register/negation → Japanese UI terms → Arabic numeric fidelity → quoted injection/ambiguity |
| Audio & speech | `audio` | 6 | Clean speech → correction → two speakers → noise/arithmetic → interrupted correction chain → missing authorization and quoted instruction |
| Image generation | `image_generation` | 6 | Single object → exact spatial arrangement → typography → material/lighting → dense relationships → triptych continuity |
| Video generation | `video_generation` | 6 | Simple motion → causal sequence → camera tracking → occlusion → collision → multi-shot continuity |
| General writing | `writing` | 6 | Announcement → plain-language rewrite → balanced synthesis → audience adaptation → conflicting notes → decision memo |
| Creative work | `creative` | 6 | Vignette → character voice → subtext → earned mystery → consistent perspectives → branching narrative |

Difficulty is **designed, not empirically calibrated**. Six items per card are
an initial evaluation set, not a statistically reliable universal leaderboard.
Retain category, level, task, model, modality and evaluation method in reports;
never average all ten categories into one undifferentiated accuracy score.

## Files and offline commands

- `data/routing_grid_v1/tasks/`: one public task descriptor per case.
- `data/routing_grid_v1/oracles/`: evaluator-only answers, reference SQL,
  simulator examples and human-review rubrics.
- `data/routing_grid_v1/assets/`: six actual 16 kHz mono PCM speech recordings.
- `data/routing_grid_v1/manifest.json`: ordered inventory, descriptor/oracle
  hashes, grading source hash and speech provenance. Each audio descriptor
  binds its WAV hash.
- `scripts/routing_grid_suite.py`: catalog, capability checks, offline grading
  and category/level summaries.
- `scripts/routing_grid_benchmarks.py`: list, safe input export and assessment.

Python 3.11+ is required for bounded SQL execution. Text listing/export and
non-SQL assessment use the standard library. Generated PNG validation needs
Pillow; generated MP4 validation needs ffprobe and ffmpeg. Missing evaluator
dependencies are **grader errors**, never quality rejections. Committed audio
plays on Linux/macOS; it does not require macOS voices at evaluation time.

```bash
# Read-only catalog, all 60 tasks; never contacts any inference service.
python3 scripts/routing_grid_benchmarks.py --list-tasks
python3 scripts/routing_grid_benchmarks.py --category research --list-tasks

# Fresh directory only. Exports prompts, source data and actual audio files;
# excludes answers, reference SQL, source speech transcripts and other oracles.
python3 scripts/routing_grid_benchmarks.py --export /tmp/routing-grid-inputs

# Grade a completed response produced by an external harness.
python3 scripts/routing_grid_benchmarks.py \
  --task grid_research_l1 --response /path/to/response.txt

# Inspect a generated file and obtain the binding for semantic review.
python3 scripts/routing_grid_benchmarks.py \
  --task grid_image_generation_l1 --response /path/to/response.txt \
  --workspace /path/to/generated-files

# Finish a human evaluation, preserving the exact emitted review binding.
python3 scripts/routing_grid_benchmarks.py \
  --task grid_image_generation_l1 --response /path/to/response.txt \
  --workspace /path/to/generated-files --review /path/to/review.json
```

The registry also exposes `suite_task_catalog("routing-grid")`. Legacy inference
runners deliberately reject this suite: they cannot silently send speech as text
or treat descriptions of generated media as actual output. This addition is a
**fixture/export/grading pack**, not a new DarwinRouter execution adapter. A
future authorized campaign must connect a modality-capable adapter to these
contracts. None of the examples above executes a model or updates its ranking.
Exit status is 0 for pass/needs-review and 1 for other assessment verdicts; always
read the explicit verdict, since `needs_review` is not a pass.

## Grading contracts

### Objective JSON

Research, reasoning and audio require one bare JSON object with the exact keys,
value types and facts requested. Fences, duplicate keys, non-finite numbers,
prose wrappers and extra fields fail. Strings normalize Unicode NFC and
whitespace only. Numbers do not accept booleans, numeric strings or float
substitutes for integers. Citations must identify the minimal supporting source
set specified by the task. Null is required where the task asks for abstention.
A changed factual field cannot be rescued by plausible explanatory prose.

Audio evaluates understanding/extraction from **actual audio input**, not OCR
of a transcript or text reasoning over a supplied script. Speaker changes,
noise and corrections increase difficulty. The evaluator scripts are private.
The six files use synthetic English Samantha/Daniel voices and deterministic
low-level noise in levels 4–6. Level 6 states that authorization is absent;
it does not require hallucinating a value or interpreting a silent redaction.
These are not a test of all accents, real meetings, music, TTS or simultaneous
overlapping speech. Speech generation would be a separate subprofile.

### SQL

The model returns `{"sql":"..."}`. The evaluator creates a fresh in-memory
SQLite database from the frozen tables. It permits read-only queries and uses
authorization, query-size/value-size/VM limits, a progress budget and a row cap.
It forbids mutation, attachment, extensions and external file access. It never
executes generated shell or Python code. Queries must produce the right rows
in the requested order on the visible fixture and two hidden variants that
change values and introduce additional rows; hardcoded visible results fail.
Equivalent SQL is accepted. Integer cents avoid floating-point ambiguity.
This is a bounded local evaluator, not a general hostile-SQL security sandbox.

### Workflow

The model returns `{"calls":[...]}` using the documented simulated API. The
evaluator executes those calls against an in-memory state machine and records
results/effects. Version reads, complete pagination, idempotency and rollback
must actually satisfy the simulator; unsupported operations, wrong arguments,
duplicate writes and incomplete final states fail. An ambiguous timeout after
a charge is explicitly an applied effect; retrying with a different key fails.

This tests **structured workflow semantics**, including injected data handling.
It does not claim that the model emitted native SDK tool calls, responded to
live intermediate tool messages, or administered a real API. A future native
tool-interaction track must use a separate profile and trusted event transcript.
There are no real accounts, network endpoints or external side effects here.

### Human-reviewed language and media

Translation must preserve meaning, numbers, negation, uncertainty and register;
valid alternative translations are welcome. It is not graded by matching one
reference sentence. Writing must preserve source facts and meet the audience,
structure and length contract. Creative work requires coherence, originality
and compliance with its explicit brief. Automated checks enforce specified
literal preservation and writing/creative word counts; passing these checks
only produces **needs_review**. Word counts use whitespace-separated tokens,
including labels; this limit is not applied to translation in Japanese/Arabic.

Generated images must be actual decodable static PNGs, at least 512 × 512.
Generated videos must be decodable MP4s, at least 512 × 512, 4–12 seconds and
at least 12 fps. A 100 MiB artifact cap, image/frame pixel limit, safe path
checks, local-only media protocols and bounded decode time apply. Artifact
inspection rejects missing, malformed, undersized, linked or outside-workspace
files. It does not prove model generation provenance. Blank media remains
**needs_review**, not pass. A description, SVG, code or renamed text file cannot
stand in for generation.

Review each rubric dimension using these anchors:

| Score | Anchor |
|---:|---|
| 0 | Missing, irrelevant or fundamentally wrong |
| 1 | Major failures; most requested behavior absent |
| 2 | Partially successful, but at least one material failure |
| 3 | Meets all material requirements; only minor polish issues |
| 4 | Fully meets requirements with excellent clarity/craft |

Every dimension must score at least **3** to pass. A critical factual error,
wrong relationship/count, lost negation, invented missing value or broken
narrative constraint is material and must score at most 2 in its dimension.
Averages cannot mask a failed dimension. Scores require a reviewer identifier
and specific evidence (quotes, visible regions or video timestamps). Use a
qualified target-language reviewer for translation. Prefer blinded, independent
reviewers and adjudicate disagreements before recording final feedback.

A review file has this structure; copy `review_binding` from the assessment
result unchanged into `binding` and use the exact emitted dimension names:

```json
{
  "binding": {"task_id":"...", "suite_version":"routing-grid-v1", "task_sha256":"...", "oracle_sha256":"...", "response_sha256":"...", "artifacts":{}},
  "reviewer":"reviewer-identifier",
  "scores": {
    "semantic_fidelity":{"score":3,"evidence":"Specific cited evidence from this submission."},
    "language_quality":{"score":3,"evidence":"Specific cited evidence from this submission."},
    "task_constraints":{"score":3,"evidence":"Specific cited evidence from this submission."}
  }
}
```

The binding includes hashes of the task, rubric, exact response and every
submitted artifact. Changing any bound content invalidates the review.
An invalid/missing review is never a model-quality failure: missing reviews
remain pending; invalid reviews are grader errors. Reviews are trusted evaluator
input, not a field the candidate model is allowed to submit.

## Routing and future execution contract

All new cases use their explicit category domain and `benchmark-v1` evidence
profile; the fixture/grader version is separately `routing-grid-v1`. The six
new specialist cards already request `benchmark-v1`. Existing Image generation,
Video generation, Writing and Creative cards currently request `default`:
a future integration must explicitly map those cards to measured
`benchmark-v1` evidence. **Do not relabel the new results as default or silently
add a fallback.** This patch does not change the web UI or implement the
backlogged subtask selectors.

Before any future dispatch, the campaign must:

1. Snapshot fresh provider-advertised capabilities for the actual selected
   model. Unknown/unsupported models are disqualified before dispatch. Model
   names or historical quality samples are insufficient. Use `eligible()` as
   a conservative gate: audio **input** is separate from TTS, and vision/OCR
   capabilities do not imply image/video generation.
2. Capture run, actual provider/model identity, input hashes, receipt of the
   real audio payload where relevant, model output/artifact provenance,
   timing, status and errors. Export only model-visible task directories;
   never mount the repository/oracle directory in a candidate workspace.
3. Preserve local/cloud policy, reservations and context/deadline rules.
   Unsupported generation capability is a disqualification, not a forced
   text-only fallback. Do not reuse an old campaign or broaden authorization.
4. Grade only completed execution. Supply trusted execution status separately
   from candidate content. Timeout, admission, stream/provider and context
   failures stay infrastructure outcomes even if a saved artifact looks right.
   `assess()` does not itself verify a real dispatch or write feedback.
5. Keep pass, mismatch, infrastructure, disqualified, grader-error and pending
   human-review totals distinct. `summary()` rejects duplicate task results
   and mixed grader versions; use one model/run at a time and retain per-level
   counts. Record quality feedback only after execution/provenance validation
   and any required human review, for the actual completed model.

Different modality adapters will be needed for speech input and genuine media
output; current text-only DarwinRouter surfaces must not pretend to support
these. Automatic model selection measures the selected models only, not an
exhaustive ranking of every eligible model. Future benchmark runs can learn
routing from this set, but held-out generalization needs separate unseen tasks.

## Maintenance and validation

These fixtures are original synthetic material. No live web pages, copyrighted
source documents, external accounts or personal recordings are needed. Once
used for measured feedback, preserve the manifest and use a new version for
prompt, asset, rubric or grader changes. Do not regenerate audio during a run;
voice rendering may differ across OS versions, so the committed hashes identify
the actual test inputs.

`generate_routing_grid_fixtures.py --output NEW_DIRECTORY` is an authoring tool,
requires macOS `say`, and refuses existing destinations. It does not contact an
LLM. The frozen pack is consumed without this generator.

```bash
python3 -m pytest -q tests/test_routing_grid_suite.py
python3 -m pytest -q
python3 -m ruff check scripts/routing_grid_suite.py scripts/routing_grid_benchmarks.py \
  scripts/generate_routing_grid_fixtures.py scripts/benchmark_tests tests/test_routing_grid_suite.py
python3 -m mypy
```
