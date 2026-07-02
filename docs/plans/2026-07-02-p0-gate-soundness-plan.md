# Implementation plan: 2026-07-02-p0-gate-soundness-v3

Design: `2026-07-02-p0-gate-soundness-design.md`. TDD throughout (red → green
per guard). Tier hint: Normal. Incorporates round-1 plan review (Codex +
Claude subagent, both REVISE → all items resolved; v1 slug abandoned because
scope expansion has no redeclare CLI).

## Task 1 — F1: reject approve on a failing checklist (both reviewer branches)

1. **Test (red)** — `tests/unit/core/test_review_verdict.py`:
   - `failing_items` returns the `item` names whose `status == "fail"`, in
     checklist order; empty list when all pass/na.
   - `tests/unit/cli/test_review_verdict_gate.py`:
     - code-reviewer approve with a `fail` checklist item (+ required finding)
       exits `EXIT_VALIDATION`, names the failing item(s), points at
       `review reject`;
     - plan-reviewer approve with an inlined `--verdict-file` containing a
       `fail` item exits `EXIT_VALIDATION` the same way;
     - the same verdict file remains accepted by `review reject`.
2. **Implement (green)** —
   - `core/review_verdict.py`: pure `failing_items(verdict) -> list[str]`.
   - `cli/review.py`: in `_validate_code_review_verdict` immediately after
     `parse_verdict_file` (before any git work), and in the plan-reviewer
     approve branch where the optional verdict is parsed → `format_error` +
     `sys.exit(EXIT_VALIDATION)`.
3. **Doc surfaces** — `scripts/gen_cli_reference.py` `_EXIT_CODES["review
   approve"]`: add the failing-checklist reason; regenerate
   `docs/cli-reference.md`. `adapters/agent/claude_code.py`
   `agents_md_subsection` review-protocol wording: add "or any checklist item is
   `fail` (record it with `review reject` instead)"; regenerate `AGENTS.md` via
   `super-harness sync --agents-md`.

## Task 2 — F2: tolerate non-mapping state.yaml

1. **Test (red)** — `tests/unit/core/test_active_change.py`:
   `read_active_change_id` returns `None` (no raise) for state.yaml containing a
   **non-empty list** (`- a`) and a bare scalar (`foo`); pin existing behavior
   for `[]`/empty file (already `None` via the `or {}` coalesce — not expected
   red, behavior pin only).
2. **Implement (green)** — `core/active_change.py::read_active_change_id`:
   `if not isinstance(data, dict): return None` after the load (mirrors
   `hot_state.py` / `state_yaml.py` precedent).

## Task 3 — F3: `timestamp` must be a string (read + write layers)

1. **Test (red)** —
   - `tests/unit/core/test_events.py`: `parse_event_line` raises
     `EventSchemaError` for `"timestamp": 123` / `null` / list; empty string
     `""` still parses.
   - `tests/unit/core/test_reducer.py`: two regression shapes, neither raises —
     (a) second event of a change has an int timestamp → that line is skipped,
     state reflects only valid events; (b) int-timestamp event FOLLOWED by a
     valid event (the `prev_ts` detonation path).
   - `tests/unit/core/test_writer.py`: `emit` with `Event(timestamp=123)` (and
     a `datetime`) raises `EmitPreconditionError` even with
     `skip_validation=True`; `timestamp=""` still writes.
2. **Implement (green)** —
   - `core/events.py::parse_event_line`: `isinstance(obj["timestamp"], str)`
     shape check beside the actor/framework checks.
   - `core/writer.py::EventWriter.emit`: type-only guard before the
     validation/serialize step, independent of `skip_validation`.

## Task 4 — full verification

- `.venv/bin/python -m pytest` full suite green.
- `PYTHONPATH=src lint-imports --config .importlinter --no-cache` KEPT.
- `super-harness decision check` clean (no decisions touched; sanity only).
- `super-harness sync --check` clean after AGENTS.md/cli-reference regen.

## Declared scope (attest coverage)

- `docs/plans/2026-07-02-p0-gate-soundness-design.md`
- `docs/plans/2026-07-02-p0-gate-soundness-plan.md`
- `src/super_harness/cli/review.py`
- `src/super_harness/core/review_verdict.py`
- `src/super_harness/core/active_change.py`
- `src/super_harness/core/events.py`
- `src/super_harness/core/writer.py`
- `src/super_harness/core/reducer.py` (regression-test target; source expected untouched)
- `src/super_harness/adapters/agent/claude_code.py`
- `AGENTS.md`
- `scripts/gen_cli_reference.py`
- `docs/cli-reference.md`
- `tests/unit/cli/test_review.py`
- `tests/unit/cli/test_review_verdict_gate.py`
- `tests/unit/core/test_review_verdict.py`
- `tests/unit/core/test_active_change.py`
- `tests/unit/core/test_events.py`
- `tests/unit/core/test_reducer.py`
- `tests/unit/core/test_writer.py`
- `docs/decisions/d-events-append-only.md` (tier-2 anchor on events.py; the
  reconcile rewrites its frontmatter — v2 was abandoned for missing exactly this)
- `.harness/attestations/2026-07-02-p0-gate-soundness-v3.jsonl`

## Risks / notes

- F1 tightens a CLI verb: no existing test approves with a fail-status verdict
  (round-1 grep: fail fixtures exist only in pure parse tests), and "approve
  with nits" (findings + all-pass checklist) stays expressible.
- F3 read-side: tool-written events always carry str timestamps; hand-edited /
  legacy lines flip from "crash the reducer" to "warn + skip" — the documented
  tolerant contract. Write-side guard is type-only; the dispatcher's
  blank-then-stamp flow is untouched.
