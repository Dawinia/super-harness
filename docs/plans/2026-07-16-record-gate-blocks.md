# Record Gate Blocks (Stage 2 — the missing prevention signal) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record every pre-tool-use gate BLOCK as durable, local, best-effort telemetry so `super-harness report` can show prevention-success — the gate *working* — not only `gate_bypassed` (the gate *defeated*).

**Architecture:** The pre-tool-use gate is a pure query (gates never emit events); the hook dispatcher records a BLOCK **out-of-band** (as the gate contract already prescribes) into a new append-only log `.harness/gate-blocks.jsonl`. This is **observability telemetry**, NOT a lifecycle event: it never drives state, never gates anything, and lives *outside* `events.jsonl` so the gate's fail-open + fast hot-path contract is untouched. The write is best-effort and **never raises** — a failed write must not flip a real BLOCK into an ALLOW (the Claude shim treats an uncaught hook exception as exit 1 = non-blocking = fail-open). Raw records duplicate on agent retries; the honest count is computed at **read time** in the report.

**The metric, stated honestly (plan-review CODX-002):** We record NO edit content (privacy + size), so the hot path cannot distinguish an agent *retrying the same edit* from *two genuinely different edits to the same file in the same state*. We therefore do NOT claim "N edits". The report renders the **conservative floor**: the number of **distinct out-of-lifecycle edit targets `(change_id, file, state)`** the gate held. This deliberately UNDER-counts (never inflates) — the honest direction. Raw block lines are retained in the log for unaggregated detail; the headline is the distinct-target floor, worded as such.

**Tech Stack:** Python 3.10+, stdlib `json`, existing `core/` primitives (`utc_now_iso`, `paths`), pytest.

**Non-goals (YAGNI):** No cross-machine / team aggregation. No log rotation/GC. No recording of ALLOW decisions. No new event type; nothing enters `events.jsonl`. No recording from the `gate check` CLI (diagnostic path, not routed through `_decide`). No edit-content / attempt-fingerprint capture (privacy — and it is what forces the honest distinct-target framing above).

**Scope (files) — matches the `plan ready --scope` declaration exactly:**
- Create: `src/super_harness/core/gate_blocks.py`, `tests/unit/core/test_gate_blocks.py`
- Modify: `src/super_harness/core/paths.py` (+`gate_blocks_path`)
- Modify: `tests/unit/core/test_paths.py` (**exists** — the `gate_blocks_path` test is added here)
- Modify: `src/super_harness/daemon/hook_entry.py`
- Modify: `src/super_harness/engineering/value_report.py`
- Modify: `src/super_harness/cli/report.py`
- Modify: `src/super_harness/engineering/gitignore_injector.py` + `tests/unit/engineering/test_gitignore_injector.py`
- Modify: `tests/integration/daemon/test_hook_entry.py`, `tests/unit/engineering/test_value_report.py`, `tests/unit/cli/test_report.py`
- Modify: `.gitignore` (regen via `sync`)
- Modify: `private/OPEN-ITEMS.md` (register Stage-2 done, the killed-tier line, and the HG-PLAN-AUTHORING gap below)
- This plan doc: `docs/plans/2026-07-16-record-gate-blocks.md`

> **Close-out artifacts NOT in review scope:** `private/CAPABILITY-CONVERGENCE-LEDGER.md`/`.html` and `private/NEXT-SESSION-PROMPT.md` are refreshed *post-merge* (they reference the merge commit) and are gitignored, so they never appear in this PR's reviewable diff. Handled in Phase F step 5, outside the attested change diff.

---

## Phase 0: Lifecycle ordering constraint (addresses CODX-001 / CODX-003)

The live pre-tool-use gate ALLOWs source edits only in `PLAN_APPROVED` / `IMPLEMENTATION_IN_PROGRESS` / `CODE_REVIEW_REJECTED`, and BLOCKs `INTENT_DECLARED` / `AWAITING_PLAN_REVIEW` / `PLAN_REJECTED` (see `gates/decisions.py`). So implementation MUST follow approval. Ordering:

