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


# --------------------------------------------------------------------------- #
# `implementation reopen` — the route back for a code-only fix
#
# Before it existed, `plan redeclare` into a full plan cycle was the only way out of
# a frozen state. This repository's own state.yaml carries two redeclares whose
# recorded reason is exactly that, and an adopter livelocked on the same route.
# --------------------------------------------------------------------------- #
_FROZEN = (
    "intent_declared", "plan_ready", "plan_approved", "implementation_started",
    "verification_passed", "implementation_complete",
)  # → AWAITING_CODE_REVIEW


def _reopen(ws: Path, slug: str, *, reason: str = "fold in two minor findings"):
    return CliRunner().invoke(main, [
        "--workspace", str(ws), "implementation", "reopen", slug, "--reason", reason])


def test_reopen_returns_ready_to_merge_to_editing(tmp_path: Path) -> None:
    _seed(tmp_path, "c", *_FROZEN, "code_review_passed")
    assert _state(tmp_path, "c") == "READY_TO_MERGE"
    r = _reopen(tmp_path, "c")
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "IMPLEMENTATION_IN_PROGRESS"
    last = _events(tmp_path)[-1]
    assert last["type"] == "implementation_invalidated"
    assert last["payload"]["reason"] == "fold in two minor findings"


def test_reopen_returns_awaiting_code_review_to_editing(tmp_path: Path) -> None:
    _seed(tmp_path, "c", *_FROZEN)
    assert _state(tmp_path, "c") == "AWAITING_CODE_REVIEW"
    r = _reopen(tmp_path, "c", reason="spotted a bug while the reviewer was out")
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "IMPLEMENTATION_IN_PROGRESS"


def test_reopen_states_the_cost_it_imposes(tmp_path: Path) -> None:
    """The verb reads cheap; the review it voids is the part that is not.

    The wording must hold from AWAITING_CODE_REVIEW too, where the round is still out
    and NOTHING has passed — claiming a passed review there would be false in half the
    cases the verb exists for (CR-001).
    """
    for prefix in ((*_FROZEN, "code_review_passed"), _FROZEN):
        ws = tmp_path / f"ws{len(prefix)}"
        ws.mkdir()
        _seed(ws, "c", *prefix)
        r = _reopen(ws, "c")
        assert r.exit_code == EXIT_OK, r.output
        assert "no longer stands" in r.output
        assert "review again before merge" in r.output
        assert "already passed" not in r.output


def test_reopen_refuses_plan_rejected(tmp_path: Path) -> None:
    """The exclusion that keeps plan rejection from becoming advisory.

    Reopening out of a rejection discards it: the change would return to editing,
    `done` and code review would carry it to READY_TO_MERGE, and the merge gate would
    be satisfied by the stale plan_approved from the earlier epoch. Nothing here can
    tell a code-level finding from a real plan rejection, so the state stays out.
    """
    _seed(tmp_path, "c", *_FROZEN, "code_review_passed", "plan_redeclared",
          "plan_ready", "plan_rejected")
    assert _state(tmp_path, "c") == "PLAN_REJECTED"
    before = len(_events(tmp_path))
    r = _reopen(tmp_path, "c")
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before          # nothing appended
    assert _state(tmp_path, "c") == "PLAN_REJECTED"
    assert "review skip" in r.output                 # names the disclosed exit instead


def test_reopen_refuses_before_anything_was_implemented(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    before = len(_events(tmp_path))
    r = _reopen(tmp_path, "c")
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before


def test_reopen_refuses_unknown_change(tmp_path: Path) -> None:
    _seed(tmp_path, "other", *_FROZEN)
    r = _reopen(tmp_path, "nope")
    assert r.exit_code == EXIT_VALIDATION, r.output


def test_reopen_requires_a_reason(tmp_path: Path) -> None:
    """It voids a review that passed; `review authorize` requires one for less."""
    _seed(tmp_path, "c", *_FROZEN, "code_review_passed")
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "implementation", "reopen", "c"])
    assert r.exit_code != EXIT_OK
    assert "--reason" in r.output


def test_reopen_no_config_exits_3(tmp_path: Path) -> None:
    r = _reopen(tmp_path, "c")
    assert r.exit_code == EXIT_NO_CONFIG


def test_reopen_then_done_closes_the_loop(tmp_path: Path) -> None:
    """The escape does not launder code review: the way back to READY_TO_MERGE still
    runs through implementation_complete and a fresh code_review_passed."""
    _seed(tmp_path, "c", *_FROZEN, "code_review_passed")
    assert _reopen(tmp_path, "c").exit_code == EXIT_OK
    _seed(tmp_path, "c", "verification_passed", "implementation_complete")
    assert _state(tmp_path, "c") == "AWAITING_CODE_REVIEW"
    _seed(tmp_path, "c", "code_review_passed")
    assert _state(tmp_path, "c") == "READY_TO_MERGE"


def test_reopen_json_envelope(tmp_path: Path) -> None:
    _seed(tmp_path, "c", *_FROZEN, "code_review_passed")
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "--json", "implementation", "reopen", "c",
        "--reason", "why"])
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.stdout)
    assert payload["data"]["event_emitted"] == "implementation_invalidated"
    assert payload["data"]["new_state"] == "IMPLEMENTATION_IN_PROGRESS"
    assert payload["data"]["reason"] == "why"


def test_reopen_records_a_real_identity_not_the_cli_placeholder(tmp_path: Path) -> None:
    """`report` renders this actor, and `cli` is what `PLACEHOLDER_IDENTITY` renders
    as "unattributed" elsewhere in the same codebase. The surface this verb mirrors,
    `review authorize`, resolves the identity for the reason AUTH-003 records: with
    two people on a repo, unnamed rows leave neither able to falsify the other's."""
    _seed(tmp_path, "c", *_FROZEN, "code_review_passed")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "implementation", "reopen", "c",
         "--reason", "fold in a finding"],
        env={"SUPER_HARNESS_ACTOR": "bob@example.test"},
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _events(tmp_path)[-1]["actor"]["identifier"] == "bob@example.test"
