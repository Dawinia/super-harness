"""Tests for the `super-harness report` CLI command (Stage 1 value report)."""
import json as _json

from click.testing import CliRunner

from super_harness.cli import main


def _seed(tmp_path, lines):
    (tmp_path / ".harness").mkdir(exist_ok=True)
    (tmp_path / ".harness" / "events.jsonl").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def _imp(eid, change, ts, *, reviewer, source, round_id, total=None, findings=()):
    payload = {
        "reviewer": reviewer, "source": source, "round_id": round_id,
        "receipt": {"usage": {"total_tokens": total}} if total is not None else {},
        "verdict": {"findings": [{"id": f} for f in findings]},
    }
    return _json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": source},
        "framework": "plain", "payload": payload,
    })


def _closed(eid, change, ts, outcome, round_id):
    return _json.dumps({
        "event_id": eid, "type": "review_round_closed", "change_id": change,
        "timestamp": ts, "actor": {"type": "sensor", "identifier": "review"},
        "framework": "plain", "payload": {"round_id": round_id, "outcome": outcome},
    })


# --- Task 6: human rendering + registration ---


def test_report_human_shows_effect_and_bottom_line(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 0
    assert "what it did for you" in result.output
    assert "nothing" in result.output.lower() or "no measurable" in result.output.lower()
    # CODX-003: an open finding must never be rendered as a user action.
    assert "acknowledged" not in result.output.lower()


# --- Task 7: brief + json ---


def test_report_brief_is_one_line(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report", "--brief"])
    assert result.exit_code == 0
    assert result.output.strip().count("\n") == 0


def test_report_json_envelope_shape(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "report"])
    assert result.exit_code == 0
    env = _json.loads(result.output)
    assert env["command"] == "report"
    assert env["status"] == "pass"
    assert set(env.keys()) == {"command", "version", "status", "exit_code", "data", "errors"}
    assert "findings_resolved" in env["data"]


# --- Cost breakdown (role x source x round) ---


def test_report_json_includes_cost_breakdown_rows(tmp_path):
    _seed(tmp_path, [
        _imp("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
             source="codex", round_id="r1", total=620000, findings=["F1"]),
    ])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "report"])
    assert result.exit_code == 0
    data = _json.loads(result.output)["data"]
    assert isinstance(data["cost_breakdown"], list)
    row = data["cost_breakdown"][0]
    assert set(row) >= {"role", "source", "change_id", "round", "round_id",
                        "tokens", "findings_raised", "outcome"}
    assert row["role"] == "plan-reviewer"
    assert row["tokens"] == 620000


def test_report_human_shows_role_source_breakdown_with_flags(tmp_path):
    _seed(tmp_path, [
        _imp("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
             source="codex", round_id="r1", total=620000, findings=["F1", "F2"]),
        _imp("e2", "c1", "2026-07-02T01:00:00Z", reviewer="plan-reviewer",
             source="codex", round_id="r2", total=310000, findings=[]),   # 0-finding round
        _closed("e3", "c1", "2026-07-02T02:00:00Z", "rejected", "r2"),
    ])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 0
    out = result.output
    assert "Review cost breakdown" in out
    assert "review-side" in out and "partial" in out          # caveat present
    assert "plan-reviewer" in out and "codex" in out
    assert "0-finding round" in out                            # zero-finding flag fired
    assert "rejected round" in out                             # rejected flag fired


