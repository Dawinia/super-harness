---
change: 2026-09-04-windows-verification
stage: plan
scope:
  files:
    - docs/plans/2026-09-04-windows-verification.md
    - .harness/verification.yaml
    - src/super_harness/core/shell_runner.py
    - src/super_harness/core/check_runner.py
    - src/super_harness/core/doc_check.py
    - src/super_harness/cli/observe.py
    - scripts/run_project_check.py
    - scripts/gen_cli_reference.py
    - scripts/gen_state_machine.py
    - tests/unit/core/test_shell_runner.py
    - tests/unit/core/test_check_runner.py
    - tests/unit/core/test_doc_check_engine.py
    - tests/unit/sensors/test_verification_runner.py
    - tests/unit/cli/test_done.py
    - tests/unit/cli/test_observe.py
    - tests/unit/scripts/test_run_project_check.py
    - tests/unit/scripts/test_gen_cli_reference.py
    - tests/unit/scripts/test_gen_state_machine.py
    - docs/architecture.md
tier_hint: Normal
---

# Execute native Windows verification honestly

## Authority, baseline and boundaries

The maintainer authorized repair of false-positive verification, existing POSIX
decision checks, and full CLI-document generation on native Windows without WSL
or Docker. This is a separate change on `codex/2026-09-04-windows-verification`,
starting at `49f4cf0`. The existing product-baseline change remains
`IMPLEMENTATION_IN_PROGRESS`; do not close, merge, or change its scope. Existing
untracked `.superpowers/` and `docs/research/` are excluded.

Read `AGENTS.md`, `.harness/review-governance.yaml`, the local producer profile,
the unified-shell-runner design, and the ratified checks before implementation.
`d-core-is-base`, `d-gh-cli-not-rest`, and `d-merge-gate-pure-git` retain their
locked bodies and bite tests. No review skip or human approval from another
change applies here. Commit this plan and obtain the configured independent
plan review before source changes; no agent may confirm a human nonce.

## Confirmed failures

- Direct Python prints an execution marker and exits 23. Both `shell=True` and
  the actual `run_shell` primitive return 0 with empty stdout/stderr for
  `PATH="$(pwd)/.venv/bin:$PATH" python -c "print(123); raise SystemExit(23)"`.
  Windows cmd interprets this as its PATH built-in, consuming the supposed
  program. Exit zero is a shell outcome, not evidence that pytest ran.
- Preserve the historical summary at
  `.harness/verification-results/2026-09-04-product-baseline/2026-09-03T17-36-52.439925Z/summary.json`.
  SHA256: `f66229bb8a5d1db6a1b05e8fd8c3fbc40dbe1b030e6969f446538d0b06455c15`.
  Its pytest duration is 23 ms. Ruff and mypy have the same false-positive shape.
- Full decision check executes the three POSIX snippets through cmd and fails
  on assignment, negation, and regular-expression syntax. The documented
  contract is POSIX `/bin/sh`, including env prefixes, pipes, and `! grep`.
  The installed Git for Windows native `usr/bin/sh.exe` can execute the exit-23
  probe and the import-linter check; there is no need for WSL or a new shell for
  ordinary verification commands.
- CLI reference generation loads `cli.observe` then `daemon.supervisor`, whose
  module-level `fcntl` import prevents command-tree enumeration on Windows.
- Generator execution uses shell-free argv but bare `python` resolves to the
  base `C:\Python311\python.exe` under Windows CreateProcess, even with the
  venv first on PATH. That interpreter cannot import the editable package.
  Explicit venv Python runs the state generator but emits GBK to a pipe, whereas
  the document-check contract requires UTF-8. These are separate failures.
- The shared timeout handler calls `os.killpg`, unavailable on Windows. It must
  remain bounded and report failure when a real check hangs.

## Minimal repair and command contracts

### Ordinary verification and the project configuration

Preserve native shell string semantics for ordinary verification: `/bin/sh` on
POSIX, cmd on Windows. Do not globally choose Git Bash, reinterpret arbitrary
shell programs, or change the check schema. At the shared runner boundary reject
leading shell-style `NAME=value` assignments on Windows native-shell commands
with a clear execution/configuration error and nonzero sentinel; recommend
structured `defaults.env` / check `env` instead. This deliberately rejects cmd's
ambiguous PATH-assignment form too. It is a narrow guard against the demonstrated
false success, not proof that arbitrary owner-trusted shell text runs a program.

Replace this repository's POSIX PATH prefixes with a small project-local Python
launcher (`python -m scripts.run_project_check MODULE ...`). The launcher chooses
the repository `.venv/Scripts/python.exe` or `.venv/bin/python`, prepends that
scripts directory using `os.pathsep`, and executes module argv without a shell.
Ruff, mypy and pytest use the same venv and child console scripts on both hosts.
Missing venv/tool, nonzero exit and launch errors remain failures; never silently
fall back to a different interpreter, omit tests, or alter `must_pass`.

