# parse_ts primitive + encoding/doc consistency debt — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the last four fresh-eyes review debts (F11b, F11-encoding, F6, F15) — chiefly by converging the two ISO-timestamp parsers into a single `core.parse_ts` primitive, the same "collapse to one primitive" theme as #64 (`shell_runner`) and #68 (`state_snapshot`).

**Architecture:** Introduce a pure, never-raising `core/parse_ts.py` returning `datetime | None`, with every returned value normalized to **aware-UTC** (`.astimezone(timezone.utc)`; naive gets UTC attached) so mixed ISO forms are always mutually comparable. `active_change` wraps it with a lowest-sorting sentinel for ORDERING; `reducer` uses the `None` signal to skip drift detection on unparseable input. Fold in a mechanical `encoding="utf-8"` sweep of text file I/O, an honest docstring fix in the reducer, and two ledger wording touch-ups.

> **Plan-review convergence (round 1, 2026-07-03):** Claude subagent APPROVE (all 6 load-bearing claims verified TRUE, incl. live-reproduced reducer `TypeError`); Codex REVISE with plan-text/contract fixes only — no design dispute. All fixes below are folded in: (B1) real test paths `tests/unit/core/`; (B2) explicit commit steps for every tracker file incl. `OPEN-ITEMS.md` + `NEXT-SESSION-PROMPT.md`; (B3) `parse_ts` normalizes aware datetimes to UTC so the "aware-UTC" contract is literally true; (NITs) use the existing `_raw_line` helper for the reducer RED test, update the `active_change.py:49` docstring, use a real non-terminal state name, and add `encoding="utf-8"` to the `state_yaml.py` write `open()` for round-trip symmetry.

**Tech Stack:** Python 3.10+, pytest, PyYAML, import-linter (G-FITNESS), self-hosted super-harness lifecycle.

---

## Context the executor needs

### Why one primitive (F11b) — the load-bearing design

Two ISO-8601 timestamp parsers exist today, with **different robustness and different caller needs**:

- `core/active_change.py::_parse_ts(value: object) -> datetime` — bullet-proof: accepts `datetime` OR `str`, normalizes naive→aware-UTC, maps empty/malformed/`None`/other-type → `datetime.min` (UTC). NEVER raises. Feeds the gate hot path. Its caller (`pick_active_change`) needs a **total order** — a bad value must sort LOWEST, never win.
- `core/reducer.py` inline (lines ~78-93) — parses `prev_ts`/`ev.timestamp` only to **compare** (`cur_dt < prev_dt`) and warn on >60s backward drift. It catches only `ValueError`. Its caller needs to **distinguish parseable from unparseable**: on unparseable it must do NOTHING (no spurious drift warning).

Two real problems with the reducer copy:
1. **Latent `TypeError`.** Comparing a naive datetime against an aware one (mixed `Z` / tz-less ISO forms across two events) raises `TypeError`, which the `except ValueError` does NOT catch → the reducer crashes, violating its own "TOLERANT — never raise" contract. `_parse_ts` avoids this by normalizing everything to aware-UTC.
2. **Duplication.** #62/#63 each hardened one copy; this is the second copy the review (F3/F11b) said to converge.

Note F3 is already CLOSED: `parse_event_line` (`core/events.py:129`) now guards `isinstance(obj["timestamp"], str)`, so `ev.timestamp` reaching the reducer is guaranteed `str`. The convergence is not about that crash (fixed) but about the naive/aware `TypeError` and the single-primitive hygiene.

**The primitive must serve both needs**, so it returns `datetime | None` (NOT a sentinel):
- `active_change` wraps: `parse_ts(v) or _TS_MIN` — restores the lowest-sorting-sentinel ordering semantics byte-for-byte.
- `reducer` uses: `if prev_dt is not None and cur_dt is not None and cur_dt < prev_dt` — preserves "skip drift check silently on unparseable" exactly, and (bonus) fixes the naive/aware `TypeError`.

Returning a sentinel from the primitive instead would be WRONG for the reducer: an unparseable `cur` → `datetime.min` → `cur < prev` True → **spurious** drift warning. That is a behavior change we must NOT introduce. This is the crux — do not "simplify" the primitive to return `datetime.min`.

### Tax / lifecycle notes

