---
change: 2026-09-04-windows-verification
stage: plan
scope:
  files:
    - docs/plans/2026-09-04-windows-verification.md
    - .harness/verification.yaml
    - .harness/derived-docs.yaml
    - src/super_harness/templates/verification_defaults.yaml
    - src/super_harness/templates/derived_docs_defaults.yaml
    - src/super_harness/core/shell_runner.py
    - src/super_harness/core/check_runner.py
    - src/super_harness/core/doc_check.py
    - src/super_harness/engineering/verification_config.py
    - src/super_harness/cli/adapter.py
    - src/super_harness/cli/verification.py
    - src/super_harness/adapters/framework/openspec.py
    - src/super_harness/cli/observe.py
    - src/super_harness/cli/lazy_group.py
    - src/super_harness/sensors/verification_runner.py
    - src/super_harness/daemon/supervisor.py
    - src/super_harness/daemon/server.py
    - src/super_harness/adapters/agent/_settings_merge.py
    - src/super_harness/core/anchor_scanner.py
    - src/super_harness/core/decision_check.py
    - src/super_harness/core/doc_refs.py
    - src/super_harness/core/file_lock.py
    - scripts/run_project_check.py
    - scripts/gen_cli_reference.py
    - scripts/gen_state_machine.py
    - docs/cli-reference.md
    - docs/architecture.md
    - tests/unit/core/test_shell_runner.py
    - tests/unit/core/test_check_runner.py
    - tests/unit/core/test_doc_check_engine.py
    - tests/unit/core/test_doc_check_loader.py
    - tests/unit/core/test_anchor_scanner.py
    - tests/unit/core/test_decision_check.py
    - tests/unit/core/test_doc_refs.py
    - tests/unit/engineering/test_verification_config.py
    - tests/unit/adapters/framework/test_openspec.py
    - tests/unit/sensors/test_verification_runner.py
    - tests/unit/cli/test_done.py
    - tests/unit/cli/test_verify.py
    - tests/unit/cli/test_doc.py
    - tests/unit/cli/test_observe.py
    - tests/unit/scripts/test_run_project_check.py
    - tests/unit/scripts/test_gen_cli_reference.py
    - tests/unit/scripts/test_gen_state_machine.py
    - tests/integration/cli/test_adapter.py
    - tests/integration/cli/test_verification.py
    - tests/integration/daemon/test_daemonize.py
    - tests/integration/daemon/test_framework_observer.py
    - tests/integration/daemon/test_observer_host.py
    - tests/e2e/openspec_claude_code/test_full_lifecycle.py
    - tests/e2e/conftest.py
    - src/super_harness/adapters/registry.py
    - src/super_harness/cli/decision.py
    - tests/unit/cli/test_review_runs.py
    - tests/e2e/test_pre_tool_use_claude_code.py
    - .github/workflows/test.yml
  tier_hint: Normal
---

# Execute native Windows verification honestly

## Authority, product obligation and boundaries

This is the independent repair change on `codex/2026-09-04-windows-verification`,
rebased onto `origin/main` at `da49e91` so the separate product-baseline change is
not part of this branch or its PR. Native
Windows, macOS and Linux execution is an existing product axiom: the checks must
have the same meaning, result categories and blocking behavior on all three
platforms. Windows support means native Windows / PowerShell operation without
WSL or Docker; a missing required runtime must fail explicitly and is not a
completed portability result.

The existing product-baseline change `2026-09-04-product-baseline` remains
`IMPLEMENTATION_IN_PROGRESS` on its own branch. Do not edit, merge, close, or
reconcile it. Existing untracked `.superpowers/` and `docs/research/` are not in
this change. Do not push, merge, publish, or edit any ratified decision body.

The previous plan's proposed Windows-only leading `NAME=value` rejection and
unchanged string-only configuration are explicitly superseded by the maintainer's
new direction. There is no old configuration to support: do not add a compatibility
layer, transitional syntax, OS-specific command guessing, or silent fallback.

