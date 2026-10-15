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
