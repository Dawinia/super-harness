# Record Gate Blocks (Stage 2 — the missing prevention signal) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record every pre-tool-use gate BLOCK as durable, local, best-effort telemetry so `super-harness report` can finally show prevention-success — the gate *working* — not only `gate_bypassed` (the gate *defeated*).

**Architecture:** The pre-tool-use gate is a pure query (gates never emit events); the hook dispatcher records a BLOCK **out-of-band** (as the gate contract already prescribes) into a new append-only log `.harness/gate-blocks.jsonl`. This is **observability telemetry**, NOT a lifecycle event: it never drives state, never gates anything, and lives *outside* `events.jsonl` so the gate's fail-open + fast hot-path contract is untouched. The write is best-effort and **never raises** — a failed write must not flip a real BLOCK into an ALLOW (the Claude shim treats an uncaught hook exception as exit 1 = non-blocking = fail-open). Raw records are append-only and duplicate on agent retries; the honest count is computed at **read time** in the report (dedup by `(change_id, file, state)`), keeping the hot path a pure append.

**Tech Stack:** Python 3.10+, stdlib `json`, existing `core/` primitives (`utc_now_iso`, `paths`), pytest.

**Non-goals (YAGNI):** No cross-machine / team aggregation (log is per-repo local). No log rotation/GC (append-only local JSON lines; a GC is a separate concern if it ever matters). No recording of ALLOW decisions (every edit -> huge volume, zero signal). No new event type; nothing enters `events.jsonl`. No change to `gate check` CLI recording (diagnostic path, not the live agent path — would pollute the "real block" count).

**Scope (files):**
- Create: `src/super_harness/core/gate_blocks.py`, `tests/unit/core/test_gate_blocks.py`
- Modify: `src/super_harness/core/paths.py` (+`gate_blocks_path`)
- Modify: `src/super_harness/daemon/hook_entry.py` (record at BLOCK point in `_decide`)
- Modify: `src/super_harness/engineering/value_report.py` (+`edits_blocked` field + count)
- Modify: `src/super_harness/cli/report.py` (render + retire the Stage-2 footnote debt)
- Modify: `src/super_harness/engineering/gitignore_injector.py` (+canonical path) + its test
- Tests: `tests/integration/daemon/test_hook_entry.py`, `tests/unit/engineering/test_value_report.py`, `tests/unit/cli/test_report.py`
- The repo's own `.gitignore` (regen via `sync`) so the dogfood run never commits the log
- This plan doc

---

## Task 1: `gate_blocks_path` helper in `core/paths.py`

