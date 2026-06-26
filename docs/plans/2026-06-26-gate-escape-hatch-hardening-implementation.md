# Gate Escape-Hatch Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the PreToolUse gate from teaching agents the kill-switch escape hatch (Part A), and disclose any gate bypass at the merge gate so a bypass can't hide (Part C, strict).

**Architecture:** A = text changes across agent channels (3 hook block messages + 2 AGENTS.md subsections + status `next:` + reframed adapter doc). C = a new `gate_bypassed` audit event recorded on every kill-switch ALLOW (emitted with `skip_validation=True`, daemon-independent), auto-snapshotted into the committed attestation, and an undisclosed bypass made a merge-gate blocker via a new pure helper mirroring `derive_independence`. Design: `docs/plans/2026-06-26-gate-escape-hatch-hardening-design.md`.

**Tech Stack:** Python 3.10+, click, stdlib, pytest. Verify with `V=PATH="$(pwd)/.venv/bin:$PATH"`.

---

## File Structure

- `src/super_harness/core/events.py` — register 2 new event types in `KNOWN_EVENT_TYPES`.
- `src/super_harness/core/transitions.py` — add 2 types to `_INFORMATIONAL`.
- `src/super_harness/daemon/hook_entry.py` — A.1 (3 block messages → shared halt constant) + C.1 (`_record_bypass`).
- `src/super_harness/adapters/agent/codex.py`, `claude_code.py` — A.2 (subsection bullet).
- `src/super_harness/cli/status.py` — A.1b (`next:` from `SUGGESTIONS.get(state)`, human + `--json`).
- `docs/getting-started.md`, `docs/adapters/claude-code.md` — A.3 (human-only reframe).
- `src/super_harness/engineering/attestation.py` — C.3 helpers + verify blocker.
- `src/super_harness/cli/attest.py` — C.3 `--disclose-gate-bypass` flag + disclosure line.
- `AGENTS.md` — regenerated via `sync --agents-md`.
- Tests: `tests/unit/core/test_events.py` or `test_transitions.py`, `tests/unit/daemon/test_hook_entry.py`, `tests/unit/adapters/test_codex.py`/`test_claude_code.py`, `tests/integration/cli/` for status + attest, `tests/unit/engineering/test_attestation.py`.

Verification shorthand: `V=PATH="$(pwd)/.venv/bin:$PATH"`. Every commit message ends with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Strict `git add <files>`, never `-A`.

---

## Task 1: Register the two new event types (foundation for C)

**Files:**
- Modify: `src/super_harness/core/events.py` (`KNOWN_EVENT_TYPES`)
- Modify: `src/super_harness/core/transitions.py` (`_INFORMATIONAL`)
- Test: `tests/unit/core/test_transitions.py` (or the events test module)

- [ ] **Step 1: Write the failing test** — append to `tests/unit/core/test_transitions.py`:

```python
def test_gate_bypass_events_are_informational_and_known():
    from super_harness.core.transitions import _INFORMATIONAL, compute_target_state
    from super_harness.core.events import KNOWN_EVENT_TYPES
    for t in ("gate_bypassed", "gate_bypass_disclosed"):
        assert t in KNOWN_EVENT_TYPES
        assert t in _INFORMATIONAL
        # informational over an existing state preserves it (not INVALID)
        assert compute_target_state("INTENT_DECLARED", t) == "INTENT_DECLARED"
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/core/test_transitions.py::test_gate_bypass_events_are_informational_and_known -v`
Expected: FAIL (`gate_bypassed` not in `KNOWN_EVENT_TYPES`).

- [ ] **Step 3: Register the types.** In `core/events.py`, add `"gate_bypassed"` and `"gate_bypass_disclosed"` to the `KNOWN_EVENT_TYPES` collection (find the EXTENSION event list — grep `KNOWN_EVENT_TYPES` and add both names alongside the other extension types like `verification_passed`). In `core/transitions.py`, add both names to the `_INFORMATIONAL` frozenset (lines ~19-24):

```python
_INFORMATIONAL: frozenset[str] = frozenset({
    # ... existing informational types ...
    "gate_bypassed",
    "gate_bypass_disclosed",
})
```

