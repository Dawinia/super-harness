from __future__ import annotations

import ast
from pathlib import Path

import pytest

from super_harness.cli.init_plan import (
    ExistingFileDecision,
    FileAction,
    GitHubDecision,
    GithubFileDecision,
    HarnessState,
    InitChoices,
    InitPlanValidationError,
    InitRequest,
    InteractionMode,
    ReviewWrite,
    build_init_plan,
    inspect_workspace,
)


def _lookup(*available: str):
    installed = frozenset((*available, "super-harness-hook", "super-harness"))
    return lambda executable: f"/bin/{executable}" if executable in installed else None


def _request(
    workspace: Path,
    *,
    mode: InteractionMode = InteractionMode.NON_INTERACTIVE,
    force: bool = False,
    integrations: tuple[str, ...] = (),
    producers: tuple[str, ...] = (),
    models: dict[str, str] | None = None,
    review_flags_explicit: bool = False,
    setup_github: bool = False,
) -> InitRequest:
    return InitRequest(
        workspace=workspace,
        interaction_mode=mode,
        force=force,
        integrations=integrations,
        review_producers=producers,
        review_models={} if models is None else models,
        review_flags_explicit=review_flags_explicit,
        setup_github=setup_github,
    )


def _write_review_config(
    workspace: Path,
    *,
    producer: str = "codex-cli",
    source: str = "codex",
    model: str = "gpt-review",
) -> tuple[bytes, bytes]:
    harness = workspace / ".harness"
    harness.mkdir(exist_ok=True)
    (harness / "events.jsonl").write_text("")
    governance = (
        "version: 1\n"
        "review:\n"
        "  base_branch: main\n"
        "  sources:\n"
        f"    {source}:\n"
        "      kind: automated\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        f"      participants: [{source}]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds: 2\n"
        "    code-reviewer:\n"
        f"      participants: [{source}]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds: 2\n"
        "  require_distinct_model_families: false\n"
    ).encode()
    profile = (
        "version: 1\n"
        "sources:\n"
        f"  {source}:\n"
        f"    protocol: {producer}\n"
        f"    model: {model}\n"
        "    cost_class: standard\n"
        "    agent_options: {}\n"
    ).encode()
    (harness / "review-governance.yaml").write_bytes(governance)
    (harness / "review-profiles.local.yaml").write_bytes(profile)
    return governance, profile


def _review_actions(plan):
    return {
        action.path.name: action
        for action in plan.file_actions
        if action.path.name in {"review-governance.yaml", "review-profiles.local.yaml"}
    }


def _write_user_models(home: Path) -> None:
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model = "gpt-configured"\n', encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(
        '{"model": "opus-configured"}', encoding="utf-8"
    )


def test_fresh_plan_leaves_review_authority_to_external_recognition(
    tmp_path: Path,
) -> None:
    request = _request(
        tmp_path,
        integrations=("codex",),
        setup_github=True,
    )

    preflight = inspect_workspace(request, executable_lookup=_lookup("codex", "gh"))
    plan = build_init_plan(request, preflight, InitChoices())

    assert preflight.harness_state is HarnessState.ABSENT
    assert plan.review_write is ReviewWrite.PRESERVE
    assert plan.integrations == ("codex",)
    assert plan.review_producers == ()
    assert dict(plan.review_models) == {}
    assert plan.github_decision is GitHubDecision.CREATE
    by_path = {action.path.as_posix(): action for action in plan.file_actions}
    assert by_path[".harness/review-recognition.yaml"].action is FileAction.CREATE
    assert by_path[".harness/review-governance.yaml"].action is FileAction.SKIP
    assert by_path[".harness/review-profiles.local.yaml"].action is FileAction.SKIP


def test_preflight_never_discovers_reviewer_models_or_reads_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Path,
        "home",
        classmethod(lambda cls: pytest.fail("noninteractive init resolved the user home")),
    )

    result = inspect_workspace(
        _request(tmp_path, mode=InteractionMode.NON_INTERACTIVE),
        executable_lookup=_lookup("codex", "claude"),
    )

    assert dict(result.reviewer_model_candidates) == {}
    assert dict(result.reviewer_model_errors) == {}


