import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.cli import main
from super_harness.cli.init_plan import (
    GitHubDecision,
    InitChoices,
    InteractionMode,
    build_init_plan,
    inspect_workspace,
)
from super_harness.cli.init_ui import (
    InteractiveInitUI,
    TerminalCapabilities,
    WizardResult,
)
from super_harness.engineering.gh import GhError
from super_harness.version import __version__

_FAKE_HOOK = "/usr/local/bin/super-harness-hook"


def _assert_single_guided_frame(output: str, final_content: str) -> None:
    assert output.count("┌ super-harness init") == 1
    assert output.count("└") == 1
    assert output.index("┌ super-harness init") < output.index(final_content)
    assert output.index(final_content) < output.index("└")


def _assert_init_owned_paths_absent(root: Path) -> None:
    assert not (root / ".harness").exists()
    assert not (root / "AGENTS.md").exists()
    assert not (root / ".gitignore").exists()
    assert not (root / ".github").exists()


class _ScriptedInitUI:
    def __init__(
        self,
        prepare: Callable[[Any, Any, InitChoices | None], WizardResult],
    ) -> None:
        self._prepare = prepare
        self.events: list[Any] = []
        self.outcome: Any = None
        self.cancelled_rendered = False
        self.already_initialized_path: Path | None = None
        self.session_opened = False
        self.session_closed = False

    def open_session(self) -> None:
        self.session_opened = True

    def close_session(self) -> None:
        self.session_closed = True

    def collect_github_setup(self, request: Any, _preflight: Any) -> GitHubDecision:
        return GitHubDecision.CREATE if request.setup_github else GitHubDecision.SKIP

    def prepare_plan(
        self,
        request: Any,
        preflight: Any,
        *,
        initial_choices: InitChoices | None = None,
        github_resolver: Callable[[], Any] | None = None,
    ) -> WizardResult:
        del github_resolver
        return self._prepare(request, preflight, initial_choices)

    def render_cancelled(self) -> None:
        self.cancelled_rendered = True
        print("Setup cancelled")

    def on_step(self, event: Any) -> None:
        self.events.append(event)

    def render_outcome(self, result: Any) -> None:
        self.outcome = result

    def render_already_initialized(self, path: Path) -> None:
        self.already_initialized_path = path


class _GuidedAnswers:
    def __init__(
        self,
        *,
        checkboxes: list[tuple[str, ...] | None],
        selects: list[str | None],
        texts: list[str] | None = None,
        before_review: Callable[[], None] | None = None,
    ) -> None:
        self.checkboxes = iter(checkboxes)
        self.selects = iter(selects)
        self.texts = iter(texts or [])
        self.before_review = before_review
        self.text_calls: list[str] = []

    def checkbox(self, _message: str, _choices: Any) -> tuple[str, ...] | None:
        return next(self.checkboxes)

    def select(
        self,
        message: str,
        _choices: Any,
        *,
        default: str | None = None,
    ) -> str | None:
        del default
        if message == "Apply this plan?" and self.before_review is not None:
            self.before_review()
        return next(self.selects)

    def text(self, message: str, *, default: str | None = None) -> str | None:
        del default
        self.text_calls.append(message)
        return next(self.texts)


class _PlanCaptureRenderer:
    def __init__(self) -> None:
        self.plans: list[Any] = []

    def open_session(self) -> None:
        return None

    def close_session(self) -> None:
        return None

    def render_stage(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def render_plan(self, plan: Any) -> None:
        self.plans.append(plan)

    def render_validation(self, _message: str) -> None:
        return None

    def render_event(self, _event: Any) -> None:
        return None


def test_init_creates_harness_dir(tmp_path: Path):
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    assert (tmp_path / ".harness").is_dir()
    assert (tmp_path / ".harness" / "events.jsonl").exists()
    assert not (tmp_path / ".harness" / "policy.yaml").exists()
    governance = yaml.safe_load((tmp_path / ".harness" / "review-governance.yaml").read_text())
    review = governance["review"]
    assert review["sources"] == {"human": {"kind": "human"}}
    assert review["roles"]["plan-reviewer"] == {
        "participants": ["human"],
        "min_independent": 1,
        "max_automatic_rounds_per_epoch": 2,
    }
    assert review["roles"]["code-reviewer"] == {
        "participants": ["human"],
        "min_independent": 1,
        "max_automatic_rounds_per_epoch": 2,
    }
    assert not (tmp_path / ".harness" / "review-profiles.local.yaml").exists()
    assert (tmp_path / ".harness" / "sensors.yaml").exists()
    assert (tmp_path / ".harness" / "verification.yaml").exists()
    assert (tmp_path / ".harness" / "source-paths.yaml").exists()


def test_init_non_tty_configures_explicit_codex_review_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/abs/bin/{name}")

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
            "--review-producer",
            "codex-cli",
            "--review-model",
            "codex=gpt-review",
        ],
    )

    assert result.exit_code == 0, result.output
    governance = yaml.safe_load((tmp_path / ".harness" / "review-governance.yaml").read_text())[
        "review"
    ]
    assert governance["sources"] == {
        "codex": {"kind": "automated"},
        "human": {"kind": "human"},
    }
    assert governance["roles"]["plan-reviewer"]["participants"] == ["codex"]
    assert governance["roles"]["code-reviewer"]["participants"] == ["codex"]
    profiles = yaml.safe_load((tmp_path / ".harness" / "review-profiles.local.yaml").read_text())
    assert profiles["sources"]["codex"] == {
        "protocol": "codex-cli",
        "model": "gpt-review",
        "cost_class": "standard",
        "agent_options": {
            "reasoning_effort": "medium",
            "sandbox": "read-only",
        },
    }


