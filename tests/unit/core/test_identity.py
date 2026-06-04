"""Tests for core.identity.resolve_identity (HG-12 cut 1 substrate).

Precedence: --as override > env SUPER_HARNESS_ACTOR > git config user.email >
fallback "cli". The git call is an isolated seam (_git_config_email) that swallows
every failure mode → None (so resolve_identity falls through to "cli", never
raises).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from super_harness.core.identity import _git_config_email, resolve_identity


def test_override_wins_over_everything(monkeypatch):
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "env@x")
    with patch("super_harness.core.identity._git_config_email", return_value="git@x"):
        assert resolve_identity(Path("."), override="me@flag") == "me@flag"


def test_env_wins_over_git(monkeypatch):
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "env@x")
    with patch("super_harness.core.identity._git_config_email", return_value="git@x"):
        assert resolve_identity(Path(".")) == "env@x"


def test_git_used_when_no_override_or_env(monkeypatch):
    monkeypatch.delenv("SUPER_HARNESS_ACTOR", raising=False)
    with patch("super_harness.core.identity._git_config_email", return_value="git@x"):
        assert resolve_identity(Path(".")) == "git@x"


def test_fallback_cli_when_all_unset(monkeypatch):
    monkeypatch.delenv("SUPER_HARNESS_ACTOR", raising=False)
    with patch("super_harness.core.identity._git_config_email", return_value=None):
        assert resolve_identity(Path(".")) == "cli"


def test_blank_override_and_env_fall_through(monkeypatch):
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "   ")
    with patch("super_harness.core.identity._git_config_email", return_value=None):
        assert resolve_identity(Path("."), override="  ") == "cli"


def test_git_seam_swallows_nonzero(monkeypatch):
    class _P:
        returncode = 1
        stdout = ""
        stderr = "not a git repo"

    monkeypatch.setattr(
        "super_harness.core.identity.subprocess.run", lambda *a, **k: _P()
    )
    assert _git_config_email(Path(".")) is None


def test_git_seam_swallows_missing_binary(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr("super_harness.core.identity.subprocess.run", _boom)
    assert _git_config_email(Path(".")) is None


def test_git_seam_strips_and_blank_is_none(monkeypatch):
    class _P:
        returncode = 0
        stdout = "  \n"
        stderr = ""

    monkeypatch.setattr(
        "super_harness.core.identity.subprocess.run", lambda *a, **k: _P()
    )
    assert _git_config_email(Path(".")) is None


def test_git_seam_returns_stripped_email(monkeypatch):
    class _P:
        returncode = 0
        stdout = "  dev@example.com \n"
        stderr = ""

    monkeypatch.setattr(
        "super_harness.core.identity.subprocess.run", lambda *a, **k: _P()
    )
    assert _git_config_email(Path(".")) == "dev@example.com"
