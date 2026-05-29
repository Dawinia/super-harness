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

from super_harness.cli.errors import format_error
from super_harness.cli.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
)
from super_harness.core.clock import utc_now_iso
from super_harness.engineering.agents_md import AgentsMdInjectionError
from super_harness.engineering.agents_md_render import render_super_harness_section
from super_harness.engineering.gh import GhError, check_gh, enable_repo_merge_settings
from super_harness.engineering.pr_metadata import (
    METADATA_BEGIN,
    METADATA_END,
    parse_metadata_block,
)
from super_harness.version import __version__

_TEMPLATES = files("super_harness.templates")


def _pull_request_template() -> str:
    """Load the bundled PR template (engineering-integration §2.6)."""
    src = _TEMPLATES.joinpath("pull_request_template.md")
    if not src.is_file():
        raise click.ClickException(
            "super-harness install is corrupt — bundled template "
            "'pull_request_template.md' missing. Reinstall super-harness."
        )
    return src.read_text()


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
    help="Run gh CLI checks, write .github/pull_request_template.md, and "
    "best-effort enable repo auto-merge/squash settings (requires gh).",
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
    # --framework remains a CLI-surface placeholder in v0.1 (Phase 1 convention:
    # accept the flag, mark it unread, advertise the no-op via the --help caveat —
    # NO runtime stderr notice). Phase 4 wires --framework detection.
    # --setup-github is wired in Phase 12 (gh checks + PR template + repo settings).
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
    # The injectors' atomic-write / CRLF-safety guarantees are documented in the
    # `super_harness.engineering.agents_md` module docstring (single source of
    # truth). Idempotent: a re-render (e.g. --force) replaces the existing section
    # rather than duplicating it.
    agents_path = root / "AGENTS.md"
    # .harness/ is fully scaffolded above. An OSError (unwritable AGENTS.md / full
    # disk) or AgentsMdInjectionError (duplicate super-harness outer block) here
    # must surface through format_error like the .harness-exists branch — never a
    # raw traceback. `init --force` re-renders the section in place, so the
    # recovery contract is "fix AGENTS.md, re-run init --force".
    # A re-render (`--force`) rewrites the super-harness section back to the
    # base template (the no-agent anchor) — but it then RE-INJECTS every adapter
    # still registered in `.harness/adapters.yaml`, so installed agent/framework
    # guidance is never lost (full `--force` loop closure). On a fresh init (no
    # adapters.yaml) re-injection is a no-op, so the render is unconditional. The
    # shared renderer (init + sync SSOT) lets OSError / AgentsMdInjectionError
    # propagate into THIS try's AGENTS.md envelope (fail-loud); only its internal
    # adapters.yaml load is non-fatal (advisory + skip) — see the renderer module.
    try:
        render_super_harness_section(root, agents_path, __version__)
    except (OSError, AgentsMdInjectionError) as e:
        click.echo(
            format_error(
                subcommand="init",
                message=f"scaffolded .harness/ but failed to write AGENTS.md: {e}",
                hint=(
                    "Fix AGENTS.md (permissions / duplicate super-harness markers) "
                    "and re-run `init --force`."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    if setup_github:
        _setup_github(ctx, root, harness)
    click.echo(f"super-harness initialized at {harness}")
    sys.exit(EXIT_OK)


def _setup_github(ctx: click.Context, root: Path, harness: Path) -> None:
    """Phase 12 `--setup-github` flow (engineering-integration §2.6 / §3.1).

    Sequence (runs AFTER `.harness/` is scaffolded, BEFORE the final echo):

    1. ``check_gh()`` first — any ``GhError`` aborts with EXIT_EXTERNAL_TOOL (4),
       BEFORE any ``.github/`` write (AC-1: no silent fallback). The partial
       `.harness/` left behind is acceptable (init is re-runnable).
    2. Write / marker-merge ``<root>/.github/pull_request_template.md`` (§2.6).
    3. Best-effort repo settings — a ``GhError`` is non-fatal: write an
       operation-log + advisory to stderr + continue (exit stays 0; AC-7).
    """
    # --- Step 1: gh checks first (before any .github/ write) ---
    try:
        check_gh()
    except GhError as e:
        click.echo(
            format_error(
                subcommand="init",
                message=f"--setup-github requires gh CLI: {e}",
                hint=(
                    "Install (`brew install gh`), authenticate (`gh auth login`), "
                    "and grant workflow scope (`gh auth refresh -s workflow`)."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_EXTERNAL_TOOL)

    # --- Step 2: write / marker-merge .github/pull_request_template.md ---
    _write_pr_template(ctx, root)

    # --- Step 3: best-effort repo settings (non-fatal) ---
    try:
        enable_repo_merge_settings()
    except GhError as e:
        _log_setup_github_failure(harness, e)
        click.echo(
            format_error(
                subcommand="init",
                message=f"could not auto-enable repo merge settings: {e}",
                hint=(
                    "Enable manually in Settings -> General -> Pull Requests "
                    "(Allow auto-merge + Allow squash merging). "
                    "See .harness/operation-logs/setup-github/ for details."
                ),
            ),
            err=True,
        )


def _write_pr_template(ctx: click.Context, root: Path) -> None:
    """Write or marker-merge ``.github/pull_request_template.md`` (§2.6).

    - File absent → write the bundled template verbatim (no prompt).
    - File present → marker-aware merge: ensure exactly one metadata placeholder
      block exists. ``block_count >= 2`` → FAIL LOUD (never splice — the AGENTS.md
      greedy-regex data-loss lesson). Already exactly one → no-op (idempotent).
      Modifying an EXISTING file prompts (unless global ``--quiet``); decline →
      leave untouched (non-fatal, continue).
    """
    gh_dir = root / ".github"
    template_path = gh_dir / "pull_request_template.md"

    if not template_path.exists():
        gh_dir.mkdir(parents=True, exist_ok=True)
        template_path.write_text(_pull_request_template())
        return

    try:
        existing = template_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        # error-family: UnicodeDecodeError is a ValueError (not OSError), so both
        # must be caught — a non-UTF-8 / unreadable existing template must surface
        # a friendly error, never a raw traceback.
        click.echo(
            format_error(
                subcommand="init",
                message=f"could not read existing {template_path}: {e}",
                hint="Ensure the file is UTF-8 and readable, then re-run.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)
    block = parse_metadata_block(existing)

    if block.block_count >= 2:
        click.echo(
            format_error(
                subcommand="init",
                message=(
                    f"{template_path} has {block.block_count} super-harness "
                    f"metadata blocks; refusing to splice (manual cleanup required)."
                ),
                hint=(
                    "Remove the duplicate "
                    "`<!-- super-harness:metadata -->` … "
                    "`<!-- /super-harness:metadata -->` block(s); "
                    "exactly one is expected."
                ),
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if block.block_count == 1:
        # Already has exactly one placeholder block — idempotent no-op.
        return

    # Exactly zero blocks: append one placeholder, preserving the user's content.
    # Modifying an existing file → overwrite-confirm unless --quiet.
    quiet = bool(ctx.obj.get("quiet"))
    if not quiet:
        try:
            proceed = click.confirm(
                f"Append super-harness metadata placeholder to existing {template_path}?",
                default=True,
            )
        except click.Abort:
            # Non-interactive (CI / no TTY) or ^C → we cannot prompt. Leave the
            # user's file UNTOUCHED (never modify it silently), non-fatal, and
            # advise how to proceed. (Without this, Click raises Abort → exit 1.)
            click.echo(
                format_error(
                    subcommand="init",
                    message=(
                        f"skipped appending the metadata placeholder to existing "
                        f"{template_path} (non-interactive)"
                    ),
                    hint="Re-run with --quiet to append it, or add the block manually.",
                ),
                err=True,
            )
            return
        if not proceed:
            return  # declined → leave untouched, non-fatal
    placeholder = f"{METADATA_BEGIN}\n{METADATA_END}\n"
    new = existing.rstrip("\n") + "\n\n" + placeholder
    template_path.write_text(new)


def _log_setup_github_failure(harness: Path, error: GhError) -> None:
    """Write a plain-text operation-log for a failed repo-settings attempt (AC-7).

    Path: ``<harness>/operation-logs/setup-github/<utc-ts>.log`` (``:`` in the
    timestamp is sanitized to ``-`` for cross-filesystem portability). Body is a
    human-read audit trail (NOT JSON): the attempted commands + captured detail +
    a one-line outcome.
    """
    body = (
        "operation: setup-github (enable repo merge settings)\n"
        f"timestamp: {utc_now_iso()}\n"
        "command: gh api -X PATCH /repos/{owner}/{repo} -f allow_auto_merge=true\n"
        "command: gh api -X PATCH /repos/{owner}/{repo} -f allow_squash_merge=true\n"
        f"detail: {error}\n"
        "outcome: FAILED — repo merge settings not auto-enabled (non-fatal; "
        "configure manually in Settings -> General -> Pull Requests).\n"
    )
    try:
        log_dir = harness / "operation-logs" / "setup-github"
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = utc_now_iso().replace(":", "-")
        (log_dir / f"{ts}.log").write_text(body)
    except OSError:
        # Operation-logging is itself best-effort: a log-write failure (full disk,
        # unwritable .harness/) must NOT turn the non-fatal repo-settings
        # degradation into a hard init failure. Swallow — the caller already
        # printed the actionable advisory to stderr.
        pass
