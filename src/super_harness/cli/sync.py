"""super-harness sync — re-render the AGENTS.md super-harness section.

The "re-render-without-reinit" path: bring the §2.2 outer super-harness section
(version stamp + every installed adapter's guidance block) back up to date
WITHOUT re-running `init` — it never touches `.harness/` — and WITHOUT ever
modifying user content outside the begin/end markers. The render logic itself is
shared with `init` via ``engineering.agents_md_render.render_super_harness_section``
(the init + sync SSOT), so the §2.2 template never drifts between the two.

Surface (cli-command-surface §sync):

    super-harness sync [--agents-md] [--adapter <name>] [--yes/-y]

Modes:
1. No flags / ``--agents-md`` — FULL re-render (identical in v0.1: built-in
   adapters contribute no verification.yaml checks yet, so the adapter-checks
   sync leg is a no-op; v0.2 adds it). Stamps the outer section at the current
   ``__version__`` and re-injects the plain framework block + every installed
   adapter.
2. ``--adapter <name>`` — re-inject ONLY that adapter's subsection (NO outer
   version bump). If BOTH ``--adapter`` and ``--agents-md`` are given,
   ``--adapter`` wins (adapter-only scope).

Overwrite-confirm (both modes): if an existing super-harness section would be
overwritten (``section_present``) we prompt — UNLESS global ``--quiet`` or the
local ``--yes`` was passed. Declining raises Click's ``Abort`` → exit 1 (matches
``adapter uninstall``). No section present (AGENTS.md absent, or present without
our markers) → no prompt (no overwrite risk).

Workspace resolution + error/exit-code conventions mirror the sibling `adapter`
group (cli/adapter.py): walk up for ``.harness/`` and map
``HarnessNotInitialized`` → ``EXIT_NO_CONFIG``; errors go through ``format_error``
on stderr. Exit set is ``0/1/3/5`` — ``5``/EXIT_CONCURRENCY (adapters.yaml file
lock) is NOT emitted in v0.1, same as the `adapter` group (no locking yet).

``--json`` is NOT honored in v0.1 (like `init`): human output only; passing it
does not crash.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
import yaml

from super_harness.adapters import AgentAdapter, FrameworkAdapter
from super_harness.adapters.registry import load_adapters
from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK
from super_harness.core.paths import (
    HarnessNotInitialized,
    adapters_yaml_path,
    find_harness_root,
)
from super_harness.engineering.agents_md import (
    AgentsMdInjectionError,
    inject_agent_subsection,
    inject_framework_subsection,
    section_present,
)
from super_harness.engineering.agents_md_render import render_super_harness_section
from super_harness.version import __version__

# Shared AGENTS.md error envelope (mirrors init / adapter install): an OSError
# (unwritable AGENTS.md / full disk) or AgentsMdInjectionError (duplicate outer
# block) must surface through format_error — never a raw traceback.
_AGENTS_MD_WRITE_HINT = (
    "Fix AGENTS.md (file permissions / duplicate super-harness markers) and "
    "re-run `sync`."
)


@click.command("sync")
@click.option(
    "--agents-md",
    "agents_md",
    is_flag=True,
    help=(
        "Re-render the AGENTS.md super-harness section (version bump + re-inject "
        "installed adapters). v0.1: identical to no-arg — built-in adapters "
        "contribute no verification.yaml checks yet, so the adapter-checks sync "
        "leg is a no-op (v0.2 adds it)."
    ),
)
@click.option(
    "--adapter",
    "adapter_name",
    default=None,
    help="Re-inject ONLY this adapter's subsection (no outer version bump). "
    "Wins over --agents-md if both are given.",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    help="Skip the overwrite-confirm prompt.",
)
@click.pass_context
def sync_cmd(
    ctx: click.Context, agents_md: bool, adapter_name: str | None, assume_yes: bool
) -> None:
    """Re-render the AGENTS.md super-harness section without re-running init.

    v0.1: --json is not honored by sync (re-render produces no machine-parseable
    state) and --agents-md is identical to the no-arg full re-render.
    """
    # --agents-md is accepted but unread in the full-render branch: it selects the
    # default (full) mode and only carries the v0.1 no-op caveat in --help (Phase 1
    # placeholder convention — no runtime stderr notice).
    _ = agents_md
    root = _resolve_root(ctx, "sync")
    agents_path = root / "AGENTS.md"
    quiet = bool(ctx.obj.get("quiet"))

    if adapter_name is not None:
        _sync_adapter(root, agents_path, adapter_name, quiet=quiet, assume_yes=assume_yes)
    else:
        _sync_full(root, agents_path, quiet=quiet, assume_yes=assume_yes)


def _sync_full(
    root: Path, agents_path: Path, *, quiet: bool, assume_yes: bool
) -> None:
    """Full re-render: outer section version bump + re-inject all adapters."""
    # The shared renderer (init + sync SSOT) lets OSError / AgentsMdInjectionError
    # propagate into THIS envelope (fail-loud); its internal adapters.yaml load is
    # non-fatal (advisory + skip), so a corrupt adapters.yaml is NOT re-handled here.
    # The confirm is INSIDE the try so the section_present read (an unreadable /
    # non-UTF-8 AGENTS.md) surfaces through format_error too; click.Abort from a
    # declined prompt is not in the catch tuple → propagates → exit 1.
    try:
        _confirm_overwrite_if_present(agents_path, quiet=quiet, assume_yes=assume_yes)
        render_super_harness_section(root, agents_path, __version__)
    except (OSError, AgentsMdInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update AGENTS.md: {e}",
                hint=_AGENTS_MD_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if not quiet:
        click.echo(f"Synced AGENTS.md super-harness section (v{__version__}).")
    sys.exit(EXIT_OK)


def _sync_adapter(
    root: Path, agents_path: Path, name: str, *, quiet: bool, assume_yes: bool
) -> None:
    """Re-inject ONLY ``name``'s subsection (no outer version bump)."""
    # Mirror adapter.py's corrupt-yaml handling. `load_adapters` raises TWO error
    # families: a syntactically-broken file (`yaml.YAMLError`, which does NOT derive
    # from `ValueError`) AND a wrong-shape / unloadable config (`ValueError` /
    # `OSError` / `ImportError` / `AttributeError` / `TypeError`) — list both.
    try:
        frameworks, agents = load_adapters(adapters_yaml_path(root))
    except (
        yaml.YAMLError,
        ValueError,
        OSError,
        ImportError,
        AttributeError,
        TypeError,
    ) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"adapters.yaml is corrupt or unreadable: {e}",
                hint="Fix or remove .harness/adapters.yaml and retry.",
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    # Explicitly-typed union list: a bare `(*frameworks, *agents)` tuple widens
    # the element type to the shared `ABC` base (mypy joins the two adapter ABCs),
    # which then has no `.name` / `.agents_md_subsection`.
    installed: list[FrameworkAdapter | AgentAdapter] = [*frameworks, *agents]
    # `load_adapters` skips `enabled: false` entries, so a disabled-but-listed
    # adapter is reported as "not installed" here (it has no live subsection to
    # re-render). This differs from `adapter uninstall`, which reads the raw yaml.
    adapter = next((a for a in installed if a.name == name), None)
    if adapter is None:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"adapter {name!r} is not installed",
                hint="Use `adapter list` to see installed adapters.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    # AGENTS.md absent → skip silently (best-effort, like `adapter install`): there
    # is no section to sync a single adapter into, and an adapter-scoped sync must
    # NOT regenerate the whole file. Exit OK.
    if not agents_path.exists():
        if not quiet:
            click.echo("No AGENTS.md to sync.")
        sys.exit(EXIT_OK)

    # Confirm INSIDE the try so the section_present read (unreadable / non-UTF-8
    # AGENTS.md) surfaces through format_error; a declined prompt raises
    # click.Abort (not in the catch tuple) → propagates → exit 1.
    try:
        _confirm_overwrite_if_present(agents_path, quiet=quiet, assume_yes=assume_yes)
        if isinstance(adapter, AgentAdapter):
            inject_agent_subsection(agents_path, adapter.name, adapter.agents_md_subsection())
        else:
            inject_framework_subsection(
                agents_path, adapter.name, adapter.agents_md_subsection()
            )
    except (OSError, AgentsMdInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update AGENTS.md: {e}",
                hint=_AGENTS_MD_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if not quiet:
        click.echo(f"Re-injected {name} adapter subsection in AGENTS.md.")
    sys.exit(EXIT_OK)


def _confirm_overwrite_if_present(
    agents_path: Path, *, quiet: bool, assume_yes: bool
) -> None:
    """Prompt before overwriting an existing section (skipped on --quiet / --yes).

    Only prompts when a super-harness section is actually present (the only
    overwrite risk). Declining raises Click's ``Abort`` → exit 1 (matches
    ``adapter uninstall``).
    """
    if quiet or assume_yes:
        return
    if not section_present(agents_path):
        return
    click.confirm(
        "This will overwrite content inside the super-harness section; continue?",
        abort=True,
    )


def _resolve_root(ctx: click.Context, subcommand: str) -> Path:
    """Resolve the workspace root or exit EXIT_NO_CONFIG (mirrors adapter.py)."""
    try:
        return find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand=subcommand, message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
