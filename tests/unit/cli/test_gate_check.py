"""Unit tests for `super-harness gate check` (Phase 5 Task 5.2).

`gate check pre-tool-use` is the manual/CI/debug entry to the pre-tool-use gate
decision. Per the ratified `cli-command-surface` §gate-check it decides
**in-process** ("读 state.yaml + 事件流 → gate decide ALLOW/BLOCK") — NOT via
the daemon (the daemon is only for the hot-path `super-harness-hook` binary).
These tests therefore set up `<ws>/.harness/state.yaml` with the production
`write_state_yaml` + `ChangeState` and invoke `gate check` via CliRunner; no
`supervisor` monkeypatch.

Coverage:
  1. allow: PLAN_APPROVED → exit 0, human output contains "allow:"
  2. block: INTENT_DECLARED → exit 2, output mentions the state/reason
  3. --json: frozen `data` schema {gate_name, decision, current_state, reason,
     suggested_action} with correct values
  4. --quiet: allow case → no stdout, still exit 0
  5. cold-path (pre-commit) → exit 1 (EXIT_GENERIC), NOT a click usage error
  6. no harness (.harness/ absent) → EXIT_NO_CONFIG (3)
  7. change_id resolution: no active change in state.yaml → ALLOW exit 0
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.state import ChangeState
from super_harness.core.state_yaml import write_state_yaml
from super_harness.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)


def _write_state(ws: Path, changes: dict[str, ChangeState]) -> None:
    """Materialize `<ws>/.harness/state.yaml` from a changes map.

    Mirrors the reducer→writer pipeline the production code consumes: the
    `gate check` command reads this file in-process (no daemon).
    """
    (ws / ".harness").mkdir(parents=True, exist_ok=True)
    last_id = next(iter(changes.values())).last_event_id if changes else "ev_0"
    write_state_yaml(
        ws / ".harness" / "state.yaml", changes, last_reduced_event_id=last_id
    )


def test_gate_check_allow(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        {"c1": ChangeState(change_id="c1", current_state="PLAN_APPROVED")},
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "gate", "check", "pre-tool-use",
         "--tool", "Edit", "--file", "a.py"],
    )
    assert r.exit_code == EXIT_OK
    assert "allow:" in r.output


def test_gate_check_block_exit_2(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        {"c1": ChangeState(change_id="c1", current_state="INTENT_DECLARED")},
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "gate", "check", "pre-tool-use",
         "--tool", "Edit", "--file", "a.py"],
    )
    assert r.exit_code == EXIT_VALIDATION
    assert "INTENT_DECLARED" in r.output


def test_gate_check_json_block_schema(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        {"c1": ChangeState(change_id="c1", current_state="INTENT_DECLARED")},
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "gate", "check", "pre-tool-use",
         "--tool", "Edit", "--file", "a.py"],
    )
    assert r.exit_code == EXIT_VALIDATION
    payload = json.loads(r.output)
    assert payload["command"] == "gate check"
    assert payload["status"] == "fail"
    assert payload["exit_code"] == EXIT_VALIDATION
    data = payload["data"]
    assert set(data) == {
        "gate_name",
        "decision",
        "current_state",
        "reason",
        "suggested_action",
    }
    assert data["gate_name"] == "pre-tool-use"
    assert data["decision"] == "block"
    assert data["current_state"] == "INTENT_DECLARED"
    assert data["reason"]
    assert data["suggested_action"] is not None


def test_gate_check_json_allow_schema(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        {"c1": ChangeState(change_id="c1", current_state="PLAN_APPROVED")},
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "gate", "check", "pre-tool-use"],
    )
    assert r.exit_code == EXIT_OK
    payload = json.loads(r.output)
    assert payload["status"] == "pass"
    data = payload["data"]
    assert set(data) == {
        "gate_name",
        "decision",
        "current_state",
        "reason",
        "suggested_action",
    }
    assert data["decision"] == "allow"
    assert data["current_state"] == "PLAN_APPROVED"


def test_gate_check_quiet_suppresses_human_output(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        {"c1": ChangeState(change_id="c1", current_state="PLAN_APPROVED")},
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--quiet", "gate", "check", "pre-tool-use"],
    )
    assert r.exit_code == EXIT_OK
    assert r.output == ""


def test_gate_check_cold_path_not_implemented(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "gate", "check", "pre-commit"]
    )
    assert r.exit_code == EXIT_GENERIC  # NOT a click usage error (exit 2 from Choice)
    combined = r.output + (r.stderr or "")
    assert "not yet implemented" in combined


def test_gate_check_when_no_harness(tmp_path: Path) -> None:
    # Mirrors test_gate.py::test_list_when_no_harness — no `.harness/` → EXIT_NO_CONFIG.
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "gate", "check", "pre-tool-use"]
    )
    assert r.exit_code == EXIT_NO_CONFIG
    combined = r.output + (r.stderr or "")
    assert "No .harness/" in combined


def test_gate_check_no_active_change_allows(tmp_path: Path) -> None:
    # state.yaml exists but holds no non-terminal change → "no active change" → ALLOW.
    _write_state(
        tmp_path,
        {"c1": ChangeState(change_id="c1", current_state="ARCHIVED")},
    )
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "gate", "check", "pre-tool-use"]
    )
    assert r.exit_code == EXIT_OK
    assert "allow:" in r.output


def test_gate_check_missing_state_yaml_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `.harness/` present but no state.yaml → treat as no active change → ALLOW.
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "gate", "check", "pre-tool-use"]
    )
    assert r.exit_code == EXIT_OK
    assert "allow:" in r.output
