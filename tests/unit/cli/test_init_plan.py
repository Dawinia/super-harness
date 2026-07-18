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
        "      max_automatic_rounds_per_epoch: 2\n"
        "    code-reviewer:\n"
        f"      participants: [{source}]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds_per_epoch: 2\n"
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


def test_noninteractive_fresh_explicit_configuration_builds_plan(tmp_path: Path) -> None:
    request = _request(
        tmp_path,
        integrations=("codex",),
        producers=("codex-cli",),
        models={"codex": "gpt-review"},
        review_flags_explicit=True,
        setup_github=True,
    )

    preflight = inspect_workspace(request, executable_lookup=_lookup("codex", "gh"))
    plan = build_init_plan(request, preflight, InitChoices())

    assert preflight.harness_state is HarnessState.ABSENT
    assert plan.review_write is ReviewWrite.UPDATE
    assert plan.integrations == ("codex",)
    assert plan.review_producers == ("codex-cli",)
    assert dict(plan.review_models) == {"codex": "gpt-review"}
    assert plan.github_decision is GitHubDecision.CREATE
    assert all(action.action is not FileAction.PRESERVE for action in plan.file_actions)


def test_preflight_captures_immutable_reviewer_model_candidates(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_user_models(home)
    request = _request(tmp_path, mode=InteractionMode.GUIDED)

    result = inspect_workspace(
        request,
        executable_lookup=_lookup("codex", "claude"),
        home=home,
    )

    assert result.reviewer_model_candidates["codex"][0].model == "gpt-configured"
    assert result.reviewer_model_candidates["claude"][0].model == "opus-configured"
    with pytest.raises(TypeError):
        result.reviewer_model_candidates["codex"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        result.reviewer_model_errors["codex"] = "changed"  # type: ignore[index]


def test_preflight_orders_persisted_model_before_user_config(tmp_path: Path) -> None:
    _write_review_config(tmp_path, model="gpt-workspace")
    home = tmp_path / "home"
    _write_user_models(home)
    request = _request(tmp_path, mode=InteractionMode.GUIDED, force=True)

    result = inspect_workspace(request, executable_lookup=_lookup("codex"), home=home)

    assert [item.model for item in result.reviewer_model_candidates["codex"]] == [
        "gpt-workspace",
        "gpt-configured",
    ]


def test_preflight_records_sanitized_reviewer_model_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("model = [", encoding="utf-8")

    result = inspect_workspace(
        _request(tmp_path, mode=InteractionMode.LINE),
        executable_lookup=_lookup("codex"),
        home=home,
    )

    assert dict(result.reviewer_model_errors) == {"codex": "Codex CLI config is not valid TOML"}


def test_explicit_reviewer_model_excludes_its_provider_from_discovery(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("model = [", encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(
        '{"model": "opus-configured"}', encoding="utf-8"
    )
    request = _request(
        tmp_path,
        mode=InteractionMode.GUIDED,
        models={"codex": "gpt-explicit"},
    )

    result = inspect_workspace(
        request,
        executable_lookup=_lookup("codex", "claude"),
        home=home,
    )

    assert "codex" not in result.reviewer_model_candidates
    assert "codex" not in result.reviewer_model_errors
    assert result.reviewer_model_candidates["claude"][0].model == "opus-configured"


def test_interactive_preflight_resolves_home_at_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "runtime-home"
    _write_user_models(home)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    result = inspect_workspace(
        _request(tmp_path, mode=InteractionMode.GUIDED),
        executable_lookup=_lookup("codex"),
    )

    assert result.reviewer_model_candidates["codex"][0].model == "gpt-configured"


def test_noninteractive_preflight_never_resolves_home_or_reads_provider_config(
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
    ("producers", "models", "message"),
    [
        (("codex-cli",), {}, "requires an explicit model"),
        ((), {"codex": "gpt-review"}, "has no selected producer"),
        (("codex-cli",), {"claude": "claude-review"}, "does not match"),
        (("codex-cli", "claude-cli"), {"codex": "gpt-review"}, "requires an explicit model"),
        (("codex-cli",), {"codex": ""}, "non-empty"),
    ],
)
def test_noninteractive_explicit_review_flags_never_fill_gaps_from_persisted_config(
    tmp_path: Path,
    producers: tuple[str, ...],
    models: dict[str, str],
    message: str,
) -> None:
    _write_review_config(tmp_path)
    request = _request(
        tmp_path,
        force=True,
        producers=producers,
        models=models,
        review_flags_explicit=True,
    )

    preflight = inspect_workspace(request, executable_lookup=_lookup("codex", "claude"))

    with pytest.raises(InitPlanValidationError, match=message):
        build_init_plan(request, preflight, InitChoices())


def test_noninteractive_complete_explicit_pair_updates_and_ignores_persisted_values(
    tmp_path: Path,
) -> None:
    old_governance, old_profile = _write_review_config(tmp_path, model="old-model")
    request = _request(
        tmp_path,
        force=True,
        producers=("codex-cli",),
        models={"codex": "new-model"},
        review_flags_explicit=True,
    )

    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))
    plan = build_init_plan(request, preflight, InitChoices())

    actions = _review_actions(plan)
    assert plan.review_write is ReviewWrite.UPDATE
    assert dict(plan.review_models) == {"codex": "new-model"}
    assert actions["review-governance.yaml"].content != old_governance
    assert actions["review-profiles.local.yaml"].content != old_profile
    assert b"new-model" in actions["review-profiles.local.yaml"].content
    assert b"old-model" not in actions["review-profiles.local.yaml"].content


def test_interactive_force_edit_uses_persisted_pairs_as_defaults_and_choices_override(
    tmp_path: Path,
) -> None:
    _write_review_config(tmp_path, model="persisted-model")
    request = _request(tmp_path, mode=InteractionMode.GUIDED, force=True)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))

    default_plan = build_init_plan(
        request,
        preflight,
        InitChoices(review_write=ReviewWrite.UPDATE),
    )
    override_plan = build_init_plan(
        request,
        preflight,
        InitChoices(
            review_write=ReviewWrite.UPDATE,
            review_models={"codex": "choice-model"},
        ),
    )

    assert preflight.persisted_review_producers == ("codex-cli",)
    assert dict(default_plan.review_models) == {"codex": "persisted-model"}
    assert dict(override_plan.review_models) == {"codex": "choice-model"}


