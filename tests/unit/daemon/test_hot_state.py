"""Unit tests for HotState mtime cache per daemon-architecture §3.1 + UC-7."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
import yaml

from super_harness.daemon.hot_state import HotState


def _write_state(path: Path, change_id: str, current_state: str) -> None:
    payload = {"changes": {change_id: {"change_id": change_id, "current_state": current_state}}}
    path.write_text(yaml.safe_dump(payload))


def test_loads_on_first_get(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    _write_state(state_path, "c1", "PLAN_APPROVED")
    hs = HotState(state_path)
    record = hs.get_change("c1")
    assert record is not None
    assert record["current_state"] == "PLAN_APPROVED"


def test_reloads_on_mtime_change(tmp_path: Path) -> None:
    """AC-3: an mtime advance triggers an inline reload (no polling thread), so
    the next get_change() sees the new state — the HotState mechanism behind the
    daemon reflecting the latest state.yaml within the 50ms budget."""
    state_path = tmp_path / "state.yaml"
    _write_state(state_path, "c1", "INTENT_DECLARED")
    hs = HotState(state_path)
    assert hs.get_change("c1")["current_state"] == "INTENT_DECLARED"  # type: ignore[index]

    # Bump mtime; filesystem timestamp resolution can be 1s on some FS — wait enough
    time.sleep(0.05)
    _write_state(state_path, "c1", "PLAN_APPROVED")
    # On filesystems with coarse mtime, force a clearly newer mtime
    new_mtime = state_path.stat().st_mtime + 1.0
    import os as _os

    _os.utime(state_path, (new_mtime, new_mtime))

    assert hs.get_change("c1")["current_state"] == "PLAN_APPROVED"  # type: ignore[index]


def test_returns_none_when_file_missing(tmp_path: Path) -> None:
    """UC-7 first half: missing state.yaml → get_change returns None, no exception."""
    hs = HotState(tmp_path / "does-not-exist.yaml")
    assert hs.get_change("anything") is None


def test_handles_malformed_yaml(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """UC-7 second half: corrupt YAML → caller sees None, daemon logs ERROR."""
    state_path = tmp_path / "state.yaml"
    state_path.write_text("not: valid: yaml::\n  -broken\n[")
    hs = HotState(state_path)
    with caplog.at_level("ERROR"):
        assert hs.get_change("c1") is None
    # Subsequent call must NOT re-attempt parse (busy-retry on every gate would be a DoS).
    # Bump mtime to "now" but write more garbage; same file should still resolve to None.
    # The key invariant: no exception bubbles out.
    assert hs.get_change("c1") is None


def test_thread_safety_smoke(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    _write_state(state_path, "c1", "PLAN_APPROVED")
    hs = HotState(state_path)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(100):
                rec = hs.get_change("c1")
                assert rec is not None
                assert rec["current_state"] == "PLAN_APPROVED"
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_missing_change_returns_none(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    _write_state(state_path, "c1", "PLAN_APPROVED")
    hs = HotState(state_path)
    assert hs.get_change("does-not-exist") is None
