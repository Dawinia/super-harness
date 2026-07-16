"""super-harness init — scaffold the `.harness/` workspace.

Creates the canonical directory layout (4 subdirs + 6 skeleton files) per
`engineering-integration` §2.1. Idempotent without `--force`; `--force`
overwrites all skeleton files including user edits. Per `cli-command-surface` §2.3.
"""

from __future__ import annotations

import shutil
import sys
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import click
import yaml

from super_harness.adapters.install import install_agent_integration
from super_harness.cli.errors import format_error
from super_harness.core.clock import utc_now_iso
from super_harness.engineering.agents_md import AgentsMdInjectionError
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

if TYPE_CHECKING:
    from super_harness.engineering.gh import GhError

_TEMPLATES = files("super_harness.templates")

_REVIEW_PRODUCERS: dict[str, dict[str, object]] = {
    "codex-cli": {
        "source": "codex",
        "executable": "codex",
        "agent_options": {
            "reasoning_effort": "medium",
            "sandbox": "read-only",
        },
    },
    "claude-cli": {
        "source": "claude",
        "executable": "claude",
        "agent_options": {"effort": "medium"},
    },
}


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def _prompt_multi_select(
    title: str,
    options: tuple[str, ...],
) -> tuple[str, ...]:
    if not options:
        click.echo(f"{title}: no installed options detected")
        return ()
    click.echo(f"{title}:")
    for index, option in enumerate(options, start=1):
        click.echo(f"  {index}. {option} (recommended)")
    default = ",".join(str(index) for index in range(1, len(options) + 1))
    raw = click.prompt(
        "Select comma-separated numbers, or 'none'",
        default=default,
        show_default=True,
    ).strip()
    if raw.lower() == "none":
        return ()
    selected: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        try:
            index = int(token)
        except ValueError as exc:
            raise click.ClickException(
                f"invalid selection {token!r}; enter comma-separated numbers"
            ) from exc
        if index < 1 or index > len(options):
            raise click.ClickException(f"selection {index} is out of range 1..{len(options)}")
        option = options[index - 1]
        if option not in selected:
            selected.append(option)
    return tuple(selected)


