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


def _import_full(eid, change, ts, *, reviewer, source, round_id, usage=None, findings=()):
    payload = {
        "reviewer": reviewer,
        "source": source,
        "round_id": round_id,
        "receipt": {"usage": usage} if usage is not None else {},
        "verdict": {"findings": [{"id": f} for f in findings]},
    }
    return json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": source},
        "framework": "plain", "payload": payload,
    })


def _round_closed(eid, change, ts, outcome, round_id="r1"):
    return json.dumps({
        "event_id": eid, "type": "review_round_closed", "change_id": change,
        "timestamp": ts, "actor": {"type": "sensor", "identifier": "review"},
        "framework": "plain", "payload": {"round_id": round_id, "outcome": outcome},
    })


def _write_blocks(tmp_path: Path, records: list[dict]) -> None:
    """Seed the Stage-2 gate-blocks telemetry log under the workspace root."""
    d = tmp_path / ".harness"
    d.mkdir(exist_ok=True)
    (d / "gate-blocks.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8",
    )


def _block(change, file, state, ts):
    return {"ts": ts, "change_id": change, "state": state, "tool": "Write",
            "file": file, "reason": "r", "gate": "pre-tool-use"}


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


# --- Cost breakdown (role x source x round) ---


def test_cost_breakdown_one_row_per_run(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="r1", usage={"total_tokens": 620000},
                     findings=["F1", "F2"]),
        _import_full("e2", "c1", "2026-07-02T00:01:00Z", reviewer="plan-reviewer",
                     source="claude", round_id="r1", usage={"total_tokens": 580000},
                     findings=["F3"]),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    rows = report.cost_breakdown
    assert len(rows) == 2
    codex = next(r for r in rows if r.source == "codex")
    assert codex.role == "plan-reviewer"
    assert codex.change_id == "c1"
    assert codex.round_id == "r1"
    assert codex.tokens == 620000
    assert codex.findings_raised == 2
    assert codex.outcome == "open"          # no review_round_closed seeded


def test_cost_breakdown_missing_usage_is_none_not_zero(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="code-reviewer",
                     source="claude", round_id="r1", usage=None, findings=[]),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.cost_breakdown[0].tokens is None      # NOT 0 — usage not captured
    assert report.cost_breakdown[0].findings_raised == 0


def test_cost_breakdown_tolerates_missing_source_and_round(tmp_path):
    # legacy/minimal import event (mirrors _import_with_usage: no source/round_id)
    events_file = _write_events(tmp_path, [
        _import_with_usage("e1", "c1", "2026-07-02T00:00:00Z", {"total_tokens": 100}),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    row = report.cost_breakdown[0]
    assert row.source == "unknown"
    assert row.round_id == ""
    assert row.round == 0
    assert row.tokens == 100


def test_cost_breakdown_assigns_round_ordinals_per_change_and_role(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="rA", usage={"total_tokens": 1}),
        _import_full("e2", "c1", "2026-07-02T01:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="rB", usage={"total_tokens": 1}),
        # different role, first round -> ordinal restarts at 1
        _import_full("e3", "c1", "2026-07-02T02:00:00Z", reviewer="code-reviewer",
                     source="codex", round_id="rC", usage={"total_tokens": 1}),
        # different change, same round_id-space -> ordinal restarts at 1
        _import_full("e4", "c2", "2026-07-02T03:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="rD", usage={"total_tokens": 1}),
    ])
    rows = build_value_report(
        events_file, since=None, until=None, workspace_root=tmp_path
    ).cost_breakdown
    by_rid = {r.round_id: r.round for r in rows}
    assert by_rid["rA"] == 1
    assert by_rid["rB"] == 2          # 2nd plan round in c1
    assert by_rid["rC"] == 1          # code-reviewer restarts
    assert by_rid["rD"] == 1          # c2 restarts


def test_cost_breakdown_row_carries_round_outcome(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="r1", usage={"total_tokens": 1}, findings=[]),
        _round_closed("e2", "c1", "2026-07-02T00:05:00Z", "rejected", round_id="r1"),
    ])
    rows = build_value_report(
        events_file, since=None, until=None, workspace_root=tmp_path
    ).cost_breakdown
    assert rows[0].outcome == "rejected"


def test_cost_breakdown_respects_window(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_full("e1", "c1", "2026-06-01T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="r1", usage={"total_tokens": 1}),
        _import_full("e2", "c1", "2026-07-05T00:00:00Z", reviewer="plan-reviewer",
                     source="codex", round_id="r2", usage={"total_tokens": 1}),
    ])
    rows = build_value_report(
        events_file, since="2026-07-01", until=None, workspace_root=tmp_path
    ).cost_breakdown
    assert [r.round_id for r in rows] == ["r2"]   # June run excluded from breakdown too


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


