"""Single-check subprocess executor for the verification runner.

Phase 8 task 8.3 (engineering-integration §2.3 / §3.6). This module owns ONE
narrow responsibility: run a single `CheckSpec` as a subprocess, with a timeout
and optional output capture, and return a structured `CheckResult`.

Out of scope here (later tasks 8.4/8.5): collecting checks from a config,
sequential/parallel scheduling, the `VerificationRunner` sensor class, baseline
checks, building interpolation variables, and repo-root path helpers. This file
provides only the leaf primitives `CheckResult` + `run_check`.

**`shell=True` is intentional** (spec §3.6 #7): `verification.yaml` is
repo-owner-trusted, so check commands run through the shell exactly as written.
No shell-escaping is applied. Variable interpolation is restricted by the
`engineering.verification_config` allowlist (untrusted PR-context names cannot be
referenced), so the only injection surface is the repo owner's own config.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from super_harness.engineering.verification_config import CheckSpec, interpolate

__all__ = ["CheckResult", "run_check"]

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
