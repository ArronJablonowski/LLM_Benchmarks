# Streamlog

Implement the public API in `streamlog/pipeline.py`. `process(records,
secret_fields)` returns `(normalized_iterator, summary, errors)` without
eagerly consuming `records`. See the benchmark issue for full requirements.