## Observed failure and preserved evidence

The current `.harness/verification.yaml` sends
`PATH="$(pwd)/.venv/bin:$PATH" python -m pytest -q` to
`subprocess.Popen(..., shell=True)`. Native Windows `cmd` consumes the leading
assignment as its `PATH` builtin, launches no target, and returns zero with empty
stdout/stderr. The direct project-venv probe prints `TARGET_EXECUTED` and exits
23, while the current runner reports zero in roughly 20–23 ms. Ruff and mypy
have the same false-positive shape.

Retain the historical result exactly, without rewriting it:

```
.harness/verification-results/2026-09-04-product-baseline/2026-09-03T17-36-52.439925Z/summary.json
SHA256 f66229bb8a5d1db6a1b05e8fd8c3fbc40dbe1b030e6969f446538d0b06455c15
```

The three ratified decision checks remain locked and semantically POSIX shell:
`d-core-is-base`, `d-gh-cli-not-rest`, and `d-merge-gate-pure-git`. Their bodies,
counterexamples and hashes stay unchanged. The nine existing tier-2
`REVIEW-NEEDED` reminders and the three historical `l1_update_completed` unknown
events are preserved and reported, not bulk-reconciled.

## Explicit command contract

Introduce one typed command contract at the configuration boundary and use it for
all new direct subprocess calls:

* `command` as a non-empty array of non-empty strings means direct execution with
  `shell=False`. Each argument is preserved as an argument; spaces, non-ASCII
  paths, environment values and shell metacharacters are never reparsed.
* `command` as a non-empty string is accepted only with an explicit `shell: sh`.
  It runs as `sh -c <command>` through the shared shell path. A string without
  `shell`, an unknown shell, or `shell` attached to an array is a validation
  error. No OS-dependent shell inference is allowed.
* `env` is a structured string-to-string mapping and `workdir` is a structured
  path field. The existing scrubbed ambient environment plus defaults and
  per-check environment merge is retained; replacement environments must carry
  the required `PATH` explicitly.
* Interpolation remains allowlisted, but applies to every array element without
  joining or shell-quoting the array. String interpolation remains text
  substitution before the explicitly selected shell runs.

The parsed `CheckSpec` stores the array form immutably and records the explicit
shell separately. The loader rejects the old string-only rows; repository config,
adapter rows and tests are migrated in the same change. The historical result
archive is evidence only and is not migrated.

## Implementation slices

### 1. Shared process execution and truthful outcomes

Replace the implicit `run_shell` boundary with a shared `run_command` primitive
used by direct checks, explicit shell checks, decision checks and document
generators. It will:

* validate the command/shell combination, resolve a bare direct executable from
  the supplied `PATH` before `CreateProcess`/`exec`, and return a structured
  spawn error when resolution, cwd or launch fails;
* use `/bin/sh -c` on POSIX for `shell: sh`; on native Windows resolve Git for
  Windows from the installed Git layout, require its native `usr/bin/sh.exe` and
  `grep.exe`, append that utility directory after the caller's existing PATH,
  and never use `cmd`, WSL launchers or an unverified fallback;
* capture bytes first and decode the check contract as UTF-8 with replacement for
  ordinary check output. The document path can request strict UTF-8 and must
  classify invalid generator output as a generator failure;
* preserve environment replacement, stdout/stderr capture, accurate exit codes,
  durations and POSIX process-group cleanup;
* on Windows create a killable process group and use bounded native process-tree
  termination/reaping (with a direct-child fallback only as best effort). A
  timeout remains a timeout and cleanup errors cannot turn it into success or
  mask it with `AttributeError`.

The result model must distinguish: `pass` (started and exited 0), `fail` (started
and exited nonzero), `spawn_error` (not started), and `timeout` (started but not
completed). Decision `CheckRun` and verification `CheckResult`/summary/event
rendering will preserve that distinction. Existing POSIX behavior remains on the
same shared path.

