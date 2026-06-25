import json
import shutil

import pytest

from super_harness.adapters.agent.codex import CodexAdapter


def test_detect_requires_codex_dir(tmp_path):
    a = CodexAdapter()
    assert a.detect(tmp_path) is False
    (tmp_path / ".codex").mkdir()
    assert a.detect(tmp_path) is True


def test_install_writes_pre_tool_use_and_session_start(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    pre = data["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "^(apply_patch|Edit|Write)$"
    assert pre["hooks"][0]["command"] == "/abs/super-harness-hook --agent codex"
    ss = data["hooks"]["SessionStart"][0]
    assert ss["hooks"][0]["command"] == "/abs/super-harness change resume"


def test_install_aborts_when_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    (tmp_path / ".codex").mkdir()
    with pytest.raises(RuntimeError):
        CodexAdapter().install_hooks(tmp_path)
    assert not (tmp_path / ".codex" / "hooks.json").exists()  # no write before abort


def test_install_does_not_touch_gitignore(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    assert not (tmp_path / ".gitignore").exists()  # gitignore is sync/init's job


def test_install_detail_and_relpath():
    a = CodexAdapter()
    assert a.local_config_relpath() == ".codex/hooks.json"
    assert "/hooks" in a.installed_detail()  # trust reminder present


def test_agents_md_subsection_has_trust_and_caveat():
    sub = CodexAdapter().agents_md_subsection()
    assert "/hooks" in sub  # trust step
    assert "apply_patch" in sub
    assert "WebSearch" in sub  # coverage caveat


def test_on_uninstall_restores_earliest_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": []}}))  # pristine user file
    CodexAdapter().install_hooks(tmp_path)  # writes a backup of pristine
    CodexAdapter().on_uninstall(tmp_path)
    assert json.loads(hooks.read_text()) == {"hooks": {"PreToolUse": []}}
