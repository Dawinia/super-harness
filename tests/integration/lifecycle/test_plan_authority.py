"""Lifecycle seam tests for external evidence and plan authority."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.approval import make_code_subject, recognition_policy_digest
from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def _event(slug: str, event_type: str, payload: dict | None = None) -> Event:
    return Event(
        event_id=new_event_id(),
        type=event_type,
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="test"),
        framework="plain",
        payload=payload or {},
    )


def _recognition(root: Path) -> None:
    policy = {
        "version": "review-recognition/v1",
        "enabled": True,
        "process": {
            "id": "owner-review",
            "version": "1",
            "kinds": ["plan", "code"],
            "issuers": ["owner"],
            "evidence_forms": ["json"],
        },
    }
    policy["process"]["policy_digest"] = recognition_policy_digest(policy)
    (root / ".harness" / "review-recognition.yaml").write_text(
        yaml.safe_dump(policy, sort_keys=False), encoding="utf-8"
    )


def _evidence(root: Path, *, kind: str, subject_id: str, evidence_id: str) -> Path:
    path = root / f"{evidence_id}.json"
    recognition = yaml.safe_load(
        (root / ".harness" / "review-recognition.yaml").read_text(encoding="utf-8")
    )
    path.write_text(
        json.dumps(
            {
                "version": "review-evidence/v1",
                "evidence_id": evidence_id,
                "kind": kind,
                "subject_id": subject_id,
                "decision": "approve",
                "process": {"id": "owner-review", "version": "1"},
                "issuer": "owner",
                "original_evidence": {"conclusion": "approve", "notes": "checked"},
                "provenance": {
                    "change_id": "c",
                    "source": "owner-review",
                    "policy_digest": recognition_policy_digest(recognition),
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_external_plan_and_code_evidence_drive_only_matching_subjects(tmp_path: Path) -> None:
    root = tmp_path
    (root / ".harness").mkdir()
    _recognition(root)
    (root / "plan.md").write_text("# plan\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    base = _git(root, "rev-parse", "HEAD")

    writer = EventWriter(events_path(root))
    writer.emit(_event("c", "intent_declared"))
    ready = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "plan",
            "ready",
            "c",
            "--scope",
            "[plan.md, src/a.py]",
            "--plan",
            "plan.md",
            "--commitment",
            "goal=ship the change",
        ],
    )
    assert ready.exit_code == 0, ready.output
    state = derive_state(events_path(root))["c"]
    subject = state.pending_revision["subject"]
    plan_evidence = _evidence(
        root, kind="plan", subject_id=subject["subject_id"], evidence_id="plan-1"
    )
    imported = CliRunner().invoke(
        main, ["--workspace", str(root), "review", "import", "c", "--evidence", str(plan_evidence)]
    )
    assert imported.exit_code == 0, imported.output
    assert derive_state(events_path(root))["c"].effective_approval is not None

    # A new-contract raw milestone cannot be smuggled through the writer.
    with pytest.raises(EmitPreconditionError):
        writer.emit(_event("c", "implementation_started"), skip_validation=True)
    started = CliRunner().invoke(main, ["--workspace", str(root), "implementation", "start", "c"])
    assert started.exit_code == 0, started.output

    (root / "src" / "a.py").write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "implementation"], cwd=root, check=True)
    code_subject = make_code_subject(
        root,
        change_id="c",
        approval_id=derive_state(events_path(root))["c"].effective_approval["approval_id"],
        base=base,
    )
    writer.emit(
        _event(
            "c",
            "verification_passed",
            {
                "verification": {
                    "subject_id": code_subject["subject_id"],
                    "code_subject": code_subject,
                    "outcome": "passed",
                }
            },
        )
    )
    with pytest.raises(EmitPreconditionError, match="implementation assessment"):
        writer.emit(_event("c", "implementation_complete", {"code_subject": code_subject}))
    writer.emit(
        _event(
            "c",
            "implementation_recorded",
            {
                "assessment": {"approval_id": code_subject["approval_id"], "within_approval": True},
                "coverage": [{"path": "src/a.py", "reason": "implemented"}],
            },
        )
    )
    writer.emit(_event("c", "implementation_complete", {"code_subject": code_subject}))
    code_evidence = _evidence(
        root, kind="code", subject_id=code_subject["subject_id"], evidence_id="code-1"
    )
    imported_code = CliRunner().invoke(
        main,
        ["--workspace", str(root), "review", "import", "c", "--evidence", str(code_evidence)],
    )
    assert imported_code.exit_code == 0, imported_code.output
    assert derive_state(events_path(root))["c"].current_state == "READY_TO_MERGE"

    refresh_state_after_emit(root)
    assert derive_state(events_path(root))["c"].evidence_references


def test_plan_redeclare_withdraws_previous_authority(tmp_path: Path) -> None:
    root = tmp_path
    (root / ".harness").mkdir()
    _recognition(root)
    (root / "plan.md").write_text("# plan\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)

    writer = EventWriter(events_path(root))
    writer.emit(_event("c", "intent_declared"))
    ready = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(root),
            "plan",
            "ready",
            "c",
            "--scope",
            "[plan.md]",
            "--plan",
            "plan.md",
            "--commitment",
            "goal=ship the change",
        ],
    )
    assert ready.exit_code == 0, ready.output
    subject = derive_state(events_path(root))["c"].pending_revision["subject"]
    evidence = _evidence(root, kind="plan", subject_id=subject["subject_id"], evidence_id="plan-1")
    imported = CliRunner().invoke(
        main, ["--workspace", str(root), "review", "import", "c", "--evidence", str(evidence)]
    )
    assert imported.exit_code == 0, imported.output

    redeclared = CliRunner().invoke(
        main,
        ["--workspace", str(root), "plan", "redeclare", "c", "--reason", "changed commitment"],
    )
    assert redeclared.exit_code == 0, redeclared.output
    state = derive_state(events_path(root))["c"]
    assert state.current_state == "INTENT_DECLARED"
    assert state.effective_approval is None
    assert state.legacy_plan_approval is None
    assert state.pending_revision is None
    assert state.current_code_subject is None
    assert state.current_verification is None

    blocked = CliRunner().invoke(main, ["--workspace", str(root), "implementation", "start", "c"])
    assert blocked.exit_code != 0