# --- Stage 2: gate-blocked edit targets (distinct floor) ---


def test_edits_blocked_counts_distinct_target_tuples(tmp_path):
    events_file = tmp_path / "events.jsonl"
    _write_blocks(tmp_path, [
        _block("c1", "a.py", "INTENT_DECLARED", "2026-07-16T00:00:00Z"),
        _block("c1", "a.py", "INTENT_DECLARED", "2026-07-16T00:00:01Z"),  # retry dup
        _block("c1", "b.py", "INTENT_DECLARED", "2026-07-16T00:00:02Z"),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.edits_blocked == 2  # (c1,a.py,S) collapses; (c1,b.py,S) distinct


def test_edits_blocked_respects_window(tmp_path):
    events_file = tmp_path / "events.jsonl"
    _write_blocks(tmp_path, [
        _block("c1", "a.py", "S", "2026-07-01T00:00:00Z"),
        _block("c1", "b.py", "S", "2026-07-20T00:00:00Z"),
        {"ts": "not-a-date", "change_id": "c1", "state": "S", "tool": "Write",
         "file": "c.py", "reason": "r", "gate": "g"},  # unparseable → excluded with a bound
    ])
    report = build_value_report(
        events_file, since="2026-07-15", until=None, workspace_root=tmp_path
    )
    assert report.edits_blocked == 1  # only the 2026-07-20 target; unparseable excluded


def test_edits_blocked_zero_when_no_log(tmp_path):
    events_file = tmp_path / "events.jsonl"
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.edits_blocked == 0


def test_usage_tokens_counts_claude_cache_fields():
    """Cache reads are where reviewer CLIs actually bill. A real claude receipt has a
    near-empty input_tokens and millions of cache-read tokens; counting only
    input+output under-reports by ~86x."""
    from super_harness.engineering.value_report import usage_tokens

    assert usage_tokens({
        "input_tokens": 63,
        "output_tokens": 23320,
        "cache_creation_input_tokens": 110069,
        "cache_read_input_tokens": 2629763,
    }) == 63 + 23320 + 110069 + 2629763


def test_usage_tokens_does_not_add_the_codex_subset_key():
    """codex reports cached_input_tokens as a portion ALREADY INSIDE input_tokens.
    Adding it would inflate that producer's total by ~89%. The rule is per named
    key, never 'anything containing cache'."""
    from super_harness.engineering.value_report import usage_tokens

    assert usage_tokens({
        "input_tokens": 1007614,
        "cached_input_tokens": 898816,
        "output_tokens": 5903,
        "reasoning_output_tokens": 3166,
    }) == 1007614 + 5903


def test_usage_tokens_prefers_reported_total():
    from super_harness.engineering.value_report import usage_tokens

    assert usage_tokens({
        "total_tokens": 500,
        "input_tokens": 1,
        "cache_read_input_tokens": 999999,
    }) == 500


def test_usage_tokens_absent_usage_is_none_not_zero():
    from super_harness.engineering.value_report import usage_tokens

    assert usage_tokens(None) is None
    assert usage_tokens({}) is None
    assert usage_tokens("nope") is None


def test_usage_tokens_never_raises_on_junk_values():
    from super_harness.engineering.value_report import usage_tokens

    assert usage_tokens({"input_tokens": "x", "cache_read_input_tokens": None}) is None
    assert usage_tokens({"input_tokens": 10, "cache_read_input_tokens": "x"}) == 10
    assert usage_tokens({"input_tokens": True, "output_tokens": 5}) == 5


def _import_with_cost(eid, change, ts, *, usage, cost):
    receipt = {"usage": usage}
    if cost is not None:
        receipt["reported_cost_usd"] = cost
    return json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "claude"},
        "framework": "plain",
        "payload": {"reviewer": "code-reviewer", "source": "claude", "round_id": "r1",
                    "receipt": receipt, "verdict": {"findings": []}},
    })


