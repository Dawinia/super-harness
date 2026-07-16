"""plan_artifacts accumulation on plan_ready (HG-PLAN-AUTHORING).

The gate's PLAN_REJECTED plan-artifact carve-out consults
`ChangeState.plan_artifacts`. The reducer must (a) record it from the plan_ready
payload, (b) ALWAYS replace it on each plan_ready (an empty re-submit revokes prior
authorization), (c) accept it only as a list of str (a mapping / non-str must not
smuggle a path in), and (d) clear it on plan_redeclared.
"""
from __future__ import annotations

from pathlib import Path

from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter


def _emit(root: Path, **kw: object) -> None:
    ev = Event(
        event_id=new_event_id(),
        actor=Actor(type="human", identifier="t"),
        framework="plain",
        timestamp="2026-07-16T00:00:00Z",
        **kw,  # type: ignore[arg-type]
    )
    EventWriter(events_path(root)).emit(ev, skip_validation=True)


def _seed(root: Path, slug: str = "c") -> None:
    (root / ".harness").mkdir(exist_ok=True)
    _emit(root, type="intent_declared", change_id=slug, payload={"description": "d"})


def test_plan_ready_records(tmp_path: Path) -> None:
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": ["docs/plans/c.md"]})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == ["docs/plans/c.md"]


def test_default_empty(tmp_path: Path) -> None:
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c", payload={"scope": {"files": []}})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []


def test_empty_resubmit_revokes(tmp_path: Path) -> None:
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": ["docs/plans/c.md"]})
    _emit(tmp_path, type="plan_rejected", change_id="c", payload={})
    _emit(tmp_path, type="plan_ready", change_id="c", payload={"scope": {"files": []}})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []


def test_malformed_payload_becomes_empty(tmp_path: Path) -> None:
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": {"src/evil.py": True}})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []


def test_non_str_items_filtered(tmp_path: Path) -> None:
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": ["docs/plans/c.md", 123, None]})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == ["docs/plans/c.md"]


def test_redeclare_clears(tmp_path: Path) -> None:
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": ["docs/plans/c.md"]})
    _emit(tmp_path, type="plan_redeclared", change_id="c", payload={"reason": "x"})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []
