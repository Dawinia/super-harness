"""Unit tests for `pick_active_change` — the pure "which change is active"
definition (most-recent non-terminal, robust timestamp parsing) — and for
`read_active_change_id`'s state.yaml shape tolerance (gate hot path: a
malformed cache must resolve to None, never raise)."""
from datetime import datetime, timezone
from pathlib import Path

from super_harness.core.active_change import pick_active_change, read_active_change_id


def test_most_recent_wins():
    assert pick_active_change([
        ("a", "READY_TO_MERGE", "2026-06-10T00:00:00Z"),
        ("b", "IMPLEMENTATION_IN_PROGRESS", "2026-07-02T00:00:00Z"),
    ]) == "b"


def test_skips_terminal():
    # newer but terminal -> skipped; the older non-terminal wins
    assert pick_active_change([
        ("a", "IMPLEMENTATION_IN_PROGRESS", "2026-07-02T00:00:00Z"),
        ("b", "ARCHIVED", "2026-07-03T00:00:00Z"),
    ]) == "a"


def test_none_when_all_terminal():
    assert pick_active_change([
        ("a", "ARCHIVED", "2026-07-03T00:00:00Z"),
        ("b", "ABANDONED", "2026-07-04T00:00:00Z"),
    ]) is None


def test_none_when_empty():
    assert pick_active_change([]) is None


def test_single_non_terminal_returned():
    assert pick_active_change([("only", "INTENT_DECLARED", "2026-07-02T00:00:00Z")]) == "only"


def test_tiebreak_by_change_id():
    # identical timestamps -> deterministic tie-break by change_id (higher wins)
    assert pick_active_change([
        ("a", "INTENT_DECLARED", "2026-07-02T00:00:00Z"),
        ("b", "INTENT_DECLARED", "2026-07-02T00:00:00Z"),
    ]) == "b"


def test_mixed_z_and_offset_sort_chronologically():
    # `Z` vs `+00:00` must be parsed (same instant class), not string-compared
    assert pick_active_change([
        ("older", "INTENT_DECLARED", "2026-07-02T00:00:00+00:00"),
        ("newer", "INTENT_DECLARED", "2026-07-02T09:00:00Z"),
    ]) == "newer"


def test_naive_ts_normalized_not_crash():
    # a tz-less ISO string parses to a NAIVE datetime (not a ValueError); it must
    # be normalized to aware UTC, else max() vs the aware entries raises TypeError.
    assert pick_active_change([
        ("naive", "INTENT_DECLARED", "2026-07-02T00:00:00"),
        ("aware_newer", "INTENT_DECLARED", "2026-07-02T09:00:00Z"),
    ]) == "aware_newer"


def test_malformed_ts_sorts_lowest():
    assert pick_active_change([
        ("good", "INTENT_DECLARED", "2026-07-02T00:00:00Z"),
        ("bad", "INTENT_DECLARED", "not-a-timestamp"),
    ]) == "good"


def test_empty_ts_sorts_lowest():
    assert pick_active_change([
        ("good", "INTENT_DECLARED", "2026-07-02T00:00:00Z"),
        ("empty", "INTENT_DECLARED", ""),
    ]) == "good"


def test_datetime_value_from_yaml_does_not_crash():
    # PyYAML loads an UNQUOTED ISO timestamp as a datetime (aware or naive); the
    # resolver must accept it, not assume str (else `.replace("Z",...)` TypeErrors
    # on the gate hot path). Both aware and naive datetimes are handled + ordered.
    aware = datetime(2026, 7, 2, 9, 0, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 7, 2, 0, 0, 0)  # tz-less, as PyYAML may load
    assert pick_active_change([
        ("older", "INTENT_DECLARED", naive),
        ("newer", "INTENT_DECLARED", aware),
    ]) == "newer"


def test_none_and_nonstr_ts_sort_lowest():
    assert pick_active_change([
        ("good", "INTENT_DECLARED", "2026-07-02T00:00:00Z"),
        ("none", "INTENT_DECLARED", None),
        ("int", "INTENT_DECLARED", 12345),
    ]) == "good"


def _state_yaml(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".harness" / "state.yaml").write_text(text)
    return tmp_path


# F2 (review 2026-07-02): valid-YAML-but-non-mapping state.yaml must resolve to
# None, not AttributeError — this feeds the PreToolUse hook (a crash there exits
# 1, which Claude Code treats as a NON-blocking error → silent fail-open).

def test_read_tolerates_nonempty_list_state_yaml(tmp_path: Path):
    assert read_active_change_id(_state_yaml(tmp_path, "- a\n- b\n")) is None


def test_read_tolerates_scalar_state_yaml(tmp_path: Path):
    assert read_active_change_id(_state_yaml(tmp_path, "just a string\n")) is None


def test_read_tolerates_empty_list_state_yaml(tmp_path: Path):
    # behavior pin: [] is falsy → already coalesced by `or {}`
    assert read_active_change_id(_state_yaml(tmp_path, "[]\n")) is None


def test_read_tolerates_empty_state_yaml(tmp_path: Path):
    # behavior pin: safe_load("") is None → `or {}`
    assert read_active_change_id(_state_yaml(tmp_path, "")) is None
