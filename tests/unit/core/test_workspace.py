"""Tests for the relocated WorkspaceContext (Cut B — now core-owned)."""
from __future__ import annotations

from pathlib import Path


def test_workspace_context_lives_in_core() -> None:
    from super_harness.core.workspace import WorkspaceContext

    ctx = WorkspaceContext(workspace_root=Path("/x"))
    assert ctx.workspace_root == Path("/x")
    assert ctx.git_branch is None
    assert ctx.active_change_id is None
    assert ctx.framework is None


def test_sensors_reexports_same_class() -> None:
    from super_harness.core.workspace import WorkspaceContext as Core
    from super_harness.sensors import WorkspaceContext as Sensors

    assert Core is Sensors  # sensors re-exports the core definition (single source of truth)