`core.check_runner.run_one_check` will call the shared primitive with the explicit
`shell="sh"` contract. The native shell preflight checks the shell and required
Git POSIX utility runtime before running the three locked checks, so `! grep ...`
cannot pass merely because a missing `grep` was swallowed by shell negation. The
clean and counterexample sides remain real executions; no stderr scanning or
invented execution evidence is added.

### 2. Project verification configuration and interpreter selection

Update `engineering.verification_config`, the shipped template and the checked-in
`.harness/verification.yaml` to the union command schema. Migrate the three
self-host checks to direct argv through a new small `scripts/run_project_check.py`
launcher. The launcher resolves only the repository's `.venv/Scripts` or
`.venv/bin` toolchain, runs the requested tool/module without a shell, passes
through stdout/stderr and returns the exact child exit code. Missing project
runtime or tool is a launch failure; the harness installation's pipx/venv Python
is never silently substituted for the project environment.

The runner and verification sensor will execute the migrated rows with exact
argv, merged env and resolved workdir. `must_pass` is unchanged and a failed or
unstarted required check prevents `done` from emitting `implementation_complete`
or advancing state. Add focused tests for target markers, both zero and 23 exit
codes, stdout/stderr, paths containing spaces and Chinese characters, argument
boundaries, environment propagation, missing project runtime, and the actual
`done` no-completion path for both launch failure and a started failing program.

### 2a. Adapter and registration producers

Audit every internal verification producer before applying the runner change.
The OpenSpec framework adapter must emit direct argv for its
`openspec validate ${SLUG} --strict --json` check, and the shared adapter merge
boundary must validate incoming rows with the same command contract before it
writes them. `verification register` must surface that validation as a clean
validation exit and never persist a legacy string-only row. Migrate all
adapter-install, registration, and lifecycle fixtures that represent direct
commands to argv arrays; retain string commands only in tests that explicitly
exercise `shell: sh` or reject the invalid form. The OpenSpec E2E must reload
the resulting config through the real `done` path after its fixture edit.

### 2b. POSIX-only daemon collection and cross-platform typing

The remaining full-verification failures are confined to the optional POSIX
observer boundary: mypy on native Windows cannot describe `fcntl.flock`,
`os.fork` or `os.setsid`, while three daemon integration modules import `fcntl`
before pytest can collect them. Do not port the observer or change its POSIX
runtime behavior. Keep the POSIX implementation on its existing path, but make
its platform-specific API access opaque to the cross-platform type checker
without broad or blanket ignores. In the Windows-only branch of test
collection, use `pytest.importorskip("fcntl")` before importing the daemon
modules; Linux and macOS must still import and execute the same tests.

The Windows-only `ctypes.WinDLL` and `ctypes.get_last_error` access in the
settings merge module must likewise remain behaviorally unchanged while using
portable attribute lookup so mypy does not report platform-dependent
false-positive/unused-ignore errors. No daemon module is made into a Windows
observer implementation, and no full test group is hidden behind a generic
skip.

### 2c. Stable repository paths and native project-check execution

The differential Windows runs leave four independent portability boundaries that
are not fixed by the daemon changes:

* Repository-relative paths are public comparison and report values. The anchor
  scanner, decision-check result, and documentation-reference result must emit
  `/` separators on every host; native `Path` separators remain allowed only for
  filesystem access. Add focused regressions for the existing decision and doc
  reference failures, including the scanner location that feeds the decision
  result.
* `scripts/run_project_check.py` is the repository verification execution
  contract, not a CI-only wrapper. Its child environment will prepend the
  repository's `.venv/Scripts` or `.venv/bin` to `PATH` so console scripts and
  nested `gh`/hook calls resolve to the project toolchain, while preserving the
  caller's remaining environment and exact child exit code. It will set
  `PYTHONUTF8=1` for this verification child process so repository UTF-8 files
  are read consistently in local verification and CI; this does not change the
  encoding policy of ordinary application invocations. Test the environment and
  marker/exit behavior without relying on a CI-only variable.
