"""Unit tests for `super-harness verify` (Phase 8 Task 8.7).

`verify` drives the builtin VerificationRunner sensor through a one-shot
SensorDispatcher and reports its verdict. These tests stand up a real
`.harness/` with a hand-written `verification.yaml` and invoke `verify` via
CliRunner (the Phase-2 CLI test pattern).

Coverage:
  1. all-pass → exit 0 + a verification_passed event lands on disk
  2. must_pass fail (a `false` check) → exit 2 + verification_failed event
  3. --json → envelope.data == result.details (frozen verify_data_block keys)
  4. no `.harness/` → exit 3 (EXIT_NO_CONFIG)
  5. --layer / --check thread into the activity payload (here: --check selects)
  6. no slug + no active change → exit 2 (EXIT_VALIDATION)
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
from super_harness.core.paths import events_path
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter

# A verification.yaml with baseline disabled (keeps fixtures simple — no
# anchor/lifecycle/scope baselines to satisfy) and sequential execution so the
# `false` must_pass check is the deterministic verdict driver.
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
  - id: ok-2
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
  - id: ok-1
    command: "true"
  - id: boom
    command: "false"
adapter_provided: []
"""


def _init_workspace(ws: Path, *, yaml_text: str, slug: str | None = None) -> None:
    """Create `<ws>/.harness/` with verification.yaml and (optionally) a change.

    When `slug` is given, drive it through the lifecycle to
    IMPLEMENTATION_IN_PROGRESS so its events.jsonl is a realistic stream the
    sensor's baselines (if enabled) could read; for verify the change just needs
    to exist so read_active_change_id resolves it when no explicit slug is passed.
    """
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    (ws / ".harness" / "verification.yaml").write_text(yaml_text)
    if slug is not None:
        _drive_to_in_progress(ws, slug)


def _drive_to_in_progress(ws: Path, slug: str) -> None:
    """Emit intent_declared → plan_ready → plan_approved → implementation_started."""
    writer = EventWriter(events_path(ws))
    for evt_type in (
        "intent_declared",
        "plan_ready",
        "plan_approved",
        "implementation_started",
    ):
        writer.emit(
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
    refresh_state_after_emit(ws)


def _read_event_types(ws: Path) -> list[str]:
    lines = events_path(ws).read_text().splitlines()
    return [json.loads(line)["type"] for line in lines if line.strip()]


def test_verify_all_pass_exit_0_and_event(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_OK, r.output
    assert _read_event_types(tmp_path)[-1] == "verification_passed"


def test_verify_must_pass_fail_exit_2(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert _read_event_types(tmp_path)[-1] == "verification_failed"


def test_verify_json_data_is_result_details(tmp_path: Path) -> None:
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "verify", "my-change"]
    )
    assert r.exit_code == EXIT_OK, r.output
    payload = json.loads(r.output)
    assert payload["command"] == "verify"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == EXIT_OK
    data = payload["data"]
    # Frozen verify_data_block keys (cli-command-surface §3.4).
    assert set(data) == {
        "change_id",
        "all_pass_must",
        "checks_run",
        "results",
        "summary_path",
    }
    assert data["change_id"] == "my-change"
    assert data["all_pass_must"] is True
    assert data["checks_run"] == 2
    # Per-result row shape (no `command` key in the data block — that lives in
    # summary.json only).
    assert set(data["results"][0]) == {
        "id",
        "status",
        "exit_code",
        "duration_ms",
        "must_pass",
        "output_path",
    }


def test_verify_no_harness_exit_3(tmp_path: Path) -> None:
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "No .harness/" in combined


def test_verify_missing_config_exit_3(tmp_path: Path) -> None:
    # `.harness/` exists but verification.yaml absent → EXIT_NO_CONFIG.
    (tmp_path / ".harness").mkdir()
    _drive_to_in_progress(tmp_path, "my-change")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "verify", "my-change"]
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "verification config not found" in combined


def test_verify_check_filter_selects_subset(tmp_path: Path) -> None:
    # --check selects only `boom`; with `ok-1` filtered out the run is the single
    # failing must_pass check → fail, and checks_run == 1 proves the filter threaded
    # through the activity payload into collect_checks.
    _init_workspace(tmp_path, yaml_text=_FAIL_YAML, slug="my-change")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "verify", "my-change",
         "--check", "boom"],
    )
    assert r.exit_code == EXIT_VALIDATION, r.output
    data = json.loads(r.output)["data"]
    assert data["checks_run"] == 1
    assert data["results"][0]["id"] == "boom"


def test_verify_layer_filter_threaded(tmp_path: Path) -> None:
    # --layer user keeps the user_checks layer (both pass) → exit 0 with both
    # checks run. (Proves --layer reaches collect_checks; baseline is disabled
    # anyway so this isolates the user layer.)
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="my-change")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "verify", "my-change",
         "--layer", "user"],
    )
    assert r.exit_code == EXIT_OK, r.output
    assert json.loads(r.output)["data"]["checks_run"] == 2


def test_verify_no_slug_no_active_exit_2(tmp_path: Path) -> None:
    # `.harness/` + verification.yaml present, but NO change exists → no slug
    # and no active change → EXIT_VALIDATION.
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug=None)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "verify"])
    assert r.exit_code == EXIT_VALIDATION
    combined = r.output + (r.stderr or "")
    assert "no change specified" in combined


def test_verify_resolves_active_change(tmp_path: Path) -> None:
    # No explicit slug → resolve the first non-terminal change from state.yaml.
    _init_workspace(tmp_path, yaml_text=_PASS_YAML, slug="active-one")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "verify"])
    assert r.exit_code == EXIT_OK, r.output
    assert _read_event_types(tmp_path)[-1] == "verification_passed"
