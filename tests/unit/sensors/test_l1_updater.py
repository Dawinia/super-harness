"""Unit tests for L1Updater sensor (Phase 13 Task 13.5).

Coverage matrix (spec §3.4 v0.1-reconcile blockquote):

Short-circuits (no gh, no git, no pending file):
 1. test_no_change_id_returns_pass_skipped
 2. test_no_state_returns_informational_with_empty_completed
 3. test_no_affected_anchors_returns_informational
 4. test_all_stubs_unchanged_returns_informational

Happy path:
 5. test_happy_path_opens_pr_and_auto_merges
 6. test_happy_path_files_includes_anchor_index

AC-7 failure paths (NO re-raise; pending file + l1_update_failed emitted):
 7. test_gh_create_pr_failure_writes_pending_and_emits_failed
 8. test_gh_auto_merge_failure_writes_pending_and_emits_failed
 9. test_git_failure_writes_pending
10. test_check_does_not_re_raise_on_any_failure

Nested-failure (pending write fails) — sanity:
11. test_pending_write_failure_falls_back_to_operation_log

Registration:
12. test_l1_updater_registered_as_builtin

Event shape:
13. test_l1_update_completed_payload_key_is_pr_url

Idempotency / re-run:
14. test_rerun_after_successful_run_finds_files_current
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.gh import GhError
from super_harness.sensors import WorkspaceContext
from super_harness.sensors.l1_updater import L1Updater

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _evt(change_id: str, evt_type: str, payload: dict[str, Any] | None = None) -> Event:
    return Event(
        event_id=new_event_id(),
        type=evt_type,
        change_id=change_id,
        timestamp="2026-05-30T10:00:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework="plain",
        payload=payload or {},
    )


def _seed_events(root: Path, change_id: str, items: list[tuple[str, dict[str, Any]]]) -> None:
    w = EventWriter(events_path(root))
    for evt_type, payload in items:
        w.emit(_evt(change_id, evt_type, payload), skip_validation=True)


def _merged_trigger(change_id: str | None) -> Event:
    """Construct a `merged` Event for the sensor. change_id may be empty str."""
    return Event(
        event_id=new_event_id(),
        type="merged",
        change_id=change_id or "",
        timestamp="2026-05-30T10:30:00Z",
        actor=Actor(type="ci", identifier="test"),
        framework="plain",
        payload={"merge_commit_sha": "abc123"},
    )


def _harness_root(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _ctx(root: Path, *, active: str | None = None) -> WorkspaceContext:
    return WorkspaceContext(workspace_root=root, active_change_id=active)


# --------------------------------------------------------------------------- #
# 1. No change_id → graceful skip (pass)
# --------------------------------------------------------------------------- #


def test_no_change_id_returns_pass_skipped(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)

    # Build a trigger with an empty change_id and a context with no active id.
    trigger = _merged_trigger(None)
    ctx = _ctx(root, active=None)

    with (
        patch("super_harness.sensors.l1_updater.create_pr") as m_create,
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash") as m_merge,
        patch("super_harness.sensors.l1_updater.git_branch_commit_push") as m_git,
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "pass"
    assert "no change_id" in result.summary
    assert result.emit_events == []
    m_create.assert_not_called()
    m_merge.assert_not_called()
    m_git.assert_not_called()
    assert not (root / ".harness" / "pending-l1-updates").exists()


# --------------------------------------------------------------------------- #
# 2. No state for change_id → informational + empty completed event
# --------------------------------------------------------------------------- #


def test_no_state_returns_informational_with_empty_completed(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    # Note: events.jsonl never written for "ch-unknown" — derive_state returns {}.

    trigger = _merged_trigger("ch-unknown")
    ctx = _ctx(root)

    with (
        patch("super_harness.sensors.l1_updater.create_pr") as m_create,
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash"),
        patch("super_harness.sensors.l1_updater.git_branch_commit_push") as m_git,
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "informational"
    assert len(result.emit_events) == 1
    ev = result.emit_events[0]
    assert ev.type == "l1_update_completed"
    assert ev.change_id == "ch-unknown"
    assert ev.payload["files_updated"] == []
    assert ev.payload["pr_url"] is None
    m_create.assert_not_called()
    m_git.assert_not_called()


# --------------------------------------------------------------------------- #
# 3. plan_ready with empty affected_anchors → informational + empty completed
# --------------------------------------------------------------------------- #


def test_no_affected_anchors_returns_informational(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-empty",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": []}),
        ],
    )

    trigger = _merged_trigger("ch-empty")
    ctx = _ctx(root)

    with (
        patch("super_harness.sensors.l1_updater.create_pr") as m_create,
        patch("super_harness.sensors.l1_updater.git_branch_commit_push") as m_git,
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "informational"
    assert len(result.emit_events) == 1
    assert result.emit_events[0].payload["files_updated"] == []
    assert result.emit_events[0].payload["pr_url"] is None
    m_create.assert_not_called()
    m_git.assert_not_called()


# --------------------------------------------------------------------------- #
# 4. All stubs already match → generate_l1_stubs returns []; short-circuit
# --------------------------------------------------------------------------- #


def test_all_stubs_unchanged_returns_informational(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-current",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )
    # Pre-create the stub identical to what generate_l1_stubs would write.
    cap_dir = root / "docs" / "reference" / "capabilities"
    cap_dir.mkdir(parents=True)
    body = (
        "# cap-foo\n\n"
        "<!-- L1 capability stub auto-written by super-harness l1-updater. -->\n"
        "<!-- Real generation is v0.2+; this file marks the placeholder location. -->\n"
    )
    (cap_dir / "cap-foo.md").write_text(body)

    trigger = _merged_trigger("ch-current")
    ctx = _ctx(root)

    with (
        patch("super_harness.sensors.l1_updater.create_pr") as m_create,
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash") as m_merge,
        patch("super_harness.sensors.l1_updater.git_branch_commit_push") as m_git,
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "informational"
    assert len(result.emit_events) == 1
    assert result.emit_events[0].payload["files_updated"] == []
    assert result.emit_events[0].payload["pr_url"] is None
    m_create.assert_not_called()
    m_merge.assert_not_called()
    m_git.assert_not_called()


# --------------------------------------------------------------------------- #
# 5. Happy path: opens PR + auto-merges
# --------------------------------------------------------------------------- #


def test_happy_path_opens_pr_and_auto_merges(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-happy",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    pr_url = "https://github.com/o/r/pull/77"
    trigger = _merged_trigger("ch-happy")
    ctx = _ctx(root)

    with (
        patch("super_harness.sensors.l1_updater.create_pr", return_value=pr_url) as m_create,
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash") as m_merge,
        patch("super_harness.sensors.l1_updater.git_branch_commit_push") as m_git,
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "pass"
    assert pr_url in result.summary
    assert len(result.emit_events) == 1
    ev = result.emit_events[0]
    assert ev.type == "l1_update_completed"
    assert ev.payload["pr_url"] == pr_url
    # files_updated contains a repo-relative-ish display string.
    files = ev.payload["files_updated"]
    assert isinstance(files, list)
    assert any("cap-foo.md" in f for f in files)

    # gh.merge_pr_auto_squash called with the parsed integer PR number.
    m_merge.assert_called_once_with(77)
    # git_branch_commit_push called with the expected branch name.
    m_git.assert_called_once()
    branch_arg = m_git.call_args.args[1]
    assert branch_arg == "harness/l1-update-ch-happy"
    # create_pr received our labels.
    create_kwargs = m_create.call_args.kwargs
    assert create_kwargs["labels"] == ["harness-auto", "no-human-review"]
    assert create_kwargs["base"] == "main"
    assert create_kwargs["head"] == "harness/l1-update-ch-happy"
    # anchor index was rebuilt at the canonical path.
    assert (root / ".harness" / "anchors" / "index.yaml").is_file()


# --------------------------------------------------------------------------- #
# 6. Happy path: files list passed to git includes the anchor index
# --------------------------------------------------------------------------- #


def test_happy_path_files_includes_anchor_index(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-idx",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    trigger = _merged_trigger("ch-idx")
    ctx = _ctx(root)

    with (
        patch("super_harness.sensors.l1_updater.create_pr", return_value="https://github.com/o/r/pull/9"),
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash"),
        patch("super_harness.sensors.l1_updater.git_branch_commit_push") as m_git,
    ):
        L1Updater().check(trigger, ctx)

    files_arg: list[Path] = m_git.call_args.args[2]
    assert any(
        Path(p).parts[-3:] == (".harness", "anchors", "index.yaml") for p in files_arg
    )


# --------------------------------------------------------------------------- #
# 7. AC-7: gh create_pr failure → pending file + l1_update_failed + no re-raise
# --------------------------------------------------------------------------- #


def test_gh_create_pr_failure_writes_pending_and_emits_failed(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-ghfail",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    trigger = _merged_trigger("ch-ghfail")
    ctx = _ctx(root)

    with (
        patch(
            "super_harness.sensors.l1_updater.create_pr",
            side_effect=GhError("create_pr failed"),
        ),
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash") as m_merge,
        patch("super_harness.sensors.l1_updater.git_branch_commit_push"),
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "fail"
    assert "create_pr failed" in result.summary
    assert len(result.emit_events) == 1
    ev = result.emit_events[0]
    assert ev.type == "l1_update_failed"
    assert "create_pr failed" in ev.payload["reason"]
    pending = root / ".harness" / "pending-l1-updates" / "ch-ghfail.md"
    assert ev.payload["pending_path"] == str(pending)
    assert pending.exists()
    text = pending.read_text()
    assert "ch-ghfail" in text
    assert "create_pr failed" in text
    m_merge.assert_not_called()


# --------------------------------------------------------------------------- #
# 8. AC-7: auto-merge failure → pending + l1_update_failed
# --------------------------------------------------------------------------- #


def test_gh_auto_merge_failure_writes_pending_and_emits_failed(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-mergefail",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    trigger = _merged_trigger("ch-mergefail")
    ctx = _ctx(root)

    with (
        patch(
            "super_harness.sensors.l1_updater.create_pr",
            return_value="https://github.com/o/r/pull/55",
        ),
        patch(
            "super_harness.sensors.l1_updater.merge_pr_auto_squash",
            side_effect=GhError("merge failed"),
        ),
        patch("super_harness.sensors.l1_updater.git_branch_commit_push"),
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "fail"
    assert len(result.emit_events) == 1
    assert result.emit_events[0].type == "l1_update_failed"
    pending = root / ".harness" / "pending-l1-updates" / "ch-mergefail.md"
    assert pending.exists()
    assert "merge failed" in pending.read_text()


# --------------------------------------------------------------------------- #
# 9. AC-7: git failure → pending + failed event
# --------------------------------------------------------------------------- #


def test_git_failure_writes_pending(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-gitfail",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    trigger = _merged_trigger("ch-gitfail")
    ctx = _ctx(root)

    with (
        patch(
            "super_harness.sensors.l1_updater.git_branch_commit_push",
            side_effect=subprocess.CalledProcessError(1, ["git", "checkout"]),
        ),
        patch("super_harness.sensors.l1_updater.create_pr") as m_create,
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash") as m_merge,
    ):
        result = L1Updater().check(trigger, ctx)

    assert result.status == "fail"
    assert len(result.emit_events) == 1
    assert result.emit_events[0].type == "l1_update_failed"
    pending = root / ".harness" / "pending-l1-updates" / "ch-gitfail.md"
    assert pending.exists()
    m_create.assert_not_called()
    m_merge.assert_not_called()


# --------------------------------------------------------------------------- #
# 10. check() does not re-raise on any failure type
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc",
    [
        GhError("gh boom"),
        subprocess.CalledProcessError(1, ["git"]),
        OSError("disk hates us"),
    ],
)
def test_check_does_not_re_raise_on_any_failure(tmp_path: Path, exc: Exception) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-norerise",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    trigger = _merged_trigger("ch-norerise")
    ctx = _ctx(root)

    with (
        patch("super_harness.sensors.l1_updater.git_branch_commit_push", side_effect=exc),
        patch("super_harness.sensors.l1_updater.create_pr"),
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash"),
    ):
        # MUST NOT RAISE
        result = L1Updater().check(trigger, ctx)

    assert result.status == "fail"


# --------------------------------------------------------------------------- #
# 11. Nested failure: pending write fails → falls back to operation-log
# --------------------------------------------------------------------------- #


def test_pending_write_failure_falls_back_to_operation_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-nested",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    # Block pending-l1-updates dir creation by sitting a regular file there.
    (root / ".harness" / "pending-l1-updates").write_text("not a directory")

    trigger = _merged_trigger("ch-nested")
    ctx = _ctx(root)

    with (
        patch(
            "super_harness.sensors.l1_updater.create_pr",
            side_effect=GhError("create_pr failed"),
        ),
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash"),
        patch("super_harness.sensors.l1_updater.git_branch_commit_push"),
    ):
        # Must not raise even though the pending write fails.
        result = L1Updater().check(trigger, ctx)

    assert result.status == "fail"
    assert len(result.emit_events) == 1
    assert result.emit_events[0].type == "l1_update_failed"
    # Operation-log fallback recorded the failure.
    op_log_dir = root / ".harness" / "operation-logs" / "l1-updater"
    assert op_log_dir.is_dir()
    logs = list(op_log_dir.glob("*.log"))
    assert len(logs) >= 1


# --------------------------------------------------------------------------- #
# 12. Registration
# --------------------------------------------------------------------------- #


def test_l1_updater_registered_as_builtin() -> None:
    from super_harness.sensors.registry import get_builtin

    assert get_builtin("l1-updater") is L1Updater


# --------------------------------------------------------------------------- #
# 13. Event-shape: payload key is `pr_url` (NOT `l1_pr_url`)
# --------------------------------------------------------------------------- #


def test_l1_update_completed_payload_key_is_pr_url(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-keyssot",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    trigger = _merged_trigger("ch-keyssot")
    ctx = _ctx(root)

    with (
        patch(
            "super_harness.sensors.l1_updater.create_pr",
            return_value="https://github.com/o/r/pull/123",
        ),
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash"),
        patch("super_harness.sensors.l1_updater.git_branch_commit_push"),
    ):
        result = L1Updater().check(trigger, ctx)

    payload = result.emit_events[0].payload
    assert "pr_url" in payload
    assert "l1_pr_url" not in payload
    assert isinstance(payload["files_updated"], list)
    assert all(isinstance(f, str) for f in payload["files_updated"])


# --------------------------------------------------------------------------- #
# 14. Idempotency: rerun after successful run finds files current
# --------------------------------------------------------------------------- #


def test_rerun_after_successful_run_finds_files_current(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(
        root,
        "ch-rerun",
        [
            ("intent_declared", {"description": "x"}),
            ("plan_ready", {"affected_anchors": ["cap-foo"]}),
        ],
    )

    trigger = _merged_trigger("ch-rerun")
    ctx = _ctx(root)

    # First run: happy path.
    with (
        patch(
            "super_harness.sensors.l1_updater.create_pr",
            return_value="https://github.com/o/r/pull/1",
        ),
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash"),
        patch("super_harness.sensors.l1_updater.git_branch_commit_push"),
    ):
        first = L1Updater().check(trigger, ctx)
    assert first.status == "pass"

    # Second run: stub should already match → short-circuit; mocks not called.
    with (
        patch("super_harness.sensors.l1_updater.create_pr") as m_create,
        patch("super_harness.sensors.l1_updater.merge_pr_auto_squash") as m_merge,
        patch("super_harness.sensors.l1_updater.git_branch_commit_push") as m_git,
    ):
        second = L1Updater().check(trigger, ctx)

    assert second.status == "informational"
    assert second.emit_events[0].payload["files_updated"] == []
    assert second.emit_events[0].payload["pr_url"] is None
    m_create.assert_not_called()
    m_merge.assert_not_called()
    m_git.assert_not_called()
