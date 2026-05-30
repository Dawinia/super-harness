"""Integration tests for ``super-harness on-merge --commit <sha>`` (Phase 13 Task 13.6).

``on-merge`` is the CI-side ``merged`` emitter that fires the L1 follow-up
workflow. It is the first **production** caller of
``SensorDispatcher.on_event_emit`` (verify/done call ``on_activity``).

Coverage map (per the brief):

Resolution:
  1. test_explicit_change_wins
  2. test_fallback_parses_merge_commit_message
  3. test_unresolved_exits_1_with_actionable_stderr

Wiring:
  4. test_emits_merged_event_then_refreshes_state
  5. test_dispatches_l1_updater_and_anchor_index_rebuilder
  6. test_data_schema_pass_path
  7. test_l1_followup_pr_is_null_when_l1_updater_failed
  8. test_l1_followup_pr_is_null_when_no_anchors

Pre-flight:
  9. test_no_harness_dir_exits_3

Format:
 10. test_human_mode_summary_to_stdout
 11. test_json_mode_no_envelope_on_exit_3_or_1

gh is ALWAYS mocked (Phase 12 lesson). The merge-commit-parse fallback
DOES need real git in a tmp repo, used only via ``_init_repo_with_merge_commit``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest import mock

import yaml
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import anchors_index_path, events_path, state_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.gh import GhError
from super_harness.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
)

# Where the gh wrappers + git push helper land when imported by l1_updater.
# Mock at the SENSOR import site (Phase 12 pattern), since the dispatcher
# transitively calls into the sensor.
CREATE_PR = "super_harness.sensors.l1_updater.create_pr"
MERGE_PR = "super_harness.sensors.l1_updater.merge_pr_auto_squash"
GIT_OPS = "super_harness.sensors.l1_updater.git_branch_commit_push"


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


CHANGE_ID = "2026-05-30-add-foo"


def _evt(change_id: str, evt_type: str, payload: dict[str, Any] | None = None) -> Event:
    return Event(
        event_id=new_event_id(),
        type=evt_type,
        change_id=change_id,
        timestamp="2026-05-30T10:00:00Z",
        actor=Actor(type="human", identifier="cli"),
        framework="plain",
        payload=payload or {},
    )


def _drive_to_ready_to_merge(
    root: Path, change_id: str, *, anchors: list[str] | None = None
) -> None:
    """Drive ``change_id`` to READY_TO_MERGE so a strict ``merged`` emit is legal.

    Per transitions: intent_declared → plan_ready → plan_approved →
    implementation_started → verification_passed → implementation_complete →
    code_review_passed. (``implementation_complete`` requires
    ``verification_passed`` per ``_HARD_PREREQ_EVENTS``.)
    """
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    writer = EventWriter(events_path(root))
    sequence: list[tuple[str, dict[str, Any]]] = [
        ("intent_declared", {"description": "x"}),
        ("plan_ready", {"affected_anchors": anchors or []}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
        ("code_review_passed", {}),
    ]
    for evt_type, payload in sequence:
        writer.emit(_evt(change_id, evt_type, payload))
    refresh_state_after_emit(root)


def _read_event_types(root: Path) -> list[str]:
    if not events_path(root).exists():
        return []
    return [
        json.loads(line)["type"]
        for line in events_path(root).read_text().splitlines()
        if line.strip()
    ]


def _read_events(root: Path) -> list[dict[str, Any]]:
    if not events_path(root).exists():
        return []
    return [
        json.loads(line)
        for line in events_path(root).read_text().splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------------- #
# Real-git fallback helpers (merge-commit-message parse)
# --------------------------------------------------------------------------- #


def _run_git(root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <argv>`` in ``root`` with isolated config (no user creds leak)."""
    env = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "HOME": str(root),  # isolate ~/.gitconfig
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    return subprocess.run(
        ["git", *argv],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo_with_commit_subject(root: Path, subject: str) -> str:
    """Init a tmp repo with one empty commit whose subject = ``subject``.

    Returns the commit SHA (full). Used by the fallback-parse tests so the
    on-merge command's `git log -1 --format=%s <sha>` lookup returns ``subject``.
    """
    _run_git(root, "init", "--quiet", "-b", "main")
    _run_git(root, "commit", "--allow-empty", "-m", subject)
    out = _run_git(root, "rev-parse", "HEAD")
    return out.stdout.strip()


# --------------------------------------------------------------------------- #
# 1. Resolution — explicit --change wins
# --------------------------------------------------------------------------- #


def test_explicit_change_wins(tmp_path: Path) -> None:
    """``--change my-slug`` wins; merge-commit parse fallback is NOT consulted."""
    _drive_to_ready_to_merge(tmp_path, "my-slug")

    # Use a SHA whose commit-message would parse to a different slug if the
    # fallback were consulted — the explicit flag must short-circuit.
    sha = _init_repo_with_commit_subject(
        tmp_path, "Merge pull request #99 from owner/wrong-slug"
    )

    with (
        mock.patch(CREATE_PR, return_value="https://github.com/o/r/pull/200"),
        mock.patch(MERGE_PR),
        mock.patch(GIT_OPS),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "on-merge",
                "--commit",
                sha,
                "--change",
                "my-slug",
            ],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    # The emitted merged event references my-slug (explicit), not wrong-slug.
    types_for_explicit = [
        e for e in _read_events(tmp_path) if e["type"] == "merged"
    ]
    assert len(types_for_explicit) == 1
    assert types_for_explicit[0]["change_id"] == "my-slug"


# --------------------------------------------------------------------------- #
# 2. Resolution — fallback parses merge-commit message
# --------------------------------------------------------------------------- #


def test_fallback_parses_valid_slug_branch(tmp_path: Path) -> None:
    """No ``--change``; merge-commit subject ``Merge pull request #N from owner/<slug>``
    → branch becomes the change_id when it is a valid kebab-case slug.
    """
    sha = _init_repo_with_commit_subject(
        tmp_path,
        "Merge pull request #42 from owner/my-feature-branch",
    )
    _drive_to_ready_to_merge(tmp_path, "my-feature-branch")

    with (
        mock.patch(CREATE_PR, return_value="https://github.com/o/r/pull/201"),
        mock.patch(MERGE_PR),
        mock.patch(GIT_OPS),
    ):
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "on-merge", "--commit", sha],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    merged = [e for e in _read_events(tmp_path) if e["type"] == "merged"]
    assert len(merged) == 1
    assert merged[0]["change_id"] == "my-feature-branch"