1. **Draft this plan while no change is active** (state is None → the gate ALLOWs the edit). Do NOT declare intent first and then author under a block: the harness offers no in-gate plan-authoring path, and the gate must not be worked around via the shell (that would defeat the integrity this very change measures — see the HG-PLAN-AUTHORING gap, registered in `private/OPEN-ITEMS.md`).
2. `super-harness change start record-gate-blocks` → `INTENT_DECLARED`; commit the drafted plan (git, not an edit tool).
3. `super-harness plan ready record-gate-blocks --tier-hint Normal --scope @<scope.yaml of every Scope file>` → `AWAITING_PLAN_REVIEW`.
4. **Plan review, 2 independent sources (codex + claude)** → address findings, re-`plan ready` on reject → `PLAN_APPROVED`.
5. `super-harness implementation start record-gate-blocks` → `IMPLEMENTATION_IN_PROGRESS` (edits now gate-ALLOWed).

Only now do Tasks 1–6 run, each TDD (failing test → red → implement → green → commit).

---

## Task 1: `gate_blocks_path` helper in `core/paths.py`

**Files:** Modify `src/super_harness/core/paths.py` (after `events_path`); test add to existing `tests/unit/core/test_paths.py`.

**Test (red):**

    from super_harness.core.paths import gate_blocks_path
    def test_gate_blocks_path_is_under_harness(tmp_path):
        assert gate_blocks_path(tmp_path) == tmp_path / ".harness" / "gate-blocks.jsonl"

**Implement (green):**

    def gate_blocks_path(root: Path) -> Path:
        """`.harness/gate-blocks.jsonl` — append-only, best-effort local telemetry of
        pre-tool-use gate BLOCK decisions (Stage 2). Observability only: NOT part of
        the event stream, never drives state (see core/gate_blocks.py)."""
        return root / ".harness" / "gate-blocks.jsonl"

**Commit** `feat(paths): add gate_blocks_path for the Stage 2 block-telemetry log`

---

## Task 2: `core/gate_blocks.py` — writer (never-raises) + tolerant reader

**Files:** Create `src/super_harness/core/gate_blocks.py`, `tests/unit/core/test_gate_blocks.py`.

**Tests (red):**
- `test_record_block_appends_one_json_line` — one call → 1 record, change_id/state/tool/file preserved, ts present.
- `test_record_block_appends_not_overwrites` — two calls → 2 records.
- `test_record_block_never_raises_when_dir_missing` — no `.harness/` → no exception, read == [].
- `test_record_block_never_raises_when_path_unwritable` — monkeypatch `Path.open` to raise OSError → no exception.
- `test_read_blocks_missing_file_is_empty` — [].
- `test_read_blocks_skips_malformed_lines` — good/`not json`/non-object/field-missing lines → only valid; `file: null` → None.

**Implement:** frozen `GateBlockRecord(ts, change_id, state, tool, file, reason, gate)`; `record_block(root, *, change_id, state, tool, file, reason, gate="pre-tool-use")` json-dumps one object and appends via `gate_blocks_path(root).open("a", encoding="utf-8")`, whole body wrapped in `try/except Exception: pass` (**NEVER raises**); `read_blocks(path)` returns [] on missing/unreadable, tolerantly parses each line (skip non-json/non-dict/missing required ts+change_id+state), coerces optional str fields, `file` None-safe. Module docstring: telemetry not lifecycle event; never raises because an uncaught hook exception = exit 1 = fail-open.

**Commit** `feat(core): gate-blocks telemetry writer+reader (never-raises, tolerant)`

---

## Task 3: gitignore the log (canonical path + repo `.gitignore`)

**Files:** Modify `src/super_harness/engineering/gitignore_injector.py` (`_CANONICAL_PATHS`, add `.harness/gate-blocks.jsonl` next to `events.jsonl`; extend group-1 comment) + `tests/unit/engineering/test_gitignore_injector.py`.