(Preserve the existing members; just add the two. If `_INFORMATIONAL` lists members inline, append the two strings.)

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/core/test_transitions.py -v && $V python -m pytest tests/unit/core/test_events.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/core/events.py src/super_harness/core/transitions.py tests/unit/core/test_transitions.py
git commit -m "feat: register gate_bypassed/gate_bypass_disclosed as informational events" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Record a bypass event on every kill-switch ALLOW (C.1)

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py`
- Test: `tests/unit/daemon/test_hook_entry.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/daemon/test_hook_entry.py` (uses `tmp_path`; build a minimal `.harness` with `gate-disabled` + a state.yaml so `_read_active_change_id` resolves a change):

```python
def test_kill_switch_records_gate_bypassed_event(tmp_path, monkeypatch):
    import json
    from super_harness.core.paths import HARNESS_DIRNAME
    h = tmp_path / HARNESS_DIRNAME
    h.mkdir()
    (h / "gate-disabled").touch()
    (h / "state.yaml").write_text("schema_version: 1\nchanges:\n  c1:\n    state: INTENT_DECLARED\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "c1")
    from super_harness.daemon import hook_entry
    decision, _ = hook_entry._decide("apply_patch", None)
    assert decision == "allow"
    events = (h / "events.jsonl").read_text().strip().splitlines()
    rec = [json.loads(l) for l in events if json.loads(l)["type"] == "gate_bypassed"]
    assert len(rec) == 1
    assert rec[0]["change_id"] == "c1"
    assert rec[0]["payload"]["tool"] == "apply_patch"


def test_kill_switch_with_no_active_change_records_nothing(tmp_path, monkeypatch):
    from super_harness.core.paths import HARNESS_DIRNAME
    h = tmp_path / HARNESS_DIRNAME
    h.mkdir()
    (h / "gate-disabled").touch()  # no state.yaml → no active change
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPER_HARNESS_CHANGE_ID", raising=False)
    from super_harness.daemon import hook_entry
    decision, _ = hook_entry._decide("apply_patch", None)
    assert decision == "allow"
    assert not (h / "events.jsonl").exists() or "gate_bypassed" not in (h / "events.jsonl").read_text()


def test_record_bypass_never_raises(tmp_path, monkeypatch):
    from super_harness.daemon import hook_entry
    # emit fails (bad root) → swallowed, no exception escapes
    hook_entry._record_bypass(tmp_path / "nonexistent", tool="apply_patch", file=None)
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/daemon/test_hook_entry.py -k "kill_switch or record_bypass" -v`
Expected: FAIL (`_record_bypass` undefined; the kill-switch path records nothing today).

- [ ] **Step 3: Implement `_record_bypass` + call it at the short-circuit.** In `hook_entry.py`, at the kill-switch check in `_decide` (currently `if (root / ".harness" / "gate-disabled").exists(): return "allow", "gate disabled ..."`), insert the record call before the return:

```python
    if (root / ".harness" / "gate-disabled").exists():
        _record_bypass(root, tool=tool, file=file)
        return "allow", "gate disabled (.harness/gate-disabled present)"
```

Add the helper (top-level in the module):

```python
def _record_bypass(root: Path, *, tool: str, file: str | None) -> None:
    """Best-effort record a `gate_bypassed` audit event. NEVER raises — recording
    must not break the safety path. Skips when no active change (a bypass with no
    change has no merge gate to disclose at; design §4)."""
    try:
        import os

        from super_harness.core.clock import utc_now_iso
        from super_harness.core.events import Actor, Event
        from super_harness.core.paths import events_path
        from super_harness.core.ulid import new_event_id
        from super_harness.core.writer import EventWriter

        change_id = os.environ.get("SUPER_HARNESS_CHANGE_ID") or _read_active_change_id(root)
        if not change_id:
            return
        ev = Event(
            event_id=new_event_id(),
            type="gate_bypassed",
            change_id=change_id,
            timestamp=utc_now_iso(),
            actor=Actor(type="sensor", identifier="gate"),
            framework="plain",
            payload={"tool": tool, "file": file or ""},
        )
        EventWriter(events_path(root)).emit(ev, skip_validation=True)
    except Exception:
        pass
```

(Verify the exact import paths with `grep -rn "def new_event_id\|def utc_now_iso\|def events_path" src/super_harness/core/`. `_read_active_change_id` already exists in this module.)

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/daemon/test_hook_entry.py -v`
Expected: PASS (new + all pre-existing hook tests).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/unit/daemon/test_hook_entry.py
git commit -m "feat: record gate_bypassed audit event on kill-switch ALLOW" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Remove escape-hatch from the 3 hook block messages (A.1)

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py`
- Test: `tests/unit/daemon/test_hook_entry.py`

- [ ] **Step 1: Write the failing test** — append:

```python
def test_block_messages_do_not_teach_escape_hatch(capsys, monkeypatch):
    """All three shims' BLOCK output must halt-and-surface, never name gate-disabled."""
    import io, json, pytest
    from super_harness.daemon import hook_entry
    monkeypatch.setattr(hook_entry, "_decide", lambda tool, file: ("block", "INTENT_DECLARED: plan not drafted yet"))
    # positional
    with pytest.raises(SystemExit):
        hook_entry._run_positional(["Edit", "a.py"])
    err = capsys.readouterr().err
    assert "gate-disabled" not in err
    assert "do not bypass" in err.lower()
    # codex (deny JSON reason)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "apply_patch", "tool_input": {"command": "x"}})))
    with pytest.raises(SystemExit):
        hook_entry._run_codex_shim()
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "gate-disabled" not in reason
    assert "do not bypass" in reason.lower()
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/daemon/test_hook_entry.py::test_block_messages_do_not_teach_escape_hatch -v`
Expected: FAIL (current messages contain `gate-disabled`).

- [ ] **Step 3: Replace the three block messages with a shared halt constant.** In `hook_entry.py`, add near the top:

```python
_HALT_HINT = (
    "Stop and tell the human — run `super-harness status` for the next valid step. "
    "Do NOT bypass the gate yourself."
)
```

Then:
- Positional shim (`_run_positional`): replace the stderr write
  `f"super-harness: BLOCK ({reason})\n  escape hatch: touch .harness/gate-disabled to disable the gate\n"`
  with `f"super-harness: BLOCK ({reason}). {_HALT_HINT}\n"`.
- Claude shim (`_run_claude_code_shim`): same replacement for its stderr write.
- Codex shim (`_run_codex_shim`): replace the `permissionDecisionReason` f-string
  `f"super-harness: {reason} — escape hatch: touch .harness/gate-disabled ..."`
  with `f"super-harness: BLOCK ({reason}). {_HALT_HINT}"`.

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/daemon/test_hook_entry.py -v`
Expected: PASS (incl. the existing exit-code tests — only message text changed).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/unit/daemon/test_hook_entry.py
git commit -m "fix: block messages halt-and-surface, no longer teach the kill switch" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `super-harness status` surfaces the next step (A.1b)

**Files:**
- Modify: `src/super_harness/cli/status.py`
- Test: `tests/integration/cli/test_status.py` (match the module's existing CliRunner pattern; create the test if the module lacks one — find it with `ls tests/**/test_status*.py`)

- [ ] **Step 1: Write the failing test** — assert a blocking-state change shows the next step in both human + json output:

```python
def test_status_shows_next_step_for_blocking_state(tmp_path):
    import json
    from click.testing import CliRunner
    from super_harness.cli import main
    # init a workspace + start a change (INTENT_DECLARED is blocking)
    (tmp_path / ".harness").mkdir()
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "change", "start", "c1"])
    human = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "c1"])
    assert "next:" in human.output.lower()
    assert "Draft a plan" in human.output  # from SUGGESTIONS[INTENT_DECLARED]
    js = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "status", "c1"])
    data = json.loads(js.output)
    # find the c1 entry and assert it carries a `next` key
    assert any("Draft a plan" in str(e.get("next", "")) for e in (data if isinstance(data, list) else data.get("changes", [])))
```

(Adjust the JSON-shape assertion to the real `status --json` structure — inspect `cli/status.py:121-135` for the entry shape.)

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/integration/cli/test_status.py -k next_step -v`
Expected: FAIL (no `next:` surfaced today).

- [ ] **Step 3: Add the next-step lookup.** In `cli/status.py`, import `SUGGESTIONS`:

```python
from super_harness.gates.decisions import SUGGESTIONS
```

In the `--json` entry builder (around :121-135), add to each change entry:

```python
        "next": SUGGESTIONS.get(cs.current_state),
```

In the human render loop (around :137-147), after the state line, add (only when a suggestion exists):

```python
        nxt = SUGGESTIONS.get(cs.current_state)
        if nxt:
            click.echo(f"  next: {nxt}")
```

(Use the real variable names from `status.py` — `cs.current_state` per the change-state object.)

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/integration/cli/test_status.py -v`
Expected: PASS. (If an existing test asserts exact status output, update its expectation to include the `next:` line / `next` key.)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/status.py tests/integration/cli/test_status.py
git commit -m "feat: status surfaces the next valid lifecycle step (SUGGESTIONS)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Reframe AGENTS.md subsections + adapter doc to human-only (A.2 + A.3)

**Files:**
- Modify: `src/super_harness/adapters/agent/codex.py`, `claude_code.py`
- Modify: `docs/adapters/claude-code.md`, `docs/getting-started.md`
- Test: `tests/unit/adapters/test_codex.py`, `tests/unit/adapters/test_claude_code.py`

- [ ] **Step 1: Write the failing test** — append to BOTH `test_codex.py` and `test_claude_code.py`:

```python
def test_agents_md_subsection_does_not_teach_kill_switch():
    from super_harness.adapters.agent.codex import CodexAdapter  # or ClaudeCodeAdapter
    sub = CodexAdapter().agents_md_subsection()
    assert "gate-disabled" not in sub
    assert "surface" in sub.lower()  # tells the agent to surface to the human
    assert "human" in sub.lower()
```

(Use `ClaudeCodeAdapter` in the claude test.)

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py -k kill_switch -v`
Expected: FAIL (subsections currently contain `gate-disabled`).

- [ ] **Step 3: Reframe the bullets.** In `codex.py` replace the escape-hatch bullet (line ~55, `- Escape hatch (if the gate is wrong): \`touch .harness/gate-disabled\` ...`) and in `claude_code.py` the analogous bullet (line ~69) with this shared wording:

```
- **If a tool call is blocked by the gate:** stop, and surface the block + the next
  valid step (`super-harness status`) to the human. Do **not** touch
  `.harness/gate-disabled` yourself — it is a **human-only** emergency override; an
  agent using it to get past a block defeats the gate, and any such bypass is recorded
  and disclosed at the merge gate. Whether to override is the human's call.
```

In `docs/adapters/claude-code.md`: drop the escape-hatch how-to from the gate-block section (~:87-88); in common-issues (~:118-119) reframe to: "A **human** may, as an emergency override, `touch .harness/gate-disabled` (`rm` to re-enable) — agents must not; any bypass is disclosed at the merge gate. See getting-started troubleshooting."

In `docs/getting-started.md` (~:434-435, KEEP the human how-to): append one sentence: "Note: if you disable the gate while a change is in flight, the bypass is recorded and surfaced at the merge gate (`attest verify`)."

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/agent/codex.py src/super_harness/adapters/agent/claude_code.py docs/adapters/claude-code.md docs/getting-started.md tests/unit/adapters/test_codex.py tests/unit/adapters/test_claude_code.py
git commit -m "fix: AGENTS.md + adapter docs reframe kill switch as human-only" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Merge-gate disclosure helper + verify blocker (C.3 verify side)

**Files:**
- Modify: `src/super_harness/engineering/attestation.py`
- Test: `tests/unit/engineering/test_attestation.py`

- [ ] **Step 1: Write the failing test** — append:

```python
def test_gate_bypass_disclosure_counts_append_order():
    from super_harness.engineering.attestation import gate_bypass_disclosure
    from super_harness.core.events import Event, Actor

    def ev(t):
        return Event(event_id="x", type=t, change_id="c1", timestamp="2026-01-01T00:00:00Z",
                     actor=Actor(type="sensor", identifier="gate"), framework="plain", payload={})

    # bypass then disclose → covered
    r = gate_bypass_disclosure([ev("gate_bypassed"), ev("gate_bypass_disclosed")])
    assert r["undisclosed"] == 0
    # bypass AFTER the last disclosure → undisclosed
    r = gate_bypass_disclosure([ev("gate_bypass_disclosed"), ev("gate_bypassed")])
    assert r["undisclosed"] == 1
    # no disclosure at all
    r = gate_bypass_disclosure([ev("gate_bypassed"), ev("gate_bypassed")])
    assert r["undisclosed"] == 2


def test_verify_blocks_undisclosed_bypass(tmp_path):
    # build an attestation jsonl with a gate_bypassed and no disclosure → blocker
    # (mirror the existing skip-override verify test in this module for setup)
    ...  # fill using the module's existing attestation-fixture helper
```

(For the second test, copy the setup pattern from the module's existing skip-override / `verify_attestations` test — build a committed attestation file + diff entries, then assert a blocker string containing "bypassed" and "without disclosure".)

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/engineering/test_attestation.py -k "gate_bypass or undisclosed" -v`
Expected: FAIL (`gate_bypass_disclosure` undefined).

- [ ] **Step 3: Add the helpers + wire the blocker.** In `attestation.py`, add the pure helpers next to `derive_independence` / `independence_for_attestation`:

```python
def gate_bypass_disclosure(events: list[Event]) -> dict[str, Any]:
    """Count gate bypasses vs disclosures by APPEND ORDER (pure; no timestamps).

    Append position is causal truth. A `gate_bypassed` is undisclosed iff it appears
    after the last `gate_bypass_disclosed` in the event list.
    """
    last_disclosed = max(
        (i for i, e in enumerate(events) if e.type == "gate_bypass_disclosed"),
        default=-1,
    )
    undisclosed = sum(
        1 for i, e in enumerate(events) if e.type == "gate_bypassed" and i > last_disclosed
    )
    bypassed = sum(1 for e in events if e.type == "gate_bypassed")
    disclosed = sum(1 for e in events if e.type == "gate_bypass_disclosed")
    reasons = [e.payload.get("reason") for e in events if e.type == "gate_bypass_disclosed"]
    return {"bypassed": bypassed, "disclosed": disclosed, "undisclosed": undisclosed, "reasons": reasons}


def gate_bypass_for_attestation(att_path: Path) -> dict[str, Any]:
    """Tolerant-parse the committed attestation and derive bypass disclosure."""
    events: list[Event] = []
    for raw in att_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            events.append(parse_event_line(s))
        except EventSchemaError:
            continue
    return gate_bypass_disclosure(events)
```

Wire the blocker inside `verify_attestations`' `for slug in added_slugs` loop, right after the existing skip-override check (~:228-233, no `continue` — accumulate):

```python
        gb = gate_bypass_for_attestation(att_path)
        if gb["undisclosed"] > 0:
            blockers.append(
                f"attestation {slug}: the gate was bypassed {gb['undisclosed']} time(s) "
                f"during this change without disclosure (a deliberate "
                f'`attest write {slug} --disclose-gate-bypass "<reason>"` is required to merge)'
            )
```

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/engineering/test_attestation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/engineering/attestation.py tests/unit/engineering/test_attestation.py
git commit -m "feat: merge gate blocks undisclosed gate bypasses (append-order)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `attest write --disclose-gate-bypass` + disclosure line (C.3 write side)

**Files:**
- Modify: `src/super_harness/cli/attest.py`
- Test: `tests/integration/cli/test_attest.py` (match the module's CliRunner pattern)

- [ ] **Step 1: Write the failing test** — a workspace with a recorded `gate_bypassed`: `attest write --disclose-gate-bypass "<reason>"` clears the `attest verify` blocker; without it, verify blocks.

```python
def test_disclose_gate_bypass_clears_the_blocker(tmp_path):
    from click.testing import CliRunner
    from super_harness.cli import main
    # ... set up a change with a gate_bypassed event in events.jsonl (emit via EventWriter
    #     skip_validation, like _record_bypass), run the lifecycle to READY_TO_MERGE,
    #     `attest write c1` then `attest verify` → expect a "without disclosure" blocker;
    #     then `attest write c1 --disclose-gate-bypass "daemon was wedged"` + `attest verify`
    #     → expect pass + a disclosure line mentioning the reason. ...
```

(Build the setup from the module's existing `attest write`/`attest verify` integration test; the new assertions are the two verify outcomes.)

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/integration/cli/test_attest.py -k disclose_gate_bypass -v`
Expected: FAIL (`--disclose-gate-bypass` unknown option).

- [ ] **Step 3: Add the flag + disclosure line.** In `cli/attest.py`:
- Add `@click.option("--disclose-gate-bypass", "disclose_reason", default=None, help="Disclose+justify that the gate was bypassed during this change (clears the merge-gate blocker).")` to the `attest write` command.
- In the command body, BEFORE the `write_attestation(events_path(root), ...)` call (~:89), if `disclose_reason`:

```python
    if disclose_reason:
        from super_harness.core.clock import utc_now_iso
        from super_harness.core.events import Actor, Event
        from super_harness.core.ulid import new_event_id
        from super_harness.core.writer import EventWriter
        ev = Event(
            event_id=new_event_id(), type="gate_bypass_disclosed", change_id=slug,
            timestamp=utc_now_iso(), actor=Actor(type="human", identifier=resolve_identity(root, None)),
            framework="plain", payload={"reason": disclose_reason},
        )
        EventWriter(events_path(root)).emit(ev, skip_validation=True)  # non-transition; no state refresh
```

- In `attest verify` output, beside the independence disclosure comprehension (~:166-197), add a per-attestation bypass line: for each `slug in verdict.attestations`, `gb = gate_bypass_for_attestation(att_path)`, and if `gb["bypassed"]`, print
  `f"gate bypass: {gb['bypassed']} bypass(es), {gb['disclosed']} disclosure(s) — {'; '.join(r for r in gb['reasons'] if r)}"` (plain ASCII, same placement discipline as `_independence_line`).

(Confirm `resolve_identity` is imported in `cli/attest.py`; if not, import from where `cli/review.py` gets it.)

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/integration/cli/test_attest.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/attest.py tests/integration/cli/test_attest.py
git commit -m "feat: attest write --disclose-gate-bypass + verify disclosure line" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Regenerate AGENTS.md + full suite + OPEN-ITEMS

**Files:**
- Modify: `AGENTS.md` (regenerated)
- Modify: `private/OPEN-ITEMS.md` (if residue)

- [ ] **Step 1: Regenerate + drift checks.**

Run:
```bash
$V super-harness sync --agents-md
$V super-harness sync --check
$V super-harness doc check
```
Expected: `sync --check` + `doc check` exit 0. Confirm AGENTS.md no longer contains `gate-disabled`: `grep -c gate-disabled AGENTS.md` → 0.

- [ ] **Step 2: Full suite green**

Run: `$V ruff check src tests && $V mypy src && $V python -m pytest -q`
Expected: all green.

- [ ] **Step 3: Record any residue** in `private/OPEN-ITEMS.md` (e.g. the `codex exec` no-hook gap is already recorded; note here only NEW residue, e.g. "bypass outside an active change is not disclosed — by design, §4").

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md private/OPEN-ITEMS.md
git commit -m "docs: regen AGENTS.md (no kill-switch in agent channel) + open items" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-host merge sequence (after all tasks green)

Scope MUST cover every vs-main changed file — enumerate from `git diff --name-only main` at plan-ready time (the design + this plan doc included). Sequence per `project-self-host-pr-attest-scope` (same as #50):

```
change start gate-escape-hatch-hardening
plan ready gate-escape-hatch-hardening --tier-hint Normal --scope '[<git diff --name-only main>]'
review approve gate-escape-hatch-hardening --reviewer plan-reviewer
implementation start gate-escape-hatch-hardening
# ... Tasks 1-8, full suite green ...
done gate-escape-hatch-hardening                 # pass slug explicitly
review prepare gate-escape-hatch-hardening --reviewer code-reviewer
# independent reviewer subagent → verdict file (checklist incl. doc-impact)
review approve gate-escape-hatch-hardening --reviewer code-reviewer --verdict-file <path>
attest write gate-escape-hatch-hardening && git add .harness/attestations && git commit
attest verify --base main --head HEAD
git push -u origin <branch> && gh pr create   # title/body right first time (token lacks read:org)
# CI green → squash → on-merge --commit <sha> --change gate-escape-hatch-hardening
```

**Dogfood note:** this change must NOT need to bypass its own gate during implementation (A removes the temptation; work proceeds through PLAN_APPROVED→IMPLEMENTATION_IN_PROGRESS). If a bypass ever happens, disclose it (`attest write --disclose-gate-bypass`) — dogfooding the new teeth.

---

## Self-Review (completed)

- **Spec coverage:** A.1→Task 3; A.1b→Task 4; A.2/A.3→Task 5; C.1→Task 2 (+ event types Task 1); C.3 verify→Task 6; C.3 write→Task 7; regen/OPEN-ITEMS→Task 8. All spec sections mapped.
- **Ordering:** Task 1 (event types) before Task 2 (emit) and Task 6/7 (verify needs the types parse-clean). Task 4 (status next:) lands before/with Task 3's redirect — both early, no dead end at merge. Noted.
- **Type/name consistency:** `gate_bypassed`/`gate_bypass_disclosed`, `_record_bypass`, `gate_bypass_disclosure`/`gate_bypass_for_attestation`, `--disclose-gate-bypass`/`disclose_reason`, `_HALT_HINT` used identically across tasks.
- **No placeholders:** every code step shows real code. The two integration tests (Task 6 step1 second test, Task 7) reference "the module's existing fixture" rather than re-deriving full attestation setup — intentional (reuse the established helper); the new ASSERTIONS are spelled out.
