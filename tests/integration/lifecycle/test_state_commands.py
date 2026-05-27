"""Integration tests for `state rebuild`, `state verify`, and `event log` CLI.

Task 1.8 — wires the previously-built reducer (1.6) + state.yaml writer (1.7)
+ event parser (1.2) into operator-facing subcommands. These tests exercise
the full CliRunner path including `--workspace`, `--json`, and exit codes.

`state verify` invariant coverage map (matches the four checks in
`cli/state.py::state_verify` docstring):
1. Malformed JSON / missing required fields
   → test_state_verify_detects_malformed_json
2. Illegal transitions per compute_target_state
   → test_state_verify_detects_illegal_transition
3. Reducer non-idempotency
   → covered by `tests/unit/core/test_reducer.py` (no CLI surface needed)
4. event_counts contamination with unknown event types
   → unreachable from CLI today because `derive_state` filters unknown event
   types out of `event_counts` before they're recorded (see the
   `KNOWN_EVENT_TYPES` check in `core/reducer.py` around lines 95-99).
   `parse_event_line` (core/events.py lines 78-80, 142-151) is intentionally
   tolerant of unknown types per lifecycle-event-model spec §3.8.1 — the
   parser does NOT reject them. The invariant check in `state verify` defends
   against a hypothetical future reducer bug that bypasses that filter
   (e.g. a refactor that drops the KNOWN_EVENT_TYPES gate or a synthetic
   event injected past the reducer).
"""
import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.exit_codes import EXIT_VALIDATION
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


# -------------------- `state verify` invariant coverage --------------------
#
# These tests exercise the two CLI-reachable failure modes that previously
# had no end-to-end coverage. The reducer-idempotency invariant (#3) is
# already covered at the unit layer; the unknown-event-type invariant (#4)
# is unreachable from the CLI today because `parse_event_line` rejects
# unknown types upstream — the check defends against a future reducer that
# bypasses parsing, not the current path.


def test_state_verify_detects_malformed_json(tmp_path: Path) -> None:
    """Invariant #1: any non-blank line that fails parse_event_line → exit 2.

    Writes a deliberately broken JSON line (unclosed brace) before a valid
    event so the per-line iteration is forced to recover and report the
    bad line by its 1-based line number. Stderr message is shaped by
    `format_error("state verify", ...)` — assert the canonical prefix +
    the underlying "malformed event" wording + the offending line number.
    """
    (tmp_path / ".harness").mkdir()
    events_file = tmp_path / ".harness" / "events.jsonl"
    # Line 1: bad JSON (unclosed brace). Line 2: a legal intent_declared so
    # the file isn't empty after the bad line — proves the iterator keeps
    # walking and the violation is recorded for line 1 specifically.
    bad_line = '{"event_id": "01H0000000000000000000BAD", "type":'
    good_event = _make_event("c1")
    EventWriter(events_file).emit(good_event)
    # Prepend the bad line so it occupies line 1.
    original = events_file.read_text()
    events_file.write_text(bad_line + "\n" + original)

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "state", "verify"])
    assert r.exit_code == EXIT_VALIDATION
    assert "super-harness state verify:" in r.stderr
    assert "malformed" in r.stderr
    assert "line 1" in r.stderr


def test_state_verify_detects_illegal_transition(tmp_path: Path) -> None:
    """Invariant #2: events that violate the transition table → exit 2.

    Constructs `intent_declared` → `merged` for the same change. `merged` is
    only legal from `READY_TO_MERGE`, so against the INTENT_DECLARED state
    that the first event leaves us in, the table returns INVALID and the
    verifier records "illegal transition <prev> --[<type>]--> ?" tagged
    with the offending event_id. We bypass emit-time validation by writing
    the raw JSON line directly (matches the approach used in
    `tests/integration/cli/test_change.py::test_change_resume_recent_events_*`).
    """
    (tmp_path / ".harness").mkdir()
    events_file = tmp_path / ".harness" / "events.jsonl"
    # First event: legal intent_declared via the writer (also pins the
    # writer-side append-order semantics for the test).
    EventWriter(events_file).emit(_make_event("c1", "intent_declared"))
    # Second event: raw-write `merged` for the same change — illegal from
    # INTENT_DECLARED per `compute_target_state`. Use a known event_id so
    # the assertion can pin "the violation names this specific event".
    illegal_event = {
        "event_id": "01H00000000000000000ILLEGAL",
        "type": "merged",
        "change_id": "c1",
        "timestamp": "2026-05-27T10:01:00Z",
        "actor": {"type": "human", "identifier": "test"},
        "framework": "plain",
        "payload": {},
    }
    with events_file.open("a") as f:
        f.write(json.dumps(illegal_event) + "\n")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "state", "verify"])
    assert r.exit_code == EXIT_VALIDATION
    assert "super-harness state verify:" in r.stderr
    assert "illegal transition" in r.stderr
    # The verifier tags the violation with the offending event_id so an
    # operator can grep the log for the exact line.
    assert "01H00000000000000000ILLEGAL" in r.stderr
