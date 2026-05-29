"""Unit tests for the daemon-hosted framework observer (Task 10.6 / OI-7).

Deterministic coverage (NO real Observer / FSEvents here — that lives in the
integration test):

- ``_observe_and_emit`` happy path: an openspec workspace with a proposal.md
  gets ``intent_declared`` into events.jsonl + state.yaml refreshed.
- ``_observe_and_emit`` resilience: a malformed change (a lone ``plan_ready``,
  i.e. tasks.md with no proposal.md) → the ``EmitPreconditionError`` is LOGGED
  and SWALLOWED (the helper does NOT raise) and the scan continues.
- ``FrameworkObserverManager.start()/stop()`` lifecycle on a real watch dir:
  the Observer thread starts then joins cleanly (no leaked thread).
- ``build_manager_failsafe`` fail-safe: a corrupt adapters.yaml at the load step
  is logged + swallowed (returns None) — the daemon would keep serving.
- ``start()`` no-op surfaces: no adapters / no watch paths / absent watch path.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from super_harness.adapters import FrameworkAdapter
from super_harness.adapters.framework.openspec import OpenSpecAdapter
from super_harness.core.events import Actor, Event
from super_harness.core.ulid import new_event_id
from super_harness.daemon.framework_observer import (
    FrameworkObserverManager,
    _observe_and_emit,
    build_manager_failsafe,
)

# --- helpers ---------------------------------------------------------------

def _events(ws: Path) -> list[dict[str, Any]]:
    path = ws / ".harness" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _make_openspec_workspace(ws: Path, *, with_proposal: bool = True) -> None:
    """Minimal-but-real openspec tree so detect()/observe() work."""
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    changes = ws / "openspec" / "changes"
    (changes / "foo").mkdir(parents=True)
    if with_proposal:
        (changes / "foo" / "proposal.md").write_text(
            "## Why\nBecause foo needs doing.\n", encoding="utf-8"
        )
    (ws / "openspec" / "specs").mkdir(parents=True)


class _LonePlanReadyAdapter(FrameworkAdapter):
    """Synthetic adapter that yields a malformed event stream: a lone plan_ready.

    A ``plan_ready`` with no preceding ``intent_declared`` violates the emit
    precondition (transition table rejects plan_ready as a first event), so
    ``EventWriter.emit`` raises ``EmitPreconditionError`` — exactly the malformed
    case the daemon must LOG + swallow rather than crash on.
    """

    name = "lone-plan-ready"
    version = "0.1.0"
    is_fallback = False

    def detect(self, workspace: Path) -> bool:
        return True

    def observe(self, workspace: Path) -> Iterator[Event]:
        yield Event(
            event_id=new_event_id(),
            type="plan_ready",
            change_id="malformed",
            timestamp="2026-05-29T00:00:00+00:00",
            actor=Actor(type="adapter", identifier="test"),
            framework="plain",
            payload={},
        )

    def get_state(self, change_id: str) -> dict[str, Any] | None:
        return None

    def verification_checks(self) -> list[dict[str, Any]]:
        return []

    def agents_md_subsection(self) -> str:
        return ""


# --- _observe_and_emit -----------------------------------------------------

def test_observe_and_emit_writes_intent_and_refreshes_state(tmp_path: Path) -> None:
    """A real proposal.md → intent_declared in events.jsonl + state.yaml refreshed."""
    _make_openspec_workspace(tmp_path)
    adapter = OpenSpecAdapter()

    _observe_and_emit(adapter, tmp_path)

    events = _events(tmp_path)
    intents = [
        e for e in events if e["change_id"] == "foo" and e["type"] == "intent_declared"
    ]
    assert len(intents) == 1, events
    assert intents[0]["framework"] == "openspec"

    # state.yaml was refreshed after the emit (refresh_state_after_emit ran).
    state_path = tmp_path / ".harness" / "state.yaml"
    assert state_path.exists()
    state = yaml.safe_load(state_path.read_text())
    assert "foo" in state["changes"]
    assert state["changes"]["foo"]["current_state"] == "INTENT_DECLARED"


def test_observe_and_emit_swallows_and_logs_emit_precondition_error(
    tmp_path: Path, caplog: Any
) -> None:
    """A malformed change (lone plan_ready) → emit error LOGGED + swallowed.

    The helper must NOT raise; nothing should be written for the rejected event.
    """
    (tmp_path / ".harness").mkdir(parents=True)
    adapter = _LonePlanReadyAdapter()

    with caplog.at_level(logging.WARNING, logger="super_harness.daemon"):
        # Must not raise — the EmitPreconditionError is contained.
        _observe_and_emit(adapter, tmp_path)

    # Nothing landed on disk (the lone plan_ready was rejected before write).
    assert _events(tmp_path) == []
    # The rejection was logged (so an AI debugger can correlate).
    assert any(
        getattr(rec, "log_reason", None) == "emit_precondition_failed"
        for rec in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_observe_and_emit_continues_after_one_bad_event(tmp_path: Path) -> None:
    """A scan that mixes a good change with a malformed one keeps emitting.

    Uses two synthetic adapters' worth of stream in one observe: a valid
    intent_declared FOLLOWED by a lone plan_ready for a DIFFERENT change. The
    valid event lands; the bad one is swallowed.
    """

    class _MixedAdapter(FrameworkAdapter):
        name = "mixed"
        version = "0.1.0"
        is_fallback = False

        def detect(self, workspace: Path) -> bool:
            return True

        def observe(self, workspace: Path) -> Iterator[Event]:
            yield Event(
                event_id=new_event_id(),
                type="intent_declared",
                change_id="good",
                timestamp="2026-05-29T00:00:00+00:00",
                actor=Actor(type="adapter", identifier="test"),
                framework="plain",
                payload={},
            )
            yield Event(
                event_id=new_event_id(),
                type="plan_ready",
                change_id="bad",  # no preceding intent_declared → precondition fail
                timestamp="2026-05-29T00:00:01+00:00",
                actor=Actor(type="adapter", identifier="test"),
                framework="plain",
                payload={},
            )

        def get_state(self, change_id: str) -> dict[str, Any] | None:
            return None

        def verification_checks(self) -> list[dict[str, Any]]:
            return []

        def agents_md_subsection(self) -> str:
            return ""

    (tmp_path / ".harness").mkdir(parents=True)
    _observe_and_emit(_MixedAdapter(), tmp_path)

    events = _events(tmp_path)
    assert [e["change_id"] for e in events] == ["good"], events


# --- FrameworkObserverManager start()/stop() lifecycle ---------------------

def test_manager_start_stop_no_leaked_thread(tmp_path: Path) -> None:
    """start() on a real watch dir spawns an Observer; stop() joins it cleanly."""
    _make_openspec_workspace(tmp_path)
    adapter = OpenSpecAdapter()
    manager = FrameworkObserverManager(tmp_path, [adapter])

    before = threading.active_count()
    manager.start()
    try:
        # At least one Observer thread is alive after start.
        assert manager._observers, "expected at least one Observer started"
        assert all(o.is_alive() for o in manager._observers)
        assert threading.active_count() > before
    finally:
        observers = list(manager._observers)
        manager.stop()

    # Every Observer joined (no leaked watcher thread).
    for o in observers:
        assert not o.is_alive(), "Observer thread leaked after stop()"
    # stop() cleared the manager's tracking.
    assert manager._observers == []


def test_manager_stop_is_idempotent(tmp_path: Path) -> None:
    """stop() may be called twice (shutdown() + serve_forever finally)."""
    _make_openspec_workspace(tmp_path)
    manager = FrameworkObserverManager(tmp_path, [OpenSpecAdapter()])
    manager.start()
    manager.stop()
    manager.stop()  # must not raise
    assert manager._observers == []


def test_manager_start_is_idempotent(tmp_path: Path) -> None:
    """A double start() does not double-spawn Observers."""
    _make_openspec_workspace(tmp_path)
    manager = FrameworkObserverManager(tmp_path, [OpenSpecAdapter()])
    manager.start()
    try:
        n = len(manager._observers)
        manager.start()  # no-op
        assert len(manager._observers) == n
    finally:
        manager.stop()


def test_manager_start_noop_when_no_adapters(tmp_path: Path) -> None:
    """No adapters → start() is a clean no-op (no Observers)."""
    manager = FrameworkObserverManager(tmp_path, [])
    manager.start()
    try:
        assert manager._observers == []
    finally:
        manager.stop()


def test_manager_start_noop_when_no_watch_paths(tmp_path: Path) -> None:
    """An adapter with an empty watch_paths() (plain fallback) → no Observers."""
    from super_harness.adapters.framework.plain import PlainAdapter

    manager = FrameworkObserverManager(tmp_path, [PlainAdapter()])
    manager.start()
    try:
        assert manager._observers == []
    finally:
        manager.stop()


def test_manager_start_noop_when_watch_path_absent(tmp_path: Path) -> None:
    """An openspec adapter whose openspec/changes/ does not exist → no Observers."""
    (tmp_path / ".harness").mkdir()
    # NB: no openspec/ tree → watch_paths() points at a nonexistent dir.
    manager = FrameworkObserverManager(tmp_path, [OpenSpecAdapter()])
    manager.start()
    try:
        assert manager._observers == []
    finally:
        manager.stop()


# --- build_manager_failsafe ------------------------------------------------

def test_build_manager_failsafe_on_corrupt_adapters_yaml(
    tmp_path: Path, caplog: Any
) -> None:
    """A corrupt adapters.yaml at the load step → logged + swallowed (returns None).

    Proves the daemon would keep serving with no watchers (Axiom 3 fail-safe)
    rather than letting load_adapters' error escape into main()'s crash path.
    """
    harness = tmp_path / ".harness"
    harness.mkdir()
    # Syntactically broken YAML → load_adapters raises yaml.YAMLError.
    (harness / "adapters.yaml").write_text("adapters: [unbalanced\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="super_harness.daemon"):
        manager = build_manager_failsafe(tmp_path)

    assert manager is None
    assert any(
        getattr(rec, "log_reason", None) == "adapter_setup_failed"
        for rec in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_build_manager_failsafe_happy_path_returns_manager(tmp_path: Path) -> None:
    """A valid openspec workspace → a real (not-yet-started) manager is returned."""
    _make_openspec_workspace(tmp_path)
    # adapters.yaml with the openspec framework enabled.
    (tmp_path / ".harness" / "adapters.yaml").write_text(
        yaml.safe_dump(
            {"adapters": [{"name": "openspec", "type": "framework", "builtin": True}]}
        ),
        encoding="utf-8",
    )

    manager = build_manager_failsafe(tmp_path)
    assert isinstance(manager, FrameworkObserverManager)
    # Not started yet — caller (serve_forever) starts it.
    assert manager._observers == []
