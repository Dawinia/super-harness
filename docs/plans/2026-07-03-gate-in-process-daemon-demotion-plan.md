# Gate In-Process + Daemon→Observer Demotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the PreToolUse gate decision fully in-process (snapshot loader → pure policy), delete the UDS RPC layer, and demote the daemon to an optional filesystem-only framework observer. Resolves REVIEW-FINDINGS §F7 (daemon doesn't earn its rent / silent fail-open) and §F8 (block message drops `suggested_action`) in one cut.

**Architecture:** Two planes with **no runtime dependency on each other**, meeting only through `events.jsonl`/`state.yaml`. Decision plane = `hook shim | gate check CLI → load_state_snapshot(root)` (the single I/O seam, one parse, CSafeLoader-preferred, never-raises, consolidates the three defensive parses) `→ PreToolUseGate.decide` (pure). Observation plane = a resident host that only runs watchdog `Observer`s and emits events (#67 flock); liveness = pidfile-flock probe; zero sockets, zero protocol, zero client.

**Tech Stack:** Python 3.10–3.13, click (CLI), PyYAML (CSafeLoader), watchdog (observer), fcntl flock (liveness + #67 write path), pytest.

> **Revision note:** This is **revision 3**, after **two rounds** of two-actor plan review (Claude subagent + `codex exec --sandbox read-only`), converged. Full round-by-round findings + dispositions are logged at the end under **"Review resolution log."** Rev-1→rev-2 (structural): `gate check` CLI rewired to the snapshot; RPC deletion sequenced after all src+test importers are rewritten (a `conftest.py` `DaemonServer` import would otherwise break collection of the whole `tests/integration/daemon/` package); daemon-coupled e2e/integration tests rewritten; `load_state_snapshot` made never-raises; read-only test switched to `sys.addaudithook`. Rev-2→rev-3 (localized correctness): `_observer_binary` resolves via `sysconfig.get_path("scripts")` (robust under `python -m pytest`); non-UTF-8 read no longer fails closed (`errors="replace"`); `current_state` type-guarded on the override path; `is_running` uses `O_RDONLY`; Task-9 grep-gate made import-specific + stale-prose sweep; concurrent test proves single-instance via identical PIDs. Rev-3 (round-3, **converged**): both actors found the same single one-line bug — `load_state_snapshot`'s guard-fallback returned `change_id_override` instead of the resolved `cid` — now fixed by hoisting `cid` above the `try`; all seven round-2 fixes independently verified correct.

---

## Background the executor MUST read first

- `docs/plans/2026-07-03-gate-in-process-daemon-demotion-design.md` — the design of record (root causes R1/R2/R3, two-plane diagram, component-disposition table, closed failure set, tax list). **This plan implements that design; if they disagree, the design doc wins — except where this plan explicitly corrects a verified factual error in the design (see "Verified corrections" below).**
- The already-proven in-process gate path is `cli/gate.py::gate_check` → `_read_change_state` → `PreToolUseGate().decide(...)`, documented "reads `state.yaml` in-process … with **NO daemon dependency**." The decision plane *reuses* this path; the snapshot loader replaces the two ad-hoc parses (`read_active_change_id` + `_read_change_state`) with one.
- The gate policy SSOT is `gates/decisions.py::PRE_TOOL_USE_DECISIONS` + `SUGGESTIONS`; the pure engine is `gates/pre_tool_use.py::PreToolUseGate`. **Both unchanged in behavior** — F8's `suggested_action` already flows out of `PreToolUseGate`; the daemon dispatch dropped it, the in-process path picks it up for free.

## Verified corrections to the design doc's tax list (checked against the repo; re-confirmed by both plan reviewers)

1. **Only `src/super_harness/gates/decisions.py` is a reconciled anchor in scope.** It carries the sole `# @decision:d-single-gate-policy` sentinel and is the only file in that decision's `reconciled_anchors`. The design's "`server.py` and `hook_entry.py` sit on reconciled anchors" is **wrong** (verified: `grep -rn "@decision:" src/super_harness/daemon/` → only `gates/decisions.py`; no `docs/decisions/*.md` `reconciled_anchors` lists `server.py`/`hook_entry.py`). Tier-2 reconcile tax = one file, `gates/decisions.py`.
2. **`d-single-gate-policy` re-ratify tax is real** — ratified body says "daemon + in-process gate both read it" and it has a `ratified_text_hash` → body edit needs `decision ratify` (pothole #6), adjacent to the edit.
3. **`d-core-is-base` is OUT of scope** — its "(e.g. by the daemon)" is illustrative; the observer host still imports core, so the illustration and the import-linter contract are unaffected. Editing it would force a needless tier-1 re-ratify + bite-test.
4. **No `pyproject.toml` change.** `super-harness-daemon = "super_harness.daemon.server:main"` stays valid (server.py keeps `main()` as the observer host); `super-harness-hook` unchanged. We keep the module name `server.py`, the package `daemon/`, the script `super-harness-daemon`, and the runtime filenames `daemon.pid`/`daemon.log`. The **only** settled rename is the user-facing CLI command group `daemon`→`observe`. Renaming the package/script/runtime-files was considered and deferred (out of settled scope; churn across ~12 test modules for zero architectural gain). Because runtime filenames are unchanged and the socket was never in the gitignore injector (a UDS is git-untracked), the `.gitignore` managed block needs **no change** — a verify-only step.

---

## File Structure

**New:**
- `src/super_harness/core/state_snapshot.py` — THE single I/O seam. `load_state_snapshot(root, *, change_id_override) → StateSnapshot(change_id, state)`: one `state.yaml` parse (CSafeLoader-preferred, SafeLoader fallback), resolves active change (override > recency via shared `pick_active_change`), reconstructs the active `ChangeState`. **Never raises** — every corrupt/unhashable shape degrades to `state=None`. Consolidates the three defensive parses (`hot_state.get_change`, `active_change.read_active_change_id`, `cli/gate._read_change_state`).
- `src/super_harness/cli/observe.py` — the renamed `daemon` command group (`observe start/stop/status`), socket/protocol-free.
- Tests: `tests/unit/core/test_state_snapshot.py`, `tests/integration/daemon/test_observer_host.py`, `tests/unit/cli/test_observe.py`.

**Modified (src):**
- `src/super_harness/daemon/hook_entry.py` — `_decide` → snapshot+policy; returns `(decision, reason, suggested_action)`; block message gains the suggestion (F8).
- `src/super_harness/cli/gate.py` — `gate_check` → `load_state_snapshot`; delete the now-dead `_read_change_state`. (**Second decision-plane entry point — required for the "single seam" claim.**)
- `src/super_harness/daemon/server.py` — stripped from UDS gate server to observer host (`daemonize` + JSON logging kept; `DaemonServer`/accept loop/gate dispatch replaced with `run_observer_host`).
- `src/super_harness/daemon/supervisor.py` — observer lifecycle only: absolute-path spawn (raises if the sibling binary is absent — no bare-name fallback), pidfile-flock liveness. Deletes the hot half entirely.
- `src/super_harness/daemon/__init__.py` — docstring.
- `src/super_harness/cli/__init__.py` — `daemon_group` → `observe_group`.
- `src/super_harness/gates/decisions.py` — docstring only (drop "both the daemon … and the in-process"). **Reconciled anchor → reconcile tax.**
- `src/super_harness/core/active_change.py`, `src/super_harness/daemon/framework_observer.py` — stale-prose sweep only (comments referencing the deleted `HotState`/`DaemonServer`); no code change.

**Modified (docs/decisions):**
- `docs/decisions/d-single-gate-policy.md` (body → `reconcile` + `ratify`), `private/specs/2026-05-28-daemon-architecture.md` (supersede), `private/specs/2026-05-27-cli-command-surface.md`, `docs/getting-started.md`, `docs/cli-reference.md`, `docs/ARCHITECTURE.md`, `docs/adapters/claude-code.md`, `AGENTS.md` (regen).

**Modified (tests):** `tests/unit/daemon/test_hook_entry.py`, `tests/integration/daemon/test_hook_entry.py`, `tests/integration/daemon/conftest.py`, `tests/integration/daemon/test_daemonize.py`, `tests/integration/daemon/test_framework_observer.py`, `tests/unit/cli/test_gate_check.py`, `tests/e2e/conftest.py`, `tests/e2e/test_pre_tool_use_claude_code.py`, `tests/e2e/openspec_claude_code/test_full_lifecycle.py`.

**Deleted:**
- src: `daemon/client.py`, `daemon/protocol.py`, `daemon/hot_state.py`, `daemon/_uds_path.py`, `cli/daemon.py`.
- tests: `tests/unit/daemon/test_client.py`, `test_protocol.py`, `test_hot_state.py`; `tests/integration/daemon/test_server.py`, `test_concurrent_spawn.py`, `test_latency.py`, `test_readonly_invariant.py`, `test_supervisor.py`; `tests/unit/cli/test_daemon.py`.

---

## Phasing

**Green boundary = the PHASE, not the task.** Phase 3 is a coupled cut (rewrite every importer, then delete the RPC modules); intermediate commits inside Phase 3 may not all independently import-resolve, but the phase ends green. Every other phase ends green per its last task.

- **Phase 1 — Snapshot seam** (new module + tests).
- **Phase 2 — Decision plane in-process** (hook_entry + gate check CLI both → snapshot; F8). Daemon still exists; both entry points work.
- **Phase 3 — Retire RPC + stand up the observer plane** (supervisor strip · server→observer host + its coupled test rewrites · cli rename · hook/e2e test rewrites · THEN delete the orphaned RPC modules). Green asserted at phase end.
- **Phase 4 — Taxes & regeneration** (reconcile+ratify · spec · docs · AGENTS.md · gitignore verify).
- **Phase 5 — Full-suite green + self-host lifecycle**.

---

## Phase 1 — Snapshot seam

### Task 1: `load_state_snapshot` — the single, never-raising I/O seam

**Files:**
- Create: `src/super_harness/core/state_snapshot.py`
- Test: `tests/unit/core/test_state_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_state_snapshot.py
"""Unit tests for load_state_snapshot — the single I/O seam for the in-process gate.

Consolidates the three historical defensive parses into ONE parse returning the
active change's reconstructed ChangeState. Every permissive branch (missing /
corrupt / non-mapping / unhashable field / no non-terminal change / unknown
override) resolves to state=None so the pure gate ALLOWs. The loader NEVER
raises and NEVER write-opens state.yaml.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from super_harness.core.state import ChangeState
from super_harness.core.state_snapshot import StateSnapshot, load_state_snapshot


def _write_state(root: Path, body: str) -> Path:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text(body, encoding="utf-8")
    return root


def test_no_state_file_is_empty_snapshot(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    assert load_state_snapshot(tmp_path) == StateSnapshot(change_id=None, state=None)


def test_recency_picks_most_recent_non_terminal(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n"
        "  old:\n    change_id: old\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n"
        "  new:\n    change_id: new\n    current_state: AWAITING_CODE_REVIEW\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path)
    assert snap.change_id == "new"
    assert isinstance(snap.state, ChangeState)
    assert snap.state.current_state == "AWAITING_CODE_REVIEW"


def test_override_selects_named_change(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n"
        "  a:\n    change_id: a\n    current_state: READY_TO_MERGE\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n"
        "  b:\n    change_id: b\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path, change_id_override="b")
    assert snap.change_id == "b"
    assert snap.state is not None and snap.state.current_state == "PLAN_APPROVED"


def test_override_names_unknown_change_yields_no_state(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path, change_id_override="ghost")
    assert snap.change_id == "ghost"
    assert snap.state is None


def test_all_terminal_yields_no_active(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n  done:\n    change_id: done\n    current_state: ARCHIVED\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n",
    )
    assert load_state_snapshot(tmp_path) == StateSnapshot(change_id=None, state=None)


@pytest.mark.parametrize("body", ["just a string\n", "- a\n- b\n", "[]\n"])
def test_non_mapping_root_is_permissive(tmp_path: Path, body: str) -> None:
    _write_state(tmp_path, body)
    assert load_state_snapshot(tmp_path).state is None


def test_corrupt_yaml_is_permissive(tmp_path: Path) -> None:
    _write_state(tmp_path, "changes: {unterminated\n")
    assert load_state_snapshot(tmp_path).state is None


def test_non_utf8_bytes_are_permissive(tmp_path: Path) -> None:
    # Invalid UTF-8 raises UnicodeDecodeError (a ValueError, NOT an OSError) — it
    # must not escape the loader. In positional mode a raise → exit 1 → BLOCK, i.e.
    # fail-CLOSED, the opposite of Axiom 1. `errors="replace"` on the read
    # neutralizes it; the mangled text then fails the YAML parse → state=None.
    harness = tmp_path / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_bytes(b"\xff\xfe changes: {a: b}\n")
    assert load_state_snapshot(tmp_path).state is None  # must not raise


def test_record_with_unknown_field_is_permissive(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n    bogus_field: 1\n",
    )
    snap = load_state_snapshot(tmp_path)
    assert snap.change_id == "a"
    assert snap.state is None


@pytest.mark.parametrize("bad", ["[x]", "{a: b}", "5"])
@pytest.mark.parametrize("override", [None, "a"])
def test_malformed_current_state_never_raises(tmp_path: Path, bad: str, override: str | None) -> None:
    # A list/dict current_state is unhashable (pick_active_change's frozenset test
    # raises TypeError); an int is hashable but not a real state. BOTH resolution
    # paths must degrade to state=None WITHOUT raising: the recency path
    # (override=None) AND the override path (which SKIPS pick_active_change, so the
    # guard is the isinstance(current_state, str) check, not the frozenset test).
    # The downstream pure gate must not raise on the result either.
    from super_harness.gates import ProposedAction
    from super_harness.gates.pre_tool_use import PreToolUseGate

    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n"
        f"    current_state: {bad}\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path, change_id_override=override)  # must not raise
    assert snap.state is None
    # And the pure policy must not raise on the (None) state either.
    PreToolUseGate().decide(ProposedAction(kind="edit", file="f.py"), snap.state, [])


def test_loader_never_write_opens_state(tmp_path: Path) -> None:
    # The decision plane must be read-only w.r.t. state.yaml. A builtins.open spy
    # is INSUFFICIENT (Path.read_text bypasses builtins.open on 3.11+, so the spy
    # sees nothing and passes vacuously). Use sys.addaudithook, which fires at the
    # C level for pathlib reads / io.open / builtins.open alike on 3.10–3.13.
    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    reads: list[str] = []
    writes: list[str] = []
    active = True  # addaudithook is process-global + permanent; gate the body so it
    #              becomes a no-op after this test (no slow leak across the session).

    def hook(event: str, args: tuple) -> None:
        if not active or event != "open":
            return
        path, mode = args[0], args[1]
        if not (isinstance(path, str) and path.endswith("state.yaml")):
            return
        # mode is None for os.open (raw fd); str for io.open/builtins.open/pathlib.
        if mode is None:
            return
        (writes if any(c in mode for c in "wax+") else reads).append(f"{path}:{mode}")

    sys.addaudithook(hook)  # cannot be removed; keep it cheap + gated by `active`
    try:
        load_state_snapshot(tmp_path)
    finally:
        active = False  # noqa: F841 — read by the closure above on every later open
    assert writes == [], f"gate loader write-opened state.yaml: {writes}"
    assert reads, "audit hook saw no read of state.yaml — the assertion would be vacuous"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/core/test_state_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'super_harness.core.state_snapshot'`.

- [ ] **Step 3: Write the module (never-raises: the whole post-parse resolution is guarded)**

```python
# src/super_harness/core/state_snapshot.py
"""The single I/O seam for the in-process PreToolUse gate (design 2026-07-03).

`load_state_snapshot` performs ONE `state.yaml` parse and returns the active
change's reconstructed `ChangeState`. It consolidates the three historical
defensive parses — `daemon.hot_state.get_change`, `core.active_change.
read_active_change_id`, and `cli/gate._read_change_state` — that used to each
re-read the same file on either side of the (now-deleted) daemon RPC boundary.

CSafeLoader (libyaml) is preferred over the pure-Python SafeLoader (measured
68ms → 7ms on this repo's state.yaml) with a graceful fallback. The loader is
pure-read and **NEVER raises**: every corrupt / missing / non-mapping /
unhashable-field / unknown-change branch degrades to `state=None`, which the
pure `PreToolUseGate` maps to ALLOW ("no active change"). This closed,
deterministic failure set replaces the daemon's open set (reachability ×
protocol version × cache freshness × PATH).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from super_harness.core.active_change import pick_active_change
from super_harness.core.state import ChangeState


@dataclass(frozen=True)
class StateSnapshot:
    """Immutable result of one `state.yaml` read for the gate decision.

    `change_id` is the resolved active change (override > recency), or None.
    `state` is that change's reconstructed `ChangeState`, or None when there is
    no active change / the record is absent or malformed (→ gate ALLOWs).
    """

    change_id: str | None
    state: ChangeState | None


def _safe_load(text: str) -> object:
    """Parse YAML with CSafeLoader when available, else the pure SafeLoader."""
    import yaml

    try:
        loader = yaml.CSafeLoader
    except AttributeError:  # libyaml not built into this PyYAML
        loader = yaml.SafeLoader
    return yaml.load(text, Loader=loader)


def load_state_snapshot(
    root: Path, *, change_id_override: str | None = None
) -> StateSnapshot:
    """Read `.harness/state.yaml` once and resolve the active change. NEVER raises."""
    state_path = root / ".harness" / "state.yaml"
    try:
        # errors="replace" so non-UTF-8 bytes NEVER raise UnicodeDecodeError (a
        # ValueError, NOT an OSError — it would escape a bare `except OSError` and
        # propagate out on the hot path; in positional mode a raise → exit 1 →
        # BLOCK, i.e. fail-CLOSED, the opposite of Axiom 1). A mangled file then
        # simply fails the YAML parse below → state=None (ALLOW).
        text = state_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return StateSnapshot(change_id=None, state=None)
    try:
        data = _safe_load(text)
    except Exception:
        return StateSnapshot(change_id=None, state=None)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return StateSnapshot(change_id=None, state=None)
    changes = data.get("changes")
    if not isinstance(changes, dict):
        # Malformed/absent changes map: honour an explicit override id (harmless)
        # but there is no state to apply → ALLOW.
        return StateSnapshot(change_id=change_id_override, state=None)

    # The whole resolution is guarded: a malformed record shape (e.g. an
    # unhashable `current_state` that trips pick_active_change's frozenset
    # membership test, or a record the dataclass can't accept) must degrade to
    # no-active-change, never raise on the hot path. `cid` is initialized BEFORE
    # the try so the fallback returns the best-resolved change_id (not just the
    # override) — a record that resolves fine but fails ChangeState() still
    # reports its change_id with state=None.
    cid = change_id_override
    try:
        if not cid:
            candidates = (
                (str(c), r.get("current_state", ""), r.get("last_event_at", ""))
                for c, r in changes.items()
                if isinstance(r, dict)
            )
            cid = pick_active_change(candidates)
        if cid is None:
            return StateSnapshot(change_id=None, state=None)
        record = changes.get(cid)
        if not isinstance(record, dict):
            return StateSnapshot(change_id=cid, state=None)
        # `current_state` MUST be a str. The override path skips pick_active_change
        # (whose frozenset test would otherwise reject an unhashable value), and
        # ChangeState does NOT enforce field types — a list/dict/int current_state
        # would sail through construction and then raise TypeError (unhashable) or
        # misbehave inside PreToolUseGate's `PRE_TOOL_USE_DECISIONS.get(...)` dict
        # lookup. Guard it here so a corrupt field degrades to no-active-change
        # (ALLOW), never a downstream raise.
        if not isinstance(record.get("current_state"), str):
            return StateSnapshot(change_id=cid, state=None)
        return StateSnapshot(change_id=cid, state=ChangeState(**record))
    except Exception:
        return StateSnapshot(change_id=cid, state=None)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/core/test_state_snapshot.py -q`
Expected: PASS (all cases, including the unhashable + addaudithook tests).

- [ ] **Step 5: Confirm core stays the base layer**

Run: `PYTHONPATH=src .venv/bin/lint-imports --config .importlinter --contract core-is-base --no-cache`
Expected: PASS (imports only `core.active_change` + `core.state`).

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/core/state_snapshot.py tests/unit/core/test_state_snapshot.py
git commit -m "feat(core): add load_state_snapshot single I/O seam (never-raises) for in-process gate"
```

---

## Phase 2 — Decision plane in-process (both entry points)

### Task 2: Rewire `hook_entry._decide` in-process; add `suggested_action` to the block message (F8)

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py`
- Test: `tests/unit/daemon/test_hook_entry.py` (rewrite)

- [ ] **Step 1: Rewrite the unit test in full**

Replace the entire contents of `tests/unit/daemon/test_hook_entry.py` with:

```python
"""Unit tests for the in-process PreToolUse decision core (design 2026-07-03).

No daemon, no socket, no timeout knob: `_decide` resolves the workspace, honours
the kill switch, loads ONE state snapshot, and runs the pure PreToolUseGate. The
block message now carries the state's `suggested_action` (F8).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.daemon import hook_entry


def _init_state(root: Path, change_id: str, state: str, at: str = "2026-07-02T00:00:00Z") -> None:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text(
        "changes:\n"
        f"  {change_id}:\n    change_id: {change_id}\n"
        f"    current_state: {state}\n    last_event_at: '{at}'\n",
        encoding="utf-8",
    )


@pytest.fixture()
def in_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPER_HARNESS_CHANGE_ID", raising=False)
    return tmp_path


def test_no_harness_allows(in_workspace: Path) -> None:
    decision, _reason, suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"
    assert suggested is None


def test_blocking_state_returns_suggestion(in_workspace: Path) -> None:
    _init_state(in_workspace, "c1", "INTENT_DECLARED")
    decision, reason, suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "block"
    assert "INTENT_DECLARED" in reason
    assert suggested == "Draft a plan, then mark it ready, then retry the edit."


def test_allowing_state_allows(in_workspace: Path) -> None:
    _init_state(in_workspace, "c1", "IMPLEMENTATION_IN_PROGRESS")
    decision, _reason, _suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"


def test_env_override_selects_change(in_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = in_workspace / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text(
        "changes:\n"
        "  live:\n    change_id: live\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n"
        "  frozen:\n    change_id: frozen\n    current_state: READY_TO_MERGE\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "frozen")
    decision, _reason, suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "block"
    assert suggested == "Open/merge the PR; do not edit further."


def test_kill_switch_allows_and_records_bypass(in_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_state(in_workspace, "c1", "INTENT_DECLARED")  # would block
    (in_workspace / ".harness" / "gate-disabled").touch()
    recorded: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        hook_entry, "_record_bypass",
        lambda root, *, tool, file: recorded.append((tool, file)),
    )
    decision, _reason, _suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"
    assert recorded == [("Edit", "f.py")]


def test_corrupt_state_fails_open(in_workspace: Path) -> None:
    harness = in_workspace / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text("changes: {oops\n", encoding="utf-8")
    decision, _reason, _suggested = hook_entry._decide("Edit", "f.py")
    assert decision == "allow"


def test_format_block_includes_suggestion_and_halt_hint() -> None:
    msg = hook_entry._format_block("READY_TO_MERGE: ready for merge", "Open/merge the PR; do not edit further.")
    assert "BLOCK (READY_TO_MERGE: ready for merge)" in msg
    assert "Open/merge the PR" in msg
    assert "Stop and tell the human" in msg


def test_format_block_without_suggestion() -> None:
    msg = hook_entry._format_block("unknown state: WAT", None)
    assert "BLOCK (unknown state: WAT)" in msg
    assert "Stop and tell the human" in msg
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/daemon/test_hook_entry.py -q`
Expected: FAIL — `_decide` returns a 2-tuple (unpack error); `_format_block` missing.

- [ ] **Step 3: Rewire `_decide`, add `_format_block`, update the three shims**

In `src/super_harness/daemon/hook_entry.py`:

(a) **Module docstring** — replace the closing `env` + fail-open paragraph so it no longer mentions the daemon:
```python
env SUPER_HARNESS_CHANGE_ID  optional override (all modes); default derives
                              the active (most recently active non-terminal) change
                              from the `changes` map in .harness/state.yaml.

Fail-open everywhere (Axiom 1: prevent, don't punish — never block on a call
shape we don't understand): empty argv, no .harness/, malformed stdin, corrupt
state, and an unknown --agent ALL ALLOW. The decision is pure and in-process
(design 2026-07-03): one state.yaml snapshot → the pure PreToolUseGate; there is
no daemon on the decision path.
```

(b) **Leave** the top-of-file `from super_harness.core.active_change import read_active_change_id` import — `_record_bypass` / `_read_active_change_id` still use it. **Remove** any `from super_harness.daemon import supervisor` (there is none at top level; it was a late import inside `_decide` — delete that late import when you rewrite `_decide`).

(c) **Add** `_format_block` just below `_HALT_HINT`:
```python
def _format_block(reason: str, suggested: str | None) -> str:
    """Build the BLOCK message: WHY (reason) + the state's what-to-do-next line
    (suggested_action, F8 — previously dropped by the daemon dispatch) + the halt
    hint. `suggested` is None for allowing states / no-active-change."""
    next_step = f" {suggested}" if suggested else ""
    return f"super-harness: BLOCK ({reason}).{next_step} {_HALT_HINT}"
```

(d) **Replace `_decide`** in full:
```python
def _decide(
    tool: str, file: str | None
) -> tuple[Literal["allow", "block"], str, str | None]:
    """Shared in-process decision core for all invocation modes (design 2026-07-03).

    Resolves the workspace root (ALLOW if no .harness/), short-circuits the kill
    switch (ALLOW + audit), loads ONE state snapshot (the single I/O seam), and
    runs the pure `PreToolUseGate`. Returns `(decision, reason, suggested_action)`;
    callers map the block onto the exit code / envelope their agent expects. No
    daemon, no socket — the failure set is closed and deterministic.
    """
    try:
        root = find_harness_root(Path.cwd())
    except HarnessNotInitialized:
        return "allow", "no .harness in workspace", None

    if (root / ".harness" / "gate-disabled").exists():
        _record_bypass(root, tool=tool, file=file)
        return "allow", "gate disabled (.harness/gate-disabled present)", None

    import os

    from super_harness.core.state_snapshot import load_state_snapshot
    from super_harness.gates import GateDecision, ProposedAction
    from super_harness.gates.pre_tool_use import PreToolUseGate

    override = os.environ.get("SUPER_HARNESS_CHANGE_ID")
    snapshot = load_state_snapshot(root, change_id_override=override)
    result = PreToolUseGate().decide(
        ProposedAction(kind="edit", file=file), snapshot.state, []
    )
    if result.decision is GateDecision.BLOCK:
        return "block", result.reason, result.suggested_action
    return "allow", result.reason, result.suggested_action
```

(e) **Update the three shims** to unpack the 3-tuple and use `_format_block`:

`_run_positional`:
```python
    decision, reason, suggested = _decide(tool, file)
    if decision == "block":
        sys.stderr.write(_format_block(reason, suggested) + "\n")
        sys.exit(1)
    sys.exit(0)
```
`_run_claude_code_shim` (same, exit 2):
```python
    decision, reason, suggested = _decide(tool, file)
    if decision == "block":
        sys.stderr.write(_format_block(reason, suggested) + "\n")
        sys.exit(2)  # Claude Code: exit 2 = block + stderr → model
    sys.exit(0)
```
`_run_codex_shim` (same, deny JSON):
```python
    decision, reason, suggested = _decide(tool, None)
    if decision == "block":
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": _format_block(reason, suggested),
                }
            },
            sys.stdout,
        )
        sys.exit(0)
    sys.exit(0)
```

- [ ] **Step 4: Run to verify it passes + the hook stays daemon-free**

Run: `.venv/bin/python -m pytest tests/unit/daemon/test_hook_entry.py -q`
Expected: PASS.

Run: `.venv/bin/python -c "import super_harness.daemon.hook_entry as h, sys; assert 'super_harness.daemon.supervisor' not in sys.modules and 'super_harness.daemon.client' not in sys.modules; print('clean')"`
Expected: `clean`.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/unit/daemon/test_hook_entry.py
git commit -m "feat(gate): decide PreToolUse in-process via snapshot+policy; add suggested_action to block message (F8)"
```

### Task 3: Rewire the `gate check` CLI to the snapshot (the second decision-plane entry point)

The `gate check pre-tool-use` command is the other decision-plane entry (manual/CI/debug). For the snapshot to be THE single seam, `gate_check` must resolve state through `load_state_snapshot`, not its own `read_active_change_id` + `_read_change_state` pair.

**Files:**
- Modify: `src/super_harness/cli/gate.py`
- Test: `tests/unit/cli/test_gate_check.py` (adjust)

- [ ] **Step 1: Rewire `gate_check`'s state resolution**

In `src/super_harness/cli/gate.py`, inside `gate_check` (currently lines ~169–173), replace:
```python
    cid = change_id or read_active_change_id(root)
    state = _read_change_state(root, cid)
    result = PreToolUseGate().decide(ProposedAction(kind="edit", file=file), state, [])
    allow = result.decision is GateDecision.ALLOW
    current_state = state.current_state if state is not None else None
```
with:
```python
    from super_harness.core.state_snapshot import load_state_snapshot

    snapshot = load_state_snapshot(root, change_id_override=change_id)
    result = PreToolUseGate().decide(
        ProposedAction(kind="edit", file=file), snapshot.state, []
    )
    allow = result.decision is GateDecision.ALLOW
    current_state = snapshot.state.current_state if snapshot.state is not None else None
```

- [ ] **Step 2: Delete the now-dead `_read_change_state` + prune imports**

Delete the `_read_change_state` function (lines ~196–223). Then prune imports that become unused in `cli/gate.py`:
- Remove `from super_harness.core.active_change import read_active_change_id` **only if** no other use remains in the file (grep first: `grep -n read_active_change_id src/super_harness/cli/gate.py`).
- Remove `from super_harness.core.state import ChangeState`, `from super_harness.core.state_yaml import read_state_yaml`, `from super_harness.core.paths import ... state_path ...`, and the `import yaml` **only if** now unused (grep each: `_read_change_state` was their only consumer in this file — confirm with `grep -n "ChangeState\|read_state_yaml\|state_path\|yaml\." src/super_harness/cli/gate.py`). Keep `find_harness_root` / `gates_yaml_path` (still used by `gate list`).
- **Sweep the `gate_check` docstring** (currently ~line 141): it still says the hot path "talks to the daemon" and calls itself "NOT the hot path (that's the click-less `super-harness-hook` binary, which talks to the daemon)". There is no daemon on any path now — reword to: "Manual/CI/debug entry to the pre-tool-use gate; the click-less `super-harness-hook` binary is the hot path. Both decide **in-process** through `load_state_snapshot` + `PreToolUseGate` — NO daemon."

- [ ] **Step 3: Update `test_gate_check.py`**

Open `tests/unit/cli/test_gate_check.py`. It should already exercise `gate check pre-tool-use` end-to-end (writing a `state.yaml`, asserting decision/exit/`suggested_action` in JSON). The behavior is unchanged, so tests should still pass. If any test monkeypatched `read_active_change_id` or `_read_change_state` directly (grep: `grep -n "_read_change_state\|read_active_change_id" tests/unit/cli/test_gate_check.py`), rewrite that test to drive through a real `state.yaml` instead (the snapshot reads the file; there is no separate seam to patch).

- [ ] **Step 4: Run**

Run: `.venv/bin/python -m pytest tests/unit/cli/test_gate_check.py -q`
Expected: PASS. (Confirms both decision-plane entry points now share the one seam.)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/gate.py tests/unit/cli/test_gate_check.py
git commit -m "refactor(gate): route gate check CLI through load_state_snapshot; drop _read_change_state"
```

---

## Phase 3 — Retire RPC + stand up the observer plane

> **Coupled cut.** Tasks 4–8 rewrite every importer of the RPC modules (src **and** tests); Task 9 then deletes the orphaned modules. Do NOT run the full suite for green until the end of Task 9 — intermediate states will not all import-resolve. Commit per task for reviewability; the phase is the green boundary.

### Task 4: Shrink `supervisor.py` to observer lifecycle (absolute-path spawn, flock liveness)

**Files:**
- Modify: `src/super_harness/daemon/supervisor.py` (full replace)
- Create: `tests/integration/daemon/test_observer_host.py`
- Delete: `tests/integration/daemon/test_supervisor.py`

- [ ] **Step 1: Replace `supervisor.py` in full**

```python
# src/super_harness/daemon/supervisor.py
"""Lifecycle for the OPTIONAL framework-observer host (design 2026-07-03).

Post-demotion the resident process is no longer on the gate hot path (the
PreToolUse gate decides in-process via core.state_snapshot + gates.pre_tool_use).
This module manages only the observer host:

- spawn by ABSOLUTE path resolved from the running interpreter's scripts dir
  (`sysconfig.get_path("scripts")` → the venv/pipx `bin/`), NOT PATH.
  console_scripts install `super-harness` and `super-harness-daemon` side by side
  there, but the hook/CLI environment often has no venv bin/ on PATH — a bare-name
  spawn then raises OSError and the process silently never comes up (the
  month-long fail-open root cause). This realizes the design's "absolute path,
  not bare name" intent while being invocation-independent: unlike `sys.argv[0]`
  (which under `python -m pytest` points at pytest's package dir) and unlike
  `Path(sys.executable).resolve()` (which walks a symlinked `.venv/bin/python`
  out of the venv). If the binary is genuinely absent we RAISE (the explicit
  `observe start` path — a clear error beats a PATH-ambiguous bare-name spawn).
- liveness by pidfile flock: `daemonize()` holds `LOCK_EX` on `.harness/daemon.pid`
  for the process lifetime, so a non-blocking `LOCK_EX` probe that WOULD block
  proves a live host holds it; one that acquires proves nobody does. (flock is
  advisory but conflicts across processes regardless of open mode, on Linux and
  macOS alike; the kernel releases it on process death, so a `kill -9`'d host's
  stale pidfile correctly reads as dead.)

No socket, no protocol, no client, no fail-open, no fallback audit.
"""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sysconfig
import time
from pathlib import Path


def _pid_path(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "daemon.pid"


def _observer_binary() -> str:
    """Absolute path to the `super-harness-daemon` entry-point (observer host),
    resolved from the RUNNING INTERPRETER's scripts dir (`sysconfig.get_path
    ("scripts")` → the venv/pipx `bin/` where console_scripts install side by
    side). Invocation-independent (works under the console script AND under
    `python -m pytest`, unlike `sys.argv[0]`).

    Raises:
        RuntimeError: the binary is absent (unusual install). We do NOT fall back
        to a bare `super-harness-daemon` — that reintroduces the PATH ambiguity
        this whole change exists to kill.
    """
    binary = Path(sysconfig.get_path("scripts")) / "super-harness-daemon"
    if not binary.exists():
        raise RuntimeError(
            f"observer host binary not found in the scripts dir ({binary}); "
            "install super-harness-daemon alongside super-harness"
        )
    return str(binary)


def is_running(workspace_root: Path) -> bool:
    """True iff a live observer host holds the pidfile flock. No ping, no socket.

    Opens the pidfile O_RDONLY (flock is mode-independent — it works on a
    read-only fd and conflicts across processes regardless — so O_RDONLY avoids
    the EROFS/EACCES failure modes O_RDWR would add on a read-only mount/pidfile).
    Any OSError other than the expected `BlockingIOError` (held) is treated as
    'cannot determine → not running' so `status`/`start` never raise on a quirk."""
    pid_path = _pid_path(workspace_root)
    if not pid_path.exists():
        return False
    try:
        fd = os.open(str(pid_path), os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True  # held by a live host
        except OSError:
            return False  # can't probe → treat as not-running
        # Acquired → nobody holds it; release and report dead.
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)


def ensure_running(workspace_root: Path, *, wait_seconds: float = 5.0) -> int:
    """Spawn the observer host (idempotent) and block until it holds the pidfile
    flock. Returns the host PID.

    Raises:
        RuntimeError: sibling binary absent, spawn failed, or host did not become
        live in time.
    """
    if is_running(workspace_root):
        return _read_pid(workspace_root)
    binary = _observer_binary()  # raises if absent
    try:
        subprocess.Popen(
            [binary, "--workspace", str(workspace_root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"could not spawn observer host ({binary}): {exc}") from exc
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        # is_running flips True the instant the grandchild holds the flock, which
        # in daemonize() PRECEDES the ftruncate+write(pid); poll until the pid is
        # actually readable so a caller never gets 0 from a half-written pidfile.
        if is_running(workspace_root):
            pid = _read_pid(workspace_root)
            if pid > 0:
                return pid
        time.sleep(0.05)
    raise RuntimeError(f"observer host did not become live within {wait_seconds:.1f}s")


def stop(workspace_root: Path, *, wait_seconds: float = 2.0) -> bool:
    """SIGTERM the observer host and wait for it to exit (flock release).

    Returns True if it stopped (or was already stopped), False on timeout.
    """
    if not is_running(workspace_root):
        return True
    pid = _read_pid(workspace_root)
    if pid <= 0:
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if not is_running(workspace_root):
            return True
        time.sleep(0.05)
    return not is_running(workspace_root)


def _read_pid(workspace_root: Path, *, default: int = 0) -> int:
    try:
        return int(_pid_path(workspace_root).read_text().strip())
    except (ValueError, OSError):  # FileNotFoundError ⊂ OSError
        return default
```

- [ ] **Step 2: Write the observer-host integration test (incl. concurrent single-instance)**

```python
# tests/integration/daemon/test_observer_host.py
"""Integration tests for the observer-host lifecycle (design 2026-07-03).

Exercises the real `super-harness-daemon` binary via supervisor: absolute-path
spawn, pidfile-flock liveness, idempotent start, SIGTERM stop, and the
single-instance invariant under concurrent starts (formerly covered by the
deleted test_concurrent_spawn.py — the daemonize pidfile flock still enforces
one live host). No socket.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.daemon import supervisor


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    yield tmp_path
    supervisor.stop(tmp_path)  # best-effort cleanup


def test_not_running_before_start(ws: Path) -> None:
    assert supervisor.is_running(ws) is False


def test_start_makes_it_live_then_stop(ws: Path) -> None:
    pid = supervisor.ensure_running(ws, wait_seconds=10.0)
    assert pid > 0
    assert supervisor.is_running(ws) is True
    # 2s stop budget preserves the retired daemon's clean-shutdown SLA coverage.
    assert supervisor.stop(ws, wait_seconds=2.0) is True
    assert supervisor.is_running(ws) is False


def test_start_is_idempotent(ws: Path) -> None:
    pid1 = supervisor.ensure_running(ws, wait_seconds=10.0)
    pid2 = supervisor.ensure_running(ws, wait_seconds=10.0)
    assert pid1 == pid2  # second call returns the live host, no respawn


def test_concurrent_starts_yield_one_live_host(ws: Path) -> None:
    # Concurrent ensure_running() calls must converge on ONE live host: the
    # daemonize pidfile flock lets exactly one grandchild survive (losers exit 1),
    # so EVERY caller that returns sees the SAME pid. (Threaded ensure_running,
    # not raw Popen: 'losers exit 1' is not observable through the Popen parent
    # after the double-fork, and identical return pids is the stronger proof.)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        pids = list(
            ex.map(lambda _: supervisor.ensure_running(ws, wait_seconds=15.0), range(5))
        )
    assert all(p == pids[0] for p in pids), f"expected one live host, got pids {pids}"
    assert pids[0] > 0
    assert supervisor.is_running(ws) is True


def test_observer_binary_is_scripts_dir_sibling() -> None:
    # Resolution is invocation-independent (sysconfig scripts dir), so this holds
    # under `python -m pytest` too — unconditional, non-vacuous. The test venv
    # installs super-harness-daemon, so _observer_binary() must NOT raise.
    import sysconfig

    expected = Path(sysconfig.get_path("scripts")) / "super-harness-daemon"
    resolved = supervisor._observer_binary()
    assert resolved == str(expected)
    assert Path(resolved).is_absolute()
```

- [ ] **Step 3: Delete the old supervisor test**

```bash
git rm tests/integration/daemon/test_supervisor.py
```

- [ ] **Step 4: Commit (do NOT run the full suite yet — coupled cut)**

Run the observer-host test in isolation to sanity-check the new supervisor:
Run: `.venv/bin/python -m pytest tests/integration/daemon/test_observer_host.py -q`
Expected: PASS (needs `.venv/bin/super-harness-daemon`; the observer host in Task 5 must be in place — if you commit Task 4 before Task 5, this test's spawn will fail because `server.py` still imports deleted modules? No — nothing is deleted yet in Task 4; `server.py` still has the OLD DaemonServer, which still works as a host. The spawned daemon comes up as the old UDS server, still holds the pidfile flock, so flock liveness passes. It is replaced in Task 5.)

```bash
git add src/super_harness/daemon/supervisor.py tests/integration/daemon/test_observer_host.py
git commit -m "refactor(observer): supervisor manages observer host only (absolute-path spawn, flock liveness)"
```

### Task 5: Strip `server.py` to the observer host + rewrite its coupled tests

**Files:**
- Modify: `src/super_harness/daemon/server.py` (strip), `src/super_harness/daemon/__init__.py`
- Modify: `tests/integration/daemon/conftest.py` (remove DaemonServer helpers AND the timeout fixture — one clean pass), `tests/integration/daemon/test_framework_observer.py` (rewrite against `run_observer_host`), `tests/integration/daemon/test_daemonize.py` (strip deleted-module imports + socket tests)
- Delete: `tests/integration/daemon/test_server.py`, `test_concurrent_spawn.py`, `test_latency.py`, `test_readonly_invariant.py`

- [ ] **Step 1: Replace `server.py` in full (keep `daemonize` + JSON logging verbatim)**

```python
# src/super_harness/daemon/server.py
"""Framework-observer host process — `super-harness-daemon` entry-point.

Demoted from a UDS gate server to a pure observation host (design 2026-07-03):
the PreToolUse gate now decides in-process (core.state_snapshot +
gates.pre_tool_use). This process's ONLY job is to host watchdog Observers that
watch framework artifacts and emit lifecycle events (daemon.framework_observer,
#67 flock on the write path). Liveness is the pidfile flock that `daemonize()`
holds for the process lifetime; there is no socket, no protocol, and no
request/response interface. The decision plane never talks to this process —
they meet only through events.jsonl / state.yaml.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from super_harness.daemon.framework_observer import build_manager_failsafe

__all__ = ["daemonize", "main", "run_observer_host"]

_log = logging.getLogger(__name__)


# -- JSON-lines logging (unchanged) ---------------------------------------

_LOGRECORD_STANDARD = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
})


class _JsonLineFormatter(logging.Formatter):
    """Emit one JSON object per log record (stable schema for AI parsing)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in _LOGRECORD_STANDARD or k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging(log_path: Path) -> None:
    """Wire the `super_harness.daemon` logger to a JSON-lines file handler."""
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(_JsonLineFormatter())
    root = logging.getLogger("super_harness.daemon")
    root.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.propagate = False


def run_observer_host(workspace_root: Path, stop: threading.Event) -> None:
    """Start framework Observers, block until `stop` is set, then stop them.

    FAIL-SAFE (Axiom 3 — observation must never crash noisily): a corrupt
    adapters.yaml makes build_manager_failsafe return None (host idles until
    SIGTERM); ANY watcher-start error is logged and swallowed. Observers spawn
    HERE — AFTER daemonize()'s single-thread `assert active_count()==1` fork —
    matching the historical post-fork ordering.
    """
    manager = None
    try:
        manager = build_manager_failsafe(workspace_root)
        if manager is not None:
            manager.start()
            _log.info("observer host: watching framework artifacts")
        else:
            _log.info("observer host: no framework watchers configured; idling")
    except Exception:
        _log.warning("observer host: watcher start failed; idling with no watchers")
        manager = None
    try:
        stop.wait()
    finally:
        if manager is not None:
            try:
                manager.stop()
            except Exception:
                _log.exception("observer host: manager.stop() failed during shutdown")


# -- POSIX double-fork daemonize (unchanged: the flock-liveness core) ------

def daemonize(pid_path: Path, log_path: Path) -> None:
    """Self-daemonize via POSIX double-fork; hold an exclusive pidfile flock for
    the process lifetime (single-instance + the liveness signal supervisor
    probes). Unchanged from the pre-demotion server — see git history for the
    Stevens APUE §13.3 rationale of each step."""
    assert threading.active_count() == 1, (
        f"daemonize() called with {threading.active_count()} live threads; "
        "POSIX fork in a multi-threaded process is undefined behavior. "
        "Must run before any thread is spawned."
    )
    if os.fork() != 0:
        os._exit(0)
    os.setsid()
    os.umask(0)
    if os.fork() != 0:
        os._exit(0)
    os.chdir("/")
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    logfd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(devnull, 0)
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)
    os.close(devnull)
    os.close(logfd)
    pid_fd = os.open(str(pid_path), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)  # another host won the race — single-instance
    os.ftruncate(pid_fd, 0)
    os.write(pid_fd, f"{os.getpid()}\n".encode())
    # KEEP pid_fd open for life of process: the flock auto-releases on death.


def main() -> int:
    """`super-harness-daemon` entry-point (observer host).

    Exit codes: 0 clean SIGTERM · 1 crash / flock loser · 3 no .harness/.
    """
    parser = argparse.ArgumentParser(prog="super-harness-daemon")
    parser.add_argument("--workspace", default=".", type=Path)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    harness_dir = workspace / ".harness"
    if not harness_dir.exists():
        print(
            f"super-harness-daemon: no .harness/ directory at {workspace}",
            file=sys.stderr,
        )
        return 3

    pid_path = harness_dir / "daemon.pid"
    log_path = harness_dir / "daemon.log"

    daemonize(pid_path, log_path)  # does not return in parent/first-child

    _configure_logging(log_path)
    log = logging.getLogger("super_harness.daemon")
    log.info(
        "super-harness observer host starting",
        extra={"workspace": str(workspace), "pid": os.getpid()},
    )

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    try:
        run_observer_host(workspace, stop)
    except Exception:
        log.exception("observer host crashed")
        return 1

    log.info("super-harness observer host stopped cleanly")
    return 0
```

- [ ] **Step 2: Update `daemon/__init__.py` docstring**

```python
"""super-harness daemon package.

Hosts the click-less PreToolUse hook entry-point (`hook_entry` — the in-process
decision plane) and the optional framework-observer host (`server` +
`framework_observer` — the observation plane). Post-2026-07-03 there is no UDS
server or RPC protocol; the two planes meet only through events.jsonl/state.yaml.
"""
from __future__ import annotations
```

- [ ] **Step 3: Clean `tests/integration/daemon/conftest.py` in one pass**

Read the file. Remove **both**:
- the autouse fixture that widens `SUPER_HARNESS_HOOK_QUERY_TIMEOUT` (the hot path no longer queries anything), and
- every helper coupled to the deleted UDS server: the module-level `from super_harness.daemon.server import DaemonServer` (and any `_uds_path`/`protocol`/`resolve_socket_path` imports), and the `start_server` / `kill_daemon` / socket-path helpers.

Keep any generic fixtures the surviving tests still use (e.g. a `tmp_path`-based workspace). After editing, grep to confirm nothing references the removed names:
```bash
grep -n "DaemonServer\|resolve_socket_path\|_uds_path\|protocol\|SUPER_HARNESS_HOOK_QUERY_TIMEOUT\|start_server\|kill_daemon" tests/integration/daemon/conftest.py
```
Expected: no matches.

- [ ] **Step 4: Rewrite the integration `test_framework_observer.py` against `run_observer_host`**

The current file imports `DaemonServer` (line ~39), constructs it (~180), and regression-locks the watcher fail-safe **inside `serve_forever`**. That invariant moves to `run_observer_host`. Rewrite the host-level tests to drive `run_observer_host` directly (the watchdog-Observer behavior tests that don't touch `DaemonServer` stay). Add the fail-safe-idle test that was previously implicit:

```python
# tests/integration/daemon/test_framework_observer.py  (host-level section — rewrite)
import threading
from pathlib import Path

import pytest

from super_harness.daemon import server as observer_server
from super_harness.daemon.framework_observer import FrameworkObserverManager


def test_run_observer_host_idles_when_manager_build_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt/raising adapter setup must NOT crash the host — run_observer_host
    logs + idles, then returns cleanly when signalled (Axiom 3 fail-safe, formerly
    locked inside DaemonServer.serve_forever)."""
    (tmp_path / ".harness").mkdir()

    def boom(_root: Path):
        raise RuntimeError("corrupt adapters.yaml")

    monkeypatch.setattr(observer_server, "build_manager_failsafe", boom)
    stop = threading.Event()
    stop.set()  # return immediately after the guarded start
    observer_server.run_observer_host(tmp_path, stop)  # must not raise


def test_run_observer_host_starts_and_stops_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path: run_observer_host starts the manager and stops it on signal."""
    (tmp_path / ".harness").mkdir()
    calls: list[str] = []

    class _FakeManager:
        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    monkeypatch.setattr(
        observer_server, "build_manager_failsafe", lambda _root: _FakeManager()
    )
    stop = threading.Event()
    stop.set()
    observer_server.run_observer_host(tmp_path, stop)
    assert calls == ["start", "stop"]
```

Keep the existing watchdog-driven tests (the ones that exercise `FrameworkObserverManager.start/stop` and emit-on-fs-event) — they never referenced `DaemonServer`. Delete only the `DaemonServer`-coupled ones.

- [ ] **Step 5: Trim `test_daemonize.py`**

Remove the module-level `from super_harness.daemon._uds_path import resolve_socket_path` and `from super_harness.daemon.protocol import (...)` (both deleted). Delete the socket-liveness tests (any that bind/ping the UDS socket to detect readiness). Keep:
- `test_no_super_harness_directory_exits_3` (exit 3 on missing `.harness/`),
- `test_pre_fork_threading_invariant` (source-inspects `daemonize` for the `threading.active_count()` assert),
- any pure double-fork / pidfile-flock test.
For a "the binary comes up" test, use `supervisor.is_running` (flock) polling instead of socket appearance, or defer that coverage to `test_observer_host.py` and delete the socket-based version here.

- [ ] **Step 6: Delete the UDS-only integration tests**

```bash
git rm tests/integration/daemon/test_server.py \
       tests/integration/daemon/test_concurrent_spawn.py \
       tests/integration/daemon/test_latency.py \
       tests/integration/daemon/test_readonly_invariant.py
```
(Rationale for the commit body: `test_server`/`test_concurrent_spawn` test the deleted UDS accept loop + socket race — `test_concurrent_spawn`'s single-instance invariant is re-covered by `test_observer_host::test_concurrent_starts_yield_one_live_host`; `test_latency` measured the retired daemon round-trip; `test_readonly_invariant` asserted the daemon never write-opened state/events while serving gate queries — the daemon no longer serves gate queries and the decision plane's read-only-ness is asserted in `test_state_snapshot::test_loader_never_write_opens_state`. The observer's job IS to write events.)

- [ ] **Step 7: Commit (still a coupled cut — no full-suite green yet)**

```bash
git add src/super_harness/daemon/server.py src/super_harness/daemon/__init__.py \
        tests/integration/daemon/conftest.py \
        tests/integration/daemon/test_framework_observer.py \
        tests/integration/daemon/test_daemonize.py
git commit -m "refactor(observer): strip server.py to observer host; rewrite coupled tests; retire UDS/gate dispatch"
```

### Task 6: Rename the `daemon` CLI group to `observe`

**Files:**
- Create: `src/super_harness/cli/observe.py`
- Delete: `src/super_harness/cli/daemon.py`
- Modify: `src/super_harness/cli/__init__.py`
- Create: `tests/unit/cli/test_observe.py`; Delete: `tests/unit/cli/test_daemon.py`

- [ ] **Step 1: Write `cli/observe.py`**

```python
# src/super_harness/cli/observe.py
"""`observe` CLI subgroup — start / stop / status the optional framework-observer host.

Renamed from `daemon` (design 2026-07-03): the resident process is now purely an
observer (the gate decides in-process), so its command surface names the job it
actually does. Liveness is a pidfile-flock probe (`supervisor.is_running`); no
socket, no protocol, no ping. Vibe-coder journeys never need these commands.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
from super_harness.daemon import supervisor
from super_harness.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK


@click.group("observe")
def observe_group() -> None:
    """Operate the optional framework-observer host (start / stop / status)."""


def _resolve_root(ctx: click.Context, subcommand: str) -> Path:
    workspace = ctx.obj.get("workspace") if ctx.obj else None
    try:
        return find_harness_root(Path(workspace or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand=f"observe {subcommand}", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)


@observe_group.command("start")
@click.pass_context
def start(ctx: click.Context) -> None:
    """Start the observer host (idempotent; blocks until live)."""
    root = _resolve_root(ctx, "start")
    # Flock-liveness wait budget. Production default 5s; under heavy CI contention
    # a host's spawn→double-fork→flock can exceed 5s, so the harness may widen it.
    # Production should never set this — 5s is the contract.
    wait_seconds = float(os.environ.get("SUPER_HARNESS_OBSERVE_START_TIMEOUT", "5.0"))
    try:
        pid = supervisor.ensure_running(root, wait_seconds=wait_seconds)
    except RuntimeError as e:
        click.echo(
            format_error(
                subcommand="observe start", message=str(e),
                hint="check super-harness-daemon is installed alongside super-harness and `.harness/` is writable",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    if ctx.obj.get("json"):
        click.echo(json_envelope(command="observe start", status="pass", exit_code=EXIT_OK, data={"pid": pid}))
    else:
        click.echo(f"observer running (pid {pid})")
    sys.exit(EXIT_OK)


@observe_group.command("stop")
@click.pass_context
def stop(ctx: click.Context) -> None:
    """SIGTERM the observer host; wait up to 2s for it to exit."""
    root = _resolve_root(ctx, "stop")
    if not supervisor.is_running(root):
        click.echo("not running", err=True)
        sys.exit(EXIT_GENERIC)
    if supervisor.stop(root):
        click.echo("stopped")
        sys.exit(EXIT_OK)
    click.echo(
        format_error(
            subcommand="observe stop", message="observer did not shut down within 2s",
            hint="send SIGKILL to the pid in .harness/daemon.pid if it remains unresponsive",
        ),
        err=True,
    )
    sys.exit(EXIT_GENERIC)


@observe_group.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Report observer host state: running / not running."""
    root = _resolve_root(ctx, "status")
    running = supervisor.is_running(root)
    pid = supervisor._read_pid(root) if running else 0
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="observe status",
                status="pass" if running else "fail",
                exit_code=EXIT_OK if running else EXIT_GENERIC,
                data={"running": running, "pid": pid},
            )
        )
    elif running:
        click.echo(f"running (pid {pid})")
    else:
        click.echo("not running", err=True)
    sys.exit(EXIT_OK if running else EXIT_GENERIC)
```

- [ ] **Step 2: Delete `cli/daemon.py`, rewire registration**

```bash
git rm src/super_harness/cli/daemon.py
```
In `cli/__init__.py`: replace `from super_harness.cli.daemon import daemon_group` → `from super_harness.cli.observe import observe_group`, and `main.add_command(daemon_group)` → `main.add_command(observe_group)`.

- [ ] **Step 3: Write `tests/unit/cli/test_observe.py`, delete `test_daemon.py`**

```python
# tests/unit/cli/test_observe.py
"""Unit tests for the `observe` command group (renamed from `daemon`)."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.daemon import supervisor


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def test_status_not_running(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: False)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "status"])
    assert res.exit_code != 0
    assert "not running" in res.output


def test_status_running_json(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: True)
    monkeypatch.setattr(supervisor, "_read_pid", lambda root: 4242)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "--json", "observe", "status"])
    assert res.exit_code == 0
    assert '"running": true' in res.output
    assert "4242" in res.output


def test_start_reports_pid(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "ensure_running", lambda root, **k: 777)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "start"])
    assert res.exit_code == 0
    assert "777" in res.output


def test_start_surfaces_spawn_failure(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root, **k):
        raise RuntimeError("observer host binary not found")

    monkeypatch.setattr(supervisor, "ensure_running", boom)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "start"])
    assert res.exit_code != 0
    assert "not found" in res.output


def test_stop_when_not_running(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: False)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "stop"])
    assert res.exit_code != 0
    assert "not running" in res.output


def test_stop_success(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "is_running", lambda root: True)
    monkeypatch.setattr(supervisor, "stop", lambda root, **k: True)
    res = CliRunner().invoke(main, ["--workspace", str(ws), "observe", "stop"])
    assert res.exit_code == 0
    assert "stopped" in res.output
```

```bash
git rm tests/unit/cli/test_daemon.py
```

- [ ] **Step 4: Commit**

```bash
git add src/super_harness/cli/observe.py src/super_harness/cli/__init__.py tests/unit/cli/test_observe.py
git commit -m "refactor(cli): rename daemon command group to observe"
```

### Task 7: Rewrite the hook integration test as in-process

**Files:**
- Modify: `tests/integration/daemon/test_hook_entry.py` (full rewrite)

- [ ] **Step 1: Replace the file in full**

```python
# tests/integration/daemon/test_hook_entry.py
"""Integration tests for the `super-harness-hook` binary — in-process decision
(design 2026-07-03). Drives the installed console-script as a real subprocess;
asserts exit codes and the F8 suggestion in the block message. No daemon.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _init(root: Path, change_id: str | None = None, state: str | None = None) -> None:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    if change_id and state:
        (harness / "state.yaml").write_text(
            "changes:\n"
            f"  {change_id}:\n    change_id: {change_id}\n"
            f"    current_state: {state}\n    last_event_at: '2026-07-02T00:00:00Z'\n",
            encoding="utf-8",
        )


def _run(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["super-harness-hook", *args], cwd=str(root), input=stdin,
        capture_output=True, text=True,
    )


def test_positional_block_carries_suggestion(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "INTENT_DECLARED")
    res = _run(tmp_path, "Edit", "f.py")
    assert res.returncode == 1
    assert "BLOCK (INTENT_DECLARED" in res.stderr
    assert "Draft a plan" in res.stderr


def test_positional_allow(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "IMPLEMENTATION_IN_PROGRESS")
    assert _run(tmp_path, "Edit", "f.py").returncode == 0


def test_no_harness_allows(tmp_path: Path) -> None:
    assert _run(tmp_path, "Edit", "f.py").returncode == 0


def test_claude_code_shim_blocks_exit_2(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "READY_TO_MERGE")
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "f.py"}})
    res = _run(tmp_path, "--agent", "claude-code", stdin=payload)
    assert res.returncode == 2
    assert "Open/merge the PR" in res.stderr


def test_codex_shim_deny_json(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "AWAITING_CODE_REVIEW")
    payload = json.dumps({"tool_name": "Shell", "tool_input": {"command": "echo hi"}})
    res = _run(tmp_path, "--agent", "codex", stdin=payload)
    assert res.returncode == 0
    out = json.loads(res.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "review" in out["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_kill_switch_allows(tmp_path: Path) -> None:
    _init(tmp_path, "c1", "INTENT_DECLARED")
    (tmp_path / ".harness" / "gate-disabled").touch()
    assert _run(tmp_path, "Edit", "f.py").returncode == 0
```

- [ ] **Step 2: Commit**

```bash
git add tests/integration/daemon/test_hook_entry.py
git commit -m "test: rewrite hook integration tests as in-process (no daemon)"
```

### Task 8: Rewrite the daemon-coupled E2E tests

**Files:**
- Modify: `tests/e2e/test_pre_tool_use_claude_code.py`, `tests/e2e/openspec_claude_code/test_full_lifecycle.py`, `tests/e2e/conftest.py`

- [ ] **Step 1: Rewrite `test_pre_tool_use_claude_code.py` in-process**

The current test does `super-harness init` → `adapter install claude-code` → **`daemon start`** → invoke the registered hook asserting block/allow → **`daemon stop`**, and its docstring says the hook "fail-opens to ALLOW when the daemon is down, so the daemon MUST be up." Post-change the gate decides in-process, so the daemon steps and the fail-open caveat are **gone** — the test gets simpler and stronger (deterministic block with no host). Replace the daemon-coupled body:

- Delete step 3 (`daemon start`) and the `finally: daemon stop`.
- Delete the fail-open language in the docstring; replace with "the gate decides in-process, so no host is needed for a deterministic BLOCK."
- Keep: `init`, `adapter install claude-code`, `_registered_command`, `set_state`, and the block-then-allow assertions invoking the exact registered command with the JSON payload (they now exercise the in-process path directly).

Result (the try/finally collapses to a straight sequence):
```python
    # Blocking state → exit 2 (Claude Code BLOCK) — decided in-process, no host.
    set_state("INTENT_DECLARED")
    assert _wait_for_returncode(run_hook, 2) == 2, "expected BLOCK (exit 2) in INTENT_DECLARED"

    # Advance to an allowing state → exit 0 (ALLOW).
    set_state("PLAN_APPROVED")
    assert _wait_for_returncode(run_hook, 0) == 0, "expected ALLOW (exit 0) in PLAN_APPROVED"
```

- [ ] **Step 2: Delete Phase B (`daemon start`) from `test_full_lifecycle.py`**

In `tests/e2e/openspec_claude_code/test_full_lifecycle.py`, the "Phase B" step runs `super-harness ... daemon start` (~line 75). The observer host is irrelevant to this test (its hook assertions drive the in-process path). **Delete** that step entirely (do not rename it to `observe start` — that would spawn a stray host with no teardown). Also drop any stale comment referencing the "HotState mtime-reload race" (no longer exists in-process).

- [ ] **Step 3: Strip `tests/e2e/conftest.py`**

Remove:
- the autouse `SUPER_HARNESS_HOOK_QUERY_TIMEOUT` widening fixture,
- the `SUPER_HARNESS_DAEMON_START_TIMEOUT` widening,
- the best-effort `super-harness ... daemon stop` teardown and the direct-SIGTERM `daemon.pid` fallback (nothing starts an observer in E2E now, so there is nothing to stop).

Grep to confirm no E2E test still invokes a `daemon` command:
```bash
grep -rn "daemon\|SUPER_HARNESS_HOOK_QUERY_TIMEOUT\|SUPER_HARNESS_DAEMON_START_TIMEOUT" tests/e2e/
```
Expected: no matches (or only comments you then remove).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_pre_tool_use_claude_code.py tests/e2e/openspec_claude_code/test_full_lifecycle.py tests/e2e/conftest.py
git commit -m "test(e2e): assert in-process gate decision; drop daemon start/stop scaffolding"
```

### Task 9: Delete the now-orphaned RPC layer (gated on zero importers)

**Files:**
- Modify (stale-prose sweep): `src/super_harness/core/active_change.py`, `src/super_harness/daemon/framework_observer.py`
- Delete: `src/super_harness/daemon/client.py`, `protocol.py`, `hot_state.py`, `_uds_path.py`
- Delete: `tests/unit/daemon/test_client.py`, `test_protocol.py`, `test_hot_state.py`

- [ ] **Step 1: Sweep stale prose that names the deleted concepts (best, not minimal)**

Some surviving files mention `HotState`/`DaemonServer` only in comments/docstrings that will read as lies once the modules are gone. Update them (they are NOT importers — no code change, just honest prose):
- `src/super_harness/core/active_change.py` — the `HotState`/`hot_state` mentions (comments ~lines 62, 76: "NOT via HotState — that's daemon-side"). Reword to reference the in-process snapshot seam instead.
- `src/super_harness/daemon/framework_observer.py` — the `HotState` mentions (~lines 15, 94) and `DaemonServer` (~line 193). Reword to "the observer host" / "the decision plane's next `state.yaml` read".

Do this BEFORE the deletion gate so the gate (below) is not polluted by these benign hits.

- [ ] **Step 2: Prove zero *importers* remain (the gate for deletion — import-specific, not bare symbols)**

Grep for actual import statements of the doomed modules (a bare-symbol grep false-positives on the `gates/decisions.py` docstring — edited later in Task 10 — and on any remaining prose):
```bash
grep -rnE "(from|import)[[:space:]]+super_harness\.daemon\.(client|protocol|hot_state|_uds_path)" src/ tests/
grep -rn "from super_harness.daemon.server import" src/ tests/ | grep -v "run_observer_host\|daemonize\|main"
```
Expected: **both empty** — no module imports `client`/`protocol`/`hot_state`/`_uds_path`, and nothing imports the deleted `DaemonServer` from `server` (only `daemonize`/`main`/`run_observer_host` survive there). Any hit is a real missed importer — fix before deleting.

- [ ] **Step 3: Delete**

```bash
git rm src/super_harness/daemon/client.py \
       src/super_harness/daemon/protocol.py \
       src/super_harness/daemon/hot_state.py \
       src/super_harness/daemon/_uds_path.py \
       tests/unit/daemon/test_client.py \
       tests/unit/daemon/test_protocol.py \
       tests/unit/daemon/test_hot_state.py
```

- [ ] **Step 4: Phase-3 green gate — run the full daemon + cli + e2e-unit surface**

Run: `.venv/bin/python -m pytest tests/unit/daemon tests/integration/daemon tests/unit/cli/test_observe.py tests/unit/cli/test_gate_check.py -q`
Expected: PASS. Then the whole suite:
Run: `.venv/bin/python -m pytest -q`
Expected: PASS (this is the Phase-3 green boundary). Fix any dangling reference before committing.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete orphaned UDS RPC layer (client/protocol/hot_state/_uds_path); sweep stale prose"
```

---

## Phase 4 — Taxes & regeneration

### Task 10: Re-ratify `d-single-gate-policy` (body + anchor docstring) — pothole #6 + #2

**Ordering (pothole #6):** edit the decision body and the anchor file, then `reconcile` (re-fingerprint anchor) and `ratify` (re-hash body) **adjacent** to the edits.

**Files:** `docs/decisions/d-single-gate-policy.md`, `src/super_harness/gates/decisions.py`

- [ ] **Step 1: Edit the anchor docstring** (replace `gates/decisions.py` module docstring, lines 1–12):
```python
"""Single source of truth for the 10-state pre-tool-use gate matrix.

This module is the **canonical** copy of lifecycle-event-model §3.7's "Gate
矩阵": each of the 10 states maps to an `(decision, reason)` pair. The in-process
`super_harness.gates.pre_tool_use.PreToolUseGate` — used by both the
`super-harness-hook` decision path and the `gate check` CLI — reads THIS literal
so the policy lives in exactly one place. The gate does NOT invent policy — it
only executes this table. (Cold-path gates in Phase 12/13 read their own tables;
the invariant is that no reader forks THIS one.)

Kept import-light on purpose (pure literals, no heavy imports) so that importing
the policy never drags in the CLI or observer stacks.
"""
```
Leave the `# @decision:d-single-gate-policy` sentinel and the two literals unchanged.

- [ ] **Step 2: Edit the decision body** in `docs/decisions/d-single-gate-policy.md` (everything after the frontmatter `---`):
```markdown
Gate policy lives in one literal (gates.decisions); the in-process gate reads it, neither invents nor forks policy.

```review
The gate decision policy lives in one literal (`gates.decisions`,
PRE_TOOL_USE_DECISIONS); the in-process gate (`PreToolUseGate`, shared by the
`super-harness-hook` decision path and the `gate check` CLI) reads this single
table — it does not invent, hardcode, or fork its own per-state allow/block
policy, and no future gate may fork it. On any change to the gate paths, confirm
the reader still defers to this single SSOT. Still holds -> `decision reconcile
d-single-gate-policy`; broken -> `decision betray d-single-gate-policy` with a
justification.
```
```
(Do not hand-edit the frontmatter — the CLI restamps it next.)

- [ ] **Step 3: Reconcile the anchor**
Run: `.venv/bin/super-harness --workspace . decision reconcile d-single-gate-policy --kind self --justification "Demoted daemon reader; gate policy now has a single in-process reader (PreToolUseGate, shared by hook + gate check). Invariant holds: no reader forks the table."`
Expected: `reconciled d-single-gate-policy (1 file(s), kind=self, ...)`.

- [ ] **Step 4: Re-ratify the body**
Run: `.venv/bin/super-harness --workspace . decision ratify d-single-gate-policy`
Expected: `ratified d-single-gate-policy (by ...)`.

- [ ] **Step 5: Verify clean**
Run: `.venv/bin/super-harness --workspace . decision check`
Expected: exit 0; no integrity_violation / needs-reconcile for `d-single-gate-policy`.

- [ ] **Step 6: Commit (edits + restamps together)**
```bash
git add docs/decisions/d-single-gate-policy.md src/super_harness/gates/decisions.py
git commit -m "docs(decision): re-ratify d-single-gate-policy for single in-process reader (reconcile anchor + rehash body)"
```

### Task 11: Supersede the `daemon-architecture` spec

**Files:** `private/specs/2026-05-28-daemon-architecture.md`

- [ ] **Step 1: Prepend a SUPERSEDED banner + trim the RPC sections**
```markdown
> **SUPERSEDED 2026-07-03** by `docs/plans/2026-07-03-gate-in-process-daemon-demotion-design.md`.
> The UDS gate daemon was retired: the PreToolUse gate now decides **in-process**
> (`core.state_snapshot` → `gates.pre_tool_use.PreToolUseGate`), and the resident
> process was demoted to an **optional framework-observer host** (no socket, no
> protocol; liveness via pidfile flock). The AC-2 latency budget, UC-6
> version-mismatch respawn, and the client/supervisor RPC split no longer apply.
```
Trim the body to the surviving observer-host + `daemonize` + `framework_observer` design (or leave the historical body under the banner). No live reader should mistake the RPC sections for current behavior.

- [ ] **Step 2: Commit**
```bash
git add private/specs/2026-05-28-daemon-architecture.md
git commit -m "docs(spec): supersede daemon-architecture — in-process gate + observer host"
```

### Task 12: Regenerate AGENTS.md + sweep the public/prose docs

**Files:** `AGENTS.md` (regen), `docs/getting-started.md`, `docs/cli-reference.md`, `docs/ARCHITECTURE.md`, `docs/adapters/claude-code.md`, `private/specs/2026-05-27-cli-command-surface.md`

- [ ] **Step 1: Sweep prose docs for stale daemon-hot-path language**
```bash
grep -rn "daemon start\|daemon stop\|daemon status\|daemon" docs/getting-started.md docs/cli-reference.md docs/ARCHITECTURE.md docs/adapters/claude-code.md private/specs/2026-05-27-cli-command-surface.md
```
For each hit:
- `cli-command-surface` + `cli-reference.md`: rename the `daemon` command group to `observe` (start/stop/status); drop `protocol_version`/`daemon_version`/`uptime` from the documented `status` JSON; state the gate is in-process.
- `getting-started.md` + `ARCHITECTURE.md` + `adapters/claude-code.md`: change `super-harness daemon ...` → `super-harness observe ...`; **remove any "the daemon must be running for the gate to enforce / else it fail-opens" language** (the gate enforces in-process, always). In `ARCHITECTURE.md`, update the component picture to the two-plane model (decision plane in-process; observer optional).

- [ ] **Step 2: Regenerate AGENTS.md via the sync path (NOT `doc check --fix`)**
Run: `.venv/bin/super-harness --workspace . sync --agents-md`
Run: `.venv/bin/super-harness --workspace . sync --check`
Expected: exit 0.

- [ ] **Step 3: Verify the gitignore managed block did NOT change (verify-only tax)**
```bash
git diff --name-only -- .gitignore
```
Expected: `.gitignore` NOT in the diff (runtime filenames `daemon.pid`/`daemon.log` unchanged; `.sock` was never in the managed block). If it changed, a runtime filename drifted — stop and reconcile.

- [ ] **Step 4: Commit**
```bash
git add AGENTS.md docs/getting-started.md docs/cli-reference.md docs/ARCHITECTURE.md docs/adapters/claude-code.md private/specs/2026-05-27-cli-command-surface.md
git commit -m "docs: rename daemon→observe surface; regen AGENTS.md; gate is in-process; two-plane architecture"
```

---

## Phase 5 — Full-suite green + self-host lifecycle

### Task 13: Full suite + import-graph + lint clean

- [ ] **Step 1: No dangling references to deleted symbols**
```bash
grep -rn "daemon.protocol\|daemon.client\|daemon.hot_state\|daemon._uds_path\|DaemonServer\|gate_pre_tool_use\|_write_fallback_audit_log\|cli.daemon\|daemon_group\|SUPER_HARNESS_HOOK_QUERY_TIMEOUT\|SUPER_HARNESS_DAEMON_START_TIMEOUT" src/ tests/
```
Expected: no matches.

- [ ] **Step 2: Whole suite**
Run: `.venv/bin/python -m pytest -q`
Expected: all pass (baseline 1651 passed pre-change; expect baseline − deleted-tests + new-tests, zero failures).

- [ ] **Step 3: Import-linter**
Run: `PYTHONPATH=src .venv/bin/lint-imports --config .importlinter --no-cache`
Expected: PASS (all contracts).

- [ ] **Step 4: Ruff (catch import-order lint the sandbox may miss — F4 pothole)**
Run: `.venv/bin/ruff check src/ tests/`
Expected: clean. Sweep now-unused imports left by the RPC removal in `hook_entry.py`, `cli/gate.py`, `cli/__init__.py`, `server.py`.

- [ ] **Step 5: Commit any fixups**
```bash
git add -A && git commit -m "chore: ruff + import cleanup after RPC removal" || echo "nothing to fix"
```

### Task 14: Self-host lifecycle — scope declaration + pothole checklist

- [ ] **Step 1: Assemble the exact scope list** (every created/modified/deleted path). Src + decision/doc + tests as enumerated in "File Structure" above, **plus** the design doc and this plan:
```
docs/plans/2026-07-03-gate-in-process-daemon-demotion-design.md
docs/plans/2026-07-03-gate-in-process-daemon-demotion-plan.md
```
Cross-check the list against `git status` / `git diff --name-only main` before declaring — a missed file forces an abandon+redeclare under a new slug (pothole #3).

- [ ] **Step 2: Pothole pre-flight**
- **#2 reconcile:** only `gates/decisions.py` anchored (verified — server.py/hook_entry.py are NOT); reconciled in Task 10.
- **#6 ratified body:** `d-single-gate-policy` re-ratified adjacent to the edit (Task 10); `decision check` clean.
- **#3 no-redeclare:** declare the full list up front.
- **#4 stale-change hijack:** confirm this change is the most-recent non-terminal (`super-harness status`).
- **decision md + spec + docs ARE scope:** all in the list — do not omit.

- [ ] **Step 3: Run the lifecycle** — declare intent → `plan ready --scope <all files>` (this plan already went through **multi-round two-actor plan review** per the session process) → implement → **two-actor code review** (Claude subagent + `codex exec --sandbox read-only`) → address feedback → `attest write` → open PR → merge gate (CI attest-verify) → merge → `on-merge`.

- [ ] **Step 4: Final green gate**
Run: `.venv/bin/python -m pytest -q && PYTHONPATH=src .venv/bin/lint-imports --config .importlinter --no-cache && .venv/bin/super-harness --workspace . decision check`
Expected: all green; decision check exit 0.

---

## Self-Review (fresh eyes vs. the design doc)

**Spec coverage:** decision plane (entry→snapshot→policy) = Tasks 1–3 (snapshot + hook + **gate check CLI**); F8 = Task 2; delete RPC = Task 9; supervisor hot-half deletion + observer lifecycle = Task 4; server→observer host = Task 5; fallback audit retired (dies with supervisor's hot half); `daemon`→`observe` = Task 6; closed failure set preserved = Tasks 1+2; reconcile/ratify = Task 10; spec = Task 11; docs/AGENTS.md/gitignore = Task 12; lifecycle = Task 14.

**Placeholder scan:** every code step has complete code; deletions are exact `git rm` gated on a zero-importer grep; test rewrites give full files. No "TBD"/"similar to".

**Type consistency:** `_decide → (Literal["allow","block"], str, str|None)` threaded through all three shims; `StateSnapshot(change_id, state)` and `snapshot.state` used consistently (Tasks 1/2/3); `PreToolUseGate().decide(ProposedAction(kind="edit", file=file), snapshot.state, [])` matches the real signature; supervisor surface `ensure_running`/`is_running`/`stop`/`_read_pid`/`_observer_binary` consistent across `cli/observe.py` + `test_observer_host.py`.

**Ordering safety:** deletions (Task 9) are gated by a grep proving zero src+test importers; DaemonServer-coupled tests (conftest, integration framework_observer, daemonize) are rewritten in Task 5 (adjacent to the strip), not left dangling; Phase-3 green is asserted only at Task 9 Step 3.

**Deliberate residuals for code review:** (1) package `daemon/` keeps its name while hosting the decision plane `hook_entry` — bounded-scope call, see Verified-corrections §4; (2) `SUPER_HARNESS_OBSERVE_START_TIMEOUT` is a test-only knob on the CLI path (no hot-path knob survives).

---

## Review resolution log (two-actor plan review, multi-round)

### Round 1 (rev 1 → rev 2)

Both reviewers verified their file/line claims against the repo; none were hallucinated. Dispositions:

**Codex `codex exec --sandbox read-only` — all accepted:**
- BLOCKER: `cli/gate.py` never rewired despite the "consolidates gate check" claim → **Task 3** (new).
- BLOCKER: phase order deletes RPC modules while importers remain → **restructured** (Phase 3 coupled cut; deletion gated to Task 9).
- BLOCKER: `tests/e2e/test_pre_tool_use_claude_code.py` missed → **Task 8**.
- SHOULD: read-only test weakened → **addaudithook** (Task 1 test).
- SHOULD: `_observer_binary` bare-name fallback → **raises now** (Task 4).
- SHOULD: `is_running` only catches `BlockingIOError` → **O_RDWR probe + broad OSError→False** (Task 4).
- SHOULD: concurrent single-instance test dropped → **`test_concurrent_starts_yield_one_live_host`** (Task 4).
- SHOULD: doc tax misses `cli-reference.md`/`ARCHITECTURE.md` → **Task 12** (+ `adapters/claude-code.md`).
- NIT: "socket-wait budget" comment → **"flock-liveness wait budget"** (Task 6). NIT: snapshot never-raises → **catch Exception around the whole resolution** (Task 1).

**Claude subagent — all accepted; confirmed the tax claims + flock correctness:**
- BLOCKER B1: `conftest.py:20` `DaemonServer` import breaks collection of the whole `tests/integration/daemon/` package → conftest cleaned in **Task 5** (adjacent to the strip), not Task 3.
- BLOCKER B2: integration `test_framework_observer.py` mis-classified as unchanged; host-level fail-safe coverage would vanish → **rewritten against `run_observer_host`** with an explicit idle-not-crash test (Task 5).
- S1: `load_state_snapshot` raises on unhashable `current_state` (`pick_active_change` outside try) → **whole resolution guarded** + unhashable test (Task 1).
- S2: read-only test vacuously green on 3.11+ (`Path.read_text` bypasses `builtins.open`) → **addaudithook**, asserts a read WAS seen (Task 1).
- S3: `test_daemonize.py` imports deleted `_uds_path`/`protocol` → **import strip + survivor list** (Task 5).
- S4/N1: `test_full_lifecycle.py` Phase B + e2e conftest teardown → **delete, not rename** (Task 8).
- Confirmed no defect (kept as designed): pidfile-flock liveness is a correct cross-process probe on Linux+macOS (open mode irrelevant to flock; stale pidfile reads dead); `daemonize` single-thread assert precedes watchdog spawn; no hot-path daemon spawn survives; `PreToolUseGate` signature/behavior unchanged; all four tax claims (only `gates/decisions.py` anchored, `d-core-is-base` out of scope, no `pyproject` change, gitignore verify-only) hold.

**Round-1 outcome:** no finding rejected; the two reviews were complementary (Codex found the `gate.py` + e2e + fallback gaps; Claude found the collection-break chain + the never-raises + vacuous-test defects).

### Round 2 (rev 2 → rev 3)

Both reviewers **cleared** the round-1 fixes (Task 3 gate.py rewire correct; Phase-3 ordering covers all named importers; `addaudithook` sound + non-leaking; `stop`/`is_running` don't call `_observer_binary`; Task 8 direction correct) and confirmed the flock mechanism. Remaining findings — all accepted, all narrow correctness bugs (convergence signal):

- **BLOCKER (both actors):** `_observer_binary()` via `sys.argv[0]` breaks under `.venv/bin/python -m pytest` (argv[0] = pytest's package dir → sibling absent → `RuntimeError` before liveness; and the sibling-check test went vacuous). → **resolved via `sysconfig.get_path("scripts")`** (Claude empirically verified it works where `sys.argv[0]` and `sys.executable.resolve()` don't); test made unconditional (Task 4).
- **BLOCKER (Claude):** non-UTF-8 `state.yaml` raises `UnicodeDecodeError` (a `ValueError`, not `OSError`) → escapes the read guard → hook exits 1 = BLOCK (fail-CLOSED, Axiom-1 violation). → **`read_text(errors="replace")`** + a non-UTF-8 test (Task 1).
- **BLOCKER (Codex):** `change_id_override` path skips `pick_active_change`, so a poisoned `current_state` (list/dict) reaches `ChangeState` and raises later inside `PreToolUseGate.decide`'s dict lookup. → **`isinstance(current_state, str)` guard**; test parametrized over `override ∈ {None,"a"}` + `int`, asserts `decide()` doesn't raise (Task 1).
- **SHOULD (Claude):** `is_running` `O_RDWR` adds EROFS/EACCES misreports vs mode-independent flock → **`O_RDONLY`** (Task 4).
- **SHOULD (both):** Task 9 grep-gate over-matches benign prose (`DaemonServer` in `decisions.py` docstring; `HotState` comments) → **import-specific grep** + a **stale-prose sweep** of `active_change.py`/`framework_observer.py` before the gate (Task 9).
- **SHOULD (Codex):** concurrent test didn't prove "exactly one" → **threaded `ensure_running` asserting identical PIDs** (Task 4).
- **NIT (Claude):** pid-read race (flock held before pid written) → `ensure_running` polls until `_read_pid > 0` (Task 4); `addaudithook` active-flag (Task 1); `gate_check` docstring "talks to the daemon" swept (Task 3); <2s stop SLA preserved via a 2s stop assertion (Task 4).

**Round-2 outcome:** the two reviews **converged** (both independently flagged the `_observer_binary`/pytest BLOCKER); all findings are localized correctness fixes, no structural change.

### Round 3 (rev 3 → rev 3-final) — CONVERGED

Both reviewers **verified all seven round-2 fixes correct** (Codex + Claude each empirically confirmed `sysconfig.get_path("scripts") == .venv/bin`; both confirmed the non-UTF-8, str-guard, `O_RDONLY`, concurrent-test, and addaudithook-closure fixes) and **independently found the SAME single BLOCKER** — nothing else:

- **BLOCKER (both actors, identical):** `load_state_snapshot`'s `except Exception` returned `change_id=change_id_override` instead of the resolved `cid`, so `test_record_with_unknown_field_is_permissive` (expects `change_id=="a"` when `ChangeState(**record)` rejects an unknown field on the recency path) would fail. → **fixed**: `cid` hoisted above the `try`; the fallback returns `StateSnapshot(change_id=cid, state=None)`. Verified: recency-path construction failure now preserves `cid="a"`; the unhashable-recency path leaves `cid=None` (pick raises during assignment); every `malformed_current_state` case stays green.
- **NIT (Claude):** `_read_pid` caught redundant `FileNotFoundError ⊂ OSError` → trimmed.

**Round-3 outcome — CONVERGED.** Two independent actors gave a clean bill except one shared one-line bug, now fixed; both explicitly concluded "after that fix, no remaining blocker." The finding trajectory across rounds — structural (R1) → localized correctness (R2) → a single shared one-liner (R3) — is the convergence signature. No further plan-review round is warranted; residuals (if any) are best caught by the two-actor **code** review against real code during the lifecycle.

## Execution Handoff

This plan is **converged** after three rounds of two-actor plan review. Per the session directive and the writing-plans handoff, do **not** silently auto-start execution — the self-host lifecycle (Task 14) is a large, multi-hour undertaking (14 tasks TDD'd + two-actor code review + PR + merge gate). On the user's go, execute via `superpowers:subagent-driven-development` under the full lifecycle, with two-actor **code** review (Claude subagent + `codex exec --sandbox read-only`) before merge.