def test_interactive_explicit_pair_replaces_a_different_persisted_pair(
    tmp_path: Path,
) -> None:
    _write_review_config(tmp_path, model="persisted-codex")
    request = _request(
        tmp_path,
        mode=InteractionMode.GUIDED,
        force=True,
        producers=("claude-cli",),
        models={"claude": "explicit-claude"},
        review_flags_explicit=True,
    )
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex", "claude"))

    plan = build_init_plan(
        request,
        preflight,
        InitChoices(review_write=ReviewWrite.UPDATE),
    )

    assert plan.review_producers == ("claude-cli",)
    assert dict(plan.review_models) == {"claude": "explicit-claude"}


@pytest.mark.parametrize(
    ("governance", "profile"),
    [
        (b"not: [yaml", b"version: 1\nsources: {}\n"),
        (b"version: 999\nreview: {}\n", b"version: 1\nsources: {}\n"),
        (
            b"version: 1\nreview: {sources: {}, roles: {}}\n",
            b"version: 1\nsources:\n  alien:\n    protocol: alien-cli\n    model: x\n",
        ),
    ],
)
def test_invalid_interactive_persisted_review_requires_explicit_reset(
    tmp_path: Path, governance: bytes, profile: bytes
) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "events.jsonl").write_text("")
    (harness / "review-governance.yaml").write_bytes(governance)
    (harness / "review-profiles.local.yaml").write_bytes(profile)
    request = _request(tmp_path, mode=InteractionMode.GUIDED, force=True)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))

    assert preflight.review_config_error is not None
    with pytest.raises(InitPlanValidationError, match="RESET"):
        build_init_plan(request, preflight, InitChoices(review_write=ReviewWrite.UPDATE))

    reset = build_init_plan(
        request,
        preflight,
        InitChoices(review_write=ReviewWrite.RESET, review_producers=(), review_models={}),
    )
    assert reset.review_write is ReviewWrite.RESET
    assert reset.review_producers == ()


