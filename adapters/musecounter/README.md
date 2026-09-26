# Muse exact-accounting adapter (integration in progress)

This isolated module packages the offline prototype. The coding host enables it only when DARWIN_BENCH_MUSE_IDENTITY names an explicitly accepted identity/asset configuration; the default remains conservative byte accounting. The local Darwin replace path is development-only and must be recorded in build provenance. Ollama renderer/tokenizer dependency is pinned at v0.32.15 with compiled checksum verification.

The provider probe targets this specific macOS validation host. Binary and manifest identity gating plus preserved transcript parity do not establish general serving-build source certification. Startup must explicitly supply accepted identity and verified tokenizer assets and install the factory through SDK ContextEstimatorFactory. Never enable by model name alone.

Private transcript fixtures remain in Darwin work/muse-offline-counter and are not copied here. Actual tokenizer encoding remains non-interruptible; estimator execution is bounded by Darwin EstimateWith. The host installs the factory through the validated effective SDK configuration before admission.

Build and test the host from this module directory so its pinned dependencies and local Darwin replacement apply:

```sh
go test -race ./...
go test -race ../../scripts/darwinrouter_coding_host.go ../../scripts/darwinrouter_coding_host_test.go
go build -o /absolute/path/to/candidate ../../scripts/darwinrouter_coding_host.go
```

Record both repository revisions and the candidate SHA256 before replay. The opt-in JSON contains `Identity`, `TokenizerPath`, and `ConfigPath`; startup rejects unknown fields, trailing data, files over 16 KiB, identity mismatch, and unverified assets. The Python runner inherits the opt-in environment variable. Keep host-specific identity files and private transcript fixtures outside the repository. Startup verification alone is not a completed coding benchmark.
