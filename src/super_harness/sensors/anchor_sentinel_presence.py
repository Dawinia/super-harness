"""AnchorSentinelPresence sensor — dual-trigger, audit-only.

Phase 11 Task 11.1 (sensor-gate-architecture §3.1 table row: anchor-sentinel-presence).

Design:
- Triggers on `implementation_complete` event or `commit` activity.
- Reads declared anchors from derived state (cs.affected_anchors).
- Scans source files for `@capability:<id>` sentinels (scan_sentinels).
- Missing anchors: commit → always warning; implementation_complete → fail iff
  anchor_must_pass_for_tier(tier) else warning.
- Empty declared / no change_id → pass.
- Never emits events (audit-only: §3.1 "warning report 不 emit 状态变化").

API stability: **experimental** (v0.1).
"""
from __future__ import annotations

from typing import ClassVar

from super_harness.core.anchor_scanner import scan_sentinels
from super_harness.core.events import Event
from super_harness.core.paths import events_path
from super_harness.core.reducer import derive_state
from super_harness.sensors import (
    Activity,
    ActivityType,
    Determinism,
    Sensor,
    SensorResult,
    SensorStatus,
    WorkspaceContext,
)
from super_harness.sensors._anchor_policy import anchor_must_pass_for_tier


class AnchorSentinelPresence(Sensor):
    """Audit-only sensor: reports missing @capability sentinels for declared anchors.

    Dual-trigger: fires on `implementation_complete` lifecycle event and on
    `commit` activity. Never emits events — purely observational.

    See sensor-gate-architecture spec §3.1 (anchor-sentinel-presence row).
    """

    name: ClassVar[str] = "anchor-sentinel-presence"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("implementation_complete",)
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ("commit",)
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger: Event | Activity, context: WorkspaceContext) -> SensorResult:
        """Check that every declared anchor has a @capability:<id> sentinel in source.

        Returns pass immediately when:
        - No change_id can be resolved from trigger or context.
        - The change has no declared anchors in derived state.

        Otherwise scans source and returns warning/fail for missing sentinels.
        No events are emitted regardless of outcome.
        """
        change_id = getattr(trigger, "change_id", None) or context.active_change_id
        if change_id is None:
            return SensorResult(
                status="pass",
                summary="anchor-sentinel-presence: no change_id — skipped",
            )

        cs = derive_state(events_path(context.workspace_root)).get(change_id)
        declared = list(cs.affected_anchors) if cs is not None else []
        if not declared:
            return SensorResult(
                status="pass",
                summary="anchor-sentinel-presence: no declared anchors — skipped",
            )

        present = scan_sentinels(context.workspace_root)
        missing = sorted(set(declared) - present)
        if not missing:
            return SensorResult(
                status="pass",
                summary="all declared anchors have @capability sentinels",
            )

        tier = cs.tier if cs is not None else None
        is_impl_complete = isinstance(trigger, Event) and trigger.type == "implementation_complete"
        status: SensorStatus = (
            "fail" if (is_impl_complete and anchor_must_pass_for_tier(tier)) else "warning"
        )
        return SensorResult(
            status=status,
            summary=f"missing @capability sentinels for declared anchors: {missing}",
            details={
                "missing": missing,
                "declared": sorted(declared),
                "found": sorted(present),
                "tier": tier,
                "trigger": getattr(trigger, "type", None),
            },
            # NO emit_events: audit-only — §3.1 "warning report 不 emit 状态变化".
        )
