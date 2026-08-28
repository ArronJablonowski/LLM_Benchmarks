# Layered Config

`layered_config.merge(base, overlay)` combines configuration layers. A recent
regression broke recursive merges and mutates caller-owned data. See the issue
provided by the benchmark agent prompt.

Run tests with `python -m unittest discover -s tests -v`.
