"""`on-merge` command — CI-side `merged` emitter that fires the L1 follow-up workflow.

Per cli-command-surface §`on-merge` + Phase 13 plan Task 13.6 (reconcile #1/#2).

Flow (mirrors verify/done's one-shot dispatcher idiom):

1. `find_harness_root` → exit 3 if no `.harness/`.
2. Resolve `change_id`: explicit `--change <slug>` (CI passes
   ``${{ github.head_ref }}`` = branch = slug, VISION convention) → fallback
   parse of the merge-commit subject (`Merge pull request #N from owner/branch`)
   → unresolved → exit 1 (actionable). NOT ``read_active_change_id`` (meaningless
   in CI; OI-1 branch-inference deferral is scoped to local commands only).
3. Emit `merged{change_id, merge_commit_sha}` via the strict EventWriter +
   `refresh_state_after_emit(root)` so the dispatcher's `WorkspaceContext`
   reflects MERGED.
4. Construct a one-shot ``SensorDispatcher([L1Updater(), AnchorIndexRebuilder()], …)``
   and call ``on_event_emit(merged_event)``. The dispatcher emits the sensors'
   extension events (`l1_update_completed` / `l1_update_failed` from L1Updater;
   AnchorIndexRebuilder is silent) and refreshes state internally.
5. Output the **frozen** `data` schema per cli-surface §on-merge data:
   `commit_sha` / `change_id` / `events_emitted: ["merged"]` /
   `sensors_triggered: ["l1-updater", "anchor-index-rebuilder"]` (literally the
   fixed dispatched-sensor list — frozen by the spec) / `l1_followup_pr` (URL
   walked from L1Updater's SensorResult, else null).

This is the first **production** caller of `SensorDispatcher.on_event_emit`
(verify/done call `on_activity`).

Exit codes (cli-command-surface §`on-merge`):
- 0 — happy path. INCLUDING the l1-updater-failed-but-handled branch: the merge
  already happened, the failure landed as `l1_update_failed` + a
  `.harness/pending-l1-updates/<slug>.md` operator note, and §3.4 declares this
  MUST NOT interrupt the main flow. `l1_followup_pr` is null in that envelope.
- 1 — change_id resolution failed (neither `--change` nor a parseable merge-commit
  subject). NO `--json` envelope (matches verify's HarnessNotInitialized + Phase
  12 `pr validate`'s 3/4 patterns: 0/2 emit envelope; 1/3/4 do not).
- 3 — `.harness/` missing. NO envelope.
- 5 — reserved (concurrency conflict; no v0.1 path actually exits 5).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
)
from super_harness.cli.output import json_envelope
from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter
from super_harness.sensors import WorkspaceContext
from super_harness.sensors.anchor_index_rebuilder import AnchorIndexRebuilder
from super_harness.sensors.dispatcher import (
    ONESHOT_DISPATCHER_PARALLELISM,
    ONESHOT_DISPATCHER_TIMEOUT_S,
    SensorDispatcher,
)
from super_harness.sensors.l1_updater import L1Updater

# Frozen per cli-command-surface §on-merge data: the dispatched-sensor list IS
# the field's contract, regardless of whether any single sensor was a no-op for
# this trigger (e.g. AnchorIndexRebuilder is silent).
_SENSORS_TRIGGERED: list[str] = ["l1-updater", "anchor-index-rebuilder"]

# Subject pattern for the GitHub merge-commit message fallback. The branch name
# may contain `/` (e.g. `harness/l1-update-foo` or `feature/foo-bar`) — the
# capture is greedy-everything-up-to-trailing-whitespace, NOT split on `/`.
_MERGE_COMMIT_SUBJECT_RE = re.compile(
    r"^Merge pull request #\d+ from [^/]+/(.+?)\s*$"
)


def _parse_merge_commit_branch(root: Path, sha: str) -> str | None:
    """Best-effort: extract branch name from a merge-commit subject.

    Runs ``git log -1 --format=%s <sha>`` (argv list, ``shell=False``). Returns
    the captured branch slug on a clean match; ``None`` on any failure (git
    missing / not-a-repo / SHA unknown / subject not a merge-commit pattern).

    Caller treats ``None`` as "fallback failed" and exits 1 with the actionable
    "pass --change explicitly" message — never re-raises.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%s", sha],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    m = _MERGE_COMMIT_SUBJECT_RE.match(out.stdout.strip())
    return m.group(1) if m else None


def _resolve_change_id(root: Path, commit_sha: str, change: str | None) -> str | None:
    """Strict-order resolver: explicit ``--change`` → merge-commit-subject parse.

    Returns the resolved slug or ``None`` if both legs miss. The first success
    wins; the fallback is NOT attempted when ``change`` is given (this is what
    makes ``--change`` the production path that an unparseable subject cannot
    poison).
    """
    if change:
        return change
    return _parse_merge_commit_branch(root, commit_sha)


