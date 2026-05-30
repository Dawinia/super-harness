"""`done` command — verify a change, then mark implementation complete.

Per cli-command-surface §3.4 + lifecycle-event-model §3.4. `done` is the
operator's "I'm finished implementing" signal. It runs verification (via the
same one-shot `SensorDispatcher` + `VerificationRunner` as `verify`) and, only
if the verdict passes, emits `implementation_complete` to advance the change
from IMPLEMENTATION_IN_PROGRESS → AWAITING_CODE_REVIEW.

Emit ordering (the subtle part): the dispatcher emits `verification_passed`
DURING `on_activity`, which satisfies `implementation_complete`'s hard
prerequisite (emit_validation §_HARD_PREREQ_EVENTS). `done` then emits
`implementation_complete` through the strict writer path — the prior
`verification_passed` is what lets that emit pass precondition validation.

`--skip-verify` bypasses the dispatcher entirely: `done` emits a synthetic
`verification_passed` (payload `{skipped: true}`) FIRST, then
`implementation_complete`. Both go through the STRICT writer (never
`skip_validation`). A pre-flight gate (below) rejects any change that is not
IMPLEMENTATION_IN_PROGRESS before either path emits anything, so the strict
writer's `EmitPreconditionError` is only a defensive backstop.

Every CLI-issued `writer.emit(...)` here is followed by
`refresh_state_after_emit(root)` (B-3 wiring) so state.yaml never lags.

Slug resolution mirrors `verify`: explicit `<slug>` wins; else `--pr <num>`
fetches the PR body and parses the §2.5 metadata block's ``Change`` field
(Phase 14 Task 14.3); else ``read_active_change_id``. Neither → EXIT_VALIDATION.

When ``--pr`` is given, its raw value plays a DUAL role: it drives slug
resolution AND its string form lands on the ``implementation_complete``
payload as ``pr_url`` (unchanged from pre-14.3). Failure-mode classification
for ``--pr`` resolution mirrors `verify` and DIVERGES from ``pr validate`` —
see ``engineering/pr_metadata.py`` for the exit-code matrix. The A6
slug-format gate (``core/slug.py::validate_slug``) is applied only on the
``--pr`` path (attacker-influenceable input); positional / active-change
slugs skip A6.

Trust model for positional slug: in local use, the positional ``<slug>``
argument comes from the operator (trusted). In CI, the bundled
``super-harness.yml`` workflow's ``verification`` job passes
``${{ github.head_ref }}`` as the positional slug. ``head_ref`` is
PR-author-controlled (kebab branch name = slug per VISION convention), but
A6 is NOT applied on the positional path — git ref-name rules permit
characters like ``/`` that A6 forbids (so a ``feature/foo`` head_ref reaches
``verify`` unvalidated by A6); the workflow's defense-in-depth is the
``pr-decorate`` job, which applies A6 inside ``pr emit-opened --change`` and
fails first (exit 1) on non-kebab refs — ``verify`` is therefore never
reached with a non-conforming slug under normal CI flow. Summary:
positional / active-change inherit upstream validation (CLI args, ``change
start``, git ref-name rules); only ``--pr <num>`` (which parses an
attacker-controlled PR body) invokes A6 inside
``resolve_slug_from_pr_body_strict``.

Exit codes (cli-command-surface §2.2):
- 0 — verified (or --skip-verify) + implementation_complete emitted.
- 2 — the change is not IMPLEMENTATION_IN_PROGRESS (pre-flight state gate, before
  any verification runs or any event is written) OR verification failed (no
  implementation_complete) OR a config validation error (syntax-corrupt /
  wrong-shape / bad-placeholder verification.yaml) surfaced by the default-path
  pre-load before dispatch OR a ``--pr`` resolution failure classified as
  EXIT_VALIDATION (malformed block, ≥2 blocks, bad slug format, non-integer
  ``--pr`` value).
- 3 — `.harness/verification.yaml` missing.
- 4 — ``--pr`` resolution failure classified as EXIT_EXTERNAL_TOOL (gh fetch
  failed, no metadata block, or block missing Change field).
- 5 — concurrency conflict (reserved; not raised by v0.1 in practice).
- 1 — the sensor crashed / timed out (no verdict came back).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
import yaml

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.active_change import read_active_change_id
from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
    verification_yaml_path,
)
from super_harness.core.post_emit import refresh_state_after_emit
from super_harness.core.reducer import derive_state
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EmitPreconditionError, EventWriter
from super_harness.engineering import gh
from super_harness.engineering.pr_metadata import (
    PrSlugLookupError,
    resolve_slug_from_pr_body_strict,
)
from super_harness.engineering.verification_config import load_verification_config
from super_harness.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.sensors import Activity, WorkspaceContext
from super_harness.sensors.dispatcher import (
    ONESHOT_DISPATCHER_PARALLELISM,
    ONESHOT_DISPATCHER_TIMEOUT_S,
    SensorDispatcher,
)
from super_harness.sensors.verification_runner import VerificationRunner


def _current_state(root: Path, slug: str) -> str | None:
    """Current derived state for `slug`, or None if the change has no events."""
    cs = derive_state(events_path(root)).get(slug)
    return cs.current_state if cs else None


@click.command("done")
@click.argument("slug", required=False)
@click.option(
    "--skip-verify",
    "skip_verify",
    is_flag=True,
    help="Skip verification; emit a synthetic verification_passed then complete.",
)
@click.option(
    "--pr",
    "pr",
    default=None,
    help=(
        "PR number — when no positional <slug> is given, resolves the slug "
        "from the PR body's super-harness metadata block. The raw --pr value "
        "is ALSO recorded on implementation_complete as pr_url."
    ),
)
@click.pass_context
def done_cmd(
    ctx: click.Context,
    slug: str | None,
    skip_verify: bool,
    pr: str | None,
) -> None:
    """Verify a change and emit implementation_complete on a pass."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="done", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    # Slug resolution: positional wins; else --pr fetches the PR body + parses
    # the §2.5 metadata block (Phase 14 Task 14.3); else active change. The
    # raw `pr` value (unchanged) still flows into the implementation_complete
    # payload as pr_url below. See this module's docstring + engineering/
    # pr_metadata.py for the --pr exit-code matrix (DIVERGES from `pr validate`).
    resolved: str | None
    if slug:
        resolved = slug
    elif pr is not None:
        try:
            pr_int = int(pr)
        except ValueError:
            click.echo(
                format_error(
                    subcommand="done",
                    message=f"--pr value must be an integer: {pr!r}",
                    hint="Pass the PR number, e.g. `--pr 42`.",
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)
        try:
            body = gh.view_pr(pr_int, fields=["body"])["body"] or ""
        except gh.GhError as e:
            click.echo(
                format_error(
                    subcommand="done",
                    message=f"could not fetch PR #{pr_int}: {e}",
                    hint=(
                        "Check the PR number, `gh auth status`, and the "
                        "current repo."
                    ),
                ),
                err=True,
            )
            sys.exit(EXIT_EXTERNAL_TOOL)
        try:
            resolved = resolve_slug_from_pr_body_strict(body, pr_number=pr_int)
        except PrSlugLookupError as e:
            click.echo(
                format_error(subcommand="done", message=e.message, hint=e.hint),
                err=True,
            )
            sys.exit(e.exit_code)
    else:
        resolved = read_active_change_id(root)
    if resolved is None:
        click.echo(
            format_error(
                subcommand="done",
                message="no change specified and no active change found",
                hint="Pass a `<slug>` or run `super-harness change start <slug>` first.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    # `done` only completes a change that is already IMPLEMENTATION_IN_PROGRESS.
    # Gate this BEFORE running verification or emitting anything: otherwise a
    # `done` run too early would leave an orphan verification_passed in the
    # append-only stream (the dispatcher / synthetic emit lands as a legal
    # self-loop on the earlier state, and only the later implementation_complete
    # is rejected). Fail fast and write nothing.
    current = _current_state(root, resolved)
    if current != "IMPLEMENTATION_IN_PROGRESS":
        click.echo(
            format_error(
                subcommand="done",
                message=(
                    f"change {resolved!r} is "
                    f"{current or 'unknown (no such change)'}, "
                    "not IMPLEMENTATION_IN_PROGRESS"
                ),
                hint=(
                    "`done` completes an in-progress change; start implementation "
                    "first, or check the slug with `super-harness status`."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    writer = EventWriter(events_path(root))

    if skip_verify:
        _done_skip_verify(ctx, root, writer, resolved, pr)
        return  # _done_skip_verify always sys.exit()s

    # --- Default path: run verification through the dispatcher. -----------
    # Pre-load + validate verification.yaml before dispatch (same rationale as
    # `verify` — keep config errors precise instead of letting the sensor's
    # in-thread-pool load raise into the dispatcher's broad `except Exception`).
    # NOTE: only the DEFAULT path loads config; `--skip-verify` (handled above)
    # never touches verification.yaml, so a missing/invalid config is irrelevant
    # there. Missing → EXIT_NO_CONFIG; syntax / wrong-shape / bad-placeholder →
    # EXIT_VALIDATION.
    try:
        load_verification_config(verification_yaml_path(root))
    except FileNotFoundError:
        click.echo(
            format_error(
                subcommand="done",
                message=f"verification config not found at {verification_yaml_path(root)}",
                hint="Run `super-harness init` to create `.harness/verification.yaml`.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="done",
                message=f"{verification_yaml_path(root)} is not valid YAML: {e}",
                hint="Fix the YAML syntax in `.harness/verification.yaml`.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    except ValueError as e:
        # VerificationConfigError / InterpolationError both subclass ValueError;
        # the exception message is already descriptive.
        click.echo(
            format_error(subcommand="done", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    ctx_ws = WorkspaceContext(
        workspace_root=root, git_branch=None, active_change_id=resolved
    )
    dispatcher = SensorDispatcher(
        [VerificationRunner()],
        writer=writer,
        context=ctx_ws,
        timeout_s=ONESHOT_DISPATCHER_TIMEOUT_S,
        max_parallelism=ONESHOT_DISPATCHER_PARALLELISM,
    )
    # The dispatcher emits verification_passed/failed + refreshes state.yaml
    # internally. On a pass, that verification_passed satisfies the
    # implementation_complete hard-prereq we rely on below.
    results = dispatcher.on_activity(
        Activity(type="cli_done", change_id=resolved, payload={})
    )

    if not results:
        click.echo(
            format_error(
                subcommand="done",
                message="verification did not complete (sensor crashed or timed out)",
                hint="Check `.harness/events.jsonl` for a sensor_crashed event.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    result = results[0]
    if result.status != "pass":
        # Verification failed → do NOT emit implementation_complete.
        if ctx.obj.get("json"):
            click.echo(
                json_envelope(
                    command="done",
                    status="fail",
                    exit_code=EXIT_VALIDATION,
                    data=result.details,
                )
            )
        else:
            click.echo(
                format_error(
                    subcommand="done",
                    message=f"verification failed for {resolved}; "
                    "implementation_complete not emitted",
                    hint=(
                        "Inspect the run summary "
                        f"({(result.details or {}).get('summary_path', '<summary>')}), "
                        "fix the failing checks, then re-run `super-harness done`."
                    ),
                ),
                err=True,
            )
        sys.exit(EXIT_VALIDATION)

    # Verification passed (dispatcher already wrote verification_passed +
    # refreshed). Emit implementation_complete through the strict writer.
    if not _emit_implementation_complete(ctx, root, writer, resolved, pr):
        return  # error already reported + sys.exit()'d inside
    _report_done_success(ctx, resolved, result.details)


def _done_skip_verify(
    ctx: click.Context,
    root: Path,
    writer: EventWriter,
    slug: str,
    pr: str | None,
) -> None:
    """`--skip-verify` path: synthetic verification_passed, then complete.

    Both emits go through the STRICT writer (never skip_validation). The caller's
    pre-flight gate has already rejected any change that is not
    IMPLEMENTATION_IN_PROGRESS, so the strict writer here is a defensive backstop
    (an EmitPreconditionError would still surface as EXIT_VALIDATION).
    """
    vp = Event(
        event_id=new_event_id(),
        type="verification_passed",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework="plain",
        payload={"skipped": True, "reason": "--skip-verify"},
    )
    try:
        writer.emit(vp)  # strict — NOT skip_validation
    except EmitPreconditionError as e:
        click.echo(
            format_error(subcommand="done", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)

    if not _emit_implementation_complete(ctx, root, writer, slug, pr):
        return  # error already reported + sys.exit()'d inside
    _report_done_success(ctx, slug, data=None)


def _emit_implementation_complete(
    ctx: click.Context,
    root: Path,
    writer: EventWriter,
    slug: str,
    pr: str | None,
) -> bool:
    """Emit `implementation_complete` (strict) + refresh state.

    Returns True on success. On EmitPreconditionError (e.g. the change is not in
    IMPLEMENTATION_IN_PROGRESS, or the verification_passed prereq is missing)
    reports the error and `sys.exit(EXIT_VALIDATION)` — so a False return is
    never actually observed by the caller (the sys.exit raises first); the bool
    keeps the call sites readable.
    """
    ev = Event(
        event_id=new_event_id(),
        type="implementation_complete",
        change_id=slug,
        timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier="cli"),
        framework="plain",
        payload={"pr_url": pr} if pr else {},
    )
    try:
        # Strict — the prior verification_passed (dispatcher-emitted on the
        # default path, or the synthetic one on --skip-verify) satisfies the
        # implementation_complete hard prerequisite.
        writer.emit(ev)
    except EmitPreconditionError as e:
        click.echo(
            format_error(subcommand="done", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    refresh_state_after_emit(root)
    return True


def _report_done_success(
    ctx: click.Context, slug: str, data: dict[str, Any] | None
) -> None:
    """Emit the success envelope / line and `sys.exit(EXIT_OK)`."""
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="done",
                status="pass",
                exit_code=EXIT_OK,
                data=data or {"change_id": slug},
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(f"done {slug}: implementation_complete emitted")
    sys.exit(EXIT_OK)
