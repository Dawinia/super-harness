"""Unit test for the deferral property (design 2026-07-29): `_decide` must skip
the `.harness/plan-paths.yaml` read entirely unless the active state is one that
can use it (`PLAN_PATH_ALLOW_STATES`). This is a PERFORMANCE property, not an
allow/block one — `load_plan_paths` fails closed to `[]` and the gate re-checks
`PLAN_PATH_ALLOW_STATES` itself, so reading or not reading yields the same
verdict either way. Assert directly on whether the read happened, not on the
exit code / decision.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import super_harness.core.plan_paths as plan_paths
from super_harness.daemon import hook_entry


def _repo(tmp_path: Path, change_id: str, state: str) -> Path:
    """Minimal workspace: .harness/ plus one change in the requested state."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "state.yaml").write_text(
        yaml.safe_dump(
            {"changes": {change_id: {
                "change_id": change_id,
                "current_state": state,
                "last_event_at": "2026-07-29T00:00:00Z",
                "plan_artifacts": [],
            }}}
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize(
    "state,expect_read",
    [("INTENT_DECLARED", True), ("IMPLEMENTATION_IN_PROGRESS", False),
     ("AWAITING_CODE_REVIEW", False), ("READY_TO_MERGE", False)],
)
def test_plan_path_config_read_only_where_it_is_consulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, expect_read: bool
) -> None:
    # `_decide` honours SUPER_HARNESS_CHANGE_ID as a change-id override; a value
    # leaking in from the ambient environment would resolve a different (or no)
    # change and silently invalidate the parametrisation. Same hazard #60 fixed.
    monkeypatch.delenv("SUPER_HARNESS_CHANGE_ID", raising=False)
    root = _repo(tmp_path, "my-change", state)
    calls: list = []
    monkeypatch.setattr(
        plan_paths, "load_plan_paths", lambda r: calls.append(r) or []
    )
    monkeypatch.chdir(root)
    hook_entry._decide("Edit", "src/api.py")
    assert bool(calls) is expect_read