def test_report_human_breakdown_renders_all_groups_never_capped(tmp_path):
    # CODX-001: the "typically <=4 rows" note is a common-case estimate, NOT a
    # hard cap — legacy/unknown or custom-source runs must all still render.
    _seed(tmp_path, [
        _imp("e1", "c1", "2026-07-02T00:00:00Z", reviewer="plan-reviewer",
             source="codex", round_id="r1", total=10),
        _imp("e2", "c1", "2026-07-02T00:01:00Z", reviewer="plan-reviewer",
             source="claude", round_id="r2", total=10),
        _imp("e3", "c1", "2026-07-02T00:02:00Z", reviewer="code-reviewer",
             source="codex", round_id="r3", total=10),
        _imp("e4", "c1", "2026-07-02T00:03:00Z", reviewer="code-reviewer",
             source="claude", round_id="r4", total=10),
        _imp("e5", "c1", "2026-07-02T00:04:00Z", reviewer="plan-reviewer",
             source="gemini", round_id="r5", total=10),   # 5th group (custom source)
    ])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 0
    assert "plan-reviewer/gemini" in result.output          # 5th group not dropped
    # all 5 distinct role/source group lines render (3 plan + 2 code), no cap
    assert result.output.count("plan-reviewer/") == 3
    assert result.output.count("code-reviewer/") == 2


def test_report_human_omits_breakdown_when_no_review_runs(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 0
    assert "Review cost breakdown" not in result.output


def test_report_human_breakdown_renders_unknown_tokens_as_dash(tmp_path):
    _seed(tmp_path, [
        _imp("e1", "c1", "2026-07-02T00:00:00Z", reviewer="code-reviewer",
             source="claude", round_id="r1", total=None, findings=[]),   # no usage
    ])
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 0
    assert "—" in result.output          # unknown tokens shown as em dash, never 0


# --- Task 8: error handling ---


def test_report_without_harness_exits_no_config(tmp_path):
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"])
    assert result.exit_code == 3  # EXIT_NO_CONFIG


def test_report_bad_since_is_ignored_not_crash(tmp_path):
    _seed(tmp_path, [])
    result = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "report", "--since", "not-a-date"]
    )
    assert result.exit_code == 0


# --- Stage 2: gate-held targets rendered honestly across all modes ---

from super_harness.cli.report import _bottom_line, _render_brief, _render_human  # noqa: E402
from super_harness.engineering.value_report import ValueReport  # noqa: E402


def _vr(**over):
    base = dict(
        since=None, until=None, changes_touched=1, findings_resolved=0,
        findings_open_undisposed=0, undisclosed_bypasses=0, edits_blocked=0,
        review_tokens=0, review_runs_total=0, review_runs_with_usage=0,
        findings_wontfix=0, rejected_rounds=0, armed_decisions=0,
    )
    base.update(over)
    return ValueReport(**base)


def test_human_render_shows_distinct_blocked_targets():
    out = _render_human(_vr(edits_blocked=3))
    assert "3 distinct out-of-lifecycle edit target" in out


def test_brief_render_shows_blocked_targets():
    # CODX-004/CODX-006: --brief must reflect the signal AND carry the unit.
    out = _render_brief(_vr(edits_blocked=2, findings_resolved=0))
    assert "2 distinct target(s) held" in out


def test_bottom_line_counts_blocks_as_a_catch():
    out = _bottom_line(_vr(findings_resolved=0, undisclosed_bypasses=0, edits_blocked=2))
    assert "no measurable catches" not in out
    assert "2" in out


def test_footnote_no_longer_claims_gate_leaves_no_trace():
    out = _render_human(_vr(edits_blocked=0))
    note = out.split("Note:")[1]
    assert "lifecycle gate" not in note


def _imp_with_cost(eid, change, ts, *, cost, total=100):
    receipt = {"usage": {"total_tokens": total}}
    if cost is not None:
        receipt["reported_cost_usd"] = cost
    return _json.dumps({
        "event_id": eid, "type": "review_result_imported", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "claude"},
        "framework": "plain",
        "payload": {"reviewer": "code-reviewer", "source": "claude", "round_id": "r1",
                    "receipt": receipt, "verdict": {"findings": []}},
    })


def test_report_human_shows_producer_reported_cost(tmp_path):
    _seed(tmp_path, [
        _imp_with_cost("e1", "c1", "2026-07-02T00:00:00Z", cost=1.340269),
        _imp_with_cost("e2", "c1", "2026-07-02T01:00:00Z", cost=0.659731),
    ])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "producer-reported cost: $2.00" in res.output
    assert "2/2 runs" in res.output