def test_init_non_tty_configures_multiple_agent_integrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "super_harness.adapters.agent.codex.shutil.which",
        lambda name: f"/abs/bin/{name}",
    )
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: f"/abs/bin/{name}",
    )

    result = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
            "--integration",
            "codex",
            "--integration",
            "claude-code",
        ],
    )

    assert result.exit_code == 0, result.output
    adapters = yaml.safe_load((tmp_path / ".harness" / "adapters.yaml").read_text())["adapters"]
    assert [entry["name"] for entry in adapters] == ["codex", "claude-code"]
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert (tmp_path / ".claude" / "settings.local.json").exists()
    assert list(tmp_path.rglob("*.super-harness-backup.*")) == []


def test_init_rejects_reviewed_settings_drift_before_backup_or_settings_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/abs/{name}")
    settings = tmp_path / ".codex" / "hooks.json"

    def confirm(request: Any, preflight: Any, initial: InitChoices | None) -> WizardResult:
        plan = build_init_plan(request, preflight, initial or InitChoices())
        settings.parent.mkdir()
        settings.write_bytes(b'{"review":"drifted"}\n')
        return WizardResult.confirmed(plan)

    ui = _ScriptedInitUI(confirm)
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)
    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--integration", "codex"],
    )

    assert result.exit_code == 1, result.output
    assert settings.read_bytes() == b'{"review":"drifted"}\n'
    assert list(settings.parent.glob("*.super-harness-backup.*")) == []
    assert "changed after review" in result.output
    assert "rerun init" in result.output


def test_init_guided_confirmation_boundary_writes_only_after_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def confirm(request: Any, preflight: Any, initial: InitChoices | None) -> WizardResult:
        _assert_init_owned_paths_absent(tmp_path)
        return WizardResult.confirmed(build_init_plan(request, preflight, initial or InitChoices()))

    ui = _ScriptedInitUI(confirm)
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".harness" / "events.jsonl").exists()
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / ".gitignore").exists()
    assert ui.outcome is not None and ui.outcome.success is True


def test_init_explicit_cancel_is_a_normal_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cancel(request: Any, _preflight: Any, _initial: InitChoices | None) -> WizardResult:
        _assert_init_owned_paths_absent(request.workspace)
        return WizardResult.cancelled()

    ui = _ScriptedInitUI(cancel)
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    assert "Setup cancelled" in result.output
    assert ui.cancelled_rendered is True
    _assert_init_owned_paths_absent(tmp_path)


def test_init_keyboard_interrupt_before_apply_exits_one_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt(request: Any, _preflight: Any, _initial: InitChoices | None) -> WizardResult:
        _assert_init_owned_paths_absent(request.workspace)
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "super_harness.cli.init.create_init_ui",
        lambda *_a, **_kw: _ScriptedInitUI(interrupt),
    )

    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 1
    _assert_init_owned_paths_absent(tmp_path)


def test_init_non_tty_accepts_yes_but_never_requires_it(tmp_path: Path) -> None:
    without_yes = tmp_path / "without-yes"
    with_yes = tmp_path / "with-yes"
    without_yes.mkdir()
    with_yes.mkdir()

    first = CliRunner().invoke(main, ["--workspace", str(without_yes), "init"])
    second = CliRunner().invoke(main, ["--workspace", str(with_yes), "init", "--yes"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert (without_yes / ".harness" / "events.jsonl").exists()
    assert (with_yes / ".harness" / "events.jsonl").exists()


def test_init_help_documents_yes_exactly() -> None:
    result = CliRunner().invoke(main, ["init", "--help"], terminal_width=200)

    assert result.exit_code == 0
    assert "--yes" in result.output
    assert "Skip the final confirmation in interactive mode." in result.output


def test_init_line_mode_uses_per_option_prompts_without_comma_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = TerminalCapabilities(InteractionMode.LINE, False, False, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=lambda name: (
                f"/abs/{name}"
                if name in {"super-harness-hook", "super-harness"}
                else None
            ),
        ),
    )

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init"],
        input="n\nn\ny\n",
    )

    assert result.exit_code == 0, result.output
    assert "Select Codex integration?" in result.output
    assert "Select Claude Code integration?" in result.output
    assert "Apply this plan?" in result.output
    assert "comma-separated" not in result.output


