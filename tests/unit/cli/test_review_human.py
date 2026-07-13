"""First-class human review inspection, draft, and confirmation contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from click.testing import CliRunner
from pytest import MonkeyPatch

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path, pending_reviews_dir
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.review_verdict import read_change_events
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.review_runs import derive_review_execution
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    )


def _repo(root: Path) -> Path:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "human@example.test")
    _git(root, "config", "user.name", "Human Reviewer")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "feature")
    (root / "src" / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(root, "commit", "-aqm", "implementation")
    harness = root / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  base_branch: main\n"
        "  sources:\n"
        "    human:\n"
        "      kind: human\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [human]\n"
        "      min_independent: 1\n"
        "    code-reviewer:\n"
        "      participants: [human]\n"
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
    prepared = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "prepare",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )
    assert prepared.exit_code == EXIT_OK, prepared.output
    return root


def _verdict(root: Path) -> Path:
    packet_path = (
        pending_reviews_dir(root, "change")
        / "code-reviewer"
        / "draft.packet.json"
    )
    packet = json.loads(packet_path.read_text())
    verdict = {
        "bundle_digest": packet["bundle_digest"],
        "scope_sufficient": True,
        "checklist": [
            {"item": item, "status": "pass"} for item in packet["checklist"]
        ],
        "findings": [],
        "prior_findings": [],
    }
    path = root / "human-verdict.json"
    path.write_text(json.dumps(verdict), encoding="utf-8")
    return path


def _draft(root: Path) -> dict[str, object]:
    result = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "human",
            "draft",
            "change",
            "--reviewer",
            "code-reviewer",
            "--verdict-file",
            str(_verdict(root)),
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    return cast(dict[str, object], json.loads(result.output)["data"])


def test_human_inspect_json_is_compact(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "--json",
            "--workspace",
            str(root),
            "review",
            "human",
            "inspect",
            "change",
            "--reviewer",
            "code-reviewer",
        ],
    )

    assert result.exit_code == EXIT_OK, result.output
    data = json.loads(result.output)["data"]
    assert data["checklist_count"] > 0
    assert data["source_count"] == 1
    assert "assignments" not in data
    assert "prompt" not in result.output


def test_human_confirm_requires_tty_then_records_non_automatic_receipt(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    draft = _draft(root)
    args = [
        "--workspace",
        str(root),
        "review",
        "human",
        "confirm",
        "change",
        "--reviewer",
        "code-reviewer",
        "--nonce",
        cast(str, draft["nonce"]),
    ]

    non_tty = CliRunner().invoke(main, args)
    assert non_tty.exit_code == EXIT_VALIDATION
    assert "requires an interactive TTY" in non_tty.output

    monkeypatch.setattr("super_harness.cli.review._interactive_terminal", lambda: True)
    confirmed = CliRunner().invoke(main, args, input="y\n")

    assert confirmed.exit_code == EXIT_OK, confirmed.output
    assert derive_state(events_path(root))["change"].current_state == "READY_TO_MERGE"
    events = read_change_events(events_path(root), "change")
    imported = next(event for event in events if event.type == "review_result_imported")
    assert imported.payload["receipt"]["human_nonce"] == draft["nonce"]
    assert imported.payload["receipt"]["usage"] is None
    execution = derive_review_execution(events, "code-reviewer")
    assert execution.automatic_rounds_used == 0
    assert execution.rounds[-1].automatic is False


def test_expired_human_nonce_is_rejected(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    draft = _draft(root)
    nonce = cast(str, draft["nonce"])
    draft_path = (
        pending_reviews_dir(root, "change")
        / "code-reviewer"
        / "human-drafts"
        / f"{nonce}.json"
    )
    stored = json.loads(draft_path.read_text())
    stored["expires_at"] = "2000-01-01T00:00:00+00:00"
    draft_path.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr("super_harness.cli.review._interactive_terminal", lambda: True)

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "review",
            "human",
            "confirm",
            "change",
            "--reviewer",
            "code-reviewer",
            "--nonce",
            nonce,
        ],
        input="y\n",
    )

    assert result.exit_code == EXIT_VALIDATION
    assert "nonce expired" in result.output
    assert derive_state(events_path(root))["change"].current_state == "AWAITING_CODE_REVIEW"