def test_report_human_omits_cost_line_when_nobody_reported_one(tmp_path):
    """A `$0.00` line would read as 'this was free'. Omit it instead."""
    _seed(tmp_path, [_imp_with_cost("e1", "c1", "2026-07-02T00:00:00Z", cost=None)])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "producer-reported cost" not in res.output


def _budget_hit(eid, change, ts, attempted):
    return _json.dumps({
        "event_id": eid, "type": "review_budget_exceeded", "change_id": change,
        "timestamp": ts, "actor": {"type": "agent", "identifier": "review-protocol"},
        "framework": "plain",
        "payload": {"reviewer": "plan-reviewer", "attempted_round": attempted,
                    "started_rounds": attempted - 1, "max_automatic_rounds": 6},
    })


def test_report_human_shows_budget_hits(tmp_path):
    _seed(tmp_path, [
        _budget_hit("e1", "c1", "2026-08-06T00:00:00Z", 7),
        _budget_hit("e2", "c1", "2026-08-06T01:00:00Z", 8),
    ])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "round budget: held 2 automatic round(s)" in res.output


def test_report_human_omits_budget_line_when_the_brake_never_fired(tmp_path):
    _seed(tmp_path, [])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "round budget" not in res.output


# --- 2026-08-09-authorization-channel: the count that replaced the TTY gate ---


def _authorized(eid, change, ts, *, reviewer="code-reviewer", reason="why"):
    return _json.dumps({
        "event_id": eid, "type": "review_round_authorized", "change_id": change,
        "timestamp": ts, "actor": {"type": "human", "identifier": "someone@example.test"},
        "framework": "plain", "payload": {"reviewer": reviewer, "reason": reason},
    })


def test_report_human_shows_the_count_and_every_reason(tmp_path):
    """The acceptance obligation is the RENDERED surface, not the derivation: a
    computed-but-unshown count is exactly the failure this cut exists to avoid.

    Both halves are load-bearing. The count is what a human can falsify from memory;
    the reasons are the only account of why each round was funded.
    """
    _seed(tmp_path, [
        _authorized("e1", "c1", "2026-08-09T10:18:46Z", reason="expensive profile, my call"),
        _authorized("e2", "c1", "2026-08-09T11:10:38Z", reviewer="plan-reviewer",
                    reason="one more plan round"),
    ])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "2 human authorization(s) recorded" in res.output
    assert "expensive profile, my call" in res.output
    assert "one more plan round" in res.output
    assert "code-reviewer" in res.output and "plan-reviewer" in res.output
    assert "2026-08-09" in res.output


def test_report_human_states_zero_authorizations_rather_than_omitting_the_line(tmp_path):
    """Unlike the budget line, a zero here is not noise — it is the reading the
    falsify-from-memory check needs most. A human who authorized twice and sees
    nothing at all cannot tell "none happened" from "the section isn't printed".
    """
    _seed(tmp_path, [])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "0 human authorization(s) recorded" in res.output


def test_report_human_says_the_reason_is_unverified(tmp_path):
    """`--reason` records what was typed, not what was true — a known tax of the
    design. The surface that displays it has to say so, or it reads as a checked
    claim."""
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:00:00Z", reason="<why>")])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "<why>" in res.output          # a placeholder is rendered verbatim, not cleaned
    assert "verified by nothing" in res.output


def test_report_human_marks_a_missing_reason_as_absent(tmp_path):
    """An authorization whose payload carried no reason must not render as a blank
    where the words go — that reads as 'approved, no comment'."""
    _seed(tmp_path, [_json.dumps({
        "event_id": "e1", "type": "review_round_authorized", "change_id": "c1",
        "timestamp": "2026-08-09T10:00:00Z",
        "actor": {"type": "human", "identifier": "someone@example.test"},
        "framework": "plain", "payload": {"reviewer": "code-reviewer"},
    })])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "(no reason recorded)" in res.output


