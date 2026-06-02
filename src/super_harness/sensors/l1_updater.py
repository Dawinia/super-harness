# L1 anchor (HG-D self-host) — @capability:capability-l1-updater
"""L1Updater sensor — opens a follow-up PR with regenerated L1 docs on `merged`.

Phase 13 Task 13.5. Assembles the four Phase 13 helpers
(``_l1_helpers.generate_l1_stubs`` / ``git_branch_commit_push`` /
``build_l1_pr_body`` / ``pr_num_from_url``), the Phase 12 ``gh`` wrappers
(``create_pr`` / ``merge_pr_auto_squash``), and the Phase 11
``rebuild_anchor_index`` into the engineering-integration §3.4 workflow.

Triggered by ``merged``. On every merge:

1. Resolve the change's declared anchors from derived state (``plan_ready``
   payload's ``affected_anchors``).
2. Generate L1 capability stubs for them (idempotent; unchanged → skip).
3. Rebuild ``.harness/anchors/index.yaml`` so the index persists into the PR.
4. Open a follow-up branch + PR labelled ``harness-auto`` /
   ``no-human-review`` and enable auto-squash-merge.
5. Emit ``l1_update_completed`` with the PR URL + the updated files.

**AC-7 transactional boundary (§3.4 v0.1-reconcile blockquote).** Every step
from "rebuild + git" through "create_pr + auto-merge" runs inside a single
``try`` / ``except``. On failure:

- Write ``.harness/pending-l1-updates/<change_id>.md`` with the change id,
  the files we intended to update, and the exception string. (Nested-failure
  guard: if the pending write itself raises, fall back to
  ``engineering.operation_log.write_operation_log`` under ``l1-updater/``.)
- Emit ``l1_update_failed`` (``reason`` + ``pending_path``).
- Return a ``SensorResult(status="fail", ...)`` — DO NOT re-raise. A raise
  would let the dispatcher's ``_safe_run`` stamp an additional
  ``sensor_crashed`` event, double-emitting the failure signal.

Payload-key SSOT (per spec §3.4 v0.1 reconcile, edited 2026-05-30): the PR URL
field is named ``pr_url`` (NOT pseudocode's ``l1_pr_url``) — matches the
reducer's existing ``pr_url`` convention on ``implementation_complete``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.paths import anchors_index_path, events_path
from super_harness.core.reducer import derive_state
from super_harness.engineering.gh import GhError, create_pr, merge_pr_auto_squash
from super_harness.engineering.operation_log import write_operation_log
from super_harness.sensors import (
    Activity,
    ActivityType,
    Determinism,
    Sensor,
    SensorResult,
    WorkspaceContext,
)
from super_harness.sensors._l1_helpers import (
    build_l1_pr_body,
    generate_l1_stubs,
    git_branch_commit_push,
    pr_num_from_url,
)
from super_harness.sensors.anchor_index_rebuilder import rebuild_anchor_index

__all__ = ["L1Updater"]


# Canonical anchor-index path segments — used to recognise the index path in
# the files list passed to the PR body / files_updated payload.
_L1_CAP_SEGMENTS = ("docs", "reference", "capabilities")


def _display_path(path: Path, root: Path) -> str:
    """Repo-relative display path for an L1 file or the anchor index.

    Tries ``path.relative_to(root)`` first; falls back to scanning for a
    canonical segment sequence; otherwise uses the bare filename. This mirrors
    ``_l1_helpers._repo_relative_display`` but accepts a *root* hint so we can
    render ``.harness/anchors/index.yaml`` correctly without re-implementing
    its private helper.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        pass
    parts = path.parts
    for i in range(len(parts) - len(_L1_CAP_SEGMENTS) + 1):
        if parts[i : i + len(_L1_CAP_SEGMENTS)] == _L1_CAP_SEGMENTS:
            return "/".join(parts[i:])
    return path.name


def _pending_path(root: Path, change_id: str) -> Path:
    return root / ".harness" / "pending-l1-updates" / f"{change_id}.md"


def _l1_completed(
    change_id: str,
    files: list[Path],
    *,
    pr_url: str | None,
    root: Path,
) -> Event:
    """Construct an ``l1_update_completed`` event.

    Payload-key SSOT: ``pr_url`` (NOT ``l1_pr_url``), ``files_updated`` (list
    of repo-relative path strings). ``event_id`` / ``timestamp`` are blank
    placeholders; the dispatcher stamps them, and ALWAYS overwrites ``actor``.
    """
    return Event(
        event_id="",
        type="l1_update_completed",
        change_id=change_id,
        timestamp="",
        actor=Actor(type="sensor", identifier="l1-updater"),
        framework="plain",
        payload={
            "pr_url": pr_url,
            "files_updated": [_display_path(f, root) for f in files],
        },
    )


def _l1_failed(change_id: str, reason: str, pending_path: Path | str) -> Event:
    """Construct an ``l1_update_failed`` event.

    Payload always carries the ``pending_path`` as a string, even when the
    nested-failure path means the file does not actually exist on disk.
    """
    return Event(
        event_id="",
        type="l1_update_failed",
        change_id=change_id,
        timestamp="",
        actor=Actor(type="sensor", identifier="l1-updater"),
        framework="plain",
        payload={"reason": reason, "pending_path": str(pending_path)},
    )


