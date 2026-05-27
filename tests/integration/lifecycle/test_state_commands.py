"""Integration tests for `state rebuild`, `state verify`, and `event log` CLI.

Task 1.8 — wires the previously-built reducer (1.6) + state.yaml writer (1.7)
+ event parser (1.2) into operator-facing subcommands. These tests exercise
the full CliRunner path including `--workspace`, `--json`, and exit codes.
"""
import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.writer import EventWriter
from tests.unit.core.test_writer import _make_event


def test_state_rebuild_dry_run_outputs_yaml(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    EventWriter(tmp_path / ".harness" / "events.jsonl").emit(_make_event("c1"))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "state", "rebuild", "--dry-run"])
    assert r.exit_code == 0
    assert "INTENT_DECLARED" in r.output


def test_state_rebuild_writes_file(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    EventWriter(tmp_path / ".harness" / "events.jsonl").emit(_make_event("c1"))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "state", "rebuild"])
    assert r.exit_code == 0
    assert (tmp_path / ".harness" / "state.yaml").exists()


def test_event_log_json(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    EventWriter(tmp_path / ".harness" / "events.jsonl").emit(_make_event("c1"))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "event", "log"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert len(payload["data"]["events"]) == 1