**Step:** regenerate this repo's `.gitignore` via `super-harness sync`; confirm `git check-ignore .harness/gate-blocks.jsonl` prints the path.

**Commit** `chore: gitignore .harness/gate-blocks.jsonl telemetry log`

---

## Task 4: Record the BLOCK in `hook_entry._decide` (fail-open preserved)

**Files:** Modify `src/super_harness/daemon/hook_entry.py`; test `tests/integration/daemon/test_hook_entry.py`.

**Tests (red):**
- `test_block_records_a_gate_block_line` — reuse the file's INTENT_DECLARED-block fixture; invoke the claude-code shim with a Write on `src/x.py` (exit 2); assert `read_blocks(gate_blocks_path(root))` has 1 record: state INTENT_DECLARED, tool "Write", file "src/x.py".
- `test_block_still_blocks_when_recording_fails` — **headline safety test.** monkeypatch `super_harness.core.gate_blocks.record_block` to raise OSError; call `hook_entry._decide("Write", "src/x.py")` in the blocking workspace; assert returned decision == "block" (verdict unchanged, no exception escapes).
- `test_allow_records_nothing` — no active change (state None) → ALLOW → no log file.

**Implement:** add `_record_block(root, *, change_id, state, tool, file, reason)` mirroring `_record_bypass` (skip when `not change_id`; lazy-import `record_block`; wrap in `try/except Exception: pass`). In `_decide`'s BLOCK branch call it (`change_id=snapshot.change_id`, `state=snapshot.state.current_state if snapshot.state else ""`, `tool=tool`, `file=file`, `reason=result.reason`) BEFORE `return "block", ...`. Record the real `tool` name; `tool` is not in the dedup key.

**Commit** `feat(gate): record BLOCK decisions to gate-blocks telemetry (fail-open preserved)`

---

## Task 5: `edits_blocked` (distinct target floor) in the value report

**Files:** Modify `src/super_harness/engineering/value_report.py`; test `tests/unit/engineering/test_value_report.py`.

**Tests (red):**
- `test_edits_blocked_counts_distinct_target_tuples` — raw (c1,a.py,S)x2 + (c1,b.py,S)x1 → 2 (retry collapses; two distinct targets).
- `test_edits_blocked_respects_window` — ts 2026-07-01 and 2026-07-20, since=2026-07-15 → 1; unparseable ts with a bound → excluded.
- `test_edits_blocked_zero_when_no_log` — no log → 0, no crash.

**Implement:** add Band-1 field `edits_blocked: int` (NO default → update existing constructions). `_edits_blocked(records, lo, hi)` → size of the dedup set of `(change_id, file, state)` after the same window filter as `_in_window` (unparseable ts excluded when a bound is set). Docstring: **distinct out-of-lifecycle edit targets; a conservative floor that under-counts (retries collapse), never inflates.** Wire into `build_value_report` via `read_blocks(gate_blocks_path(workspace_root))` with the already-end-of-day-extended `hi`.

**Commit** `feat(report): count distinct out-of-lifecycle edit targets the gate held`

---

## Task 6: Render honestly across ALL report modes + retire the footnote debt (addresses CODX-004)

**Files:** Modify `src/super_harness/cli/report.py` (`_render_human`, `_render_brief`, `_bottom_line`, footnote); test `tests/unit/cli/test_report.py`.

**Tests (red):**
- `test_human_render_shows_distinct_blocked_targets` — edits_blocked=3 → output contains "3 distinct out-of-lifecycle edit target" (NOT "3 edits").
- `test_brief_render_shows_blocked_targets` — brief mode with edits_blocked=2, findings_resolved=0 → the one-liner contains the explicit unit phrase `2 distinct target(s) held` (carries the distinct-target unit; must NOT read as a raw "2 blocked" edit/attempt count, and must NOT silently drop the signal in `--brief`).
- `test_bottom_line_counts_blocks_as_a_catch` — findings_resolved=0, undisclosed_bypasses=0, edits_blocked=2 → NOT "no measurable catches"; mentions 2 as held targets.
- `test_footnote_no_longer_claims_gate_leaves_no_trace` — after "Note:" the text must NOT list "lifecycle gate" among the still-invisible guardrails.

