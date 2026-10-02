# DGX Spark campaign result snapshot

This snapshot was exported on October 2, 2026 from the completed campaigns
through September 29, 2026. `manifest.json` lists source file paths relative
to the Spark campaign root, row counts, and SHA-256 hashes for each CSV.

The CSVs retain only an allowlist of campaign, model, harness, task, outcome,
grading-count, timing, and maximum-GPU-temperature fields. They exclude model
responses, prompts, stderr, commands, environment settings, and credentials.
Full canonical JSONL evidence remains host-local on the Spark.

A clean pass requires both `status=ok` and `verdict=pass`. Creative records
use `status=submitted` and remain ungraded. Skips, missing pairings, resource
incompatibility, and run errors must not be counted as passes. Model/task
duplicates within a campaign/harness retain the latest observed row in source
files ordered by modification time and then path. The dedicated OCR campaign
is separate from standard text; `ocrbench_mini` is excluded from the text CSV.

| File | Observations | Clean passes | Notes |
| --- | ---: | ---: | --- |
| `standard_text.csv` | 1,456 | 974 | 17 non-OCR core tasks; coverage varies |
| `ocr.csv` | 87 | 35 | One task; 49 skipped observations |
| `coding.csv` | 865 | 117 | Original three harnesses plus the selected Pi panel |
| `creative.csv` | 486 | — | 434 submissions awaiting human review |
| `cybersecurity.csv` | 3,191 | 739 | Five harnesses; some incomplete pairings |
| `commandline.csv` | 4,080 | 926 | Complete 34-model × six-harness × 20-task campaign |

The standalone reports under `docs/reports/` explain ranking rules and
cross-campaign comparability limits. Older development runs, recovery copies,
pilots, and stopped campaigns are not included in these result snapshots.
