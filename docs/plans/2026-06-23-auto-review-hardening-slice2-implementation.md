# Auto-review hardening slice-2 Implementation Plan (D rework-loop teeth + E skip/attest gate)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the code-review rework loop teeth (an approve out of a rejected review must dispose every open finding) and close the skip bypass (a bare `review skip` of code-reviewer becomes a merge-gate blocker unless a deliberate `--override --reason` is recorded).

**Architecture:** D is an emit-time check on `review approve` (only when state is `CODE_REVIEW_REJECTED`) that derives the open-finding set from the raw event stream and requires the verdict's `prior_findings` to dispose all of them. E is a verify-time check: `review skip` gains `--override`; `attest verify` blocks a validated attestation whose terminal code-review is a non-overridden skip. No state-machine change, no new event type — only payload flags + emit-time / verify-time checks.

**Tech Stack:** Python 3.10+, click, pytest, PyYAML. Verify with `PATH="$(pwd)/.venv/bin:$PATH"` (never `uv run` in-project, per project discipline).

**Design SSOT:** `docs/plans/2026-06-23-auto-review-hardening-slice2-design.md` (and umbrella `2026-06-23-auto-review-hardening-design.md` §4.D/§4.E/§10).

**Conventions verified in-repo:**
- `tests/unit/core/test_review_verdict.py` — `_write(tmp_path, text)` helper; `_OK` verdict fixture.
- `tests/unit/cli/test_review.py` — `_emit/_seed/_state/_event_types` CliRunner helpers.
- `tests/unit/cli/test_review_verdict_gate.py` — `_git/_repo_change/_good_verdict/_prepare_digest` full git+lifecycle harness for code-review approve.
- `tests/unit/engineering/test_attestation.py` — `_emit(writer, etype, slug, payload)`, `_ready_with_scope(root, slug, files)`, `DiffEntry` usage.
- Commit style: conventional commits, English. Commit after each task.

---

## File structure (locks in decomposition)

- `src/super_harness/core/review_verdict.py` (modify) — add `read_change_events`, extend `parse_verdict_file` (require `findings[].id` + validate `prior_findings`), add `derive_open_findings`, add `check_disposed`.
- `src/super_harness/cli/review.py` (modify) — D check folded into `_validate_code_review_verdict`; `skip` gains `--override` + reason-required.
- `src/super_harness/engineering/attestation.py` (modify) — `derive_independence` surfaces `override`/`reason` in the `code_review` sub-dict; `verify_attestations` appends a blocker for a validated slug whose terminal code-review is a non-overridden skip.
- `src/super_harness/cli/attest.py` (modify) — `_independence_line` discloses override + reason.
- `scripts/gen_cli_reference.py` (modify) — add `"review skip"` `_EXIT_CODES` entry.
- `src/super_harness/adapters/agent/claude_code.py` (modify) — rewrite the `review skip` AGENTS.md wording + add the dispose-findings rule.
- `docs/cli-reference.md`, `AGENTS.md` (regenerated, not hand-edited).
- `private/OPEN-ITEMS.md` (modify) — record the deferred reducer field.

Tasks 1–4 are pure helpers (fast TDD). Tasks 5–6 are CLI (D + skip). Tasks 7–9 are attest (E). Task 10 is doc-sync. Task 11 is OPEN-ITEMS + ledger wrap.

---

## Task 1: `read_change_events` — tolerant stream reader

**Files:**
- Modify: `src/super_harness/core/review_verdict.py`
- Test: `tests/unit/core/test_review_verdict.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/core/test_review_verdict.py`:

```python
def test_read_change_events_filters_and_tolerates(tmp_path: Path) -> None:
    from super_harness.core.review_verdict import read_change_events

    f = tmp_path / "events.jsonl"
    f.write_text(
        '{"event_id":"e1","type":"intent_declared","change_id":"c",'
        '"timestamp":"2026-06-23T00:00:00Z",'
        '"actor":{"type":"human","identifier":"t"},"framework":"plain","payload":{}}\n'
        "this is not json\n"
        '{"event_id":"e2","type":"code_review_failed","change_id":"other",'
        '"timestamp":"2026-06-23T00:00:01Z",'
        '"actor":{"type":"human","identifier":"t"},"framework":"plain","payload":{}}\n'
        '{"event_id":"e3","type":"code_review_failed","change_id":"c",'
        '"timestamp":"2026-06-23T00:00:02Z",'
        '"actor":{"type":"human","identifier":"t"},"framework":"plain","payload":{}}\n'
    )
    evs = read_change_events(f, "c")
    assert [e.event_id for e in evs] == ["e1", "e3"]  # malformed skipped, "other" filtered


def test_read_change_events_missing_file_returns_empty(tmp_path: Path) -> None:
    from super_harness.core.review_verdict import read_change_events

    assert read_change_events(tmp_path / "nope.jsonl", "c") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -k read_change_events -v`
