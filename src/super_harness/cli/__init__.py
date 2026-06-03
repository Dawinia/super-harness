# L1 anchor (HG-D self-host) — @capability:capability-cli-surface
"""super-harness CLI root group.

Defines the top-level `super-harness` Click group and wires up all subcommands
(init, change, status, state, event). Per `cli-command-surface` §2.1 global
conventions (`--workspace`, `--json`, `--quiet`, `--verbose` flags).
"""
from __future__ import annotations

import click

from super_harness.cli.adapter import adapter_group
from super_harness.cli.anchor import anchor_group
from super_harness.cli.attest import attest_group
from super_harness.cli.change import change_group
from super_harness.cli.daemon import daemon_group
from super_harness.cli.done import done_cmd
from super_harness.cli.event import event_group
from super_harness.cli.gate import gate_group
from super_harness.cli.group_options import (
    GroupAwareCommand,
    GroupAwareGroup,
    rewrap_subtree,
)
from super_harness.cli.implementation import implementation_group
from super_harness.cli.init import init_cmd
from super_harness.cli.on_merge import on_merge_cli
from super_harness.cli.plan import plan_group
from super_harness.cli.pr import pr_group
from super_harness.cli.review import review_group
from super_harness.cli.sensor import sensor_group
from super_harness.cli.state import state_group
from super_harness.cli.status import status_cmd
from super_harness.cli.sync import sync_cmd
from super_harness.cli.verification import verification_group
from super_harness.cli.verify import verify_cmd
from super_harness.version import __version__


@click.group(
    cls=GroupAwareGroup,
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


# Subgroup registration is at module bottom because it must reference `main`
# after its definition. The subgroup *imports* themselves are top-of-file —
# state.py / event.py only depend on `super_harness.exit_codes` and
# `super_harness.cli.output`, never on `super_harness.cli` itself, so there's
# no circular-import risk.
main.add_command(state_group)
main.add_command(event_group)
main.add_command(init_cmd)
main.add_command(change_group)
main.add_command(status_cmd)
main.add_command(sensor_group)
main.add_command(gate_group)
main.add_command(daemon_group)
main.add_command(adapter_group)
main.add_command(verify_cmd)
main.add_command(done_cmd)
main.add_command(sync_cmd)
main.add_command(verification_group)
main.add_command(anchor_group)
main.add_command(pr_group)
main.add_command(review_group)
main.add_command(plan_group)
main.add_command(implementation_group)
main.add_command(on_merge_cli)
main.add_command(attest_group)


# Rewrap every registered subcommand (and its descendants) so each one is a
# `GroupAwareCommand` / `GroupAwareGroup`. Subgroups defined in their own
# modules use plain ``@click.group(...)`` decorators, so the root group's
# `command_class`/`group_class` propagation does NOT retroactively apply to
# them. The class-swap below fixes that minimally — no per-module
# `cls=GroupAwareCommand` wiring needed. Refs OPEN-ITEMS #6 S8-misleading.
rewrap_subtree(main)


# Re-export names used by other modules / tests.
__all__ = [
    "GroupAwareCommand",
    "GroupAwareGroup",
    "main",
]
