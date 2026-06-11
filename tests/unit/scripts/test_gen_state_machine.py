from scripts.gen_state_machine import NOOP_EVENTS, build_rows, render_markdown


def test_transition_rows_exclude_self_loops():
    rows = build_rows()
    assert all(r.frm != r.to for r in rows)         # no X --e--> X
    assert any(r.frm is None and r.to == "INTENT_DECLARED" for r in rows)  # start row


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


def test_noop_events_match_informational_set():
    from super_harness.core.transitions import _INFORMATIONAL
    assert set(NOOP_EVENTS) == _INFORMATIONAL
