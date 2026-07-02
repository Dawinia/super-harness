# Active-change recency resolver — Implementation Plan

Goal: make "active change" = the MOST-RECENTLY-active non-terminal change (not the oldest), so a stale merged-but-not-archived change can never hijack the gate. Fixes the acute half of HG-STALE-MERGED-CHANGE (GC deferred).

Architecture: pure `pick_active_change(candidates)` (parses `last_event_at`, no git) is THE definition of "active change"; `active_change.read_active_change_id` + `cli/status.py` both use it (kill a duplicate). Single commit.

## File Structure
- core/active_change.py — add `_parse_ts` + pure `pick_active_change`; `read_active_change_id` uses it; docstring updated.
- cli/status.py — replace its duplicate first-non-terminal loop with `pick_active_change`; drop now-unused `TERMINAL_STATES` import.
- tests/unit/core/test_active_change.py (NEW) — pick_active_change unit tests.
- tests/integration/cli/test_status.py — update `test_status_default_first_active` (breaks: asserts oldest).
- tests/integration/daemon/test_hook_entry.py, tests/integration/cli/test_change.py, tests/unit/cli/test_done.py, tests/unit/cli/test_verify.py — update stale "first non-terminal" comments/docstrings (single-change fixtures; functionally green).

## Task 1: `_parse_ts` + `pick_active_change` (core/active_change.py) — pure, tested
Test (tests/unit/core/test_active_change.py):
    from super_harness.core.active_change import pick_active_change
    def test_most_recent_wins():
        assert pick_active_change([
            ("a","READY_TO_MERGE","2026-06-10T00:00:00Z"),
            ("b","IMPLEMENTATION_IN_PROGRESS","2026-07-02T00:00:00Z"),
        ]) == "b"
    def test_skips_terminal():
        assert pick_active_change([
            ("a","IMPLEMENTATION_IN_PROGRESS","2026-07-02T00:00:00Z"),
            ("b","ARCHIVED","2026-07-03T00:00:00Z"),
        ]) == "a"
    def test_none_when_all_terminal():
        assert pick_active_change([("a","ARCHIVED","2026-07-03T00:00:00Z")]) is None
    def test_tiebreak_by_id():
        assert pick_active_change([
            ("a","INTENT_DECLARED","2026-07-02T00:00:00Z"),
            ("b","INTENT_DECLARED","2026-07-02T00:00:00Z"),
        ]) == "b"
    def test_mixed_ts_formats_sort_chronologically():
        # Z vs +00:00 must compare as the SAME instant class -> parsed, not string-compared
        assert pick_active_change([
            ("older","INTENT_DECLARED","2026-07-02T00:00:00+00:00"),
            ("newer","INTENT_DECLARED","2026-07-02T09:00:00Z"),
        ]) == "newer"
    def test_malformed_ts_sorts_lowest():
        assert pick_active_change([
            ("good","INTENT_DECLARED","2026-07-02T00:00:00Z"),
            ("bad","INTENT_DECLARED","not-a-timestamp"),
        ]) == "good"
    def test_naive_ts_normalized_not_crash():
        # tz-less ISO parses to NAIVE -> must be normalized (else max() TypeErrors vs aware)
        assert pick_active_change([
            ("a","INTENT_DECLARED","2026-07-02T00:00:00"),
            ("b","INTENT_DECLARED","2026-07-02T09:00:00Z"),
        ]) == "b"
    def test_single_non_terminal_returned():
        assert pick_active_change([("only","INTENT_DECLARED","2026-07-02T00:00:00Z")]) == "only"
Impl (core/active_change.py):
    from collections.abc import Iterable
    from datetime import datetime, timezone
    def _parse_ts(s: str) -> datetime:
        """Parse an ISO-8601 timestamp for ORDERING. Handles `Z` (what utc_now_iso emits)
        and `+00:00`; empty/malformed -> datetime.min (UTC) so it sorts LOWEST (never wins
        unless everything is malformed). Parse, not string-compare — mixed forms misfire
        lexically (the reducer avoids string-compare for the same reason)."""
        if not s:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
        # a tz-LESS ISO string parses OK to a NAIVE datetime (no ValueError); normalize
        # to aware UTC or max() vs the aware entries raises TypeError on the gate hot path.
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    def pick_active_change(candidates: Iterable[tuple[str, str, str]]) -> str | None:
        """THE definition of 'which change is active': the non-terminal change with the
        latest last_event_at (parsed), ties broken by change_id. None if none. PURE."""
        from super_harness.core.state import TERMINAL_STATES
        live = [(cid, at) for cid, st, at in candidates if st not in TERMINAL_STATES]
        if not live:
            return None
        return max(live, key=lambda t: (_parse_ts(t[1]), t[0]))[0]
