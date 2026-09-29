# Progressive OCR and document-image suite

`--suite ocr` adds **30 original, synthetic image tests**, ordered as six levels of five tasks. It leaves the existing 18-task Standard suite and all historical observations untouched. Each task sends a different frozen 1200 × 1550 PNG. These are rasterized Word-style documents and visual diagrams, not DOCX text extraction or text pasted into a prompt.

| Level | Image difficulty | Added demands |
| --- | --- | --- |
| 1 — clean | Large, high-contrast print | Short memo, form fields, small table, bar chart, simple flowchart |
| 2 — formatted | Smaller type, longer document | Mixed serif/sans fonts, dates, decimals, confusable identifiers |
| 3 — layout | Main body plus sidebar; larger table | Reading regions, distractors, additional diagram steps |
| 4 — scan | Page skew, tint and lower contrast | Same five task families under scan conditions |
| 5 — degraded | Resampling, blur and compression | Longer memo, extra table rows and exact numeric reading |
| 6 — expert | Stronger degradation, seeded speckle and stamp | Redaction abstention, confusable glyphs, misleading instructions inside a table document |

Every level contains:

1. **Memo transcription:** read the main document body, preserve spelling/punctuation/numbers, and extract named fields. Sidebars and footnotes are separate regions.
2. **Form extraction:** read identifiers, dates, money and checkboxes. A fully redacted field must return `unreadable`, never an invented value.
3. **Table understanding:** extract a cell, identify the largest count and compute a filtered total from the visible rows.
4. **Chart analysis:** associate numeric labels with bars, identify the maximum and compute a difference.
5. **Diagram analysis:** follow labeled arrows and distinguish `>` from `>=`, including additional steps at harder levels.

Difficulty increases by design. It is not yet calibrated against model performance. These are synthetic English printed documents, not a claim of real-world handwriting, multilingual, camera-photo or multipage proficiency. Some field values recur across levels; always use fresh model contexts, and do not treat the later levels as a held-out generalization test. A separate unseen corpus is needed for that claim.

## Grading and evidence

Answers must be one JSON object containing exactly the requested string-valued fields. Code fences, duplicate keys, extra fields, wrong types and trailing prose fail the format contract. All fields must be correct to pass. Normalization allows Unicode NFC and whitespace differences only; case, punctuation, signs, decimal points and digit/letter distinctions are preserved.

The grader additionally records field accuracy and Levenshtein character/word error rates (CER/WER) for memo transcriptions. These diagnostic metrics do not relax the exact pass requirement. Missing fields or invalid JSON are format mismatches; they are not silently scored as zero transcription error. Transport failures and capability skips remain separate from model-quality pass/mismatch denominators.

The manifest binds every descriptor and image by SHA-256. Runners validate these before dispatch and record the OCR grader, manifest and selected-task hashes. Gold answers stay in local grader descriptors. Only the question and PNG pixels are sent to models. The agent runners copy each task's image to run assets; the direct runner references its frozen image and records its hash. Fixture mismatch aborts rather than substituting the legacy one-line OCR image.

## List tests without inference

```bash
python3 scripts/ollama_standardized_local_benchmarks.py --suite ocr --list-tasks
python3 scripts/hermes_agent_17_test_benchmarks.py --suite ocr --list-tasks
python3 scripts/openclaw_18_test_benchmarks.py --suite ocr --list-tasks
```

All three native vision runners accept `--suite ocr`. Continue using their documented model-selection, capacity and telemetry controls. Use fresh output directories. Text-only models skip image tasks. Do not infer image support from a model's name. The direct runner disallows paired/randomized OCR runs to preserve level order; ordinary single-arm runs execute levels 1–6 sequentially. Select individual IDs with `--tasks` when needed. No model campaign is launched by adding or listing this suite.

## DarwinRouter automatic OCR campaign

`scripts/darwinrouter_ocr_host.go` provides a dedicated trusted SDK image adapter. DarwinRouter owns automatic ranking, local-only admission, reservations, durable task events, stream parsing and actual-model identity. The adapter attaches the hash-verified PNG to the admitted Ollama request using DarwinRouter's supplied policy transport. This does **not** add image support to the daemon's public text API or its legacy Standard adapter.