def test_fallback_captures_invalid_slug_then_validate_rejects(tmp_path: Path) -> None:
    """Architecture-round A6 guard: the regex captures branch names containing
    ``/`` (e.g. ``feature/foo-bar``) intact rather than truncating at ``/``, but
    ``validate_slug`` REJECTS the resulting value with an actionable stderr
    message — `feature/foo-bar` is not a valid kebab slug per ``core/slug.py``.
    Without this gate the slug would silently pollute the L1 follow-up branch
    name and the pending-file path.
    """
    sha = _init_repo_with_commit_subject(
        tmp_path,
        "Merge pull request #42 from owner/feature/foo-bar",
    )
    # No need to drive state — validation fires BEFORE the merged event emit.
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "on-merge", "--commit", sha],
    )

    assert r.exit_code == 1, r.output + (r.stderr or "")
    err = (r.stderr or "") + r.output
    assert "invalid change_id" in err
    assert "feature/foo-bar" in err
    # No `merged` event should have been emitted (validation gate is pre-emit).
    assert [e for e in _read_events(tmp_path) if e["type"] == "merged"] == []


# --------------------------------------------------------------------------- #
# 3. Resolution — unresolved exits 1 with actionable stderr (no envelope)
# --------------------------------------------------------------------------- #


def test_unresolved_exits_1_with_actionable_stderr(tmp_path: Path) -> None:
    """Squash-style subject (no ``Merge pull request`` prefix) → exit 1 + stderr."""
    sha = _init_repo_with_commit_subject(tmp_path, "feat(v0.1): blah (#7)")
    # `.harness/` must exist so we get PAST the exit-3 path and into resolution.
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "on-merge", "--commit", sha],
    )

    assert r.exit_code == EXIT_GENERIC
    combined = (r.stderr or "") + r.output
    assert sha in combined
    assert "--change" in combined
    # No JSON envelope on stdout even under --json (matches verify pattern).
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# 4. Wiring — emits merged then refreshes state.yaml
# --------------------------------------------------------------------------- #


