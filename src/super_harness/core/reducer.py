"""Full-rebuild reducer per lifecycle-event-model §3.8.

Replays events.jsonl from line 1 to derive `dict[change_id, ChangeState]`.
v0.1 uses full rebuild every time (no snapshots / no incremental). v0.2 may
revisit if append-only replay cost becomes a bottleneck.

Validation policy (spec §3.8.1 layered):
- Emit-time (writer / emit_validation): strict — reject before append.
- Reducer-time (this module): TOLERANT — log.warning + skip; never raise.
  Events on disk already passed emit-time validation OR were appended by older
  tool versions / direct edits / partial writes — the reducer must not crash.

Invariants enforced (§3.8.5):
1. Idempotent: derive(events) == derive(events) for same input
2. Rebuildable: derive output round-trips through state.yaml (Task 1.7 wires this)
3. Prefix consistency: derive(events[:N]) == state-at-N
4. Tolerant of a truncated last line: a partial final write leaves incomplete
   JSON, which is skipped via the malformed-JSON path (crash recovery)
5. event_counts excludes unknown event types
"""
from __future__ import annotations

import logging
from pathlib import Path

from super_harness.core.events import (
    KNOWN_EVENT_TYPES,
    EventSchemaError,
    parse_event_line,
)
from super_harness.core.parse_ts import parse_ts
from super_harness.core.state import ChangeState
from super_harness.core.transitions import INVALID, compute_target_state

log = logging.getLogger("super_harness.reducer")

# Per spec §3.8.3: append order is causal truth; timestamps are audit-only.
# Severe clock drift (>60s out of order) gets a warning, but we do NOT reorder.
CLOCK_DRIFT_WARN_THRESHOLD_S = 60


