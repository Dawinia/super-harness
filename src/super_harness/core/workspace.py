"""Workspace snapshot type shared across the harness (core-owned base type).

`WorkspaceContext` is the read-only snapshot passed to every `Sensor.check()`
call and consumed by adapters/CLI. It lives in `core` (the base layer) so neither
`sensors` nor `adapters` has to own it — `sensors` re-exports it for back-compat.
See decision d-core-is-base: core is the base layer; the upper layers depend on
it, not vice-versa.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceContext:
    """Read-only snapshot of the workspace passed to every Sensor.check() call.

    See sensor-gate-architecture spec §2.1.
    """

    workspace_root: Path
    git_branch: str | None = None
    active_change_id: str | None = None
    # Framework name of the active change (HG-01), used by the verification runner
    # to resolve `${SPEC_PATH}`/`${PLAN_PATH}` via the adapter's `spec_paths`.
    # None → those vars stay empty. Defaulted so every existing construction site
    # (and sensors that don't need it) keep working unchanged.
    framework: str | None = None
