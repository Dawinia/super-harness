"""Adapter registry tests (Phase 6, Task 6.3).

Covers `adapters/registry.py` (builtin resolution, framework/agent split,
enabled:false skip, fallback activation, same-name conflict, missing file) and
the v0.1 builtin-only guarantee: a non-builtin (`builtin` not literally true)
entry is REJECTED without importing/executing its module (F12 — no arbitrary
code execution from `.harness/adapters.yaml`).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from super_harness.adapters import FrameworkAdapter
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.adapters.registry import (
    activate_with_fallback,
    get_builtin,
    list_builtins,
    load_adapters,
)


# A non-fallback framework whose detect() we can drive via a marker file.
class _DetectingFramework(FrameworkAdapter):
    name: ClassVar[str] = "detector"
    version: ClassVar[str] = "0.1.0"
    is_fallback: ClassVar[bool] = False

    def detect(self, workspace: Path) -> bool:
        return (workspace / ".detector").exists()

    def observe(self, workspace):  # type: ignore[no-untyped-def]
        return iter([])

    def get_state(self, change_id):  # type: ignore[no-untyped-def]
        return None

    def verification_checks(self):  # type: ignore[no-untyped-def]
        return []

    def agents_md_subsection(self) -> str:
        return ""


# ---------------------------------------------------------------------------
# Built-in registration / accessors
# ---------------------------------------------------------------------------


def test_builtins_registered() -> None:
    assert get_builtin("plain") is PlainAdapter
    assert get_builtin("claude-code") is ClaudeCodeAdapter
    assert get_builtin("nope") is None
    names = list_builtins()
    assert "plain" in names
    assert "claude-code" in names
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# load_adapters — builtin resolution + framework/agent split
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    frameworks, agents = load_adapters(tmp_path / "does-not-exist.yaml")
    assert frameworks == []
    assert agents == []


def test_load_rejects_non_list_adapters_value(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(yaml.safe_dump({"adapters": "oops"}))
    with pytest.raises(ValueError, match="must be a list"):
        load_adapters(yml)


@pytest.mark.parametrize("body", ["- just\n- a\n- list\n", "just-a-string\n", "42\n"])
def test_load_rejects_non_mapping_top_level(tmp_path: Path, body: str) -> None:
    """A valid-but-truthy-non-mapping top level (list / scalar) → ValueError, not
    a leaked AttributeError from `cfg.get(...)`. (Falsy shapes — empty / null /
    false / 0 — normalize to `{}` and are accepted as 'no adapters'.)"""
    yml = tmp_path / "adapters.yaml"
    yml.write_text(body)
    with pytest.raises(ValueError, match="top level must be a mapping"):
        load_adapters(yml)


def test_load_rejects_non_dict_entry(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(yaml.safe_dump({"adapters": ["bare-string"]}))
    with pytest.raises(ValueError, match="must be a dict"):
        load_adapters(yml)


def test_load_builtins_split_into_framework_and_agent(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "plain", "type": "framework", "builtin": True,
                     "version": "0.1.0", "enabled": True},
                    {"name": "claude-code", "type": "agent", "builtin": True,
                     "version": "0.1.0", "enabled": True},
                ]
            }
        )
    )
    frameworks, agents = load_adapters(yml)
    assert [type(f) for f in frameworks] == [PlainAdapter]
    assert [type(a) for a in agents] == [ClaudeCodeAdapter]


def test_builtin_kind_derived_from_abc_not_yaml_type(tmp_path: Path) -> None:
    """A contradicting yaml `type` on a builtin must not mis-route it."""
    yml = tmp_path / "adapters.yaml"
    # Lie about claude-code being a framework — it must still land in agents.
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "claude-code", "type": "framework", "builtin": True,
                     "version": "0.1.0", "enabled": True},
                ]
            }
        )
    )
    frameworks, agents = load_adapters(yml)
    assert frameworks == []
    assert [type(a) for a in agents] == [ClaudeCodeAdapter]


def test_unknown_builtin_raises(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"adapters": [{"name": "nope", "type": "framework", "builtin": True}]}
        )
    )
    with pytest.raises(ValueError, match="unknown built-in adapter"):
        load_adapters(yml)


# ---------------------------------------------------------------------------
# load_adapters — v0.1 builtin-only: non-builtin entries rejected WITHOUT exec
# (F12: no arbitrary code execution from .harness/adapters.yaml)
# ---------------------------------------------------------------------------


def _evil_module(sentinel: Path) -> str:
    # Import side effect = writing the sentinel. If the module is ever exec'd,
    # the sentinel appears — the assertion that it does NOT is the RCE guard.
    return (
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('pwned')\n"
        "class Evil:\n    pass\n"
    )


@pytest.mark.parametrize("kind", ["framework", "agent"])
@pytest.mark.parametrize(
    "extra",
    [
        {"builtin": False},                    # explicit non-builtin
        {},                                    # builtin key omitted
        {"builtin": False, "enabled": False},  # disabled non-builtin — must STILL raise
    ],
)
def test_non_builtin_adapter_is_rejected_without_executing(
    tmp_path: Path, kind: str, extra: dict
) -> None:
    sentinel = tmp_path / "EXECUTED"
    mod = tmp_path / "evil.py"
    mod.write_text(_evil_module(sentinel), encoding="utf-8")
    entry = {"name": "evil", "type": kind, "path": str(mod), "class": "Evil", **extra}
    yml = tmp_path / "adapters.yaml"
    yml.write_text(yaml.safe_dump({"adapters": [entry]}), encoding="utf-8")

    with pytest.raises(ValueError, match="custom plugins are not supported"):
        load_adapters(yml)
    assert not sentinel.exists(), "plugin module was executed — RCE surface still open"


# ---------------------------------------------------------------------------
# load_adapters — enabled: false skip (disabled BUILTIN)
# ---------------------------------------------------------------------------


def test_disabled_entry_skipped(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "plain", "type": "framework", "builtin": True,
                     "enabled": False},
                    {"name": "claude-code", "type": "agent", "builtin": True,
                     "enabled": True},
                ]
            }
        )
    )
    frameworks, agents = load_adapters(yml)
    assert frameworks == []  # plain was disabled, skipped
    assert [type(a) for a in agents] == [ClaudeCodeAdapter]


# ---------------------------------------------------------------------------
# Same-name conflict
# ---------------------------------------------------------------------------


def test_duplicate_yaml_name_raises(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "claude-code", "type": "agent", "builtin": True},
                    {"name": "claude-code", "type": "agent", "builtin": True},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="duplicate adapter name"):
        load_adapters(yml)


# ---------------------------------------------------------------------------
# Fallback activation
# ---------------------------------------------------------------------------


def test_activate_with_empty_frameworks_returns_empty(tmp_path: Path) -> None:
    assert activate_with_fallback([], tmp_path) == []


def test_fallback_active_when_nothing_detects(tmp_path: Path) -> None:
    frameworks = [_DetectingFramework(), PlainAdapter()]
    # No `.detector` marker → _DetectingFramework.detect == False → plain active.
    active = activate_with_fallback(frameworks, tmp_path)
    assert [type(f) for f in active] == [PlainAdapter]


def test_fallback_excluded_when_a_framework_detects(tmp_path: Path) -> None:
    (tmp_path / ".detector").touch()
    frameworks = [_DetectingFramework(), PlainAdapter()]
    active = activate_with_fallback(frameworks, tmp_path)
    assert [type(f) for f in active] == [_DetectingFramework]


def test_codex_is_registered():
    from super_harness.adapters.agent.codex import CodexAdapter
    from super_harness.adapters.registry import get_builtin
    assert get_builtin("codex") is CodexAdapter


def test_resolve_spec_plan_paths_openspec(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    spec, plan = resolve_spec_plan_paths("openspec", tmp_path, "c")
    assert spec == str(tmp_path / "openspec" / "changes" / "c" / "proposal.md")
    assert plan == str(tmp_path / "openspec" / "changes" / "c" / "tasks.md")


def test_resolve_spec_plan_paths_plain_is_empty(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    assert resolve_spec_plan_paths("plain", tmp_path, "c") == ("", "")


def test_resolve_spec_plan_paths_no_framework(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    assert resolve_spec_plan_paths(None, tmp_path, "c") == ("", "")
    assert resolve_spec_plan_paths("", tmp_path, "c") == ("", "")


def test_resolve_spec_plan_paths_unknown_framework(tmp_path: Path) -> None:
    from super_harness.adapters.registry import resolve_spec_plan_paths

    assert resolve_spec_plan_paths("no-such-fw", tmp_path, "c") == ("", "")
