"""Unit tests for AnchorSentinelPresence sensor (Phase 11 Task 11.1).

6 cases per spec:
1. all declared anchors have sentinels → pass (+ emit_events == [])
2. commit activity trigger + missing sentinel → warning
3. implementation_complete event trigger + missing + tier Normal → fail
4. implementation_complete event trigger + missing + tier Micro → warning
5. no declared anchors (empty affected_anchors) → pass
6. no change_id resolvable (trigger has none + context.active_change_id is None) → pass
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.sensors import Activity, WorkspaceContext
from super_harness.sensors.anchor_sentinel_presence import AnchorSentinelPresence

# --------------------------------------------------------------------------- #
# Helpers (mirror test_verification_runner.py seeding pattern)
# --------------------------------------------------------------------------- #


def _evt(change_id: str, evt_type: str, payload: dict[str, Any] | None = None) -> Event:
    return Event(
        event_id=new_event_id(),
        type=evt_type,
        change_id=change_id,
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework="plain",
        payload=payload or {},
    )


def _seed_events(root: Path, change_id: str, items: list[tuple[str, dict[str, Any]]]) -> None:
    """Append events (bypassing emit-time validation) to root/.harness/events.jsonl."""
    w = EventWriter(events_path(root))
    for evt_type, payload in items:
        w.emit(_evt(change_id, evt_type, payload), skip_validation=True)


def _plan_items(
    *,
    anchors: list[str] | None = None,
    tier: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """A minimal intent_declared → plan_ready stream carrying anchors/tier."""
    plan_payload: dict[str, Any] = {}
    if anchors is not None:
        plan_payload["affected_anchors"] = anchors
    if tier is not None:
        plan_payload["tier_hint"] = tier
    return [
        ("intent_declared", {"description": "x"}),
        ("plan_ready", plan_payload),
    ]


def _harness_root(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _impl_complete_trigger(change_id: str) -> Event:
    """An implementation_complete Event carrying change_id."""
    return _evt(change_id, "implementation_complete")


def _commit_trigger(change_id: str) -> Activity:
    """A commit Activity carrying change_id."""
    return Activity(type="commit", change_id=change_id)


# --------------------------------------------------------------------------- #
# Case 1: all declared anchors have sentinels → pass + emit_events == []
# --------------------------------------------------------------------------- #


def test_pass_when_all_anchors_planted(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(root, "c1", _plan_items(anchors=["cap-foo"], tier="Normal"))
    (root / "src").mkdir()
    (root / "src" / "foo.py").write_text("# @capability:cap-foo\n")
    r = AnchorSentinelPresence().check(
        _commit_trigger("c1"),
        WorkspaceContext(workspace_root=root, active_change_id="c1"),
    )
    assert r.status == "pass"
    assert r.emit_events == []  # audit-only: blocks nothing


# --------------------------------------------------------------------------- #
# Case 2: commit activity trigger + missing sentinel → warning
# --------------------------------------------------------------------------- #


def test_commit_trigger_missing_anchor_is_warning(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(root, "c2", _plan_items(anchors=["cap-foo"], tier="Normal"))
    # No source file containing @capability:cap-foo
    r = AnchorSentinelPresence().check(
        _commit_trigger("c2"),
        WorkspaceContext(workspace_root=root, active_change_id="c2"),
    )
    assert r.status == "warning"
    assert r.emit_events == []
    assert r.details is not None
    assert "cap-foo" in r.details["missing"]


# --------------------------------------------------------------------------- #
# Case 3: implementation_complete + missing + Normal tier → fail
# --------------------------------------------------------------------------- #


def test_impl_complete_normal_tier_missing_is_fail(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(root, "c3", _plan_items(anchors=["cap-bar"], tier="Normal"))
    # Sentinel absent
    r = AnchorSentinelPresence().check(
        _impl_complete_trigger("c3"),
        WorkspaceContext(workspace_root=root, active_change_id="c3"),
    )
    assert r.status == "fail"
    assert r.emit_events == []
    assert r.details is not None
    assert "cap-bar" in r.details["missing"]
    assert r.details["tier"] == "Normal"


# --------------------------------------------------------------------------- #
# Case 4: implementation_complete + missing + Micro tier → warning
# --------------------------------------------------------------------------- #


def test_impl_complete_micro_tier_missing_is_warning(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(root, "c4", _plan_items(anchors=["cap-baz"], tier="Micro"))
    # Sentinel absent
    r = AnchorSentinelPresence().check(
        _impl_complete_trigger("c4"),
        WorkspaceContext(workspace_root=root, active_change_id="c4"),
    )
    assert r.status == "warning"
    assert r.emit_events == []
    assert r.details is not None
    assert r.details["tier"] == "Micro"


# --------------------------------------------------------------------------- #
# Case 5: no declared anchors (empty affected_anchors) → pass
# --------------------------------------------------------------------------- #


def test_pass_when_no_declared_anchors(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    # plan_ready with empty anchors list (or absent)
    _seed_events(root, "c5", _plan_items(anchors=[], tier="Normal"))
    r = AnchorSentinelPresence().check(
        _commit_trigger("c5"),
        WorkspaceContext(workspace_root=root, active_change_id="c5"),
    )
    assert r.status == "pass"
    assert r.emit_events == []


# --------------------------------------------------------------------------- #
# Case 6: no change_id resolvable → pass
# --------------------------------------------------------------------------- #


def test_pass_when_no_change_id(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    # Activity without change_id + context without active_change_id
    r = AnchorSentinelPresence().check(
        Activity(type="commit", change_id=None),
        WorkspaceContext(workspace_root=root, active_change_id=None),
    )
    assert r.status == "pass"
    assert r.emit_events == []
