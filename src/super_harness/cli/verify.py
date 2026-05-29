"""`verify` command — drive the VerificationRunner sensor on a change.

Per cli-command-surface §3.4 + engineering-integration §2.3 / §3.6. `verify`
runs `.harness/verification.yaml`'s three layers (baseline / framework_adapter /
user_checks) for a change and reports the verdict. It does NOT advance the
lifecycle (that's `done` — `verify` is a read-only-on-state probe), though the
underlying VerificationRunner sensor DOES emit a `verification_passed` /
`verification_failed` informational event (state-preserving) via the dispatcher.

Wiring: `verify` builds a one-shot `SensorDispatcher` holding only the builtin
`VerificationRunner`, injects a `cli_verify` `Activity`, and reads the single
`SensorResult` back. The dispatcher emits the verification event + refreshes
state.yaml internally, so `verify` itself neither emits nor refreshes.

Slug resolution (cli-command-surface convention, NOT git-branch parsing):
explicit `<slug>` argument wins; else the first non-terminal change via
`read_active_change_id`. Neither → EXIT_VALIDATION. `--pr` is surface-only in
v0.1 (the gh wrapper that resolves a slug from a PR number is Phase 12).

Exit codes (cli-command-surface §2.2):
- 0 — verdict pass (every must_pass check passed).
- 2 — verdict fail (a must_pass check failed) OR a config validation error
  (syntax-corrupt / wrong-shape / bad-placeholder verification.yaml, or an
  unknown `--check` id) surfaced by the pre-load before dispatch.
- 3 — `.harness/verification.yaml` missing (uninitialized / hand-deleted).
- 1 — the sensor crashed / timed out (no result came back).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.cli.output import json_envelope
from super_harness.core.active_change import read_active_change_id
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
    verification_yaml_path,
)
from super_harness.core.writer import EventWriter
from super_harness.engineering.verification_config import load_verification_config
from super_harness.sensors import Activity, WorkspaceContext
from super_harness.sensors.dispatcher import (
    ONESHOT_DISPATCHER_PARALLELISM,
    ONESHOT_DISPATCHER_TIMEOUT_S,
    SensorDispatcher,
)
from super_harness.sensors.verification_runner import (
    VerificationRunner,
    collectable_check_ids,
)


@click.command("verify")
@click.argument("slug", required=False)
@click.option(
    "--pr",
    "pr",
    default=None,
    help="PR number/URL (surface-only in v0.1; does NOT resolve the slug).",
)
@click.option(
    "--layer",
    type=click.Choice(["baseline", "adapter", "user"]),
    default=None,
    help="Restrict the run to a single verification layer.",
)
@click.option(
    "--check",
    "check",
    multiple=True,
    help="Run only checks with this id (repeatable).",
)
@click.pass_context
def verify_cmd(
    ctx: click.Context,
    slug: str | None,
    pr: str | None,
    layer: str | None,
    check: tuple[str, ...],
) -> None:
    """Run verification checks for a change and report the verdict."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="verify", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    # TODO(phase 12): resolve slug from --pr (the gh wrapper lands then). For now
    # --pr is surface-only and never participates in slug resolution.
    resolved = slug or read_active_change_id(root)
    if resolved is None:
        click.echo(
            format_error(
                subcommand="verify",
                message="no change specified and no active change found",
                hint="Pass a `<slug>` or run `super-harness change start <slug>` first.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    # Pre-load + validate verification.yaml *before* dispatch so config errors
    # are clean, precise exits — NOT swallowed sensor crashes. The sensor re-loads
    # the config during its run (the double-load is cheap); this pre-load exists
    # purely so a missing / syntax-corrupt / wrong-shape / bad-placeholder config
    # surfaces correctly instead of being caught by the dispatcher's broad
    # `except Exception` → sensor_crashed → empty results → the imprecise
    # EXIT_GENERIC below. FileNotFoundError → EXIT_NO_CONFIG; yaml.YAMLError
    # (syntax) and the VerificationConfigError/ValueError family (wrong shape /
    # bad enum / dup id / non-allowlisted ${NAME} placeholder) → EXIT_VALIDATION.
    try:
        cfg = load_verification_config(verification_yaml_path(root))
    except FileNotFoundError:
        click.echo(
            format_error(
                subcommand="verify",
                message=f"verification config not found at {verification_yaml_path(root)}",
                hint="Run `super-harness init` to create `.harness/verification.yaml`.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    except yaml.YAMLError as e:
        click.echo(
            format_error(
                subcommand="verify",
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
            format_error(subcommand="verify", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    # Reject an unknown `--check <id>` BEFORE dispatch: an id collectable in no
    # (selected) layer would otherwise silently run 0 checks and "pass" vacuously.
    if check:
        collectable = collectable_check_ids(cfg, layer=layer)
        unknown = [c for c in check if c not in collectable]
        if unknown:
            in_layer = f" in layer {layer!r}" if layer else ""
            click.echo(
                format_error(
                    subcommand="verify",
                    message=f"no such check{in_layer}: {', '.join(sorted(unknown))}",
                    hint="Check the ids in `.harness/verification.yaml`"
                    + (" or drop `--layer`." if layer else "."),
                ),
                err=True,
            )
            sys.exit(EXIT_VALIDATION)

    ctx_ws = WorkspaceContext(
        workspace_root=root, git_branch=None, active_change_id=resolved
    )
    writer = EventWriter(events_path(root))
    dispatcher = SensorDispatcher(
        [VerificationRunner()],
        writer=writer,
        context=ctx_ws,
        timeout_s=ONESHOT_DISPATCHER_TIMEOUT_S,
        max_parallelism=ONESHOT_DISPATCHER_PARALLELISM,
    )
    activity = Activity(
        type="cli_verify",
        change_id=resolved,
        payload={"layer": layer, "checks": list(check) or None},
    )
    # The dispatcher emits verification_passed/failed + refreshes state.yaml
    # internally; verify neither emits nor refreshes.
    results = dispatcher.on_activity(activity)

    if not results:
        # The lone VerificationRunner crashed or timed out (its
        # sensor_crashed / sensor_timeout_exceeded event was auto-emitted by
        # the dispatcher, but no verdict came back).
        click.echo(
            format_error(
                subcommand="verify",
                message="verification did not complete (sensor crashed or timed out)",
                hint="Check `.harness/events.jsonl` for a sensor_crashed event.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    result = results[0]
    passed = result.status == "pass"
    exit_code = EXIT_OK if passed else EXIT_VALIDATION

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="verify",
                status="pass" if passed else "fail",
                exit_code=exit_code,
                # result.details IS the frozen verify --json data block
                # (verify_data_block); lift it verbatim — do NOT reshape.
                data=result.details,
            )
        )
    elif not ctx.obj.get("quiet"):
        click.echo(result.summary)

    sys.exit(exit_code)
