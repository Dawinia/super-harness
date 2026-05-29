"""Canonical UTC timestamp helper — the single source of the ``Z``-suffixed form.

lifecycle-event-model §2 stamps events as ISO 8601 UTC with a trailing ``Z``
(not ``+00:00``). Several emit sites need that EXACT format — the CLI
``change`` / ``done`` commands, the sensor dispatcher + verification runner, and
the ``state.yaml`` reducer stamp. Keeping the one true formatter here stops those
copies from drifting (they were five identical inline expressions before).
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Current UTC time as ISO 8601 with a trailing ``Z`` (lifecycle §2 format)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
