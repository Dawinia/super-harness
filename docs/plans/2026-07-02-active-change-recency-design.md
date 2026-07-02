# Design — active-change resolution: oldest → most-recent (fix stale-change gate hijack)

Date: 2026-07-02. Fixes the acute half of HG-STALE-MERGED-CHANGE (dogfood-surfaced in #61). GC deferred (see below).

## Problem
`on-merge` (READY_TO_MERGE→ARCHIVED) is manual + local-only + has NO CI automation; state.yaml/events.jsonl are gitignored (per-machine). A merged PR whose `on-merge` was skipped locally leaves its change stuck non-terminal. The active-change resolver (`active_change.read_active_change_id`; `status.py` has a DUPLICATE loop) picks the FIRST/OLDEST non-terminal change — a "deliberately dumb v0.1 placeholder". So the oldest stuck change (READY_TO_MERGE = "no edits") HIJACKS the gate and freezes all edits. #61 opened with 5 such stale changes; the oldest froze the first edit until diagnosed. This is a harness self-inflicted footgun, NOT a value-bleed (same category as #60 hygiene) — strict value-bleed count stays 1.

## Fix (root, pure, no git): resolver picks the MOST-RECENTLY-active non-terminal
- New pure helper `core/active_change.pick_active_change(candidates)`: given (change_id, current_state, last_event_at) triples, return the non-terminal change with the max PARSED last_event_at, tie-break by change_id; None if none. The single definition of "which change is active".
- **Parse timestamps, don't compare strings** (per plan-review; the reducer deliberately avoids string-compare, and this feeds the gate hot path): a small `_parse_ts` handles both `Z` (what `utc_now_iso` emits) and `+00:00`; empty/malformed → `datetime.min` (sorts LOWEST); and a tz-LESS ISO string (parses OK to a NAIVE datetime — NOT a ValueError) is normalized to aware UTC, else `max()` vs the aware entries would `TypeError` on the gate hot path (plan-review catch). Uniform in practice, robust to legacy/adopter events.
- `read_active_change_id` builds triples from state.yaml and calls `pick_active_change`. `status.py`'s duplicate first-non-terminal loop is replaced with the same call (kill the drift — same lesson as #61 `has_runnable_check`); its now-unused `TERMINAL_STATES` import is removed (ruff F401).
- Effect: a freshly-started change has the newest `last_event_at` → always "active"; a stale June change can never hijack the gate. NO-OP when there is a single non-terminal change (only one to pick); only changes behavior in the pile-up case, where most-recent is unambiguously right. Used by gate/status/resume/done — all improve. No git, no latency change (pure state.yaml read, same as today).

## GC deferred (was in scope, pulled after plan-review)
The planned `change gc` relied on "branch merged-into-main / gone" detection, which BOTH reviewers killed: branch naming is OPTIONAL (slug ≠ branch per AGENTS.md), so "branch gone ≈ merged" would false-classify real in-progress changes and emit false `merged` events; and git-absent / non-git-repo were unguarded (mass false-positives + breaks the SessionStart fail-open contract). The ROOT FIX alone removes the ACUTE harm (the hijack/freeze); leftover stale changes now merely accumulate as harmless clutter in `change list`. GC is deferred to a follow-up that will use a SOUND, branch-independent signal: the change's attestation file `.harness/attestations/<slug>.jsonl` is slug-named and committed before merge, so "present on the base branch (main) = merged" is reliable and branch-name-agnostic. Registered in OPEN-ITEMS as the GC follow-up.

## Boundary decisions
- Timestamp: parse (Z/+00:00), malformed→min→sorts lowest; tie-break change_id (date-prefixed slugs → higher id ≈ later, documented).
- DRY: `pick_active_change` is THE definition; both active_change.py and status.py use it.
- No CLI-surface change (active_change internal; status output semantics change, not its signature/exit codes) → NO cli-reference/AGENTS/sync churn.
- Doc-drift sweep: ALL current-mechanism references to "first/oldest non-terminal / first active" are updated to "most-recent active" — the edited files themselves (status.py module docstring + command help → the generated cli-reference.md line, regenerated via `doc check --fix`) AND cli/verify.py, cli/change.py, daemon/hook_entry.py + daemon test comments. Historical design docs are left as point-in-time records. Existing tests: `test_status_default_first_active` (integration/cli/test_status.py) starts TWO non-terminal changes and asserts the OLDEST — it BREAKS and must be updated to assert the most-recent (rename + docstring). Stale "first non-terminal" COMMENTS in hook_entry/change/done/verify tests (single-change fixtures — functionally green) are updated for accuracy (anti doc-drift). The recent_events "oldest→newest" comments are a DIFFERENT feature (event listing) — untouched.

## Honest framing
NOT a #45-style value-bleed: fixes a harness self-inflicted footgun (the gate froze its own operator via stale state), the opposite of catching a real governed-artifact violation. Same category as #60 (hygiene). Strict value-bleed count stays 1. Genuine dogfood win (real lifecycle surfaced it; hits any adopter).

## Testing
- `pick_active_change` pure (no clock/git): most-recent wins; tie-break by id; None when all terminal; single non-terminal returned; **mixed `Z`/`+00:00` sort chronologically**; empty/malformed sorts lowest.
- Update `test_status_default_first_active` → asserts the most-recent of two non-terminal changes.
- Regression: `read_active_change_id` single-non-terminal unchanged; hook/resume/done/verify still green (comment-only edits).

## Scope / commit (single commit — small, cohesive)
`core/active_change.py`, `cli/status.py`, `tests/unit/core/test_active_change.py` (new), `tests/integration/cli/test_status.py`, `tests/integration/daemon/test_hook_entry.py`, `tests/integration/cli/test_change.py`, `tests/unit/cli/test_done.py`, `tests/unit/cli/test_verify.py`, design + plan docs, attestation. No decisions.py change → no reconcile tax; no @decision anchor on touched files (verified in plan-review).