* Settings backups are byte-preserving artifacts. The Windows CRT descriptor
  used by `_write_backup_bytes` must be opened in binary mode (`O_BINARY` when
  available), with zero behavioral change on POSIX. Existing exact-byte backup
  regressions are the proof; no newline normalization is acceptable.
* The `mock_gh` E2E fixture must install a Windows-resolvable `gh` command. An
  extensionless POSIX executable is not a valid native Windows `PATHEXT` shim
  and may lose to an installed `gh.exe`; use a minimal Windows command wrapper
  that invokes the fixture's Python source, while retaining the extensionless
  executable on POSIX. The fixture must still assert that `shutil.which("gh")`
  resolves to the test shim, and the real OpenSpec lifecycle remains covered.

These repairs are execution-contract, output-contract, and fixture-boundary
fixes respectively; none changes Linux/macOS observer behavior, GitHub CLI
governance, check exit semantics, or the required-check lifecycle gate. The
focused proof must run the path, launcher/PATH/UTF-8, exact-byte, and E2E tests
before the final full verification.

The full run narrowed the remaining failures to four related boundaries: normalize
the decision creation message, recognize Windows rooted paths in adapter artifact
resolution, install a Windows-resolvable fake Codex command, and invoke the
registered hook command using the host subprocess contract rather than POSIX
shlex parsing on Windows. Include these four files in this same repair. Keep
the block/allow assertions and the producer-not-executed assertion intact.

### 2d. Windows file-lock initialization

The final full Windows run exposed a race in the existing cross-process lock
primitive: every process opens the sentinel and tries to create its first byte
before acquiring the region lock. On native Windows, concurrent buffered flushes
can therefore fail with `PermissionError`. Acquire the existing host lock first
on both platforms, then initialize and rewind the sentinel byte inside the
critical section. Preserve the existing POSIX `flock` and Windows `msvcrt`
primitives, retry behavior, unlock behavior, and append atomicity. The existing
multi-process writer regression is the acceptance test; no lock downgrade or
test skip is allowed.

### 3. Full documentation without porting the observer

Migrate `.harness/derived-docs.yaml` to the same direct argv plus structured
workdir/env shape. `core.doc_check` will validate that shape, resolve generator
executables explicitly through the supplied PATH, and use the shared direct
process primitive. Both generators will write UTF-8 bytes independently of the
Windows console locale while preserving Markdown content.

Move the `daemon.supervisor` import in `cli.observe` behind an actual operation.
The lazy command tree therefore remains complete for help and documentation on
Windows; `observe start`, `observe stop` and `observe status` remain registered
and report a clear unsupported-operation error on native Windows instead of
loading POSIX-only `fcntl` code. Existing POSIX observer behavior and tests stay
intact. This is an explicit boundary of the optional observer implementation,
not a new exception to the product's cross-platform verification axiom and not
an observer-porting project.

Migrate the checked-in derived-doc template and existing CLI verification/doc
test fixtures at the same boundary; there is no compatibility path for the old
string-only rows. Update the architecture note and generated CLI reference only when the real
`doc check` establishes content drift. The reference must contain the complete
`observe start`, `observe stop` and `observe status` command tree.

### 3a. Installed-environment CI closure

PR CI runs the installed `super-harness` entrypoint without a repository-local
`.venv`. For managed Python documentation generators, prepend the repository
venv tool directory when it exists and otherwise prepend the directory of the
interpreter running `super-harness`; keep the configured argv unchanged. This
preserves native local selection while allowing the installed Linux CI runtime
to execute the same generator instead of clearing `PATH` and reporting a false
missing executable.

Click 8.5 also exposes the deprecated `click.utils.make_default_short_help`
symbol as an opaque object to mypy. Render registered lazy-command help through
Click's public `Command.get_short_help_str` API so the declared `click>=8.4`
range remains valid without pinning away a supported release or suppressing the
type error.

## Regression-first proof