@pytest.mark.parametrize(
    ("governance", "profile"),
    [
        (b"not: [yaml", b"also: [broken"),
        (b"version: 999\nunknown: true\n", b"version: 999\nunknown: true\n"),
    ],
)
def test_noninteractive_force_without_review_flags_preserves_opaque_review_bytes(
    tmp_path: Path, governance: bytes, profile: bytes
) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "events.jsonl").write_text("")
    (harness / "review-governance.yaml").write_bytes(governance)
    (harness / "review-profiles.local.yaml").write_bytes(profile)
    request = _request(tmp_path, force=True)

    preflight = inspect_workspace(request, executable_lookup=_lookup())
    plan = build_init_plan(request, preflight, InitChoices())

    assert preflight.review_config_error is None
    assert preflight.persisted_review_producers == ()
    actions = _review_actions(plan)
    assert plan.review_write is ReviewWrite.PRESERVE
    assert actions["review-governance.yaml"].action is FileAction.PRESERVE
    assert actions["review-governance.yaml"].content == governance
    assert actions["review-profiles.local.yaml"].action is FileAction.PRESERVE
    assert actions["review-profiles.local.yaml"].content == profile


@pytest.mark.parametrize(
    ("producers", "models"),
    [
        (("codex-cli",), {}),
        ((), {"codex": "gpt-review"}),
        (("codex-cli",), {"claude": "claude-review"}),
    ],
)
def test_review_configuration_flags_are_retired(
    tmp_path: Path,
    producers: tuple[str, ...],
    models: dict[str, str],
) -> None:
    request = _request(
        tmp_path,
        producers=producers,
        models=models,
        review_flags_explicit=True,
    )

    with pytest.raises(InitPlanValidationError, match="configuration is retired"):
        preflight = inspect_workspace(request, executable_lookup=_lookup())
        build_init_plan(request, preflight, InitChoices())


def test_review_choices_are_retired(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(request, executable_lookup=_lookup())

    with pytest.raises(InitPlanValidationError, match="configuration is retired"):
        build_init_plan(
            request,
            preflight,
            InitChoices(review_models={"codex": "gpt-review"}),
        )


def test_existing_review_files_are_opaque_and_always_preserved(tmp_path: Path) -> None:
    governance, profile = _write_review_config(tmp_path)
    request = _request(tmp_path, mode=InteractionMode.GUIDED, force=True)
    plan = build_init_plan(
        request,
        inspect_workspace(request, executable_lookup=_lookup()),
        InitChoices(),
    )

    actions = _review_actions(plan)
    assert plan.review_write is ReviewWrite.PRESERVE
    assert actions["review-governance.yaml"].action is FileAction.PRESERVE
    assert actions["review-governance.yaml"].content == governance
    assert actions["review-profiles.local.yaml"].action is FileAction.PRESERVE
    assert actions["review-profiles.local.yaml"].content == profile


def test_fresh_interactive_plan_has_no_reviewer_defaults(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=InteractionMode.LINE)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))
    plan = build_init_plan(request, preflight, InitChoices())

    assert preflight.detected_integrations == ("codex",)
    assert preflight.detected_review_producers == ()
    assert plan.integrations == ("codex",)
    assert plan.review_producers == ()
    assert dict(plan.review_models) == {}


def test_unavailable_integration_can_be_explicit_but_is_not_preselected(tmp_path: Path) -> None:
    interactive = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(interactive, executable_lookup=_lookup())
    default_plan = build_init_plan(interactive, preflight, InitChoices())

    explicit = _request(tmp_path, mode=InteractionMode.GUIDED, integrations=("codex",))
    explicit_preflight = inspect_workspace(explicit, executable_lookup=_lookup())
    explicit_plan = build_init_plan(explicit, explicit_preflight, InitChoices())

    assert default_plan.integrations == ()
    assert explicit_plan.integrations == ("codex",)


def test_reviewer_producer_is_never_defaulted_or_selected(tmp_path: Path) -> None:
    interactive = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(interactive, executable_lookup=_lookup())
    plan = build_init_plan(interactive, preflight, InitChoices())
    assert plan.review_producers == ()


