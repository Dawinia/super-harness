from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from super_harness.engineering.verification_config import CheckSpec
from super_harness.sensors.verification_runner import CheckResult, run_check

# subprocess.run(env=...) REPLACES the environment, so any command resolved via
# PATH (sleep, ls, python …) needs PATH present. See run_check docstring.
# (`true`/`false` are shell builtins and need no PATH, but we keep PATH for the
# rest.)
_ENV: dict[str, str] = {"PATH": os.environ["PATH"]}


def _spec(
    *,
    command: str,
    check_id: str = "c",
    must_pass: bool = True,
    timeout_seconds: int = 30,
    capture: str = "both",
    workdir: str = ".",
    env: dict[str, str] | None = None,
) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        command=command,
        must_pass=must_pass,
        timeout_seconds=timeout_seconds,
        capture=capture,
        workdir=workdir,
        env=env if env is not None else {},
    )


def test_pass_zero_exit(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="true", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert res.status == "pass"
    assert res.exit_code == 0
    assert res.must_pass is True


def test_fail_nonzero_exit(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="false", capture="none", must_pass=False),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert res.status == "fail"
    assert res.exit_code == 1
    assert res.must_pass is False


def test_timeout(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="sleep 5", timeout_seconds=1, capture="both"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert res.status == "timeout"
    assert res.exit_code == -1
    assert res.must_pass is True
    # command is still populated on timeout; output_path is None (nothing archived).
    assert res.command == "sleep 5"
    assert res.output_path is None
    # No archive files written on timeout.
    assert not (tmp_path / "arch" / "c.stdout").exists()
    assert not (tmp_path / "arch" / "c.stderr").exists()


def test_capture_stdout_only(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo hello", capture="stdout"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert res.status == "pass"
    assert (archive / "c.stdout").read_text() == "hello\n"
    assert not (archive / "c.stderr").exists()
    assert res.output_path == str(archive / "c.stdout")


def test_capture_stderr_only(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo oops 1>&2", capture="stderr"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert (archive / "c.stderr").read_text() == "oops\n"
    assert not (archive / "c.stdout").exists()
    assert res.output_path == str(archive / "c.stderr")


def test_capture_both(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo out; echo err 1>&2", capture="both"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert (archive / "c.stdout").read_text() == "out\n"
    assert (archive / "c.stderr").read_text() == "err\n"
    assert res.output_path == str(archive)


def test_capture_none(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo hello", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert res.status == "pass"
    assert not (archive / "c.stdout").exists()
    assert not (archive / "c.stderr").exists()
    assert res.output_path is None


def test_interpolation_applied(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo ${SLUG}", capture="stdout"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={"SLUG": "x"},
    )
    assert (archive / "c.stdout").read_text() == "x\n"
    # command field holds the INTERPOLATED string actually run.
    assert res.command == "echo x"


def test_duration_ms_is_nonneg_int(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="true", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert isinstance(res.duration_ms, int)
    assert res.duration_ms >= 0


def test_command_field_holds_interpolated(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="echo ${CHANGE_ID}", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={"CHANGE_ID": "abc-123"},
    )
    assert res.command == "echo abc-123"


def test_workdir_is_used(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "marker.txt").write_text("here")
    res = run_check(
        _spec(command="ls", capture="stdout"),
        workdir=sub,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert "marker.txt" in (archive / "c.stdout").read_text()
    assert res.status == "pass"


def test_archive_dir_created_when_missing(tmp_path: Path) -> None:
    archive = tmp_path / "nested" / "deep" / "arch"
    assert not archive.exists()
    run_check(
        _spec(command="echo hi", capture="stdout"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert (archive / "c.stdout").exists()


def test_check_result_is_frozen() -> None:
    res = CheckResult(
        id="c",
        status="pass",
        exit_code=0,
        duration_ms=1,
        must_pass=True,
        command="echo hi",
        output_path=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.status = "fail"  # type: ignore[misc]
