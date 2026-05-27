from __future__ import annotations

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


def _verification_default() -> str:
    # I-1 fix: load lazily (inside init_cmd, not at module import) so that
    # `super-harness --help` and every unrelated subcommand still works even
    # if the wheel install is corrupt. Asymmetric with _source_paths_default
    # because we have NO sensible inline fallback for verification policy —
    # the only honest behavior is to abort with an actionable message.
    src = _TEMPLATES.joinpath("verification_defaults.yaml")
    if not src.is_file():
        raise click.ClickException(
            "super-harness install is corrupt — bundled template "
            "'verification_defaults.yaml' missing. Reinstall super-harness."
        )
    return src.read_text()


def _skeleton_files() -> dict[str, str]:
    return {
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
        "verification.yaml": _verification_default(),
        "conventions.md": "# Project conventions (referenced by reviewer sensors)\n",
    }


@click.command("init")
@click.option(
    "--setup-github",
    is_flag=True,
    help="(v0.1: no-op placeholder; Phase 11 wires gh CLI integration.)",
)
@click.option(
    "--framework",
    type=click.Choice(["openspec", "spec-kit", "superpowers", "plain"]),
    help="Explicit framework; default = auto-detect "
    "(v0.1: no-op placeholder; Phase 4 wires adapter selection.)",
)
@click.option("--force", is_flag=True)
@click.pass_context
def init_cmd(ctx: click.Context, setup_github: bool, framework: str | None, force: bool) -> None:
    """Initialize a project for super-harness.

    v0.1: --json is not honored by init (bootstrap command produces no
    machine-parseable state).
    """
    # I-2 (round 2): --setup-github and --framework are CLI-surface placeholders
    # in v0.1. Match Phase 1 convention (`state rebuild --verify`, `event log
    # --tail`): accept the flag, mark it unread, and advertise the no-op via the
    # --help caveat — NO runtime stderr notice (one convention, project-wide).
    # Phase 4 wires --framework detection; Phase 11 wires --setup-github.
    _ = setup_github
    _ = framework
    root = Path(ctx.obj.get("workspace") or ".").resolve()
    harness = root / ".harness"
    if harness.exists() and not force:
        click.echo(
            f"super-harness init: .harness/ already exists at {harness}\n"
            f"  Hint: pass --force to overwrite",
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
    for name, content in _skeleton_files().items():
        path = harness / name
        if path.exists() and not force:
            continue
        path.write_text(content)
    click.echo(f"super-harness initialized at {harness}")
    sys.exit(EXIT_OK)