def test_file_actions_are_ordered_before_any_apply_boundary(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("user agents\n")
    (tmp_path / ".gitignore").write_text("user ignore\n")
    request = _request(
        tmp_path,
        mode=InteractionMode.GUIDED,
        integrations=("codex",),
        setup_github=True,
    )
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex", "gh"))

    plan = build_init_plan(
        request,
        preflight,
        InitChoices(
            existing_files={
                "AGENTS.md": ExistingFileDecision.PRESERVE,
                ".gitignore": ExistingFileDecision.UPDATE,
            }
        ),
    )

    paths = [action.path.as_posix() for action in plan.file_actions]
    assert paths == [
        ".harness/events.jsonl",
        ".harness/state.yaml",
        ".harness/adapters.yaml",
        ".harness/review-recognition.yaml",
        ".harness/sensors.yaml",
        ".harness/gates.yaml",
        ".harness/source-paths.yaml",
        ".harness/derived-docs.yaml",
        ".harness/verification.yaml",
        ".harness/conventions.md",
        ".harness/plan-paths.yaml",
        ".harness/review-governance.yaml",
        ".harness/review-profiles.local.yaml",
        ".codex/hooks.json",
        ".claude/settings.local.json",
        "AGENTS.md",
        ".gitignore",
        ".github/workflows/super-harness.yml",
        ".github/pull_request_template.md",
    ]
    by_path = {action.path.as_posix(): action for action in plan.file_actions}
    assert by_path[".harness/events.jsonl"].action is FileAction.CREATE
    assert by_path[".harness/state.yaml"].action is FileAction.SKIP
    assert by_path[".harness/adapters.yaml"].action is FileAction.CREATE
    assert by_path[".harness/verification.yaml"].action is FileAction.CREATE
    assert by_path[".harness/review-recognition.yaml"].action is FileAction.CREATE
    assert by_path[".harness/review-governance.yaml"].action is FileAction.SKIP
    assert by_path[".harness/review-governance.yaml"].review_write is ReviewWrite.PRESERVE
    assert by_path[".harness/review-profiles.local.yaml"].action is FileAction.SKIP
    assert by_path[".codex/hooks.json"].action is FileAction.CREATE
    assert by_path[".claude/settings.local.json"].action is FileAction.SKIP
    assert by_path["AGENTS.md"].action is FileAction.UPDATE
    assert by_path[".gitignore"].action is FileAction.UPDATE
    assert by_path[".github/workflows/super-harness.yml"].action is FileAction.CREATE


def test_plan_marks_derived_and_optional_runtime_files_truthfully(tmp_path: Path) -> None:
    state = tmp_path / ".harness" / "state.yaml"
    state.parent.mkdir()
    state.write_text("derived: existing\n")
    request = _request(tmp_path, force=True)
    preflight = inspect_workspace(request, executable_lookup=_lookup())

    plan = build_init_plan(request, preflight, InitChoices())

    by_path = {action.path.as_posix(): action for action in plan.file_actions}
    assert by_path[".harness/events.jsonl"].action is FileAction.CREATE
    assert by_path[".harness/state.yaml"].action is FileAction.PRESERVE
    assert by_path[".harness/adapters.yaml"].action is FileAction.SKIP


def test_plan_marks_an_existing_selected_integration_hook_for_update(tmp_path: Path) -> None:
    hook = tmp_path / ".codex" / "hooks.json"
    hook.parent.mkdir()
    hook.write_text('{"hooks": {}}\n')
    request = _request(tmp_path, integrations=("codex",))
    preflight = inspect_workspace(request, executable_lookup=_lookup())

    plan = build_init_plan(request, preflight, InitChoices())

    by_path = {action.path.as_posix(): action for action in plan.file_actions}
    assert by_path[".codex/hooks.json"].action is FileAction.UPDATE


def test_plan_freezes_selected_integration_transactions_and_truthful_backups(
    tmp_path: Path,
) -> None:
    existing = tmp_path / ".codex" / "hooks.json"
    existing.parent.mkdir()
    existing.write_text('{"user":true}\n')
    current = tmp_path / ".claude" / "settings.local.json"
    current.parent.mkdir()
    current.write_text("{}\n")
    lookup = _lookup("super-harness-hook", "super-harness")
    current.write_bytes(
        inspect_workspace(
            _request(tmp_path, integrations=("claude-code",)),
            executable_lookup=lookup,
        )
        .integration_plans["claude-code"]
        .settings.desired_bytes
    )

    request = _request(tmp_path, integrations=("codex", "claude-code"))
    plan = build_init_plan(
        request,
        inspect_workspace(request, executable_lookup=lookup),
        InitChoices(),
    )

    assert tuple(plan.integration_plans) == ("codex", "claude-code")
    assert plan.backup_paths == (tmp_path / ".codex" / "hooks.json",)
    by_path = {action.path.as_posix(): action for action in plan.file_actions}
    assert by_path[".claude/settings.local.json"].action is FileAction.PRESERVE


def test_integration_preflight_needs_management_binaries_not_agent_binaries(
    tmp_path: Path,
) -> None:
    preflight = inspect_workspace(
        _request(tmp_path, mode=InteractionMode.GUIDED),
        executable_lookup=_lookup("super-harness-hook", "super-harness"),
    )
    assert preflight.available_integrations == frozenset({"codex", "claude-code"})


def test_unselected_malformed_integration_config_does_not_block_plan(tmp_path: Path) -> None:
    bad = tmp_path / ".claude" / "settings.local.json"
    bad.parent.mkdir()
    bad.write_text("{not-json")
    request = _request(tmp_path, integrations=("codex",))

    preflight = inspect_workspace(request, executable_lookup=_lookup())
    plan = build_init_plan(request, preflight, InitChoices())

    assert "claude-code" in preflight.integration_plan_errors
    assert plan.integrations == ("codex",)
    assert not (tmp_path / ".codex" / "hooks.json").exists()


def test_selected_malformed_integration_config_fails_at_plan_boundary(
    tmp_path: Path,
) -> None:
    bad = tmp_path / ".claude" / "settings.local.json"
    bad.parent.mkdir()
    original = b"{not-json"
    bad.write_bytes(original)
    request = _request(tmp_path, integrations=("claude-code",))

    preflight = inspect_workspace(request, executable_lookup=_lookup())
    with pytest.raises(InitPlanValidationError, match=r"claude-code.*not valid JSON"):
        build_init_plan(request, preflight, InitChoices())

    assert bad.read_bytes() == original
    assert not (tmp_path / ".harness").exists()


def test_unselected_unreadable_integration_config_is_captured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / ".claude" / "settings.local.json"
    bad.parent.mkdir()
    bad.touch()
    real_read_bytes = Path.read_bytes

    def deny_bad_settings(path: Path) -> bytes:
        if path == bad:
            raise PermissionError("settings are unreadable")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_bad_settings)
    request = _request(tmp_path, integrations=("codex",))

    preflight = inspect_workspace(request, executable_lookup=_lookup())
    plan = build_init_plan(request, preflight, InitChoices())

    assert preflight.integration_plan_errors["claude-code"] == "settings are unreadable"
    assert plan.integrations == ("codex",)


