"""Unit tests for `super-harness anchor` subgroup (Phase 11 Task 11.3).

Coverage:
  1. anchor sync — index written, exit 0, summary printed.
  2. anchor list on absent index → friendly message, exit 0.
  3. anchor list after sync → shows anchor rows; --capability filters.
  4. anchor list on corrupt index.yaml → exit 3, error on stderr, no traceback.
  5. anchor list --missing-sentinel with declared anchor absent from index → reports it.
  6. anchor list --missing-sentinel with no active change → friendly note, exit 0.
  7. anchor list --capability for unknown id → clear message, exit 0.
  8. No .harness/ tree → EXIT_NO_CONFIG for both subcommands.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK
from super_harness.core.events import Actor, Event
from super_harness.core.paths import anchors_index_path, events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_workspace(ws: Path) -> None:
    """Create a minimal .harness/ directory."""
    (ws / ".harness").mkdir(parents=True, exist_ok=True)


def _plant_sentinel(ws: Path, rel_path: str, anchor_id: str, line: int = 1) -> None:
    """Write a source file with a @capability:<anchor_id> sentinel."""
    target = ws / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write enough lines so the sentinel sits at `line`.
    lines = ["\n"] * (line - 1) + [f"# @capability:{anchor_id}\n"]
    target.write_text("".join(lines))


def _emit_plan_ready(ws: Path, change_id: str, affected_anchors: list[str]) -> None:
    """Emit intent_declared + plan_ready (with affected_anchors) and refresh state."""
    writer = EventWriter(events_path(ws))
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="intent_declared",
            change_id=change_id,
            timestamp="2026-05-29T00:00:00Z",
            actor=Actor(type="human", identifier="cli"),
            framework="plain",
            payload={},
        )
    )
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="plan_ready",
            change_id=change_id,
            timestamp="2026-05-29T00:01:00Z",
            actor=Actor(type="human", identifier="cli"),
            framework="plain",
            payload={"affected_anchors": affected_anchors},
        )
    )
    refresh_state_after_emit(ws)


# ---------------------------------------------------------------------------
# anchor sync
# ---------------------------------------------------------------------------


def test_anchor_sync_builds_index_and_exits_0(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _plant_sentinel(tmp_path, "src/foo.py", "my-cap", line=3)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])

    assert r.exit_code == EXIT_OK, r.output
    idx = anchors_index_path(tmp_path)
    assert idx.exists(), "index.yaml should be written"
    assert "my-cap" in idx.read_text()
    assert "rebuilt" in r.output  # summary line contains the word "rebuilt"


def test_anchor_sync_no_harness_exits_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "No .harness/" in combined


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root bypasses filesystem permission bits"
)
def test_anchor_sync_unwritable_harness_exits_clean(tmp_path: Path) -> None:
    # A read-only .harness/ makes the rebuild's mkdir/write raise OSError. It must
    # surface as a clean error (exit 1), never a raw traceback.
    _init_workspace(tmp_path)
    harness = tmp_path / ".harness"
    os.chmod(harness, 0o500)  # r-x: cannot create anchors/ subdir
    try:
        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])
    finally:
        os.chmod(harness, 0o700)  # restore so tmp cleanup works

    assert r.exit_code == EXIT_GENERIC, r.output
    combined = r.output + (r.stderr or "")
    assert "could not write" in combined
    assert isinstance(r.exception, SystemExit), (
        f"Expected SystemExit, got {type(r.exception)}: {r.exception}"
    )
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# anchor list — absent index
# ---------------------------------------------------------------------------


def test_anchor_list_absent_index_friendly_message_exit_0(tmp_path: Path) -> None:
    _init_workspace(tmp_path)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "list"])

    assert r.exit_code == EXIT_OK, r.output
    assert "anchor sync" in r.output
    assert "No anchor index" in r.output


# ---------------------------------------------------------------------------
# anchor list — after sync
# ---------------------------------------------------------------------------


def test_anchor_list_shows_rows_after_sync(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _plant_sentinel(tmp_path, "src/alpha.py", "cap-alpha", line=1)
    _plant_sentinel(tmp_path, "src/beta.py", "cap-beta", line=2)

    CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "list"])

    assert r.exit_code == EXIT_OK, r.output
    assert "cap-alpha" in r.output
    assert "cap-beta" in r.output
    # Each row should contain file:line
    assert "src/alpha.py:1" in r.output
    assert "src/beta.py:2" in r.output


def test_anchor_list_capability_filter(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _plant_sentinel(tmp_path, "src/alpha.py", "cap-alpha", line=1)
    _plant_sentinel(tmp_path, "src/beta.py", "cap-beta", line=2)

    CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "anchor", "list", "--capability", "cap-alpha"],
    )

    assert r.exit_code == EXIT_OK, r.output
    assert "cap-alpha" in r.output
    assert "cap-beta" not in r.output


def test_anchor_list_capability_not_found_message_exit_0(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    # No sentinels — sync produces empty index.
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])

    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "anchor", "list", "--capability", "nonexistent"],
    )

    assert r.exit_code == EXIT_OK, r.output
    assert "not found" in r.output


# ---------------------------------------------------------------------------
# anchor list — corrupt index
# ---------------------------------------------------------------------------


def test_anchor_list_corrupt_index_exits_3(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    idx = anchors_index_path(tmp_path)
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(":::not yaml :::\n{[invalid")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "list"])

    assert r.exit_code == EXIT_NO_CONFIG, r.output
    combined = r.output + (r.stderr or "")
    assert "corrupt" in combined or "unreadable" in combined
    # No raw traceback
    assert "Traceback" not in combined


def test_anchor_list_wrong_shape_index_exits_3(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    idx = anchors_index_path(tmp_path)
    idx.parent.mkdir(parents=True, exist_ok=True)
    # Valid YAML but a top-level list, not a mapping.
    idx.write_text("- a\n- b\n")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "list"])

    assert r.exit_code == EXIT_NO_CONFIG, r.output
    combined = r.output + (r.stderr or "")
    assert "unexpected shape" in combined
    # Must be a clean SystemExit, not an AttributeError
    assert isinstance(r.exception, SystemExit), (
        f"Expected SystemExit, got {type(r.exception)}: {r.exception}"
    )
    assert "Traceback" not in combined


def test_anchor_list_malformed_location_rows_exits_3(tmp_path: Path) -> None:
    # Valid YAML mapping, `anchors` is a mapping, but an anchor's value is not a
    # list of {file, line} mappings — must NOT raw-traceback (the inner error-family
    # gap): a non-dict row and a non-list value both route to exit 3.
    for body in ("anchors:\n  cap-x:\n  - just-a-string\n", "anchors:\n  cap-x: 42\n"):
        _init_workspace(tmp_path)
        idx = anchors_index_path(tmp_path)
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(body)

        r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "list"])

        assert r.exit_code == EXIT_NO_CONFIG, r.output
        combined = r.output + (r.stderr or "")
        assert "malformed anchor locations" in combined
        assert isinstance(r.exception, SystemExit), (
            f"Expected SystemExit, got {type(r.exception)}: {r.exception}"
        )
        assert "Traceback" not in combined


def test_anchor_list_non_utf8_index_exits_3(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    idx = anchors_index_path(tmp_path)
    idx.parent.mkdir(parents=True, exist_ok=True)
    # Write raw bytes that are invalid UTF-8.
    idx.write_bytes(b"\xff\xfe INVALID UTF-8 \x80\x81")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "list"])

    assert r.exit_code == EXIT_NO_CONFIG, r.output
    combined = r.output + (r.stderr or "")
    assert "corrupt" in combined or "unreadable" in combined
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# anchor list --missing-sentinel
# ---------------------------------------------------------------------------


def test_anchor_list_missing_sentinel_reports_absent_anchor(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    # Declare "cap-declared" in the change but don't plant any sentinel for it.
    _emit_plan_ready(tmp_path, "my-change", affected_anchors=["cap-declared"])
    # Plant a *different* sentinel that IS in the index.
    _plant_sentinel(tmp_path, "src/other.py", "cap-other", line=1)
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "anchor", "list", "--missing-sentinel"]
    )

    assert r.exit_code == EXIT_OK, r.output
    assert "cap-declared" in r.output
    # "cap-other" is NOT declared for the change (only present in the index), so the
    # declared-but-absent report must not mention it at all.
    assert "cap-other" not in r.output


def test_anchor_list_missing_sentinel_all_present(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    _emit_plan_ready(tmp_path, "my-change", affected_anchors=["cap-present"])
    _plant_sentinel(tmp_path, "src/foo.py", "cap-present", line=1)
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "anchor", "list", "--missing-sentinel"]
    )

    assert r.exit_code == EXIT_OK, r.output
    # All declared anchors present → "no missing" message
    assert "cap-present" in r.output or "all present" in r.output


def test_anchor_list_missing_sentinel_no_active_change(tmp_path: Path) -> None:
    _init_workspace(tmp_path)
    # Sync produces empty index; no events at all → no active change.
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "sync"])

    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "anchor", "list", "--missing-sentinel"]
    )

    assert r.exit_code == EXIT_OK, r.output
    assert "no active change" in r.output.lower() or "No active change" in r.output


# ---------------------------------------------------------------------------
# anchor list — no .harness/
# ---------------------------------------------------------------------------


def test_anchor_list_no_harness_exits_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "anchor", "list"])
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "No .harness/" in combined
