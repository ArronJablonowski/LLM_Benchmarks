# Benchmark test components

The core profile is a registry of one JSON component per benchmark test in
`scripts/benchmark_tests/core/`.  The Direct Ollama, Hermes, and OpenClaw
runners all load exactly that registry; harnesses supply transport, telemetry,
and model lifecycle only.

## Component contract

Each file name must equal its `id` (for example, `math500_mini.json`). Every
component contains these fields:

```json
{
  "id": "example_mini",
  "family": "Example",
  "category": "reasoning",
  "name": "Human-readable title",
  "prompt": "The prompt sent to each harness.",
  "grading": {"kind": "final_answer", "expected": "42"}
}
```

Supported grading kinds are:

- `exact` with a string `expected`
- `final_answer` with a string `expected`; answers must terminate in `FINAL:`
- `json` with `expected`, and optional `strict_json`, `exact_json_keys`, and
  `compact_json` booleans
- `python` with `fixture` set to `private_ipv4` or `count_unique_ips`, and an
  optional positive `line_limit`

Set `requires_image: true` and `image_text` for a vision component. The shared
OCR asset and each harness's transport remain outside the descriptor so test
content is independent of the agent/API used to execute it.

## Adding or replacing a test

1. Add or replace one JSON file under `scripts/benchmark_tests/core/`.
2. Keep the filename and `id` identical, and update `CORE_TASK_ORDER` in
   `scripts/benchmark_tests/registry.py` when changing the core suite's
   execution order.
3. Run `python3 -m unittest discover -s tests -v` and each runner's
   `--list-tasks` command. Registry validation fails closed on malformed,
   duplicate, missing, or unlisted components.

For a new grading semantic, add a deliberately reviewed grader in
`scripts/accuracy_grading.py`, then add an explicit registry adapter rather
than embedding executable code in a component file. This preserves the suite's
deterministic, dependency-light execution and review boundary.