Before each implementation slice, add the smallest test at the seam that reaches
the failure and run it red against the current code. Then apply the fix and run
the same test green. The focused suite must cover:

1. Direct argv and explicit `sh` execution, exact exit/output/marker behavior,
   argument and path boundaries, and invalid command/schema forms.
2. Real `done` execution: a required check that starts and returns 23, and a
   required check that cannot start, both leave no `implementation_complete` event
   and no completion state advance.
3. All three locked decision checks on clean and injected counterexample trees,
   plus missing native POSIX-shell/utility dependencies failing closed. Do not
   change, ratify, reconcile or weaken the decision records.
4. A timeout child that writes a start marker and launches a descendant, followed
   by bounded return and proof that the descendant does not perform its delayed
   side effect. Preserve the POSIX group test and add native Windows coverage
   without asserting a status field in place of a real marker.
5. Project launcher selection, missing `.venv`, exact tool exit propagation and
   non-ASCII output.
6. Full CLI-tree generation in a real subprocess, UTF-8 output from both
   generators, explicit Windows observer operation failure, and a real `doc check`
   without `--fix` before assessing any drift.
7. The complete portable verification execution chain (config load → direct
   subprocess → result classification → `verify`/`done` lifecycle gate) on
   native Ubuntu, macOS, and Windows runners. The repository workflow must use
   one focused matrix job for this same suite on all three platforms; the
   Windows leg runs natively in PowerShell with no WSL or Docker.
8. Full native Windows collection reaches the non-daemon suite and skips only
   the three POSIX daemon modules before their `fcntl` imports; the same modules
   remain collected on POSIX. Full mypy passes on Windows without changing
   observer behavior or adding blanket ignores.

## Verification and evidence

After focused regressions, run the migrated project checks and archive the normal
timestamped verification summary and captured outputs. Report separately:

* not executed / could not start (`spawn_error`),
* started and found a code violation (`fail`),
* timed out (`timeout`), and
* started and passed (`pass`).

On the native Windows host use the explicit project-vendored interpreter for
agent-run commands, not a PATH prefix:

```
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe -m mypy src
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\super-harness.exe verify <change>
.venv\Scripts\super-harness.exe decision check --changed
.venv\Scripts\super-harness.exe decision check
.venv\Scripts\super-harness.exe doc refs --gate
.venv\Scripts\super-harness.exe doc check
```

Also run `git diff --check` and the actual migrated checks through their normal
verification path. Retain existing Linux/macOS test coverage and state clearly
which platforms were actually executed; a workflow matrix alone is not evidence.
The committed `.github/workflows/test.yml` adds a `verification-cross-platform`
matrix for Python 3.12 on `ubuntu-latest`, `macos-latest`, and
`windows-latest`; its focused pytest command is the reproducible CI execution
chain proof, while local reports must still distinguish executed platform legs
from merely declared matrix entries.
The known baseline includes collection failures from observer `fcntl` and
platform-dependent mypy errors in POSIX-only daemon code and Windows-only
ctypes access. This scope repairs their typing and collection boundary only;
it does not port the observer. Any remaining failure in the declared repair
scope blocks completion; unrelated failures are reported as separate gaps.

## Governance and handoff

Commit only declared files. Run `decision check --changed` at checkpoints and
full `decision check`, `doc refs --gate`, `doc check`, and the configured
verification before completion. Preserve the 9 tier-2 reminders and old unknown
events. Recheck the historical summary hash and unrelated worktree state.

The revised plan must be sent through the configured independent plan review as a
new epoch after `plan ready`, `review prepare` and `review begin`. The only
configured automatic source is `claude-cli` with model
`claude-opus-5[1m]` and `effort=medium`; the current Luna/Max implementation
model does not replace that reviewer. Run every issued invocation unchanged,
import a real result or record one producer failure exactly once, and never
confirm a human nonce. If the configured producer or human authorization is
blocked, stop before source edits and report the exact lifecycle state and next
permitted command.
