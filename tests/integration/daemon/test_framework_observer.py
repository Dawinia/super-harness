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

``test_serve_forever_second_layer_failsafe_keeps_gate_serving`` (Task 10.6 review):
Regression-locks the SECOND-LAYER fail-safe guard in ``DaemonServer.serve_forever``
(lines ~179-188 in server.py). ``build_manager_failsafe`` has its own internal
``try/except`` that swallows corrupt adapters.yaml errors; the serve_forever guard is
the backstop that catches anything that escapes from there (e.g. a ``start()``
failure or a future refactor that weakens ``build_manager_failsafe``'s guard).
Non-vacuousness: temporarily removing the ``try/except`` block from ``serve_forever``
makes this test fail — a patched ``build_manager_failsafe`` that raises propagates
out and crashes ``serve_forever`` before ``bind()``, so ``start_server`` times out.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from super_harness.adapters.framework.openspec import OpenSpecAdapter
from super_harness.daemon.framework_observer import (
    FrameworkObserverManager,
    _ObserveHandler,
)
from super_harness.daemon.server import DaemonServer
from tests.integration.daemon.conftest import start_server


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


# --- serve_forever second-layer fail-safe (Task 10.6 review) ----------------


def _ping(socket_path: Path) -> dict[str, Any]:
    """Send a ping to the daemon and return the decoded response dict."""
    from super_harness.daemon.protocol import (
        GateQueryRequest,
        decode_response,
        encode_request,
    )

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    try:
        s.connect(str(socket_path))
        s.sendall(encode_request(GateQueryRequest(method="ping", params={}, id="failsafe-test")))
        line = s.makefile("rb").readline()
    finally:
        s.close()
    resp = decode_response(line)
    return {"result": resp.result, "error": resp.error}


def test_serve_forever_second_layer_failsafe_keeps_gate_serving(
    tmp_path: Path,
) -> None:
    """Axiom-3: a watcher-setup failure in serve_forever does NOT crash the daemon.

    The serve_forever try/except guard (lines ~179-188 in server.py) is the
    SECOND layer of protection: it catches anything that escapes
    ``build_manager_failsafe``'s own internal guard (e.g. a future refactor that
    weakens it, or a ``start()`` failure). We trigger this second layer by patching
    ``build_manager_failsafe`` in the ``server`` module to raise directly —
    simulating a watcher-setup failure that bypasses the inner guard.

    Non-vacuousness contract (verified manually during review):
    - Remove the ``try/except`` block from ``serve_forever`` (lines ~179-188 of
      server.py, i.e. the ``try:`` through ``self._framework_observers = None``).
    - With the guard removed, the patched ``build_manager_failsafe`` raises →
      exception propagates out of ``serve_forever`` before ``bind()``, so
      ``start_server`` times out with RuntimeError and this test FAILS.
    - Restore the guard → test passes again.

    The corrupt ``adapters.yaml`` documents the intent (a real file would be swallowed
    by ``build_manager_failsafe``'s own inner try/except, so we patch at the
    server-module import level to reach serve_forever's second layer specifically).

    Teardown: server.shutdown() + bounded thread join in finally — cannot hang suite.
    """
    harness = tmp_path / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    # Corrupt adapters.yaml — documents intent; real corruption is swallowed by
    # build_manager_failsafe's own guard, hence the patch below targets the second
    # layer in serve_forever instead.
    (harness / "adapters.yaml").write_text("adapters: [unclosed\n", encoding="utf-8")
    state_path = harness / "state.yaml"
    events_path = harness / "events.jsonl"
    socket_path = harness / "daemon.sock"

    server = DaemonServer(
        workspace_root=tmp_path,
        socket_path=socket_path,
        state_path=state_path,
        events_path=events_path,
    )

    server_thread: threading.Thread | None = None
    # Patch build_manager_failsafe in the server module (where it is CALLED) so the
    # RuntimeError hits serve_forever's second-layer try/except, not the inner guard.
    with patch(
        "super_harness.daemon.server.build_manager_failsafe",
        side_effect=RuntimeError("simulated watcher-setup failure"),
    ):
        try:
            server_thread = start_server(server)

            # Daemon is serving: send a live ping and assert a valid response.
            ping_resp = _ping(server.socket_path)
            assert ping_resp["error"] is None, (
                f"ping returned error: {ping_resp['error']}"
            )
            assert ping_resp["result"] is not None

            # Second-layer guard set _framework_observers to None on failure.
            assert server._framework_observers is None, (
                "_framework_observers must be None when watcher setup failed"
            )
        finally:
            server.shutdown()
            if server_thread is not None:
                server_thread.join(timeout=3.0)
