# Native NexusRouter coding command isolation

The native coding host has no unrestricted-shell fallback. File tools remain
rooted in `os.Root`; command tools require a successful real isolation preflight
before SDK initialization/model inference. `--check-isolation` performs only a
bounded Python/SQLite/tempfile/heredoc sandbox probe. The Python campaign runner
calls it before creating evidence/workspaces, sampling, or unloading owned models.
A nonzero exit or a 30-second timeout aborts the runner. `--finalize` has no coding
tools and does not require a command sandbox.

## macOS

The existing `/usr/bin/sandbox-exec` policy and `/bin/zsh -f` implementation remain.
Existing runtime read grants and workspace/private-home write grants are unchanged.
This is the existing macOS boundary, not the narrower Linux runtime view below.

## Linux backend contract

Requires `/usr/bin/bwrap` with FD bind mounts, working mandatory user/mount/PID/
network/IPC/UTS namespaces, nested-user-namespace disabling, and capability drops.
No `*-try` namespace fallback, shared network, host root bind, or privileged
security-policy adjustment is used. The real preflight compares the new namespace
identities with the host identities as well as executing Python and SQLite.

The filesystem starts empty. Workspace and private scratch directories are pinned
with open descriptors and mounted writable; the root is read-only after setup.
Bind descriptors are consumed by Bubblewrap, not intentionally passed to the task.
A fresh PID-scoped procfs and minimal private device filesystem are provided.
Task tempfiles use the private scratch HOME/tmp; no host /tmp, /proc, /dev, /run,
credential directory, other workspace, or entire /usr is mounted. Absolute host
workspace paths have empty ancestor directories in the sandbox; those are not
binds of the corresponding host home directories.

Runtime reads are restricted to root-owned, non-group/world-writable system files:
a fixed allowlist of shell/basic POSIX utilities, system Python, SQLite, their
`ldd`-resolved shared libraries, and `/usr/lib/python3.X` standard library. Python
extension dependencies are included. No user package directories, inherited PATH,
loader/Python overrides, proxy variables, shell startup hooks or tokens are
inherited. A task receives a fixed PATH, private HOME/TMPDIR, locale and Python
bytecode/user-site controls. The host environment is also sanitized for dependency
inspection. This initial backend targets distribution Python/SQLite and basic
shell workflows; npm, Node, Go, third-party Python packages, package installation
and general development toolchains are not provided or qualified by this change.
The full nine-task coding campaign is not qualified on Linux.

Commands retain the two-minute context deadline and two-second pipe wait limit.
Combined output is now buffered as a bounded 64-KiB tail, rather than collecting
unlimited output before truncation. Owned descriptors close after execution and
on setup failures. The process group is killed on cancellation and after normal
completion; the Linux PID-namespace init also terminates surviving namespace
children, including children which create a new session. The workspace directory
and subdirectory validation remain unchanged, and host path/symlink replacement
cannot change the pinned bind source.

This is namespace isolation, not a VM, seccomp syscall allowlist or disk/CPU/RAM
quota. It relies on trusted OS runtime files, a patched kernel and an exclusively
owned fixture/scratch tree. Do not populate fixtures with credentials, host
sockets or external hard links. It does not change SDK admission, the existing
4-GiB reserve, model-service configuration, host namespace/security settings,
other native hosts, campaign evidence, grades, or feedback.

## Spark verification (2026-10-15)

Pinned benchmark base: `492d349b17b50c1db5dd2e064063ee55249afd6b`.
Pinned compatible SDK: `4fd3002d4c8e99c3044ce364ece2143d72e5e218`.
Build using `source ~/.local/share/llm-benchmark-native/spark-nexus-environment.sh`;
the external Go workspace replaces legacy `github.com/ArronJablonowski/DarwinRouter`.

**Linux execution is NOT qualified on this Spark.** Bubblewrap 0.9.0 fails with
`loopback: Failed RTM_NEWADDR: Operation not permitted`; separate probes also
report `setting up uid map: Permission denied` / `unshare: write failed
/proc/self/uid_map: Operation not permitted`. These establish unavailable required
namespace operations; the exact enforcing security layer has not been identified.
No sysctl, AppArmor, capabilities, service, trust or other host safeguard was changed.
A supported, administratively approved isolation environment is required to finish
Linux qualification; do not work around this by sharing host namespaces.

Verification results:

- Native Linux ARM64 build and `go vet` pass through the configured Go workspace.
- Native coding-host race tests pass (3.533s), including fail-closed policy detection,
  runtime mount/environment plan and bounded output buffering. Linux SQLite,
  heredoc, adversarial execution and background cleanup tests explicitly SKIP
  because the sandbox cannot start; those skips are verification gaps, not proof.
- Existing macOS command confinement/network/environment, directory/symlink,
  SQLite and heredoc regression tests execute and pass on an actual macOS ARM64
  host using a Darwin test executable built from the same source. This was not
  race-enabled on macOS; Linux race checks are separate.
- Full Python suite: 443 passed, 335 subtests passed (11.29s).
- Ruff: all checks passed. Mypy: no issues in 3 configured source files. Ruff 0.17.1
  and mypy 2.4.0 were installed only into a private verification venv; system
  packages were not changed.
