"""super-harness init — scaffold the `.harness/` workspace.

Creates the canonical directory layout (4 subdirs + 6 skeleton files) per
`engineering-integration` §2.1. Idempotent without `--force`; `--force`
overwrites all skeleton files including user edits. Per `cli-command-surface` §2.3.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import click
import yaml

from super_harness.adapters.install import install_agent_integration
from super_harness.cli.errors import format_error
from super_harness.cli.init_executor import (
    InitExecutor,
    InitOperationError,
    InitOperationResult,
    InitOperations,
)
from super_harness.cli.init_github import (
    GithubFileError,
    GithubFileKind,
    GithubFilePlan,
    GithubKeepReason,
    GithubPlan,
    apply_github_file,
    inspect_github_files,
    resolve_github_plan,
)
from super_harness.cli.init_plan import (
    GithubFileDecision,
    HarnessState,
    InitPlan,
    InitPlanValidationError,
    InitRequest,
    InteractionMode,
    ReviewWrite,
    inspect_workspace,
)
from super_harness.core.clock import utc_now_iso
from super_harness.engineering.agents_md import AgentsMdInjectionError
from super_harness.engineering.gitignore_injector import (
    GitignoreInjectionError,
    inject_gitignore_block,
)
from super_harness.engineering.operation_log import write_operation_log
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


def detect_runtime_terminal_capabilities(
    stdin: Any,
    stdout: Any,
    environ: Mapping[str, str],
) -> Any:
    """Lazily load the Questionary/Rich boundary only when init executes."""

    from super_harness.cli.init_ui import detect_runtime_terminal_capabilities as detect

    return detect(stdin, stdout, environ)


def create_init_ui(capabilities: Any, **kwargs: Any) -> Any:
    """Preserve an injectable command seam without eagerly importing Questionary."""

    from super_harness.cli.init_ui import create_init_ui as create

    return create(capabilities, **kwargs)


def check_gh() -> None:
    """Lazily run the GitHub CLI preflight while preserving the patch seam."""
    from super_harness.engineering.gh import check_gh as _check_gh

    _check_gh()


def enable_repo_merge_settings() -> None:
    """Lazily enable repository settings while preserving the patch seam."""
    from super_harness.engineering.gh import (
        enable_repo_merge_settings as _enable_repo_merge_settings,
    )

    _enable_repo_merge_settings()


def _gh_error_type() -> type[GhError]:
    """Resolve the GitHub integration error type only on an executed error path."""
    from super_harness.engineering.gh import GhError

    return GhError


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


def build_init_operations(
    *,
    ctx: click.Context,
    request: InitRequest,
    github_plan: GithubPlan | None,
) -> InitOperations:
    """Adapt the established init helpers into prompt-free executor operations."""

    root = request.workspace
    harness = root / ".harness"

    def scaffold(_plan: InitPlan) -> InitOperationResult:
        harness.mkdir(parents=True, exist_ok=True)
        (harness / "events.jsonl").touch()
        for subdir in (
            "sensor-results",
            "verification-results",
            "operation-logs",
            "pending-reviews",
        ):
            (harness / subdir).mkdir(exist_ok=True)
        return InitOperationResult("Scaffolded .harness and runtime directories.")

    def skeleton_config(_plan: InitPlan) -> InitOperationResult:
        try:
            skeletons = _skeleton_files()
            for name, content in skeletons.items():
                if name == "review-governance.yaml":
                    continue
                path = harness / name
                if path.exists() and not request.force:
                    continue
                path.write_text(content, encoding="utf-8")
        except (OSError, click.ClickException) as error:
            raise InitOperationError(
                str(error),
                exit_code=EXIT_GENERIC,
                recovery_command="super-harness init --force",
            ) from error
        return InitOperationResult("Wrote skeleton configuration.")

    def review_config(plan: InitPlan) -> InitOperationResult:
        if plan.review_write is ReviewWrite.PRESERVE:
            return InitOperationResult("Preserved existing review configuration.")
        models = tuple(f"{source}={model}" for source, model in plan.review_models.items())
        try:
            _configure_review_producers(root, plan.review_producers, models)
        except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as error:
            raise InitOperationError(
                f"could not configure review producers: {error}",
                exit_code=EXIT_GENERIC,
                hint="Select an installed producer and pass one explicit model per source.",
            ) from error
        verb = "Reset" if plan.review_write is ReviewWrite.RESET else "Configured"
        return InitOperationResult(f"{verb} review configuration.")

    def agent_integrations(plan: InitPlan) -> InitOperationResult:
        configured: list[str] = []
        for integration in plan.integrations:
            try:
                adapter = install_agent_integration(
                    root,
                    integration,
                    plan=plan.integration_plans[integration],
                )
            except (RuntimeError, ValueError, yaml.YAMLError, OSError) as error:
                raise InitOperationError(
                    f"could not configure {integration} integration: {error}",
                    exit_code=EXIT_GENERIC,
                    hint=(
                        "Settings or executable paths may have changed after review; "
                        "rerun init and review the refreshed plan. If a management "
                        "executable is missing, reinstall super-harness first."
                    ),
                ) from error
            configured.append(integration)
            if not (
                request.quiet
                or request.json_output
                or request.interaction_mode is InteractionMode.GUIDED
            ):
                click.echo(f"configured {integration} integration: {adapter.installed_detail()}")
        labels = {"codex": "Codex", "claude-code": "Claude Code"}
        named = [labels.get(integration, integration) for integration in configured]
        if len(named) > 1:
            rendered = f"{', '.join(named[:-1])} and {named[-1]}"
        else:
            rendered = "".join(named)
        detail = (
            f"{rendered} {'integrations' if len(named) > 1 else 'integration'} configured."
            if named
            else "No agent integrations selected."
        )
        return InitOperationResult(detail)

    def agents_md(_plan: InitPlan) -> InitOperationResult:
        agents_path = root / "AGENTS.md"
        try:
            from super_harness.engineering.agents_md_render import (
                render_super_harness_section,
            )

            render_super_harness_section(root, agents_path, __version__)
        except (OSError, AgentsMdInjectionError) as error:
            raise InitOperationError(
                f"scaffolded .harness/ but failed to write AGENTS.md: {error}",
                exit_code=EXIT_GENERIC,
                hint=(
                    "Fix AGENTS.md (permissions / duplicate super-harness markers) "
                    "and re-run `init --force`."
                ),
            ) from error
        return InitOperationResult("Updated AGENTS.md.")

    def gitignore(_plan: InitPlan) -> InitOperationResult:
        try:
            inject_gitignore_block(root / ".gitignore")
        except (OSError, GitignoreInjectionError) as error:
            raise InitOperationError(
                f"scaffolded .harness/ but failed to write .gitignore: {error}",
                exit_code=EXIT_GENERIC,
                hint=(
                    "Fix .gitignore (permissions / duplicate super-harness markers) "
                    "and re-run `init --force`."
                ),
            ) from error
        return InitOperationResult("Updated .gitignore.")

    def github(_plan: InitPlan) -> InitOperationResult:
        if github_plan is None:
            return InitOperationResult("GitHub setup skipped.")
        try:
            warning = _setup_github(
                ctx,
                root,
                harness,
                github_plan,
                compact_output=request.interaction_mode is InteractionMode.GUIDED,
            )
        except GithubFileError as error:
            raise InitOperationError(
                str(error),
                exit_code=EXIT_GENERIC,
                hint=error.hint,
            ) from error
        if warning is not None:
            return InitOperationResult(warning, warned=True)
        return InitOperationResult("GitHub files ensured.")

    return InitOperations(
        scaffold=scaffold,
        skeleton_config=skeleton_config,
        review_config=review_config,
        agent_integrations=agent_integrations,
        agents_md=agents_md,
        gitignore=gitignore,
        github=github,
    )


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
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Skip the final confirmation in interactive mode.",
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
    assume_yes: bool,
) -> None:
    """Initialize a project for super-harness.

    v0.1: --json is not honored by init (bootstrap command produces no
    machine-parseable state).
    """
    root = Path(ctx.obj.get("workspace") or ".").resolve()
    quiet = bool(ctx.obj.get("quiet"))
    json_output = bool(ctx.obj.get("json"))
    capabilities = detect_runtime_terminal_capabilities(sys.stdin, sys.stdout, os.environ)
    try:
        parsed_models = _parse_review_models(review_models)
    except ValueError as error:
        click.echo(
            format_error(
                subcommand="init",
                message=f"could not configure review producers: {error}",
                hint="Select an installed producer and pass one explicit model per source.",
            ),
            err=True,
        )
        ctx.exit(EXIT_GENERIC)

    request = InitRequest(
        workspace=root,
        interaction_mode=capabilities.mode,
        force=force,
        integrations=integrations,
        review_producers=review_producers,
        review_models=parsed_models,
        review_flags_explicit=bool(review_producers or review_models),
        framework=framework,
        no_agent=no_agent,
        setup_github=setup_github,
        assume_yes=assume_yes,
        quiet=quiet,
        json_output=json_output,
    )
    ui = create_init_ui(
        capabilities,
        input_fn=input,
        output_fn=click.echo,
        quiet=quiet or json_output,
    )
    ui.open_session()
    ctx.call_on_close(ui.close_session)
    preflight = inspect_workspace(request)
    harness = root / ".harness"
    if preflight.harness_state is not HarnessState.ABSENT and not force:
        if capabilities.mode is InteractionMode.GUIDED:
            ui.render_already_initialized(harness)
        else:
            click.echo(
                format_error(
                    subcommand="init",
                    message=f".harness/ already exists at {harness}",
                    hint=(
                        "Run `super-harness status` to inspect the existing setup. "
                        "Use `super-harness init --force` to review and reconfigure it."
                    ),
                ),
                err=True,
            )
        ctx.exit(EXIT_NO_CONFIG)

    try:
        wizard = ui.prepare_plan(
            request,
            preflight,
            github_resolver=lambda: _plan_github_setup(
                ctx,
                root,
                compact_output=capabilities.mode is InteractionMode.GUIDED,
            ),
        )
    except KeyboardInterrupt as error:
        raise click.Abort() from error
    except InitPlanValidationError as error:
        message = str(error)
        prefix = (
            "could not configure review producers"
            if "review" in message or "producer" in message or "model" in message
            else "could not prepare init plan"
        )
        click.echo(
            format_error(
                subcommand="init",
                message=f"{prefix}: {message}",
                hint="Select valid, complete choices and re-run init.",
            ),
            err=True,
        )
        ctx.exit(EXIT_GENERIC)

    if getattr(wizard.decision, "value", wizard.decision) == "cancel":
        ui.render_cancelled()
        ctx.exit(EXIT_OK)
    if wizard.plan is None:  # defensive totality for injected UI implementations
        raise click.ClickException("init UI confirmed without a plan")

    result = InitExecutor(
        build_init_operations(ctx=ctx, request=request, github_plan=wizard.github_plan)
    ).apply(wizard.plan, ui.on_step)
    ui.render_outcome(result)
    if not result.success:
        click.echo(
            format_error(
                subcommand="init",
                message=result.message or "initialization failed",
                hint=result.hint,
            ),
            err=True,
        )
        ctx.exit(result.exit_code)

    if capabilities.mode is not InteractionMode.GUIDED:
        click.echo(f"super-harness initialized at {harness}")
    ctx.exit(EXIT_OK)


def _plan_github_setup(
    ctx: click.Context,
    root: Path,
    *,
    compact_output: bool = False,
) -> GithubPlan:
    """Resolve every GitHub file conflict before the first init write."""

    advise = not (compact_output or bool(ctx.obj.get("quiet")) or bool(ctx.obj.get("json")))
    try:
        check_gh()
    except _gh_error_type() as e:
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

    try:
        inspection = inspect_github_files(
            root,
            _pull_request_template().encode("utf-8"),
            _workflow_template().encode("utf-8"),
        )
    except GithubFileError as e:
        click.echo(
            format_error(subcommand="init", message=str(e), hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_GENERIC)

    decisions: dict[str, GithubFileDecision] = {}
    keep_reasons: dict[str, GithubKeepReason] = {}
    quiet = bool(ctx.obj.get("quiet"))
    for file in (inspection.pr_template, inspection.workflow):
        if file.decision is not None:
            continue
        relative = file.path.relative_to(root).as_posix()
        write_decision = (
            GithubFileDecision.APPEND
            if file.kind is GithubFileKind.PR_TEMPLATE
            else GithubFileDecision.OVERWRITE
        )
        if quiet:
            decisions[relative] = write_decision
            continue
        prompt = (
            f"Append super-harness metadata placeholder to existing {file.path}?"
            if file.kind is GithubFileKind.PR_TEMPLATE
            else f"Overwrite existing {file.path}?"
        )
        try:
            proceed = click.confirm(prompt, default=True)
        except click.Abort:
            if sys.stdin.isatty():
                raise
            decisions[relative] = GithubFileDecision.KEEP
            keep_reasons[relative] = GithubKeepReason.NON_INTERACTIVE
            if file.kind is GithubFileKind.PR_TEMPLATE:
                message = (
                    "skipped appending the metadata placeholder to existing "
                    f"{file.path} (non-interactive)"
                )
                hint = "Re-run with --quiet to append it, or add the block manually."
            else:
                message = f"skipped overwriting existing {file.path} (non-interactive)"
                hint = "Re-run with --quiet to overwrite, or update the file manually."
            click.echo(
                format_error(subcommand="init", message=message, hint=hint),
                err=True,
            )
            continue
        decisions[relative] = write_decision if proceed else GithubFileDecision.KEEP
        if not proceed:
            keep_reasons[relative] = GithubKeepReason.DECLINED
    return resolve_github_plan(inspection, decisions, keep_reasons)


def _setup_github(
    ctx: click.Context,
    root: Path,
    harness: Path,
    plan: GithubPlan,
    *,
    compact_output: bool = False,
) -> str | None:
    """Phase 12 `--setup-github` flow (engineering-integration §2.6 / §3.1).

    The read-only inspection, ``gh`` preflight, and every conflict prompt have
    already completed in ``_plan_github_setup`` before this apply phase starts.
    This function runs AFTER `.harness/` is scaffolded, BEFORE the final echo:

    1. Apply the resolved PR-template and workflow decisions without prompting.
    2. Best-effort repo settings — a ``GhError`` is non-fatal: write an
       operation-log + advisory to stderr + continue (exit stays 0; AC-7).

    Plain modes print one stdout advisory per substep, preserving the existing
    typed outcomes from `_write_pr_template` / `_write_workflow_file`. Guided
    mode suppresses those raw lines and returns an actionable repository-setting
    warning for the executor renderer instead.
    """
    # Raw advisories stay in plain modes and remain suppressed for quiet / JSON.
    advise = not (compact_output or bool(ctx.obj.get("quiet")) or bool(ctx.obj.get("json")))

    # --- Step 2: write / marker-merge .github/pull_request_template.md ---
    pr_outcome = _write_pr_template(ctx, root, plan.pr_template)
    if advise:
        _echo_outcome(".github/pull_request_template.md", pr_outcome)

    # --- Step 2.5: write .github/workflows/super-harness.yml (Task 14.2) ---
    wf_outcome = _write_workflow_file(ctx, root, plan.workflow)
    if advise:
        _echo_outcome(".github/workflows/super-harness.yml", wf_outcome)

    # --- Step 3: best-effort repo settings (non-fatal) ---
    try:
        enable_repo_merge_settings()
    except _gh_error_type() as e:
        _log_setup_github_failure(harness, e)
        if compact_output:
            return (
                "GitHub repository settings need manual confirmation. "
                "Settings -> General -> Pull Requests."
            )
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
    return None


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


def _write_pr_template(
    ctx: click.Context,
    root: Path,
    plan: GithubFilePlan,
) -> PRTemplateOutcome:
    """Apply a pre-resolved PR-template decision without prompting."""

    _ = (ctx, root)
    return apply_github_file(plan)


def _write_workflow_file(
    ctx: click.Context,
    root: Path,
    plan: GithubFilePlan,
) -> WorkflowOutcome:
    """Apply a pre-resolved workflow decision without prompting."""

    _ = (ctx, root)
    return apply_github_file(plan)


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
