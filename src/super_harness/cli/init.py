# L1 anchor (HG-D self-host) — @capability:capability-ci-templates
"""super-harness init — scaffold the `.harness/` workspace.

Creates the canonical directory layout (4 subdirs + 6 skeleton files) per
`engineering-integration` §2.1. Idempotent without `--force`; `--force`
overwrites all skeleton files including user edits. Per `cli-command-surface` §2.3.
"""
from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path
from typing import Literal

import click
import yaml

from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

# TODO(v0.2): extract shared adapters.yaml persistence so cli.init + cli.adapter
# both import a public helper instead of this private cross-module reference.
from super_harness.cli.adapter import _persist_install_entry
from super_harness.cli.errors import format_error
from super_harness.core.clock import utc_now_iso
from super_harness.engineering.agents_md import AgentsMdInjectionError
from super_harness.engineering.agents_md_render import render_super_harness_section
from super_harness.engineering.gh import GhError, check_gh, enable_repo_merge_settings
from super_harness.engineering.gitignore_injector import (
    GitignoreInjectionError,
    inject_gitignore_block,
)
from super_harness.engineering.operation_log import write_operation_log
from super_harness.engineering.pr_metadata import (
    METADATA_BEGIN,
    METADATA_END,
    parse_metadata_block,
)
from super_harness.exit_codes import (
    EXIT_EXTERNAL_TOOL,
    EXIT_GENERIC,
    EXIT_NO_CONFIG,
    EXIT_OK,
)
from super_harness.version import __version__

_TEMPLATES = files("super_harness.templates")

# S3 fix (OPEN-ITEMS #6): typed outcome literals returned by `_write_pr_template`
# and `_write_workflow_file` so the advisory printed in `_setup_github` honestly
# matches what actually happened (wrote / kept-existing / declined). Chosen over
# generic post-call prints because reality is asymmetric — a no-op idempotent
# branch must NOT report "wrote".
#
# - "wrote"         : new file written (fresh) OR existing file modified.
# - "kept-existing" : byte-identical / idempotent no-op (file left untouched).
# - "declined"      : user declined overwrite/append (file left untouched).
# - "skipped"       : non-interactive EOF without --quiet (file left untouched,
#                     advisory already on stderr).
PRTemplateOutcome = Literal["wrote", "kept-existing", "declined", "skipped"]
WorkflowOutcome = Literal["wrote", "kept-existing", "declined", "skipped"]


def _pull_request_template() -> str:
    """Load the bundled PR template (engineering-integration §2.6)."""
    src = _TEMPLATES.joinpath("pull_request_template.md")
    if not src.is_file():
        raise click.ClickException(
            "super-harness install is corrupt — bundled template "
            "'pull_request_template.md' missing. Reinstall super-harness."
        )
    return src.read_text()