- One bounded synthetic coding smoke was attempted with the existing small
  `smoke-qwen` configuration and a fresh arithmetic fixture/database path. It
  failed closed before inference; the implementation stayed unchanged and no
  database was created. **Actual Linux model edit and behavioral test execution
  remain unverified.** No benchmark campaign or grades were produced.

Remote logs are under `~/.local/share/llm-benchmark-native/verification/`:
`coding-linux-race.log`, `coding-linux-python.log`, `coding-linux-lint.log`,
`coding-linux-types.log`; the synthetic smoke has its own
`coding-linux-synthetic-*/smoke-result.json`. Existing `coding-test.log` is retained.
The candidate executable is `verification/coding-linux-candidate`, not installed
over an existing campaign host. Do not enable a campaign until real Linux success,
escape, host-socket/network, symlink/FD, and background cleanup tests all execute
and the synthetic model performs a verified edit and nonzero behavioral tests.

## Source checkpoint and changed files

Tested code checkpoint: `201f59b7833b9da72bd74dfd4f902becc4826a1f`, on branch
`codex/linux-native-coding-isolation` in the Spark benchmark repository. The SDK
checkout remains at its pinned commit with no local modifications. The candidate
Linux executable SHA-256 is
`00e9fda4853012eede6ef6d6ef95c605e88a32c6bae25045a5b15dd601d0a59e`.

Changed files:

- `scripts/nexusrouter_coding_host.go`: OS dispatch, existing macOS sandbox,
  Linux runtime mount plan, mandatory real preflight, bounded output and cleanup.
- `scripts/nexusrouter_coding_host_test.go`: Linux policy/static-plan/adversarial/
  background tests and explicit unavailable-execution skips; macOS regressions retained.
- `scripts/nexusrouter_coding_benchmarks.py`: pre-inference preflight and timeout failure.
- `scripts/test_nexusrouter_coding_ownership.py`: verifies preflight failure cannot
  prepare workspaces/evidence, sample, unload models or dispatch model work.
- `docs/NATIVE_CODING_ISOLATION.md`: platform contract, limits and actual verification.
- `docs/DEVELOPMENT.md`: link to platform documentation.

At the initial checkpoint, publication from Spark was blocked by its available
GitHub credentials. The committed changes were backed up in a Git bundle in both
hosts' private verification directories. No production/campaign executable was
replaced. The subsequent VM verification below does not qualify direct execution
under the physical Spark host's unchanged security policy.

## Subsequent isolated-VM qualification (2026-10-15)

The same candidate binary, SHA-256
`00e9fda4853012eede6ef6d6ef95c605e88a32c6bae25045a5b15dd601d0a59e`,
was tested in a Debian 13 ARM64 VM on Spark. QEMU TCG software emulation ran
inside a non-root Docker container with its default seccomp/AppArmor protection,
all capabilities dropped, no-new-privileges, a read-only container root and no
device/KVM passthrough. The guest had four virtual CPUs and 20 GiB RAM; the
container was capped at four CPUs and 24 GiB RAM with no additional swap.
The Debian cloud image was verified against its published SHA-512 checksum.

No host directories were shared inside the guest. QEMU restricted guest egress;
management used authenticated SSH through the container. The only model-service
connection was a temporary SSH reverse tunnel from guest loopback to Spark's
Ollama loopback port. The test account had no sudo privileges. No host sysctl,
AppArmor, capability or other security safeguard was relaxed.

Verification results:

- The actual isolation preflight succeeded in the guest.
- The race-enabled ARM64 qualification test binary passed. Linux SQLite,
  heredoc, runtime/environment, adversarial escape/network/socket/symlink/FD and
  background-cleanup tests executed successfully; only the two macOS-specific
  confinement/directory tests skipped. These were executions, not capability skips.
- A fresh synthetic arithmetic fixture initially failed all three behavioral
  tests. Through the native coding host, local `qwen3:8b` changed `add(a, b)`
  from subtraction to addition and invoked the isolated command tool. Its durable
  confirmed tool event records three tests run, all passing, with exit code zero.
  The test-file SHA-256 was unchanged, and an independent rerun also passed all
  three tests. The model did not create the requested optional output log; the
  durable tool result, not its final prose, is the execution evidence.
- The smoke was limited to eight turns, a 150-second host task deadline and a
  240-second outer deadline. A physical-host memory monitor enforced the 4-GiB
  reserve. The model service ran on Spark, not inside the guest; guest admission
  measurements alone do not represent the physical inference host's resources.
- No full campaign started, no existing grades/evidence changed and no production
  harness executable was replaced. Original command-line evidence checksums
  remained unchanged. The test model was released and the VM retained stopped
  with no automatic restart.

Scope: this qualifies the tested Python/SQLite command runtime **inside this
particular VM**, not direct physical-host execution, a universal sandbox-security
claim, a model-performance comparison or the full nine-task coding campaign.
Node/npm, Go and third-party package workflows remain unqualified. The ordinary
host runner is not automatically redirected into the VM.

Private operational evidence on Spark is retained under
`~/.local/share/llm-benchmark-native/qualification-vm/`, including
`qualification-receipt.json`, `guest-qualification.log`,
`independent-behavioral-tests.log` and `guest-smoke-evidence.tar.gz`.
VM disks, private keys, databases, raw session evidence and the separate
NexusRouter application/SDK are not part of this benchmark-suite publication.
