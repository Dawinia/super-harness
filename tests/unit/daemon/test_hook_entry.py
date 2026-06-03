"""Unit tests for hook_entry._decide's file-based kill switch."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from super_harness.daemon.hook_entry import _decide


def _init_blocking_workspace(root: Path) -> None:
    """A workspace whose active change is in a BLOCKING state (AWAITING_PLAN_REVIEW)."""
    (root / ".harness").mkdir()
    (root / ".harness" / "state.yaml").write_text(
        yaml.safe_dump(
            {"changes": {"ch1": {"change_id": "ch1",
                                 "current_state": "AWAITING_PLAN_REVIEW"}}}
        )
    )


def test_gate_disabled_sentinel_forces_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.harness/gate-disabled` short-circuits to ALLOW even when the active
    change is in a blocking state — without contacting the daemon."""
    _init_blocking_workspace(tmp_path)
    (tmp_path / ".harness" / "gate-disabled").touch()
    monkeypatch.chdir(tmp_path)  # _decide resolves root from cwd

    decision, reason = _decide("Edit", str(tmp_path / "foo.py"))

    assert decision == "allow"
    assert "gate-disabled" in reason


def test_gate_disabled_sentinel_allows_with_corrupt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill switch is the design's strongest robustness claim: the sentinel
    is checked BEFORE any state read, so a missing/corrupt `.harness/state.yaml`
    can never trap the user. With a CORRUPT state and the sentinel present,
    `_decide` must short-circuit to ALLOW and never touch the daemon path."""
    (tmp_path / ".harness").mkdir()
    # CORRUPT, non-mapping state — would raise if any code tried to read it.
    (tmp_path / ".harness" / "state.yaml").write_text(": : not valid yaml : :\n")
    (tmp_path / ".harness" / "gate-disabled").touch()
    monkeypatch.chdir(tmp_path)  # _decide resolves root from cwd

    # The daemon path must NEVER run while the sentinel is present: if it does,
    # fail loudly (the hook does a late `from super_harness.daemon import
    # supervisor` inside _decide, so patch the attribute on that module).
    monkeypatch.setattr(
        "super_harness.daemon.supervisor.gate_pre_tool_use",
        lambda *a, **k: pytest.fail("daemon path must not run when gate-disabled"),
    )

    decision, reason = _decide("Edit", str(tmp_path / "foo.py"))

    assert decision == "allow"
    assert "gate-disabled" in reason
