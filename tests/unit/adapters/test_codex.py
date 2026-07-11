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
    # Coverage caveat must not conflate what Codex *surfaces* to a hook with what
    # super-harness *gates*: the old "intercepts only simple shell" phrasing read as
    # a contradiction against "Bash is never gated" (PR#74 review).
    assert "simple shell" not in sub.lower()
    assert "Codex surfaces only shell commands" in sub  # caveat separates surface from gating


def test_on_uninstall_restores_earliest_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": []}}))  # pristine user file
    CodexAdapter().install_hooks(tmp_path)  # writes a backup of pristine
    CodexAdapter().on_uninstall(tmp_path)
    assert json.loads(hooks.read_text()) == {"hooks": {"PreToolUse": []}}


def test_agents_md_subsection_does_not_teach_kill_switch():
    sub = CodexAdapter().agents_md_subsection()
    assert "gate-disabled" not in sub
    assert "surface" in sub.lower()
    assert "human" in sub.lower()
    assert "kill-switch always works" not in sub
    # Generalized wording: do not name/point at the override file or a discrete
    # "kill switch" in the agent channel — keep the norm ("don't work around the
    # gate, surface to human") without surfacing the bypass mechanism's location.
    assert "override file" not in sub.lower()
    assert "kill switch" not in sub.lower()
    flat = " ".join(sub.split()).lower()  # collapse line-wraps before phrase match
    assert "work around the gate" in flat
    assert "kill switch always works" not in sub.lower()


def test_codex_capabilities():
    caps = CodexAdapter().capabilities
    assert caps["post_tool_use_hook"] is True       # spike-verified (fires under codex exec)
    assert caps["turn_end_feedback_hook"] is True    # cut-2 Stop delivery


def test_codex_stop_should_check_and_feedback_delegate():
    from super_harness.core.authoring_check import Verdict, Violation
    a = CodexAdapter()
    assert a.stop_should_check({"stop_hook_active": True}) is False
    assert a.stop_should_check({"stop_hook_active": False}) is True
    v = Verdict(violations=[Violation("d-core-is-base", "x", "docs/decisions/d-core-is-base.md")])
    obj = json.loads(a.format_stop_feedback(v))
    assert obj["decision"] == "block" and "d-core-is-base" in obj["reason"]
    assert set(obj) == {"decision", "reason"}  # reason-ONLY (spike: extra fields break Codex Stop)


def test_codex_install_writes_stop_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    stop = data["hooks"]["Stop"][0]
    assert stop["hooks"][0]["command"] == "/abs/super-harness-hook --agent codex --event stop"


def test_codex_install_stop_is_idempotent_on_reinstall(tmp_path, monkeypatch):
    # BLOCKER regression (review B1): a Codex-specific marker must make reinstall REPLACE,
    # not append. Two Stop entries → two JSON objects on stdout → Codex "Stop Failed".
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    CodexAdapter().install_hooks(tmp_path)  # reinstall
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert len(data["hooks"]["Stop"]) == 1  # replaced, not duplicated


def test_codex_uninstall_round_trips_stop_hook(tmp_path, monkeypatch):
    # S3: install onto a PRE-EXISTING hooks.json, then uninstall → earliest backup restored,
    # Stop entry gone. (Fresh-install-absent-file uninstall leak is pre-existing / OUT.)
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.write_text('{"hooks": {}}\n')  # pristine pre-existing file
    CodexAdapter().install_hooks(tmp_path)
    assert "Stop" in json.loads(hooks.read_text())["hooks"]
    CodexAdapter().on_uninstall(tmp_path)
    assert json.loads(hooks.read_text()) == {"hooks": {}}  # restored, Stop gone


def test_codex_installed_detail_mentions_stop():
    assert "Stop" in CodexAdapter().installed_detail()


def test_codex_agents_md_mentions_stop_authoring_check():
    sub = CodexAdapter().agents_md_subsection()
    assert "Stop" in sub
    assert "authoring" in sub.lower()
    assert "/hooks" in sub  # trust caveat still present


def test_codex_agents_md_mentions_source_profiles():
    sub = CodexAdapter().agents_md_subsection()
    assert "agent_options" in sub
    assert "bundle-only" in sub
    assert "incremental" in sub


def test_codex_agents_md_teaches_compiled_review_contract():
    sub = " ".join(CodexAdapter().agents_md_subsection().split()).lower()
    assert "dispatch every assignment in listed order" in sub
    assert "apply its agent_options verbatim" in sub
    assert "collect every raw verdict before recording" in sub
    assert "does not trigger plan review" in sub
    assert "plan, scope, or requirements changed" in sub
    assert "never widen an assignment to the whole pr" in sub
