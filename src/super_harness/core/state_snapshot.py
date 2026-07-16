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
deterministic failure set replaces the daemon's open set (reachability x
protocol version x cache freshness x PATH).
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

    # getattr fallback (not try/except) so the two loader types don't produce an
    # incompatible-assignment under mypy; CSafeLoader is absent when PyYAML was
    # built without libyaml.
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
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
        # `plan_artifacts` feeds the gate's `x in state.plan_artifacts` carve-out. A
        # hand-forged non-list value (null/str/int) would make that a TypeError /
        # substring test. Coerce it to [] WITHOUT degrading the real state to None —
        # degrading would ALLOW edits in e.g. PLAN_REJECTED (fail-open to source);
        # [] just means "no artifacts recorded" → the carve-out doesn't fire → BLOCK.
        if not isinstance(record.get("plan_artifacts", []), list):
            record = {**record, "plan_artifacts": []}
        return StateSnapshot(change_id=cid, state=ChangeState(**record))
    except Exception:
        return StateSnapshot(change_id=cid, state=None)
