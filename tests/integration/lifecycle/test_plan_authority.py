"""Lifecycle seam tests for external evidence and plan authority."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import super_harness.sensors.verification_runner as verification_runner
from super_harness.cli import main
from super_harness.core.approval import (
    evidence_digest,
    make_code_subject,
    recognition_policy_digest,
)
from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter
from super_harness.sensors import Activity, WorkspaceContext


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

    # The CLI performs this check before writing, but the writer must repeat it
    # under its append lock so two concurrent imports cannot install conflicting
    # conclusions for the same subject.
    second_evidence = {
        **json.loads(plan_evidence.read_text(encoding="utf-8")),
        "evidence_id": "plan-2",
        "decision": "reject",
    }
    with pytest.raises(EmitPreconditionError, match="supersede"):
        writer.emit(
            _event(
                "c",
                "review_evidence_imported",
                {
                    "evidence": second_evidence,
                    "evidence_digest": evidence_digest(second_evidence),
                    "subject_id": subject["subject_id"],
                },
            )
        )

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
                "assessment": {
                    "approval_id": code_subject["approval_id"],
                    "change_id": "c",
                    "affected_commitments": ["goal"],
                    "conclusion": "implemented the approved goal",
                    "reasons": ["the committed source change matches the plan"],
                    "references": [{"path": "src/a.py"}],
                },
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


def test_verification_rejects_a_scope_change_during_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    (root / ".harness").mkdir()
    _recognition(root)
    (root / "plan.md").write_text("# plan\n", encoding="utf-8")
    (root / "src").mkdir()
    source = root / "src" / "a.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-qb", "candidate"], cwd=root, check=True)
    source.write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "implementation"], cwd=root, check=True)
    (root / ".harness" / "verification.yaml").write_text(
        """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  timeout_seconds: 30
  must_pass: true
  capture: none
  workdir: .
  env: {}
execution:
  mode: sequential
  max_parallelism: 1
  fail_fast: false
checks:
  - id: ok
    command: "true"
    shell: sh