def test_emits_merged_event_then_refreshes_state(tmp_path: Path) -> None:
    """Happy path emits a ``merged`` event AND refreshes ``state.yaml`` to MERGED."""
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID)

    with (
        mock.patch(CREATE_PR, return_value="https://github.com/o/r/pull/300"),
        mock.patch(MERGE_PR),
        mock.patch(GIT_OPS),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "on-merge",
                "--commit",
                "deadbeef",
                "--change",
                CHANGE_ID,
            ],
        )
    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")

    merged_events = [e for e in _read_events(tmp_path) if e["type"] == "merged"]
    assert len(merged_events) == 1
    ev = merged_events[0]
    assert ev["change_id"] == CHANGE_ID
    # Payload key is `merge_commit_sha` (reducer SSOT — see core/reducer.py:146).
    assert ev["payload"].get("merge_commit_sha") == "deadbeef"
    assert ev["actor"]["type"] == "ci"

    # state.yaml reflects the merge.
    state_doc = yaml.safe_load(state_path(tmp_path).read_text())
    changes = state_doc.get("changes", {})
    assert changes.get(CHANGE_ID, {}).get("current_state") in {"MERGED", "ARCHIVED"}


# --------------------------------------------------------------------------- #
# 5. Wiring — dispatches L1Updater AND AnchorIndexRebuilder
# --------------------------------------------------------------------------- #


def test_dispatches_l1_updater_and_anchor_index_rebuilder(tmp_path: Path) -> None:
    """Both sensors fire on the ``merged`` event.

    L1Updater proves itself by emitting ``l1_update_completed``; the rebuilder
    proves itself by writing ``.harness/anchors/index.yaml``.
    """
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID, anchors=["cap-foo"])

    with (
        mock.patch(CREATE_PR, return_value="https://github.com/o/r/pull/301"),
        mock.patch(MERGE_PR),
        mock.patch(GIT_OPS),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "on-merge",
                "--commit",
                "abc",
                "--change",
                CHANGE_ID,
            ],
        )
    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")

    types = _read_event_types(tmp_path)
    assert "l1_update_completed" in types  # L1Updater fired
    # Rebuilder is silent (no event) but its side-effect = anchors/index.yaml.
    assert anchors_index_path(tmp_path).is_file()


# --------------------------------------------------------------------------- #
# 6. Frozen `data` schema on the pass path
# --------------------------------------------------------------------------- #


def test_data_schema_pass_path(tmp_path: Path) -> None:
    """``--json`` envelope on the pass path carries the frozen ``data`` schema."""
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID, anchors=["cap-foo"])
    pr_url = "https://github.com/o/r/pull/123"

    with (
        mock.patch(CREATE_PR, return_value=pr_url),
        mock.patch(MERGE_PR),
        mock.patch(GIT_OPS),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "--json",
                "on-merge",
                "--commit",
                "deadbeef",
                "--change",
                CHANGE_ID,
            ],
        )
    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")

    payload = json.loads(r.stdout)
    assert payload["command"] == "on-merge"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == EXIT_OK
    data = payload["data"]
    # Frozen key set (cli-command-surface §on-merge data).
    assert set(data) == {
        "commit_sha",
        "change_id",
        "events_emitted",
        "sensors_triggered",
        "l1_followup_pr",
    }
    assert data["commit_sha"] == "deadbeef"
    assert data["change_id"] == CHANGE_ID
    assert data["events_emitted"] == ["merged"]
    assert data["sensors_triggered"] == ["l1-updater", "anchor-index-rebuilder"]
    assert data["l1_followup_pr"] == pr_url


# --------------------------------------------------------------------------- #
# 7. l1_followup_pr is null when L1Updater failed (gh.create_pr raises)
# --------------------------------------------------------------------------- #


def test_l1_followup_pr_is_null_when_l1_updater_failed(tmp_path: Path) -> None:
    """gh.create_pr raises ``GhError`` → exit 0, ``l1_followup_pr`` is None,
    ``l1_update_failed`` lands in events.jsonl, ``pending-l1-updates/<slug>.md`` exists.
    """
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID, anchors=["cap-foo"])

    with (
        mock.patch(CREATE_PR, side_effect=GhError("gh pr create failed (exit 1)")),
        mock.patch(MERGE_PR),
        mock.patch(GIT_OPS),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "--json",
                "on-merge",
                "--commit",
                "abc",
                "--change",
                CHANGE_ID,
            ],
        )

    # Merge already happened — l1-updater failure does NOT change the exit code.
    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    payload = json.loads(r.stdout)
    assert payload["data"]["l1_followup_pr"] is None

    types = _read_event_types(tmp_path)
    assert "l1_update_failed" in types
    pending = tmp_path / ".harness" / "pending-l1-updates" / f"{CHANGE_ID}.md"
    assert pending.is_file()


# --------------------------------------------------------------------------- #
# 8. l1_followup_pr is null with no anchors (short-circuit path)
# --------------------------------------------------------------------------- #