### Existing POSIX decision checks

Make shell selection explicit at the shared primitive and have `run_one_check`
request the established POSIX contract. On Linux/macOS retain `/bin/sh`; on
Windows resolve a native Git for Windows sh with its companion utilities from
the installed Git layout. Use explicit argv (`sh -c`, shell=False) for that
mode. Preserve the caller's PATH priority and append the utility directory so
venv tools still resolve first. Never select the Windows WSL launchers or fall
back to cmd if the required shell/utilities are absent: return a clear failed
execution result. Document the native Git dependency and its limits.

Keep environment replacement/scrubbing, output capture and exit-code propagation
intact. Preserve POSIX process-group cleanup. On Windows use bounded native
process-tree termination and reap for timeouts; cleanup errors must never turn
timeout into success or mask it with `AttributeError`. Test a real started child,
including descendants, rather than inferring termination from a status field.

### Full documentation without porting the observer

Keep every observe command registered on every platform. Defer supervisor import
until an observe operation actually runs, with an explicit unsupported-platform
error on Windows. Help and complete document enumeration must work; do not hide
commands, fake daemon state, or port the observer's flock/fork/signals machinery.
Preserve POSIX observe behavior and its existing test seams where feasible.

Resolve generator executable names through PATH before subprocess launch so
Windows honors the activated environment. Keep generator commands shell-free.
Make the two project generators emit UTF-8 independently of the Windows locale,
without changing their Markdown content. Run doc check without `--fix` first;
unexpected content drift must be assessed, not overwritten for a green result.

## Regression proof and actual verification

Write focused regressions before fixes and observe their failures. Follow the
existing test suite, without adding a new framework:

1. Real child programs write unique filesystem markers plus stdout/stderr, and
   return both 0 and 23. Assert the marker, captured content and exact exit code
   through the runner and verification wrapper. On Windows the legacy prefix
   must fail clearly and leave its marker absent, never be recorded as pass.
2. Exercise actual `done` with a required failing check in an isolated fixture:
   program execution is proven, verify fails, and no implementation-completed
   event/state advance occurs. Cover a launch/configuration failure separately
   from a running program returning a violation.
3. Exercise POSIX env assignment, negation and pipes through the decision seam,
   including clean and counterexample cases. Prove an unavailable required shell
   fails closed. Run the unchanged three repository decision checks and their
   sandbox counterexamples without ratifying or modifying locked text.
4. Prove timeout is bounded and a child actually started before termination;
   preserve POSIX behavior and add native Windows process-tree coverage.
5. Verify project launcher argv, paths containing spaces, venv selection, marker
   execution, output and exit propagation. Adjust only directly affected tests
   whose command fixtures accidentally rely on the wrong platform shell.
6. Generate the whole CLI reference in a real subprocess; assert observe
   start/stop/status are present. Test Windows unsupported operation separately
   from help/enumeration. Test PATH selection and UTF-8 output with non-ASCII
   text for both generators, and run the actual doc check.

After focused regressions, run the configured full ruff/mypy/pytest verification
on native Windows and retain its timestamped output/summary. Run `git diff
--check`, `decision check --changed`, full `decision check`, `doc refs --gate`,
and `doc check`. Record precise execution/configuration failures, actual code
violations, and genuine passes separately. Do not skip POSIX-only observer tests
or broaden into observer portability to make the full suite green. Any remaining
unrelated failure is an explicit completion blocker, not a passed check.

Linux/macOS behavior must remain covered by existing tests and unchanged POSIX
branches; local Windows proof is not a claim of a live Linux/macOS test run.
Store diagnostic commands and results under this change's allowed scratch area,
and keep final verification summaries in the standard archive. Recheck the
historical summary hash and unrelated worktree state at the end.

## Governance and completion

The initial full decision check reports these nine existing tier-2 reminders:
`d-dangling-check`, `d-decision-records`, `d-events-append-only`,
`d-fixed-transition-matrix`, `d-gate-governs-git-product`,
`d-identity-resolution-order`, `d-pitfall-is-proposed-decision`,
`d-single-gate-policy`, and `d-state-pure-fold`. Preserve and report them; no
bulk reconcile, betrayal, or ratification is authorized here.

Commit only declared scope files after the relevant conformance checkpoint.
Use the configured review participants and their exact local models/options;
freeze and invoke every issued contract unchanged, import results or record
producer failure once, and collect all sources before fixing findings. A blocked
plan review leaves this change at `AWAITING_PLAN_REVIEW` with no source edits.
If implementation is approved, only successful required checks permit `done`
to advance; obtain code review through the same protocol. No push or merge.

Return the root causes, changed files, actual verification evidence, commits,
lifecycle/review status, precise blockers and next permitted action. Scheduling,
global shell migration, full observer Windows support, old tier-2 review work,
and product-baseline redesign remain outside this change.
