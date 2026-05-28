"""PlainAdapter — fallback FrameworkAdapter for workspaces with no spec framework."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from super_harness.adapters import FrameworkAdapter
from super_harness.core.events import Event


class PlainAdapter(FrameworkAdapter):
    """Fallback adapter used when no spec-driven framework is detected.

    The dispatcher force-activates this adapter only when every non-fallback
    adapter's detect() returns False. PlainAdapter's own detect() always returns
    False — it is never auto-detected, only force-activated.
    """

    name = "plain"
    version = "0.1.0"
    is_fallback = True

    def detect(self, workspace: Path) -> bool:
        return False  # dispatcher's fallback logic activates this when nothing else matches

    def observe(self, workspace: Path) -> Iterator[Event]:
        return iter([])  # user drives lifecycle via CLI emit commands

    def get_state(self, change_id: str) -> dict[str, Any] | None:
        return None

    def verification_checks(self) -> list[dict[str, Any]]:
        return []

    def agents_md_subsection(self) -> str:
        return (
            "<!-- super-harness framework: plain -->\n"
            "- No framework: drive lifecycle via `super-harness change start <slug>` / "
            "`super-harness plan ready <slug>` / `super-harness done <slug>`.\n"
            "<!-- /super-harness framework: plain -->\n"
        )
