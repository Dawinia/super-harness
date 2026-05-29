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

**Baseline layer is a Task 8.5 stub here** — `baseline_check_tasks` returns `[]`.
The 3 real baselines (anchor-sentinel-presence-final / lifecycle-ordering /
scope-vs-plan-final) and `find_ordering_violations` are out of scope for 8.4.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from super_harness.core.events import Actor, Event
from super_harness.core.paths import verification_results_dir, verification_yaml_path
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
    "CheckResult",
    "CheckTask",
    "VerificationRunner",
    "build_variables",
    "collect_checks",
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


def _ts() -> str:
    """ISO-8601 UTC timestamp for the per-run archive dir (matches lifecycle §2)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_variables(change_id: str, context: WorkspaceContext) -> dict[str, str]:
    """Build the `${NAME}` interpolation variables for a verification run.

    `SLUG` and `CHANGE_ID` are aliases of the change id (per the
    `verification_config` allowlist). `SPEC_PATH` / `PLAN_PATH` are HARDCODED
    EMPTY in v0.1 — the allowlist accepts the names but they substitute to `""`
    until spec/plan path resolution lands in a later version.

    Args:
        change_id: The change this run is for (already None-resolved by caller).
        context: The workspace snapshot (reserved for future spec/plan lookup).

    Returns:
        A mapping consumed by `interpolate` for every check `command`.
    """
    return {
        "SLUG": change_id,
        "CHANGE_ID": change_id,
        "SPEC_PATH": "",
        "PLAN_PATH": "",
    }


def baseline_check_tasks(
    cfg: VerificationConfig,
    *,
    context: WorkspaceContext,
    archive: Path,
    variables: dict[str, str],
    only_ids: list[str] | None = None,
) -> list[CheckTask]:
    """Build the baseline-layer `CheckTask`s — a STUB returning `[]` in Task 8.4.

    The baseline layer is the 3 in-process Python checks (anchor-sentinel-
    presence-final / lifecycle-ordering / scope-vs-plan-final). Those, plus
    `find_ordering_violations`, are implemented in Task 8.5; this function is the
    seam they plug into.

    The signature already accepts everything the real baselines will need
    (`context` for workspace access, `archive` for any written artifacts,
    `variables`/`only_ids` for parity with `collect_checks`) so Task 8.5 fills
    the body without touching `collect_checks`.

    Returns:
        `[]` — no baseline checks run in v0.1 Task 8.4.
    """
    # Task 8.5 plugs the 3 real baselines in here.
    return []


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
        wanted = set(only_ids)
        tasks = [t for t in tasks if t.id in wanted]

    return tasks


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
    != "pass"), remaining work is abandoned — in sequential mode the loop breaks;
    in parallel mode not-yet-started futures are cancelled (mirroring
    `SensorDispatcher._run_all`'s `future.cancel()` stance — running futures
    cannot be killed in CPython and complete on their own, but their results are
    dropped). Results are returned in task-submission order for determinism,
    regardless of completion order.

    Args:
        tasks: The runnable units (already filtered by `collect_checks`).
        mode: `"parallel"` or `"sequential"` (from `execution.mode`).
        max_workers: Thread-pool bound for parallel mode (`execution.max_parallelism`).
        fail_fast: Abort remaining checks after a must_pass failure (`execution.fail_fast`).

    Returns:
        One `CheckResult` per task that actually ran, in submission order.
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
    # failure cancel any not-yet-started futures (running ones are abandoned).
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

    Args:
        archive: The per-run archive dir (`verification_results_dir(...)`).
        results: Every `CheckResult` produced this run.
        verdict: `"passed"` or `"failed"`.
    """
    archive.mkdir(parents=True, exist_ok=True)
    summary = {
        "verdict": verdict,
        "generated_at": _ts(),
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
) -> dict[str, Any]:
    """Build the FROZEN `verify --json` data block (cli-command-surface §3.4).

    Returned verbatim as `SensorResult.details`. Keys are a frozen contract —
    do NOT add/rename/reorder downstream-visible keys.

    Returns:
        `{change_id, all_pass_must, checks_run, results[...], summary_path}` where
        each result row is `{id, status, exit_code, duration_ms, must_pass,
        output_path}` and `summary_path` points at `archive/summary.json`.
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
                "output_path": r.output_path,
            }
            for r in results
        ],
        "summary_path": str(archive / "summary.json"),
    }


def make_verification_event(
    evt_type: str,
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

        archive = verification_results_dir(context.workspace_root, change_id, _ts())
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

        evt_type = "verification_passed" if verdict == "passed" else "verification_failed"
        status: SensorStatus = "pass" if verdict == "passed" else "fail"
        return SensorResult(
            status=status,
            summary=(
                f"verification {verdict} "
                f"({len(results)} checks, {len(must_pass_failed)} failed)"
            ),
            details=verify_data_block(change_id, results, archive),
            emit_events=[make_verification_event(evt_type, change_id, results, archive)],
        )