def _write_pending(root: Path, change_id: str, files: list[Path], error: BaseException) -> Path:
    """Best-effort write of ``.harness/pending-l1-updates/<change_id>.md``.

    Returns the intended path even when the write fails (the path is part of
    the ``l1_update_failed`` payload contract).

    Nested-failure guard: if writing the pending file raises ``OSError`` /
    ``UnicodeDecodeError``, fall back to a plain operation-log via
    ``engineering.operation_log.write_operation_log`` (which itself silently
    swallows OSError on its own write — see rule-of-three factoring decision).
    """
    pending = _pending_path(root, change_id)
    body = (
        f"# L1 follow-up pending for `{change_id}`\n\n"
        f"timestamp: {utc_now_iso()}\n"
        f"error: {error}\n\n"
        "## Intended files\n\n"
        + ("\n".join(f"- `{_display_path(f, root)}`" for f in files) or "- (none)")
        + "\n"
    )
    try:
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(body)
    except (OSError, UnicodeDecodeError) as write_err:
        # Operation-log fallback: best-effort itself. write_operation_log
        # swallows its own OSError, so this nested path never raises.
        fallback_body = (
            "operation: l1-updater pending-write fallback\n"
            f"timestamp: {utc_now_iso()}\n"
            f"change_id: {change_id}\n"
            f"primary_error: {error}\n"
            f"pending_write_error: {write_err}\n"
            f"intended_pending_path: {pending}\n"
        )
        write_operation_log(root / ".harness", "l1-updater", fallback_body)
    return pending


class L1Updater(Sensor):
    """Open a follow-up PR with regenerated L1 docs after the parent change merges.

    See engineering-integration §3.4 for the full workflow contract (including
    the AC-7 transactional boundary).
    """

    name: ClassVar[str] = "l1-updater"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("merged",)
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ()
    determinism: ClassVar[Determinism] = "computational"

    def check(
        self, trigger: Event | Activity, context: WorkspaceContext
    ) -> SensorResult:
        change_id = getattr(trigger, "change_id", None) or context.active_change_id
        if not change_id:
            # Graceful skip — mirrors AnchorSentinelPresence's no-change-id path.
            # The dispatcher would never trigger us in production without a
            # change_id, but defensive code keeps unit tests + future callers
            # honest.
            return SensorResult(
                status="pass",
                summary="l1-updater: no change_id — skipped",
                emit_events=[],
            )

        # Resolve declared anchors from derived state. derive_state is the SSOT
        # for "what does the latest plan_ready say" (engineering-integration
        # §3.4 reconcile #6 + verification_runner's baseline pattern).
        states = derive_state(events_path(context.workspace_root))
        cs = states.get(change_id)
        anchors = list(cs.affected_anchors) if cs is not None else []
        if not anchors:
            # No L1 work to do — emit a completed event with empty files so
            # downstream consumers see the close-out (the merge already happened).
            return SensorResult(
                status="informational",
                summary="l1-updater: no L1 anchors to update",
                emit_events=[
                    _l1_completed(
                        change_id, [], pr_url=None, root=context.workspace_root
                    )
                ],
            )

        # --- AC-7 transactional region ---------------------------------------
        # generate_l1_stubs runs INSIDE the try so a pre-existing non-UTF-8
        # stub file (UnicodeDecodeError, a ValueError subclass) routes through
        # AC-7's pending-file path instead of bypassing it and becoming
        # sensor_crashed via the dispatcher's _safe_run.
        files: list[Path] = []
        try:
            files = generate_l1_stubs(context.workspace_root, anchors)
            if not files:
                # All stubs already up-to-date — short-circuit before any git/gh I/O.
                return SensorResult(
                    status="informational",
                    summary="l1-updater: L1 files already current",
                    emit_events=[
                        _l1_completed(
                            change_id, [], pr_url=None, root=context.workspace_root
                        )
                    ],
                )

            # Phase 11's rebuilder writes .harness/anchors/index.yaml. The index
            # is the v0.1 persistence path for anchor locations (the on-merge
            # CI runner's separate rebuilder call is ephemeral).
            rebuild_anchor_index(context.workspace_root)
            index_path = anchors_index_path(context.workspace_root)
            commit_files = [*files, index_path]

            branch = f"harness/l1-update-{change_id}"
            git_branch_commit_push(
                context.workspace_root,
                branch,
                commit_files,
                f"chore(l1): update L1 capabilities for {change_id}",
            )
            url = create_pr(
                base="main",
                head=branch,
                title=f"chore(l1): update L1 capabilities for {change_id}",
                body=build_l1_pr_body(change_id, commit_files),
                labels=["harness-auto", "no-human-review"],
            )
            merge_pr_auto_squash(pr_num_from_url(url))
            return SensorResult(
                status="pass",
                summary=f"l1-updater: follow-up PR opened {url}",
                emit_events=[
                    _l1_completed(
                        change_id,
                        commit_files,
                        pr_url=url,
                        root=context.workspace_root,
                    )
                ],
            )
        except (GhError, subprocess.CalledProcessError, OSError, ValueError) as e:
            # Catch tuple per error-family lessons (Phase 9/10/12):
            #   GhError                       — gh wrapper failures
            #   subprocess.CalledProcessError — git_branch_commit_push failure
            #   OSError                       — pending-file path I/O, fs issues
            #   ValueError                    — pr_num_from_url could not parse
            # We deliberately do NOT include Exception: the dispatcher's
            # _safe_run is the catch-all-and-emit-sensor_crashed safety net.
            pending = _write_pending(context.workspace_root, change_id, files, e)
            return SensorResult(
                status="fail",
                summary=f"l1-updater: follow-up failed: {e}",
                emit_events=[_l1_failed(change_id, str(e), pending)],
            )
