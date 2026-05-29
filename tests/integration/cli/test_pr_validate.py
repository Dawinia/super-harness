"""Integration tests for `super-harness pr validate <num>` (Phase 12 Task 12.3b).

`pr validate` fetches a PR body via `gh pr view`, parses the super-harness
metadata block, and runs three blocker checks:
  1. metadata block present + complete (all REQUIRED_METADATA_KEYS)
  2. the resolved change's lifecycle event sequence is violation-free
  3. the change is in READY_TO_MERGE

gh is ALWAYS mocked here — never the real binary, never the network. We patch
`super_harness.cli.pr.gh.view_pr` directly.

Exit codes (cli-command-surface §`pr validate`):
- 0 — no blockers
- 2 — one or more blockers (EXIT_VALIDATION)
- 3 — no `.harness/` (EXIT_NO_CONFIG; reads events.jsonl for the lifecycle checks)
- 4 — gh failure (EXIT_EXTERNAL_TOOL)

The exit-3 / exit-4 ("couldn't run") branches print `format_error` to stderr and
emit NO json envelope, even under `--json` (mirrors verify's HarnessNotInitialized
path).

Coverage map:
- test_pr_validate_no_harness_exits_3               — exit 3, format_error, no envelope (--json)
- test_pr_validate_gh_error_exits_4                 — gh.GhError -> exit 4, no envelope
- test_pr_validate_no_metadata_block_exits_2        — no block blocker
- test_pr_validate_null_body_exits_2                — gh returns {"body": null} -> no-block
- test_pr_validate_empty_body_exits_2               — empty body -> no-block blocker
- test_pr_validate_multiple_blocks_exits_2          — block_count >= 2 (AC-3 violation)
- test_pr_validate_missing_required_keys_exits_2    — incomplete fields
- test_pr_validate_complete_block_not_ready_exits_2 — valid block but not READY_TO_MERGE
- test_pr_validate_lifecycle_violation_exits_2      — valid block + ordering violation
- test_pr_validate_all_pass_exits_0                 — clean READY_TO_MERGE + complete block
- test_pr_validate_json_envelope_pass               — frozen envelope + data sub-schema (pass)
- test_pr_validate_json_envelope_fail               — envelope + errors[] (fail)
- test_resolve_change_from_pr_returns_change        — helper returns the Change field
- test_resolve_change_from_pr_no_block_returns_none — helper returns None with no block
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.pr import resolve_change_from_pr
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter

# A change_id used across the seeding helpers / mocked metadata bodies.
CHANGE_ID = "2026-05-30-add-foo"

# Path of the gh wrapper as imported into cli/pr.py — patch THIS, never real gh.
VIEW_PR = "super_harness.cli.pr.gh.view_pr"


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


def _init(tmp_path: Path) -> None:
    """Create `.harness/` (via `init`) so find_harness_root succeeds."""
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])


def _evt(change_id: str, evt_type: str) -> Event:
    return Event(
        event_id=new_event_id(),
        type=evt_type,
        change_id=change_id,
        timestamp="2026-05-30T10:00:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework="plain",
        payload={"description": "x"},
    )


def _seed(root: Path, change_id: str, types: list[str], *, skip: bool = False) -> None:
    """Append a stream to `.harness/events.jsonl`.

    `skip=True` bypasses emit-time validation so an illegal sequence can land
    on disk (used to exercise the lifecycle-violation blocker).
    """
    w = EventWriter(events_path(root))
    for t in types:
        w.emit(_evt(change_id, t), skip_validation=skip)


# The happy-path event sequence that reaches READY_TO_MERGE (transitions.py):
# intent_declared → plan_ready → plan_approved → implementation_started →
# verification_passed (informational; satisfies implementation_complete prereq)
# → implementation_complete → code_review_passed → READY_TO_MERGE.
_READY_SEQUENCE = [
    "intent_declared",
    "plan_ready",
    "plan_approved",
    "implementation_started",
    "verification_passed",
    "implementation_complete",
    "code_review_passed",
]


def _complete_metadata_body(change_id: str = CHANGE_ID) -> str:
    """A PR body carrying ONE complete super-harness metadata block."""
    return (
        "Some PR description.\n\n"
        "<!-- super-harness:metadata -->\n"
        f"Change: {change_id}\n"
        "Tier: Normal\n"
        "Verification: passed\n"
        "super-harness version: 0.1.0\n"
        "<!-- /super-harness:metadata -->\n"
    )


# --------------------------------------------------------------------------- #
# exit 3 — no .harness/
# --------------------------------------------------------------------------- #


def test_pr_validate_no_harness_exits_3(tmp_path: Path) -> None:
    """No `.harness/` → HarnessNotInitialized → exit 3, format_error, NO envelope.

    Even with --json the "couldn't run" branch prints format_error to stderr and
    emits no envelope (mirrors verify's HarnessNotInitialized path). gh must not
    even be called — assert the mock stayed untouched.
    """
    with mock.patch(VIEW_PR) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "--json", "pr", "validate", "7"]
        )
    assert r.exit_code == 3
    assert "super-harness pr validate:" in r.stderr
    assert "Hint:" in r.stderr
    # No envelope emitted on stdout even under --json.
    assert r.stdout.strip() == ""
    m.assert_not_called()


# --------------------------------------------------------------------------- #
# exit 4 — gh failure
# --------------------------------------------------------------------------- #


def test_pr_validate_gh_error_exits_4(tmp_path: Path) -> None:
    """gh.GhError from view_pr → exit 4 (EXIT_EXTERNAL_TOOL), no envelope."""
    _init(tmp_path)
    from super_harness.engineering import gh

    with mock.patch(VIEW_PR, side_effect=gh.GhError("gh pr view 7 failed (exit 1)")):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "--json", "pr", "validate", "7"]
        )
    assert r.exit_code == 4
    assert "super-harness pr validate:" in r.stderr
    assert r.stdout.strip() == ""


# --------------------------------------------------------------------------- #
# exit 2 — metadata-block blockers
# --------------------------------------------------------------------------- #


def test_pr_validate_no_metadata_block_exits_2(tmp_path: Path) -> None:
    """A PR body with no super-harness block → no-block blocker → exit 2."""
    _init(tmp_path)
    with mock.patch(VIEW_PR, return_value={"body": "just a normal PR description"}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 2
    assert "no super-harness metadata block" in r.stderr


def test_pr_validate_null_body_exits_2(tmp_path: Path) -> None:
    """gh returns {"body": null} → `or ""` makes it the no-block blocker, not a crash."""
    _init(tmp_path)
    with mock.patch(VIEW_PR, return_value={"body": None}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 2
    assert "no super-harness metadata block" in r.stderr


def test_pr_validate_empty_body_exits_2(tmp_path: Path) -> None:
    """Empty-string body → no-block blocker → exit 2."""
    _init(tmp_path)
    with mock.patch(VIEW_PR, return_value={"body": ""}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 2
    assert "no super-harness metadata block" in r.stderr


def test_pr_validate_multiple_blocks_exits_2(tmp_path: Path) -> None:
    """Two metadata blocks → AC-3 violation blocker → exit 2."""
    _init(tmp_path)
    body = _complete_metadata_body() + "\n" + _complete_metadata_body()
    with mock.patch(VIEW_PR, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 2
    assert "multiple metadata blocks" in r.stderr


def test_pr_validate_missing_required_keys_exits_2(tmp_path: Path) -> None:
    """A present block missing required keys → incomplete-fields blocker → exit 2."""
    _init(tmp_path)
    body = (
        "<!-- super-harness:metadata -->\n"
        f"Change: {CHANGE_ID}\n"
        "Tier: Normal\n"
        "<!-- /super-harness:metadata -->\n"
    )
    with mock.patch(VIEW_PR, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 2
    assert "missing required keys" in r.stderr
    # The two missing required keys are named in the blocker text.
    assert "Verification" in r.stderr
    assert "super-harness version" in r.stderr


# --------------------------------------------------------------------------- #
# exit 2 — lifecycle blockers (block is complete, but state / sequence fails)
# --------------------------------------------------------------------------- #


def test_pr_validate_complete_block_not_ready_exits_2(tmp_path: Path) -> None:
    """Complete block but the change is NOT READY_TO_MERGE → merge-ready blocker."""
    _init(tmp_path)
    # Seed only up to IMPLEMENTATION_IN_PROGRESS — clean sequence, not yet ready.
    _seed(
        tmp_path,
        CHANGE_ID,
        ["intent_declared", "plan_ready", "plan_approved", "implementation_started"],
    )
    with mock.patch(VIEW_PR, return_value={"body": _complete_metadata_body()}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 2
    assert "not READY_TO_MERGE" in r.stderr
    # The lifecycle sequence itself is clean, so only the merge-ready blocker fires.
    assert "lifecycle sequence invalid" not in r.stderr


def test_pr_validate_lifecycle_violation_exits_2(tmp_path: Path) -> None:
    """Complete block + a hand-edited (illegal) event sequence → sequence blocker."""
    _init(tmp_path)
    # Illegal: plan_ready as the FIRST event (no intent_declared). skip=True so the
    # bad stream lands on disk; find_ordering_violations then flags it.
    _seed(tmp_path, CHANGE_ID, ["plan_ready", "plan_approved"], skip=True)
    with mock.patch(VIEW_PR, return_value={"body": _complete_metadata_body()}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 2
    assert "lifecycle sequence invalid" in r.stderr


# --------------------------------------------------------------------------- #
# exit 0 — all-pass
# --------------------------------------------------------------------------- #


def test_pr_validate_all_pass_exits_0(tmp_path: Path) -> None:
    """Clean READY_TO_MERGE stream + complete block whose Change matches → exit 0."""
    _init(tmp_path)
    _seed(tmp_path, CHANGE_ID, _READY_SEQUENCE)
    with mock.patch(VIEW_PR, return_value={"body": _complete_metadata_body()}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "pr", "validate", "7"]
        )
    assert r.exit_code == 0, r.output + r.stderr
    assert CHANGE_ID in r.output
    assert r.stderr.strip() == ""


# --------------------------------------------------------------------------- #
# --json envelope shape on both pass and fail
# --------------------------------------------------------------------------- #


def test_pr_validate_json_envelope_pass(tmp_path: Path) -> None:
    """--json on a pass: frozen 6-key envelope + the data sub-schema field-for-field."""
    _init(tmp_path)
    _seed(tmp_path, CHANGE_ID, _READY_SEQUENCE)
    with mock.patch(VIEW_PR, return_value={"body": _complete_metadata_body()}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "--json", "pr", "validate", "42"]
        )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    # Frozen 6-key envelope.
    assert set(payload.keys()) == {
        "command",
        "version",
        "status",
        "exit_code",
        "data",
        "errors",
    }
    assert payload["command"] == "pr validate"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == 0
    assert payload["errors"] == []
    # data sub-schema field-for-field (cli-surface §`pr validate` data).
    data = payload["data"]
    assert set(data.keys()) == {
        "pr_number",
        "change_id",
        "metadata_block",
        "lifecycle_check",
        "blockers",
    }
    assert data["pr_number"] == 42
    assert data["change_id"] == CHANGE_ID
    assert data["metadata_block"] == {"present": True, "fields_complete": True}
    assert data["lifecycle_check"] == {"valid_sequence": True, "merge_ready": True}
    assert data["blockers"] == []


def test_pr_validate_json_envelope_fail(tmp_path: Path) -> None:
    """--json on a fail: status=fail, exit_code=2, blockers surfaced in data + errors[]."""
    _init(tmp_path)
    # Complete block but no events for the change → not READY_TO_MERGE.
    with mock.patch(VIEW_PR, return_value={"body": _complete_metadata_body()}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "--json", "pr", "validate", "9"]
        )
    assert r.exit_code == 2
    payload = json.loads(r.output)
    assert set(payload.keys()) == {
        "command",
        "version",
        "status",
        "exit_code",
        "data",
        "errors",
    }
    assert payload["command"] == "pr validate"
    assert payload["status"] == "fail"
    assert payload["exit_code"] == 2
    data = payload["data"]
    assert data["pr_number"] == 9
    assert data["change_id"] == CHANGE_ID
    assert data["metadata_block"] == {"present": True, "fields_complete": True}
    assert data["lifecycle_check"]["merge_ready"] is False
    assert data["blockers"]  # non-empty
    # errors[] mirrors blockers, each {code: "validation", message: <blocker>}.
    assert payload["errors"]
    for err in payload["errors"]:
        assert err["code"] == "validation"
        assert err["message"] in data["blockers"]


# --------------------------------------------------------------------------- #
# resolve_change_from_pr helper (Fork C) — unit
# --------------------------------------------------------------------------- #


def test_resolve_change_from_pr_returns_change() -> None:
    """The helper returns the block's Change field when a complete block exists."""
    with mock.patch(VIEW_PR, return_value={"body": _complete_metadata_body()}):
        assert resolve_change_from_pr(7) == CHANGE_ID


def test_resolve_change_from_pr_no_block_returns_none() -> None:
    """No metadata block (incl. null body) → helper returns None."""
    with mock.patch(VIEW_PR, return_value={"body": None}):
        assert resolve_change_from_pr(7) is None
    with mock.patch(VIEW_PR, return_value={"body": "no block here"}):
        assert resolve_change_from_pr(7) is None
