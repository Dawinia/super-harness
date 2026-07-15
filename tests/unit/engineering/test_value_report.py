"""Tests for the Stage 1 value-report rollup (engineering/value_report.py).

Per docs/plans/2026-07-15-value-report-stage1.md. Each test seeds a synthetic
events.jsonl and asserts the rolled-up counts. The taxonomy contract (only
review + bypass-audit leave a realized-effect trace; every number can show a
negative; never fabricate) is the acceptance oracle.
"""
import json
from pathlib import Path

from super_harness.engineering.value_report import ValueReport, build_value_report


def _write_events(tmp_path: Path, lines: list[str]) -> Path:
    f = tmp_path / "events.jsonl"
    f.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return f


def _code_verdict_event(eid, change, ts, *, findings=(), prior=()):
    payload = {
        "reviewer": "code-reviewer",
        "verdict": {
            "findings": [
                {"id": fid, "severity": "major", "file": "x", "summary": "s"}
                for fid in findings
            ],
            "prior_findings": [
                {"id": pid, "disposition": disp, "note": "n"} for pid, disp in prior
            ],
        },
    }
    return json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "codex"},
        "framework": "plain", "payload": payload,
    })


def _bypass(eid, change, ts, type_="gate_bypassed"):
    return json.dumps({
        "event_id": eid, "type": type_, "change_id": change, "timestamp": ts,
        "actor": {"type": "sensor", "identifier": "gate"}, "framework": "plain",
        "payload": {"tool": "Write", "file": "x.py"},
    })


def _import_with_usage(eid, change, ts, usage):
    return json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "codex"},
        "framework": "plain",
        "payload": {"reviewer": "code-reviewer", "receipt": {"usage": usage}, "verdict": {}},
    })


def _round_closed(eid, change, ts, outcome):
    return json.dumps({
        "event_id": eid, "type": "review_round_closed", "change_id": change,
        "timestamp": ts, "actor": {"type": "sensor", "identifier": "review"},
        "framework": "plain", "payload": {"outcome": outcome},
    })


# --- Task 1: skeleton + windowing ---


def test_empty_stream_yields_zeroed_report(tmp_path):
    events_file = tmp_path / "events.jsonl"  # does not exist
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert isinstance(report, ValueReport)
    assert report.changes_touched == 0
    assert report.findings_resolved == 0
    assert report.undisclosed_bypasses == 0
    assert report.review_tokens == 0


def test_changes_touched_counts_distinct_change_ids_in_window(tmp_path):
    events_file = _write_events(tmp_path, [
        '{"event_id":"e1","type":"intent_declared","change_id":"c1","timestamp":"2026-07-01T00:00:00Z","actor":{"type":"human","identifier":"u"},"framework":"plain","payload":{}}',
        '{"event_id":"e2","type":"intent_declared","change_id":"c2","timestamp":"2026-07-10T00:00:00Z","actor":{"type":"human","identifier":"u"},"framework":"plain","payload":{}}',
        '{"event_id":"e3","type":"intent_declared","change_id":"c3","timestamp":"2026-06-01T00:00:00Z","actor":{"type":"human","identifier":"u"},"framework":"plain","payload":{}}',
    ])
    report = build_value_report(
        events_file, since="2026-07-01", until=None, workspace_root=tmp_path
    )
    assert report.changes_touched == 2  # c3 (June) excluded


# --- Task 2: findings resolved / wontfix / open-undisposed ---


