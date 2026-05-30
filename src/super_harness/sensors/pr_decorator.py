"""PRDecorator sensor — injects metadata block into PR descriptions.

Phase 13 Task 13.2 (engineering-integration spec §3.3).

Triggers on ``pr_opened`` event, fetches the current PR body, builds a
super-harness metadata block via ``build_metadata``, merges it idempotently,
and writes the result back via ``edit_pr_body``.

Merge strategy (§3.3 reconcile note):
- 0 existing blocks → append.
- 1 balanced block  → replace in-place (re.sub count=1, DOTALL).
- Malformed / unbalanced markers present → raise PRDecoratorError (fail-loud;
  never best-effort splice ambiguous marker state — Phase 9/12 lesson).
- 2+ balanced blocks → raise PRDecoratorError (AC-3).

All I/O is confined to ``check()``; ``_merge_metadata_block`` is pure.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import re
from typing import ClassVar

from super_harness.core.events import Event
from super_harness.engineering.gh import edit_pr_body, view_pr
from super_harness.engineering.pr_metadata import (
    METADATA_BEGIN,
    METADATA_END,
    build_metadata,
    parse_metadata_block,
)
from super_harness.sensors import (
    Activity,
    ActivityType,
    Determinism,
    Sensor,
    SensorResult,
    WorkspaceContext,
)


class PRDecoratorError(RuntimeError):
    """Raised when the PR body's marker state is ambiguous or violates AC-3.

    Callers (dispatcher) surface this as ``sensor_crashed`` — the sensor
    intentionally refuses to splice rather than risk eating user content.
    """


def _merge_metadata_block(body: str, block: str) -> str:
    """Return *body* with *block* injected or replacing the existing metadata.

    Pure function — no I/O, no side effects.

    Raises
    ------
    PRDecoratorError
        When block_count >= 2 (AC-3 violation) or when markers are present but
        unbalanced/malformed (present=False yet a marker string exists in body).
        Never best-effort splices ambiguous marker state.
    """
    result = parse_metadata_block(body)

    if result.block_count >= 2:
        raise PRDecoratorError(
            f"PR-decorator: PR body contains {result.block_count} super-harness "
            f"metadata blocks; manual cleanup required (only 1 expected)"
        )

    # Detect malformed/unbalanced: parse says not-present but markers exist.
    # This covers: dangling END, unclosed BEGIN, nested BEGIN — all anomalies
    # where parse_metadata_block sets present=False despite a marker being found.
    if not result.present and (METADATA_BEGIN in body or METADATA_END in body):
        raise PRDecoratorError(
            "PR-decorator: PR body contains unbalanced or malformed super-harness "
            "marker(s); manual cleanup required before auto-injection"
        )

    if result.block_count == 1 and result.present:
        # Safe to replace: exactly one balanced pair verified by parse_metadata_block.
        pattern = re.escape(METADATA_BEGIN) + r".*?" + re.escape(METADATA_END)
        return re.sub(pattern, block, body, count=1, flags=re.DOTALL)

    # block_count == 0 and no stray markers → append.
    return body.rstrip() + "\n\n" + block + "\n"


class PRDecorator(Sensor):
    """Injects the super-harness metadata block into a newly opened PR's body.

    Triggers on ``pr_opened``. Reads the PR body, builds a metadata block from
    events.jsonl, merges idempotently, and writes back via gh CLI.

    See engineering-integration spec §3.3 for the reconcile algorithm.
    """

    name: ClassVar[str] = "PR-decorator"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("pr_opened",)
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ()
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger: Event | Activity, context: WorkspaceContext) -> SensorResult:
        """Fetch PR body, inject metadata block, write back.

        The base ``Sensor.check`` accepts ``Event | Activity`` for both trigger
        kinds; at runtime this sensor is only routed ``pr_opened`` events
        (``triggers_on_activities`` is empty), but the parameter type matches
        the base for Liskov correctness — mirroring sibling sensors.

        Raises
        ------
        KeyError
            If ``trigger.payload["pr_number"]`` is absent — surfaces via
            dispatcher as ``sensor_crashed``.
        PRDecoratorError
            If the PR body's marker state is ambiguous or violates AC-3, or
            no change_id can be resolved from trigger or context.
        """
        pr_number: int = trigger.payload["pr_number"]
        change_id = getattr(trigger, "change_id", None) or context.active_change_id
        if change_id is None:
            raise PRDecoratorError(
                "PR-decorator: cannot resolve change_id from trigger or context"
            )

        body = view_pr(pr_number, fields=["body"]).get("body") or ""
        new_block = build_metadata(change_id, context.workspace_root)
        merged = _merge_metadata_block(body, new_block)
        edit_pr_body(pr_number, merged)

        return SensorResult(
            status="pass",
            summary=f"PR #{pr_number} metadata injected",
            emit_events=[],
        )
