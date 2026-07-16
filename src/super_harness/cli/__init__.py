"""super-harness CLI root group.

Defines the top-level `super-harness` Click group and its lazy command registry.
Per `cli-command-surface` §2.1 global conventions (`--workspace`, `--json`,
`--quiet`, `--verbose` flags).
"""

from __future__ import annotations

import click

from super_harness.cli.group_options import (
    GroupAwareCommand,
    GroupAwareGroup,
)
from super_harness.cli.lazy_group import CommandSpec, LazyGroup
from super_harness.version import __version__

COMMAND_SPECS = {
    "state": CommandSpec(
        "super_harness.cli.state:state_group",
        "Inspect / rebuild the derived state.yaml.",
    ),
    "event": CommandSpec(
        "super_harness.cli.event:event_group",
        "Inspect the event stream.",
    ),
    "init": CommandSpec(
        "super_harness.cli.init:init_cmd",
        "Initialize a project for super-harness.",
    ),
    "change": CommandSpec(
        "super_harness.cli.change:change_group",
        "Declare / abandon / list lifecycle changes.",
    ),
    "status": CommandSpec(
        "super_harness.cli.status:status_cmd",
        "Show current state for one change, all changes, or the most recently active change.",
    ),
    "report": CommandSpec(
        "super_harness.cli.report:report_cmd",
        "Show what the harness measurably did for you over a repo/time-window.",
    ),
    "sensor": CommandSpec(
        "super_harness.cli.sensor:sensor_group",
        "Inspect the sensor registry.",
    ),
    "gate": CommandSpec(
        "super_harness.cli.gate:gate_group",
        "Inspect the gate registry.",
    ),
    "observe": CommandSpec(
        "super_harness.cli.observe:observe_group",
        "Operate the optional framework-observer host (start / stop / status).",
    ),
    "adapter": CommandSpec(
        "super_harness.cli.adapter:adapter_group",
        "Install / uninstall / list super-harness integrations for frameworks + agents.",
    ),
    "verify": CommandSpec(
        "super_harness.cli.verify:verify_cmd",
        "Run verification checks for a change and report the verdict.",
    ),
    "done": CommandSpec(
        "super_harness.cli.done:done_cmd",
        "Verify a change and emit implementation_complete on a pass.",
    ),
    "sync": CommandSpec(
        "super_harness.cli.sync:sync_cmd",
        "Re-render the managed super-harness artifacts without re-running init.",
    ),
    "verification": CommandSpec(
        "super_harness.cli.verification:verification_group",
        "Register / manage verification checks in `.harness/verification.yaml`.",
    ),
    "pr": CommandSpec(
        "super_harness.cli.pr:pr_group",
        "PR-side helpers (validate PR metadata + lifecycle).",
    ),
    "review": CommandSpec(
        "super_harness.cli.review:review_group",
        "Compile contracts, import receipts, or disclose a review skip.",
    ),
    "plan": CommandSpec(
        "super_harness.cli.plan:plan_group",
        "Plan-phase lifecycle verbs (plain-mode manual emit).",
    ),
    "implementation": CommandSpec(
        "super_harness.cli.implementation:implementation_group",
        "Implementation-phase lifecycle verbs.",
    ),
    "on-merge": CommandSpec(
        "super_harness.cli.on_merge:on_merge_cli",
        "Emit a ``merged`` event (transitions the change to ARCHIVED).",
    ),
    "attest": CommandSpec(
        "super_harness.cli.attest:attest_group",
        "Lifecycle attestation: snapshot evidence + verify it covers a diff.",
    ),
    "decision": CommandSpec(
        "super_harness.cli.decision:decision_group",
        "Author, ratify, and check decision records.",
    ),
    "doc": CommandSpec(
        "super_harness.cli.doc:doc_group",
        "Check that derivable docs match their generators.",
    ),
}


@click.group(
    cls=LazyGroup,
    command_specs=COMMAND_SPECS,
    help="super-harness — CI-first cross-framework + cross-agent AI coding harness.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "--version", "-V")
@click.option(
    "--workspace",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help="Project root (defaults to walk-up from cwd).",
)
@click.option("--json", "json_output", is_flag=True, help="Machine-parseable JSON output.")
@click.option("--quiet", "-q", is_flag=True)
@click.option("--verbose", "-v", count=True)
@click.pass_context
def main(
    ctx: click.Context,
    workspace: str | None,
    json_output: bool,
    quiet: bool,
    verbose: int,
) -> None:
    """Root command group for super-harness."""
    # Naming convention for ctx.obj keys:
    #   - Keys use the SHORT CLI flag name (e.g. "json", "quiet", "workspace"), NOT the
    #     Python param name (`json_output` was renamed only to avoid shadowing stdlib `json`).
    #   - Subcommands must read `ctx.obj["json"]`, `ctx.obj["quiet"]`, etc. — never
    #     `ctx.obj["json_output"]`. The Python param `json_output` is private to this function.
    #   - If you add a new global flag in the future, store it under its CLI name in ctx.obj.
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = workspace
    ctx.obj["json"] = json_output
    ctx.obj["quiet"] = quiet
    ctx.obj["verbose"] = verbose


# Re-export names used by other modules / tests.
__all__ = [
    "CommandSpec",
    "GroupAwareCommand",
    "GroupAwareGroup",
    "LazyGroup",
    "main",
]
