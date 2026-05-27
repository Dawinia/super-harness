import sys
from importlib.resources import files
from pathlib import Path

import click

from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK

_TEMPLATES = files("super_harness.templates")


def _source_paths_default() -> str:
    src = _TEMPLATES.joinpath("source_paths_defaults.yaml")
    if src.is_file():
        return src.read_text()
    return "source_paths:\n  include:\n    - '**/*'\n  exclude:\n    - 'docs/**'\n"


_SKELETON_FILES = {
    "policy.yaml": "# super-harness policy (see sensor-gate-architecture §2.4)\n",
    "sensors.yaml": "sensors: []\n",
    "gates.yaml": (
        "gates:\n"
        "  - pre-tool-use\n"
        "  - pre-commit\n"
        "  - pre-push\n"
        "  - pr-open\n"
        "  - pr-merge\n"
    ),
    "source-paths.yaml": _source_paths_default(),
    "verification.yaml": _TEMPLATES.joinpath("verification_defaults.yaml").read_text(),
    "conventions.md": "# Project conventions (referenced by reviewer sensors)\n",
}


@click.command("init")
@click.option("--setup-github", is_flag=True, help="Also run GitHub repo setup.")
@click.option(
    "--framework",
    type=click.Choice(["openspec", "spec-kit", "superpowers", "plain"]),
    help="Explicit framework; default = auto-detect.",
)
@click.option("--force", is_flag=True)
@click.pass_context
def init_cmd(ctx: click.Context, setup_github: bool, framework: str | None, force: bool) -> None:
    """Initialize a project for super-harness."""
    root = Path(ctx.obj.get("workspace") or ".").resolve()
    harness = root / ".harness"
    if harness.exists() and not force:
        click.echo(
            f"super-harness init: .harness/ already exists at {harness}\n"
            f"  Hint: pass --force to overwrite\n",
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    harness.mkdir(parents=True, exist_ok=True)
    # events.jsonl created empty (writer appends later)
    (harness / "events.jsonl").touch()
    # N-4 fix: create all 6 sub-directories per engineering-integration §2.1
    for subdir in (
        "anchors",
        "sensor-results",
        "verification-results",
        "operation-logs",
        "pending-l1-updates",
        "pending-reviews",
    ):
        (harness / subdir).mkdir(exist_ok=True)
    for name, content in _SKELETON_FILES.items():
        path = harness / name
        if path.exists() and not force:
            continue
        path.write_text(content)
    click.echo(f"super-harness initialized at {harness}")
    sys.exit(EXIT_OK)
