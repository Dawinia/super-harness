"""Unit tests for the platform-neutral adapter installation service."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from super_harness.adapters.install import (
    _ADAPTERS_YAML_HEADER,
    _persist_install_entry,
    _read_adapter_entries,
    _remove_install_entry,
    install_agent_integration,
    preview_agent_integration,
)


def _adapters_yaml(root: Path) -> Path:
    return root / ".harness" / "adapters.yaml"


def test_persist_install_entry_creates_yaml_with_managed_header(
    tmp_path: Path,
) -> None:
    (tmp_path / ".harness").mkdir()

    _persist_install_entry(tmp_path, name="plain", kind="framework", version="0.1.0")

    text = _adapters_yaml(tmp_path).read_text(encoding="utf-8")
    assert text.startswith(_ADAPTERS_YAML_HEADER)
    assert yaml.safe_load(text) == {
        "adapters": [
            {
                "name": "plain",
                "type": "framework",
                "builtin": True,
                "version": "0.1.0",
                "enabled": True,
            }
        ]
    }


def test_persist_install_entry_preserves_other_top_level_keys(
    tmp_path: Path,
) -> None:
    path = _adapters_yaml(tmp_path)
    path.parent.mkdir()
    path.write_text("custom:\n  keep: true\nadapters: []\n", encoding="utf-8")

    _persist_install_entry(tmp_path, name="plain", kind="framework", version="0.1.0")

    assert yaml.safe_load(path.read_text(encoding="utf-8"))["custom"] == {"keep": True}


def test_persist_install_entry_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()

    _persist_install_entry(tmp_path, name="plain", kind="framework", version="0.0.9")
    _persist_install_entry(tmp_path, name="plain", kind="framework", version="0.1.0")

    assert _read_adapter_entries(_adapters_yaml(tmp_path)) == [
        {
            "name": "plain",
            "type": "framework",
            "builtin": True,
            "version": "0.1.0",
            "enabled": True,
        }
    ]


def test_remove_install_entry_preserves_other_entries_and_top_level_keys(
    tmp_path: Path,
) -> None:
    path = _adapters_yaml(tmp_path)
    path.parent.mkdir()
    path.write_text(
        "custom: retained\n"
        "adapters:\n"
        "  - name: plain\n"
        "    type: framework\n"
        "  - name: codex\n"
        "    type: agent\n",
        encoding="utf-8",
    )

    _remove_install_entry(tmp_path, name="plain")

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["custom"] == "retained"
    assert cfg["adapters"] == [{"name": "codex", "type": "agent"}]


def test_corrupt_yaml_error_propagates(tmp_path: Path) -> None:
    path = _adapters_yaml(tmp_path)
    path.parent.mkdir()
    path.write_text("adapters: [\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        _persist_install_entry(tmp_path, name="plain", kind="framework", version="0.1.0")


@pytest.mark.parametrize(
    ("name", "config_relpath", "expected_command"),
    [
        (
            "claude-code",
            ".claude/settings.local.json",
            "/opt/super-harness-hook --agent claude-code",
        ),
        (
            "codex",
            ".codex/hooks.json",
            "/opt/super-harness-hook --agent codex",
        ),
    ],
)
def test_install_agent_integration_installs_hooks_and_registry_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    config_relpath: str,
    expected_command: str,
) -> None:
    (tmp_path / ".harness").mkdir()

    def _which(executable: str) -> str:
        return f"/opt/{executable}"

    monkeypatch.setattr(shutil, "which", _which)

    adapter = install_agent_integration(tmp_path, name)

    config = json.loads((tmp_path / config_relpath).read_text(encoding="utf-8"))
    commands = [
        hook["command"] for entry in config["hooks"]["PreToolUse"] for hook in entry["hooks"]
    ]
    assert expected_command in commands
    assert adapter.name == name
    assert _read_adapter_entries(_adapters_yaml(tmp_path)) == [
        {
            "name": name,
            "type": "agent",
            "builtin": True,
            "version": adapter.version,
            "enabled": True,
        }
    ]


def test_frozen_integration_plan_rejects_executable_drift_before_settings_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".harness").mkdir()
    settings = tmp_path / ".codex" / "hooks.json"
    settings.parent.mkdir()
    original = b'{"user":true}\n'
    settings.write_bytes(original)
    monkeypatch.setattr(shutil, "which", lambda name: f"/reviewed/{name}")
    plan = preview_agent_integration(tmp_path, "codex")
    monkeypatch.setattr(shutil, "which", lambda name: f"/drifted/{name}")

    with pytest.raises(RuntimeError, match="executable path changed after review"):
        install_agent_integration(tmp_path, "codex", plan=plan)

    assert settings.read_bytes() == original
    assert list(settings.parent.glob("*.super-harness-backup.*")) == []
    assert not _adapters_yaml(tmp_path).exists()


def test_frozen_integration_plan_cannot_be_reused_in_another_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed = tmp_path / "reviewed"
    requested = tmp_path / "requested"
    monkeypatch.setattr(shutil, "which", lambda name: f"/reviewed/{name}")
    plan = preview_agent_integration(reviewed, "codex")

    with pytest.raises(ValueError, match="does not belong to requested workspace"):
        install_agent_integration(requested, "codex", plan=plan)

    assert not (reviewed / ".codex" / "hooks.json").exists()
    assert not (requested / ".codex" / "hooks.json").exists()
    assert not _adapters_yaml(reviewed).exists()
    assert not _adapters_yaml(requested).exists()
