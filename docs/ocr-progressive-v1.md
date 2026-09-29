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

**DarwinRouter integration is not yet qualified.** Its current Standard adapter accepts text tasks only and explicitly excludes image tasks. These direct Ollama / Hermes / OpenClaw observations must not be presented as DarwinRouter end-to-end OCR results or written into its routing feedback. Native DarwinRouter image input, actual-provider image delivery and feedback attribution must be implemented and tested before an OCR campaign can populate its grid. This change does not enable GLM-OCR or restart cancelled diagnostics.

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
