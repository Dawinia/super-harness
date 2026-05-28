"""super-harness init — scaffold the `.harness/` workspace.

Creates the canonical directory layout (6 subdirs + 6 skeleton files) per
`engineering-integration` §2.1. Idempotent without `--force`; `--force`
overwrites all skeleton files including user edits. Per `cli-command-surface` §2.3.
"""
from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

import click

from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK
from super_harness.engineering.agents_md import (
    inject_framework_subsection,
    inject_section,
)
from super_harness.version import __version__

_TEMPLATES = files("super_harness.templates")

# The §2.2 outer-section template. The framework placeholder is kept literal
# (inject_framework_subsection replaces it with the plain block on init); the
# agent slot carries the no-agent anchor directly rather than the
# [AGENT_SECTION_AUTO_INSERTED] literal — init knows there is no agent adapter
# yet, and the anchor is what a later `adapter install <agent>` replaces. This
# leaves NO [*_SECTION_AUTO_INSERTED] literal after init (§3.2 line 676).
_AGENTS_MD_SECTION_TEMPLATE = """\
<!-- super-harness section begin · v{version} · DO NOT EDIT MANUALLY -->
## Super-harness conventions

This project uses super-harness to ensure AI coding reliability.

### Branch naming

Branches MUST be named matching a registered super-harness change slug.
Examples: `2026-05-26-add-l1-anchors` / `feat-mobile-auth-flow`

If you use git directly: `git checkout -b <slug>`
If you use a framework command (recommended): the framework auto-creates the branch.

### PR creation

Use your framework's native PR command:

[FRAMEWORK_SECTION_AUTO_INSERTED]

super-harness will automatically append a metadata block to your PR description
between `<!-- super-harness:metadata -->` markers.
**Do not modify content between those markers manually.**

### Agent-specific guidance

<!-- super-harness no-agent-adapter-installed -->

### Before opening PR

Ensure `super-harness verify` passes (tests / lint / build / anchor sentinels).
If using a `done` skill, run `super-harness done <slug>` instead—it triggers
verify and emits the lifecycle event automatically.

### File scope

When implementing a change, edit only files in the declared `scope.files`
(see the plan artifact). Edits outside scope trigger drift warnings.

<!-- super-harness section end -->"""


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
            format_error(
                subcommand="init",
                message=f".harness/ already exists at {harness}",
                hint="Pass `--force` to overwrite the existing directory.",
            ),
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
    # Wire the repo-root AGENTS.md "super-harness section" (§2.2 / §3.2): create
    # or append our section (preserving any user content outside the markers),
    # then replace the framework placeholder with the plain framework block.
    # PlainAdapter is the single source of the plain block (no hardcoded text).
    # Both injectors write atomically and are CRLF-safe. Idempotent: a re-render
    # (e.g. --force) replaces the existing section rather than duplicating it.
    agents_path = root / "AGENTS.md"
    inject_section(agents_path, _AGENTS_MD_SECTION_TEMPLATE.format(version=__version__))
    inject_framework_subsection(agents_path, "plain", PlainAdapter().agents_md_subsection())
    click.echo(f"super-harness initialized at {harness}")
    sys.exit(EXIT_OK)
