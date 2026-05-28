"""`adapter` subgroup — install super-harness integration for an AI agent.

v0.1 MINIMAL surface (Task 5.7): a single subcommand, `adapter install
claude-code`, which registers the super-harness PreToolUse gate hook into the
workspace's ``.claude/settings.json`` via the reference ``ClaudeCodeAdapter``
(Task 5.6). Deliberately deferred to Phase 6: ``uninstall`` / ``list``, any
non-Claude-Code adapter, and ``.harness/adapters.yaml`` persistence.

Workspace resolution + error/exit-code conventions mirror the sibling `gate`
group (cli/gate.py): walk up for ``.harness/`` and map
``HarnessNotInitialized`` → ``EXIT_NO_CONFIG``; all error output goes through
``format_error`` on stderr.

`.claude/`-absent decision (option (a) — install in a fresh repo):
``ClaudeCodeAdapter.detect(root)`` is NOT a precondition. ``install_hooks``
delegates to ``merge_pre_tool_use_hook``, which mkdirs ``.claude/`` and creates
``settings.json`` from an empty config when absent. Installing should "just
work" in a repo that has not yet been touched by Claude Code, so we do not
require ``.claude/`` to pre-exist; we only print a note that it was created.
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import EXIT_GENERIC, EXIT_NO_CONFIG, EXIT_OK
from super_harness.core.paths import HarnessNotInitialized, find_harness_root


@click.group("adapter")
def adapter_group() -> None:
    """Install super-harness integration for an AI coding agent."""


@adapter_group.command("install")
@click.argument("name", type=click.Choice(["claude-code"]))  # v0.1: claude-code only
@click.pass_context
def adapter_install(ctx: click.Context, name: str) -> None:
    """Install the <name> agent adapter (registers its hooks)."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(
                subcommand="adapter install", message=e.message, hint=e.hint
            ),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)

    adapter = ClaudeCodeAdapter()
    # `.claude/`-absent is fine — install creates it (decision (a), see module
    # docstring). We note the creation so the user knows a new dir appeared.
    created_claude_dir = not adapter.detect(root)

    try:
        adapter.install_hooks(root)
    except RuntimeError as e:
        # The only documented RuntimeError is "super-harness-hook not on PATH"
        # (a broken install the user must repair). Surface its message verbatim
        # through format_error rather than letting a traceback escape.
        click.echo(
            format_error(subcommand="adapter install", message=str(e)),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if not ctx.obj.get("quiet"):
        settings_rel = ".claude/settings.json"
        if created_claude_dir:
            click.echo(f"Created {settings_rel} (no .claude/ existed).")
        click.echo(
            f"Installed {name} adapter: PreToolUse gate hook registered in "
            f"{settings_rel}."
        )
    sys.exit(EXIT_OK)
