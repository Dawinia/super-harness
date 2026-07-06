"""Unit tests for the `super-harness plan` group (HG-13): `ready` + `redeclare`.

`plan ready <slug> [--scope <files-yaml>] [--tier-hint <t>]`
(cli-command-surface §418) manually emits `plan_ready`, advancing
INTENT_DECLARED / PLAN_REJECTED → AWAITING_PLAN_REVIEW. It is the plain-mode
emitter that lets a cold-start change leave the very first lifecycle stage via
CLI (no framework adapter, no `skip_validation` seeding). Strict emit — an
illegal transition is rejected and nothing is appended.

The payload carries the lifecycle-event-model §3.2 fields the reducer already
consumes: `scope` ({files: [...]}), `tier_hint`
(Micro/Normal/Large → cs.tier).

`plan redeclare <slug> [--reason <text>]` emits `plan_redeclared`, rewinding a
change from any active (non-terminal) state back to INTENT_DECLARED so its scope
can be re-declared via a subsequent `plan ready --scope` (which re-routes through
the plan review). Strict emit — terminal states / unknown slug are rejected.
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


def test_ready_advances_intent_declared_to_awaiting_review(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "ready", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "AWAITING_PLAN_REVIEW"
    assert _events(tmp_path)[-1]["type"] == "plan_ready"


def test_ready_from_plan_rejected_resubmits(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_rejected")
    assert _state(tmp_path, "c") == "PLAN_REJECTED"
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "ready", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "AWAITING_PLAN_REVIEW"


def test_ready_records_scope_files_in_payload(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "ready", "c", "--scope", "[src/a.py, src/b.py]"],
    )
    assert r.exit_code == EXIT_OK, r.output
    scope = _events(tmp_path)[-1]["payload"].get("scope")
    assert scope == {"files": ["src/a.py", "src/b.py"]}


def test_ready_scope_at_file_reads_from_disk(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    scope_file = tmp_path / "scope.yaml"
    scope_file.write_text("- src/x.py\n- src/y.py\n")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "ready", "c", "--scope", f"@{scope_file}"],
    )
    assert r.exit_code == EXIT_OK, r.output
    scope = _events(tmp_path)[-1]["payload"].get("scope")
    assert scope == {"files": ["src/x.py", "src/y.py"]}


def test_ready_records_tier_hint(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "ready", "c", "--tier-hint", "Normal"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _events(tmp_path)[-1]["payload"].get("tier_hint") == "Normal"
    # tier_hint is wired through the reducer onto cs.tier.
    assert derive_state(events_path(tmp_path)).get("c").tier == "Normal"


def test_ready_illegal_state_rejected_no_event(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")  # PLAN_APPROVED
    before = len(_events(tmp_path))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "ready", "c"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before


def test_ready_malformed_scope_yaml_rejected(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    before = len(_events(tmp_path))
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "ready", "c", "--scope", "[unterminated"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before


def test_ready_scope_at_missing_file_rejected(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    before = len(_events(tmp_path))
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "ready", "c", "--scope", "@/nope/missing.yaml"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before


def test_ready_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "ready", "c"])
    assert r.exit_code == EXIT_NO_CONFIG, r.output


def test_ready_json_envelope(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "plan", "ready", "c"]
    )
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.stdout)
    assert payload["status"] == "pass"
    assert payload["data"]["event_emitted"] == "plan_ready"
    assert payload["data"]["new_state"] == "AWAITING_PLAN_REVIEW"


# --- plan redeclare (rewind any active state → INTENT_DECLARED) ---


def test_redeclare_from_ready_to_merge_rewinds_to_intent(tmp_path: Path) -> None:
    # verification_passed precedes implementation_complete: _HARD_PREREQ_EVENTS
    # (emit_validation.py) enforces it, and it is informational (stays in
    # IMPLEMENTATION_IN_PROGRESS) so the downstream transitions are unaffected.
    _seed(
        tmp_path, "c",
        "intent_declared", "plan_ready", "plan_approved",
        "implementation_started", "verification_passed", "implementation_complete",
        "code_review_passed",
    )
    assert _state(tmp_path, "c") == "READY_TO_MERGE"
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "INTENT_DECLARED"
    assert _events(tmp_path)[-1]["type"] == "plan_redeclared"


def test_redeclare_from_plan_rejected_rewinds_to_intent(tmp_path: Path) -> None:
    # A rejection state is still "any active" — cements the universal-branch
    # wording (transitions.py: intent_redeclared/plan_redeclared → INTENT_DECLARED
    # from any non-terminal state).
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_rejected")
    assert _state(tmp_path, "c") == "PLAN_REJECTED"
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "INTENT_DECLARED"
    assert _events(tmp_path)[-1]["type"] == "plan_redeclared"


def test_redeclare_from_intent_declared_is_legal_noop(tmp_path: Path) -> None:
    # Redeclaring an already-INTENT_DECLARED change is legal (stays INTENT_DECLARED);
    # the reducer still records the event in redeclaration_history — intended audit
    # behavior, not a bug.
    _seed(tmp_path, "c", "intent_declared")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert _state(tmp_path, "c") == "INTENT_DECLARED"
    assert _events(tmp_path)[-1]["type"] == "plan_redeclared"


def test_redeclare_records_reason_in_history(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "plan", "redeclare", "c", "--reason", "expand scope"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _events(tmp_path)[-1]["payload"].get("reason") == "expand scope"
    history = derive_state(events_path(tmp_path)).get("c").redeclaration_history
    assert history[-1]["reason"] == "expand scope"
    assert history[-1]["type"] == "plan_redeclared"


def test_redeclare_without_reason_omits_reason_from_payload(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_OK, r.output
    assert "reason" not in _events(tmp_path)[-1]["payload"]
    # Reducer records a None reason for an omitted-reason redeclaration.
    history = derive_state(events_path(tmp_path)).get("c").redeclaration_history
    assert history[-1]["reason"] is None


def test_redeclare_from_terminal_archived_rejected_no_event(tmp_path: Path) -> None:
    _seed(
        tmp_path, "c",
        "intent_declared", "plan_ready", "plan_approved",
        "implementation_started", "verification_passed", "implementation_complete",
        "code_review_passed", "merged",
    )
    assert _state(tmp_path, "c") == "ARCHIVED"
    before = len(_events(tmp_path))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before
    assert _events(tmp_path)[-1]["type"] != "plan_redeclared"


def test_redeclare_from_terminal_abandoned_rejected_no_event(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "intent_abandoned")
    assert _state(tmp_path, "c") == "ABANDONED"
    before = len(_events(tmp_path))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert len(_events(tmp_path)) == before
    assert _events(tmp_path)[-1]["type"] != "plan_redeclared"


def test_redeclare_unknown_slug_rejected_no_event(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "ghost"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    # No plan_redeclared may be appended for an unknown slug (strict emit).
    assert not events_path(tmp_path).exists() or not any(
        e["type"] == "plan_redeclared" for e in _events(tmp_path)
    )


def test_redeclare_rejects_scope_flag(tmp_path: Path) -> None:
    # The no-`--scope` boundary is central: scope is re-declared on the following
    # `plan ready --scope`, never on redeclare. Click rejects the unknown option
    # (exit 2) and no event is appended.
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")
    before = len(_events(tmp_path))
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "plan", "redeclare", "c", "--scope", "[a.py]"]
    )
    assert r.exit_code != EXIT_OK
    assert len(_events(tmp_path)) == before


def test_redeclare_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "plan", "redeclare", "c"])
    assert r.exit_code == EXIT_NO_CONFIG, r.output


def test_redeclare_json_envelope(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "plan", "redeclare", "c"]
    )
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.stdout)
    assert payload["status"] == "pass"
    assert payload["data"]["event_emitted"] == "plan_redeclared"
    assert payload["data"]["new_state"] == "INTENT_DECLARED"