def test_report_brief_carries_the_count(tmp_path):
    """`--brief` is the one-line form people paste; the count is the mechanism, so it
    travels with it. Still exactly one line."""
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:00:00Z")])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report", "--brief"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert res.output.strip().count("\n") == 0
    assert "1 human authorization(s)" in res.output


def test_report_json_carries_every_authorization_record(tmp_path):
    """The human view is the obligation; `--json` is where the full records live for
    anything that wants to audit them."""
    _seed(tmp_path, [
        _authorized("e1", "c1", "2026-08-09T10:00:00Z", reason="first"),
        _authorized("e2", "c2", "2026-08-09T11:00:00Z", reason="second"),
    ])
    res = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    data = _json.loads(res.output)["data"]
    assert data["authorizations_total"] == 2
    assert [a["reason"] for a in data["authorizations"]] == ["first", "second"]
    assert data["authorizations"][0]["actor"] == "someone@example.test"


def test_report_human_marks_the_timestamp_as_utc(tmp_path):
    """AUTH-002. The whole mechanism rests on a human falsifying the record from
    memory, and time is the field memory keys on. An unlabelled `10:18` read by
    someone who authorized at 18:18 local is their own act looking like a stranger's
    — the exact misreading the count exists to prevent.

    Marked rather than converted: `report` also runs in CI and in other people's
    shells, where "local" is a different answer for the same row.
    """
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:18:46Z", reason="mine")])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "2026-08-09 10:18 UTC" in res.output


def test_report_human_names_who_authorized(tmp_path):
    """AUTH-003. `derive_authorizations` already computes the actor and only `--json`
    showed it — a field derived but not read, which is the failure mode this cut
    exists to avoid.

    It matters most for the stated audience: with two people on a repo, rows with no
    name mean neither of them can falsify the ones that are not theirs.
    """
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:00:00Z", reason="mine")])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "someone@example.test" in res.output


def test_report_human_collapses_whitespace_that_would_forge_a_row(tmp_path):
    """AUTH-004. A `--reason` carrying a newline plus the row's leading spaces prints
    as two rows indistinguishable from two authorizations — a forged row in the one
    surface the design calls the mechanism.

    Tabs collapse too, for the same reason: they forge column alignment just as well
    as a newline forges a row. The verbatim text survives in `--json`, which is where
    an audit reads it; the human view owes one row per authorization.
    """
    forged = "ok\n    2026-08-09 10:00  c1  code-reviewer  someone@example.test  routine"
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:00:00Z", reason=forged)])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    rows = [ln for ln in res.output.splitlines() if "code-reviewer" in ln]
    assert len(rows) == 1, rows
    assert "ok 2026-08-09 10:00 c1 code-reviewer" in rows[0]   # collapsed, not dropped


def test_report_json_keeps_the_reason_exactly_as_typed(tmp_path):
    """The collapsing is a rendering concern only. `--json` is the audit surface and
    must still carry the bytes that were recorded."""
    forged = "ok\n    forged row"
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:00:00Z", reason=forged)])
    res = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert _json.loads(res.output)["data"]["authorizations"][0]["reason"] == forged


def test_report_human_treats_an_all_whitespace_reason_as_absent(tmp_path):
    """The AUTH-004 collapse opened this: `derive_authorizations` maps `""` to None,
    but `"   "` is a truthy string that survives derivation and collapses to `""` at
    render time — a row with a blank where the words go, which reads as 'approved,
    no comment' rather than as nothing recorded.
    """
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:00:00Z", reason="  \t \n ")])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "(no reason recorded)" in res.output