**Implement:**
- `_render_human` "Caught for you": add `- {r.edits_blocked} distinct out-of-lifecycle edit target(s) the gate held (file x state; a conservative floor — retries collapse)`.
- `_render_brief`: append a `{r.edits_blocked} distinct target(s) held` bit when `edits_blocked > 0` (mirrors how it appends `undisclosed bypass(es)`; the phrase carries the distinct-`(change_id, file, state)` unit so `--brief` never reads as a raw blocked-edit count), so `--brief` reflects the new signal.
- Footnote: drop the lifecycle gate from the still-invisible list; keep locked rules + verification + doc-sync as "a further Stage 2 cut".
- `_bottom_line`: the no-catch guard also requires `edits_blocked == 0`; add clause `the gate held {N} out-of-lifecycle edit target(s)` when >0. Honest — a held target, never "prevented N disasters".
- `--json` unchanged (`asdict` includes the field).

**Commit** `feat(report): surface gate-held targets in all modes + retire the Stage-2 footnote`

---

## Phase F: verify + code review + merge + close-out

1. `pytest -q` / `ruff check .` / `mypy src` / `super-harness verify record-gate-blocks` all green. **No spec edit is required for the new canonical path:** verified that no `docs/decisions/**` record and no spec enumerates canonical `.harness/` paths (they appear only as prose mentions in `docs/ARCHITECTURE.md` / `docs/cli-reference.md`, which no doc-sync gate diffs against `_CANONICAL_PATHS`); the sync-regenerated `.gitignore` is the only generated artifact. (If `super-harness verify` unexpectedly flags drift, that is a Phase-F discovery handled by the normal `plan redeclare` mechanism — never a silent out-of-scope edit.)
2. **Manual dogfood (measured, not claimed):** trigger a real BLOCK, then `super-harness report` shows "N distinct out-of-lifecycle edit target(s) the gate held" and `--json report -> .data.edits_blocked` matches. Record the observed count in the PR body (feedback-dont-conflate-ritual-with-value). Confirm `git status` clean of `.harness/gate-blocks.jsonl`.
3. `super-harness done record-gate-blocks` → `AWAITING_CODE_REVIEW`.
4. **Code review, 2 independent sources (codex + claude) at FULL intensity** — touches the gate hot path; the Task-4 `test_block_still_blocks_when_recording_fails` safety property is the headline. On reject, delta round = `review prepare` (fix stays in-scope).
5. Merge → `super-harness on-merge` → **post-merge close-out (outside the PR diff):** refresh `private/CAPABILITY-CONVERGENCE-LEDGER.md`/`.html`, `private/NEXT-SESSION-PROMPT.md`, auto-memory.

---

## Review focus (hand to reviewers)

1. **Fail-open is sacred.** `record_block` cannot, under any failure (disk full, unwritable dir, encoding, monkeypatched raise), change the gate verdict or crash the hook — an uncaught hook exception is exit 1 = non-blocking = fail-OPEN for the Claude shim (Task 4 `test_block_still_blocks_when_recording_fails`).
2. **No hot-path regression.** Bare append: no flock, no validation, no read; does not touch `events.jsonl` or its lock. The `record_block` import is inside the BLOCK branch only (ALLOW path cold-start unchanged).
3. **Honest metric.** The headline is the distinct `(change_id, file, state)` target floor — a deliberate conservative UNDER-count, rendered as "held targets" across ALL report modes (human, brief, json), never "N edits" or "prevented N disasters".
4. **Telemetry != governance.** This file is NOT attestable/replayable and gates nothing (unlike `gate_bypassed`, a merge blocker). That asymmetry is deliberate and is why it lives outside the event stream.
