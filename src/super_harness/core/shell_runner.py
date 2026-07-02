"""One leaf primitive for running a shell check: timeout, group kill, reap.

Both check runners (`core.check_runner.run_one_check`, decision checks;
`sensors.verification_runner.run_check`, verification checks) wrap this so the
timeout/kill/reap/env semantics have a single point of truth. `shell=True` is
intentional on both paths: commands are repo-owner-trusted (ratified
hash-locked checks / verification.yaml); the trust boundary is upstream of
this primitive, which runs any string it is handed.

Lives in core because sensors→core is the legal import direction
(d-core-is-base forbids core→sensors); imports stdlib only.

API stability: **experimental** (v0.1).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ShellResult", "run_shell", "scrubbed_environ"]

_HARNESS_ENV_PREFIX = "SUPER_HARNESS_"


@dataclass(frozen=True)
class ShellResult:
    """Raw outcome of one `run_shell` call; callers shape it to their domain."""

    exit_code: int          # -1 sentinel when timed_out or spawn_error
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    spawn_error: str | None  # OSError text when the shell could not launch


def scrubbed_environ() -> dict[str, str]:
    """Ambient `os.environ` minus every ``SUPER_HARNESS_*`` knob.

    Check subprocesses must run in a clean-room with respect to harness-control
    env (as CI does). Otherwise an exported knob — e.g.
    ``SUPER_HARNESS_CHANGE_ID`` set for the self-host lifecycle — leaks into
    the child (and any hooks it spawns), changing gate behaviour and causing
    false failures, or letting an authoring-time verdict diverge from the
    merge-gate verdict. This drops EVERY knob by design — identity and perf
    tuners alike; a check that needs a value re-declares it (verification
    `defaults.env`/`spec.env`, or inline in a ratified check snippet). Scrubs
    the ambient base only; `os.environ` is never mutated.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_HARNESS_ENV_PREFIX)
    }


def run_shell(
    command: str,
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> ShellResult:
    """Run `command` through the shell; never raises.

    `env=None` inherits the ambient environment; a dict REPLACES the entire
    child environment (it must include `PATH` or PATH-resolved commands fail
    to launch).

    On timeout the whole process GROUP is killed, not just the shell: with
    `start_new_session=True` the child is its own group leader (pgid == pid),
    so `os.killpg(proc.pid, SIGKILL)` reaps backgrounded grandchildren (e.g. a
    hung test run under the shell) that would otherwise be orphaned — left
    running unsupervised and holding the output pipes. We target `proc.pid`
    (not `os.getpgid`, which raises once the shell leader has exited) and
    bound the post-kill reap so a stuck grandchild can never hang the caller.
    On timeout any collected output is returned BEST-EFFORT (may be empty; not
    a contract — both wrappers ignore it).
    """
    t0 = time.perf_counter()  # measure real elapsed wall-clock
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", start_new_session=True,
        )
    except OSError as e:  # shell missing, bad cwd, etc.
        return ShellResult(
            exit_code=-1, stdout="", stderr="", timed_out=False,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            spawn_error=str(e),
        )
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()  # best effort: at least the direct child
        try:
            # bounded reap; a SIGKILL'd group EOFs the pipes fast
            out, err = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            out, err = "", ""  # give up reaping; the group is killed, never hang
    return ShellResult(
        exit_code=-1 if timed_out else proc.returncode,
        stdout=out or "",
        stderr=err or "",
        timed_out=timed_out,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        spawn_error=None,
    )
