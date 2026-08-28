# Packsmith

`packsmith` builds `.tar.gz` release archives from a source directory and can
optionally run a post-build verification command. The current implementation
is unsafe and nondeterministic. Harden it according to the benchmark issue.

Usage: `python -m packsmith SOURCE OUTPUT [--dry-run] [--verify PROGRAM ARG ...]`
