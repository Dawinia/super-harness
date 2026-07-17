from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.cli.init_models import discover_reviewer_models


def _write_provider_configs(home: Path) -> None:
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        'model = "gpt-active"\n'
        "[profiles.slow]\n"
        'model = "gpt-slow"\n'
        "[profiles.fast]\n"
        'model = "gpt-fast"\n',
        encoding="utf-8",
    )
    (home / ".claude" / "settings.json").write_text(
        '{"model": "opus", "apiKey": "must-not-leak"}',
        encoding="utf-8",
    )


def test_discovery_orders_workspace_active_and_named_profile_models(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _write_provider_configs(home)

    result = discover_reviewer_models(
        home=home,
        persisted_models={"codex": "gpt-workspace"},
    )

    assert [(item.model, item.origin) for item in result.candidates["codex"]] == [
        ("gpt-workspace", "existing workspace profile"),
        ("gpt-active", "Codex CLI config"),
        ("gpt-fast", "Codex CLI profile fast"),
        ("gpt-slow", "Codex CLI profile slow"),
    ]
    assert [(item.model, item.origin) for item in result.candidates["claude"]] == [
        ("opus", "Claude CLI config")
    ]
    assert dict(result.errors) == {}


def test_discovery_deduplicates_exact_model_values_by_precedence(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        'model = "same"\n[profiles.fast]\nmodel = "same"\n',
        encoding="utf-8",
    )

    result = discover_reviewer_models(
        home=home,
        persisted_models={"codex": "same"},
    )

    assert [(item.model, item.precedence) for item in result.candidates["codex"]] == [
        ("same", 0)
    ]


def test_discovery_ignores_missing_files_and_non_string_models(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        "model = 42\n[profiles.fast]\nmodel = false\n",
        encoding="utf-8",
    )

    result = discover_reviewer_models(home=home, persisted_models={})

    assert dict(result.candidates) == {}
    assert dict(result.errors) == {}


@pytest.mark.parametrize(
    ("relative_path", "content", "expected"),
    [
        (Path(".codex/config.toml"), "model = [", "Codex CLI config is not valid TOML"),
        (Path(".claude/settings.json"), "{", "Claude CLI config is not valid JSON"),
    ],
)
def test_discovery_reports_sanitized_parse_errors(
    tmp_path: Path,
    relative_path: Path,
    content: str,
    expected: str,
) -> None:
    home = tmp_path / "home"
    path = home / relative_path
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    result = discover_reviewer_models(home=home, persisted_models={})

    source = "codex" if relative_path.suffix == ".toml" else "claude"
    assert result.errors[source] == expected
    assert content not in result.errors[source]


def test_discovery_reports_sanitized_unreadable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    path = home / ".codex" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('model = "gpt"\n', encoding="utf-8")
    original = Path.read_text

    def fail_for_codex(candidate: Path, *args: object, **kwargs: object) -> str:
        if candidate == path:
            raise PermissionError("secret operating-system detail")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_for_codex)

    result = discover_reviewer_models(home=home, persisted_models={})

    assert result.errors["codex"] == "Codex CLI config could not be read"
    assert "secret" not in result.errors["codex"]


def test_source_filter_never_opens_excluded_provider_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("model = [", encoding="utf-8")
    (home / ".claude" / "settings.json").write_text(
        '{"model": "opus"}', encoding="utf-8"
    )

    result = discover_reviewer_models(
        home=home,
        persisted_models={"codex": "workspace", "claude": "workspace-claude"},
        sources={"claude"},
    )

    assert set(result.candidates) == {"claude"}
    assert [item.model for item in result.candidates["claude"]] == [
        "workspace-claude",
        "opus",
    ]
    assert dict(result.errors) == {}


def test_discovery_result_mappings_are_immutable(tmp_path: Path) -> None:
    result = discover_reviewer_models(
        home=tmp_path / "home",
        persisted_models={"codex": "workspace"},
    )

    with pytest.raises(TypeError):
        result.candidates["codex"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        result.errors["codex"] = "changed"  # type: ignore[index]