def _workflow_template() -> str:
    """Load the bundled GitHub Actions workflow template (engineering-integration §2.8)."""
    src = _TEMPLATES.joinpath("super_harness_workflow.yml")
    if not src.is_file():
        raise click.ClickException(
            "super-harness install is corrupt — bundled template "
            "'super_harness_workflow.yml' missing. Reinstall super-harness."
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
        "policy.yaml": (
            "# super-harness policy (see sensor-gate-architecture §2.4)\n"
            "\n"
            "# Reviewer strategy (HG-02.C). super-harness never runs the review itself;\n"
            "# the strategy tells the agent how to produce the verdict:\n"
            "#   subagent (default) — dispatch a reviewer subagent (Task tool)\n"
            "#   human              — a person reviews + records `review approve|reject`\n"
            "#   hybrid             — subagent first, escalate to a human on fail/Large\n"
            "# Set `human` when a token budget rules out subagent review for everything.\n"
            "reviewers:\n"
            "  plan-reviewer:\n"
            "    strategy: subagent\n"
            "  code-reviewer:\n"
            "    strategy: subagent\n"
        ),
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
    help="Run gh CLI checks, write .github/pull_request_template.md and "
    ".github/workflows/super-harness.yml, and best-effort enable repo "
    "auto-merge/squash settings (requires gh).",
)
@click.option(
    "--framework",
    type=click.Choice(["openspec", "spec-kit", "superpowers", "plain"]),
    help="Explicit framework; default = auto-detect "
    "(v0.1: no-op placeholder; framework adapters auto-detect at install time.)",
)
@click.option("--force", is_flag=True)
@click.option(
    "--no-agent",
    is_flag=True,
    help="Skip auto-installing the detected agent's gate hook.",
)
@click.pass_context
def init_cmd(
    ctx: click.Context,
    setup_github: bool,
    framework: str | None,
    force: bool,
    no_agent: bool,
) -> None:
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
    # N-4 fix: create all 4 sub-directories per engineering-integration §2.1
    for subdir in (
        "sensor-results",
        "verification-results",
        "operation-logs",
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
    # Auto-install the detected agent adapter's gate hook (one-command onboarding).
    # Runs BEFORE render_super_harness_section so the renderer injects the agent's
    # AGENTS.md subsection from the freshly-persisted adapters.yaml entry. The gate
    # is dormant until a change is active (no active change -> allow), so this never
    # surprises a fresh init by blocking edits. Non-fatal: a missing hook binary
    # warns and leaves the gate uninstalled rather than aborting init. We
    # intentionally do NOT call _merge_verification_checks — claude-code contributes
    # no verification checks, so it would be a no-op (YAGNI).
    if not no_agent:
        agent = ClaudeCodeAdapter()
        if agent.detect(root):
            agent_installed = False
            try:
                agent.install_hooks(root)
                _persist_install_entry(
                    root, name=agent.name, kind="agent", version=agent.version
                )
                agent_installed = True
            except RuntimeError as e:
                # RuntimeError = hook NOT installed (e.g. super-harness-hook off
                # PATH). State: no gate wired, no adapters.yaml entry.
                click.echo(
                    format_error(
                        subcommand="init",
                        message=f"agent gate hook not installed: {e}",
                        hint="reinstall super-harness so super-harness-hook is on "
                             "PATH, then run `super-harness adapter install claude-code`.",
                    ),
                    err=True,
                )
                # Non-fatal: continue init without the gate.
            except yaml.YAMLError as e:
                # YAMLError = hook IS installed but registration failed (corrupt /
                # unreadable .harness/adapters.yaml). State differs from the
                # RuntimeError case, so it is a SEPARATE clause. Still non-fatal —
                # consistent with init's fail-friendly contract elsewhere.
                click.echo(
                    format_error(
                        subcommand="init",
                        message=f"agent gate hook installed but could not be "
                                f"registered in .harness/adapters.yaml: {e}",
                        hint="Fix or remove .harness/adapters.yaml, then run "
                             "`super-harness adapter install claude-code`.",
                    ),
                    err=True,
                )
                # Non-fatal: continue init; the hook is wired, only the registry
                # entry is missing.
            if agent_installed:
                click.echo(
                    "detected Claude Code; registered PreToolUse gate hook in "
                    ".claude/settings.local.json (pass --no-agent to skip)"
                )
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
    # Wire the repo-root .gitignore (S2 fix — OPEN-ITEMS #6): write a
    # marker-bounded block listing the canonical `.harness/` runtime + per-machine
    # `.claude/` paths so
    # `git add -A` after init does not commit auto-generated state. Same
    # marker-discipline contract as AGENTS.md: ≥2 blocks → fail loud (never
    # splice — Phase 7/9/12 data-loss lesson). We do NOT `git add` — staging is
    # the user's call.
    gitignore_path = root / ".gitignore"
    try:
        inject_gitignore_block(gitignore_path)
    except (OSError, GitignoreInjectionError) as e:
        click.echo(
            format_error(
                subcommand="init",
                message=f"scaffolded .harness/ but failed to write .gitignore: {e}",
                hint=(
                    "Fix .gitignore (permissions / duplicate super-harness markers) "
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

    S3 fix (OPEN-ITEMS #6): each substep prints a stdout advisory describing
    what actually happened (typed outcome from `_write_pr_template` /
    `_write_workflow_file`). Suppressed under ``--quiet`` or ``--json``.
    """
    # S3: advisory prints honor --quiet AND --json (init emits no JSON envelope,
    # but prose advisories would pollute JSON-consumer pipelines all the same).
    advise = not (bool(ctx.obj.get("quiet")) or bool(ctx.obj.get("json")))

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
    if advise:
        click.echo("gh CLI: ok")

    # --- Step 2: write / marker-merge .github/pull_request_template.md ---
    pr_outcome = _write_pr_template(ctx, root)
    if advise:
        _echo_outcome(".github/pull_request_template.md", pr_outcome)

    # --- Step 2.5: write .github/workflows/super-harness.yml (Task 14.2) ---
    wf_outcome = _write_workflow_file(ctx, root)
    if advise:
        _echo_outcome(".github/workflows/super-harness.yml", wf_outcome)

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
    else:
        # Only print the positive advisory on the success path. On GhError the
        # existing format_error already informs the user via stderr.
        if advise:
            click.echo("repo merge settings: enabled auto-merge + squash")


def _echo_outcome(label: str, outcome: PRTemplateOutcome | WorkflowOutcome) -> None:
    """Print an honest stdout advisory matching a helper's typed outcome.

    "wrote"         → ``wrote <label>``
    "kept-existing" → ``kept existing <label>``
    "declined"      → ``kept existing <label> (declined overwrite)``
    "skipped"       → ``kept existing <label> (skipped, non-interactive)``
    """
    if outcome == "wrote":
        click.echo(f"wrote {label}")
    elif outcome == "kept-existing":
        click.echo(f"kept existing {label}")
    elif outcome == "declined":
        click.echo(f"kept existing {label} (declined overwrite)")
    elif outcome == "skipped":
        click.echo(f"kept existing {label} (skipped, non-interactive)")


def _write_pr_template(ctx: click.Context, root: Path) -> PRTemplateOutcome:
    """Write or marker-merge ``.github/pull_request_template.md`` (§2.6).

    Returns a typed outcome literal so callers can print an HONEST advisory
    matching what actually happened (S3 fix — OPEN-ITEMS #6):

    - "wrote"         : fresh write OR append-placeholder to existing.
    - "kept-existing" : existing template already has exactly one block (no-op).
    - "declined"      : user said 'n' at the append-confirm prompt.
    - "skipped"       : non-interactive EOF without --quiet (advisory on stderr).

    Branches:
    - File absent → write the bundled template verbatim (no prompt). → "wrote"
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
        return "wrote"

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
        return "kept-existing"

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
            # click.Abort fires on BOTH an interactive Ctrl-C and a
            # non-interactive EOF. A real Ctrl-C (TTY) means "stop" → re-raise →
            # exit 1, consistent with sync.py / `adapter uninstall`'s confirm. A
            # non-interactive EOF (CI without --quiet) cannot prompt → leave the
            # user's file UNTOUCHED (never modify it silently), non-fatal, advise.
            if sys.stdin.isatty():
                raise
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
            return "skipped"
        if not proceed:
            return "declined"  # declined ('n') → leave untouched, non-fatal
    placeholder = f"{METADATA_BEGIN}\n{METADATA_END}\n"
    new = existing.rstrip("\n") + "\n\n" + placeholder
    template_path.write_text(new)
    return "wrote"


def _write_workflow_file(ctx: click.Context, root: Path) -> WorkflowOutcome:
    """Write or overwrite-with-confirm ``.github/workflows/super-harness.yml`` (§2.8).

    Returns a typed outcome literal (S3 fix — OPEN-ITEMS #6):
    - "wrote"         : fresh write OR overwrite of differing existing file.
    - "kept-existing" : byte-identical to bundled (idempotent no-op).
    - "declined"      : user said 'n' at the overwrite-confirm prompt.
    - "skipped"       : non-interactive EOF without --quiet.

    Branches:
    - File absent → write bundled template verbatim (no prompt; ``mkdir -p`` first).
    - File present + byte-identical to bundled → idempotent no-op.
    - File present + differs → confirm overwrite (unless global ``--quiet``);
      non-TTY EOF leaves untouched + advisory (non-fatal);
      TTY Ctrl-C re-raises (exit 1).
    - Read of existing file: catch ``(OSError, UnicodeDecodeError)`` → friendly
      error, EXIT_GENERIC (UnicodeDecodeError is a ValueError, not OSError —
      the project's recurring error-family bug class).
    """
    workflows_dir = root / ".github" / "workflows"
    workflow_path = workflows_dir / "super-harness.yml"
    bundled = _workflow_template()

    if not workflow_path.exists():
        workflows_dir.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text(bundled)
        return "wrote"

    try:
        existing = workflow_path.read_text()
    except (OSError, UnicodeDecodeError) as e:
        # error-family: UnicodeDecodeError is a ValueError (not OSError), so both
        # must be caught — a non-UTF-8 / unreadable existing workflow file must
        # surface a friendly error, never a raw traceback.
        click.echo(
            format_error(
                subcommand="init",
                message=f"could not read existing {workflow_path}: {e}",
                hint="Ensure the file is UTF-8 and readable, then re-run.",
            ),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    if existing == bundled:
        return "kept-existing"  # byte-identical → idempotent no-op

    quiet = bool(ctx.obj.get("quiet"))
    if not quiet:
        try:
            proceed = click.confirm(
                f"Overwrite existing {workflow_path}?",
                default=True,
            )
        except click.Abort:
            # click.Abort fires on BOTH an interactive Ctrl-C and a
            # non-interactive EOF. A real Ctrl-C (TTY) means "stop" → re-raise →
            # exit 1, consistent with _write_pr_template. A non-interactive EOF
            # (CI without --quiet) cannot prompt → leave the file UNTOUCHED
            # (never modify it silently), non-fatal, advise.
            if sys.stdin.isatty():
                raise
            click.echo(
                format_error(
                    subcommand="init",
                    message=(
                        f"skipped overwriting existing {workflow_path} (non-interactive)"
                    ),
                    hint="Re-run with --quiet to overwrite, or update the file manually.",
                ),
                err=True,
            )
            return "skipped"
        if not proceed:
            return "declined"  # declined ('n') → leave untouched, non-fatal

    workflow_path.write_text(bundled)
    return "wrote"


def _log_setup_github_failure(harness: Path, error: GhError) -> None:
    """Write a plain-text operation-log for a failed repo-settings attempt (AC-7).

    Body is a human-read audit trail (NOT JSON): the attempted commands +
    captured detail + a one-line outcome. The on-disk mechanism (path
    composition, ``:``-sanitization, OSError swallow) is shared via
    ``engineering.operation_log.write_operation_log``.
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
    write_operation_log(harness, "setup-github", body)
