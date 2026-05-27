import click

from super_harness.version import __version__


@click.group(help="super-harness — CI-first cross-framework + cross-agent AI coding harness.")
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
    ctx.ensure_object(dict)
    ctx.obj["workspace"] = workspace
    ctx.obj["json"] = json_output
    ctx.obj["quiet"] = quiet
    ctx.obj["verbose"] = verbose