- `reducer.py` is anchored by decision `d-state-pure-fold` (content fingerprint in `docs/decisions/d-state-pure-fold.md`). Editing it makes the fingerprint stale → tier-2 unreconciled → `decision check` fails → merge gate blocks. The fold stays a pure left-fold (no in-place mutation, same events→same state), so the correct verb is **`decision reconcile d-state-pure-fold`** (NOT `betray`), run after the edit.
- New `core/parse_ts.py` is unanchored, pure stdlib (`datetime`), imports nothing internal → satisfies `core-is-base` trivially. Do NOT add a `@decision` anchor (it is not about the fold).
- **`plan ready --scope` MUST cover every changed file**, including the decision record, this plan doc if committed, and the ledger/findings docs. Missing a file → `abandon` + redeclare (the #68 README pothole). Enumerate scope from the final file list in this plan.
- Post-#68 the gate is in-process with real teeth: in `AWAITING_CODE_REVIEW` / `READY_TO_MERGE` states, `Edit`/`Write` are frozen — run lifecycle commands via Bash (ungated); write verdict files via Bash heredoc.

### Honesty framing (for the ledger at close)

This is **review-driven consistency/soundness debt, NOT a value bleed** (strict count stays 1, = #45). Specifically, F11-encoding is **hygiene + latent-safety, not a currently-biting bug**: events.jsonl and state.yaml are ASCII today (`json.dumps` default `ensure_ascii=True`; `yaml.safe_dump` default escapes non-ASCII), so the write(utf-8)/read(locale) mismatch does not corrupt today. The fix is PEP-597 correctness and defense against a future non-ASCII field or a changed dump flag. Do NOT overclaim it as a live bug fix.

---

## Task 1: `core.parse_ts` primitive (TDD)

**Files:**
- Create: `src/super_harness/core/parse_ts.py`
- Test: `tests/unit/core/test_parse_ts.py`

**Step 1: Write the failing tests**

```python
# tests/unit/core/test_parse_ts.py
"""parse_ts — the single ISO-8601 timestamp primitive. Never raises."""
from datetime import datetime, timezone

from super_harness.core.parse_ts import parse_ts


def test_aware_iso_z_form():
    assert parse_ts("2026-07-03T10:00:00Z") == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)


def test_aware_iso_offset_form():
    assert parse_ts("2026-07-03T10:00:00+00:00") == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)


def test_naive_iso_normalized_to_utc():
    # tz-less string parses NAIVE, primitive attaches UTC so it can't TypeError vs aware entries.
    out = parse_ts("2026-07-03T10:00:00")
    assert out == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    assert out.tzinfo is not None


def test_datetime_aware_utc_returned_equal():
    dt = datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    out = parse_ts(dt)
    assert out == dt
    assert out.tzinfo == timezone.utc


def test_datetime_non_utc_offset_normalized_to_utc():
    # +05:00 10:00 == 05:00 UTC — the "aware-UTC" contract must hold literally.
    from datetime import timedelta
    plus5 = timezone(timedelta(hours=5))
    out = parse_ts(datetime(2026, 7, 3, 10, 0, tzinfo=plus5))
    assert out == datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
    assert out.tzinfo == timezone.utc


def test_string_non_utc_offset_normalized_to_utc():
    out = parse_ts("2026-07-03T10:00:00+05:00")
    assert out == datetime(2026, 7, 3, 5, 0, tzinfo=timezone.utc)
    assert out.tzinfo == timezone.utc


def test_datetime_naive_gets_utc_attached():
    out = parse_ts(datetime(2026, 7, 3, 10, 0))
    assert out == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)
    assert out.tzinfo == timezone.utc


def test_empty_string_is_none():
    assert parse_ts("") is None


def test_malformed_string_is_none():
    assert parse_ts("not-a-timestamp") is None


def test_none_is_none():
    assert parse_ts(None) is None


def test_wrong_type_is_none():
    assert parse_ts(12345) is None
    assert parse_ts([]) is None


def test_naive_and_aware_are_mutually_comparable():
    # The whole point: a mixed pair must not TypeError under comparison.
    a = parse_ts("2026-07-03T10:00:00")       # naive source
    b = parse_ts("2026-07-03T10:00:01+00:00")  # aware source
    assert a < b  # would raise TypeError if either stayed naive
```

**Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_parse_ts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'super_harness.core.parse_ts'`

**Step 3: Write the primitive**

```python
# src/super_harness/core/parse_ts.py
"""The single ISO-8601 timestamp parsing primitive for the core layer.

Converges two prior copies (`active_change._parse_ts` and the `reducer` inline
drift parse) into one never-raising function. Returns `datetime | None` so each
caller supplies its own policy for the unparseable case:

- ORDERING (active_change): wrap as ``parse_ts(v) or _TS_MIN`` so a bad value
  sorts lowest and never wins.
- DRIFT DETECTION (reducer): treat ``None`` as "skip the comparison" — an
  unparseable timestamp must NOT trigger a spurious drift warning.

Returning a sentinel here instead of ``None`` would be wrong for the reducer
(a sentinel would compare as an enormous backward jump). Keep it tri-valued.

All returned datetimes are aware-UTC (aware inputs are CONVERTED via
``astimezone``, not just accepted as-is) so a mixed naive/aware pair (e.g. a
``Z`` form vs a tz-less form across two events) can be compared without
``TypeError`` — the crash the reducer's old ``except ValueError``-only copy
did not catch.
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_ts(value: object) -> datetime | None:
    """Parse a timestamp into an aware-UTC ``datetime``, or ``None`` if it is
    absent/malformed/wrong-type. NEVER raises (this feeds the gate hot path via
    ``active_change``).

    Accepts the shapes a state.yaml / events.jsonl value can take:
    - ``datetime`` → converted to aware UTC (naive gets UTC attached; a non-UTC
      offset is converted to the same instant in UTC).
    - ISO ``str`` with ``Z`` or ``+00:00`` (or any offset, or tz-less) → parsed,
      then converted to aware UTC as above.
    - empty / malformed / ``None`` / any other type → ``None``.
    """
    try:
        if isinstance(value, datetime):
            return _to_utc(value)
        if not isinstance(value, str) or not value:
            return None
        return _to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except Exception:
        return None
```

A SINGLE outer `except Exception` is the never-raise belt (round-2 + code-review Codex catch): it covers not just the `astimezone` pathological-`tzinfo`/boundary-`OverflowError` case but also a hostile `str`/`datetime` **subclass** overriding `replace`/`fromisoformat` (the naive `.replace(tzinfo=utc)` and the `str.replace("Z",…)` are otherwise unguarded). `_to_utc` is then a plain helper that MAY raise; the belt turns any raise into `None`:

```python
def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
```

Regression tests: pathological `tzinfo` (utcoffset raises), `str` subclass with raising `replace`, `datetime` subclass with raising `replace` — all must resolve to `None`, never propagate.

**Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/unit/core/test_parse_ts.py -v`
Expected: PASS (all)

**Step 5: Commit**

```bash
git add src/super_harness/core/parse_ts.py tests/unit/core/test_parse_ts.py
git commit -m "feat(core): add single parse_ts timestamp primitive (F11b)"
```

---

## Task 2: Point `active_change` at the primitive

**Files:**
- Modify: `src/super_harness/core/active_change.py`

**Step 1: Add a regression test asserting ordering semantics survive**

Add to `tests/unit/core/` (find the existing active_change test file, e.g. `tests/unit/core/test_active_change.py`; if none, create `test_active_change_ordering.py`):

Use a REAL non-terminal state name (`IMPLEMENTATION_IN_PROGRESS`) — `pick_active_change` only tests membership in `TERMINAL_STATES = {ARCHIVED, ABANDONED}`, so any non-terminal string is "live", but use a real one for clarity.

```python
def test_unparseable_last_event_at_sorts_lowest():
    # A change with a garbage timestamp must never win the "most recent" pick.
    from super_harness.core.active_change import pick_active_change
    picked = pick_active_change([
        ("good", "IMPLEMENTATION_IN_PROGRESS", "2026-07-03T10:00:00Z"),
        ("bad", "IMPLEMENTATION_IN_PROGRESS", "not-a-date"),
    ])
    assert picked == "good"


def test_all_unparseable_still_returns_a_change():
    # All keys collapse to _TS_MIN → max breaks ties by change_id → "b" > "a".
    from super_harness.core.active_change import pick_active_change
    picked = pick_active_change([
        ("a", "IMPLEMENTATION_IN_PROGRESS", ""),
        ("b", "IMPLEMENTATION_IN_PROGRESS", None),
    ])
    assert picked == "b"
```

**Step 2: Run existing active_change tests to capture the green baseline**

Run: `.venv/bin/python -m pytest tests/unit/core/test_active_change.py -v` (adjust path)
Expected: PASS (baseline before refactor)

**Step 3: Refactor to use `parse_ts`**

In `active_change.py`:
- Add import: `from super_harness.core.parse_ts import parse_ts`
- Add module constant near the top: `_TS_MIN = datetime.min.replace(tzinfo=timezone.utc)`
- DELETE the `_parse_ts` function (lines ~19-40).
- In `pick_active_change`, change the key to: `key=lambda t: (parse_ts(t[1]) or _TS_MIN, t[0])`
- Update the two docstrings that name `_parse_ts` (module docstring mention at ~line 49, and any inline reference) to reference `parse_ts` / the sentinel wrap instead.
- Keep `from datetime import datetime, timezone` (still needed for `_TS_MIN`).
- Add `encoding="utf-8"` to `state_path.read_text()` at line ~71 (part of the F11-encoding sweep, done here since the file is already open): `yaml.safe_load(state_path.read_text(encoding="utf-8"))`.

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_active_change.py tests/unit/core/test_parse_ts.py -v`
Expected: PASS (all — semantics identical, sentinel restored)

**Step 5: Commit**

```bash
git add src/super_harness/core/active_change.py tests/unit/core/
git commit -m "refactor(core): active_change uses parse_ts primitive (F11b)"
```

---

## Task 3: Point `reducer` at the primitive + F6 docstring honesty

**Files:**
- Modify: `src/super_harness/core/reducer.py` (ANCHORED — reconcile after)
- Test: `tests/unit/core/test_reducer.py` (find existing)

**Step 1: Write the failing test — mixed naive/aware must NOT crash**

This is the latent `TypeError` the old copy had. Craft two events for the same change_id whose timestamps mix a tz-less form and a `Z` form, backward in time, and assert `derive_state` does not raise and produces state:

Use the EXISTING hand-crafted-line helper `_raw_line(event_type, ts)` at `tests/unit/core/test_reducer.py:172` (it bypasses `EventWriter` and mirrors the F3 tests at lines ~187-194 — exactly right for injecting a mixed naive/aware pair). Do NOT use `EventWriter.emit` (it would reject/normalize). Verify the helper's exact signature when you get there.

```python
def test_reducer_tolerates_mixed_naive_aware_timestamps(tmp_path):
    """A tz-less timestamp followed by an aware one (backward) must not raise —
    the old inline parse compared naive vs aware and TypeError'd past its
    except ValueError guard."""
    events = tmp_path / "events.jsonl"
    events.write_text(
        _raw_line("intent_declared", "2026-07-03T10:00:05") + "\n"        # naive
        + _raw_line("plan_ready", "2026-07-03T10:00:00+00:00") + "\n",    # aware, earlier
        encoding="utf-8",
    )
    from super_harness.core.reducer import derive_state
    state = derive_state(events)  # must not raise
    assert "c1" in state  # (change_id the helper stamps — align with _raw_line's default)
```

Adapt the assertion to whatever `change_id` `_raw_line` stamps (check the helper). If the pair is a >60s backward drift, the reducer should `log.warning` but NOT raise.

**Step 2: Run to verify it fails (RED on the crash)**

Run: `.venv/bin/python -m pytest tests/unit/core/test_reducer.py::test_reducer_tolerates_mixed_naive_aware_timestamps -v`
Expected: FAIL — `TypeError: can't compare offset-naive and offset-aware datetimes` (proves the bug is real before the fix).

**Step 3: Refactor the drift block**

In `reducer.py`:
- Replace `from datetime import datetime` with `from super_harness.core.parse_ts import parse_ts` (verify `datetime` is not used elsewhere in the file; if unused after, drop the datetime import entirely).
- Replace the drift `try/except` block (lines ~78-92) with:

```python
        prev_ts = last_ts.get(ev.change_id)
        if prev_ts:
            prev_dt = parse_ts(prev_ts)
            cur_dt = parse_ts(ev.timestamp)
            # Both must parse to compare; an unparseable side means "no signal",
            # NOT a drift warning. parse_ts normalizes to aware-UTC so a mixed
            # naive/aware pair compares without TypeError (the old copy crashed).
            if prev_dt is not None and cur_dt is not None and cur_dt < prev_dt:
                drift = (prev_dt - cur_dt).total_seconds()
                if drift > CLOCK_DRIFT_WARN_THRESHOLD_S:
                    log.warning(
                        "events.jsonl line %d: timestamp drift %.1fs (append order preserved)",
                        line_num, drift,
                    )
        last_ts[ev.change_id] = ev.timestamp
```

- Add `encoding="utf-8"` to `events_file.read_text()` at line ~63: `events_file.read_text(encoding="utf-8")`.

**F6 docstring honesty** — in the same file:
- The class/function docstring lists `- Truncated last line (no newline) → log.warning + skip line` (~line 50) and Invariant 4 `Tolerant of truncated last line (events.jsonl crash recovery)` (~line 17). `read_text().splitlines()` cannot detect a missing trailing newline on an otherwise-complete JSON line; the writer emits `line + "\n"` in one atomic `os.write` (`writer.py:98`), so a torn write manifests as **incomplete JSON**, skipped via the malformed-JSON path — there is no newline check. Reword to be honest:
  - Line ~50 bullet → `- Truncated last line (partial write) → surfaces as malformed JSON → log.warning + skip`
  - Line ~17 invariant → keep "Tolerant of truncated last line" but drop the "(no newline)" implication if present; ensure it reads as "a partial final write is skipped as malformed JSON", not "we detect a missing newline".

**Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/unit/core/test_reducer.py -v`
Expected: PASS (all, including the new mixed-tz test)

**Step 5: Commit**

```bash
git add src/super_harness/core/reducer.py tests/unit/core/test_reducer.py
git commit -m "refactor(core): reducer uses parse_ts; fix latent naive/aware TypeError; honest last-line docstring (F11b/F6)"
```

---

## Task 4: `encoding="utf-8"` consistency sweep (F11-encoding)

**Files (all text I/O in `src/` lacking explicit encoding — read AND write, for round-trip symmetry):**

Reads (`read_text()` / `yaml.safe_load(...read_text())`):
- `core/state_yaml.py:74`, `core/_registry.py:111,156`, `core/post_emit.py:67`, `core/emit_validation.py:84,106,192`
- `cli/verification.py:180`, `cli/event.py:68`, `cli/change.py:281`, `cli/state.py:79,129`, `cli/adapter.py:562,596`, `cli/init.py:70,81,87,111,451,556`
- `adapters/registry.py:137`, `adapters/agent/_settings_merge.py:222,254`, `adapters/agent/codex.py:130,188`, `adapters/agent/claude_code.py:220,319`
- `daemon/supervisor.py:167`
- `engineering/reviewer_policy.py:44`, `engineering/pr_metadata.py:167`, `engineering/verification_config.py:267,369`

Writes (`write_text()`):
- `cli/adapter.py:583,622`, `cli/init.py:212,447,523,552,603`
- `adapters/agent/_settings_merge.py:124,177,218,254`, `adapters/agent/codex.py:147,188`, `adapters/agent/claude_code.py:239,319`
- `engineering/operation_log.py:47`, `engineering/verification_config.py:379`

Write `open(...)` (text-mode, no encoding — round-trip pair for a read we ARE fixing):
- `core/state_yaml.py:66` `open(tmp, "w")` — state.yaml is written here and read at `:74` (which we fix). Add `encoding="utf-8"` to the `open()` so the read/write charset match. (The `open()` lock-file sites — `post_emit.py:56`, `writer.py:103` — open for `flock` only and decode no content; SKIP them. `os.fdopen(fd, "wb")` sites are binary and already `.encode()`; SKIP.)

(`core/active_change.py:71` and `core/reducer.py:63` were done in Tasks 2/3.)

**Step 1: Re-grep to get the CURRENT exact set (line numbers drift as you edit)**

Run:
```bash
grep -rn "read_text()\|write_text(" src/ | grep -v 'encoding='
grep -rn 'open(' src/ | grep -v 'encoding=' | grep -vE 'os\.fdopen|# '
```
Work from THIS output, not the stale list above. The scope is `Path.read_text` / `Path.write_text` sites PLUS the single `state_yaml.py:66` text `open()`; it is NOT a blanket rewrite of every `open()`.

**Step 2: Apply `encoding="utf-8"` mechanically**

For each site: `path.read_text()` → `path.read_text(encoding="utf-8")`; `path.write_text(x)` → `path.write_text(x, encoding="utf-8")`; `open(tmp, "w")` → `open(tmp, "w", encoding="utf-8")`. Do NOT add `errors="replace"` anywhere except where it already exists (state_snapshot owns that) — we are not widening the error-swallowing surface, just fixing decode/encode charset.

Leave already-`encoding=`'d sites untouched. `frontmatter.py:22` (`yaml.safe_load("\n".join(...))`) has no file read — skip. The `flock` `open()` sites (`post_emit.py:56`, `writer.py:103`) decode no content — skip.

**Step 3: Verify the sweep is complete**

Run:
```bash
grep -rn "read_text()\|write_text(" src/ | grep -v 'encoding='
```
Expected: EMPTY (every text I/O now explicit). If any remain, they are either intentional binary or a miss — resolve each.

**Step 4: Run the full suite (no behavior change expected)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (same count + the new parse_ts/reducer/active_change tests).

**Step 5: Commit**

```bash
git add src/
git commit -m "chore: explicit encoding=utf-8 on all text file I/O (F11-encoding)"
```

---

## Task 5: F15 wording + close-out trackers (docs only)

**Files:**
- Modify: `private/CAPABILITY-CONVERGENCE-LEDGER.md` (F15a — #51 row; + add this slice's row at close-out, Task 8)
- Modify: `private/CAPABILITY-CONVERGENCE-LEDGER.html` (if it mirrors the #51 row text; + DATA block at close-out)
- Modify: `private/REVIEW-FINDINGS-2026-07-02.md` (mark F6/F11/F15 CLOSED; note F15b decision)
- Modify: `private/OPEN-ITEMS.md` (mark the F6/F11/F15 pointers CLOSED)

This task closes the findings/OPEN-ITEMS trackers and applies the F15a wording. The ledger slice row + `.html` DATA block, and the `NEXT-SESSION-PROMPT.md` rewrite, happen at close-out (**Task 8**, post-merge). `NEXT-SESSION-PROMPT.md` is in the declared `plan ready --scope` but is committed in Task 8 — do NOT leave it with uncommitted staged drift.

**F15a:** The #51 row's outcome cell reads plain `**出血**：...` — the ONLY ledger row asserting "出血" without the "非严格价值出血计数（#45 式）/严格计数仍 1" qualifier every sibling carries. Append the qualifier so the headline strict-count-of-1 narrative is unambiguous, e.g. after the existing sentence: `（属 dogfood 价值兑现，非 #45 式 gate-tripped-in-anger tripwire；严格价值出血计数仍 1）`. Do NOT change the substance of what #51 accomplished — only add the missing qualifier. Sync the `.html` if that row's text is duplicated there.

**F15b:** No code change. The kill-switch path in the allow-reason string (`hook_entry.py`, `_record_bypass` path) appears only AFTER a bypass is already in effect and a `gate_bypassed` audit event is recorded — consistent with #52's declared scope (the norm-level guardrails stay; only literal how-to was removed). Record in REVIEW-FINDINGS that F15b is ACKNOWLEDGED / no-action, with this rationale.

**Step: Commit** (the close-out ledger row + html DATA + NEXT-SESSION rewrite land in Task 8; here commit the findings/OPEN-ITEMS closures + F15a)

```bash
git add private/REVIEW-FINDINGS-2026-07-02.md private/OPEN-ITEMS.md private/CAPABILITY-CONVERGENCE-LEDGER.md private/CAPABILITY-CONVERGENCE-LEDGER.html
git commit -m "docs: unify #51 bleed-count wording (F15a); close F6/F11/F15 in findings + OPEN-ITEMS"
```

---

## Task 6: Reconcile the anchored decision (tax)

After `reducer.py` is edited, its `d-state-pure-fold` fingerprint is stale.

**Step 1: Confirm the fold is still pure** — `derive_state` still constructs and returns a fresh dict, no in-place mutation of inputs/globals, same events→same state. (The change only swapped the drift parse; purity intact.)

**Step 2: Reconcile**

```bash
.venv/bin/super-harness decision reconcile d-state-pure-fold \
  --justification "F11b: drift parse extracted to core.parse_ts primitive; fold remains a pure left-fold (fresh state, no in-place mutation, referentially transparent)."
```

(Exact flag names: verify via `.venv/bin/super-harness decision reconcile --help`.)

**Step 3: Verify decision-check is clean**

```bash
.venv/bin/super-harness decision check
```
Expected: no unreconciled tier-2, no dangling anchors.

**Step 4: Commit**

```bash
git add docs/decisions/d-state-pure-fold.md
git commit -m "chore(decision): reconcile d-state-pure-fold after parse_ts extraction"
```

---

## Task 7: Self-host lifecycle wrap + verification

**Step 1: `done` (runs mypy + full suite)**

```bash
.venv/bin/super-harness done   # or the project's exact command; runs mypy strict + pytest
```
Expected: mypy clean (watch for `datetime | None` typing on `parse_ts`), full suite green. #68 hit a mypy miss here — check the annotations.

**Step 2: Manual mypy sanity (belt-and-suspenders)**

```bash
.venv/bin/mypy src/super_harness/core/parse_ts.py src/super_harness/core/active_change.py src/super_harness/core/reducer.py
```
Expected: no errors.

**Step 3: import-linter (G-FITNESS) — new core module must not break core-is-base**

```bash
PYTHONPATH=src .venv/bin/lint-imports --config .importlinter --no-cache
```
Expected: contracts KEPT.

**Step 4: Two-actor plan review happened BEFORE implementation; two-actor CODE review happens here** (see Discipline below).

Then: `review prepare` → `review approve --reviewer code-reviewer --verdict-file <file>` → `attest write --scope` → PR → CI (11 checks) → merge → `on-merge`.

---

## Task 8: Close-out (post-merge)

Runs AFTER the PR merges and `on-merge` transitions the change to ARCHIVED. These files are gitignored/local trackers — edit directly (no gate) and commit to `main`.

**Files:**
- `private/CAPABILITY-CONVERGENCE-LEDGER.md` — add this slice's row (theme: "collapse to one primitive", same as #64/#68; honesty: **non-value-bleed**, strict count stays 1; note the round-2 Codex robustness catch on `parse_ts` never-raise).
- `private/CAPABILITY-CONVERGENCE-LEDGER.html` — sync the top META/METRICS DATA block + add the mirrored row.
- `private/NEXT-SESSION-PROMPT.md` — rewrite for the NEXT knife (B｜go-public prep, or C｜G-FEEDFORWARD), noting this knife merged and the review backlog is fully closed (only F12 remains, folded into the go-public knife).
- Memory: new slice file under the memory dir + `MEMORY.md` index line + refresh `project-phase-status` latest line + mark F6/F11/F15 CLOSED in `project-review-findings-2026-07-02`.

**Step: Commit**

```bash
git add private/CAPABILITY-CONVERGENCE-LEDGER.md private/CAPABILITY-CONVERGENCE-LEDGER.html private/NEXT-SESSION-PROMPT.md
git commit -m "docs: ledger slice + next-session handoff for parse_ts/encoding debt knife"
```

(Memory files live outside the repo — write them with the Write tool, not committed to git.)

---

## Discipline checklist (do not skip)

1. **Before `plan ready`:** enumerate `--scope` from the FINAL changed-file set (all of: `core/parse_ts.py`, `core/active_change.py`, `core/reducer.py`, the whole encoding-sweep file list, `docs/decisions/d-state-pure-fold.md`, `docs/plans/2026-07-03-parse-ts-and-encoding-debt.md`, `private/CAPABILITY-CONVERGENCE-LEDGER.md` + `.html`, `private/REVIEW-FINDINGS-2026-07-02.md`, `private/OPEN-ITEMS.md`, and all new/changed tests). Missing one → `abandon` + redeclare.
2. **Two-round+ plan review** (Claude subagent + `codex exec --sandbox read-only`, self-contained brief with hard constraints) BEFORE implementation, to convergence.
3. **Two-actor code review** after implementation, before `attest write`.
4. **`attest write`** covering scope, then PR, then CI (11 checks), then merge, then **`on-merge`** (manual — do NOT forget, or the change stays non-terminal and hijacks the gate).
5. **Close-out:** update `CAPABILITY-CONVERGENCE-LEDGER.md` + `.html` (META/METRICS DATA block), `OPEN-ITEMS.md`, `REVIEW-FINDINGS-2026-07-02.md` (F6/F11/F15 → CLOSED), and memory (new slice file + `MEMORY.md` index + `project-phase-status` latest line).

## Non-goals (do not gold-plate)

- Do NOT touch F12 (plugin surface) or the daemon — reserved for the "go public" knife.
- Do NOT convert `parse_ts` into a general date library; it is exactly the two shapes state.yaml/events.jsonl produce.
- Do NOT add `errors="replace"` in the encoding sweep (only `state_snapshot` owns that seam).
- Do NOT widen reducer exception handling beyond what `parse_ts` already guarantees.