def _emit_merged(writer: EventWriter, change_id: str, commit_sha: str) -> Event:
    """Strict-emit a ``merged`` event from the ``ci`` actor.

    Payload key is ``merge_commit_sha`` (reducer SSOT — see
    ``core/reducer.py``: ``elif ev.type == "merged": cs.merge_commit_sha = …``).
    The ``data.commit_sha`` field on the envelope is a separate, frozen output
    field per cli-surface §on-merge data.
    """
    ev = Event(
        event_id=new_event_id(),
        type="merged",
        change_id=change_id,
        timestamp=utc_now_iso(),
        actor=Actor(type="ci", identifier="on-merge"),
        framework="plain",
        payload={"merge_commit_sha": commit_sha},
    )
    writer.emit(ev)
    return ev


def _l1_followup_pr_from_results(results: list[Any]) -> str | None:
    """Walk dispatcher results for the L1Updater's PR URL.

    L1Updater's pass path emits ONE ``l1_update_completed`` event whose payload
    carries ``pr_url`` (or ``None`` on the short-circuit "no work" branch).
    AnchorIndexRebuilder is silent (no emit_events), so we identify the right
    SensorResult by looking for a result whose ``emit_events`` includes a
    ``l1_update_completed`` event. If l1-updater failed (``l1_update_failed``
    emitted instead) OR no l1-updater result is present, return None.
    """
    for r in results:
        for ev in getattr(r, "emit_events", None) or []:
            if ev.type == "l1_update_completed":
                url = ev.payload.get("pr_url")  # may itself be None
                return url if isinstance(url, str) else None
    return None


@click.command("on-merge")
@click.option(
    "--commit",
    required=True,
    help="Merge commit SHA (opaque; CI passes ${{ github.sha }}).",
)
@click.option(
    "--change",
    default=None,
    help="Slug override (CI passes ${{ github.head_ref }} = branch = slug).",
)
@click.pass_context
def on_merge_cli(ctx: click.Context, commit: str, change: str | None) -> None:
    """Emit a ``merged`` event and dispatch L1-updater + anchor-index-rebuilder."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="on-merge", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    change_id = _resolve_change_id(root, commit, change)
    if change_id is None:
        # NO --json envelope on exit 1, matching verify's HarnessNotInitialized
        # path and pr validate's exit-3/4 "couldn't run" pattern. Stderr message
        # names the SHA and tells the operator how the CI workflow should pass
        # --change explicitly.
        click.echo(
            format_error(
                subcommand="on-merge",
                message=(
                    f"could not resolve change_id from commit {commit}: "
                    "pass --change <slug> explicitly "
                    "(CI: `${{ github.head_ref }}`)"
                ),
                hint=(
                    "Add `--change ${{ github.head_ref }}` to the CI workflow "
                    "step invoking `super-harness on-merge`."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    writer = EventWriter(events_path(root))
    try:
        merged_event = _emit_merged(writer, change_id, commit)
    except EmitPreconditionError as e:
        # The change is not in READY_TO_MERGE. This is a hard data integrity
        # signal — exit 1 with a clean format_error rather than the strict
        # writer's traceback. NO --json envelope (1/3 do not emit one).
        click.echo(
            format_error(
                subcommand="on-merge",
                message=str(e),
                hint=(
                    "Inspect `.harness/events.jsonl` and `state.yaml` — the change "
                    "must be READY_TO_MERGE before a `merged` event."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    refresh_state_after_emit(root)

    ctx_ws = WorkspaceContext(
        workspace_root=root, git_branch=None, active_change_id=change_id
    )
    dispatcher = SensorDispatcher(
        [L1Updater(), AnchorIndexRebuilder()],
        writer=writer,
        context=ctx_ws,
        timeout_s=ONESHOT_DISPATCHER_TIMEOUT_S,
        max_parallelism=ONESHOT_DISPATCHER_PARALLELISM,
    )
    # First production caller of on_event_emit (verify/done use on_activity).
    # The dispatcher routes on event.type only; we reuse the emitted event
    # (with its stamped event_id / timestamp) — re-emit is NOT triggered.
    results = dispatcher.on_event_emit(merged_event)

    l1_pr = _l1_followup_pr_from_results(results)

    # Frozen output `data` per cli-command-surface §on-merge data.
    # `sensors_triggered` is the LITERAL fixed dispatched list (spec contract),
    # not a runtime-observed list — that key is the dispatcher's intent, not its
    # outcome. `events_emitted` is the on-merge-command's own emit
    # (`merged`); sensor-emitted events live in events.jsonl, not here, per the
    # spec example.
    data: dict[str, Any] = {
        "commit_sha": commit,
        "change_id": change_id,
        "events_emitted": ["merged"],
        "sensors_triggered": list(_SENSORS_TRIGGERED),
        "l1_followup_pr": l1_pr,
    }

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="on-merge",
                status="pass",
                exit_code=EXIT_OK,
                data=data,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(
            f"on-merge: emitted merged for {change_id}; "
            f"L1 follow-up: {l1_pr or 'n/a'}"
        )

    # Exit 0 even on l1-updater failure: the merge already happened
    # (engineering-integration §3.4 — l1-updater failure MUST NOT interrupt the
    # main flow). The `l1_update_failed` event in events.jsonl + the
    # `pending-l1-updates/<slug>.md` operator note are the diagnostic surface.
    sys.exit(EXIT_OK)