def _resolve_init_selections(
    integrations: tuple[str, ...],
    review_producers: tuple[str, ...],
    review_models: tuple[str, ...],
    *,
    no_agent: bool,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Resolve optional TTY selections; non-TTY values pass through unchanged."""

    if not _stdin_is_tty():
        return integrations, review_producers, review_models

    resolved_integrations = integrations
    if not resolved_integrations and not no_agent:
        detected_integrations: list[str] = []
        if shutil.which("codex"):
            detected_integrations.append("codex")
        if shutil.which("claude"):
            detected_integrations.append("claude-code")
        resolved_integrations = _prompt_multi_select(
            "Coding-agent integrations", tuple(detected_integrations)
        )

    resolved_producers = review_producers
    if not resolved_producers:
        detected_producers: list[str] = []
        if shutil.which("codex"):
            detected_producers.append("codex-cli")
        if shutil.which("claude"):
            detected_producers.append("claude-cli")
        resolved_producers = _prompt_multi_select("Review producers", tuple(detected_producers))

    models = _parse_review_models(review_models)
    for producer in resolved_producers:
        source = str(_REVIEW_PRODUCERS[producer]["source"])
        if source not in models:
            models[source] = click.prompt(f"Explicit model for {source}", type=str).strip()
            if not models[source]:
                raise click.ClickException(f"explicit model for {source!r} cannot be empty")
    resolved_models = tuple(f"{source}={model}" for source, model in models.items())
    return resolved_integrations, resolved_producers, resolved_models


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
    return src.read_text(encoding="utf-8")


def _workflow_template() -> str:
    """Load the bundled GitHub Actions workflow template (engineering-integration §2.8)."""
    src = _TEMPLATES.joinpath("super_harness_workflow.yml")
    if not src.is_file():
        raise click.ClickException(
            "super-harness install is corrupt — bundled template "
            "'super_harness_workflow.yml' missing. Reinstall super-harness."
        )
    return src.read_text(encoding="utf-8")


def _source_paths_default() -> str:
    src = _TEMPLATES.joinpath("source_paths_defaults.yaml")
    if src.is_file():
        return src.read_text(encoding="utf-8")
    return "source_paths:\n  include:\n    - '**/*'\n  exclude:\n    - 'docs/**'\n"


def _derived_docs_default() -> str:
    src = _TEMPLATES.joinpath("derived_docs_defaults.yaml")
    try:
        return src.read_text(encoding="utf-8")
    except OSError:
        return "derived_docs: []\n"


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
    return src.read_text(encoding="utf-8")


def _skeleton_files() -> dict[str, str]:
    return {
        "review-governance.yaml": (
            "# Shared review governance. Commit this file.\n"
            "# Local producer/model choices live in the gitignored\n"
            "# .harness/review-profiles.local.yaml file.\n"
            "version: 1\n"
            "review:\n"
            "  base_branch: main\n"
            "  sources:\n"
            "    human:\n"
            "      kind: human\n"
            "  roles:\n"
            "    plan-reviewer:\n"
            "      participants: [human]\n"
            "      min_independent: 1\n"
            "      max_automatic_rounds_per_epoch: 2\n"
            "    code-reviewer:\n"
            "      participants: [human]\n"
            "      min_independent: 1\n"
            "      max_automatic_rounds_per_epoch: 2\n"
            "      # blocking_severity: major   # optional; one of blocker|major|minor\n"
            "      #   (default major). A code-review round rejects only when a finding\n"
            "      #   is at or above this severity; findings below it pass with the\n"
            "      #   finding left open (recorded + surfaced by `super-harness report`),\n"
            "      #   not forcing a re-review round. Plan review always rejects on any\n"
            "      #   checklist fail (its findings are not tracked in the report).\n"
            "  require_distinct_model_families: false\n"
        ),
        "sensors.yaml": "sensors: []\n",
        "gates.yaml": (
            "gates:\n  - pre-tool-use\n  - pre-commit\n  - pre-push\n  - pr-open\n  - pr-merge\n"
        ),
        "source-paths.yaml": _source_paths_default(),
        "derived-docs.yaml": _derived_docs_default(),
        "verification.yaml": _verification_default(),
        "conventions.md": "# Project conventions (referenced by reviewer sensors)\n",
    }


def _parse_review_models(values: tuple[str, ...]) -> dict[str, str]:
    models: dict[str, str] = {}
    for value in values:
        source, separator, model = value.partition("=")
        if not separator or not source or not model:
            raise ValueError(
                "--review-model must use SOURCE=MODEL, for example --review-model codex=gpt-review"
            )
        if source in models:
            raise ValueError(f"duplicate --review-model source {source!r}")
        models[source] = model
    return models


def _configure_review_producers(
    root: Path,
    producers: tuple[str, ...],
    model_values: tuple[str, ...],
) -> None:
    """Write governance/profile selections without executing a producer."""

    if len(set(producers)) != len(producers):
        raise ValueError("duplicate --review-producer selection")
    models = _parse_review_models(model_values)
    unknown_models = set(models)
    selected_sources: list[str] = []
    profile_sources: dict[str, object] = {}
    governance_sources: dict[str, object] = {}
    for producer in producers:
        definition = _REVIEW_PRODUCERS[producer]
        source = str(definition["source"])
        executable = str(definition["executable"])
        unknown_models.discard(source)
        model = models.get(source)
        if model is None:
            raise ValueError(
                f"--review-producer {producer} requires --review-model {source}=<model>"
            )
        if shutil.which(executable) is None:
            raise ValueError(
                f"selected review producer {producer!r} is not installed "
                f"({executable!r} not found on PATH); super-harness does not install it"
            )
        selected_sources.append(source)
        governance_sources[source] = {"kind": "automated"}
        raw_options = definition["agent_options"]
        if not isinstance(raw_options, dict):
            raise ValueError(f"built-in review producer {producer!r} has invalid agent_options")
        profile_sources[source] = {
            "protocol": producer,
            "model": model,
            "cost_class": "standard",
            "agent_options": dict(raw_options),
        }
    if unknown_models:
        source = sorted(unknown_models)[0]
        raise ValueError(f"--review-model source {source!r} has no selected --review-producer")

    governance_sources["human"] = {"kind": "human"}
    participants = selected_sources or ["human"]
    role = {
        "participants": participants,
        "min_independent": len(participants),
        "max_automatic_rounds_per_epoch": 2,
    }
    governance = {
        "version": 1,
        "review": {
            "base_branch": "main",
            "sources": governance_sources,
            "roles": {
                "plan-reviewer": dict(role),
                "code-reviewer": dict(role),
            },
            "require_distinct_model_families": False,
        },
    }
    governance_path = root / ".harness" / "review-governance.yaml"
    governance_path.write_text(yaml.safe_dump(governance, sort_keys=False), encoding="utf-8")
    profile_path = root / ".harness" / "review-profiles.local.yaml"
    if profile_sources:
        profile_path.write_text(
            yaml.safe_dump({"version": 1, "sources": profile_sources}, sort_keys=False),
            encoding="utf-8",
        )
    else:
        profile_path.unlink(missing_ok=True)


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
@click.option(
    "--integration",
    "integrations",
    multiple=True,
    type=click.Choice(["codex", "claude-code"]),
    help="Coding-agent integration to configure; repeat for multiple selections.",
)
@click.option(
    "--review-producer",
    "review_producers",
    multiple=True,
    type=click.Choice(sorted(_REVIEW_PRODUCERS)),
    help="Local review producer protocol to configure; repeat for multiple selections.",
)
@click.option(
    "--review-model",
    "review_models",
    multiple=True,
    metavar="SOURCE=MODEL",
    help="Explicit model for a selected review source; repeat per source.",
)
@click.pass_context
def init_cmd(
    ctx: click.Context,
    setup_github: bool,
    framework: str | None,
    force: bool,
    no_agent: bool,
    integrations: tuple[str, ...],
    review_producers: tuple[str, ...],
    review_models: tuple[str, ...],
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
    interactive = _stdin_is_tty()
    explicit_review_selection = bool(review_producers or review_models)
    governance_path = harness / "review-governance.yaml"
    configure_review = not governance_path.is_file() or explicit_review_selection or interactive
    integrations, review_producers, review_models = _resolve_init_selections(
        integrations,
        review_producers,
        review_models,
        no_agent=no_agent,
    )
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
        if name == "review-governance.yaml" and path.exists() and not configure_review:
            continue
        if path.exists() and not force:
            continue
        path.write_text(content, encoding="utf-8")
    if configure_review:
        try:
            _configure_review_producers(root, review_producers, review_models)
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as e:
            click.echo(
                format_error(
                    subcommand="init",
                    message=f"could not configure review producers: {e}",
                    hint=("Select an installed producer and pass one explicit model per source."),
                ),
                err=True,
            )
            sys.exit(EXIT_GENERIC)
    for integration in integrations:
        try:
            adapter = install_agent_integration(root, integration)
        except (RuntimeError, ValueError, yaml.YAMLError, OSError) as e:
            click.echo(
                format_error(
                    subcommand="init",
                    message=f"could not configure {integration} integration: {e}",
                    hint=(
                        f"Install the agent and super-harness hook, then run "
                        f"`super-harness adapter install {integration}`."
                    ),
                ),
                err=True,
            )
            sys.exit(EXIT_GENERIC)
        if not (ctx.obj.get("quiet") or ctx.obj.get("json")):
            click.echo(f"configured {integration} integration: {adapter.installed_detail()}")
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
        from super_harness.engineering.agents_md_render import (
            render_super_harness_section,
        )

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
    from super_harness.engineering.gh import (
        GhError,
        check_gh,
        enable_repo_merge_settings,
    )

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
        template_path.write_text(_pull_request_template(), encoding="utf-8")
        return "wrote"

    try:
        existing = template_path.read_text(encoding="utf-8")
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
    template_path.write_text(new, encoding="utf-8")
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
        workflow_path.write_text(bundled, encoding="utf-8")
        return "wrote"

    try:
        existing = workflow_path.read_text(encoding="utf-8")
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
                    message=(f"skipped overwriting existing {workflow_path} (non-interactive)"),
                    hint="Re-run with --quiet to overwrite, or update the file manually.",
                ),
                err=True,
            )
            return "skipped"
        if not proceed:
            return "declined"  # declined ('n') → leave untouched, non-fatal

    workflow_path.write_text(bundled, encoding="utf-8")
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
