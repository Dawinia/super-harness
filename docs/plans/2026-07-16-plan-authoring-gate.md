---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: plan-authoring-gate-v2
stage: plan
description: Implement the PLAN_REJECTED plan-artifact gate carve-out (recorded via manual plan ready) so a rejected plan is revised through normal edit tools, not shell bypass.
scope:
  files:
    - docs/plans/2026-07-16-plan-authoring-gate-design.md
    - docs/plans/2026-07-16-plan-authoring-gate.md
    - src/super_harness/core/state.py
    - src/super_harness/core/reducer.py
    - src/super_harness/core/state_snapshot.py
    - src/super_harness/core/paths.py
    - src/super_harness/gates/__init__.py
    - src/super_harness/gates/decisions.py
    - src/super_harness/gates/pre_tool_use.py
    - src/super_harness/daemon/hook_entry.py
    - src/super_harness/cli/gate.py
    - src/super_harness/cli/plan.py
    - src/super_harness/adapters/agent/claude_code.py
    - docs/decisions/d-single-gate-policy.md
    - docs/decisions/d-state-pure-fold.md
    - private/specs/2026-05-26-lifecycle-event-model.md
    - docs/concepts.md
    - docs/getting-started.md
    - docs/limitations.md
    - AGENTS.md
    - tests/unit/core/test_reducer_plan_artifacts.py
    - tests/unit/core/test_paths_canonical_relpath.py
    - tests/unit/core/test_state_snapshot.py
    - tests/unit/gates/test_pre_tool_use.py
    - tests/unit/cli/test_plan_records_artifacts.py
    - tests/unit/cli/test_gate_check.py
    - tests/integration/daemon/test_hook_entry.py
    - tests/e2e/test_plan_authoring_reject_loop.py
---

# Plan-authoring gate carve-out — Implementation Plan (v2)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** In `PLAN_REJECTED`, allow editing a file the change registered as its plan
document (marked `.md`), so a rejected plan is revised through the normal edit tools
— no more shell bypass.

**Architecture:** A change records its plan-artifact path(s) into
`ChangeState.plan_artifacts` at manual `plan ready` (derived from marked `.md` in the
declared `--scope`, checked `.md` both before and after canonicalization, case-
insensitive). The single policy module (`gates.decisions`) gains
`PLAN_ARTIFACT_ALLOW_STATES`; `PreToolUseGate` reads it and — after the `state is
None` guard — ALLOWs when `state.plan_artifacts` is a list, the incoming file
canonicalized to a repo-relative path ends in `.md`, and it is in the recorded
artifacts — else the table's BLOCK stands. The exception grants a bounded, validated
authorization; any uncertainty (incl. a forged non-list persisted value) → BLOCK.
**Scope:** the recorder is the **manual `plan ready`** verb (framework-agnostic;
marker is the boundary); framework-adapter auto-recording is deferred.

**Tech Stack:** Python 3.10+, dataclasses, click, pytest, ruff, mypy. Self-host
lifecycle, `decision ratify`/`reconcile` for the tier-2 anchor.

---

## Soundness invariants (do not violate)

1. **No source ever becomes editable pre-approval.** Only marked `.md` recordable,
   checked `.md` (case-insensitive) after symlink resolution; the gate additionally
   requires `state.plan_artifacts` to be a list and `resolved_path` to end in `.md`.
   The recorded list is the only thing consulted. (The exception narrows the *blocked*
   set — i.e. grants a bounded authorization — for validated marked `.md` only.)
2. **The gate reads policy, never invents it.** WHICH states get the exception lives
   in `gates.decisions`; `pre_tool_use.py` executes it. `d-single-gate-policy` is
   re-ratified to describe the two-constant policy honestly.
3. **Fail-safe on uncertainty.** Un-normalizable/root-escaping path, non-`.md`, empty
   or malformed `plan_artifacts`, or a **forged non-list persisted value** → the
   exception does not fire; BLOCK stands, and nothing raises.
