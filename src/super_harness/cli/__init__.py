import click

from super_harness.cli.event import event_group
from super_harness.cli.state import state_group
from super_harness.version import __version__


@click.group(
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
# state.py / event.py only depend on `super_harness.cli.exit_codes` and
# `super_harness.cli.output`, never on `super_harness.cli` itself, so there's
# no circular-import risk.
main.add_command(state_group)
main.add_command(event_group)
