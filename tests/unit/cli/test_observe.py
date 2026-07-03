"""Unit tests for the `observe` command group (renamed from `daemon`)."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.daemon import supervisor


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def test_status_not_running(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: False)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "status"])
    assert res.exit_code != 0
    assert "not running" in res.output


def test_status_running_json(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: True)
    monkeypatch.setattr(supervisor, "_read_pid", lambda root: 4242)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "--json", "observe", "status"])
    assert res.exit_code == 0
    # json_envelope emits compact separators (",", ":") — no space after the colon.
    assert '"running":true' in res.output
    assert "4242" in res.output


def test_start_reports_pid(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "ensure_running", lambda root, **k: 777)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "start"])
    assert res.exit_code == 0
    assert "777" in res.output


def test_start_surfaces_spawn_failure(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root, **k):
        raise RuntimeError("observer host binary not found")

    monkeypatch.setattr(supervisor, "ensure_running", boom)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "start"])
    assert res.exit_code != 0
    assert "not found" in res.output


def test_stop_when_not_running(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: False)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "stop"])
    assert res.exit_code != 0
    assert "not running" in res.output


def test_stop_success(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: True)
    monkeypatch.setattr(supervisor, "stop", lambda root, **k: True)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "stop"])
    assert res.exit_code == 0
    assert "stopped" in res.output