def test_unselected_symlink_integration_config_does_not_block_other_plan(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shared-settings.json"
    target.write_text("{}\n")
    link = tmp_path / ".claude" / "settings.local.json"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks unavailable: {error}")
    request = _request(tmp_path, integrations=("codex",))

    preflight = inspect_workspace(request, executable_lookup=_lookup())
    plan = build_init_plan(request, preflight, InitChoices())

    assert "symlink" in preflight.integration_plan_errors["claude-code"]
    assert plan.integrations == ("codex",)
    assert link.is_symlink()
    assert target.read_text() == "{}\n"


@pytest.mark.parametrize(
    ("unsafe_integration", "safe_integration", "directory"),
    [
        ("codex", "claude-code", ".codex"),
        ("claude-code", "codex", ".claude"),
    ],
)
def test_symlinked_integration_directory_only_blocks_selected_integration(
    tmp_path: Path,
    unsafe_integration: str,
    safe_integration: str,
    directory: str,
) -> None:
    external = tmp_path / "external-settings"
    external.mkdir()
    link = tmp_path / directory
    try:
        link.symlink_to(external, target_is_directory=True)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    unsafe_request = _request(tmp_path, integrations=(unsafe_integration,))
    unsafe_preflight = inspect_workspace(unsafe_request, executable_lookup=_lookup())
    with pytest.raises(InitPlanValidationError, match=rf"{unsafe_integration}.*symlink"):
        build_init_plan(unsafe_request, unsafe_preflight, InitChoices())

    safe_request = _request(tmp_path, integrations=(safe_integration,))
    safe_preflight = inspect_workspace(safe_request, executable_lookup=_lookup())
    safe_plan = build_init_plan(safe_request, safe_preflight, InitChoices())

    assert "symlink" in safe_preflight.integration_plan_errors[unsafe_integration]
    assert safe_plan.integrations == (safe_integration,)
    assert list(external.iterdir()) == []


def test_no_reviewer_model_default_is_invented(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))

    plan = build_init_plan(request, preflight, InitChoices())
    assert dict(plan.review_models) == {}


