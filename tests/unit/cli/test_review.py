"""Unit tests for `super-harness review skip` (HG-02, first increment).

`review skip <change> --reviewer plan-reviewer|code-reviewer` is the spec §499
escape hatch: it explicitly emits `plan_approved` / `code_review_passed` so a
human can advance the lifecycle past a reviewer (closes 2 of the 3 v0.1
lifecycle-gap emitters). Strict emit — an illegal transition is rejected.
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


def _emit(ws: Path, evt_type: str, slug: str) -> None:
    EventWriter(events_path(ws)).emit(
        Event(
            event_id=new_event_id(),
            type=evt_type,
            change_id=slug,
            timestamp="2026-06-02T00:00:00Z",
            actor=Actor(type="human", identifier="cli"),
            framework="plain",
            payload={},
        )
    )


def _seed(ws: Path, slug: str, *types: str) -> None:
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    for t in types:
        _emit(ws, t, slug)
    refresh_state_after_emit(ws)


def _state(ws: Path, slug: str) -> str | None:
    cs = derive_state(events_path(ws)).get(slug)
    return cs.current_state if cs else None


def _event_types(ws: Path) -> list[str]:
    return [json.loads(ln)["type"] for ln in events_path(ws).read_text().splitlines() if ln.strip()]


def test_skip_plan_reviewer_advances_to_plan_approved(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")  # → AWAITING_PLAN_REVIEW
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "skip", "c", "--reviewer", "plan-reviewer"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert "plan_approved" in _event_types(tmp_path)
    assert _state(tmp_path, "c") == "PLAN_APPROVED"


def test_approve_plan_reviewer_advances_to_plan_approved(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")  # → AWAITING_PLAN_REVIEW
    args = ["--workspace", str(tmp_path), "review", "approve", "c", "--reviewer", "plan-reviewer"]
    r = CliRunner().invoke(main, args)
    assert r.exit_code == EXIT_OK, r.output
    assert "plan_approved" in _event_types(tmp_path)
    assert _state(tmp_path, "c") == "PLAN_APPROVED"


def test_reject_plan_reviewer_advances_to_plan_rejected(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")  # → AWAITING_PLAN_REVIEW
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "reject", "c", "--reviewer", "plan-reviewer",
         "--reason", "scope too broad"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert "plan_rejected" in _event_types(tmp_path)
    assert _state(tmp_path, "c") == "PLAN_REJECTED"


def test_reject_code_reviewer_advances_to_code_review_rejected(tmp_path: Path) -> None:
    _seed(
        tmp_path, "c",
        "intent_declared", "plan_ready", "plan_approved", "implementation_started",
        "verification_passed", "implementation_complete",
    )  # → AWAITING_CODE_REVIEW
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "reject", "c", "--reviewer", "code-reviewer"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert "code_review_failed" in _event_types(tmp_path)
    assert _state(tmp_path, "c") == "CODE_REVIEW_REJECTED"


def test_reject_illegal_state_rejected(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")  # plan_rejected illegal from INTENT_DECLARED
    before = _event_types(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "reject", "c", "--reviewer", "plan-reviewer"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _event_types(tmp_path) == before


def test_skip_code_reviewer_advances_to_ready_to_merge(tmp_path: Path) -> None:
    _seed(
        tmp_path, "c",
        "intent_declared", "plan_ready", "plan_approved", "implementation_started",
        "verification_passed", "implementation_complete",
    )  # → AWAITING_CODE_REVIEW
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "skip", "c", "--reviewer", "code-reviewer"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert "code_review_passed" in _event_types(tmp_path)
    assert _state(tmp_path, "c") == "READY_TO_MERGE"


def test_skip_illegal_state_rejected_and_no_event_appended(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")  # INTENT_DECLARED — plan_approved illegal here
    before = _event_types(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "skip", "c", "--reviewer", "plan-reviewer"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _event_types(tmp_path) == before  # strict emit — nothing appended


def test_bad_reviewer_name_exit_2(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "skip", "c", "--reviewer", "nope"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output


def test_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "skip", "c", "--reviewer", "plan-reviewer"]
    )
    assert r.exit_code == EXIT_NO_CONFIG, r.output


def test_skip_json_envelope(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    args = ["--workspace", str(tmp_path), "--json", "review", "skip", "c"]
    args += ["--reviewer", "plan-reviewer"]
    r = CliRunner().invoke(main, args)
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.stdout)
    assert payload["status"] == "pass"
    assert payload["data"]["event_emitted"] == "plan_approved"
    assert payload["data"]["new_state"] == "PLAN_APPROVED"
    assert payload["data"]["reviewer"] == "plan-reviewer"
