"""Integration tests for ``super-harness on-merge --commit <sha>`` (Phase 13 Task 13.6).

``on-merge`` is the CI-side ``merged`` emitter. The ``merged`` event transitions
the change directly to ARCHIVED — there is no post-merge sensor dispatch.

Coverage map:

Resolution:
  1. test_explicit_change_wins
  2. test_fallback_parses_valid_slug_branch
  3. test_unresolved_exits_1_with_actionable_stderr

Wiring:
  4. test_emits_merged_event_then_refreshes_state
  6. test_data_schema_pass_path

Pre-flight:
  9. test_no_harness_dir_exits_3

Format:
 10. test_human_mode_summary_to_stdout
 11. test_json_mode_no_envelope_on_exit_3_or_1

The merge-commit-parse fallback DOES need real git in a tmp repo, used via
``_init_repo_with_commit_subject``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path, state_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
)

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


def _drive_to_ready_to_merge(root: Path, change_id: str) -> None:
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
        ("plan_ready", {}),
        ("plan_approved", {}),
        ("implementation_started", {}),
        ("verification_passed", {}),
        ("implementation_complete", {}),
        ("code_review_passed", {}),
    ]
    for evt_type, payload in sequence:
        writer.emit(_evt(change_id, evt_type, payload))
    refresh_state_after_emit(root)


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
    """Happy path emits a ``merged`` event AND refreshes ``state.yaml`` to ARCHIVED."""
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID)

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
    assert changes.get(CHANGE_ID, {}).get("current_state") == "ARCHIVED"


# --------------------------------------------------------------------------- #
# 6. Frozen `data` schema on the pass path
# --------------------------------------------------------------------------- #


def test_data_schema_pass_path(tmp_path: Path) -> None:
    """``--json`` envelope on the pass path carries the frozen ``data`` schema."""
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID)

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
    }
    assert data["commit_sha"] == "deadbeef"
    assert data["change_id"] == CHANGE_ID
    assert data["events_emitted"] == ["merged"]


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
    _drive_to_ready_to_merge(tmp_path, CHANGE_ID)

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
