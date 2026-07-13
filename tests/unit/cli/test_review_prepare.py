"""Unit tests for `super-harness review prepare`."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.paths import pending_reviews_dir
from super_harness.exit_codes import EXIT_OK, EXIT_VALIDATION


def _git(ws: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=ws, check=True, capture_output=True, text=True)


def _seed_change(
    ws: Path,
    declared: list[str],
    framework: str = "plain",
    plan_reviewed_head: str | None = None,
) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    for t, p in [("intent_declared", {}), ("plan_ready", {"scope": {"files": declared}}),
                 ("plan_approved", {"reviewed_head": plan_reviewed_head}
                  if plan_reviewed_head else {}), ("implementation_started", {}),
                 ("verification_passed", {}),
                 ("implementation_complete", {})]:
        EventWriter(events_path(ws)).emit(Event(
            event_id=new_event_id(), type=t, change_id="c",
            timestamp="2026-06-23T00:00:00Z",
            actor=Actor(type="human", identifier="cli"), framework=framework, payload=p))
    refresh_state_after_emit(ws)
    _set_reviewer_source_policy(ws)


def _seed_awaiting_plan_review(ws: Path, declared: list[str]) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    for event_type, payload in [
        ("intent_declared", {}),
        ("plan_ready", {"scope": {"files": declared}}),
    ]:
        EventWriter(events_path(ws)).emit(
            Event(
                event_id=new_event_id(),
                type=event_type,
                change_id="c",
                timestamp="2026-07-11T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="plain",
                payload=payload,
            )
        )
    refresh_state_after_emit(ws)
    _set_reviewer_source_policy(ws)


def _set_reviewer_source_policy(ws: Path) -> None:
    (ws / ".harness" / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  base_branch: main\n"
        "  sources:\n"
        "    subagent:\n"
        "      kind: automated\n"
        "    external:\n"
        "      kind: automated\n"
        "  roles:\n"
        "    code-reviewer:\n"
        "      min_independent: 2\n"
        "      participants: [subagent, external]\n"
        "    plan-reviewer:\n"
        "      min_independent: 2\n"
        "      participants: [subagent, external]\n"
    )
    (ws / ".harness" / "review-profiles.local.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  subagent:\n"
        "    protocol: claude-cli\n"
        "    model: claude-review\n"
        "    agent_options:\n"
        "      effort: medium\n"
        "  external:\n"
        "    protocol: codex-cli\n"
        "    model: gpt-review\n"
        "    agent_options:\n"
        "      reasoning_effort: medium\n"
        "      sandbox: read-only\n"
    )


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feat")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "work")
    return tmp_path


def _record_source_result(ws: Path, source: str, reviewed_head: str) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    EventWriter(events_path(ws)).emit(
        Event(
            event_id=new_event_id(),
            type="review_verdict_recorded",
            change_id="c",
            timestamp="2026-07-11T00:00:00Z",
            actor=Actor(type="agent", identifier=source),
            framework="plain",
            payload={
                "reviewer": "code-reviewer",
                "source": source,
                "outcome": "approved",
                "reviewed_head": reviewed_head,
            },
        )
    )


def test_prepare_writes_bundle(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    r = CliRunner().invoke(main, ["--json", "--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_OK, r.output
    out = json.loads(r.output)
    assert out["status"] == "pass"
    bundle_path = pending_reviews_dir(ws, "c") / "code-reviewer" / "draft.packet.json"
    assert bundle_path.is_file()
    bundle = json.loads(bundle_path.read_text())
    assert bundle["diff_in_scope"] == ["src/a.py"]
    assert bundle["bundle_digest"]


def test_prepare_embeds_tracked_reviewer_governance(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    _set_reviewer_source_policy(ws)

    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])

    assert r.exit_code == EXIT_OK, r.output
    bundle = json.loads(
        (pending_reviews_dir(ws, "c") / "code-reviewer" / "draft.packet.json").read_text()
    )
    assert bundle["review_governance"] == {
        "reviewer": "code-reviewer",
        "min_independent": 2,
        "participants": ["subagent", "external"],
        "max_automatic_rounds_per_epoch": 2,
        "require_distinct_model_families": False,
        "sources": {
            "subagent": {"kind": "automated"},
            "external": {"kind": "automated"},
        },
    }
    assert bundle["profile_digest"]
    assert bundle["contract_digest"]


def test_prepare_compiles_initial_full_change_assignments(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    _set_reviewer_source_policy(ws)

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "code-reviewer"],
    )

    assert result.exit_code == EXIT_OK, result.output
    bundle = json.loads(
        (pending_reviews_dir(ws, "c") / "code-reviewer" / "draft.packet.json").read_text()
    )
    assert len(bundle["target_head"]) == 40
    assert bundle["plan_review_required"] is False
    assert [assignment["source"] for assignment in bundle["assignments"]] == [
        "subagent", "external"
    ]
    subagent, external = bundle["assignments"]
    assert subagent["agent_options"] == {"effort": "medium"}
    assert external["agent_options"] == {
        "reasoning_effort": "medium", "sandbox": "read-only"
    }
    for assignment in bundle["assignments"]:
        assert assignment["inspection"]["mode"] == "full-change"
        assert assignment["inspection"]["files"] == ["src/a.py"]
        assert assignment["inspection"]["diff_argv"][:2] == ["git", "diff"]
        assert "Review only the assigned target delta" in assignment["prompt"]
        assert bundle["bundle_digest"] in assignment["prompt"]
        assert "bundle_digest" in assignment["prompt"]
        assert "blocker | major | minor" in assignment["prompt"]


def test_prepare_scopes_plan_review_assignments_to_declared_artifacts(
    tmp_path: Path,
) -> None:
    ws = _repo(tmp_path)
    (ws / "docs").mkdir()
    (ws / "docs" / "plan.md").write_text(
        "---\nchange: c\nstage: plan\n---\n# Plan\n"
    )
    _git(ws, "add", "docs/plan.md")
    _git(ws, "commit", "-qm", "add plan")
    _seed_awaiting_plan_review(ws, ["src/", "docs/"])
    _set_reviewer_source_policy(ws)

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "plan-reviewer"],
    )

    assert result.exit_code == EXIT_OK, result.output
    bundle = json.loads(
        (pending_reviews_dir(ws, "c") / "plan-reviewer" / "draft.packet.json").read_text()
    )
    for assignment in bundle["assignments"]:
        assert assignment["inspection"]["files"] == ["docs/plan.md"]
        assert "src/a.py" not in assignment["inspection"]["diff_argv"]


def test_prepare_keeps_declared_scope_for_artifactless_plain_plan_review(
    tmp_path: Path,
) -> None:
    ws = _repo(tmp_path)
    _seed_awaiting_plan_review(ws, ["src/"])
    _set_reviewer_source_policy(ws)

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "plan-reviewer"],
    )

    assert result.exit_code == EXIT_OK, result.output
    bundle = json.loads(
        (pending_reviews_dir(ws, "c") / "plan-reviewer" / "draft.packet.json").read_text()
    )
    for assignment in bundle["assignments"]:
        assert assignment["inspection"]["files"] == ["src/a.py"]


def test_prepare_keeps_empty_plan_target_explicitly_empty(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "a.py").write_text("v1\n")
    (tmp_path / "docs" / "plan.md").write_text(
        "---\nchange: c\nstage: plan\n---\n# Existing plan\n"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base with plan")
    _git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "src" / "a.py").write_text("v2\n")
    _git(tmp_path, "commit", "-aqm", "implementation only")
    _seed_awaiting_plan_review(tmp_path, ["src/", "docs/"])
    _set_reviewer_source_policy(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "--workspace", str(tmp_path), "review", "prepare", "c",
            "--reviewer", "plan-reviewer",
        ],
    )

    assert result.exit_code == EXIT_OK, result.output
    bundle = json.loads(
        (pending_reviews_dir(tmp_path, "c") / "plan-reviewer" / "draft.packet.json").read_text()
    )
    for assignment in bundle["assignments"]:
        assert assignment["inspection"]["files"] == []
        assert assignment["inspection"]["diff_argv"] == []
        assert "do not construct a broader diff" in assignment["prompt"]


def test_prepare_batches_code_and_docs_followups_into_one_incremental_assignment(
    tmp_path: Path,
) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/", "docs/"])
    _set_reviewer_source_policy(ws)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True, capture_output=True, text=True
    ).stdout.strip()
    _record_source_result(ws, "subagent", baseline)
    _record_source_result(ws, "external", baseline)
    (ws / "src" / "a.py").write_text("v3\n")
    _git(ws, "commit", "-aqm", "fix code review finding")
    (ws / "docs").mkdir()
    (ws / "docs" / "followup.md").write_text("follow-up\n")
    _git(ws, "add", "docs/followup.md")
    _git(ws, "commit", "-qm", "document follow-up")

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "code-reviewer"],
    )

    assert result.exit_code == EXIT_OK, result.output
    bundle = json.loads(
        (pending_reviews_dir(ws, "c") / "code-reviewer" / "draft.packet.json").read_text()
    )
    assert bundle["plan_review_required"] is False
    for assignment in bundle["assignments"]:
        assert assignment["inspection"]["mode"] == "incremental"
        assert assignment["inspection"]["base"] == baseline
        assert assignment["inspection"]["files"] == ["docs/followup.md", "src/a.py"]


def test_prepare_rejects_plan_changed_after_approval(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    (ws / "docs").mkdir()
    plan = ws / "docs" / "plan.md"
    plan.write_text("---\nchange: c\nstage: plan\n---\n# Approved plan\n")
    _git(ws, "add", "docs/plan.md")
    _git(ws, "commit", "-qm", "approve plan content")
    approved_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True, capture_output=True, text=True
    ).stdout.strip()
    _seed_change(
        ws,
        ["src/", "docs/plan.md"],
        plan_reviewed_head=approved_head,
    )
    plan.write_text("---\nchange: c\nstage: plan\n---\n# Changed plan\n")
    _git(ws, "commit", "-aqm", "change approved plan")

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "code-reviewer"],
    )

    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "plan" in result.output.lower()


def test_prepare_does_not_reuse_plan_head_from_before_redeclaration(
    tmp_path: Path,
) -> None:
    from super_harness.core.clock import utc_now_iso
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    ws = _repo(tmp_path)
    (ws / "docs").mkdir()
    plan = ws / "docs" / "plan.md"
    plan.write_text("---\nchange: c\nstage: plan\n---\n# Old approved plan\n")
    _git(ws, "add", "docs/plan.md")
    _git(ws, "commit", "-qm", "approve old plan")
    old_approved_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ws,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    declared = ["src/", "docs/plan.md"]
    _seed_change(ws, declared, plan_reviewed_head=old_approved_head)

    plan.write_text("---\nchange: c\nstage: plan\n---\n# New approved plan\n")
    _git(ws, "commit", "-aqm", "approve redeclared plan")
    writer = EventWriter(events_path(ws))
    for event_type, payload in [
        ("plan_redeclared", {"reason": "requirements changed"}),
        ("plan_ready", {"scope": {"files": declared}}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
    ]:
        writer.emit(
            Event(
                event_id=new_event_id(),
                type=event_type,
                change_id="c",
                timestamp=utc_now_iso(),
                actor=Actor(type="human", identifier="cli"),
                framework="plain",
                payload=payload,
            )
        )
    refresh_state_after_emit(ws)

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "code-reviewer"],
    )

    assert result.exit_code == EXIT_OK, result.output


def test_prepare_rejects_changed_plan_inside_directory_scope(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    (ws / "docs" / "plans").mkdir(parents=True)
    plan = ws / "docs" / "plans" / "plan.md"
    plan.write_text("---\nchange: c\nstage: plan\n---\n# Approved\n")
    _git(ws, "add", "docs/plans/plan.md")
    _git(ws, "commit", "-qm", "approved plan")
    approved_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True, capture_output=True, text=True
    ).stdout.strip()
    _seed_change(ws, ["src/", "docs/plans/"], plan_reviewed_head=approved_head)
    plan.write_text("---\nchange: c\nstage: plan\n---\n# Changed\n")
    _git(ws, "commit", "-aqm", "change directory-scoped plan")

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "code-reviewer"],
    )

    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "plan" in result.output.lower()


def test_prepare_rejects_deleted_plan_after_approval(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    (ws / "docs").mkdir()
    plan = ws / "docs" / "plan.md"
    plan.write_text("---\nchange: c\nstage: plan\n---\n# Approved\n")
    _git(ws, "add", "docs/plan.md")
    _git(ws, "commit", "-qm", "approved plan")
    approved_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True, capture_output=True, text=True
    ).stdout.strip()
    _seed_change(ws, ["src/", "docs/plan.md"], plan_reviewed_head=approved_head)
    _git(ws, "rm", "docs/plan.md")
    _git(ws, "commit", "-qm", "delete approved plan")

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "code-reviewer"],
    )

    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "plan" in result.output.lower()


def test_prepare_compiles_incremental_targets_from_complete_source_baselines(
    tmp_path: Path,
) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ws, check=True, capture_output=True, text=True
    ).stdout.strip()
    _record_source_result(ws, "subagent", baseline)
    _record_source_result(ws, "external", baseline)
    (ws / "src" / "a.py").write_text("v3\n")
    _git(ws, "commit", "-aqm", "follow-up")

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "code-reviewer"],
    )

    assert result.exit_code == EXIT_OK, result.output
    bundle = json.loads(
        (pending_reviews_dir(ws, "c") / "code-reviewer" / "draft.packet.json").read_text()
    )
    subagent, external = bundle["assignments"]
    assert subagent["inspection"]["mode"] == "incremental"
    assert subagent["inspection"]["base"] == baseline
    assert external["inspection"]["mode"] == "incremental"
    assert external["inspection"]["base"] == baseline


def test_prepare_dirty_tree_errors(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])
    (ws / "src" / "a.py").write_text("dirty\n")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "prepare", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "commit" in r.output.lower()


def test_prepare_rejects_reviewer_outside_its_lifecycle_state(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"])

    result = CliRunner().invoke(
        main,
        ["--workspace", str(ws), "review", "prepare", "c", "--reviewer", "plan-reviewer"],
    )

    assert result.exit_code == EXIT_VALIDATION, result.output
    assert "state" in result.output.lower()
    assert not (pending_reviews_dir(ws, "c") / "plan-reviewer" / "draft.packet.json").exists()


def test_prepare_wires_resolver_for_openspec(tmp_path: Path) -> None:
    ws = _repo(tmp_path)
    _seed_change(ws, ["src/"], framework="openspec")
    r = CliRunner().invoke(
        main,
        ["--json", "--workspace", str(ws), "review", "prepare", "c",
         "--reviewer", "code-reviewer", "--base", "main"],
    )
    assert r.exit_code == 0, r.output
    bundle = json.loads(
        (
            ws
            / ".harness"
            / "pending-reviews"
            / "c"
            / "code-reviewer"
            / "draft.packet.json"
        ).read_text()
    )
    assert bundle["spec_path"].endswith("openspec/changes/c/proposal.md")
    assert bundle["plan_path"].endswith("openspec/changes/c/tasks.md")
