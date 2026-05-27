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


def test_state_rebuild_records_last_event_in_file_not_dict_order(tmp_path: Path) -> None:
    """last_reduced_event_id must reflect file-tail event_id, not dict-iteration order.

    Regression test for the bug where iterating derived.values() in dict order
    (first-seen-change_id) was used instead of the literal last line of
    events.jsonl. Stream: c1, c2, c1 → derived dict order is {c1, c2}; correct
    last_reduced_event_id is c1's 2nd event (the last line in the file), NOT
    c2's only event (the last dict-iteration value).
    """
    import yaml
    (tmp_path / ".harness").mkdir()
    w = EventWriter(tmp_path / ".harness" / "events.jsonl")
    e1 = _make_event("c1", "intent_declared")
    e2 = _make_event("c2", "intent_declared")
    e3 = _make_event("c1", "plan_ready")
    w.emit(e1)
    w.emit(e2)
    w.emit(e3)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "state", "rebuild"])
    assert r.exit_code == 0
    state_yaml = yaml.safe_load((tmp_path / ".harness" / "state.yaml").read_text())
    # The literal last line of events.jsonl is e3 (c1.plan_ready)
    assert state_yaml["last_reduced_event_id"] == e3.event_id


def test_event_log_json(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    EventWriter(tmp_path / ".harness" / "events.jsonl").emit(_make_event("c1"))
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "event", "log"])
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert len(payload["data"]["events"]) == 1
