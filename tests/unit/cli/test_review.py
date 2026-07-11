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


def _events(ws: Path) -> list[dict]:
    return [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()]


def _set_independent_policy(ws: Path, *, reviewer: str, min_independent: int) -> None:
    (ws / ".harness" / "policy.yaml").write_text(
        "reviewers:\n"
        "  sources: [subagent, external]\n"
        f"  {reviewer}:\n"
        f"    min_independent: {min_independent}\n"
    )


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


def test_nonparticipant_source_cannot_satisfy_review_role(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    (tmp_path / ".harness" / "policy.yaml").write_text(
        "reviewers:\n"
        "  sources: [subagent, external, human]\n"
        "  plan-reviewer:\n"
        "    min_independent: 2\n"
        "    participants: [subagent, external]\n"
    )
    first = CliRunner().invoke(
        main,
        [
            "--workspace", str(tmp_path), "review", "approve", "c",
            "--reviewer", "plan-reviewer", "--source", "subagent",
        ],
    )

    second = CliRunner().invoke(
        main,
        [
            "--workspace", str(tmp_path), "review", "approve", "c",
            "--reviewer", "plan-reviewer", "--source", "human",
        ],
    )

    assert first.exit_code == EXIT_OK, first.output
    assert second.exit_code == EXIT_VALIDATION, second.output
    assert "participant" in second.output.lower()
    assert "plan_approved" not in _event_types(tmp_path)


def test_approve_plan_reviewer_rejects_fail_verdict(tmp_path: Path) -> None:
    # F1 (review 2026-07-02): the OPTIONAL plan-reviewer verdict, when provided,
    # must not carry a failing checklist item on an approve.
    _seed(tmp_path, "c", "intent_declared", "plan_ready")  # → AWAITING_PLAN_REVIEW
    v = tmp_path / "plan-verdict.yaml"
    v.write_text(
        "bundle_digest: whatever\nchecklist:\n"
        "  - item: scope-sanity\n    status: fail\n"
        "findings:\n  - id: f1\n    severity: minor\n    file: docs/plan.md\n"
        "    summary: scope hole\n")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c", "--reviewer", "plan-reviewer",
         "--verdict-file", str(v)],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "scope-sanity" in r.output
    assert "review reject" in r.output
    assert "plan_approved" not in _event_types(tmp_path)


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


# --- HG-12 cut 1: reviewer identity + structured skip marker ----------------- #
_PREFIX = (
    "intent_declared", "plan_ready", "plan_approved", "implementation_started",
    "verification_passed", "implementation_complete",
)  # → AWAITING_CODE_REVIEW


def _last(ws: Path, *, type: str, change_id: str) -> dict:
    evs = [json.loads(ln) for ln in events_path(ws).read_text().splitlines() if ln.strip()]
    return [e for e in evs if e["type"] == type and e["change_id"] == change_id][-1]


def test_review_approve_records_as_identity(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")  # → AWAITING_PLAN_REVIEW
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "review", "approve", "c",
        "--reviewer", "plan-reviewer", "--as", "bob@example.com"])
    assert r.exit_code == EXIT_OK, r.output
    ev = _last(tmp_path, type="plan_approved", change_id="c")
    assert ev["actor"]["identifier"] == "bob@example.com"
    assert "skipped" not in ev["payload"]  # approve must NOT set the skip marker


def test_review_reject_records_as_identity(tmp_path: Path) -> None:
    _seed(tmp_path, "c", *_PREFIX)
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "review", "reject", "c",
        "--reviewer", "code-reviewer", "--as", "carol@example.com"])
    assert r.exit_code == EXIT_OK, r.output
    ev = _last(tmp_path, type="code_review_failed", change_id="c")
    assert ev["actor"]["identifier"] == "carol@example.com"


def test_review_skip_sets_structured_marker(tmp_path: Path) -> None:
    _seed(tmp_path, "c", *_PREFIX)
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "review", "skip", "c",
        "--reviewer", "code-reviewer", "--reason", "on vacation"])
    assert r.exit_code == EXIT_OK, r.output
    ev = _last(tmp_path, type="code_review_passed", change_id="c")
    assert ev["payload"]["skipped"] is True          # marker, not the reason
    assert ev["payload"]["reason"] == "on vacation"  # reason stays free text


def test_review_approve_default_identity_via_resolver(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")  # → AWAITING_PLAN_REVIEW
    monkeypatch.setattr(
        "super_harness.cli.review.resolve_identity", lambda ws, override=None: "git@x"
    )
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "review", "approve", "c", "--reviewer", "plan-reviewer"])
    assert r.exit_code == EXIT_OK, r.output
    ev = _last(tmp_path, type="plan_approved", change_id="c")
    assert ev["actor"]["identifier"] == "git@x"


def test_skip_override_requires_reason(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved",
          "implementation_started", "verification_passed", "implementation_complete")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "skip", "c",
                                  "--reviewer", "code-reviewer", "--override"])
    assert r.exit_code == 2, r.output
    assert "reason" in r.output.lower()
    assert _event_types(tmp_path)[-1] == "implementation_complete"  # nothing emitted


