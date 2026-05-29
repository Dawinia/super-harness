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

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.exit_codes import (
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
    # Change only at INTENT_DECLARED — implementation_complete is illegal there,
    # and (default path) verification_passed is informational so it lands, but
    # implementation_complete's transition is rejected → EmitPreconditionError.
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "verification.yaml").write_text(_PASS_YAML)
    _emit(tmp_path, "my-change", "intent_declared")
    refresh_state_after_emit(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "done", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "implementation_complete" not in _event_types(tmp_path)


def test_done_skip_verify_wrong_state_exit_2(tmp_path: Path) -> None:
    # --skip-verify on a change with NO prior state: the synthetic
    # verification_passed is illegal as a first event → EmitPreconditionError.
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".harness" / "verification.yaml").write_text(_PASS_YAML)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "done", "ghost", "--skip-verify"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    # Nothing landed (the very first emit was rejected).
    assert not events_path(tmp_path).exists() or _event_types(tmp_path) == []


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
