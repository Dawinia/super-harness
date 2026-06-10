"""Verification runner: the `VerificationRunner` sensor + its execution engine.

Phase 8 tasks 8.3 + 8.4 (engineering-integration §2.3 / §3.6, sensor-gate
§3.1.3, cli-command-surface §3.4). Two responsibilities layered bottom-up:

- **Task 8.3 leaf primitive** — `run_check` runs a single `CheckSpec` as a
  subprocess with a timeout + optional output capture, returning a `CheckResult`.
- **Task 8.4 runner** — `VerificationRunner` (a `Sensor`) collects checks from
  `.harness/verification.yaml` across three layers (baseline / framework_adapter
  / user_checks), runs them sequentially or in parallel with optional fail-fast,
  archives a `summary.json`, and emits a `verification_passed` /
  `verification_failed` event.

The three layers are heterogeneous: `adapter_provided` + user `checks` are shell
subprocesses (`run_check`); the `baseline` layer is in-process Python. To keep
`run_checks` agnostic, every layer is reduced to a uniform `CheckTask` (an id, a
`must_pass` flag, and a zero-arg `run` callable producing a `CheckResult`).

**Baseline layer (Task 8.5)** — `baseline_check_tasks` builds 2 in-process
baselines: `lifecycle-ordering` (the change's event stream has no illegal
transitions — an integrity/tamper check; must_pass), and `scope-vs-plan-final` (changed files
fall within the declared plan scope; advisory must_pass=False, mirroring the
`scope_drift_detected` warning nature). `find_ordering_violations` (the
whole-stream validator powering `lifecycle-ordering`) lives in
`core.emit_validation`.

**`shell=True` is intentional** (spec §3.6 #7): `verification.yaml` is
repo-owner-trusted, so check commands run through the shell exactly as written.
No shell-escaping is applied. Variable interpolation is restricted by the
`engineering.verification_config` allowlist (untrusted PR-context names cannot be
referenced), so the only injection surface is the repo owner's own config.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from super_harness.core.clock import utc_now_iso
from super_harness.core.emit_validation import find_ordering_violations
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    events_path,
    verification_results_dir,
    verification_yaml_path,
)
from super_harness.core.reducer import derive_state
from super_harness.engineering.verification_config import (
    CheckSpec,
    VerificationConfig,
    interpolate,
    load_verification_config,
)
from super_harness.sensors import (
    Activity,
    ActivityType,
    Determinism,
    Sensor,
    SensorResult,
    WorkspaceContext,
)

if TYPE_CHECKING:
    from super_harness.sensors import SensorStatus

__all__ = [
    "BASELINE_CHECK_IDS",
    "CheckResult",
    "CheckTask",
    "VerificationRunner",
    "build_variables",
    "collect_checks",
    "collectable_check_ids",
    "make_verification_event",
    "run_check",
    "run_checks",
    "verify_data_block",
    "write_summary_json",
]

CheckStatus = Literal["pass", "fail", "timeout"]


@dataclass(frozen=True)
class CheckResult:
    """Outcome of running a single verification check.

    The five core fields (`id`, `status`, `exit_code`, `duration_ms`,
    `must_pass`) mirror the runner's per-check result. Two additional fields are
    populated here because `run_check` is the natural place that knows them and
    task 8.4 needs them for its event payload + `--json` output:

    - `command`: the INTERPOLATED command string actually run (placeholders such
      as `${SLUG}` already substituted). This is the value reported as the
      spec's `failed_checks[].command`.
    - `output_path`: where this check's captured output was archived, as a
      string, or `None` when nothing was archived:
        * `capture == "stdout"` → the `<id>.stdout` file path.
        * `capture == "stderr"` → the `<id>.stderr` file path.
        * `capture == "both"`   → the archive directory (both `.stdout` and
          `.stderr` live there; the directory is the addressable handle).
        * `capture == "none"`   → `None` (nothing written).
        * on timeout            → `None` (the process was killed; no output is
          archived).

    Frozen: results are immutable records handed to the runner/event layer.
    """

    id: str
    status: CheckStatus
    exit_code: int
    duration_ms: int
    must_pass: bool
    command: str
    output_path: str | None


def run_check(
    check: CheckSpec,
    *,
    workdir: Path,
    env: dict[str, str],
    archive_dir: Path,
    variables: dict[str, str],
) -> CheckResult:
    """Run a single `CheckSpec` as a subprocess and return its `CheckResult`.

    Interpolates `check.command` with `variables` (allowlist-enforced by
    `verification_config.interpolate`), runs it through the shell in `workdir`
    with `check.timeout_seconds`, and archives stdout/stderr under `archive_dir`
    according to `check.capture`.

    Contracts:
        - **`env` is passed PRE-MERGED.** `run_check` hands `env` straight to
          `subprocess.run(env=...)`, which REPLACES the entire child
          environment (it does NOT layer on top of `os.environ`). Building the
          `os.environ` + `defaults.env` + `check.env` merge is the caller's job
          (task 8.4). In particular, `env` MUST include `PATH` or PATH-resolved
          commands (`ls`, `sleep`, `python`, …) will fail to launch.
        - `workdir` is used verbatim as `cwd`; the caller is responsible for
          resolving any relative `check.workdir` to an absolute path.
        - `shell=True` is intentional (see module docstring); no escaping.

    Args:
        check: The check to run. `command` is interpolated; `timeout_seconds`
            and `capture` drive timeout + archiving; `id`/`must_pass` pass
            through to the result.
        workdir: Ready (caller-resolved) working directory for the subprocess.
        env: Pre-merged environment for the subprocess (replaces, not layers).
            Must include `PATH`.
        archive_dir: Directory to write `<id>.stdout` / `<id>.stderr` into. It
            is created (with parents) if missing.
        variables: Interpolation variables for `check.command`.

    Returns:
        A `CheckResult`. On timeout, `status == "timeout"`, `exit_code == -1`,
        `output_path is None`, and `command` is still the interpolated command.

    Raises:
        InterpolationError: `check.command` references a non-allowlisted
            `${NAME}` placeholder (propagated from `interpolate`).
    """
    cmd = interpolate(check.command, variables)
    archive_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()  # measure real elapsed wall-clock
    try:
        # shell=True is intentional and per-spec (§3.6 #7); see module docstring.
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=workdir,
            env=env,
            capture_output=True,
            timeout=check.timeout_seconds,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            id=check.id,
            status="timeout",
            exit_code=-1,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            must_pass=check.must_pass,
            command=cmd,
            output_path=None,
        )

    duration_ms = int((time.perf_counter() - t0) * 1000)

    output_path: str | None = None
    if check.capture in ("stdout", "both"):
        (archive_dir / f"{check.id}.stdout").write_text(proc.stdout)
    if check.capture in ("stderr", "both"):
        (archive_dir / f"{check.id}.stderr").write_text(proc.stderr)

    if check.capture == "stdout":
        output_path = str(archive_dir / f"{check.id}.stdout")
    elif check.capture == "stderr":
        output_path = str(archive_dir / f"{check.id}.stderr")
    elif check.capture == "both":
        output_path = str(archive_dir)

    return CheckResult(
        id=check.id,
        status="pass" if proc.returncode == 0 else "fail",
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        must_pass=check.must_pass,
        command=cmd,
        output_path=output_path,
    )


# --------------------------------------------------------------------------- #
# Task 8.4 — the uniform runnable seam + the VerificationRunner sensor
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CheckTask:
    """A single runnable unit the scheduler executes, agnostic to its source.

    The runner deals only in `CheckTask`s so the three heterogeneous layers
    (shell-subprocess config checks vs in-process baseline checks) share one
    scheduling path:

    - A config check (user `checks` / `adapter_provided`) binds `run` to a
      `run_check(...)` call with its merged env + resolved workdir captured.
    - A baseline check (Task 8.5) binds `run` to an in-process callable.

    `id` / `must_pass` are surfaced here so the scheduler can decide fail-fast
    (does a *must_pass* task's failure abort the rest?) without first invoking
    `run`.
    """

    id: str
    must_pass: bool
    run: Callable[[], CheckResult]


def build_variables(change_id: str, context: WorkspaceContext) -> dict[str, str]:
    """Build the `${NAME}` interpolation variables for a verification run.

    `SLUG` and `CHANGE_ID` are aliases of the change id (per the
    `verification_config` allowlist). `SPEC_PATH` / `PLAN_PATH` (HG-01) are resolved
    via the active change's framework adapter `spec_paths` (pure path derivation);
    they stay `""` when the context carries no framework, or the framework is unknown,
    or the framework has no spec/plan concept (e.g. plain).

    Args:
        change_id: The change this run is for (already None-resolved by caller).
        context: The workspace snapshot; `context.framework` drives spec/plan lookup.

    Returns:
        A mapping consumed by `interpolate` for every check `command`.
    """
    spec_path = plan_path = ""
    if context.framework:
        # Local import breaks the sensors<->adapters cycle (adapters/__init__ imports
        # WorkspaceContext from sensors). `spec_paths` is pure → daemon-safe.
        from super_harness.adapters import FrameworkAdapter
        from super_harness.adapters.registry import get_builtin

        cls = get_builtin(context.framework)
        if cls is not None and issubclass(cls, FrameworkAdapter):
            paths = cls().spec_paths(context.workspace_root, change_id)
            spec_path = paths.get("spec", "")
            plan_path = paths.get("plan", "")
    return {
        "SLUG": change_id,
        "CHANGE_ID": change_id,
        "SPEC_PATH": spec_path,
        "PLAN_PATH": plan_path,
    }


# Baseline check ids (Task 8.5). Kept as constants so `only_ids` filtering, the
# CheckTask `id`, and the `command` descriptor never drift out of sync.
_BASELINE_LIFECYCLE = "lifecycle-ordering"
_BASELINE_SCOPE = "scope-vs-plan-final"

# The 2 baseline ids, single-sourced so `baseline_check_tasks` (what runs) and
# `collectable_check_ids` (what `--check` validation reports as collectable)
# can never drift. Ordered (lifecycle → scope) to match build order.
BASELINE_CHECK_IDS: tuple[str, ...] = (
    _BASELINE_LIFECYCLE,
    _BASELINE_SCOPE,
)


def _make_baseline_result(
    check_id: str,
    *,
    passed: bool,
    must_pass: bool,
    t0: float,
    command: str,
    report: str | None,
    archive: Path,
) -> CheckResult:
    """Construct a `CheckResult` for an in-process baseline.

    Exit code is SYNTHETIC (0 pass / 1 fail) — baselines are Python functions,
    not subprocesses. When `report` is non-empty it is archived to
    `archive/<check_id>.txt` and `output_path` points at it; otherwise
    `output_path` is None (nothing to report).
    """
    output_path: str | None = None
    if report:
        archive.mkdir(parents=True, exist_ok=True)
        report_file = archive / f"{check_id}.txt"
        report_file.write_text(report)
        output_path = str(report_file)
    return CheckResult(
        id=check_id,
        status="pass" if passed else "fail",
        exit_code=0 if passed else 1,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        must_pass=must_pass,
        command=command,
        output_path=output_path,
    )


def _baseline_lifecycle_ordering(
    change_id: str,
    *,
    context: WorkspaceContext,
    archive: Path,
) -> CheckResult:
    """Baseline: the change's event stream has no illegal transitions.

    Integrity / tamper check — a stream where every event went through the strict
    emit-time gate cannot be out of order, so any `find_ordering_violations`
    result means the stream was hand-edited / imported unvetted / corrupted.
    Always must_pass.
    """
    t0 = time.perf_counter()
    violations = find_ordering_violations(events_path(context.workspace_root), change_id)
    report = None
    if violations:
        report = (
            f"Lifecycle ordering violations for change {change_id}:\n"
            + "\n".join(
                f"  - {v.event_id} ({v.event_type}) from {v.from_state}: {v.reason}"
                for v in violations
            )
            + "\n"
        )
    return _make_baseline_result(
        _BASELINE_LIFECYCLE,
        passed=not violations,
        must_pass=True,
        t0=t0,
        command=f"builtin:{_BASELINE_LIFECYCLE}",
        report=report,
        archive=archive,
    )


def _baseline_scope_vs_plan(
    change_id: str,
    *,
    context: WorkspaceContext,
    archive: Path,
) -> CheckResult:
    """Baseline (advisory): changed files fall within the declared plan scope.

    Compares the plan's declared `scope.files` against the files actually changed
    on this branch vs `main`. A changed file not covered by any declared entry
    (exact path or prefix match) is out-of-scope drift → fail. Empty declared
    scope OR no changed files → pass.

    Advisory (must_pass=False), mirroring the `scope_drift_detected` warning
    nature (sensor-gate §3.1.4): drift never fails the verdict.

    Graceful degradation: if git is unavailable or `main` is missing, the check
    CANNOT assert drift, so it returns pass with an explanatory note rather than
    crying wolf or crashing.
    """
    t0 = time.perf_counter()
    states = derive_state(events_path(context.workspace_root))
    cs = states.get(change_id)
    declared_files = list(cs.scope.get("files", [])) if cs is not None else []
    if not declared_files:
        return _make_baseline_result(
            _BASELINE_SCOPE,
            passed=True,
            must_pass=False,
            t0=t0,
            command=f"builtin:{_BASELINE_SCOPE}",
            report=None,
            archive=archive,
        )

    # TODO(post-v0.1): detect/config base branch (per-repo default branch,
    # PR-target branch, or a verification.yaml setting). Hardcoded to `main` for
    # v0.1 — no base-branch detection exists yet.
    base = "main"
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=context.workspace_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Cannot determine the diff (git missing / `main` absent / not a repo):
        # do NOT cry wolf. Pass with a note. must_pass=False → never fails verdict.
        note = (
            f"scope-vs-plan skipped for change {change_id}: could not compute "
            f"`git diff --name-only {base}...HEAD` ({type(e).__name__}). "
            f"Cannot assert scope drift; treating as pass.\n"
        )
        return _make_baseline_result(
            _BASELINE_SCOPE,
            passed=True,
            must_pass=False,
            t0=t0,
            command=f"builtin:{_BASELINE_SCOPE}",
            report=note,
            archive=archive,
        )

    changed = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not changed:
        return _make_baseline_result(
            _BASELINE_SCOPE,
            passed=True,
            must_pass=False,
            t0=t0,
            command=f"builtin:{_BASELINE_SCOPE}",
            report=None,
            archive=archive,
        )

    drifted = [f for f in changed if not _covered_by_scope(f, declared_files)]
    report = None
    if drifted:
        report = (
            f"Out-of-scope files changed (not covered by declared scope.files) "
            f"for change {change_id}:\n"
            + "\n".join(f"  - {f}" for f in drifted)
            + "\ndeclared scope.files:\n"
            + "\n".join(f"  - {d}" for d in declared_files)
            + "\n"
        )
    return _make_baseline_result(
        _BASELINE_SCOPE,
        passed=not drifted,
        must_pass=False,
        t0=t0,
        command=f"builtin:{_BASELINE_SCOPE}",
        report=report,
        archive=archive,
    )


def _covered_by_scope(changed_file: str, declared_files: list[str]) -> bool:
    """True if `changed_file` is covered by any declared scope entry.

    v0.1 matching is SEGMENT-AWARE: exact path equality OR a prefix that lands on
    a path boundary. A declared directory entry like `src/foo/` (or `src/foo`)
    covers everything under it (`src/foo/x.py`) but NOT a sibling that merely
    shares the textual prefix (`src/foobar.py`) — avoiding the naive-`startswith`
    false-negative where real out-of-scope drift would be missed.
    """
    for entry in declared_files:
        if changed_file == entry:
            return True
        prefix = entry if entry.endswith("/") else entry + "/"
        if changed_file.startswith(prefix):
            return True
    return False


def baseline_check_tasks(
    cfg: VerificationConfig,
    *,
    context: WorkspaceContext,
    archive: Path,
    variables: dict[str, str],
    only_ids: list[str] | None = None,
) -> list[CheckTask]:
    """Build the baseline-layer `CheckTask`s (Task 8.5): the 2 in-process checks.

    The 2 baselines are pure-Python (not subprocesses):
        - `lifecycle-ordering` — must_pass (integrity/tamper check).
        - `scope-vs-plan-final` — advisory (must_pass=False; scope-drift warning).

    Each baseline's `must_pass` is computed HERE (at build time) so the
    `run_checks` scheduler's fail-fast logic sees the correct flag before it ever
    calls `run`. Each `CheckTask.run` is a zero-arg closure; per-baseline values
    are bound via default args to dodge late-binding.

    `cfg` is accepted (but not read) for call-site uniformity with
    `_config_check_task` and future use (e.g. a future per-repo base-branch
    setting feeding the scope baseline); keep it for signature stability.

    `change_id` comes from `variables["CHANGE_ID"]` (set by `build_variables`).
    `only_ids` filtering happens both here (skip building skipped baselines) and
    again in `collect_checks` — building is cheap (no work runs until `.run()`),
    so the local filter just avoids constructing tasks the caller will drop.

    Returns:
        The baseline `CheckTask`s surviving the `only_ids` filter, in fixed order
        (lifecycle → scope).
    """
    change_id = variables.get("CHANGE_ID") or variables.get("SLUG") or ""
    wanted = set(only_ids) if only_ids is not None else None

    def _included(check_id: str) -> bool:
        return wanted is None or check_id in wanted

    # The lifecycle/scope baselines re-derive state inside their own closures.
    tasks: list[CheckTask] = []

    # Each `_run` binds its per-baseline values via default args to dodge
    # late-binding (mirrors `_config_check_task`). Named defs (not lambdas) so
    # mypy can infer the `Callable[[], CheckResult]` type.
    if _included(_BASELINE_LIFECYCLE):

        def _run_lifecycle(
            change_id: str = change_id,
            context: WorkspaceContext = context,
            archive: Path = archive,
        ) -> CheckResult:
            return _baseline_lifecycle_ordering(
                change_id, context=context, archive=archive
            )

        tasks.append(
            CheckTask(id=_BASELINE_LIFECYCLE, must_pass=True, run=_run_lifecycle)
        )

    if _included(_BASELINE_SCOPE):

        def _run_scope(
            change_id: str = change_id,
            context: WorkspaceContext = context,
            archive: Path = archive,
        ) -> CheckResult:
            return _baseline_scope_vs_plan(
                change_id, context=context, archive=archive
            )

        tasks.append(
            CheckTask(id=_BASELINE_SCOPE, must_pass=False, run=_run_scope)
        )

    return tasks


def _config_check_task(
    spec: CheckSpec,
    *,
    context: WorkspaceContext,
    cfg: VerificationConfig,
    archive: Path,
    variables: dict[str, str],
) -> CheckTask:
    """Wrap one config `CheckSpec` as a `CheckTask` bound to a `run_check` call.

    Resolves the per-check workdir + merges the three env layers at BIND time
    (`os.environ` < `defaults.env` < `spec.env`) so the closure captures concrete
    values, then binds them via default args to dodge Python's late-binding-in-
    loops trap (the `spec=spec, ...` defaults snapshot per-iteration values).
    """
    resolved = (context.workspace_root / spec.workdir).resolve()
    merged_env = {**os.environ, **cfg.defaults.env, **spec.env}

    # The default args snapshot this iteration's values to dodge late-binding.
    # `spec`/`workdir`/`env` are per-task-fresh, but `archive` and `variables`
    # are the SAME objects shared across every task in a `collect_checks` pass
    # — they MUST be treated read-only here (interpolate/run_check only read
    # them; never mutate them in `_run`). Not MappingProxyType so the
    # `dict[str, str]` annotations stay honest; the read-only-ness is by
    # contract, not enforced.
    def _run(
        spec: CheckSpec = spec,
        workdir: Path = resolved,
        env: dict[str, str] = merged_env,
        archive_dir: Path = archive,
        variables: dict[str, str] = variables,
    ) -> CheckResult:
        return run_check(
            spec,
            workdir=workdir,
            env=env,
            archive_dir=archive_dir,
            variables=variables,
        )

    return CheckTask(id=spec.id, must_pass=spec.must_pass, run=_run)


def collect_checks(
    cfg: VerificationConfig,
    *,
    context: WorkspaceContext,
    archive: Path,
    variables: dict[str, str],
    layer: str | None = None,
    only_ids: list[str] | None = None,
) -> list[CheckTask]:
    """Collect runnable `CheckTask`s across the three verification layers.

    Layer order is baseline → framework_adapter (adapter_provided) → user
    (`checks`). Each layer is included only when (a) its `cfg.layers.*` enable
    flag is set AND (b) it is not filtered out by `layer`.

    Args:
        cfg: The loaded verification config.
        context: Workspace snapshot (used to resolve check workdirs).
        archive: Per-run archive dir handed to every check's `run`.
        variables: Interpolation variables for config-check commands.
        layer: If given, restrict to a single layer — one of `"baseline"`,
            `"adapter"`, `"user"`. The selected layer is STILL gated by its
            enable flag (a disabled layer yields nothing even if named here).
        only_ids: If given, keep only checks whose `id` is in this list (applied
            across whichever layers survive the `layer` filter).

    Returns:
        The `CheckTask`s to run, in layer order.
    """
    want_baseline = layer in (None, "baseline")
    want_adapter = layer in (None, "adapter")
    want_user = layer in (None, "user")

    tasks: list[CheckTask] = []

    if want_baseline and cfg.layers.baseline:
        tasks.extend(
            baseline_check_tasks(
                cfg,
                context=context,
                archive=archive,
                variables=variables,
                only_ids=only_ids,
            )
        )

    if want_adapter and cfg.layers.framework_adapter:
        tasks.extend(
            _config_check_task(
                spec, context=context, cfg=cfg, archive=archive, variables=variables
            )
            for spec in cfg.adapter_provided
        )

    if want_user and cfg.layers.user_checks:
        tasks.extend(
            _config_check_task(
                spec, context=context, cfg=cfg, archive=archive, variables=variables
            )
            for spec in cfg.checks
        )

    if only_ids is not None:
        # Unknown `--check` ids (a typo'd `--check name` that would silently run
        # 0 checks) are rejected up-front by the CLI via `collectable_check_ids`
        # BEFORE dispatch, so by the time we get here every requested id is known
        # to be collectable; this just applies the filter.
        wanted = set(only_ids)
        tasks = [t for t in tasks if t.id in wanted]

    return tasks


def collectable_check_ids(
    cfg: VerificationConfig, *, layer: str | None = None
) -> set[str]:
    """The set of check ids `collect_checks` WOULD collect for `cfg` + `layer`.

    Mirrors `collect_checks`'s layer selection exactly: a layer contributes its
    ids only when (a) its `cfg.layers.*` enable flag is set AND (b) it is not
    filtered out by `layer`. Used by the `verify` / `done` CLI to validate
    `--check <id>` BEFORE dispatch so a typo (or a baseline id requested under
    `--layer user`) is a clean EXIT_VALIDATION rather than a vacuous pass.

    Baseline ids come from the single-sourced `BASELINE_CHECK_IDS` (same source
    `baseline_check_tasks` builds from) so the two can never drift.

    Args:
        cfg: The loaded verification config.
        layer: Optional single-layer restriction — one of `"baseline"`,
            `"adapter"`, `"user"` — STILL gated by the layer's enable flag.

    Returns:
        Every check id collectable under the given `cfg` + `layer` filter.
    """
    want_baseline = layer in (None, "baseline")
    want_adapter = layer in (None, "adapter")
    want_user = layer in (None, "user")

    ids: set[str] = set()
    if want_baseline and cfg.layers.baseline:
        ids.update(BASELINE_CHECK_IDS)
    if want_adapter and cfg.layers.framework_adapter:
        ids.update(spec.id for spec in cfg.adapter_provided)
    if want_user and cfg.layers.user_checks:
        ids.update(spec.id for spec in cfg.checks)
    return ids


def run_checks(
    tasks: list[CheckTask],
    *,
    mode: str,
    max_workers: int,
    fail_fast: bool,
) -> list[CheckResult]:
    """Execute `tasks` and return their `CheckResult`s.

    Scheduling:
        - `mode == "parallel"` → a bounded `ThreadPoolExecutor(max_workers)`.
        - `mode == "sequential"` → tasks run in order on the calling thread.

    Fail-fast (`fail_fast=True`): once the FIRST *must_pass* check FAILS (status
    != "pass"), remaining work is abandoned. In SEQUENTIAL mode the loop simply
    breaks, so no further check runs. In PARALLEL mode the cancellation is
    BEST-EFFORT: futures are collected in submission order, so by the time a
    must_pass failure is observed most pending futures have already started (or
    finished) and `future.cancel()` is a no-op for them — it only truly cancels
    the BACKLOG beyond `max_workers` (i.e. tasks still queued, never started).
    For tasks already running/done, fail-fast in parallel mode primarily
    SUPPRESSES REPORTING of their results rather than saving subprocess work.
    This mirrors `SensorDispatcher._run_all`'s `future.cancel()` stance: running
    futures cannot be killed in CPython and complete on their own, but their
    results are dropped. Results are returned in task-submission order for
    determinism, regardless of completion order.

    Args:
        tasks: The runnable units (already filtered by `collect_checks`).
        mode: `"parallel"` or `"sequential"` (from `execution.mode`).
        max_workers: Thread-pool bound for parallel mode (`execution.max_parallelism`).
        fail_fast: Abort remaining checks after a must_pass failure (`execution.fail_fast`).

    Returns:
        One `CheckResult` per task whose result was collected, in submission
        order. Under parallel + fail_fast this may be FEWER than the number of
        tasks that actually executed (already-run-but-dropped checks aren't
        returned); see the best-effort note above.
    """
    if not tasks:
        return []

    if mode == "sequential":
        results: list[CheckResult] = []
        for task in tasks:
            result = task.run()
            results.append(result)
            if fail_fast and result.must_pass and result.status != "pass":
                break
        return results

    # Parallel. Submit all, collect in submission order, and on a must_pass
    # failure best-effort-cancel any not-yet-started futures. Only the backlog
    # beyond max_workers is ever truly cancelled; futures already running/done
    # complete on their own and we simply drop (stop collecting) their results.
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(task.run) for task in tasks]
        parallel_results: list[CheckResult] = []
        aborted = False
        for future in futures:
            if aborted:
                future.cancel()
                continue
            result = future.result()
            parallel_results.append(result)
            if fail_fast and result.must_pass and result.status != "pass":
                aborted = True
        return parallel_results


def write_summary_json(
    archive: Path,
    results: list[CheckResult],
    verdict: str,
) -> None:
    """Write the per-run `summary.json` into `archive` (creating parents).

    Writes ONLY `archive/summary.json` (no separate verdict.json). The file
    carries the run verdict, run metadata, and one row per check.

    Caveat: under parallel + fail_fast, `checks_run` is a LOWER BOUND — checks
    that already completed but whose results were dropped on a must_pass failure
    are NOT counted (see `run_checks`), not a count of every check that executed.
    The verdict is unaffected (the failing must_pass check is always collected).

    Note: the summary's `generated_at` is a WRITE-time timestamp (when this
    function runs, i.e. after all checks finish). It differs from the archive
    directory's `ts` component (the run-START time) by the run duration.

    Args:
        archive: The per-run archive dir (`verification_results_dir(...)`).
        results: Every `CheckResult` produced this run.
        verdict: `"passed"` or `"failed"`.
    """
    archive.mkdir(parents=True, exist_ok=True)
    summary = {
        "verdict": verdict,
        "generated_at": utc_now_iso(),
        "checks_run": len(results),
        "results": [
            {
                "id": r.id,
                "status": r.status,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "must_pass": r.must_pass,
                "command": r.command,
                "output_path": r.output_path,
            }
            for r in results
        ],
    }
    (archive / "summary.json").write_text(json.dumps(summary, indent=2))


def verify_data_block(
    change_id: str,
    results: list[CheckResult],
    archive: Path,
    workspace_root: Path,
) -> dict[str, Any]:
    """Build the FROZEN `verify --json` data block (cli-command-surface §3.4).

    Returned verbatim as `SensorResult.details`. Keys are a frozen contract —
    do NOT add/rename/reorder downstream-visible keys.

    Paths are REPO-RELATIVE to `workspace_root` (cli-command-surface §3.4: the
    schema example + the human error line both use a
    `.harness/verification-results/<change>/<ts>/...` repo-relative path with no
    leading slash). The on-disk archive and the internal `CheckResult.output_path`
    stay absolute; ONLY this frozen `--json` surface is relativized. A path that
    somehow falls OUTSIDE `workspace_root` is left as-is (via `os.path.relpath`,
    which yields a `../`-style path rather than raising) — defensive, not
    expected in practice.

    Caveat: under parallel + fail_fast, `checks_run` is a LOWER BOUND — checks
    that already completed but whose results were dropped on a must_pass failure
    are NOT counted (see `run_checks`). It is not a count of every check that
    executed. The VERDICT is unaffected: the failing must_pass check that
    triggered the abort is always collected, so `all_pass_must` stays correct.

    Returns:
        `{change_id, all_pass_must, checks_run, results[...], summary_path}` where
        each result row is `{id, status, exit_code, duration_ms, must_pass,
        output_path}` (`output_path` repo-relative or `None`) and `summary_path`
        is the repo-relative path to `archive/summary.json`.
    """
    return {
        "change_id": change_id,
        "all_pass_must": _all_pass_must(results),
        "checks_run": len(results),
        "results": [
            {
                "id": r.id,
                "status": r.status,
                "exit_code": r.exit_code,
                "duration_ms": r.duration_ms,
                "must_pass": r.must_pass,
                "output_path": _repo_relative(r.output_path, workspace_root),
            }
            for r in results
        ],
        "summary_path": _repo_relative(str(archive / "summary.json"), workspace_root),
    }


def _repo_relative(path: str | None, workspace_root: Path) -> str | None:
    """Return `path` relative to `workspace_root` (repo-relative, no leading `/`).

    `None` passes through unchanged (an un-archived check's `output_path`). Uses
    `os.path.relpath` so a path outside the root degrades to a `../`-style
    relative path instead of raising — the frozen `--json` contract must never
    crash on an unexpected absolute path.
    """
    if path is None:
        return None
    return os.path.relpath(path, workspace_root)


def make_verification_event(
    evt_type: Literal["verification_passed", "verification_failed"],
    change_id: str,
    results: list[CheckResult],
    archive: Path,
) -> Event:
    """Build the `verification_passed` / `verification_failed` event to emit.

    Payloads match sensor-gate §3.1.3 EXACTLY:

    - `verification_passed`: `{"checks_run", "all_pass_must"}`.
    - `verification_failed`: `{"failed_checks": [{"id","command","exit_code",
      "output_path"}], "suggested_fix"}` — one entry per *must_pass* check that
      did not pass.

    The dispatcher (`SensorDispatcher._handle`) STAMPS `event_id` (when blank),
    `timestamp` (when blank), and ALWAYS overwrites `actor`. So we leave
    `event_id`/`timestamp` empty and pass a placeholder `actor` the dispatcher
    discards. We DO set `type`, `change_id`, `framework`, and `payload` (the
    fields `_handle` copies straight through from the sensor's emit_events).

    Args:
        evt_type: `"verification_passed"` or `"verification_failed"`.
        change_id: The run's change id (becomes `Event.change_id`).
        results: Every `CheckResult` from the run.
        archive: Per-run archive dir (reserved; not in either payload).

    Returns:
        The `Event` for `SensorResult.emit_events`.
    """
    if evt_type == "verification_passed":
        payload: dict[str, Any] = {
            "checks_run": len(results),
            "all_pass_must": _all_pass_must(results),
        }
    else:
        failed = [r for r in results if r.must_pass and r.status != "pass"]
        payload = {
            "failed_checks": [
                {
                    "id": r.id,
                    "command": r.command,
                    "exit_code": r.exit_code,
                    "output_path": r.output_path,
                }
                for r in failed
            ],
            "suggested_fix": (
                "Re-run the failed checks locally, fix the underlying issues, "
                "then re-run `super-harness verify`."
            ),
        }

    return Event(
        # event_id / timestamp left blank — the dispatcher stamps them.
        event_id="",
        type=evt_type,
        change_id=change_id,
        timestamp="",
        # actor is ALWAYS overwritten by the dispatcher; placeholder only.
        actor=Actor(type="sensor", identifier="verification-runner"),
        framework="plain",
        payload=payload,
    )


def _all_pass_must(results: list[CheckResult]) -> bool:
    """True iff every *must_pass* check passed (advisory checks are ignored)."""
    return all(r.status == "pass" for r in results if r.must_pass)


class VerificationRunner(Sensor):
    """Sensor that runs `.harness/verification.yaml` checks and emits a verdict.

    Triggered by the `cli_done` / `cli_verify` activities (the `verify` / `done`
    CLI commands; wiring lands in Task 8.6/8.7). Loads the config, collects
    `CheckTask`s across the three layers (baseline stubbed to `[]` in Task 8.4),
    runs them per `execution.{mode,max_parallelism,fail_fast}`, archives a
    `summary.json`, and emits `verification_passed` / `verification_failed`.

    The verdict is `passed` iff EVERY *must_pass* check passed; advisory
    (`must_pass: false`) checks never fail the run.
    """

    name: ClassVar[str] = "verification-runner"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ()
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = (
        "cli_done",
        "cli_verify",
    )
    determinism: ClassVar[Determinism] = "computational"

    def check(
        self, trigger: Event | Activity, context: WorkspaceContext
    ) -> SensorResult:
        change_id = getattr(trigger, "change_id", None) or context.active_change_id
        if change_id is None:
            # Defensive: no change to verify (neither the trigger nor the
            # workspace context carries one). Fail loudly without crashing.
            return SensorResult(
                status="fail",
                summary="verification skipped: no change_id available",
            )

        cfg = load_verification_config(verification_yaml_path(context.workspace_root))
        variables = build_variables(change_id, context)
        payload = getattr(trigger, "payload", {}) or {}
        layer = payload.get("layer")
        only_ids = payload.get("checks")

        archive = verification_results_dir(context.workspace_root, change_id, utc_now_iso())
        tasks = collect_checks(
            cfg,
            context=context,
            archive=archive,
            variables=variables,
            layer=layer,
            only_ids=only_ids,
        )
        results = run_checks(
            tasks,
            mode=cfg.execution.mode,
            max_workers=cfg.execution.max_parallelism,
            fail_fast=cfg.execution.fail_fast,
        )

        must_pass_failed = [r for r in results if r.must_pass and r.status != "pass"]
        verdict = "passed" if not must_pass_failed else "failed"
        write_summary_json(archive, results, verdict)

        evt_type: Literal["verification_passed", "verification_failed"] = (
            "verification_passed" if verdict == "passed" else "verification_failed"
        )
        status: SensorStatus = "pass" if verdict == "passed" else "fail"
        return SensorResult(
            status=status,
            summary=(
                f"verification {verdict} "
                f"({len(results)} checks, {len(must_pass_failed)} failed)"
            ),
            details=verify_data_block(
                change_id, results, archive, context.workspace_root
            ),
            emit_events=[make_verification_event(evt_type, change_id, results, archive)],
        )