def test_init_line_yes_skips_only_final_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = TerminalCapabilities(InteractionMode.LINE, False, False, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(request, executable_lookup=lambda _name: None),
    )

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--yes"],
        input="n\nn\n",
    )

    assert result.exit_code == 0, result.output
    assert "Select Codex integration?" in result.output
    assert "Select Claude Code integration?" in result.output
    assert "Apply this plan?" not in result.output


def test_init_guided_back_applies_the_revised_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = _GuidedAnswers(
        checkboxes=[("codex",), (), (), ()],
        selects=["back", "confirm"],
        before_review=lambda: _assert_init_owned_paths_absent(tmp_path),
    )
    ui = InteractiveInitUI(prompt_adapter=answers)
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=lambda name: (
                f"/abs/{name}"
                if name in {"super-harness-hook", "super-harness"}
                else None
            ),
        ),
    )

    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".harness" / "adapters.yaml").exists()
    assert "comma-separated" not in result.output


def test_init_guided_can_enable_github_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, False, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/abs/bin/gh" if name == "gh" else None,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=(lambda name: "/abs/bin/gh" if name == "gh" else None),
        ),
    )
    monkeypatch.setattr("super_harness.cli.init.check_gh", lambda: None)
    monkeypatch.setattr("super_harness.cli.init.enable_repo_merge_settings", lambda: None)
    renderer = _PlanCaptureRenderer()
    ui = InteractiveInitUI(
        prompt_adapter=_GuidedAnswers(
            checkboxes=[],
            selects=["create", "confirm"],
        ),
        renderer=renderer,
    )
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--no-agent"],
    )

    assert result.exit_code == 0, result.output
    assert renderer.plans[-1].github_decision is GitHubDecision.CREATE
    assert (tmp_path / ".github" / "pull_request_template.md").is_file()
    assert (tmp_path / ".github" / "workflows" / "super-harness.yml").is_file()


def test_init_guided_compacts_github_apply_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, True, 100)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=(lambda name: "/abs/bin/gh" if name == "gh" else None),
        ),
    )
    monkeypatch.setattr("super_harness.cli.init.check_gh", lambda: None)
    monkeypatch.setattr(
        "super_harness.cli.init.enable_repo_merge_settings",
        lambda: (_ for _ in ()).throw(GhError("non-admin")),
    )
    answers = _GuidedAnswers(checkboxes=[], selects=["create", "confirm"])
    monkeypatch.setattr(
        "super_harness.cli.init.create_init_ui",
        lambda *_a, **_kw: InteractiveInitUI(
            prompt_adapter=answers,
            unicode=True,
            color=False,
            width=100,
        ),
    )

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--no-agent"],
    )

    compact = " ".join(result.output.split())
    assert result.exit_code == 0, result.output
    assert "◆ apply: Applying setup" in compact
    assert "GitHub setup" in compact
    assert "Settings -> General -> Pull Requests" in compact
    assert "gh CLI: ok" not in result.output
    assert "wrote .github" not in result.output
    assert "could not auto-enable repo merge settings" not in result.output
    assert "skeleton_config" not in result.output


def test_init_guided_uses_configured_reviewer_models_without_text_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir()
    (home / ".codex" / "config.toml").write_text('model = "gpt-5.2-codex"\n')
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"model": "claude-opus-4-1", "apiKey": "never-copy-me"})
    )
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, False, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=(
                lambda name: f"/abs/bin/{name}" if name in {"codex", "claude"} else None
            ),
            home=home,
        ),
    )
    answers = _GuidedAnswers(
        checkboxes=[(), ("codex-cli", "claude-cli")],
        selects=["confirm"],
        before_review=lambda: _assert_init_owned_paths_absent(tmp_path),
    )
    ui = InteractiveInitUI(prompt_adapter=answers)
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    assert answers.text_calls == []
    profiles = yaml.safe_load((tmp_path / ".harness" / "review-profiles.local.yaml").read_text())
    assert profiles["sources"]["codex"]["model"] == "gpt-5.2-codex"
    assert profiles["sources"]["claude"]["model"] == "claude-opus-4-1"
    assert "never-copy-me" not in json.dumps(profiles)