Before every task, the host reads native Ollama capability metadata for configured local models. Only a fresh `ocr`, `image` or `vision` advertisement qualifies as image-reading support; names and manually configured labels are insufficient. Unsupported or unknown models lose OCR eligibility in a private per-attempt configuration, without changing normal settings or recording a quality rejection. Native `vision` is normalized to `ocr` for this campaign. The actual selected model is checked again immediately before image dispatch. GLM-OCR remains explicitly excluded under the prior operator cancellation.

The request uses domain `ocr`, profile `ocr-progressive-v1`, a fresh context, no tools, one response turn, a 32,768-token context and 4,096-token output ceiling. The conservative text-byte estimate is supplemented by a 16,384-token image reserve and output reserve; this is not an exact model-specific vision tokenizer. The input is restricted to these frozen 1200 × 1550 PNGs. Reservations, locality, routing weights and configured model context ceilings remain intact. Gold answers are never passed to the host. Normal `ocr/default` cards do not automatically become this distinct evidence profile.

Build and test the host from a compatible DarwinRouter checkout (the Go source depends on its SDK):

```bash
go test -race /absolute/benchmark/scripts/darwinrouter_ocr_host.go /absolute/benchmark/scripts/darwinrouter_ocr_host_test.go
go build -o /absolute/private-output/darwin-ocr-host /absolute/benchmark/scripts/darwinrouter_ocr_host.go
```

Prepare a new campaign and then run its single supervisor:

```bash
python3 scripts/darwinrouter_ocr_campaign.py /absolute/new-campaign --init \
  --host /absolute/private-output/darwin-ocr-host --config /absolute/config.yaml \
  --database /absolute/shared/darwin.db --darwin /absolute/bin/darwin
python3 scripts/darwinrouter_ocr_campaign.py /absolute/new-campaign
python3 scripts/darwinrouter_ocr_campaign.py /absolute/new-campaign --poll
```

Supply the same secret lookup environment and `DARWIN_PROCESS_OWNER_DIR` as the running daemon, without logging secrets. The private configuration disables SDK-managed model unloading because shared process admission rejects that peer-unaware controller. Shared admission stays enabled, and no unrelated resident is unloaded. Never run competing supervisors. A file lock, append-only launch ledger, exclusive attempt directories, pinned source/image/grader hashes and immutable results prevent ambiguous retries. Resume repairs missing feedback from completed evidence; it never silently reissues an ambiguous launch. Only one fresh infrastructure retry is allowed. Two failed attempts require a reviewed, hash-bound exclusion before continuation. Do not weaken grading, increase limits or turn partial answers after failed execution into passes.

Feedback is written only after independently verifying a completed durable task, its local-only domain/profile, image handoff, actual provider/model and strict grade. Failed outer and SDK recovery lineages must have zero quality feedback. Current evaluation heads are checked read-only after feedback. The report and snapshot distinguish quality mismatches, infrastructure failures, exclusions and active inference. Campaign completion still requires an independent final audit; adapter tests alone do not constitute OCR results.

After a native vision run:

```bash
python3 scripts/summarize_ocr_results.py path/to/run.jsonl > path/to/ocr-summary.json
```

The summary separates each model and level, lists pass/mismatch/infrastructure/skip counts, and calculates quality rates from valid cases only. It rejects missing/mismatched image evidence and duplicate observations. No routing feedback is written.

## Review and reproducibility

Open `data/ocr_progressive_v1/gallery.html` to inspect every image and its task. Gold answers are in `scripts/benchmark_tests/ocr/*.json` and should not be shared with models. All names, organizations and values are invented.

Pillow is needed only for regeneration, not for listing, dispatch or grading. The generator uses local Arial and Times New Roman fonts (not redistributed); the manifest records their hashes and Pillow version. Rebuild into a scratch directory with the same dependency/font versions, compare hashes and visually review before deliberately releasing a new fixture version:

```bash
python3 scripts/generate_ocr_fixtures.py --output-root /tmp/ocr-rebuild \
  --font '/System/Library/Fonts/Supplemental/Arial.ttf' \
  --serif-font '/System/Library/Fonts/Supplemental/Times New Roman.ttf'
```

Never overwrite fixtures associated with a completed campaign. Changes to prompts, images or grading rules require a new profile and new observations.
