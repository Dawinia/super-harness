"""OpenSpecAdapter — FrameworkAdapter for workspaces using the OpenSpec framework."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

from super_harness.adapters import FrameworkAdapter
from super_harness.core.events import Event


class OpenSpecAdapter(FrameworkAdapter):
    """FrameworkAdapter for projects using OpenSpec.

    Detection heuristic: openspec@1.3.1 `init` creates openspec/changes/ and
    openspec/specs/ but no config file (project.yaml never existed;
    config.yaml is interactive-config-only). Both dirs must be present.
    """

    name: ClassVar[str] = "openspec"
    version: ClassVar[str] = "0.1.0"
    is_fallback: ClassVar[bool] = False

    def detect(self, workspace: Path) -> bool:
        # verified openspec@1.3.1: `init` creates changes/ + specs/ but NO config
        # file (project.yaml never existed; config.yaml is interactive-config-only).
        return (
            (workspace / "openspec" / "changes").is_dir()
            and (workspace / "openspec" / "specs").is_dir()
        )

    def observe(self, workspace: Path) -> Iterator[Event]:  # Task 10.2
        return iter([])

    def get_state(self, change_id: str) -> dict[str, Any] | None:  # Task 10.2
        return None

    def verification_checks(self) -> list[dict[str, Any]]:  # Task 10.3
        return []

    def agents_md_subsection(self) -> str:  # Task 10.3
        return ""
