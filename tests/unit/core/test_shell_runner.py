"""Unit tests for core.shell_runner — the single subprocess check primitive."""
from __future__ import annotations

import os
import shlex
import time

import pytest

from super_harness.core.shell_runner import ShellResult, run_shell, scrubbed_environ


def test_zero_exit_captures_stdout(tmp_path):
    res = run_shell("echo hi", cwd=tmp_path, timeout=10)
    assert isinstance(res, ShellResult)
    assert res.exit_code == 0
    assert res.stdout == "hi\n"
    assert res.timed_out is False
    assert res.spawn_error is None
    assert res.duration_ms >= 0


def test_nonzero_exit_captures_stderr(tmp_path):
    res = run_shell("echo err >&2; exit 3", cwd=tmp_path, timeout=10)
    assert res.exit_code == 3
    assert res.stderr == "err\n"
    assert res.timed_out is False


def test_plain_timeout_is_bounded(tmp_path):
    t0 = time.monotonic()
    res = run_shell("sleep 5", cwd=tmp_path, timeout=1)
    assert time.monotonic() - t0 < 10
    assert res.timed_out is True
    assert res.exit_code == -1
    assert res.spawn_error is None


def _wait_for(path, timeout=3.0):
    """Poll until `path` exists (bounded); return whether it appeared."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


@pytest.mark.skipif(os.name != "posix", reason="process-group kill is POSIX-only")
def test_timeout_kills_process_group(tmp_path):
    # The backgrounded grandchild touches `spawned` immediately (a handshake
    # proving it really started — so the test can't pass vacuously by the child
    # never running), then sleeps and would touch `killed` after 1s. It inherits
    # the stdout pipe, so communicate() blocks past the shell's exit. On timeout
    # run_shell kills the WHOLE group (start_new_session + killpg); if only the
    # shell were killed, the orphan would touch `killed` after 1s.
    spawned = tmp_path / "spawned"
    killed = tmp_path / "killed"
    sq, kq = shlex.quote(str(spawned)), shlex.quote(str(killed))
    cmd = f"(touch {sq}; sleep 1; touch {kq}) & echo started"
    t0 = time.monotonic()
    res = run_shell(cmd, cwd=tmp_path, timeout=0.4)
    assert time.monotonic() - t0 < 10  # bounded: external clock, generous margin
    assert res.timed_out is True
    assert res.exit_code == -1
    assert _wait_for(spawned), "grandchild never started — test would be vacuous"
    time.sleep(1.3)  # wait past the grandchild's would-be second touch
    assert not killed.exists()  # group kill reaped it before the 1s mark


def test_env_dict_replaces_child_environment(tmp_path):
    res = run_shell(
        "echo $PROBE",
        cwd=tmp_path,
        timeout=10,
        env={"PATH": os.environ["PATH"], "PROBE": "x"},
    )
    assert res.stdout == "x\n"


def test_env_none_inherits_ambient(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLRUNNER_PROBE", "inherited")
    res = run_shell("echo $SHELLRUNNER_PROBE", cwd=tmp_path, timeout=10)
    assert res.stdout == "inherited\n"


def test_spawn_failure_reports_error_without_raising(tmp_path):
    res = run_shell("true", cwd=tmp_path / "missing", timeout=10)
    assert res.spawn_error is not None
    assert res.exit_code == -1
    assert res.timed_out is False


def test_invalid_utf8_output_is_replaced_not_raised(tmp_path):
    # octal escape: POSIX-portable (dash's printf prints `\xff` literally)
    res = run_shell(r"printf '\377'", cwd=tmp_path, timeout=10)
    assert res.exit_code == 0
    assert "�" in res.stdout


def test_scrubbed_environ_strips_harness_prefix(monkeypatch):
    monkeypatch.setenv("SUPER_HARNESS_X", "1")
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "poison")
    scrubbed = scrubbed_environ()
    assert not any(k.startswith("SUPER_HARNESS_") for k in scrubbed)
    assert "PATH" in scrubbed
    assert os.environ.get("SUPER_HARNESS_X") == "1"  # ambient not mutated
