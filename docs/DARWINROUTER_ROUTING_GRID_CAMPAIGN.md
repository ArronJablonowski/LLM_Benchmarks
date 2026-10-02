# DarwinRouter routing-grid campaign

`scripts/darwinrouter_grid_campaign.py` is the sole durable supervisor;
`scripts/darwinrouter_grid_host.go` runs each case through the DarwinRouter SDK.
The frozen routing-grid-v1 fixtures and grader remain unchanged.

## Scope and learning

Fresh provider metadata determines eligibility. With the current catalog,
16 local completion models receive the same 42 text cases, one advertised audio
model receives six audio cases, then automatic routing receives 48 cases:
726 planned executions. Twelve image/video generation tasks are unavailable;
text descriptions never substitute for generated media. GLM-OCR and cloud
providers remain excluded. These are single matched observations per task/model,
not statistically conclusive universal rankings. One audio model cannot establish
a comparative audio leaderboard.

Objective direct comparisons run before objective automatic routing. Language
and creative comparisons follow; their automatic phase waits for all completed
direct outputs requiring human review to receive bound reviews. Deterministic
contract failures can be rejected immediately. Rubric-dependent outputs remain
pending, never synthetic passes or model-quality failures. Supply a qualified
reviewer's `human-review.json` in the attempt directory using the frozen suite's
review binding; resume the wrapper to reconcile without repeating inference.
Existing final grades are immutable; corrections need an explicit revision flow.

Feedback uses the actual completed provider/model under each category's
`benchmark-v1` profile. This does not automatically populate UI cards still
requesting a different profile. Workflow measures simulated call-sequence
semantics, not native tool interaction. Auto measures informed selection on the
same cases, not held-out generalization.

## Input and execution guarantees

Public requests contain prompts and source inputs, never private oracles, source
speech transcripts or reference SQL. Audio sends the actual pinned 16 kHz mono
PCM WAV through the admitted policy transport. Ollama 0.34.4 uses its native
`messages[].images` byte field for audio and advertises `audio`; this is verified
by its [upstream audio integration test](https://github.com/ollama/ollama/blob/v0.34.4/integration/audio_test.go).
Audio requests set `think=false`. Generic image capability never qualifies audio.

Every dispatch rechecks native capability. Local-only policy, configured memory
reservations and shared process admission remain intact. Private configuration
disables SDK-managed residency and delegation. Context is 32768, output cap4096,
audio reserve16384, one response turn, no tools, 900-second deadline. The deadline
is not an ETA. A model absent from the fresh resident list gets request-scoped
`keep_alive=0`; preexisting residency is preserved. There is no manual unloading.

Requests, input hashes, manifest/source/binary/config/model digests, launches,
SDK events, dispatch evidence, results, grades and current feedback heads are
recorded. Ambiguous launches block instead of repeating inference. At most two
fresh outer attempts are allowed. Failed SDK lineage receives zero quality
feedback. Two failures require reviewed infrastructure disposition binding both
canonical hashes; no third attempt. Zero-dispatch capacity deferrals have separate
hash-bound evidence and consume no inference attempt. Safe restarts reconcile
existing results and missing feedback before launching anything new.

## Operation

Build the host against the current DarwinRouter module, run its explicit-file Go
race tests and the Python suite, then initialize a fresh campaign:

```sh
python3 scripts/darwinrouter_grid_campaign.py /absolute/campaign --init \
  --host /absolute/darwin-grid-host-v1 --config /absolute/config.yaml \
  --database /absolute/darwin.db --darwin /absolute/bin/darwin
python3 scripts/darwinrouter_grid_campaign.py /absolute/campaign
python3 scripts/darwinrouter_grid_campaign.py /absolute/campaign --poll
```

Use the existing launchd secret environment and DARWIN_PROCESS_OWNER_DIR without
printing secrets. Inspect fresh processes and campaign.lock before any resume.
A `pause` file stops the supervisor between attempts. Never change pinned sources
or an active input in place. Investigate and preserve prior manifests before a
tested execution-contract revision. Preserve historical campaigns and feedback.

`latest.json` and `report.md` separate valid grades, pending reviews, infrastructure,
unavailable tasks and feedback. A campaign is not fully audited merely because
execution stopped. Final audit must independently verify immutable inputs,
capability/media handoff, grades, all failed-lineage zero-feedback and unique
current quality heads. Timing remains unknown until representative samples and
review/capacity holds are measured.