def test_report_counts_authorizations_not_funded_rounds(tmp_path):
    """AUTH-006. The derivation counts `review_round_authorized` events; an
    authorization can be recorded and never consumed — the human authorizes, then the
    round is retired or never runs. This change's own history has exactly that shape
    more than once.

    The number is right and the noun was wrong: the falsify-from-memory check keys on
    authorizing, not on rounds. `value_report.py`'s stated design law is that no
    number may claim more than it measured, so the line says what it counted.
    """
    _seed(tmp_path, [
        _authorized("e1", "c1", "2026-08-09T10:00:00Z", reason="funded a round"),
        _authorized("e2", "c1", "2026-08-09T11:00:00Z", reason="round was retired, never ran"),
    ])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "2 human authorization(s) recorded" in res.output
    # The old wording claimed each one bought a round that actually happened.
    assert "were funded by" not in res.output


def test_report_human_cannot_be_made_to_print_a_forged_row_by_any_field(tmp_path):
    """AUTH-005. The AUTH-004 hardening covered `reason` alone, but every field on the
    row is interpolated into the same single line. `actor` is the reachable vector:
    `resolve_identity`'s `SUPER_HARNESS_ACTOR` branch only strips the ends, so an
    interior newline survives into the record.

    One authorization must print as one row no matter which field carries the
    newline; `--json` keeps the bytes.
    """
    forged_actor = "me\n    2026-08-09 09:00 UTC  c1  code-reviewer  someone-else  routine"
    _seed(tmp_path, [_json.dumps({
        "event_id": "e1", "type": "review_round_authorized", "change_id": "c1\nsplit",
        "timestamp": "2026-08-09T10:00:00Z",
        "actor": {"type": "human", "identifier": forged_actor},
        "framework": "plain", "payload": {"reviewer": "code-\nreviewer", "reason": "ok"},
    })])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    rows = [ln for ln in res.output.splitlines() if ln.startswith("    2026-08-09")]
    assert len(rows) == 1, rows


def test_report_human_keeps_an_unparseable_timestamp_on_one_row(tmp_path):
    """AUTH-005, the fallback branch: `_fmt_when` returns the raw string when the
    timestamp will not parse, which puts unfiltered event text on the row."""
    _seed(tmp_path, [_json.dumps({
        "event_id": "e1", "type": "review_round_authorized", "change_id": "c1",
        "timestamp": "not-a-date\n    forged  row  here  now",
        "actor": {"type": "human", "identifier": "me"},
        "framework": "plain", "payload": {"reviewer": "code-reviewer", "reason": "ok"},
    })])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "not-a-date forged row here now  c1  code-reviewer" in res.output
    rows = [ln for ln in res.output.splitlines() if "forged" in ln]
    assert len(rows) == 1, rows


def test_report_human_row_survives_control_characters(tmp_path):
    """AUTH-008. `_one_line` collapsed whitespace, but `\\x1b` is not whitespace —
    `str.split()` splits on `str.isspace()` alone. A reason carrying `\\x1b[1A\\x1b[2K`
    moves the terminal cursor up and erases the authorization row already printed
    above it, so one row can delete another from the display.

    That is the direction `derive_authorizations` calls "the exact direction that
    hides the abuse this count exists to expose", and the design accepts that an
    agent now runs `review authorize` and supplies the reason itself.

    Fixed as a whitelist, not a third blacklist: the two previous rounds each added
    one more excluded class and each missed the next. `str.isprintable()` is False
    for C0/C1 controls, bidi overrides, NBSP and the newlines and tabs of the earlier
    rounds all at once, and True for ordinary text in any script.
    """
    _seed(tmp_path, [_authorized(
        "e1", "c1", "2026-08-09T10:00:00Z", reason="quiet\x1b[1A\x1b[2Kgone",
    )])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "\x1b" not in res.output
    assert "quiet [1A [2Kgone" in res.output      # neutralised, not dropped


def test_report_human_row_survives_a_bidi_override(tmp_path):
    """AUTH-008, the other named vector: a right-to-left override reorders the
    characters after it, so a reason can display as text it does not contain."""
    _seed(tmp_path, [_authorized(
        "e1", "c1", "2026-08-09T10:00:00Z", reason="ok‮nwonknu",
    )])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "‮" not in res.output