def test_findings_resolved_wontfix_open(tmp_path):
    events_file = _write_events(tmp_path, [
        _code_verdict_event("e1", "c1", "2026-07-02T00:00:00Z", findings=["F1", "F2", "F3"]),
        _code_verdict_event(
            "e2", "c1", "2026-07-03T00:00:00Z", prior=[("F1", "resolved"), ("F2", "wontfix")]
        ),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.findings_resolved == 1          # F1
    assert report.findings_wontfix == 1           # F2
    assert report.findings_open_undisposed == 1   # F3 raised, never disposed


def test_open_finding_not_counted_when_resolved_outside_window(tmp_path):
    events_file = _write_events(tmp_path, [
        _code_verdict_event("e1", "c1", "2026-07-02T00:00:00Z", findings=["F1"]),
        _code_verdict_event("e2", "c1", "2026-07-20T00:00:00Z", prior=[("F1", "resolved")]),
    ])
    report = build_value_report(
        events_file, since="2026-07-01", until="2026-07-10", workspace_root=tmp_path
    )
    assert report.findings_open_undisposed == 0   # disposition seen in full stream
    assert report.findings_resolved == 0          # disposition event is out of window


# --- Task 3: order-aware undisclosed bypasses ---


def test_undisclosed_bypass_is_order_aware(tmp_path):
    events_file = _write_events(tmp_path, [
        _bypass("e1", "c1", "2026-07-02T00:00:00Z"),  # c1: undisclosed
        _bypass("e2", "c2", "2026-07-02T00:00:00Z"),  # c2: bypass...
        _bypass("e3", "c2", "2026-07-02T01:00:00Z", "gate_bypass_disclosed"),  # ...disclosed after
        _bypass("e4", "c3", "2026-07-02T00:00:00Z", "gate_bypass_disclosed"),  # c3: disclosed 1st
        _bypass("e5", "c3", "2026-07-02T02:00:00Z"),  # ...then a LATER bypass -> undisclosed
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.undisclosed_bypasses == 2  # c1 + c3's post-disclosure bypass


# --- Task 4: review cost — tokens, usage coverage, rejected rounds ---


def test_review_tokens_usage_and_rejected_rounds(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_with_usage(
            "e1", "c1", "2026-07-02T00:00:00Z", {"input_tokens": 100, "output_tokens": 20}
        ),
        _import_with_usage("e2", "c1", "2026-07-02T01:00:00Z", {"total_tokens": 300}),
        _import_with_usage("e3", "c1", "2026-07-02T02:00:00Z", None),   # no usage reported
        _round_closed("e4", "c1", "2026-07-02T03:00:00Z", "rejected"),  # counts
        _round_closed("e5", "c1", "2026-07-02T04:00:00Z", "approved"),  # does NOT
        _round_closed("e6", "c1", "2026-07-02T05:00:00Z", "execution_failed"),  # NOT counted
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_tokens == 420          # 120 + 300
    assert report.review_runs_total == 3
    assert report.review_runs_with_usage == 2
    assert report.rejected_rounds == 1          # only e4 (rejected); e6 execution_failed excluded


# --- Task 5: armed decisions (footnote) ---


def test_armed_decisions_counts_ratified_with_check(tmp_path):
    dec_dir = tmp_path / "docs" / "decisions"
    dec_dir.mkdir(parents=True)
    (dec_dir / "d-example.md").write_text(
        "---\nid: d-example\nstatus: ratified\n---\n\nBody.\n\n```check\ntrue\n```\n",
        encoding="utf-8",
    )
    events_file = tmp_path / "events.jsonl"
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.armed_decisions == 1


# --- Code-review regressions ---


def test_finding_ids_are_scoped_per_change(tmp_path):
    # CODX-007: the same short id "F1" on two changes must not collide — disposing
    # c1's F1 must NOT clear c2's F1 (legacy code_review_failed ids are short).
    events_file = _write_events(tmp_path, [
        _code_verdict_event("e1", "c1", "2026-07-02T00:00:00Z", findings=["F1"]),
        _code_verdict_event("e2", "c2", "2026-07-02T00:00:00Z", findings=["F1"]),
        _code_verdict_event("e3", "c1", "2026-07-03T00:00:00Z", prior=[("F1", "resolved")]),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.findings_resolved == 1          # c1's F1
    assert report.findings_open_undisposed == 1   # c2's F1 still open (not cleared by c1)


def test_date_only_until_includes_the_whole_day(tmp_path):
    # CODX-008: a same-day event later than midnight must be included by a
    # date-only --until (parse_ts gives midnight; the bound extends to end-of-day).
    events_file = _write_events(tmp_path, [
        _code_verdict_event("e1", "c1", "2026-07-10T15:00:00Z", findings=["F1"]),
    ])
    report = build_value_report(
        events_file, since=None, until="2026-07-10", workspace_root=tmp_path
    )
    assert report.changes_touched == 1            # not dropped by the midnight boundary
    assert report.findings_open_undisposed == 1


def test_non_dict_verdict_does_not_crash(tmp_path):
    # CLDX-001: a parseable event with a non-dict verdict must not crash the report.
    events_file = _write_events(tmp_path, [
        json.dumps({
            "event_id": "e1", "type": "review_result_imported", "change_id": "c1",
            "timestamp": "2026-07-02T00:00:00Z",
            "actor": {"type": "agent", "identifier": "x"}, "framework": "plain",
            "payload": {"reviewer": "code-reviewer", "verdict": "oops-not-a-dict"},
        }),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.findings_resolved == 0
    assert report.findings_open_undisposed == 0