def test_init_guided_without_configured_models_uses_human_only_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, False, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=(
                lambda name: f"/abs/bin/{name}" if name in {"codex", "claude"} else None
            ),
            home=home,
        ),
    )
    answers = _GuidedAnswers(checkboxes=[()], selects=["confirm"])
    ui = InteractiveInitUI(prompt_adapter=answers)
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    governance = yaml.safe_load((tmp_path / ".harness" / "review-governance.yaml").read_text())[
        "review"
    ]
    assert governance["sources"] == {"human": {"kind": "human"}}
    assert governance["roles"]["plan-reviewer"]["participants"] == ["human"]
    assert not (tmp_path / ".harness" / "review-profiles.local.yaml").exists()


def test_init_guided_isolates_malformed_provider_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir()
    codex_config = home / ".codex" / "config.toml"
    malformed = b'model = "unterminated\n'
    codex_config.write_bytes(malformed)
    (home / ".claude" / "settings.json").write_text(json.dumps({"model": "claude-sonnet-4"}))
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, False, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=(
                lambda name: f"/abs/bin/{name}" if name in {"codex", "claude"} else None
            ),
            home=home,
        ),
    )
    answers = _GuidedAnswers(
        checkboxes=[(), ("claude-cli",)],
        selects=["confirm"],
    )
    ui = InteractiveInitUI(prompt_adapter=answers)
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    profiles = yaml.safe_load((tmp_path / ".harness" / "review-profiles.local.yaml").read_text())
    assert set(profiles["sources"]) == {"claude"}
    assert profiles["sources"]["claude"]["model"] == "claude-sonnet-4"
    assert codex_config.read_bytes() == malformed


def test_init_questionary_none_cancels_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, True, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    ui = InteractiveInitUI(
        prompt_adapter=_GuidedAnswers(checkboxes=[None], selects=["skip"]),
        unicode=True,
        color=False,
        width=80,
    )
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    assert "Setup cancelled" in result.output
    _assert_single_guided_frame(result.output, "Setup cancelled")
    _assert_init_owned_paths_absent(tmp_path)


def test_init_keyboard_interrupt_during_apply_keeps_completed_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "super_harness.cli.init.install_agent_integration",
        lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--integration", "codex"],
    )

    assert result.exit_code == 1, result.output
    assert (tmp_path / ".harness" / "events.jsonl").exists()
    assert "scaffold: Scaffolded .harness and runtime directories." in result.output
    assert "agent_integrations: Interrupted while running agent_integrations." in result.output
    assert not (tmp_path / "AGENTS.md").exists()


def test_init_force_guided_edits_valid_persisted_review_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/abs/bin/{name}")
    first = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
            "--review-producer",
            "codex-cli",
            "--review-model",
            "codex=gpt-review",
        ],
    )
    assert first.exit_code == 0, first.output

    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, False, 80)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    renderer = _PlanCaptureRenderer()
    ui = InteractiveInitUI(
        prompt_adapter=_GuidedAnswers(
            checkboxes=[(), ("codex-cli",)],
            selects=["skip", "confirm"],
        ),
        renderer=renderer,
    )
    monkeypatch.setattr("super_harness.cli.init.create_init_ui", lambda *_a, **_kw: ui)

    forced = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--force"],
    )

    assert forced.exit_code == 0, forced.output
    assert renderer.plans[-1].review_write.value == "update"
    assert renderer.plans[-1].review_producers == ("codex-cli",)
    assert dict(renderer.plans[-1].review_models) == {"codex": "gpt-review"}


def test_init_scaffolds_derived_docs_skeleton(tmp_path: Path):
    # doc-leg Task 7: init ships a DISCOVERABLE skeleton derived-docs.yaml so the
    # doc-check gate is not a silent no-op — an adopter sees how to register docs.
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    f = tmp_path / ".harness" / "derived-docs.yaml"
    assert f.is_file()
    # The skeleton must parse as a VALID empty registry (`derived_docs: []` ->
    # ([], [])), NOT a malformed one.
    from super_harness.core.doc_check import load_derived_docs

    docs, errors = load_derived_docs(tmp_path)
    assert docs == []
    assert errors == []