adapter_provided: []
""",
        encoding="utf-8",
    )

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
            "[src/a.py]",
            "--plan",
            "plan.md",
            "--commitment",
            "goal=ship the change",
        ],
    )
    assert ready.exit_code == 0, ready.output
    subject = derive_state(events_path(root))["c"].pending_revision["subject"]
    evidence = _evidence(root, kind="plan", subject_id=subject["subject_id"], evidence_id="plan-1")
    assert CliRunner().invoke(
        main, ["--workspace", str(root), "review", "import", "c", "--evidence", str(evidence)]
    ).exit_code == 0
    assert CliRunner().invoke(
        main, ["--workspace", str(root), "implementation", "start", "c"]
    ).exit_code == 0

    original_run_checks = verification_runner.run_checks

    def change_scope_before_checks(*args: object, **kwargs: object) -> list[object]:
        source.write_text("value = 3\n", encoding="utf-8")
        return original_run_checks(*args, **kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(verification_runner, "run_checks", change_scope_before_checks)
    result = verification_runner.VerificationRunner().check(
        Activity(type="cli_verify", change_id="c", payload={}),
        WorkspaceContext(workspace_root=root, active_change_id="c"),
    )
    assert result.status == "fail"
    event = result.emit_events[0]
    assert event.type == "verification_failed"
    assert event.payload["verification"]["snapshot_unchanged"] is False
    assert event.payload["verification"]["outcome"] == "failed"


def test_active_recognition_does_not_accept_legacy_authority(tmp_path: Path) -> None:
    root = tmp_path
    (root / ".harness").mkdir()
    _recognition(root)
    writer = EventWriter(events_path(root))
    for event_type in ("intent_declared", "plan_ready", "plan_approved"):
        writer.emit(_event("c", event_type), skip_validation=True, historical_replay=True)
    blocked = CliRunner().invoke(
        main, ["--workspace", str(root), "implementation", "start", "c"]
    )
    assert blocked.exit_code != 0
    assert "effective" in (blocked.output + (blocked.stderr or "")) or "applicable" in (
        blocked.output + (blocked.stderr or "")
    )


def test_a17_candidate_import_path_cannot_replace_trusted_verifier(tmp_path: Path) -> None:
    candidate_checkout = Path(__file__).resolve().parents[3]
    base = _git(candidate_checkout, "rev-parse", "origin/main")
    trusted_checkout = tmp_path / "trusted-checkout"
    trusted_site = tmp_path / "trusted-site-packages"
    trusted_checkout.mkdir()
    trusted_site.mkdir()

    archive = subprocess.run(
        ["git", "archive", base],
        cwd=candidate_checkout,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        members = tar.getmembers()
        root = trusted_checkout.resolve()
        for member in members:
            extracted = (trusted_checkout / member.name).resolve()
            assert extracted == root or root in extracted.parents
        tar.extractall(trusted_checkout)
    shutil.copytree(trusted_checkout / "src" / "super_harness", trusted_site / "super_harness")

    expected_events = hashlib.sha256(
        (trusted_checkout / "src" / "super_harness" / "core" / "events.py").read_bytes()
    ).hexdigest()
    env = os.environ.copy()
    # Deliberately put the candidate checkout on PYTHONPATH.  The verifier
    # bootstrap must still put its isolated, trusted site-packages first.
    env["PYTHONPATH"] = str(candidate_checkout / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import hashlib, pathlib, sys; "
            "trusted = pathlib.Path(sys.argv[1]).resolve(); expected = sys.argv[2]; "
            "sys.path.insert(0, str(trusted)); "
            "from super_harness.cli import attest; from super_harness.core import events; "
            "verifier = pathlib.Path(attest.__file__).resolve(); "
            "loaded = pathlib.Path(events.__file__).resolve(); "
            "assert str(verifier).startswith(str(trusted)); "
            "assert str(loaded).startswith(str(trusted)); "
            "assert hashlib.sha256(loaded.read_bytes()).hexdigest() == expected; "
            "print(verifier); print(loaded)",
            str(trusted_site),
            expected_events,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(candidate_checkout / "src") not in result.stdout
    assert "trusted-site-packages" in result.stdout

    # The first cutover still uses the old verifier, so an incomplete legacy
    # attestation must fail against the candidate checkout.  Invoke the actual
    # baseline CLI imported from the trusted package rather than a stand-in
    # module, with the candidate checkout deliberately present on PYTHONPATH.
    cutover_checkout = tmp_path / "cutover-candidate"
    (cutover_checkout / ".harness" / "attestations").mkdir(parents=True)
    (cutover_checkout / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=cutover_checkout, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=cutover_checkout, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=cutover_checkout, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=cutover_checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=cutover_checkout, check=True)
    cutover_base = _git(cutover_checkout, "rev-parse", "HEAD")
    subprocess.run(["git", "checkout", "-qb", "candidate"], cwd=cutover_checkout, check=True)
    (cutover_checkout / "app.py").write_text("value = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=cutover_checkout, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=cutover_checkout, check=True)
    cutover_head = _git(cutover_checkout, "rev-parse", "HEAD")
    baseline_cli_script = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from super_harness.cli import main; "
        "sys.argv = ['super-harness', '--workspace', sys.argv[2], 'attest', 'verify', "
        "'--base', sys.argv[3], '--head', sys.argv[4]]; main()"
    )
    baseline_env = os.environ.copy()
    baseline_env["PYTHONPATH"] = str(candidate_checkout / "src")
    incomplete = subprocess.run(
        [
            sys.executable,
            "-c",
            baseline_cli_script,
            str(trusted_site),
            str(cutover_checkout),
            cutover_base,
            cutover_head,
        ],
        cwd=cutover_checkout,
        env=baseline_env,
        capture_output=True,
        text=True,
    )
    assert incomplete.returncode != 0
    assert "changed file not covered" in incomplete.stdout + incomplete.stderr
    assert str(candidate_checkout / "src") not in incomplete.stdout + incomplete.stderr

    # Once the new verifier is the trusted base for a later PR, a legacy
    # plan_approved event cannot downgrade an active recognition contract.
    new_trusted_site = tmp_path / "new-trusted-site-packages"
    shutil.copytree(
        candidate_checkout / "src" / "super_harness", new_trusted_site / "super_harness"
    )
    later_change = tmp_path / "later-change"
    (later_change / ".harness").mkdir(parents=True)
    _recognition(later_change)
    later_writer = EventWriter(later_change / ".harness" / "events.jsonl")
    for event_type in ("intent_declared", "plan_ready", "plan_approved"):
        later_writer.emit(
            _event("later-change", event_type), skip_validation=True, historical_replay=True
        )
    new_cli_script = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from super_harness.cli import main; "
        "sys.argv = ['super-harness', '--workspace', sys.argv[2], 'implementation', 'start', "
        "'later-change']; main()"
    )
    downgrade_env = os.environ.copy()
    downgrade_env["PYTHONPATH"] = str(trusted_site)
    downgrade = subprocess.run(
        [sys.executable, "-c", new_cli_script, str(new_trusted_site), str(later_change)],
        cwd=later_change,
        env=downgrade_env,
        capture_output=True,
        text=True,
    )
    assert downgrade.returncode != 0
    assert "applicable plan approval" in downgrade.stdout + downgrade.stderr
