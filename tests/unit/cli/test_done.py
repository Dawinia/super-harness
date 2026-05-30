"""Unit tests for `super-harness done` (Phase 8 Task 8.7).

`done` runs verification (via the same dispatcher + VerificationRunner as
`verify`) and, on a pass, emits `implementation_complete` to advance the change
IMPLEMENTATION_IN_PROGRESS → AWAITING_CODE_REVIEW. `--skip-verify` emits a
synthetic verification_passed first.

Coverage:
  1. happy path: in-progress change + passing config → exit 0; events end
     ...verification_passed THEN implementation_complete; state AWAITING_CODE_REVIEW.
  2. failing verification → exit 2, NO implementation_complete.
  3. --skip-verify → synthetic verification_passed{skipped:true} then
     implementation_complete, exit 0.
  4. wrong state (not IMPLEMENTATION_IN_PROGRESS) → EmitPreconditionError → exit 2.
  5. no `.harness/` → exit 3.
  6. --pr value lands on the implementation_complete payload as pr_url.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path, state_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter

_PASS_YAML = """\
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
  - id: ok-1
    command: "true"
adapter_provided: []
"""

_FAIL_YAML = """\
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
  - id: boom
    command: "false"
adapter_provided: []
"""


def _emit(ws: Path, slug: str, evt_type: str) -> None:
    EventWriter(events_path(ws)).emit(
        Event(
            event_id=new_event_id(),
            type=evt_type,
            change_id=slug,
            timestamp="2026-05-29T00:00:00Z",
            actor=Actor(type="human", identifier="cli"),
            framework="plain",
            payload={},
        )
    )


def _drive_to_in_progress(ws: Path, slug: str) -> None:
    for evt_type in (
        "intent_declared",
        "plan_ready",
        "plan_approved",
        "implementation_started",
    ):
        _emit(ws, slug, evt_type)
    refresh_state_after_emit(ws)


def _init_in_progress(ws: Path, *, yaml_text: str, slug: str = "my-change") -> None:
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    (ws / ".harness" / "verification.yaml").write_text(yaml_text)
    _drive_to_in_progress(ws, slug)


def _event_types(ws: Path) -> list[str]:
    return [
        json.loads(line)["type"]
        for line in events_path(ws).read_text().splitlines()
        if line.strip()
    ]


def _state_of(ws: Path, slug: str) -> str:
    import yaml

    data = yaml.safe_load(state_path(ws).read_text())
    return data["changes"][slug]["current_state"]


def test_done_happy_path(tmp_path: Path) -> None:
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_OK, r.output
    types = _event_types(tmp_path)
    # The last two events are verification_passed THEN implementation_complete.
    assert types[-2:] == ["verification_passed", "implementation_complete"]
    assert _state_of(tmp_path, "my-change") == "AWAITING_CODE_REVIEW"


def test_done_failing_verification_no_complete(tmp_path: Path) -> None:
    _init_in_progress(tmp_path, yaml_text=_FAIL_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    types = _event_types(tmp_path)
    assert "implementation_complete" not in types
    assert types[-1] == "verification_failed"
    # State stays put — implementation_complete never advanced it.
    assert _state_of(tmp_path, "my-change") == "IMPLEMENTATION_IN_PROGRESS"


def test_done_skip_verify(tmp_path: Path) -> None:
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change", "--skip-verify"]
    )
    assert r.exit_code == EXIT_OK, r.output
    types = _event_types(tmp_path)
    assert types[-2:] == ["verification_passed", "implementation_complete"]
    # The synthetic verification_passed carries the skip marker.
    lines = events_path(tmp_path).read_text().splitlines()
    vp = next(
        json.loads(ln)
        for ln in reversed(lines)
        if ln.strip() and json.loads(ln)["type"] == "verification_passed"
    )
    assert vp["payload"] == {"skipped": True, "reason": "--skip-verify"}
    assert _state_of(tmp_path, "my-change") == "AWAITING_CODE_REVIEW"


def test_done_wrong_state_exit_2(tmp_path: Path) -> None:
    # Change only at INTENT_DECLARED — the pre-flight state gate rejects `done`
    # BEFORE running verification or emitting anything, so NO orphan
    # verification_passed lands (the stream stays exactly [intent_declared]).
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "verification.yaml").write_text(_PASS_YAML)
    _emit(tmp_path, "my-change", "intent_declared")
    refresh_state_after_emit(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "not IMPLEMENTATION_IN_PROGRESS" in r.output
    # The gate wrote nothing new — no orphan verification_passed, no complete.
    assert _event_types(tmp_path) == ["intent_declared"]


def test_done_skip_verify_wrong_state_exit_2(tmp_path: Path) -> None:
    # --skip-verify on a change with NO prior state ("ghost"): the pre-flight
    # state gate rejects it (current state is None) before any emit — so no
    # synthetic verification_passed is even attempted.
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "verification.yaml").write_text(_PASS_YAML)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "done", "ghost", "--skip-verify"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "not IMPLEMENTATION_IN_PROGRESS" in r.output
    # Nothing landed (gated before the writer was even constructed).
    assert not events_path(tmp_path).exists() or _event_types(tmp_path) == []


def test_done_too_early_plan_approved_no_orphan_vp(tmp_path: Path) -> None:
    # Reviewer's reproduction: `done` on a PLAN_APPROVED change. There,
    # verification_passed is a LEGAL self-loop, so pre-fix it would land as an
    # orphan before implementation_complete was rejected. The pre-flight gate
    # blocks it: exit 2, stream unchanged (no orphan verification_passed).
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "verification.yaml").write_text(_PASS_YAML)
    for evt in ("intent_declared", "plan_ready", "plan_approved"):
        _emit(tmp_path, "my-change", evt)
    refresh_state_after_emit(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "verification_passed" not in _event_types(tmp_path)
    assert _event_types(tmp_path) == [
        "intent_declared",
        "plan_ready",
        "plan_approved",
    ]


def test_done_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "No .harness/" in combined


def test_done_pr_recorded_on_payload(tmp_path: Path) -> None:
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "done", "my-change", "--pr", "42"],
    )
    assert r.exit_code == EXIT_OK, r.output
    lines = events_path(tmp_path).read_text().splitlines()
    ic = next(
        json.loads(ln)
        for ln in reversed(lines)
        if ln.strip() and json.loads(ln)["type"] == "implementation_complete"
    )
    assert ic["payload"] == {"pr_url": "42"}


def test_done_resolves_active_change(tmp_path: Path) -> None:
    # No explicit slug → resolve the first non-terminal change.
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML, slug="active-one")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "done"])
    assert r.exit_code == EXIT_OK, r.output
    assert _event_types(tmp_path)[-2:] == [
        "verification_passed",
        "implementation_complete",
    ]


def test_done_json_success_envelope(tmp_path: Path) -> None:
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "done", "my-change"]
    )
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.output)
    assert payload["command"] == "done"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == EXIT_OK
    # Default path success envelope lifts the verification data block.
    assert payload["data"]["change_id"] == "my-change"


# --------------------------------------------------------------------------- #
# FIX 1 — config / placeholder errors on the DEFAULT path surface as
# EXIT_VALIDATION (2), not a swallowed sensor crash → EXIT_GENERIC (1).
# `--skip-verify` never loads config, so an invalid/absent config is irrelevant.
# --------------------------------------------------------------------------- #

_CORRUPT_YAML = "layers: {baseline: {enabled: true}\nchecks: [\n"

_BAD_ENUM_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  capture: everything
checks: []
adapter_provided: []
"""