def test_l1_followup_pr_is_null_when_no_anchors(tmp_path: Path) -> None:
    """plan_ready with empty ``affected_anchors`` → exit 0; null PR; no gh/git calls;
    events.jsonl carries a completed event with empty files; no follow-up branch.
    """
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID, anchors=[])

    with (
        mock.patch(CREATE_PR) as m_create,
        mock.patch(MERGE_PR) as m_merge,
        mock.patch(GIT_OPS) as m_git,
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "--json",
                "on-merge",
                "--commit",
                "abc",
                "--change",
                CHANGE_ID,
            ],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    payload = json.loads(r.stdout)
    assert payload["data"]["l1_followup_pr"] is None

    # Sensor emitted l1_update_completed with empty files.
    completed = [
        e for e in _read_events(tmp_path) if e["type"] == "l1_update_completed"
    ]
    assert len(completed) == 1
    assert completed[0]["payload"].get("files_updated") == []
    # No gh / git activity at all.
    m_create.assert_not_called()
    m_merge.assert_not_called()
    m_git.assert_not_called()


# --------------------------------------------------------------------------- #
# 9. Pre-flight — no .harness/ → exit 3
# --------------------------------------------------------------------------- #


def test_no_harness_dir_exits_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "on-merge", "--commit", "abc", "--change", "x"],
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = (r.stderr or "") + r.output
    assert "No .harness/" in combined or "init" in combined


# --------------------------------------------------------------------------- #
# 10. Human mode — one-line summary
# --------------------------------------------------------------------------- #


def test_human_mode_summary_to_stdout(tmp_path: Path) -> None:
    """Without ``--json``, the pass path prints a brief one-line summary."""
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID, anchors=["cap-foo"])
    pr_url = "https://github.com/o/r/pull/501"

    with (
        mock.patch(CREATE_PR, return_value=pr_url),
        mock.patch(MERGE_PR),
        mock.patch(GIT_OPS),
    ):
        r = CliRunner().invoke(
            main,
            [
                "--workspace",
                str(tmp_path),
                "on-merge",
                "--commit",
                "abc",
                "--change",
                CHANGE_ID,
            ],
        )

    assert r.exit_code == EXIT_OK, r.output + (r.stderr or "")
    assert CHANGE_ID in r.stdout
    # Not JSON.
    assert not r.stdout.lstrip().startswith("{")


# --------------------------------------------------------------------------- #
# 11. JSON mode — no envelope on exit 3 / exit 1 (matches verify pattern)
# --------------------------------------------------------------------------- #


def test_json_mode_no_envelope_on_exit_3_or_1(tmp_path: Path) -> None:
    """``--json`` + no ``.harness/`` → exit 3, NO envelope, format_error to stderr."""
    r = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "--json",
            "on-merge",
            "--commit",
            "abc",
            "--change",
            "x",
        ],
    )
    assert r.exit_code == EXIT_NO_CONFIG
    # No JSON envelope on stdout even under --json (verify's HarnessNotInitialized
    # pattern).
    assert r.stdout.strip() == ""
    assert (r.stderr or "").strip() != ""


# --------------------------------------------------------------------------- #
# Architecture-round A5 guard: frozen `sensors_triggered` must match the set
# of registered builtin sensors whose triggers_on_events contains "merged".
# --------------------------------------------------------------------------- #


def test_frozen_sensors_triggered_matches_registered_merged_sensors() -> None:
    """cli-command-surface §on-merge `data.sensors_triggered` is a FROZEN
    list: ``["l1-updater", "anchor-index-rebuilder"]``. If a future
    contributor registers a third ``merged``-triggered builtin sensor without
    updating the on-merge hardcoded list AND the spec freeze, the runtime
    dispatched set (which would include the new sensor) silently diverges
    from the advertised contract. Fail loudly here so the freeze cannot
    drift unobserved.
    """
    from super_harness.cli.on_merge import _SENSORS_TRIGGERED
    from super_harness.sensors.registry import get_builtin, list_builtins

    registered_merged_triggered = sorted(
        name
        for name in list_builtins()
        if "merged" in get_builtin(name).triggers_on_events
    )
    expected = sorted(_SENSORS_TRIGGERED)
    assert registered_merged_triggered == expected, (
        f"frozen on-merge sensors_triggered {expected!r} drifted from "
        f"registered merged-triggered builtins {registered_merged_triggered!r}. "
        "Update cli-command-surface §on-merge data schema AND _SENSORS_TRIGGERED "
        "together, or remove the new sensor's merged trigger."
    )