Expected: FAIL — `ImportError: cannot import name 'read_change_events'`.

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/core/review_verdict.py`, add imports at top (after the existing `from pathlib import Path`):

```python
from super_harness.core.events import Event, EventSchemaError, parse_event_line
```

Add the function (after `parse_verdict_file`):

```python
def read_change_events(events_file: Path, change_id: str) -> list[Event]:
    """Read the parsed events for one change, in append order (TOLERANT).

    Mirrors the reducer's read-tolerant policy: malformed lines are skipped, never
    raised — events.jsonl may carry lines from older tool versions or partial
    writes, and an emit-time check that crashes on those would be fail-open.
    Returns an empty list if the file does not exist.
    """
    if not events_file.exists():
        return []
    out: list[Event] = []
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError:
            continue
        if ev.change_id == change_id:
            out.append(ev)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -k read_change_events -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_verdict.py tests/unit/core/test_review_verdict.py
git commit -m "feat(review): add tolerant read_change_events stream reader (slice-2 D)"
```

---

## Task 2: extend `parse_verdict_file` — require `findings[].id` + validate `prior_findings`

**Files:**
- Modify: `src/super_harness/core/review_verdict.py:60-68` (findings loop + return)
- Test: `tests/unit/core/test_review_verdict.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/core/test_review_verdict.py`:

```python
# NOTE: severity FIRST so `id:` is on its own 4-space-indented line and the
# string-replace below actually strips it (B1 fix — a `- id:` inline list item
# cannot be stripped by line).
_FAIL_NO_ID = """
bundle_digest: abc123
checklist:
  - item: spec-compliance
    status: fail
findings:
  - severity: blocker
    file: src/x.py
    summary: boom
"""


def test_findings_require_id(tmp_path: Path) -> None:
    with pytest.raises(VerdictError, match="id"):
        parse_verdict_file(_write(tmp_path, _FAIL_NO_ID))


def test_prior_findings_shape_validated(tmp_path: Path) -> None:
    base = _OK + "prior_findings:\n  - id: f-001\n    disposition: resolved\n"
    assert parse_verdict_file(_write(tmp_path, base))  # resolved needs no note → ok

    bad_disp = _OK + "prior_findings:\n  - id: f-001\n    disposition: bogus\n"
    with pytest.raises(VerdictError, match="disposition"):
        parse_verdict_file(_write(tmp_path, bad_disp))

    wontfix_no_note = _OK + "prior_findings:\n  - id: f-001\n    disposition: wontfix\n"
    with pytest.raises(VerdictError, match="note"):
        parse_verdict_file(_write(tmp_path, wontfix_no_note))

    missing_id = _OK + "prior_findings:\n  - disposition: resolved\n"
    with pytest.raises(VerdictError, match="id"):
        parse_verdict_file(_write(tmp_path, missing_id))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -k "findings_require_id or prior_findings_shape" -v`
Expected: FAIL — `parse_verdict_file` does not yet enforce `id` / `prior_findings`.

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/core/review_verdict.py`, add the disposition set near the top constants:

```python
_DISPOSITIONS = {"resolved", "wontfix"}
```

Replace the findings-validation loop + return (currently lines ~60-68):

```python
    findings = parsed.get("findings") or []
    if not isinstance(findings, list):
        raise VerdictError("verdict.findings must be a list")
    for f in findings:
        if not isinstance(f, dict) or f.get("severity") not in _SEVERITIES:
            raise VerdictError(f"each finding needs severity in {sorted(_SEVERITIES)}: {f!r}")
        if not isinstance(f.get("id"), str) or not f["id"]:
            raise VerdictError(f"each finding needs a non-empty string `id`: {f!r}")
    if any_fail and not findings:
        raise VerdictError("a checklist item is `fail` but findings is empty")
    prior = parsed.get("prior_findings") or []
    if not isinstance(prior, list):
        raise VerdictError("verdict.prior_findings must be a list")
    for pf in prior:
        if not isinstance(pf, dict) or not isinstance(pf.get("id"), str) or not pf["id"]:
            raise VerdictError(f"each prior_finding needs a non-empty string `id`: {pf!r}")
        if pf.get("disposition") not in _DISPOSITIONS:
            raise VerdictError(
                f"prior_finding[{pf['id']!r}].disposition must be one of {sorted(_DISPOSITIONS)}"
            )
        if pf["disposition"] == "wontfix" and not (isinstance(pf.get("note"), str) and pf["note"]):
            raise VerdictError(f"prior_finding[{pf['id']!r}] disposition=wontfix requires a note")
    return parsed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -v`