def test_init_idempotent_without_force(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r2.exit_code == 3  # EXIT_NO_CONFIG-style for already-init
    # Status is the primary recovery; force is explicitly a review/reconfigure path.
    assert "Hint: Run `super-harness status`" in r2.stderr
    assert "Use `super-harness init --force` to review and reconfigure it." in r2.stderr


def test_init_guided_existing_harness_uses_cohesive_status_first_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    first = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert first.exit_code == 0, first.output
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, True, 100)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.create_init_ui",
        lambda *_a, **_kw: InteractiveInitUI(
            prompt_adapter=_GuidedAnswers(checkboxes=[], selects=[]),
            unicode=True,
            color=False,
            width=100,
        ),
    )

    repeated = runner.invoke(main, ["--workspace", str(tmp_path), "init"])

    assert repeated.exit_code == 3
    assert repeated.output.count("Already initialized") == 1
    assert "Next: super-harness status" in repeated.output
    assert "Review/reconfigure: super-harness init --force" in repeated.output
    assert "Error: super-harness init" not in repeated.output
    _assert_single_guided_frame(repeated.output, "Review/reconfigure")


def test_init_guided_success_has_one_completion_without_legacy_final_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, True, 100)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.create_init_ui",
        lambda *_a, **_kw: InteractiveInitUI(
            prompt_adapter=_GuidedAnswers(checkboxes=[()], selects=["confirm"]),
            unicode=True,
            color=False,
            width=100,
        ),
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(request, executable_lookup=lambda _name: None),
    )

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--no-agent"],
    )

    assert result.exit_code == 0, result.output
    assert result.output.count("outcome: Setup complete in ") == 1
    assert "super-harness initialized at" not in result.output
    _assert_single_guided_frame(result.output, "outcome: Setup complete in ")


def test_init_guided_apply_interrupt_closes_frame_after_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capabilities = TerminalCapabilities(InteractionMode.GUIDED, False, True, 100)
    monkeypatch.setattr(
        "super_harness.cli.init.detect_runtime_terminal_capabilities",
        lambda *_a, **_kw: capabilities,
    )
    monkeypatch.setattr(
        "super_harness.cli.init.create_init_ui",
        lambda *_a, **_kw: InteractiveInitUI(
            prompt_adapter=_GuidedAnswers(checkboxes=[], selects=["confirm"]),
            unicode=True,
            color=False,
            width=100,
        ),
    )
    monkeypatch.setattr(
        "super_harness.cli.init.inspect_workspace",
        lambda request: inspect_workspace(
            request,
            executable_lookup=lambda name: (
                f"/abs/{name}" if name in {"super-harness-hook", "super-harness"} else None
            ),
        ),
    )
    monkeypatch.setattr(
        "super_harness.cli.init.install_agent_integration",
        lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--integration", "codex"],
    )

    assert result.exit_code == 1, result.output
    assert "Recovery: super-harness init --force" in result.output
    _assert_single_guided_frame(result.output, "Recovery: super-harness init --force")


def test_init_non_guided_success_preserves_legacy_final_line(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    assert result.output.count(f"super-harness initialized at {tmp_path / '.harness'}") == 1


def test_init_force_overwrites(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    (tmp_path / ".harness" / "policy.yaml").write_text("# user-edit\n")
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0


def test_init_force_preserves_review_selection_without_new_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/abs/bin/{name}")
    runner = CliRunner()
    first = runner.invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
            "--review-producer",
            "codex-cli",
            "--review-model",
            "codex=gpt-review",
        ],
    )
    assert first.exit_code == 0, first.output
    governance = tmp_path / ".harness" / "review-governance.yaml"
    profiles = tmp_path / ".harness" / "review-profiles.local.yaml"
    before = (governance.read_bytes(), profiles.read_bytes())

    forced = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])

    assert forced.exit_code == 0, forced.output
    assert (governance.read_bytes(), profiles.read_bytes()) == before


def test_init_creates_all_subdirs(tmp_path: Path):
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    for d in (
        "sensor-results",
        "verification-results",
        "operation-logs",
        "pending-reviews",
    ):
        assert (tmp_path / ".harness" / d).is_dir(), f"missing subdir: {d}"


def test_init_creates_gates_and_conventions(tmp_path: Path):
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert (tmp_path / ".harness" / "gates.yaml").exists()
    assert (tmp_path / ".harness" / "conventions.md").exists()


def test_init_refuses_when_partial_harness_exists(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 3
    assert not (tmp_path / ".harness" / "events.jsonl").exists()


def test_init_accepts_noop_flags_silently(tmp_path: Path):
    """v0.1: --framework is accepted but produces no runtime notice.

    Help text carries the placeholder caveat (Phase 4 will wire --framework).
    Locks in the Phase 1 convention so a future regression that re-introduces
    a runtime stderr notice would be caught. (--setup-github is now wired in
    Phase 12 — its behavior is covered by test_init_setup_github.py.)
    """
    runner = CliRunner()
    r = runner.invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "init",
            "--framework",
            "openspec",
        ],
    )
    assert r.exit_code == 0
    assert "no-op" not in r.stderr.lower()
    assert "not yet implemented" not in r.stderr.lower()