4. **Outer fail-open untouched.** "No harness / unknown call shape" still ALLOWs.
   The persisted-state guards ensure the carve-out never *manufactures* a new
   fail-open (a `TypeError` in `decide` would be caught by the hook and ALLOW).

---

## Task 1: `ChangeState.plan_artifacts` + reducer (always-reset, shape-validated)

**Files:**
- Modify: `src/super_harness/core/state.py` (`ChangeState`)
- Modify: `src/super_harness/core/reducer.py` (`plan_ready` + `plan_redeclared` branches)
- Test: `tests/unit/core/test_reducer_plan_artifacts.py` (create)

**Step 1: Failing tests**

```python
# tests/unit/core/test_reducer_plan_artifacts.py
from super_harness.core.reducer import derive_state
from super_harness.core.writer import EventWriter
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id


def _emit(root, **kw):
    ev = Event(event_id=new_event_id(), actor=Actor(type="human", identifier="t"),
               framework="plain", timestamp="2026-07-16T00:00:00Z", **kw)
    EventWriter(events_path(root)).emit(ev, skip_validation=True)


def _seed(root, slug="c"):
    (root / ".harness").mkdir(exist_ok=True)
    _emit(root, type="intent_declared", change_id=slug, payload={"description": "d"})


def test_plan_ready_records(tmp_path):
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": ["docs/plans/c.md"]})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == ["docs/plans/c.md"]


def test_default_empty(tmp_path):
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c", payload={"scope": {"files": []}})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []


def test_empty_resubmit_revokes(tmp_path):
    # first plan_ready records; a later plan_ready with no artifacts REPLACES (clears)
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c", payload={"plan_artifacts": ["docs/plans/c.md"]})
    _emit(tmp_path, type="plan_rejected", change_id="c", payload={})
    _emit(tmp_path, type="plan_ready", change_id="c", payload={"scope": {"files": []}})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []


def test_malformed_payload_becomes_empty(tmp_path):
    # a mapping (or any non-list-of-str) must NOT smuggle a path in
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": {"src/evil.py": True}})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []


def test_non_str_items_filtered(tmp_path):
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c",
          payload={"plan_artifacts": ["docs/plans/c.md", 123, None]})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == ["docs/plans/c.md"]


def test_redeclare_clears(tmp_path):
    _seed(tmp_path)
    _emit(tmp_path, type="plan_ready", change_id="c", payload={"plan_artifacts": ["docs/plans/c.md"]})
    _emit(tmp_path, type="plan_redeclared", change_id="c", payload={"reason": "x"})
    assert derive_state(events_path(tmp_path))["c"].plan_artifacts == []
```

**Step 2: Run — expect FAIL.**

**Step 3: Implement**

`state.py` — add field (+ docstring bullet):

```python
    plan_artifacts: list[str] = field(default_factory=list)
```

`reducer.py` — a small shape-validated helper + always-reset on `plan_ready`, clear
on `plan_redeclared`:

```python
def _valid_artifacts(value: object) -> list[str]:
    """plan_artifacts is trusted only as a list of str; anything else → []."""
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []
```

In the `elif ev.type == "plan_ready":` branch, **always** set (replace, never merge):

```python
            cs.plan_artifacts = _valid_artifacts(p.get("plan_artifacts"))
```

In the `elif ev.type in ("intent_redeclared", "plan_redeclared"):` branch, after the
history append:

```python
            if ev.type == "plan_redeclared":
                cs.plan_artifacts = []
```

**Step 4: Run — expect PASS.** Also run the full reducer suite for no regression.

