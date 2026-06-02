"""Unit tests for `super-harness implementation start` (HG-02.3).

`implementation start <slug> [--first-commit <sha>]` (cli-command-surface §429)
manually emits `implementation_started`, advancing PLAN_APPROVED →
IMPLEMENTATION_IN_PROGRESS. It is the third lifecycle-gap emitter; with
`review skip` it lets a cold-start change run the whole lifecycle via CLI.
Strict emit — an illegal transition is rejected.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION


def _seed(ws: Path, slug: str, *types: str) -> None:
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    w = EventWriter(events_path(ws))
    for t in types:
        w.emit(
            Event(
                event_id=new_event_id(),
                type=t,
                change_id=slug,
                timestamp="2026-06-02T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="plain",
                payload={},
            )
        )
    refresh_state_after_emit(ws)


def _state(ws: Path, slug: str) -> str | None:
    cs = derive_state(events_path(ws)).get(slug)
    return cs.current_state if cs else None


def _events(ws: Path) -> list[dict]:
    return [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()]


def _to_plan_approved(ws: Path, slug: str) -> None:
    _seed(ws, slug, "intent_declared", "plan_ready", "plan_approved")


def test_start_advances_plan_approved_to_in_progress(tmp_path: Path) -> None:
    _to_plan_approved(tmp_path, "c")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "implementation", "start", "c"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "IMPLEMENTATION_IN_PROGRESS"
    assert _events(tmp_path)[-1]["type"] == "implementation_started"


def test_start_records_first_commit_in_payload(tmp_path: Path) -> None:
    _to_plan_approved(tmp_path, "c")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "implementation", "start", "c", "--first-commit", "abc123"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _events(tmp_path)[-1]["payload"].get("first_commit") == "abc123"


def test_start_illegal_state_rejected_no_event(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")  # INTENT_DECLARED — illegal
    before = len(_events(tmp_path))
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "implementation", "start", "c"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before


def test_start_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "implementation", "start", "c"]
    )
    assert r.exit_code == EXIT_NO_CONFIG, r.output


def test_start_json_envelope(tmp_path: Path) -> None:
    _to_plan_approved(tmp_path, "c")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "implementation", "start", "c"]
    )
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.stdout)
    assert payload["status"] == "pass"
    assert payload["data"]["event_emitted"] == "implementation_started"
    assert payload["data"]["new_state"] == "IMPLEMENTATION_IN_PROGRESS"