def test_report_sums_producer_reported_cost(tmp_path):
    """The producers state their own cost; the report adds those up and says so.
    It does not price tokens itself."""
    events_file = _write_events(tmp_path, [
        _import_with_cost("e1", "c1", "2026-07-02T00:00:00Z",
                          usage={"input_tokens": 10, "output_tokens": 5}, cost=1.340269),
        _import_with_cost("e2", "c1", "2026-07-02T01:00:00Z",
                          usage={"input_tokens": 10, "output_tokens": 5}, cost=0.659731),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_reported_cost_usd == 2.0
    assert report.review_runs_with_reported_cost == 2


def test_report_reported_cost_is_none_when_no_run_reported_one(tmp_path):
    """None, not 0.0 — a report that shows $0.00 for uncaptured cost is lying."""
    events_file = _write_events(tmp_path, [
        _import_with_cost("e1", "c1", "2026-07-02T00:00:00Z",
                          usage={"input_tokens": 10, "output_tokens": 5}, cost=None),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_reported_cost_usd is None
    assert report.review_runs_with_reported_cost == 0


def test_report_reported_cost_ignores_junk_values(tmp_path):
    events_file = _write_events(tmp_path, [
        _import_with_cost("e1", "c1", "2026-07-02T00:00:00Z",
                          usage={"input_tokens": 1}, cost="1.34"),
        _import_with_cost("e2", "c1", "2026-07-02T01:00:00Z",
                          usage={"input_tokens": 1}, cost=True),
        _import_with_cost("e3", "c1", "2026-07-02T02:00:00Z",
                          usage={"input_tokens": 1}, cost=0.5),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_reported_cost_usd == 0.5
    assert report.review_runs_with_reported_cost == 1


def _budget_exceeded(eid, change, ts, *, reviewer="plan-reviewer", attempted=7):
    return json.dumps({
        "event_id": eid, "type": "review_budget_exceeded", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "review-protocol"},
        "framework": "plain",
        "payload": {"reviewer": reviewer, "attempted_round": attempted,
                    "started_rounds": attempted - 1, "max_automatic_rounds": 6},
    })


def test_report_counts_budget_hits(tmp_path):
    """Surfaced whether or not the agent relayed the block — advisory text alone is
    not enough."""
    events_file = _write_events(tmp_path, [
        _budget_exceeded("e1", "c1", "2026-08-06T00:00:00Z", attempted=7),
        _budget_exceeded("e2", "c1", "2026-08-06T01:00:00Z", attempted=8),
        _budget_exceeded("e3", "c2", "2026-08-06T02:00:00Z", attempted=3,
                         reviewer="code-reviewer"),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_budget_hits == 3


def test_report_budget_hits_is_zero_when_the_brake_never_fired(tmp_path):
    events_file = _write_events(tmp_path, [])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_budget_hits == 0


def test_report_budget_hits_counts_distinct_rounds_not_retries(tmp_path):
    """An agent that retries a blocked `review begin` — the behaviour the block forbids
    and cannot prevent — must not be able to inflate a number presented as rounds."""
    events_file = _write_events(tmp_path, [
        _budget_exceeded("e1", "c1", "2026-08-06T00:00:00Z", attempted=7),
        _budget_exceeded("e2", "c1", "2026-08-06T00:01:00Z", attempted=7),  # retry
        _budget_exceeded("e3", "c1", "2026-08-06T00:02:00Z", attempted=7),  # retry
        _budget_exceeded("e4", "c1", "2026-08-06T01:00:00Z", attempted=8),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_budget_hits == 2


def test_report_budget_hits_separates_the_two_roles(tmp_path):
    events_file = _write_events(tmp_path, [
        _budget_exceeded("e1", "c1", "2026-08-06T00:00:00Z", attempted=7),
        _budget_exceeded("e2", "c1", "2026-08-06T01:00:00Z", attempted=7,
                         reviewer="code-reviewer"),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_budget_hits == 2


def test_budget_hits_do_not_collapse_across_changes(tmp_path):
    """f-01. `report` is repo-wide, and with the shipped plan budget of 6 EVERY change's
    first block is attempted_round=7 — so a dedupe key without change_id collapses them
    all into one and systematically undercounts the brake it exists to make visible.

    Finding identity in this module is per-change everywhere else for the same reason
    (`_edits_blocked` keys on (change_id, file, state); `_dispositions` on
    (change_id, id), with a comment explaining that ids recur across changes)."""
    events_file = _write_events(tmp_path, [
        _budget_exceeded("e1", "c1", "2026-08-06T00:00:00Z", attempted=7),
        _budget_exceeded("e2", "c2", "2026-08-06T01:00:00Z", attempted=7),
        _budget_exceeded("e3", "c3", "2026-08-06T02:00:00Z", attempted=7),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_budget_hits == 3


def test_budget_hits_still_dedupe_retries_within_one_change(tmp_path):
    """The retry-proofing must survive the per-change fix."""
    events_file = _write_events(tmp_path, [
        _budget_exceeded("e1", "c1", "2026-08-06T00:00:00Z", attempted=7),
        _budget_exceeded("e2", "c1", "2026-08-06T00:01:00Z", attempted=7),
        _budget_exceeded("e3", "c2", "2026-08-06T00:02:00Z", attempted=7),
    ])
    report = build_value_report(events_file, since=None, until=None, workspace_root=tmp_path)
    assert report.review_budget_hits == 2