**Files:**
- Modify: `src/super_harness/core/paths.py` (after `events_path`)
- Test: `tests/unit/core/test_paths.py` (if it exists; else fold into Task 2's test file)

**Step 1: Write the failing test**

    from pathlib import Path
    from super_harness.core.paths import gate_blocks_path

    def test_gate_blocks_path_is_under_harness(tmp_path: Path) -> None:
        assert gate_blocks_path(tmp_path) == tmp_path / ".harness" / "gate-blocks.jsonl"

**Step 2: Run — FAIL** (ImportError: cannot import name `gate_blocks_path`).

**Step 3: Implement** (mirror `events_path`)

    def gate_blocks_path(root: Path) -> Path:
        """`.harness/gate-blocks.jsonl` — append-only, best-effort local telemetry of
        pre-tool-use gate BLOCK decisions (Stage 2). Observability only: NOT part of
        the event stream, never drives state (see core/gate_blocks.py)."""
        return root / ".harness" / "gate-blocks.jsonl"

**Step 4: Run — PASS.**

**Step 5: Commit** `feat(paths): add gate_blocks_path for the Stage 2 block-telemetry log`

---

## Task 2: `core/gate_blocks.py` — writer (never-raises) + tolerant reader

**Files:**
- Create: `src/super_harness/core/gate_blocks.py`
- Test: `tests/unit/core/test_gate_blocks.py`

**Tests (write first, run FAIL):**
- `test_record_block_appends_one_json_line` — one call -> read_blocks returns 1 record with change_id/state/tool/file preserved, ts present.
- `test_record_block_appends_not_overwrites` — two calls -> 2 records.
- `test_record_block_never_raises_when_dir_missing` — no `.harness/` -> no exception, read_blocks == [].
- `test_record_block_never_raises_when_path_unwritable` — monkeypatch `Path.open` to raise OSError -> no exception.
- `test_read_blocks_missing_file_is_empty` — [].
- `test_read_blocks_skips_malformed_lines` — mixed good/`not json`/non-object/field-missing lines -> only valid records; `file: null` -> None.

**Implement `core/gate_blocks.py`:** frozen `GateBlockRecord(ts, change_id, state, tool, file, reason, gate)`; `record_block(root, *, change_id, state, tool, file, reason, gate="pre-tool-use")` json-dumps one object and appends via `gate_blocks_path(root).open("a", encoding="utf-8")`, wrapped in `try/except Exception: pass` (NEVER raises); `read_blocks(path)` returns [] on missing/unreadable, tolerantly parses each line (skip non-json/non-dict/missing required ts+change_id+state), coercing optional str fields, `file` None-safe. Module docstring states: telemetry not lifecycle event; never raises because an uncaught hook exception = exit 1 = fail-open.

**Commit** `feat(core): gate-blocks telemetry writer+reader (never-raises, tolerant)`

---

## Task 3: gitignore the log (canonical path + repo `.gitignore`)

**Files:**
- Modify: `src/super_harness/engineering/gitignore_injector.py` (`_CANONICAL_PATHS`, add `.harness/gate-blocks.jsonl` next to `events.jsonl`; extend group-1 comment)
- Test: `tests/unit/engineering/test_gitignore_injector.py` — assert `.harness/gate-blocks.jsonl` in `_CANONICAL_PATHS` AND that the rendered block ignores a realistic path (mirror the existing events.jsonl assertion).

**Step: Regenerate this repo's own `.gitignore`** via `super-harness sync` (check AGENTS.md for the owning generator; do not hand-edit). Confirm `git check-ignore .harness/gate-blocks.jsonl` prints the path.

**Commit** `chore: gitignore .harness/gate-blocks.jsonl telemetry log`

---

## Task 4: Record the BLOCK in `hook_entry._decide` (fail-open preserved)

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py` (new `_record_block` helper mirroring `_record_bypass`; call it in the BLOCK branch of `_decide`)
- Test: `tests/integration/daemon/test_hook_entry.py`

**Tests (write first, FAIL):**
- `test_block_records_a_gate_block_line` — reuse the file's existing INTENT_DECLARED-block fixture; invoke the claude-code shim with a Write on `src/x.py` (exit 2); assert `read_blocks(gate_blocks_path(root))` has 1 record: state INTENT_DECLARED, tool "Write", file "src/x.py".
- `test_block_still_blocks_when_recording_fails` — **the safety property.** monkeypatch `super_harness.core.gate_blocks.record_block` to raise OSError; call `hook_entry._decide("Write", "src/x.py")` in the blocking workspace; assert returned decision == "block" (verdict unchanged, no exception). This is the headline safety test.
- `test_allow_records_nothing` — no active change (state None) -> ALLOW -> no log file.

> `_decide` resolves root from `Path.cwd()`; tests `monkeypatch.chdir(root)` per the file's existing block-test convention. The record-raises test calls `_decide` directly for a deterministic verdict assertion.

**Implement:** add `_record_block(root, *, change_id, state, tool, file, reason)` (mirror `_record_bypass`: skip when `not change_id`; lazy-import `record_block`; wrap in `try/except Exception: pass`). In `_decide`'s BLOCK branch call it with `change_id=snapshot.change_id`, `state=snapshot.state.current_state if snapshot.state else ""`, `tool=tool`, `file=file`, `reason=result.reason` BEFORE `return "block", ...`. Record the real `tool` name (Write/Edit), not the generic `ProposedAction(kind="edit")`; tool is NOT in the dedup key.

**Commit** `feat(gate): record BLOCK decisions to gate-blocks telemetry (fail-open preserved)`

---

## Task 5: `edits_blocked` in the value report (window + dedup at read time)

**Files:**
- Modify: `src/super_harness/engineering/value_report.py`
- Test: `tests/unit/engineering/test_value_report.py`

**Tests (write first, FAIL):**
- `test_edits_blocked_dedups_by_change_file_state` — raw records (c1,a.py,S)x2 + (c1,b.py,S)x1 -> edits_blocked == 2.
- `test_edits_blocked_respects_window` — ts 2026-07-01 and 2026-07-20, since=2026-07-15 -> 1; unparseable ts with a bound -> excluded.
- `test_edits_blocked_zero_when_no_log` — no log -> 0, no crash.

**Implement:**
- Add Band-1 field `edits_blocked: int` (NO default — force every construction to supply it, so no test silently omits it; update the handful of existing `ValueReport(...)` test constructions).
- `_edits_blocked(records, lo, hi)` -> dedup set of `(change_id, file, state)`; when a window bound is set, exclude records whose `parse_ts(ts)` is None or out of range (mirror `_in_window`).
- In `build_value_report`: `read_blocks(gate_blocks_path(workspace_root))`, pass the already-end-of-day-extended `hi`, set `edits_blocked=_edits_blocked(block_records, lo, hi)`.

**Commit** `feat(report): count distinct gate-blocked edits (dedup at read time)`

---

## Task 6: Render it + retire the Stage-2 footnote debt in `cli/report.py`

**Files:**
- Modify: `src/super_harness/cli/report.py` (`_render_human`, `_bottom_line`, footnote)
- Test: `tests/unit/cli/test_report.py`

**Tests (write first, FAIL):**
- `test_human_render_shows_edits_blocked` — edits_blocked=3 -> "3 out-of-lifecycle edit" in output.
- `test_bottom_line_counts_blocks_as_a_catch` — findings_resolved=0, undisclosed_bypasses=0, edits_blocked=2 -> NOT "no measurable catches"; mentions 2.
- `test_footnote_no_longer_claims_gate_leaves_no_trace` — after "Note:" the text must NOT list "lifecycle gate" among the invisible guardrails.

**Implement:**
- "Caught for you": add `- {r.edits_blocked} out-of-lifecycle edit(s) the gate blocked before they landed`.
- Footnote: drop the lifecycle gate from the still-invisible list (its blocks are now counted); keep locked rules + verification + doc-sync as "a further Stage 2 cut".
- `_bottom_line`: the no-catch guard also requires `edits_blocked == 0`; add clause `the gate kept {N} out-of-lifecycle edit(s) from landing` when >0. Honest framing — "kept N edits out-of-lifecycle", never "prevented N disasters".
- `--json` unchanged (`asdict` includes the field); add a JSON assertion if that test exists.

**Commit** `feat(report): surface gate-blocked edits + retire the Stage-2 no-trace footnote`

---

## Task 7: Full verify + lifecycle close-out

1. `pytest -q` / `ruff check .` / `mypy src` / `super-harness verify record-gate-blocks` all green. If a decision/doc/sync gate fails on the new canonical `.harness/` file, grep the lifecycle spec the gitignore comment references ("lifecycle §2 canonical file locations") and update that section in scope, re-run.
2. **Manual dogfood (measured, not claimed):** trigger a real BLOCK, then `super-harness report` shows "N out-of-lifecycle edit(s) the gate blocked" and `--json report -> .data.edits_blocked` matches. Record the observed count in the PR body (feedback-dont-conflate-ritual-with-value). Confirm `git status` is clean of `.harness/gate-blocks.jsonl`.
3. **Lifecycle:** `plan ready record-gate-blocks --scope @<all touched files incl. tests + docs/plans + private/OPEN-ITEMS.md>` (full scope = self-host merge-gate requirement; include private/ for attestation coverage) -> plan review (2-source) -> `implementation start` (state advance; code already TDD'd) -> `done` -> code review (2-source Codex+Claude at FULL intensity — this touches the gate hot path; the Task-4 safety test is the headline) -> merge -> `on-merge` -> refresh ledger + NEXT-SESSION-PROMPT + auto-memory.

---

## Review focus (hand to reviewers)

1. **Fail-open is sacred.** `record_block` cannot, under any failure (disk full, unwritable dir, encoding, monkeypatched raise), change the gate verdict or crash the hook — an uncaught hook exception is exit 1 = non-blocking = fail-OPEN for the Claude shim (Task 4 `test_block_still_blocks_when_recording_fails`).
2. **No hot-path regression.** Bare append: no flock, no validation, no read; does not touch `events.jsonl` or its lock. The `record_block` import is inside the BLOCK branch only (ALLOW path cold-start unchanged).
3. **Honest metric.** Dedup by `(change_id, file, state)` at read time; retry duplicates do not inflate. Wording claims "kept N edits out-of-lifecycle", not "prevented N disasters".
4. **Telemetry != governance.** This file is NOT attestable/replayable and gates nothing (unlike `gate_bypassed`, a merge blocker). That asymmetry is deliberate and is why it lives outside the event stream.