Run: pytest tests/unit/core/test_active_change.py -q (PASS).

## Task 2: `read_active_change_id` uses the picker + docstring (core/active_change.py)
Replace the tail (keep the state.yaml load + guards) with:
        candidates = (
            (str(cid), r.get("current_state", ""), r.get("last_event_at", ""))
            for cid, r in changes.items() if isinstance(r, dict)
        )
        return pick_active_change(candidates)
(drop the old `from ...state import TERMINAL_STATES` + the for-loop — pick_active_change owns that.)
Module docstring + read_active_change_id docstring: change "first non-terminal change" → "the MOST RECENTLY active non-terminal change (by last_event_at; was 'first/oldest', a v0.1 placeholder that let a stale merged change hijack the gate)".
Run: pytest tests/unit -q -k active_change (PASS).

## Task 3: `cli/status.py` uses the picker; fix F401; update the breaking test
- Replace status.py:104-110:
    active = [cs for cs in derived.values() if cs.current_state not in TERMINAL_STATES]
    target = [active[0]] if active else []
  with:
    from super_harness.core.active_change import pick_active_change
    active_id = pick_active_change(
        (cid, cs.current_state, cs.last_event_at) for cid, cs in derived.items()
    )
    target = [derived[active_id]] if active_id else []
  and DELETE the stale TODO comment (lines ~105-108).
- Fix status.py MODULE docstring (lines ~4-18: "first active by events.jsonl insertion order",
  "first active is a deliberate placeholder") AND the command help (line 55:
  `"""Show current state for one change, all changes, or the first active change."""`) ->
  "the MOST RECENTLY active change". (The cli-reference.md "first active change" line is
  GENERATED from this command docstring — regenerated in Task 5 via `doc check --fix`.)
- Remove the now-unused `TERMINAL_STATES` import from status.py (ruff F401 — it was used ONLY in the deleted loop; grep to confirm no other use before deleting).
- Update tests/integration/cli/test_status.py `test_status_default_first_active`:
  rename -> `test_status_default_most_recent_active`; keep the two `_start` calls; change the
  assertion + docstring from `ch-first` to `ch-second` (the later last_event_at); explain "most
  recent active, not oldest". ALSO update the module-docstring test index (test_status.py line ~14)
  that still names `test_status_default_first_active` / "first active".
Run: pytest tests/integration/cli/test_status.py -q (PASS); ruff check src/super_harness/cli/status.py (clean).

## Task 4: sweep ALL stale "first/oldest non-terminal / first active" wording (anti doc-drift)
Comment/docstring-only edits — "first/oldest non-terminal" (as the ACTIVE-CHANGE MECHANISM) ->
"most-recent non-terminal (active)". Single-change fixtures → functionally green. Sweep (verified
comprehensive by grep):
  src: cli/verify.py (~"first non-terminal via read_active_change_id"), cli/change.py (resume
       no-slug ~"first non-terminal change, via"), daemon/hook_entry.py (~"first non-terminal change").
  tests: integration/daemon/test_hook_entry.py, integration/cli/test_change.py (resume-no-slug lines
       ONLY — do NOT touch the recent_events "oldest→newest" comments, a different feature),
       unit/cli/test_done.py, unit/cli/test_verify.py, integration/daemon/test_latency.py,
       integration/daemon/conftest.py.
  EXCLUDE docs/plans/2026-06-03-self-host-hard-gate-design.md (historical point-in-time record — not rewritten).
Run: `grep -rns "first non-terminal\|first active" src/ tests/` returns ONLY the historical design
doc + any recent_events lines; pytest for the touched test files PASS (comment-only).

## Task 5: full verify + commit
- `super-harness doc check --fix` (regenerates docs/cli-reference.md from the fixed status docstring); confirm the "first active change" line is gone.
- `pytest -q` all green; `ruff check` + `mypy src` clean; `lint-imports` core-is-base KEPT; `decision check` + `doc check` clean.
- Write design + plan docs to docs/plans/. Commit (single):
  "active-change resolution: most-recent non-terminal, not oldest (fix stale-change gate hijack)"

## Self-review
- pick_active_change pure + parses ts (mixed Z/+00:00, malformed→lowest) → robust for the gate hot path; DRY (status + active_change share it). NO-OP single-change. GC deferred (unsound branch-detection; attestation-signal follow-up in OPEN-ITEMS). Existing breaking test updated (test_status), stale comments fixed. No CLI-surface change → no sync/cli-reference. No decisions.py change / no @decision anchor → no reconcile tax.
