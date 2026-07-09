from scripts.gen_state_machine import NOOP_EVENTS, build_rows, render_markdown


def test_transition_rows_exclude_global_noop_self_loops():
    rows = build_rows()
    assert not any(r.event == "verification_passed" for r in rows)
    assert not any(r.event == "intent_declared" and r.frm is not None for r in rows)
    assert any(r.frm is None and r.to == "INTENT_DECLARED" for r in rows)  # start row


def test_transition_rows_include_state_specific_self_loops():
    rows = build_rows()
    self_loops = {
        r.frm for r in rows
        if r.event == "review_verdict_recorded" and r.frm == r.to
    }
    assert self_loops == {
        "AWAITING_PLAN_REVIEW",
        "AWAITING_CODE_REVIEW",
        "CODE_REVIEW_REJECTED",
    }


def test_rows_are_deterministically_sorted():
    assert build_rows() == build_rows()             # stable
    keys = [(("" if r.frm is None else r.frm), r.event) for r in build_rows()]
    assert keys == sorted(keys)


def test_known_count_is_derived_not_hardcoded():
    rows = build_rows()
    assert 40 <= len(rows) <= 60
    assert "verification_passed" in NOOP_EVENTS     # informational = no-op


def test_render_is_deterministic_and_has_header():
    out = render_markdown()
    assert out == render_markdown()
    assert out.endswith("\n") and "(start)" in out
    assert "| `AWAITING_PLAN_REVIEW` | `review_verdict_recorded` | `AWAITING_PLAN_REVIEW` |" in out


def test_noop_events_match_informational_set():
    from super_harness.core.transitions import _INFORMATIONAL
    assert set(NOOP_EVENTS) == _INFORMATIONAL