def test_request_choices_preflight_and_plan_are_deeply_immutable(tmp_path: Path) -> None:
    request = _request(
        tmp_path,
        integrations=("codex",),
    )
    choices_files = {"AGENTS.md": ExistingFileDecision.UPDATE}
    choices = InitChoices(existing_files=choices_files)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))
    plan = build_init_plan(request, preflight, choices)

    choices_files["AGENTS.md"] = ExistingFileDecision.PRESERVE
    assert dict(request.review_models) == {}
    assert choices.existing_files["AGENTS.md"] is ExistingFileDecision.UPDATE

    with pytest.raises(TypeError):
        request.review_models["codex"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        choices.existing_files["AGENTS.md"] = ExistingFileDecision.PRESERVE  # type: ignore[index]
    with pytest.raises(TypeError):
        preflight.existing_file_bytes["AGENTS.md"] = b"mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.review_models["codex"] = "mutated"  # type: ignore[index]
    with pytest.raises(AttributeError):
        plan.file_actions.append(plan.file_actions[0])  # type: ignore[attr-defined]


def test_closed_state_enums_and_forbidden_ui_lifecycle_imports() -> None:
    assert set(InteractionMode) == {
        InteractionMode.NON_INTERACTIVE,
        InteractionMode.LINE,
        InteractionMode.GUIDED,
    }
    assert set(ReviewWrite) == {ReviewWrite.PRESERVE, ReviewWrite.UPDATE, ReviewWrite.RESET}
    assert set(FileAction) == {
        FileAction.CREATE,
        FileAction.UPDATE,
        FileAction.DELETE,
        FileAction.PRESERVE,
        FileAction.SKIP,
    }
    assert set(HarnessState) == {
        HarnessState.ABSENT,
        HarnessState.INITIALIZED,
        HarnessState.PARTIAL,
    }
    assert set(ExistingFileDecision) == {
        ExistingFileDecision.PRESERVE,
        ExistingFileDecision.UPDATE,
    }
    assert set(GitHubDecision) == {GitHubDecision.SKIP, GitHubDecision.CREATE}

    module_path = Path(__file__).parents[3] / "src/super_harness/cli/init_plan.py"
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(ast.parse(module_path.read_text()))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(ast.parse(module_path.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imports.isdisjoint({"click", "rich", "questionary", "posix"})


def test_github_file_decisions_are_closed_and_choices_are_immutable() -> None:
    mutable = {
        ".github/pull_request_template.md": GithubFileDecision.APPEND,
        ".github/workflows/super-harness.yml": GithubFileDecision.OVERWRITE,
    }
    choices = InitChoices(github_file_decisions=mutable)
    mutable[".github/pull_request_template.md"] = GithubFileDecision.KEEP

    assert set(GithubFileDecision) == {
        GithubFileDecision.CREATE,
        GithubFileDecision.KEEP,
        GithubFileDecision.APPEND,
        GithubFileDecision.OVERWRITE,
    }
    assert (
        choices.github_file_decisions[".github/pull_request_template.md"]
        is GithubFileDecision.APPEND
    )
    with pytest.raises(TypeError):
        choices.github_file_decisions[".github/pull_request_template.md"] = GithubFileDecision.KEEP  # type: ignore[index]


def test_resolved_github_file_decisions_drive_truthful_file_actions(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(request, executable_lookup=_lookup("gh"))
    plan = build_init_plan(
        request,
        preflight,
        InitChoices(
            github_decision=GitHubDecision.CREATE,
            github_file_decisions={
                ".github/pull_request_template.md": GithubFileDecision.KEEP,
                ".github/workflows/super-harness.yml": GithubFileDecision.OVERWRITE,
            },
        ),
    )
    actions = {action.path.as_posix(): action.action for action in plan.file_actions}

    assert actions[".github/pull_request_template.md"] is FileAction.PRESERVE
    assert actions[".github/workflows/super-harness.yml"] is FileAction.UPDATE
