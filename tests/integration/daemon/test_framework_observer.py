"""Integration test for the daemon-hosted framework watcher (Task 10.6 / OI-7).

Exercises a REAL watchdog ``Observer`` lifecycle (start → create artifact → poll
events.jsonl with a BOUNDED wait → stop → assert joined). FSEvents delivery is
inherently timing-sensitive, so the poll uses a generous bounded timeout to
avoid CI flakiness; a SECOND deterministic test invokes the handler callback
directly (no reliance on OS event delivery) so we still cover the scan-and-emit
path even if the platform's real-event delivery is sluggish.

Teardown safety: every test that starts a real Observer stops it in a
``finally`` so a failed assertion cannot leak a watcher thread and hang the
suite. All joins are bounded inside ``manager.stop()``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from super_harness.adapters.framework.openspec import OpenSpecAdapter
from super_harness.daemon.framework_observer import (
    FrameworkObserverManager,
    _ObserveHandler,
)


def _events(ws: Path) -> list[dict[str, Any]]:
    path = ws / ".harness" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _make_openspec_workspace(ws: Path) -> None:
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    (ws / "openspec" / "changes").mkdir(parents=True)
    (ws / "openspec" / "specs").mkdir(parents=True)


def _wait_for_intent(ws: Path, change_id: str, *, timeout_s: float = 5.0) -> bool:
    """Poll events.jsonl up to ``timeout_s`` for an intent_declared for change_id."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if any(
            e["change_id"] == change_id and e["type"] == "intent_declared"
            for e in _events(ws)
        ):
            return True
        time.sleep(0.05)
    return False


def test_real_observer_emits_intent_on_proposal_creation(tmp_path: Path) -> None:
    """REAL Observer: create openspec/changes/foo/proposal.md → intent_declared.

    Timing-sensitive (relies on real FSEvents delivery) → BOUNDED 5s poll.
    """
    _make_openspec_workspace(tmp_path)
    manager = FrameworkObserverManager(tmp_path, [OpenSpecAdapter()])
    manager.start()
    observers = list(manager._observers)
    assert observers, "expected a real Observer to be running"
    try:
        change_dir = tmp_path / "openspec" / "changes" / "foo"
        change_dir.mkdir()
        (change_dir / "proposal.md").write_text(
            "## Why\nReal-watcher integration.\n", encoding="utf-8"
        )
        assert _wait_for_intent(tmp_path, "foo"), (
            "watcher did not emit intent_declared within the bounded poll; "
            f"events={_events(tmp_path)}"
        )
    finally:
        manager.stop()

    # The Observer joined (no leaked watcher thread).
    for o in observers:
        assert not o.is_alive(), "Observer thread leaked after stop()"


def test_handler_callback_simulated_event_is_deterministic(tmp_path: Path) -> None:
    """Deterministic mirror: invoke the handler callback directly (no FSEvents).

    Proves the scan-and-emit path without depending on OS event delivery — the
    callback runs ``_observe_and_emit`` synchronously, so the assert is immediate.
    """
    _make_openspec_workspace(tmp_path)
    change_dir = tmp_path / "openspec" / "changes" / "foo"
    change_dir.mkdir()
    (change_dir / "proposal.md").write_text(
        "## Why\nDeterministic callback.\n", encoding="utf-8"
    )

    handler = _ObserveHandler(OpenSpecAdapter(), tmp_path)
    # Simulate a filesystem event delivery (callback is what the Observer calls).
    handler.on_any_event(None)  # type: ignore[arg-type]  # event obj is unused

    events = _events(tmp_path)
    intents = [
        e for e in events if e["change_id"] == "foo" and e["type"] == "intent_declared"
    ]
    assert len(intents) == 1, events