**Step 5 (snapshot defense — a forged `state.yaml` must not crash the gate):** in
`core/state_snapshot.py`, where `ChangeState(**record)` is built from the persisted
map, coerce a non-list `plan_artifacts` to `[]` (mirror `_valid_artifacts`, or filter
inline) so `load_state_snapshot` upholds its "NEVER raises" contract even for a
hand-forged `plan_artifacts: null`/`42`. Add `tests/unit/core/test_state_snapshot.py`:
a state.yaml with `plan_artifacts: null` loads a `ChangeState` whose `plan_artifacts`
is `[]` (never raises).

**Step 6: Run — expect PASS.**

**Step 7: Commit** — `feat(state): record + reset plan_artifacts (shape-validated, forge-safe load)`

---

## Task 2: `canonical_relpath` shared resolver

**Files:**
- Modify: `src/super_harness/core/paths.py`
- Test: `tests/unit/core/test_paths_canonical_relpath.py` (create)

**Step 1: Failing tests** (note the corrected traversal cases — a `../../` that stays
inside root is NOT None; only a path that truly escapes, or an absolute outside, is):

```python
from pathlib import Path
from super_harness.core.paths import canonical_relpath


def test_absolute_under_root(tmp_path):
    (tmp_path / "docs/plans").mkdir(parents=True)
    f = tmp_path / "docs/plans/c.md"; f.write_text("x")
    assert canonical_relpath(tmp_path, str(f)) == "docs/plans/c.md"


def test_relative_rooted(tmp_path):
    assert canonical_relpath(tmp_path, "docs/plans/c.md") == "docs/plans/c.md"


def test_inside_root_traversal_is_kept(tmp_path):
    # resolves to <root>/etc/passwd — still INSIDE root, so it is returned (relpath),
    # NOT None. (It simply won't match any recorded .md artifact → BLOCK downstream.)
    assert canonical_relpath(tmp_path, "docs/plans/../../etc/passwd") == "etc/passwd"


def test_true_escape_is_none(tmp_path):
    deep = "../" * 40 + "etc/passwd"
    assert canonical_relpath(tmp_path, deep) is None


def test_absolute_outside_is_none(tmp_path):
    assert canonical_relpath(tmp_path, "/etc/passwd") is None


def test_none_input(tmp_path):
    assert canonical_relpath(tmp_path, None) is None


def test_symlink_resolved(tmp_path):
    (tmp_path / "src").mkdir(); (tmp_path / "docs/plans").mkdir(parents=True)
    (tmp_path / "src/x.py").write_text("x")
    link = tmp_path / "docs/plans/c.md"; link.symlink_to(tmp_path / "src/x.py")
    # canonical form follows the symlink to its target
    assert canonical_relpath(tmp_path, str(link)) == "src/x.py"
```

**Step 2: Run — expect FAIL (ImportError).**

**Step 3: Implement** (in `paths.py`):

```python
def canonical_relpath(root: Path, file: str | None) -> str | None:
    """Resolve `file` to a POSIX repo-relative path under `root`, or None.

    `file` may be absolute or relative-to-root. Symlinks and `..` are resolved.
    Anything that does not resolve to a path *inside* `root` (true traversal escape,
    absolute-outside, unresolvable) returns None. Never raises. (A path that stays
    inside root is returned even if odd — soundness comes from the caller's `.md` +
    recorded-artifact checks, not from this function guessing intent.)
    """
    if not file:
        return None
    try:
        base = root.resolve()
        p = Path(file)
        target = (p if p.is_absolute() else base / p).resolve()
        return target.relative_to(base).as_posix()
    except (ValueError, OSError, RuntimeError):
        return None
```

**Step 4: Run — expect PASS.**

**Step 5: Commit** — `feat(paths): canonical_relpath repo-relative resolver`

---

## Task 3: policy set + `ProposedAction.resolved_path` + gate carve-out (with `.md` guard)