def test_fresh_interactive_defaults_to_detected_integration_and_producer(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=InteractionMode.LINE)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))

    plan = build_init_plan(
        request,
        preflight,
        InitChoices(review_models={"codex": "chosen-model"}),
    )

    assert preflight.detected_integrations == ("codex",)
    assert preflight.detected_review_producers == ("codex-cli",)
    assert plan.integrations == ("codex",)
    assert plan.review_producers == ("codex-cli",)
    assert dict(plan.review_models) == {"codex": "chosen-model"}


def test_interactive_reset_uses_detected_defaults_not_persisted_defaults(tmp_path: Path) -> None:
    _write_review_config(
        tmp_path,
        producer="claude-cli",
        source="claude",
        model="persisted-claude",
    )
    request = _request(tmp_path, mode=InteractionMode.GUIDED, force=True)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex", "claude"))

    plan = build_init_plan(
        request,
        preflight,
        InitChoices(
            review_write=ReviewWrite.RESET,
            review_models={
                "codex": "fresh-codex",
                "claude": "fresh-claude",
            },
        ),
    )

    assert plan.review_producers == ("codex-cli", "claude-cli")
    assert dict(plan.review_models) == {
        "codex": "fresh-codex",
        "claude": "fresh-claude",
    }


def test_unavailable_integration_can_be_explicit_but_is_not_preselected(tmp_path: Path) -> None:
    interactive = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(interactive, executable_lookup=_lookup())
    default_plan = build_init_plan(interactive, preflight, InitChoices())

    explicit = _request(tmp_path, mode=InteractionMode.GUIDED, integrations=("codex",))
    explicit_preflight = inspect_workspace(explicit, executable_lookup=_lookup())
    explicit_plan = build_init_plan(explicit, explicit_preflight, InitChoices())

    assert default_plan.integrations == ()
    assert explicit_plan.integrations == ("codex",)


def test_unavailable_producer_is_not_defaulted_and_explicit_use_raises(tmp_path: Path) -> None:
    interactive = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(interactive, executable_lookup=_lookup())
    plan = build_init_plan(interactive, preflight, InitChoices())
    assert plan.review_producers == ()

    explicit = _request(
        tmp_path,
        mode=InteractionMode.GUIDED,
        producers=("codex-cli",),
        models={"codex": "gpt-review"},
        review_flags_explicit=True,
    )
    explicit_preflight = inspect_workspace(explicit, executable_lookup=_lookup())
    with pytest.raises(InitPlanValidationError, match="not available"):
        build_init_plan(explicit, explicit_preflight, InitChoices())


def test_file_actions_are_ordered_before_any_apply_boundary(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("user agents\n")
    (tmp_path / ".gitignore").write_text("user ignore\n")
    request = _request(
        tmp_path,
        mode=InteractionMode.GUIDED,
        integrations=("codex",),
        producers=("codex-cli",),
        models={"codex": "gpt-review"},
        review_flags_explicit=True,
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
        ".harness/sensors.yaml",
        ".harness/gates.yaml",
        ".harness/source-paths.yaml",
        ".harness/derived-docs.yaml",
        ".harness/verification.yaml",
        ".harness/conventions.md",
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
    assert by_path[".harness/review-governance.yaml"].review_write is ReviewWrite.UPDATE
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
        ).integration_plans["claude-code"].settings.desired_bytes
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


def test_no_model_default_is_invented(tmp_path: Path) -> None:
    request = _request(tmp_path, mode=InteractionMode.GUIDED)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))

    with pytest.raises(InitPlanValidationError, match="requires an explicit model"):
        build_init_plan(request, preflight, InitChoices())


def test_request_choices_preflight_and_plan_are_deeply_immutable(tmp_path: Path) -> None:
    request_models = {"codex": "gpt-review"}
    request = _request(
        tmp_path,
        producers=("codex-cli",),
        models=request_models,
        review_flags_explicit=True,
    )
    choices_files = {"AGENTS.md": ExistingFileDecision.UPDATE}
    choices = InitChoices(existing_files=choices_files)
    preflight = inspect_workspace(request, executable_lookup=_lookup("codex"))
    plan = build_init_plan(request, preflight, choices)

    request_models["codex"] = "mutated"
    choices_files["AGENTS.md"] = ExistingFileDecision.PRESERVE
    assert request.review_models["codex"] == "gpt-review"
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
