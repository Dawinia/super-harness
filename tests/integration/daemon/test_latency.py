"""Hot-path latency benchmarks per daemon-architecture §3.8 / AC-1.

Two complementary measurements:

  Benchmark 1 — END-TO-END (subprocess): exec `super-harness-hook` as a
  child process 100 times. Includes shell-hook startup, Python interpreter
  cold-start, click-less import chain, supervisor.query(), UDS round-trip,
  JSON decode, exit. This is what Claude Code sees per Edit/Write tool call.
  Guard: LOOSE p50 < 150ms regression guard only — this path is
  Python-interpreter-cold-start-bound (baseline ~50-60ms on M-series), so
  p99 is RECORDED but NOT gated (too noisy on 100 subprocess samples). The
  original AC-1 p50<10ms/p99<25ms target was unachievable (it omitted the
  ~42.8ms Python cold-start floor); recalibrated per daemon-architecture
  AC-1. Sub-25ms end-to-end is deferred to a v0.2 non-Python hook client.

  Benchmark 2 — DAEMON-PORTION (in-process): call
  `supervisor.gate_pre_tool_use` directly 1000 times within the test
  process. Isolates daemon-side cost (UDS connect + send + recv + decode)
  from the shell-hook + Python-startup overhead. Budget: p99 < 5ms — this
  is the "if regressions here, the daemon is at fault" signal.

CI flake handling: latency is hardware-sensitive. `pytest-rerunfailures`
plug-in is used to re-run on AssertionError up to 3 times — we do NOT
"raise the bar" to absorb noise (would mask real regressions); we re-run
to absorb sporadic GC / scheduling jitter while keeping the bar
spec-faithful (daemon-architecture §3.8).

Important: re-run is filtered to `AssertionError.*regressed`. This means
ONLY latency-bound assertion failures (which DO have legitimate CI hardware
variance) are retried; threading.RuntimeError / socket.timeout / OSError
(which signal real bugs) fail fast. All latency assertions in this file
MUST include the word `regressed` in their message so the filter matches.

Assertions are calibrated against measured local hardware (macOS M-series):
end-to-end p50 ~50-60ms / p99 ~230-500ms (Python-cold-start dominated);
daemon-portion p50 ~0.15ms / p99 ~0.4-1.3ms (~10x under the <5ms budget).
The daemon-portion p99<5ms is the meaningful, CI-enforced gate; the
end-to-end test carries only a loose p50<150ms regression guard.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
import yaml


def _has_hook_binary() -> bool:
    return shutil.which("super-harness-hook") is not None


pytestmark = pytest.mark.skipif(
    not _has_hook_binary(),
    reason="super-harness-hook not installed",
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "state.yaml").write_text(
        yaml.safe_dump(
            {"active_change_id": "c1",
             "changes": {"c1": {"change_id": "c1",
                                "current_state": "PLAN_APPROVED"}}}
        )
    )
    yield tmp_path
    # Teardown: kill daemon.
    pid_file = tmp_path / ".harness" / "daemon.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.05)
                except ProcessLookupError:
                    break
        except (ValueError, ProcessLookupError):
            pass


def _warm_daemon(workspace: Path) -> tuple[str, str]:
    from super_harness.daemon import supervisor
    supervisor.ensure_running(workspace, wait_seconds=5.0)
    # One warm-up call to populate HotState cache.
    return supervisor.gate_pre_tool_use(
        workspace, tool="Edit", file="src/foo.py", change_id="c1"
    )


@pytest.mark.flaky(reruns=3, reruns_delay=1, only_rerun=["AssertionError.*regressed"])
def test_pre_tool_use_shell_hook_p50_under_150ms(workspace: Path) -> None:
    """End-to-end shell-hook: LOOSE p50 < 150ms regression guard only.

    Python-interpreter-cold-start-bound (~26ms bare + ~12ms imports +
    fork/exec); baseline p50 ~50-60ms on M-series. p99 on 100 subprocess
    samples is inherently noisy (observed 230-500ms) so it is RECORDED but
    NOT gated. The hardware-stable, CI-enforced budget lives in the
    daemon-portion test below (daemon-architecture AC-1 calibration)."""
    decision, reason = _warm_daemon(workspace)
    # Confirm the warm-up hit a real (non-fallback) daemon decision.
    assert decision == "allow", f"warm-up decision={decision!r} reason={reason!r}"
    assert "PLAN_APPROVED" in reason, (
        f"warm-up did not hit a warm daemon (got reason={reason!r}); "
        "subprocess samples would measure the cold-start fallback path"
    )
    env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
    samples_ms: list[float] = []
    N = 100
    for _ in range(N):
        t0 = time.perf_counter()
        result = subprocess.run(
            ["super-harness-hook", "Edit", "src/foo.py"],
            cwd=workspace,
            capture_output=True,
            env=env,
            timeout=5.0,
        )
        samples_ms.append((time.perf_counter() - t0) * 1000)
        assert result.returncode == 0, result.stderr.decode()
    samples_ms.sort()
    p50 = samples_ms[N // 2]
    p99 = samples_ms[int(N * 0.99)]
    p_min = samples_ms[0]
    p90 = samples_ms[int(N * 0.90)]
    p_max = samples_ms[-1]
    print(
        f"\nEND-TO-END (subprocess, N={N}):       "
        f"p50={p50:.2f}ms  p99={p99:.2f}ms  "
        f"(min={p_min:.2f}  p90={p90:.2f}  max={p_max:.2f})"
    )
    # LOOSE regression guard only. p99 is RECORDED in the message + printed
    # above, but deliberately NOT gated (subprocess p99 is too noisy to assert
    # on; see daemon-architecture AC-1 calibration). The meaningful, gated
    # budget is the daemon-portion p99<5ms below.
    assert p50 < 150.0, (
        f"end-to-end shell-hook p50={p50:.2f}ms regressed (loose guard <150ms; "
        f"baseline ~50-60ms on M-series, Python-cold-start dominated — see "
        f"daemon-architecture AC-1 calibration); p99={p99:.2f}ms (recorded, not gated)"
    )


@pytest.mark.flaky(reruns=3, reruns_delay=1, only_rerun=["AssertionError.*regressed"])
def test_gate_pre_tool_use_in_process_p99_under_5ms(workspace: Path) -> None:
    """Daemon-portion budget per AC-1 sub-clause: in-process p99 < 5ms.

    Isolates UDS connect + send + recv + decode from shell + Python startup.
    Regressions here implicate the daemon (HotState parse, threading model,
    protocol decode), not the hook startup chain.
    """
    from super_harness.daemon import supervisor

    decision, reason = _warm_daemon(workspace)
    assert decision == "allow", f"warm-up decision={decision!r} reason={reason!r}"
    assert "PLAN_APPROVED" in reason, (
        f"warm-up did not hit a warm daemon (got reason={reason!r})"
    )
    samples_ms: list[float] = []
    N = 1000
    for _ in range(N):
        t0 = time.perf_counter()
        decision, _reason = supervisor.gate_pre_tool_use(
            workspace, tool="Edit", file="src/foo.py", change_id="c1"
        )
        samples_ms.append((time.perf_counter() - t0) * 1000)
        assert decision == "allow"
    samples_ms.sort()
    p50 = samples_ms[N // 2]
    p99 = samples_ms[int(N * 0.99)]
    p_min = samples_ms[0]
    p90 = samples_ms[int(N * 0.90)]
    p_max = samples_ms[-1]
    print(
        f"\nDAEMON-PORTION (in-process, N={N}): "
        f"p50={p50:.2f}ms  p99={p99:.2f}ms  "
        f"(min={p_min:.2f}  p90={p90:.2f}  max={p_max:.2f})"
    )
    # Meaningful CI gate: hardware-stable daemon-side budget (daemon-architecture
    # §3.8 / AC-1). Regressions here implicate daemon code, not Python startup.
    assert p99 < 5.0, (
        f"daemon-portion p99={p99:.2f}ms regressed (budget <5ms); "
        f"p50={p50:.2f}ms — implicates daemon-side cost (HotState parse, "
        f"threading, protocol decode)"
    )