**Files:**
- Modify: `src/super_harness/gates/decisions.py` (`PLAN_ARTIFACT_ALLOW_STATES` + `__all__` + docstring)
- Modify: `src/super_harness/gates/__init__.py` (`ProposedAction.resolved_path`)
- Modify: `src/super_harness/gates/pre_tool_use.py` (carve-out reading the set)
- Test: `tests/unit/gates/test_pre_tool_use.py` (extend)

**Step 1: Failing tests**

```python
from super_harness.gates import ProposedAction, GateDecision
from super_harness.gates.pre_tool_use import PreToolUseGate
from super_harness.core.state import ChangeState


def _st(name, **kw): return ChangeState(change_id="c", current_state=name, **kw)
def _act(f, rp): return ProposedAction(kind="edit", file=f, resolved_path=rp)


def test_allows_recorded_artifact():
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision is GateDecision.ALLOW


def test_blocks_source_in_scope():
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert PreToolUseGate().decide(_act("src/evil.py", "src/evil.py"), st, []).decision is GateDecision.BLOCK


def test_blocks_unrecorded_md():
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert PreToolUseGate().decide(_act("docs/other.md", "docs/other.md"), st, []).decision is GateDecision.BLOCK


def test_blocks_recorded_non_md_defense_in_depth():
    # even if a non-.md path somehow reached plan_artifacts, the gate .md guard blocks it
    st = _st("PLAN_REJECTED", plan_artifacts=["src/evil.py"])
    assert PreToolUseGate().decide(_act("src/evil.py", "src/evil.py"), st, []).decision is GateDecision.BLOCK


def test_blocks_when_plan_artifacts_forged_non_list():
    # forged/corrupt state.yaml: plan_artifacts is a string (or None) → must BLOCK, not raise
    st = _st("PLAN_REJECTED")
    st.plan_artifacts = "docs/plans/c.md"  # type: ignore[assignment]
    assert PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision is GateDecision.BLOCK


def test_allows_uppercase_md_extension():
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/C.MD"])
    assert PreToolUseGate().decide(_act("docs/plans/C.MD", "docs/plans/C.MD"), st, []).decision is GateDecision.ALLOW


def test_blocks_when_resolved_none():
    st = _st("PLAN_REJECTED", plan_artifacts=["docs/plans/c.md"])
    assert PreToolUseGate().decide(_act("/etc/passwd", None), st, []).decision is GateDecision.BLOCK


def test_blocks_when_no_artifacts():
    st = _st("PLAN_REJECTED", plan_artifacts=[])
    assert PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision is GateDecision.BLOCK


def test_awaiting_never_allows():
    st = _st("AWAITING_PLAN_REVIEW", plan_artifacts=["docs/plans/c.md"])
    assert PreToolUseGate().decide(_act("docs/plans/c.md", "docs/plans/c.md"), st, []).decision is GateDecision.BLOCK
```

**Step 2: Run — expect FAIL.**

**Step 3: Implement**

`gates/__init__.py` — add to `ProposedAction`:

```python
    resolved_path: str | None = None
```
(with a docstring line: canonical repo-relative form of `file` when computable; the
pre-tool-use plan-artifact carve-out matches against this; other gates ignore it.)

`gates/decisions.py` — add (+ `__all__`):

```python
# States whose default is `block` but where an edit to one of the change's recorded
# plan artifacts (ChangeState.plan_artifacts, marked `.md` only) is ALLOWED — the
# authorized in-gate plan-authoring path (HG-PLAN-AUTHORING). This lives HERE, in the
# single policy module, so d-single-gate-policy holds: the gate reads this set, it
# does not fork its own path policy. It only NARROWS `block` to a specific allow for a
# marked `.md` the (reviewed) plan submission recorded — never widens to source.
# @decision:d-single-gate-policy
PLAN_ARTIFACT_ALLOW_STATES: frozenset[str] = frozenset({"PLAN_REJECTED"})
```

`gates/pre_tool_use.py` — import the set at top; **AFTER the existing `if state is
None: return ALLOW` guard** (the carve-out dereferences `state.*`), before the table
lookup in `decide`:

