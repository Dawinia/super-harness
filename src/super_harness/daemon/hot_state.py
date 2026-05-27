"""In-memory state.yaml cache with mtime-based hot reload.

Implements daemon-architecture §3.1 + UC-7 resilience. Every `get_change()`
call stats the file; on mtime advance we re-parse. Missing files and YAML
parse failures both resolve to `_data = None` so callers (daemon gate handler)
treat them as "state unavailable" and fall back to ALLOW per supervisor policy.

Thread-safety: a single `threading.Lock` covers stat + parse + read. A
100-change state.yaml parses in ~3-5ms; serializing concurrent gate queries
through that critical section is acceptable for v0.1 (gate calls are sparse,
sub-second cadence). See spec §3.1 for the v0.2 optimisation path.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import yaml

__all__ = ["HotState"]

_log = logging.getLogger(__name__)


class HotState:
    """Mtime-cached view of `.harness/state.yaml`.

    Construct once per daemon process; share across connection threads.
    """

    def __init__(self, state_path: Path) -> None:
        self.path: Path = state_path
        self._lock: threading.Lock = threading.Lock()
        self._data: dict[str, Any] | None = None
        self._mtime: float = 0.0

    def get_change(self, change_id: str) -> dict[str, Any] | None:
        """Return the per-change record from state.yaml, or None if unavailable."""
        with self._lock:
            self._maybe_reload()
            if not self._data:
                return None
            changes = self._data.get("changes") or {}
            if not isinstance(changes, dict):
                return None
            record = changes.get(change_id)
            if record is None or not isinstance(record, dict):
                return None
            return record

    def _maybe_reload(self) -> None:
        """Stat + reload if mtime advanced. Caller must hold `self._lock`."""
        try:
            current_mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._data = None
            self._mtime = 0.0
            return
        if current_mtime <= self._mtime:
            return
        # Mtime advanced: attempt re-parse. Even on YAMLError we record the new
        # mtime so a corrupt file does not cause re-parse on every gate query
        # (busy-retry on a busted file would be a DoS on the hot path).
        try:
            text = self.path.read_text(encoding="utf-8")
            parsed = yaml.safe_load(text)
        except (yaml.YAMLError, OSError) as exc:
            _log.error("HotState: failed to load state.yaml (%s); treating as unavailable", exc)
            self._data = None
            self._mtime = current_mtime
            return
        if parsed is None:
            self._data = {}
        elif isinstance(parsed, dict):
            self._data = parsed
        else:
            _log.error("HotState: state.yaml root is %s, expected mapping", type(parsed).__name__)
            self._data = None
        self._mtime = current_mtime