# @decision:d-state-pure-fold
def derive_state(events_file: Path) -> dict[str, ChangeState]:
    """Full-rebuild reducer: replay events.jsonl → per-change state map.

    Returns an empty dict if `events_file` does not exist (first-run case).

    Tolerance rules:
    - Malformed JSON / missing required fields → log.warning + skip line
    - Truncated last line (partial write) → surfaces as malformed JSON → skip.
      (`splitlines()` cannot see a missing trailing newline on an otherwise-
      complete JSON line, and the writer emits `line + "\n"` in one atomic write,
      so the only observable torn write is incomplete JSON — there is no separate
      newline check.)
    - Unknown event types → log.warning + skip (not counted in event_counts)
    - Illegal transitions (e.g. plan_ready before intent_declared) → log.warning,
      preserve current_state but still update last_event_* fields (audit trail).

    Append order = causal order. Timestamps are NOT used to reorder events;
    they're only consulted to emit a clock-drift warning when a later event
    has an earlier timestamp than the previous one for the same change_id.
    """
    state: dict[str, ChangeState] = {}
    last_ts: dict[str, str] = {}
    if not events_file.exists():
        return state

    raw_lines = events_file.read_text(encoding="utf-8").splitlines()
    for line_num, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        try:
            ev = parse_event_line(line)
        except EventSchemaError as e:
            log.warning("line %d: malformed event (%s); skipping", line_num, e)
            continue

        cs = state.setdefault(ev.change_id, ChangeState(change_id=ev.change_id))

        # clock drift detection (do not reorder; §3.8.3 — append order is causal truth).
        # Parse both timestamps via the shared core.parse_ts primitive to avoid
        # string-lex misfires across mixed ISO 8601 forms (Z vs +00:00, second vs
        # microsecond precision). Both must parse to compare; an unparseable side
        # means "no signal", NOT a drift warning. parse_ts normalizes to aware-UTC
        # so a mixed naive/aware pair compares without TypeError (the old inline
        # copy caught only ValueError and crashed on that comparison).
        prev_ts = last_ts.get(ev.change_id)
        if prev_ts:
            prev_dt = parse_ts(prev_ts)
            cur_dt = parse_ts(ev.timestamp)
            if prev_dt is not None and cur_dt is not None and cur_dt < prev_dt:
                drift = (prev_dt - cur_dt).total_seconds()
                if drift > CLOCK_DRIFT_WARN_THRESHOLD_S:
                    log.warning(
                        "events.jsonl line %d: timestamp drift %.1fs (append order preserved)",
                        line_num, drift,
                    )
        last_ts[ev.change_id] = ev.timestamp

        # Invariant 5: only known event types count toward event_counts.
        if ev.type in KNOWN_EVENT_TYPES:
            cs.event_counts[ev.type] = cs.event_counts.get(ev.type, 0) + 1
        else:
            log.warning("line %d: unknown event type %r; skipping", line_num, ev.type)
            continue

        # Pass current_state only if we've already seen events for this change.
        # The `if cs.last_event_id else None` gate distinguishes "brand-new change_id"
        # (None) from "active change in INTENT_DECLARED" — both look the same by
        # default value alone, but the transition table treats them differently.
        target = compute_target_state(cs.current_state if cs.last_event_id else None, ev.type)
        if target == INVALID:
            log.warning(
                "line %d: illegal transition %s --[%s]--> ? (preserving state)",
                line_num,
                cs.current_state,
                ev.type,
            )
            # Preserve current_state but still record last_event_* for audit.
            cs.last_event_id = ev.event_id
            cs.last_event_type = ev.type
            cs.last_event_at = ev.timestamp
            continue

        cs.current_state = target
        cs.last_event_id = ev.event_id
        cs.last_event_type = ev.type
        cs.last_event_at = ev.timestamp
        # Framework is a CHANGE-level attribute set at declaration. Downstream events
        # (plan_ready, intent_abandoned, sensor emissions) carry their actor's framework
        # default ("plain" for CLI / sensors with no framework context) which must NOT
        # clobber the user's original choice. intent_redeclared is the canonical channel
        # for switching frameworks mid-change.
        if ev.type in ("intent_declared", "intent_redeclared"):
            cs.framework = ev.framework

        # Payload field accumulation (subset of §3.8.4 — v0.1 scope).
        p = ev.payload or {}
        if ev.type == "intent_declared":
            cs.description = p.get("description", cs.description) or cs.description
        elif ev.type == "plan_ready":
            if "scope" in p:
                cs.scope = p["scope"]
            if "tier_hint" in p:
                cs.tier = p["tier_hint"]
            # ALWAYS replace (never merge): an empty re-submit revokes prior
            # authorization. Trusted only as a list of str — a mapping / non-str
            # must not smuggle a path into the gate carve-out.
            cs.plan_artifacts = _valid_artifacts(p.get("plan_artifacts"))
        elif ev.type == "implementation_complete":
            if "pr_url" in p:
                cs.pr_url = p["pr_url"]
        elif ev.type == "merged":
            if "merge_commit_sha" in p:
                cs.merge_commit_sha = p["merge_commit_sha"]
        elif ev.type in ("intent_redeclared", "plan_redeclared"):
            cs.redeclaration_history.append({
                "event_id": ev.event_id,
                "type": ev.type,
                "reason": p.get("reason"),
                "at": ev.timestamp,
            })
            # A redeclare rewinds to INTENT_DECLARED; the prior plan submission (and
            # its recorded artifacts) no longer authorizes anything until re-submitted.
            if ev.type == "plan_redeclared":
                cs.plan_artifacts = []

    return state


def _valid_artifacts(value: object) -> list[str]:
    """`plan_artifacts` is trusted only as a list of ``str``; anything else → ``[]``.

    Defends the gate carve-out: a mapping like ``{"src/evil.py": True}`` (whose keys
    ``list()`` would expose) or a scalar cannot smuggle a path into the allow-list.
    """
    if isinstance(value, list):
        return [x for x in value if isinstance(x, str)]
    return []