def test_init_help_advertises_v01_caveat(tmp_path: Path):
    r = CliRunner().invoke(main, ["init", "--help"])
    assert r.exit_code == 0
    assert "v0.1" in r.output  # caveat is in --help for at least one no-op flag


# --------------------------------------------------------------------------- #
# Agent gate hook auto-install (one-command onboarding)
# --------------------------------------------------------------------------- #


def test_init_non_tty_does_not_auto_install_detected_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detection informs the TTY wizard; non-TTY init requires explicit flags."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: f"/abs/bin/{name}",
    )
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    settings = tmp_path / ".claude" / "settings.local.json"
    assert not settings.exists()
    assert not (tmp_path / ".harness" / "adapters.yaml").exists()
    assert "registered PreToolUse gate hook" not in result.output


def test_init_selected_integration_registry_error_fails_actionably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit integration selection must not hide partial installation."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: f"/abs/bin/{name}",
    )

    def _raise(*args: object, **kwargs: object) -> None:
        raise yaml.YAMLError("boom")

    monkeypatch.setattr("super_harness.adapters.install._persist_install_entry", _raise)
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--integration", "claude-code"],
    )
    assert result.exit_code == 1, result.output
    assert (tmp_path / ".harness").is_dir()
    assert "could not configure claude-code integration" in result.output


def test_init_selected_integration_missing_hook_fails_actionably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit integration selection fails when its hook cannot be installed."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: None,
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "init", "--integration", "claude-code"],
    )
    assert result.exit_code == 1, result.output
    assert not (tmp_path / ".harness").exists()
    assert not (tmp_path / ".claude" / "settings.local.json").exists()
    assert "could not prepare init plan" in result.output
    assert "must be available" in result.output


def test_init_no_agent_flag_skips_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--no-agent"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_init_no_claude_dir_is_agent_noop(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude").exists()


# --------------------------------------------------------------------------- #
# AGENTS.md outer-section wiring (engineering-integration §2.2 / §3.2)
# --------------------------------------------------------------------------- #


def test_init_writes_agents_md_fresh_repo(tmp_path: Path):
    """Fresh repo (no AGENTS.md): init writes the version-stamped section,
    the plain framework block, and the no-agent anchor — and leaves NO
    literal [*_SECTION_AUTO_INSERTED] placeholders (§3.2)."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    agents = tmp_path / "AGENTS.md"
    assert agents.exists()
    text = agents.read_text()
    # version-stamped begin marker
    assert f"<!-- super-harness section begin · v{__version__} · DO NOT EDIT MANUALLY -->" in text
    assert "<!-- super-harness section end -->" in text
    # plain framework block injected in place of the framework placeholder
    assert PlainAdapter().agents_md_subsection().rstrip("\n") in text
    # no-agent anchor present
    assert "<!-- super-harness no-agent-adapter-installed -->" in text
    # zero literal placeholders remain
    assert "[FRAMEWORK_SECTION_AUTO_INSERTED]" not in text
    assert "[AGENT_SECTION_AUTO_INSERTED]" not in text


def test_init_preserves_existing_agents_md_user_content(tmp_path: Path):
    """Existing AGENTS.md with user content (no super-harness section): init
    appends our section while preserving the user's content verbatim."""
    agents = tmp_path / "AGENTS.md"
    user_content = "# My project\n\nSome existing agent guidance.\n"
    agents.write_text(user_content)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    text = agents.read_text()
    assert user_content.rstrip() in text
    assert f"<!-- super-harness section begin · v{__version__} · DO NOT EDIT MANUALLY -->" in text
    assert PlainAdapter().agents_md_subsection().rstrip("\n") in text


def test_init_force_does_not_duplicate_agents_md_section(tmp_path: Path):
    """Re-running init with --force re-renders exactly one super-harness
    section (no duplicate blocks)."""
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.count("<!-- super-harness section begin ") == 1
    assert text.count("<!-- super-harness section end -->") == 1
    # framework block still present exactly once after re-render
    assert text.count("<!-- super-harness framework: plain -->") == 1


def test_init_does_not_write_agents_md_on_error_path(tmp_path: Path):
    """When .harness/ exists without --force, init errors and must NOT write
    AGENTS.md."""
    (tmp_path / ".harness").mkdir()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 3
    assert not (tmp_path / "AGENTS.md").exists()


