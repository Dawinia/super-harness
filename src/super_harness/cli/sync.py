"""super-harness sync — re-render the managed super-harness artifacts.

The "re-render-without-reinit" path: bring the managed artifacts back up to date
WITHOUT re-running `init` — it never touches `.harness/` config — and WITHOUT ever
modifying user content outside the begin/end markers. There are TWO managed
artifacts (both also written by `init`): the §2.2 outer AGENTS.md super-harness
section (version stamp + every installed adapter's guidance block) and the
repo-root marker-bounded ``.gitignore`` block (the canonical auto-generated /
per-machine paths). Each render is shared with `init` via its SSOT —
``engineering.agents_md_render.render_super_harness_section`` and
``engineering.gitignore_injector.inject_gitignore_block`` — so neither template
drifts between init and sync.

Surface (cli-command-surface §sync):

    super-harness sync [--agents-md] [--gitignore] [--adapter <name>] [--yes/-y]

Modes (no flag = all managed artifacts; each flag = one scope):
1. No flags — FULL re-render: the AGENTS.md section (version stamp + plain
   framework block + every installed adapter) AND the ``.gitignore`` block.
   (Passing BOTH ``--agents-md`` and ``--gitignore`` is equivalent to no-arg.)
2. ``--agents-md`` — re-render ONLY the AGENTS.md section (no ``.gitignore``
   change). v0.1: built-in adapters contribute no verification.yaml checks yet,
   so the adapter-checks sync leg is a no-op (v0.2 adds it).
3. ``--gitignore`` — re-render ONLY the managed ``.gitignore`` block. Picks up
   ``_CANONICAL_PATHS`` additions from a super-harness upgrade non-destructively
   (``init --force`` would clobber skeleton config; this does not).
4. ``--adapter <name>`` — re-inject ONLY that adapter's subsection (NO outer
   version bump). ``--adapter`` is the narrowest scope and wins if combined with
   ``--agents-md`` / ``--gitignore``.

Overwrite-confirm (AGENTS.md legs): if an existing super-harness section would be
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
from super_harness.engineering.gitignore_injector import (
    GitignoreInjectionError,
    inject_gitignore_block,
)
from super_harness.engineering.sync_check import run_sync_check
from super_harness.exit_codes import (
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
    EXIT_VALIDATION,
)
from super_harness.version import __version__

# Shared AGENTS.md error envelope (mirrors init / adapter install): an OSError
# (unwritable AGENTS.md / full disk) or AgentsMdInjectionError (duplicate outer
# block) must surface through format_error — never a raw traceback.
_AGENTS_MD_WRITE_HINT = (
    "Fix AGENTS.md (file permissions / duplicate super-harness markers) and "
    "re-run `sync`."
)
_GITIGNORE_WRITE_HINT = (
    "Fix .gitignore (permissions / duplicate super-harness markers) and "
    "re-run `sync --gitignore`."
)


@click.command("sync")
@click.option(
    "--agents-md",
    "agents_md",
    is_flag=True,
    help=(
        "Re-render ONLY the AGENTS.md super-harness section (no .gitignore "
        "change). v0.1: built-in adapters contribute no verification.yaml checks "
        "yet, so the adapter-checks sync leg is a no-op (v0.2 adds it)."
    ),
)
@click.option(
    "--adapter",
    "adapter_name",
    default=None,
    help="Re-inject ONLY this adapter's subsection (no outer version bump). "
    "Wins over --agents-md / --gitignore if combined.",
)
@click.option(
    "--gitignore",
    "gitignore",
    is_flag=True,
    help="Re-render ONLY the managed .gitignore block (no AGENTS.md change). "
    "Picks up `_CANONICAL_PATHS` additions from a super-harness upgrade "
    "without re-running init.",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    help="Skip the overwrite-confirm prompt.",
)
@click.option(
    "--check",
    "check",
    is_flag=True,
    help="Dry-run: report drift between the managed artifacts and the "
    "super-harness template WITHOUT writing. Exit 2 on drift. Composes with "
    "--agents-md / --gitignore; not supported with --adapter.",
)
@click.pass_context
def sync_cmd(
    ctx: click.Context,
    agents_md: bool,
    adapter_name: str | None,
    gitignore: bool,
    assume_yes: bool,
    check: bool,
) -> None:
    """Re-render the managed super-harness artifacts without re-running init.

    No-arg `sync` refreshes BOTH the AGENTS.md super-harness section and the
    managed `.gitignore` block. `--agents-md` / `--gitignore` are single-artifact
    scopes; `--adapter <name>` is the narrowest scope (one adapter subsection) and
    wins if combined. v0.1: --json is not honored (re-render produces no
    machine-parseable state).
    """
    root = _resolve_root(ctx, "sync")
    agents_path = root / "AGENTS.md"
    quiet = bool(ctx.obj.get("quiet"))

    if check:
        if adapter_name is not None:
            click.echo(
                format_error(
                    subcommand="sync",
                    message="`sync --check` does not support `--adapter`",
                    hint="Use `sync --agents-md --check` to verify the whole "
                    "AGENTS.md section (it already covers every adapter subsection).",
                ),
                err=True,
            )
            sys.exit(EXIT_GENERIC)
        # No scope flag → check both; a single scope flag narrows.
        check_agents = agents_md or not gitignore
        check_gitignore = gitignore or not agents_md
        _sync_check(
            root, check_agents=check_agents, check_gitignore=check_gitignore, quiet=quiet
        )

    if adapter_name is not None:
        _sync_adapter(root, agents_path, adapter_name, quiet=quiet, assume_yes=assume_yes)
    elif gitignore and not agents_md:
        _sync_gitignore(root, quiet=quiet)
    elif agents_md and not gitignore:
        _sync_agents_md_only(root, agents_path, quiet=quiet, assume_yes=assume_yes)
    else:
        # no flag, or both --agents-md and --gitignore → full (both artifacts)
        _sync_full(root, agents_path, quiet=quiet, assume_yes=assume_yes)


def _sync_full(
    root: Path, agents_path: Path, *, quiet: bool, assume_yes: bool
) -> None:
    """Full re-render: AGENTS.md section (version bump + all adapters) AND the
    managed `.gitignore` block.

    The AGENTS.md leg owns the overwrite-confirm; the `.gitignore` block has no
    user content between its markers, so it piggybacks silently after a confirmed
    AGENTS.md render. Both legs share init's fail-loud error envelope.
    """
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

    try:
        inject_gitignore_block(root / ".gitignore")
    except (OSError, GitignoreInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update .gitignore: {e}",
                hint=_GITIGNORE_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if not quiet:
        click.echo(
            f"Synced AGENTS.md super-harness section (v{__version__}) "
            f"and .gitignore block."
        )
    sys.exit(EXIT_OK)


def _sync_check(
    root: Path, *, check_agents: bool, check_gitignore: bool, quiet: bool
) -> None:
    """Report drift for the in-scope managed artifacts; never writes.

    Exit: drift → EXIT_VALIDATION (2, matches `doc check`); clean → EXIT_OK;
    a render/IO failure → EXIT_GENERIC via the shared AGENTS.md error envelope.
    """
    try:
        result = run_sync_check(
            root, __version__, check_agents=check_agents, check_gitignore=check_gitignore
        )
    except (OSError, AgentsMdInjectionError, GitignoreInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to compute drift: {e}",
                hint=_AGENTS_MD_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if result.drift:
        for artifact in result.drift:
            click.echo(artifact.diff, err=True)
        names = ", ".join(artifact.name for artifact in result.drift)
        click.echo(
            format_error(
                subcommand="sync",
                message=f"{names} out of sync with the super-harness template",
                hint="Run `super-harness sync` to regenerate, then commit the result.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    if not quiet:
        checked = []
        if check_agents:
            checked.append("AGENTS.md")
        if check_gitignore:
            checked.append(".gitignore")
        click.echo(
            f"{' and '.join(checked)} in sync with the super-harness template."
        )
    sys.exit(EXIT_OK)


def _sync_agents_md_only(
    root: Path, agents_path: Path, *, quiet: bool, assume_yes: bool
) -> None:
    """Re-render ONLY the AGENTS.md section (no gitignore leg).

    Light duplication with `_sync_full`'s AGENTS.md leg is deliberate: extracting
    a shared inner helper that calls `sys.exit` is more tangled than the two
    readable legs, and the same error-envelope shape is already mirrored across
    `_sync_adapter`.
    """
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


def _sync_gitignore(root: Path, *, quiet: bool) -> None:
    """Re-render ONLY the managed `.gitignore` block (init + sync SSOT).

    Reuses `inject_gitignore_block` (marker-bounded, non-destructive, no-op when
    current, fail-loud on duplicate/unbalanced/non-UTF-8 markers). No confirm
    prompt: the block is purely our canonical path list — there is no user
    content between the markers to lose. Mirrors init's error envelope.
    """
    try:
        inject_gitignore_block(root / ".gitignore")
    except (OSError, GitignoreInjectionError) as e:
        click.echo(
            format_error(
                subcommand="sync",
                message=f"failed to update .gitignore: {e}",
                hint=_GITIGNORE_WRITE_HINT,
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if not quiet:
        click.echo("Synced .gitignore super-harness block.")
    sys.exit(EXIT_OK)


def _sync_adapter(
    root: Path, agents_path: Path, name: str, *, quiet: bool, assume_yes: bool
) -> None:
    """Re-inject ONLY ``name``'s subsection (no outer version bump)."""
    # Catch only the CONFIG-driven failure surface of `load_adapters` (v0.1
    # builtin-only): `yaml.YAMLError` (syntactically-broken file; does NOT derive
    # from `ValueError`), `ValueError` (wrong-shape / non-mapping / non-builtin
    # config), and `OSError` (unreadable file). A bad *builtin's* own constructor
    # (a code bug, not a config problem) is deliberately NOT caught — it should
    # fail loud, not be mislabeled as "adapters.yaml unreadable". (The old
    # plugin-exec `ImportError`/`AttributeError`/`TypeError` families are gone
    # with custom plugin loading; a non-mapping top level is now a `ValueError`.)
    try:
        frameworks, agents = load_adapters(adapters_yaml_path(root))
    except (yaml.YAMLError, ValueError, OSError) as e:
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