Expected: PASS (all, including pre-existing tests — `_OK`/gate fixtures use `findings: []`, unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_verdict.py tests/unit/core/test_review_verdict.py
git commit -m "feat(review): require findings[].id + validate prior_findings shape (slice-2 D)"
```

---

## Task 3: `derive_open_findings` — append-order walker

**Files:**
- Modify: `src/super_harness/core/review_verdict.py`
- Test: `tests/unit/core/test_review_verdict.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/core/test_review_verdict.py`:

```python
def _failed(slug: str, findings: list[str], prior: list[tuple[str, str]] | None = None):
    from super_harness.core.events import Actor, Event
    from super_harness.core.ulid import new_event_id

    verdict = {
        "findings": [{"id": i, "severity": "major", "file": "x", "summary": "s"} for i in findings],
        "prior_findings": [{"id": i, "disposition": d, "note": "n"} for i, d in (prior or [])],
    }
    return Event(
        event_id=new_event_id(), type="code_review_failed", change_id=slug,
        timestamp="2026-06-23T00:00:00Z",
        actor=Actor(type="human", identifier="t"), framework="plain",
        payload={"verdict": verdict},
    )


def test_open_findings_single_reject() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    assert derive_open_findings([_failed("c", ["f1", "f2"])], "c") == ["f1", "f2"]


def test_open_findings_resolved_in_later_reject() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    evs = [_failed("c", ["f1", "f2"]), _failed("c", ["f3"], prior=[("f1", "resolved")])]
    assert derive_open_findings(evs, "c") == ["f2", "f3"]


def test_open_findings_resolved_then_reopened() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    # reject2 disposes f1 AND re-lists it → reopened, stays open
    evs = [_failed("c", ["f1"]), _failed("c", ["f1"], prior=[("f1", "resolved")])]
    assert derive_open_findings(evs, "c") == ["f1"]


def test_open_findings_dispose_unknown_id_is_noop() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    evs = [_failed("c", ["f1"], prior=[("ghost", "resolved")])]
    assert derive_open_findings(evs, "c") == ["f1"]


def test_open_findings_ignores_other_change_and_non_failed() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    assert derive_open_findings([_failed("other", ["f1"])], "c") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -k open_findings -v`
Expected: FAIL — `ImportError: cannot import name 'derive_open_findings'`.

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/core/review_verdict.py`, add (the `Event` import from Task 1 is already present):

```python
def derive_open_findings(events: list[Event], change_id: str) -> list[str]:
    """Open-finding ids the next approve must dispose, in append order.

    Walk every `code_review_failed` verdict for the change in append order; per
    verdict dispose its `prior_findings` ids FIRST, then add its `findings` ids
    (discard-then-add → a resolved finding re-listed by a later reject reopens).
    Tolerant: entries with a missing/non-string `id` are skipped (the raw stream
    can carry pre-validation payloads). See design slice-2 §4.D.
    """
    open_ids: dict[str, None] = {}  # ordered set: insertion-order preserved
    for ev in events:
        if ev.change_id != change_id or ev.type != "code_review_failed":
            continue
        verdict = (ev.payload or {}).get("verdict") or {}
        for pf in verdict.get("prior_findings") or []:
            pid = pf.get("id") if isinstance(pf, dict) else None
            if isinstance(pid, str):
                open_ids.pop(pid, None)
        for f in verdict.get("findings") or []:
            fid = f.get("id") if isinstance(f, dict) else None
            if isinstance(fid, str):
                open_ids[fid] = None
    return list(open_ids)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -k open_findings -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_verdict.py tests/unit/core/test_review_verdict.py
git commit -m "feat(review): derive_open_findings append-order walker (slice-2 D)"
```

---

## Task 4: `check_disposed` — undisposed-open helper

**Files:**
- Modify: `src/super_harness/core/review_verdict.py`
- Test: `tests/unit/core/test_review_verdict.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/core/test_review_verdict.py`:

```python
def test_check_disposed() -> None:
    from super_harness.core.review_verdict import check_disposed

    verdict = {"prior_findings": [{"id": "f1", "disposition": "resolved"}]}
    assert check_disposed(verdict, ["f1"]) == []
    assert check_disposed(verdict, ["f1", "f2"]) == ["f2"]  # order preserved
    assert check_disposed({}, ["f1"]) == ["f1"]  # no prior_findings → all undisposed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -k check_disposed -v`
Expected: FAIL — `ImportError: cannot import name 'check_disposed'`.

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/core/review_verdict.py`, add:

```python
def check_disposed(verdict: dict[str, Any], open_ids: list[str]) -> list[str]:
    """Return the open ids the verdict's `prior_findings` does NOT dispose (in order)."""
    disposed = {
        pf["id"] for pf in (verdict.get("prior_findings") or [])
        if isinstance(pf, dict) and isinstance(pf.get("id"), str)
    }
    return [i for i in open_ids if i not in disposed]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_review_verdict.py -k check_disposed -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/review_verdict.py tests/unit/core/test_review_verdict.py
git commit -m "feat(review): check_disposed helper for rework-loop teeth (slice-2 D)"
```

---

## Task 5: wire D into `review approve` (only from `CODE_REVIEW_REJECTED`)

**Files:**
- Modify: `src/super_harness/cli/review.py:152-202` (`_validate_code_review_verdict`) + imports
- Test: `tests/unit/cli/test_review_verdict_gate.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_review_verdict_gate.py` (reuses `_repo_change/_prepare_digest/_good_verdict/_git`):

```python
def _to_rejected(ws: Path, finding_id: str = "f-001") -> None:
    """Drive c from AWAITING_CODE_REVIEW into CODE_REVIEW_REJECTED with one finding."""
    from super_harness.core.events import Actor, Event
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    EventWriter(events_path(ws)).emit(Event(
        event_id=new_event_id(), type="code_review_failed", change_id="c",
        timestamp="2026-06-23T00:01:00Z",
        actor=Actor(type="human", identifier="cli"), framework="plain",
        payload={"verdict": {"findings": [
            {"id": finding_id, "severity": "blocker", "file": "src/a.py", "summary": "boom"}]}}))
    refresh_state_after_emit(ws)


def _verdict_with_prior(ws: Path, digest: str, prior: str) -> Path:
    p = ws / "v_prior.yaml"
    items = "\n".join(f"  - item: {i}\n    status: pass"
                      for i in ["spec-compliance", "scope-adherence", "code-quality", "edge-cases"])
    p.write_text(f"bundle_digest: {digest}\nchecklist:\n{items}\nfindings: []\n{prior}")
    return p


def test_approve_from_rejected_blocks_undisposed_finding(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    _to_rejected(ws)
    p = _good_verdict(ws, digest)  # no prior_findings → f-001 undisposed
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_VALIDATION, r.output
    assert "f-001" in r.output


def test_approve_from_rejected_passes_when_disposed(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)
    digest = _prepare_digest(ws)
    _to_rejected(ws)
    p = _verdict_with_prior(
        ws, digest, "prior_findings:\n  - id: f-001\n    disposition: resolved\n")
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output


def test_approve_from_awaiting_does_not_require_prior(tmp_path: Path) -> None:
    ws = _repo_change(tmp_path)  # state AWAITING_CODE_REVIEW, no reject
    digest = _prepare_digest(ws)
    p = _good_verdict(ws, digest)
    r = CliRunner().invoke(main, ["--workspace", str(ws), "review", "approve", "c",
                                  "--reviewer", "code-reviewer", "--verdict-file", str(p)])
    assert r.exit_code == EXIT_OK, r.output  # D inert from AWAITING_CODE_REVIEW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_verdict_gate.py -k "from_rejected or from_awaiting" -v`
Expected: FAIL — `test_approve_from_rejected_blocks_undisposed_finding` passes wrongly with EXIT_OK (no D check yet).

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/cli/review.py`, extend the import from `review_verdict`:

```python
from super_harness.core.review_verdict import (
    VerdictError,
    check_coverage,
    check_disposed,
    derive_open_findings,
    parse_verdict_file,
    read_change_events,
)
```

At the end of `_validate_code_review_verdict`, **immediately before the final
`return verdict` (review.py:202)** — NOT near the top — insert the D check. `cs` is
bound earlier at review.py:182 (`cs = derive_state(events_path(root)).get(change)`),
so inserting before line 202 keeps it in scope; inserting before line 182 would
`NameError`:

```python
    # D (slice-2): an approve emitted FROM CODE_REVIEW_REJECTED must dispose every
    # open finding from prior code_review_failed verdicts. Inert otherwise.
    if cs is not None and cs.current_state == "CODE_REVIEW_REJECTED":
        events = read_change_events(events_path(root), change)
        open_ids = derive_open_findings(events, change)
        undisposed = check_disposed(verdict, open_ids)
        if undisposed:
            click.echo(format_error(subcommand=subcommand,
                message=f"approve does not dispose prior open finding(s): {', '.join(undisposed)}",
                hint="Add a prior_findings entry (resolved | wontfix+note) for each open finding."),
                err=True)
            sys.exit(EXIT_VALIDATION)
    return verdict
```

(Note: `cs` is already bound earlier in the function via `derive_state(events_path(root)).get(change)`; reuse it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review_verdict_gate.py -v`
Expected: PASS (all, including the three new + four pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/review.py tests/unit/cli/test_review_verdict_gate.py
git commit -m "feat(review): rework-loop teeth — approve from rejected must dispose findings (slice-2 D)"
```

---

> Tasks 6–11 continue in this file below.

## Task 6: `review skip --override` (reason required on override)

**Files:**
- Modify: `src/super_harness/cli/review.py:267-285` (the `skip` command)
- Test: `tests/unit/cli/test_review.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_review.py` (reuses `_seed/_state/_event_types`):

```python
def test_skip_override_requires_reason(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved",
          "implementation_started", "verification_passed", "implementation_complete")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "skip", "c",
                                  "--reviewer", "code-reviewer", "--override"])
    assert r.exit_code == 2, r.output
    assert "reason" in r.output.lower()
    assert _event_types(tmp_path)[-1] == "implementation_complete"  # nothing emitted


def test_skip_override_stamps_payload(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved",
          "implementation_started", "verification_passed", "implementation_complete")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "skip", "c",
                                  "--reviewer", "code-reviewer", "--override",
                                  "--reason", "deadlocked CI"])
    assert r.exit_code == 0, r.output
    last = json.loads(events_path(tmp_path).read_text().splitlines()[-1])
    assert last["payload"]["skipped"] is True
    assert last["payload"]["override"] is True
    assert last["payload"]["reason"] == "deadlocked CI"