def test_init_agents_md_write_failure_exits_generic_with_format_error(tmp_path: Path):
    """If the AGENTS.md write raises OSError, init surfaces a clean format_error
    (exit 1, no traceback) instead of a raw crash.

    We force a portable OSError by pre-creating a DIRECTORY at the AGENTS.md path:
    `inject_section`'s read (`AGENTS.md/`.read_text()) raises IsADirectoryError
    (an OSError subclass) on every platform. .harness/ has already been scaffolded
    by this point, so the message must reflect that and that `--force` re-runs."""
    # Pre-create AGENTS.md as a directory so the injector's read/write fails.
    (tmp_path / "AGENTS.md").mkdir()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])

    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness init:" in r.stderr, r.stderr
    assert "failed to write AGENTS.md" in r.stderr, r.stderr
    assert "Hint:" in r.stderr, r.stderr
    # .harness/ was scaffolded before the AGENTS.md write — it must survive so the
    # `--force` re-run is the documented recovery.
    assert (tmp_path / ".harness").is_dir()


def test_init_force_reinjects_installed_adapters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`init` → `adapter install claude-code` → `init --force` re-renders the
    AGENTS.md super-harness section AND re-injects every installed adapter's
    subsection, so a re-render never loses adapter guidance (full --force loop
    closure). The adapters.yaml entry + the settings.local.json hooks stay intact and
    are NOT touched by init.

    `adapter install claude-code` resolves `super-harness-hook` via
    ``shutil.which``; we monkeypatch it to a fake absolute path so the real
    binary need not be on PATH — matching the pattern in
    ``tests/integration/cli/test_adapter.py``.
    """
    runner = CliRunner()
    no_agent_anchor = "<!-- super-harness no-agent-adapter-installed -->"
    claude_begin = "<!-- super-harness agent: claude-code -->"

    # init → real AGENTS.md (with the no-agent anchor) exists.
    assert runner.invoke(main, ["--workspace", str(tmp_path), "init"]).exit_code == 0

    # install claude-code → consumes the anchor, injects the agent block, and
    # records the adapter + settings.local.json hooks.
    monkeypatch.setattr(shutil, "which", lambda _name: _FAKE_HOOK)
    install = runner.invoke(
        main, ["--workspace", str(tmp_path), "adapter", "install", "claude-code"]
    )
    assert install.exit_code == 0, install.output
    agents = tmp_path / "AGENTS.md"
    assert claude_begin in agents.read_text()
    assert no_agent_anchor not in agents.read_text()

    # init --force → re-renders the section AND re-injects installed adapters.
    forced = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert forced.exit_code == 0, forced.output

    # 1) AGENTS.md preserved the claude-code agent block (re-injected, NOT reset
    #    to the no-agent anchor); exactly ONE block (no duplicate); the outer
    #    section + plain framework block are present.
    text = agents.read_text()
    assert claude_begin in text
    assert text.count(claude_begin) == 1
    assert no_agent_anchor not in text
    assert "<!-- super-harness section begin " in text
    assert text.count("<!-- super-harness framework: plain -->") == 1

    # 2) adapters.yaml STILL lists claude-code (init never touches it).
    adapters = yaml.safe_load((tmp_path / ".harness" / "adapters.yaml").read_text())
    names = [e.get("name") for e in (adapters.get("adapters") or [])]
    assert "claude-code" in names, f"claude-code dropped from adapters.yaml: {adapters}"

    # 2b) settings.local.json STILL has our PreToolUse + SessionStart hooks (unchanged).
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
    pre_commands = [
        h["command"] for entry in settings["hooks"]["PreToolUse"] for h in entry["hooks"]
    ]
    assert pre_commands == [f"{_FAKE_HOOK} --agent claude-code"]
    session_commands = [
        h["command"] for entry in settings["hooks"]["SessionStart"] for h in entry["hooks"]
    ]
    assert session_commands == [f"{_FAKE_HOOK} change resume"]

    # 3) the `init --force` run did NOT emit the old "was reset" advisory.
    combined = forced.stderr + forced.output
    assert "was reset" not in combined, combined


@pytest.mark.parametrize(
    "bad_yaml",
    [
        # wrong-shape but valid YAML → `load_adapters` raises ValueError.
        "adapters: not-a-list\n",
        # syntactically broken YAML → `yaml.safe_load` raises yaml.YAMLError.
        "{ this is: not: valid: yaml\n",
        ":\n  - [unclosed\n",
    ],
    ids=[
        "wrong-shape-valueerror",
        "broken-flow-mapping-yamlerror",
        "unclosed-seq-yamlerror",
    ],
)
def test_init_force_corrupt_adapters_yaml_emits_advisory_and_exits_ok(
    tmp_path: Path,
    bad_yaml: str,
):
    """A corrupt `.harness/adapters.yaml` makes `init --force` re-injection a
    NON-FATAL advisory (couldn't re-inject) + still exit 0 with a valid base
    AGENTS.md section (the outer section + plain block + no-agent anchor).

    Covers BOTH failure families: a wrong-shape (valid YAML, `ValueError`) and
    a syntactically-broken file (`yaml.YAMLError`) — both must route to the
    same best-effort advisory, never a raw traceback."""
    runner = CliRunner()
    assert runner.invoke(main, ["--workspace", str(tmp_path), "init"]).exit_code == 0

    (tmp_path / ".harness" / "adapters.yaml").write_text(bad_yaml)

    forced = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "couldn't re-inject installed adapters" in forced.stderr, forced.stderr
    # Never a raw traceback for either failure family.
    combined = forced.stderr + forced.output
    assert "Traceback" not in combined, combined

    # Base AGENTS.md section is still valid (init did not crash on the bad yaml).
    text = (tmp_path / "AGENTS.md").read_text()
    assert "<!-- super-harness section begin " in text
    assert text.count("<!-- super-harness framework: plain -->") == 1
    assert "<!-- super-harness no-agent-adapter-installed -->" in text


def test_init_fresh_does_not_reinject_or_warn(tmp_path: Path):
    """Fresh init (no adapters.yaml): re-injection is a no-op (load_adapters
    returns ([],[])) and emits no advisory — the no-agent anchor stays put."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0
    assert "couldn't re-inject" not in r.stderr
    assert "was reset" not in r.stderr
    text = (tmp_path / "AGENTS.md").read_text()
    assert "<!-- super-harness no-agent-adapter-installed -->" in text
    assert "<!-- super-harness agent:" not in text


# --------------------------------------------------------------------------- #
# .gitignore management (S2 fix — OPEN-ITEMS #6)
# --------------------------------------------------------------------------- #


_GITIGNORE_BEGIN = "# >>> super-harness gitignore (do not edit between markers)"
_GITIGNORE_END = "# <<< super-harness gitignore"
_CANONICAL_GITIGNORE_PATHS = (
    ".harness/state.yaml",
    ".harness/events.jsonl",
    ".harness/sensor-results/",
    ".harness/verification-results/",
    ".harness/operation-logs/",
    ".harness/pending-reviews/",
    ".harness/gate-disabled",
    ".claude/settings.local.json",
)


def test_init_writes_gitignore_block_fresh_repo(tmp_path: Path):
    """Fresh repo (no .gitignore): init writes the marker-bounded block with
    the canonical `.harness/` runtime + per-machine `.claude/` paths."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0, r.output
    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    text = gitignore.read_text()
    assert _GITIGNORE_BEGIN in text
    assert _GITIGNORE_END in text
    for p in _CANONICAL_GITIGNORE_PATHS:
        assert p in text, f"missing canonical path: {p}"


def test_init_preserves_existing_gitignore_user_content(tmp_path: Path):
    """Existing .gitignore (no super-harness block): init appends the block
    while preserving the user's content verbatim."""
    gitignore = tmp_path / ".gitignore"
    user_content = "# User-written\n*.pyc\nnode_modules/\n.env\n"
    gitignore.write_text(user_content)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 0, r.output
    text = gitignore.read_text()
    # User content preserved.
    assert "# User-written" in text
    assert "*.pyc" in text
    assert "node_modules/" in text
    assert ".env" in text
    # Block appended after user content.
    assert _GITIGNORE_BEGIN in text
    user_idx = text.index("node_modules/")
    block_idx = text.index(_GITIGNORE_BEGIN)
    assert user_idx < block_idx


def test_init_force_does_not_duplicate_gitignore_block(tmp_path: Path):
    """Re-running init with --force does not duplicate the marker block."""
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    r2 = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--force"])
    assert r2.exit_code == 0, r2.output
    text = (tmp_path / ".gitignore").read_text()
    assert text.count(_GITIGNORE_BEGIN) == 1
    assert text.count(_GITIGNORE_END) == 1


def test_init_gitignore_multiple_blocks_fails_loud(tmp_path: Path):
    """An existing .gitignore with ≥2 super-harness marker blocks fails loud
    (never splices) and leaves the file untouched (Phase 7/9/12 marker
    discipline)."""
    gitignore = tmp_path / ".gitignore"
    bad = (
        f"{_GITIGNORE_BEGIN}\n"
        ".harness/state.yaml\n"
        f"{_GITIGNORE_END}\n"
        "\n"
        f"{_GITIGNORE_BEGIN}\n"
        ".harness/events.jsonl\n"
        f"{_GITIGNORE_END}\n"
    )
    gitignore.write_text(bad)
    before = gitignore.read_text()
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.stderr, r.stderr
    assert "super-harness init:" in r.stderr
    # File left untouched (never spliced).
    assert gitignore.read_text() == before
