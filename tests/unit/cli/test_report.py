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
