# P0 gate-soundness guards: approve-with-fail, state.yaml shape, event timestamp shape

**Date**: 2026-07-02 · **Change**: `2026-07-02-p0-gate-soundness-v3` (v1 abandoned after
round-1 plan review; v2 abandoned mid-implementation when the events.py edit tripped
d-events-append-only's tier-2 anchor — the reconcile rewrites the decision file, which
must therefore be in scope; no redeclare CLI, restart-with-new-slug is the established
pattern)
· **Source**: fresh-eyes review 2026-07-02 (findings F1/F2/F3, adversarial pass by
Codex, verified in-loop) · **Plan review round 1**: Codex + independent Claude
subagent, both REVISE — all revisions incorporated below.

Three small soundness holes in the gate chain, bundled because they share one
theme: a mechanical guard the harness already promises but does not enforce.

## F1 — `review approve` accepts a verdict that says "fail"

`cli/review.py::_validate_code_review_verdict` checks coverage, freshness
(bundle digest), prior-finding disposal, and dead doc-refs — but never checks
the checklist statuses themselves. A verdict with `checklist: [{status: fail}]`
(plus the findings that `parse_verdict_file` requires when any item fails) sails
through `review approve` and emits `code_review_passed`; the merge attestation
only needs that milestone (`engineering/attestation.py` keys on the event, never
the inlined verdict), so a PR whose own review record says FAIL passes the merge
gate. The **plan-reviewer approve branch has the same hole**: an optional
`--verdict-file` with a fail item is inlined un-checked (review.py approve path).

This was a **known slice-1 gap** (recorded in
`2026-06-23-auto-review-hardening-slice2-design.md` §11 as "note only") that
never made it into OPEN-ITEMS.

**Fix**: a pure helper `failing_items(verdict) -> list[str]` in
`core/review_verdict.py` (the shape module owns checklist accessors); **both
approve branches** (code-reviewer required verdict + plan-reviewer optional
verdict) reject with `EXIT_VALIDATION` when it is non-empty, pointing at
`review reject`. The check runs immediately after parse (cheapest first, before
any git work).

**Deliberately NOT added**: an override flag. "Approve with nits" stays
expressible — findings may accompany an approve as long as every checklist item
is `pass`/`na`. A `fail` item means the reviewer's own record says the change
is not approvable; the honest paths are `review reject`, or fix and re-review
(`review skip --override --reason` remains the explicit, audited escape hatch
for skipping review entirely). An override flag on approve would recreate the
agent-channel escape hatch class removed in #51/#52. The same verdict shape must
remain VALID for `review reject`, so the check lives in the approve CLI path,
not in `parse_verdict_file`.

**Doc surfaces updated with the new refusal reason** (round-1 review catch):
`scripts/gen_cli_reference.py` `_EXIT_CODES["review approve"]` (hand-maintained
map, not --help-derived) → regenerate `docs/cli-reference.md`; the review
protocol wording in `adapters/agent/claude_code.py::agents_md_subsection`
("The approval is refused if …") → regenerate `AGENTS.md` via `sync --agents-md`.

## F2 — a non-mapping state.yaml crashes the gate hot path

`core/active_change.py::read_active_change_id` wraps `yaml.safe_load` in
`try/except` but calls `data.get("changes")` OUTSIDE it. A state.yaml that is
valid YAML but a **non-empty non-mapping** (a scalar, `[a]`) raises
`AttributeError` (an empty list `[]` is falsy and already coalesced by the
`or {}`). In Claude Code shim mode the uncaught exception exits 1, which Claude
Code treats as a NON-blocking error → **the gate silently fails open**; in
generic positional mode exit 1 means BLOCK → the same crash is a spurious
fail-closed block. CLI paths (`verify`, `done`, `change resume`) stack-trace.
The module docstring already promises "missing/unparseable → None".

**Fix**: `isinstance(data, dict)` guard after the load — the same non-mapping
guard `daemon/hot_state.py` and `core/state_yaml.py` already have;
`read_active_change_id` is the outlier (second member of the #62 PyYAML family).

## F3 — a non-string event timestamp crashes the reducer

`parse_event_line` (`core/events.py`) validates shape but not the `timestamp`
type: `"timestamp": 123` parses fine, then the reducer's clock-drift check calls
`.replace(...)` on an int → `AttributeError` (only `ValueError` is caught),
breaking state rebuild, verification baselines, and attestation on one
malformed-but-valid-JSON line — violating the reducer's "TOLERANT, never raise"
contract. The crash can also fire one line LATER than the malformed event (the
int is stored into `last_ts` and detonates as `prev_ts` on the next event).

**Fix, two thin layers**:
- **Read path (root)**: `timestamp` must be a `str` is a SHAPE property, so it
  belongs in `parse_event_line` next to the existing actor/framework shape
  checks. All 13 call sites are tolerant readers (catch `EventSchemaError`,
  warn+skip) or strict-by-design (`state verify` reports the line) — verified in
  round-1 review; the malformed line flips from "crash the reducer" to
  "skipped/reported".
- **Write path**: `EventWriter.emit` does NOT round-trip `parse_event_line`
  (it serializes the in-memory dataclass directly), so the parser check alone
  cannot keep garbage off disk. Add a type-only guard in `emit`
  (non-`str` timestamp → `EmitPreconditionError`), applied regardless of
  `skip_validation` (it is shape, not a transition precondition). Empty string
  stays legal: the sensor dispatcher builds `Event(timestamp="")` in-process and
  stamps it unconditionally before `emit`, so `""` never reaches disk today;
  the guard is type-only to keep that contract untouched.

The reducer itself needs no change — after the parse fix both `prev_ts` and
`ev.timestamp` are guaranteed `str` within a replay, so broadening its `except`
would be dead defense (round-1 consensus). Regression tests pin both crash
shapes ("int timestamp on second event" and "int timestamp then valid event").

## Scope / non-goals

Only the three guards above + their tests + the two derived doc surfaces. The
sibling findings from the same review (writer TOCTOU F4, verification
`run_check` process-group kill F5, daemon direction F7, layering F9) are
separate changes — registered in the review backlog, not expanded here.