```python
        rp = action.resolved_path
        if (
            state.current_state in PLAN_ARTIFACT_ALLOW_STATES
            and rp
            and rp.lower().endswith(".md")
            and isinstance(state.plan_artifacts, list)
            and rp in state.plan_artifacts
        ):
            return GateResult(
                decision=GateDecision.ALLOW,
                reason=(
                    f"{state.current_state}: plan-artifact revision authorized ({rp})"
                ),
            )
```

Note: `isinstance(..., list)` guards a forged/corrupt `state.yaml` where
`plan_artifacts` is a string/None — `rp in "str"` would be substring matching or
`in None` a `TypeError`; the guard forces a clean BLOCK instead. The `.lower()` makes
the `.md` suffix case-insensitive (macOS `PLAN.MD`).

**Step 4: Run — expect PASS + full `test_pre_tool_use.py` green (no regression).**

**Step 5: Commit** — `feat(gate): PLAN_REJECTED plan-artifact carve-out (marked .md, reads policy set)`

---

## Task 4: wire the two call sites + `test_gate_check`

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py` (`_decide`)
- Modify: `src/super_harness/cli/gate.py` (`gate_check`)
- Test: `tests/integration/daemon/test_hook_entry.py` (extend), `tests/unit/cli/test_gate_check.py` (extend)

**Step 1: Failing tests** — seed a `PLAN_REJECTED` change with
`plan_ready` payload `{"plan_artifacts": ["docs/plans/c.md"]}` and the file present;
assert `_decide("Write", "<abs>/docs/plans/c.md")[0] == "allow"` and
`_decide("Write", "<abs>/src/x.py")[0] == "block"`. Mirror for `gate check pre-tool-use --file`.

**Step 2: Run — expect FAIL** (both block; `resolved_path` never set).

**Step 3: Implement** — both sites add `resolved_path=canonical_relpath(root, file)` to
the `ProposedAction(...)`; import `canonical_relpath` from `core.paths`. Keep `file`
raw (telemetry/message unchanged).

**Step 4: Run — expect PASS.**

**Step 5: Commit** — `feat(hook,cli): set resolved_path so the carve-out fires`

---

## Task 5: manual `plan ready` records marked `.md` from scope (post-resolution `.md` guard)

**Files:**
- Modify: `src/super_harness/cli/plan.py` (`ready`)
- Test: `tests/unit/cli/test_plan_records_artifacts.py` (create)

**Detection (ungameable):** among declared `--scope` files, a plan artifact is one
that (a) ends in `.md`, (b) canonicalizes to a path that *also* ends in `.md`
(defeats symlink→`.py` laundering), (c) whose frontmatter `change:` equals the slug.
Record the canonical relpath. Scope omitted / none match → no artifacts.

**Step 1: Failing tests**

```python
# assert plan_ready payload plan_artifacts == ["docs/plans/c.md"], where:
#  - docs/plans/c.md has `change: c` frontmatter          -> recorded
#  - src/x.py (even with a ---\nchange: c\n--- header)     -> excluded (not .md)
#  - docs/plans/evil.md -> symlink to src/evil.py          -> excluded
#       (endswith .md pre-check passes; canonical is src/evil.py; post-.md check fails)
#  - docs/unmarked.md (no/other change:)                   -> excluded
```

**Step 2: Run — expect FAIL.**

**Step 3: Implement** — helper in `cli/plan.py`:

```python
def _detect_plan_artifacts(root: Path, slug: str, scope_files: list[str]) -> list[str]:
    """Plan artifacts = declared-scope files that are `.md` BEFORE and AFTER
    canonicalization (symlink→non-.md is rejected) and whose frontmatter `change:`
    equals `slug`. `.py`/source can never match. Unreadable skipped; never raises."""
    from super_harness.core.frontmatter import split_frontmatter
    out: list[str] = []
    for f in scope_files:
        if not f.lower().endswith(".md"):
            continue
        rel = canonical_relpath(root, f)
        if rel is None or not rel.lower().endswith(".md"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = split_frontmatter(text)
        if fm and fm[0].get("change") == slug:
            out.append(rel)
    return out
```

Wire into the `--scope` branch:

```python
        files = _resolve_scope_files(scope_raw)
        payload["scope"] = {"files": files}
        artifacts = _detect_plan_artifacts(root, slug, files)
        if artifacts:
            payload["plan_artifacts"] = artifacts
```

**Step 4: Run — expect PASS.**

**Step 5: Commit** — `feat(cli): plan ready records marked-.md plan artifacts (symlink-safe)`

---

## Task 6: spec update (`private/specs/2026-05-26-lifecycle-event-model.md`)

**Files:**
- Modify: `private/specs/2026-05-26-lifecycle-event-model.md` (§3.2, §3.7)

**Steps:**
1. §3.2 `plan_ready` payload: add optional `plan_artifacts: [repo-relative marked-.md
   paths]`, recorded into `ChangeState.plan_artifacts`, replace-on-each-plan_ready.
2. §3.7 gate matrix: note the `PLAN_REJECTED` exception — an edit to a recorded plan
   artifact (marked `.md`) is ALLOWed; all else blocks. Cross-reference
   `PLAN_ARTIFACT_ALLOW_STATES` and `d-single-gate-policy`.
3. **Commit** — `docs(spec): plan_artifacts payload + PLAN_REJECTED gate carve-out`

---

## Task 7: honestly re-ratify `d-single-gate-policy` + docstrings (LAST decisions.py touch)

Changing `gates/decisions.py` re-fingerprints the anchor. Do this **after all
`decisions.py` edits are final** (pothole ⑧).

**Files:**
- Modify: `src/super_harness/gates/decisions.py` (module docstring)
- Modify: `src/super_harness/gates/pre_tool_use.py` (class docstring)
- Modify: `docs/decisions/d-single-gate-policy.md` (prose + re-stamp)

**Steps:**
1. Update the decision **prose** to describe policy honestly: *"Gate policy lives in
   `gates.decisions`: `PRE_TOOL_USE_DECISIONS` (per-state default matrix) plus
   `PLAN_ARTIFACT_ALLOW_STATES` (the PLAN_REJECTED plan-artifact narrowing). The
   in-process gate reads both; it neither invents nor forks policy, and no reader
   forks it."* Keep the teeth ("no reader forks; gate never invents") unchanged.
2. Finalize `decisions.py` + `pre_tool_use.py` docstrings to match.
3. `super-harness decision check` → see what it demands for the body change (`ratify`
   vs `reconcile`; it is a `review`-type tier-2 anchor). Run the demanded command:
   likely `super-harness decision ratify d-single-gate-policy` (re-stamps
   `ratified_text_hash` for the new prose) **and/or** `decision reconcile
   d-single-gate-policy --kind self --justification "Policy now = PRE_TOOL_USE_DECISIONS
   + PLAN_ARTIFACT_ALLOW_STATES, both in gates.decisions; gate reads, does not fork;
   invariant holds."` Do NOT self-reconcile as "unchanged" — the prose DID change.
4. `super-harness decision check` → clean.
5. **Commit** — `docs(decision): honestly re-ratify d-single-gate-policy for the carve-out`

---

## Task 8: user-facing docs + AGENTS.md (regenerated)

**Files:**
- Modify: `docs/concepts.md`, `docs/getting-started.md`, `docs/limitations.md`
- Modify: `src/super_harness/adapters/agent/claude_code.py` (`_AGENTS_MD_SUBSECTION`)
- Modify: `AGENTS.md` (regenerated, NOT hand-edited)

**Steps:**
1. `concepts.md` / `getting-started.md`: the authorized reject-loop revise path; to
   be recorded, the plan doc must carry the `change:<slug>` marker + be in the
   declared `--scope` of `plan ready`.
2. `limitations.md`: recording is via **manual `plan ready`** only (framework-adapter
   auto-recording deferred); Codex `file=None`; hardlink residual; INTENT_DECLARED
   not relaxed. (Do NOT write "plain-mode only" — that reads as a framework
   restriction; the recorder is the manual emitter, framework-agnostic.)
3. `claude_code.py` `_AGENTS_MD_SUBSECTION`: add that revising a *rejected plan
   document* through the normal edit tools is the authorized path (so the halt-hint
   no longer implies "no legitimate edit here"). Keep the "don't self-bypass" norm.
4. `super-harness sync --agents-md` (regenerate) → `sync --check` clean.
5. **Commit** — `docs: authorized reject-loop revision + honest limitations`

---

## Task 9: full verification + reject-loop e2e (honest proof)

**Files:**
- Test: `tests/e2e/test_plan_authoring_reject_loop.py` (create)

**Step 1: e2e** — synthetic `PLAN_REJECTED` change with a recorded plan artifact;
assert the hook path (positional + claude shim) ALLOWs a `Write` to the artifact and
BLOCKs a `Write` to a source file — i.e. the reject loop needs no Bash. (This is the
honest live proof; do NOT claim this change proved it on its *own* plan phase — see
design Bootstrap disclosure.)

**Step 2:**

```bash
python -m pytest -q
ruff check . && ruff format --check .
mypy src
super-harness verify
super-harness decision check
super-harness sync --check
```

**Step 3: Commit** — `test(e2e): reject-loop plan revision authorized without shell bypass`

---

## Lifecycle wrapper (self-host) — with honest bootstrap

> Not implementation steps. **Bootstrap disclosure:** this change's own plan phase
> runs before the fix is live, so its plan convergence happens at state `None`
> (abandon → revise → re-`change start`), the design's endorsed draft-before-start
> path — NOT a shell bypass. We do NOT claim zero-Bash proof on this change's own
> reject loop. The install is editable, so once Task 3–5 land the gate honors the
> carve-out for *subsequent* changes; that plus the Task 9 e2e is the proof.

1. Converge design+plan at state `None` via 2-source plan review (done/iterating).
2. `super-harness change start plan-authoring-gate-v2` (→ INTENT_DECLARED).
3. `super-harness plan ready plan-authoring-gate-v2 --scope @<scope.yaml>` (scope =
   this plan's frontmatter `files`). If the harness plan review REJECTs and needs
   plan-doc edits: `change abandon` → revise at state `None` → re-`change start`
   (bootstrap; disclosed) — do NOT Bash-edit under the gate.
4. 2-source **plan review** (Claude subagent + `codex exec`) until converged.
5. `implementation start` → Task 1–9 TDD loop → batch ALL edits → `done`.
6. 2-source **code review** at full intensity (gate-decision core — do NOT thin the
   second source; per #82 the second actor catches the seams). `decision reconcile`/
   `ratify` LAST (pothole ⑧).
7. `attest write` + `attest verify --base main --head HEAD`; PR; merge; `on-merge`.
8. Refresh `private/OPEN-ITEMS.md` (retire HG-PLAN-AUTHORING; register the deferred
   adapter cut; correct pothole ⑩ for the plan case), ledger (`.md`+`.html`),
   `NEXT-SESSION-PROMPT.md`, auto-memory.

---

## Anti-ritual notes

- Value to prove in-anger: **a synthetic reject loop needs no Bash** (Task 9 e2e) +
  the live 2-source plan-review convergence done gate-honestly. Do not overclaim.
- Codex asymmetry, hardlink residual, manual-`plan ready`-recording-only (adapter
  auto-recording deferred), bootstrap — all disclosed.
- Full 2-source code review for the gate core; do not reflexively max plan rounds.