_BAD_PLACEHOLDER_YAML = """\
layers:
  baseline: { enabled: false }
  framework_adapter: { enabled: false }
  user_checks: { enabled: true }
defaults:
  capture: none
checks:
  - id: deploy
    command: "deploy ${PR_URL}"
adapter_provided: []
"""


def _init_in_progress_raw(ws: Path, *, yaml_text: str, slug: str = "my-change") -> None:
    """Drive a change to IMPLEMENTATION_IN_PROGRESS with a raw (possibly invalid)
    verification.yaml that we do NOT route through the schema loader."""
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    (ws / ".harness" / "verification.yaml").write_text(yaml_text)
    _drive_to_in_progress(ws, slug)


def test_done_corrupt_yaml_exit_2(tmp_path: Path) -> None:
    _init_in_progress_raw(tmp_path, yaml_text=_CORRUPT_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "not valid YAML" in combined
    assert "Traceback" not in combined
    # The config error blocks BEFORE any implementation_complete is emitted.
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_wrong_shape_exit_2(tmp_path: Path) -> None:
    _init_in_progress_raw(tmp_path, yaml_text=_BAD_ENUM_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_bad_placeholder_exit_2(tmp_path: Path) -> None:
    _init_in_progress_raw(tmp_path, yaml_text=_BAD_PLACEHOLDER_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "PR_URL" in combined
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_skip_verify_ignores_invalid_config(tmp_path: Path) -> None:
    # --skip-verify never loads verification.yaml: a corrupt config must NOT block
    # it. The change is in-progress, so done --skip-verify completes (exit 0).
    _init_in_progress_raw(tmp_path, yaml_text=_CORRUPT_YAML)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change", "--skip-verify"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _event_types(tmp_path)[-2:] == [
        "verification_passed",
        "implementation_complete",
    ]


def test_done_skip_verify_ignores_missing_config(tmp_path: Path) -> None:
    # --skip-verify with NO verification.yaml at all → still completes.
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    _drive_to_in_progress(tmp_path, "my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change", "--skip-verify"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _event_types(tmp_path)[-1] == "implementation_complete"


# --------------------------------------------------------------------------- #
# Task 14.3 — --pr slug-from-PR resolution
#
# Mirrors test_verify.py's matrix. See that file's section header for the
# resolution order, exit-code matrix, and divergence-from-`pr validate`
# rationale. `done`'s extra wrinkle: when --pr resolves the slug, the same
# --pr value is still recorded on the implementation_complete payload as
# pr_url (the pr value drives BOTH slug resolution AND payload recording).
#
# gh is mocked at `super_harness.cli.done.gh.view_pr` (the import site
# inside done.py — Phase 12 pattern).
# --------------------------------------------------------------------------- #

VIEW_PR_DONE = "super_harness.cli.done.gh.view_pr"


def _metadata_body_done(change: str = "my-change") -> str:
    return (
        "Some PR description.\n\n"
        "<!-- super-harness:metadata -->\n"
        f"Change: {change}\n"
        "Tier: Normal\n"
        "Verification: passed\n"
        "super-harness version: 0.1.0\n"
        "<!-- /super-harness:metadata -->\n"
    )


def test_done_positional_only_does_not_call_gh(tmp_path: Path) -> None:
    """Positional slug + no --pr → gh.view_pr is NOT invoked (regression guard)."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    with mock.patch(VIEW_PR_DONE) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "my-change"]
        )
    assert r.exit_code == EXIT_OK, r.output
    m.assert_not_called()


def test_done_pr_resolves_slug_and_runs(tmp_path: Path) -> None:
    """--pr only, well-formed block → resolves Change → verifies → completes."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    with mock.patch(
        VIEW_PR_DONE, return_value={"body": _metadata_body_done("my-change")}
    ) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_OK, r.output
    m.assert_called_once()
    types = _event_types(tmp_path)
    assert types[-2:] == ["verification_passed", "implementation_complete"]


def test_done_pr_no_metadata_block_exits_4(tmp_path: Path) -> None:
    """--pr but the PR body has no metadata block → exit 4 (precondition)."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    with mock.patch(
        VIEW_PR_DONE, return_value={"body": "just a normal PR description"}
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_EXTERNAL_TOOL, r.output
    combined = r.output + (r.stderr or "")
    assert "no super-harness metadata block" in combined
    assert "pr emit-opened" in combined
    # No implementation_complete landed.
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_pr_multiple_blocks_exits_2(tmp_path: Path) -> None:
    """--pr with ≥2 metadata blocks → AC-3 violation → exit 2."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    body = _metadata_body_done() + "\n" + _metadata_body_done()
    with mock.patch(VIEW_PR_DONE, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "AC-3" in combined or "metadata blocks" in combined
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_pr_malformed_block_exits_2(tmp_path: Path) -> None:
    """--pr with an unclosed begin marker → structural error → exit 2."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    body = (
        "Some PR description.\n\n"
        "<!-- super-harness:metadata -->\n"
        "Change: my-change\n"
        # NO end marker.
    )
    with mock.patch(VIEW_PR_DONE, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "malformed" in combined
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_pr_gh_error_exits_4(tmp_path: Path) -> None:
    """--pr + gh.GhError → exit 4 (EXIT_EXTERNAL_TOOL)."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    from super_harness.engineering import gh

    with mock.patch(
        VIEW_PR_DONE, side_effect=gh.GhError("gh pr view 42 failed (exit 1)")
    ):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_EXTERNAL_TOOL, r.output
    combined = r.output + (r.stderr or "")
    assert "could not fetch PR" in combined
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_positional_wins_over_pr(tmp_path: Path) -> None:
    """Positional + --pr both → positional wins; gh.view_pr is NOT called."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    with mock.patch(VIEW_PR_DONE) as m:
        r = CliRunner().invoke(
            main,
            ["--workspace", str(tmp_path), "done", "my-change", "--pr", "42"],
        )
    assert r.exit_code == EXIT_OK, r.output
    m.assert_not_called()


def test_done_pr_block_missing_change_field_exits_4(tmp_path: Path) -> None:
    """Block present + single, but missing Change field → exit 4."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    body = (
        "<!-- super-harness:metadata -->\n"
        "Tier: Normal\n"
        "Verification: passed\n"
        "<!-- /super-harness:metadata -->\n"
    )
    with mock.patch(VIEW_PR_DONE, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_EXTERNAL_TOOL, r.output
    combined = r.output + (r.stderr or "")
    assert "Change" in combined
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_pr_invalid_slug_format_exits_2(tmp_path: Path) -> None:
    """Block + Change with invalid slug (slashes) → A6 gate → exit 2."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    body = (
        "<!-- super-harness:metadata -->\n"
        "Change: feature/foo\n"
        "Tier: Normal\n"
        "Verification: passed\n"
        "<!-- /super-harness:metadata -->\n"
    )
    with mock.patch(VIEW_PR_DONE, return_value={"body": body}):
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    combined = r.output + (r.stderr or "")
    assert "feature/foo" in combined
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_pr_non_integer_exits_2(tmp_path: Path) -> None:
    """--pr value is not an integer → clean EXIT_VALIDATION (no traceback)."""
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    with mock.patch(VIEW_PR_DONE) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "not-a-number"]
        )
    assert r.exit_code == EXIT_VALIDATION, r.output
    m.assert_not_called()
    combined = r.output + (r.stderr or "")
    assert "Traceback" not in combined


def test_done_pr_resolution_records_pr_in_payload(tmp_path: Path) -> None:
    """When --pr resolves the slug, the same --pr value is recorded as pr_url.

    Confirms the dual role of --pr in `done`: it drives slug resolution AND
    its raw value continues to flow into implementation_complete's pr_url
    payload (unchanged from pre-14.3 behavior). Assertions:
      1. gh.view_pr WAS called (proves slug resolution actually went through
         the --pr code path — NOT a false-pass on active-change fallback).
      2. implementation_complete's change_id == resolved slug.
      3. implementation_complete's payload.pr_url == raw --pr value (string).
    """
    _init_in_progress(tmp_path, yaml_text=_PASS_YAML)
    with mock.patch(
        VIEW_PR_DONE, return_value={"body": _metadata_body_done("my-change")}
    ) as m:
        r = CliRunner().invoke(
            main, ["--workspace", str(tmp_path), "done", "--pr", "42"]
        )
    assert r.exit_code == EXIT_OK, r.output
    # 1. The --pr resolution code path actually ran (vs. fallback to
    #    active-change, which would silently produce the same "my-change"
    #    slug and look like a pass without exercising the wiring).
    m.assert_called_once()
    lines = events_path(tmp_path).read_text().splitlines()
    ic = next(
        json.loads(ln)
        for ln in reversed(lines)
        if ln.strip() and json.loads(ln)["type"] == "implementation_complete"
    )
    # 2. The resolved slug came from the --pr-fetched PR body.
    assert ic["change_id"] == "my-change"
    # 3. The original --pr value ALSO landed on the payload as pr_url.
    assert ic["payload"] == {"pr_url": "42"}