def test_report_human_row_keeps_non_ascii_text_intact(tmp_path):
    """The whitelist must not be a latin-1 filter. Reasons are typed by humans in
    whatever language they think in, and this repo's own records are in Chinese."""
    _seed(tmp_path, [_authorized(
        "e1", "c1", "2026-08-09T10:00:00Z", reason="计划正文没变 只是扩 scope 🙂",
    )])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "计划正文没变 只是扩 scope 🙂" in res.output


def test_report_json_keeps_control_characters_as_recorded(tmp_path):
    """Neutralising is a rendering rule, exactly as the AUTH-004 note argues. The
    audit surface still carries what was recorded."""
    reason = "quiet\x1b[1A\x1b[2Kgone"
    _seed(tmp_path, [_authorized("e1", "c1", "2026-08-09T10:00:00Z", reason=reason)])
    res = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert _json.loads(res.output)["data"]["authorizations"][0]["reason"] == reason


# --- code-only-recovery: `implementation reopen` gets the same countable trace ---


def _reopened(eid, change, ts, *, reason="fold in two minor findings",
              actor="someone@example.test"):
    return _json.dumps({
        "event_id": eid, "type": "implementation_invalidated", "change_id": change,
        "timestamp": ts, "actor": {"type": "human", "identifier": actor},
        "framework": "plain", "payload": {"reason": reason},
    })


def test_report_human_shows_reopen_count_and_every_reason(tmp_path):
    """A verb that voids a passed code review needs the surface `review authorize`
    has, for the same reason: nothing verifies the reason, so a count a human can
    falsify from memory is the only check there is."""
    _seed(tmp_path, [
        _reopened("e1", "c1", "2026-08-11T10:18:46Z", reason="fold in AUTH-005/006"),
        _reopened("e2", "c2", "2026-08-11T11:10:38Z", reason="spotted a deadlock"),
    ])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "2 frozen implementation(s) returned to editing" in res.output
    assert "already passed" not in res.output   # CR-001: half of them had not
    assert "fold in AUTH-005/006" in res.output
    assert "spotted a deadlock" in res.output
    assert "c1" in res.output and "c2" in res.output


def test_report_human_states_zero_reopens_rather_than_omitting_the_line(tmp_path):
    """A human has to be able to tell "none recorded" from "the section isn't shown"."""
    _seed(tmp_path, [])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert res.exit_code == 0
    assert "0 frozen implementation(s) returned to editing" in res.output


def test_report_reopen_row_survives_a_newline_in_any_field(tmp_path):
    """One reopen prints as one row: a reason carrying a newline would otherwise
    forge a second, and the count would stop matching what the reader can see."""
    _seed(tmp_path, [
        _reopened("e1", "c1", "2026-08-11T10:18:46Z", reason="line one\nline two"),
    ])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    rows = [ln for ln in res.output.splitlines() if "line one" in ln or "line two" in ln]
    assert len(rows) == 1
    assert "line one line two" in rows[0]


def test_report_reopen_without_a_reason_reads_as_absent(tmp_path):
    _seed(tmp_path, [_json.dumps({
        "event_id": "e1", "type": "implementation_invalidated", "change_id": "c1",
        "timestamp": "2026-08-11T10:18:46Z",
        "actor": {"type": "human", "identifier": "someone@example.test"},
        "framework": "plain", "payload": {},
    })])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "report"],
                             catch_exceptions=False)
    assert "(no reason recorded)" in res.output


def test_report_json_carries_every_reopen_record(tmp_path):
    _seed(tmp_path, [
        _reopened("e1", "c1", "2026-08-11T10:18:46Z"),
        _reopened("e2", "c1", "2026-08-11T11:10:38Z", reason="and again"),
    ])
    res = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "report"],
                             catch_exceptions=False)
    data = _json.loads(res.output)["data"]
    assert data["reopens_total"] == 2
    # Not deduped: two identical acts are two acts, and collapsing them would
    # understate in the direction that hides the abuse the count exists to expose.
    assert [r["reason"] for r in data["reopens"]] == [
        "fold in two minor findings", "and again",
    ]
