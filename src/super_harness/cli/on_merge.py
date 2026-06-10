"""`on-merge` command — CI-side `merged` emitter.

Per cli-command-surface §`on-merge` + Phase 13 plan Task 13.6 (reconcile #1/#2).

Flow:

1. `find_harness_root` → exit 3 if no `.harness/`.
2. Resolve `change_id`: explicit `--change <slug>` (CI passes
   ``${{ github.head_ref }}`` = branch = slug, VISION convention) → fallback
   parse of the merge-commit subject (`Merge pull request #N from owner/branch`)
   → unresolved → exit 1 (actionable). NOT ``read_active_change_id`` (meaningless
   in CI; OI-1 branch-inference deferral is scoped to local commands only).
3. Emit `merged{change_id, merge_commit_sha}` via the strict EventWriter +
   `refresh_state_after_emit(root)`. ``merged`` transitions the change directly
   to ARCHIVED (the L1 write-back step has been retired — there is no post-merge
   sensor dispatch).
4. Output the **frozen** `data` schema per cli-surface §on-merge data:
   `commit_sha` / `change_id` / `events_emitted: ["merged"]`.

Exit codes (cli-command-surface §`on-merge`):
- 0 — happy path.
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
from super_harness.cli.output import json_envelope
from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.slug import SlugError, validate_slug
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter
from super_harness.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
)

# Subject pattern for the GitHub merge-commit message fallback. The capture is
# greedy-everything-up-to-trailing-whitespace (NOT split on `/`) so an `/`-
# containing branch name like `feature/foo` is captured intact rather than
# silently truncated. Whether the captured value is a VALID slug (kebab-case,
# no `/`) is enforced separately downstream by `validate_slug` — capturing-then-
# rejecting yields a clean actionable error rather than a silent slug munge.
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


def _emit_merged(writer: EventWriter, change_id: str, commit_sha: str) -> None:
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
    """Emit a ``merged`` event (transitions the change to ARCHIVED)."""
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

    # Validate that the resolved slug obeys core/slug.py's kebab-case contract.
    # Both legs (--change and merge-commit-message fallback) can produce values
    # the rest of the system rejects — e.g. a CI passing `feature/foo` as
    # head_ref. Reject early with an actionable message rather than letting an
    # invalid slug pollute the `merged` event's `change_id` or downstream state.
    try:
        validate_slug(change_id)
    except SlugError as e:
        click.echo(
            format_error(
                subcommand="on-merge",
                message=f"invalid change_id `{change_id}`: {e}",
                hint=(
                    "Slugs must be kebab-case (a-z, 0-9, hyphens). A branch "
                    "like `feature/foo` is NOT a valid slug — rename the "
                    "branch or pass a normalized --change."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    writer = EventWriter(events_path(root))
    try:
        _emit_merged(writer, change_id, commit)
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

    # Frozen output `data` per cli-command-surface §on-merge data.
    # `events_emitted` is the on-merge-command's own emit (`merged`).
    data: dict[str, Any] = {
        "commit_sha": commit,
        "change_id": change_id,
        "events_emitted": ["merged"],
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
        click.echo(f"on-merge: emitted merged for {change_id}")

    sys.exit(EXIT_OK)