def test_skip_override_stamps_payload(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved",
          "implementation_started", "verification_passed", "implementation_complete")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "skip", "c",
                                  "--reviewer", "code-reviewer", "--override",
                                  "--reason", "deadlocked CI"])
    assert r.exit_code == 0, r.output
    last = json.loads(events_path(tmp_path).read_text().splitlines()[-1])
    assert last["payload"]["skipped"] is True
    assert last["payload"]["override"] is True
    assert last["payload"]["reason"] == "deadlocked CI"


def test_bare_skip_defaults_reason_no_override(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved",
          "implementation_started", "verification_passed", "implementation_complete")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "skip", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == 0, r.output
    last = json.loads(events_path(tmp_path).read_text().splitlines()[-1])
    assert last["payload"]["reason"] == "manual_skip"
    assert "override" not in last["payload"]


# --- Multi-independent reviewer-source gate --------------------------------- #


def test_independent_plan_first_source_records_partial_only(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    r = CliRunner().invoke(
        main,
        [
            "--workspace", str(tmp_path), "review", "approve", "c",
            "--reviewer", "plan-reviewer", "--source", "subagent",
        ],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _event_types(tmp_path)[-1] == "review_verdict_recorded"
    assert _state(tmp_path, "c") == "AWAITING_PLAN_REVIEW"
    last = _events(tmp_path)[-1]
    assert last["payload"]["source"] == "subagent"
    assert last["payload"]["outcome"] == "approved"


def test_independent_plan_second_source_emits_milestone(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    first = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "plan-reviewer", "--source", "subagent"],
    )
    assert first.exit_code == EXIT_OK, first.output
    second = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "plan-reviewer", "--source", "external"],
    )
    assert second.exit_code == EXIT_OK, second.output
    assert _event_types(tmp_path)[-2:] == ["review_verdict_recorded", "plan_approved"]
    assert _state(tmp_path, "c") == "PLAN_APPROVED"
    milestone = _events(tmp_path)[-1]
    assert milestone["payload"]["independent_sources"] == ["external", "subagent"]
    assert milestone["payload"]["min_independent"] == 2


def test_independent_duplicate_source_does_not_satisfy_threshold(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    for _ in range(2):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "review", "approve", "c",
             "--reviewer", "plan-reviewer", "--source", "subagent"],
        )
        assert r.exit_code == EXIT_OK, r.output
    assert _event_types(tmp_path)[-2:] == ["review_verdict_recorded", "review_verdict_recorded"]
    assert "plan_approved" not in _event_types(tmp_path)
    assert _state(tmp_path, "c") == "AWAITING_PLAN_REVIEW"


def test_independent_unknown_source_rejected_before_append(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    before = _event_types(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "plan-reviewer", "--source", "robot"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _event_types(tmp_path) == before


def test_independent_min_one_without_source_preserves_milestone_only(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "review", "approve", "c",
               "--reviewer", "plan-reviewer"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _event_types(tmp_path)[-1] == "plan_approved"
    assert "review_verdict_recorded" not in _event_types(tmp_path)


def test_independent_min_two_requires_source_before_append(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    before = _event_types(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "plan-reviewer"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _event_types(tmp_path) == before


def test_independent_cross_role_plan_reviewer_in_code_review_state_rejected(tmp_path: Path) -> None:
    _seed(tmp_path, "c", *_PREFIX)
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    before = _event_types(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "plan-reviewer", "--source", "subagent"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _event_types(tmp_path) == before


def test_independent_cross_role_code_reviewer_in_plan_review_state_rejected(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="code-reviewer", min_independent=2)
    before = _event_types(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "code-reviewer", "--source", "subagent"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _event_types(tmp_path) == before


def test_independent_stale_plan_partial_does_not_count_after_rejection(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    first = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "plan-reviewer", "--source", "subagent"],
    )
    assert first.exit_code == EXIT_OK, first.output
    reject = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "reject", "c",
         "--reviewer", "plan-reviewer"],
    )
    assert reject.exit_code == EXIT_OK, reject.output
    _emit(tmp_path, "plan_ready", "c")
    refresh_state_after_emit(tmp_path)
    second = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "approve", "c",
         "--reviewer", "plan-reviewer", "--source", "external"],
    )
    assert second.exit_code == EXIT_OK, second.output
    assert _event_types(tmp_path)[-1] == "review_verdict_recorded"
    assert _state(tmp_path, "c") == "AWAITING_PLAN_REVIEW"


def test_independent_reject_remains_immediate_and_records_source(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready")
    _set_independent_policy(tmp_path, reviewer="plan-reviewer", min_independent=2)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "review", "reject", "c",
         "--reviewer", "plan-reviewer", "--source", "subagent"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _event_types(tmp_path)[-1] == "plan_rejected"
    assert _events(tmp_path)[-1]["payload"]["source"] == "subagent"