def test_bare_skip_defaults_reason_no_override(tmp_path: Path) -> None:
    _seed(tmp_path, "c", "intent_declared", "plan_ready", "plan_approved",
          "implementation_started", "verification_passed", "implementation_complete")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "review", "skip", "c",
                                  "--reviewer", "code-reviewer"])
    assert r.exit_code == 0, r.output
    last = json.loads(events_path(tmp_path).read_text().splitlines()[-1])
    assert last["payload"]["reason"] == "manual_skip"
    assert "override" not in last["payload"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review.py -k "skip_override or bare_skip_defaults" -v`
Expected: FAIL — `--override` is an unknown option.

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/cli/review.py`, replace the whole `skip` command (currently ~267-285) with:

```python
@review_group.command("skip")
@click.argument("change")
@_reviewer_opt
@click.option("--reason", default=None, help="Audit reason recorded on the event "
              "(default: manual_skip; REQUIRED with --override).")
@click.option("--override", is_flag=True, default=False,
              help="Deliberate, disclosed override: a bare skip blocks at the merge "
                   "gate; --override (with --reason) passes-with-disclosure.")
@_as_opt
@click.pass_context
def skip(ctx: click.Context, change: str, reviewer: str, reason: str | None,
         override: bool, as_identity: str | None) -> None:
    """Escape hatch — PASS a stuck reviewer (== approve with reason=manual_skip).

    Stamps ``payload["skipped"]=True`` so the merge-boundary disclosure can tell a
    skipped review from a real one. A bare skip of ``code-reviewer`` is a merge-gate
    blocker (``attest verify``); ``--override --reason "<why>"`` stamps
    ``payload["override"]=True`` and is treated as pass-with-disclosure (slice-2 E).
    """
    if override and not reason:
        click.echo(format_error(subcommand="review skip",
            message="--override requires --reason explaining the deliberate skip.",
            hint='e.g. review skip <c> --reviewer code-reviewer --override --reason "why".'),
            err=True)
        sys.exit(EXIT_VALIDATION)
    extra: dict[str, object] = {"skipped": True}
    if override:
        extra["override"] = True
    _emit_verdict(
        ctx, subcommand="review skip", change=change, reviewer=reviewer,
        event_type=_REVIEWER_PASS[reviewer], reason=reason or "manual_skip",
        as_identity=as_identity, extra_payload=extra,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_review.py -v`
Expected: PASS (all, including pre-existing skip tests — bare skip still defaults to `manual_skip`).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/review.py tests/unit/cli/test_review.py
git commit -m "feat(review): skip --override (reason required) stamps payload.override (slice-2 E)"
```

---

## Task 7: `derive_independence` surfaces `override` + `reason`

**Files:**
- Modify: `src/super_harness/engineering/attestation.py:244-282` (`derive_independence`)
- Test: `tests/unit/engineering/test_attestation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/engineering/test_attestation.py`:

```python
def _events(*specs):
    """Build a list[Event] from (type, payload) specs for change 'c'."""
    out = []
    for etype, payload in specs:
        out.append(Event(
            event_id=new_event_id(), type=etype, change_id="c",
            timestamp=utc_now_iso(), actor=Actor(type="human", identifier="t"),
            framework="plain", payload=payload or {}))
    return out


def test_derive_independence_surfaces_override_and_reason():
    evs = _events(
        ("intent_declared", {}),
        ("code_review_passed", {"reviewer": "code-reviewer", "reason": "deadlock",
                                "skipped": True, "override": True}))
    cr = derive_independence(evs)["code_review"]
    assert cr["skipped"] is True
    assert cr["override"] is True
    assert cr["reason"] == "deadlock"


def test_derive_independence_bare_skip_no_override():
    evs = _events(
        ("intent_declared", {}),
        ("code_review_passed", {"reviewer": "code-reviewer", "reason": "manual_skip",
                                "skipped": True}))
    cr = derive_independence(evs)["code_review"]
    assert cr["skipped"] is True
    assert cr["override"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_attestation.py -k derive_independence_surfaces -v`
Expected: FAIL — `KeyError: 'override'`.

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/engineering/attestation.py`, inside `derive_independence`, extend the `reviews` branch to capture override/reason and add them to the returned `code_review` dict. Replace the body after `reviews = [...]`:

```python
    if not reviews:
        cls, reviewer, skipped, override, reason = "unattributed", None, False, False, None
    else:
        r = reviews[-1]  # last wins (reject → re-review cycles)
        reviewer = r.actor.identifier
        skipped = r.payload.get("skipped") is True
        override = r.payload.get("override") is True
        reason = r.payload.get("reason")
        if r.actor.type == "ci":
            cls = "ci"
        elif skipped:
            cls = "skipped"
        elif reviewer == PLACEHOLDER_IDENTITY or author == PLACEHOLDER_IDENTITY:
            cls = "unattributed"
        elif reviewer == author:
            cls = "self-signed"
        else:
            cls = "independent"
    return {
        "author": author,
        "code_review": {
            "classification": cls, "reviewer": reviewer, "skipped": skipped,
            "override": override, "reason": reason,
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_attestation.py -k derive_independence -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/engineering/attestation.py tests/unit/engineering/test_attestation.py
git commit -m "feat(attest): derive_independence surfaces override + reason (slice-2 E)"
```

---

## Task 8: E merge-gate blocker in `verify_attestations`

**Files:**
- Modify: `src/super_harness/engineering/attestation.py:207-238` (`verify_attestations` validated loop)
- Test: `tests/unit/engineering/test_attestation.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/engineering/test_attestation.py` (a skip-variant of `_ready_with_scope`):

```python
def _ready_skip_scope(root: Path, slug: str, files: list[str], *, override: bool) -> None:
    att_dir = root / ".harness" / "attestations"
    att_dir.mkdir(parents=True, exist_ok=True)
    w = EventWriter(att_dir / f"{slug}.jsonl")
    _emit(w, "intent_declared", slug)
    _emit(w, "plan_ready", slug, {"scope": {"files": files}})
    _emit(w, "plan_approved", slug)
    _emit(w, "implementation_started", slug)
    _emit(w, "verification_passed", slug)
    _emit(w, "implementation_complete", slug)
    pay = {"reviewer": "code-reviewer", "reason": "why", "skipped": True}
    if override:
        pay["override"] = True
    _emit(w, "code_review_passed", slug, pay)


def test_verify_bare_skip_blocks(tmp_path):
    _ready_skip_scope(tmp_path, "s", ["src/x.py"], override=False)
    diff = [DiffEntry("A", (".harness/attestations/s.jsonl",)), DiffEntry("M", ("src/x.py",))]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("skipped without --override" in b for b in v.blockers)


def test_verify_override_skip_passes(tmp_path):
    _ready_skip_scope(tmp_path, "s", ["src/x.py"], override=True)
    diff = [DiffEntry("A", (".harness/attestations/s.jsonl",)), DiffEntry("M", ("src/x.py",))]
    v = verify_attestations(tmp_path, diff)
    assert v.ok, v.blockers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_attestation.py -k "bare_skip_blocks or override_skip_passes" -v`
Expected: FAIL — `test_verify_bare_skip_blocks` passes wrongly (no E blocker yet).

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/engineering/attestation.py`, in `verify_attestations`, inside the `for slug in added_slugs:` loop, after `covered |= this_covered` and `validated.append(slug)`, append the E classification (still inside the loop, only for a slug that reached `validated`):

```python
        cr = independence_for_attestation(att_path)["code_review"]
        if cr["skipped"] and not cr["override"]:
            blockers.append(
                f"attestation {slug}: code review was skipped without --override "
                "(a deliberate `review skip --override --reason ...` is required to merge)"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/engineering/test_attestation.py -v`
Expected: PASS (all). This is the honest **E bite demonstration surface** (design §10): a constructed `DiffEntry` diff with a committed skip attestation → blocker; override → pass.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/engineering/attestation.py tests/unit/engineering/test_attestation.py
git commit -m "feat(attest): block merge on non-overridden terminal code-review skip (slice-2 E)"
```

---

## Task 9: disclose override in `_independence_line`

**Files:**
- Modify: `src/super_harness/cli/attest.py:50-65` (`_independence_line`)
- Test: `tests/unit/cli/test_attest.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_attest.py` (import `_independence_line` from `super_harness.cli.attest`):

```python
def test_independence_line_override_skip():
    from super_harness.cli.attest import _independence_line
    line = _independence_line(
        {"classification": "skipped", "reviewer": "t", "skipped": True,
         "override": True, "reason": "deadlock"})
    assert "OVERRIDE" in line
    assert "deadlock" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_attest.py -k override_skip -v`
Expected: FAIL — the line does not mention OVERRIDE/reason.

- [ ] **Step 3: Write minimal implementation**

In `src/super_harness/cli/attest.py`, replace the `skipped` branch of `_independence_line`:

```python
    if cls == "skipped":
        if item.get("override"):
            return f"review independence: skipped (OVERRIDE: {item.get('reason')}) — {who}"
        return f"review independence: skipped — {who}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/cli/test_attest.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/attest.py tests/unit/cli/test_attest.py
git commit -m "feat(attest): disclose override + reason on skipped review line (slice-2 E)"
```

---

## Task 10: doc-sync (gen_cli_reference + AGENTS.md source + regenerate)

**Files:**
- Modify: `scripts/gen_cli_reference.py:259-269` (`_EXIT_CODES`)
- Modify: `src/super_harness/adapters/agent/claude_code.py:105-117` (AGENTS.md review-protocol source)
- Regenerate: `docs/cli-reference.md`, `AGENTS.md`

- [ ] **Step 1: Add the `review skip` exit-code entry**

In `scripts/gen_cli_reference.py`, inside `_EXIT_CODES`, after the `"review approve"` block, add:

```python
    "review skip": [
        "`0` skip recorded (`code_review_passed` / `plan_approved` emitted, `skipped=True`)",
        "`2` --override without --reason",
        "`3` no `.harness/`",
    ],
```

- [ ] **Step 2: Rewrite the AGENTS.md review-protocol source**

In `src/super_harness/adapters/agent/claude_code.py`, replace the `review skip` line (currently the "escape hatch (records an approval with `reason=manual_skip`)" sentence) and add the dispose-findings rule to the approve bullet. Replace the skip line with:

```python
- `super-harness review skip <change> --reviewer <name>` PASSes a stuck reviewer, but for
  `code-reviewer` a BARE skip is a MERGE-GATE BLOCKER (`attest verify` fails). To merge with
  a skip you must record a deliberate, disclosed override:
  `review skip <change> --reviewer code-reviewer --override --reason "<why>"`.
```

In the approve bullet (item 4), append after the stale-digest sentence:

```python
     If the approval comes out of a REJECTED review, the verdict's `prior_findings` must
     dispose EVERY open finding from the prior `code_review_failed` verdicts
     (`disposition: resolved | wontfix`; `wontfix` needs a `note`) or the approve is refused.
```

- [ ] **Step 3: Regenerate BOTH derived docs (two separate generators — R2-MAJOR)**

`docs/cli-reference.md` is a `doc check` derived doc; **AGENTS.md is NOT** — it is a
`sync`-managed artifact (rendered by `engineering.agents_md_render`, registry
`.harness/derived-docs.yaml` lists only cli-reference + state-machine). CI runs
**both** `doc check` and `sync --check` (`.github/workflows/doc-check.yml`), so you
must run both regenerators or `sync --check` fails the PR:

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix    # regen docs/cli-reference.md
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md -y # re-render the AGENTS.md section
```
Expected: cli-reference.md rewritten (exit 0); AGENTS.md super-harness section re-rendered.

- [ ] **Step 4: Verify no drift remains (BOTH gates)**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check    # exit 0, no drift
PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --check  # exit 0, no AGENTS.md/.gitignore drift
```
Expected: both PASS. Also `grep -n "review skip" docs/cli-reference.md` shows the new
exit-code rows; `grep -n "override" AGENTS.md` shows the rewritten skip text.

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_cli_reference.py src/super_harness/adapters/agent/claude_code.py docs/cli-reference.md AGENTS.md
git commit -m "docs(review): sync CLI reference + AGENTS.md for skip --override + dispose rule (slice-2)"
```

---

## Task 11: record the deferred reducer field in OPEN-ITEMS

**Files:**
- Modify: `private/OPEN-ITEMS.md`

- [ ] **Step 1: Append the deferred-item entry**

Add under the `①b auto-review slice-2` section in `private/OPEN-ITEMS.md` (use the Edit tool or Bash append — `private/` is gitignored, so this is NOT part of the attested scope and is committed separately / left local per project convention):

```
### reducer latest-verdict field — DEFERRED pending real consumer (decided 2026-06-23, slice-2)
The umbrella §4.B floated retaining the latest code-review verdict on ChangeState (mirror of
`scope`). Slice-2 does NOT add it: its only designed consumer (C freshness) ships via HEAD
digest recompute (slice-1, tamper-evident) and D's open set is structurally a stream-walker
job (single field can't express multi-reject). Adding it now = orphaned dead code. Add it when
a real consumer appears (e.g. a v0.2 `change show` / status surface that wants the last verdict
inline), with a test against that use. Not blocked — just no consumer yet.
```

- [ ] **Step 2: (no commit required for gitignored private/)**

`private/OPEN-ITEMS.md` is gitignored; record it locally. No code commit. The capability-convergence ledger wrap (below) is also a `private/` edit.

---

## Final: full-repo verification + self-host merge gate

After all tasks, BEFORE `done`/PR (the implementer must run the whole suite, not just changed files — `done` runs full-repo ruff + mypy):

- [ ] `PATH="$(pwd)/.venv/bin:$PATH" ruff check src/ tests/` → clean
- [ ] `PATH="$(pwd)/.venv/bin:$PATH" mypy src/` → clean
- [ ] `PATH="$(pwd)/.venv/bin:$PATH" pytest -q` → all green

Then run the self-host merge-gate sequence from `private/NEXT-SESSION-PROMPT.md` §"成 PR 过 self-host 合并门" (branch → `change start` → `plan ready --scope '[<exact file list>]'` → plan-reviewer approve → `implementation start` → implement → full ruff/mypy/pytest → `done` → `review prepare` → independent reviewer subagent → `review approve --verdict-file` → `attest write` + commit → local `attest verify --base main --head HEAD` dry-run → push → `gh pr create` → CI green → squash merge → `on-merge`).

**Scope (exact files for `plan ready --scope`, no directory prefixes):**
```
src/super_harness/core/review_verdict.py
src/super_harness/cli/review.py
src/super_harness/engineering/attestation.py
src/super_harness/cli/attest.py
scripts/gen_cli_reference.py
src/super_harness/adapters/agent/claude_code.py
docs/cli-reference.md
AGENTS.md
tests/unit/core/test_review_verdict.py
tests/unit/cli/test_review_verdict_gate.py
tests/unit/cli/test_review.py
tests/unit/engineering/test_attestation.py
tests/unit/cli/test_attest.py
docs/plans/2026-06-23-auto-review-hardening-slice2-design.md
docs/plans/2026-06-23-auto-review-hardening-slice2-implementation.md
```

**Throwaway bite demos (design §10 — capture output for the ledger):**
- **D bite:** the Task-5 test `test_approve_from_rejected_blocks_undisposed_finding` IS the emit-layer bite (EXIT_VALIDATION + "f-001"). Re-run it and capture.
- **E bite:** the Task-8 test `test_verify_bare_skip_blocks` IS the verify-layer bite (`ok=False`, "skipped without --override") — the honest surface per R2 (no fragile git-diff CLI scaffold).

**Ledger wrap (`private/CAPABILITY-CONVERGENCE-LEDGER.md`, IN-PLACE):** add #48 row; update the 3 dashboard items (#38→latest / ①b slice-1+2 status / dogfood-bite still "engaged + throwaway self-bite demonstrated, no real evasion caught"); update the convergence judgement (is ①b closer to "closed loop" or still "engaged not bleeding"). Honest framing: teeth engaged + demonstrated biting on constructed cases; NOT "caught a real reject-skip evasion in the wild."

---

## Self-review (filled — spec coverage check)

- §4.D walker + tolerance → Tasks 1,3. `findings[].id` + prior_findings shape → Task 2. `check_disposed` → Task 4. D emit wiring (only from CODE_REVIEW_REJECTED) → Task 5. ✓
- §4.E skip --override + reason-required → Task 6. derive_independence override/reason → Task 7. verify_attestations blocker → Task 8. disclosure line → Task 9. ✓
- §9 doc-sync (gen_cli_reference + AGENTS.md source + regen) → Task 10. ✓
- §7 reducer deferral → Task 11. ✓
- §10 self-host bootstrap + throwaway bites → Final section. ✓
- Types consistent: `read_change_events`/`derive_open_findings`/`check_disposed` signatures match across Tasks 1–5; `code_review` sub-dict keys (`override`/`reason`) match across Tasks 7–9.
