"""Receipt-only verdict gate for the review execution protocol."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import EXIT_VALIDATION


def _awaiting_review(root: Path, *, legacy: bool = False) -> None:
    harness = root / ".harness"
    harness.mkdir()
    if legacy:
        (harness / "policy.yaml").write_text(
            "reviewers:\n  sources: [external]\n", encoding="utf-8"
        )
    else:
        (harness / "review-governance.yaml").write_text(
            "version: 1\n"
            "review:\n"
            "  sources:\n"
            "    external:\n"
            "      kind: automated\n"
            "    human:\n"
            "      kind: human\n"
            "  roles:\n"
            "    plan-reviewer:\n"
            "      participants: [human]\n"
            "      min_independent: 1\n"
            "    code-reviewer:\n"
            "      participants: [external]\n"
            "      min_independent: 1\n",
            encoding="utf-8",
        )
    for event_type, payload in [
        ("intent_declared", {}),
        ("plan_ready", {"scope": {"files": ["src/"]}}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
    ]:
        EventWriter(events_path(root)).emit(
            Event(
                event_id=new_event_id(),
                type=event_type,
                change_id="change",
                timestamp="2026-07-13T00:00:00Z",
                actor=Actor(type="human", identifier="test"),
                framework="plain",
                payload=payload,
            )
        )
    refresh_state_after_emit(root)


def test_direct_automated_approve_cannot_bypass_imported_receipt(tmp_path: Path) -> None:
    _awaiting_review(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "review",
            "approve",
            "change",
            "--reviewer",
            "code-reviewer",
            "--source",
            "external",
        ],
    )

    assert result.exit_code == EXIT_VALIDATION
    assert "direct review approve/reject is disabled" in result.output
    assert "review result import" in result.output


def test_direct_human_reject_cannot_bypass_nonce_confirmation(tmp_path: Path) -> None:
    _awaiting_review(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "review",
            "reject",
            "change",
            "--reviewer",
            "code-reviewer",
            "--source",
            "human",
        ],
    )

    assert result.exit_code == EXIT_VALIDATION
    assert "review human draft" in result.output
    assert "review human confirm" in result.output


def test_legacy_policy_cannot_record_new_direct_evidence(tmp_path: Path) -> None:
    _awaiting_review(tmp_path, legacy=True)

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "review",
            "approve",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )

    assert result.exit_code == EXIT_VALIDATION
    assert "legacy .harness/policy.yaml" in result.output
    assert "review-governance.yaml" in result.output
