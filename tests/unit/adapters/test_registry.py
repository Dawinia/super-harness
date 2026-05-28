"""Adapter registry tests (Phase 6, Task 6.3).

Covers `adapters/registry.py` (builtin resolution, custom path+class loading,
framework/agent split, enabled:false skip, fallback activation, same-name
conflict, missing file) plus focused coverage of the shared
`core/_plugin_loader.load_class_from_path` primitive (success + the two
asserted error substrings the sensors/gates suites also depend on).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from super_harness.adapters import AgentAdapter, FrameworkAdapter
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.adapters.registry import (
    activate_with_fallback,
    get_builtin,
    list_builtins,
    load_adapters,
)
from super_harness.core._plugin_loader import load_class_from_path


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
# load_adapters — custom (builtin: false) path+class loading
# ---------------------------------------------------------------------------


_CUSTOM_FRAMEWORK_SRC = """\
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

from super_harness.adapters import FrameworkAdapter
from super_harness.core.events import Event


class MyFramework(FrameworkAdapter):
    name: ClassVar[str] = "my-framework"
    version: ClassVar[str] = "0.0.1"

    def detect(self, workspace: Path) -> bool:
        return False

    def observe(self, workspace: Path) -> Iterator[Event]:
        return iter([])

    def get_state(self, change_id: str):
        return None

    def verification_checks(self):
        return []

    def agents_md_subsection(self) -> str:
        return ""
"""


def test_load_custom_framework_via_path_class(tmp_path: Path) -> None:
    mod = tmp_path / "my_framework.py"
    mod.write_text(_CUSTOM_FRAMEWORK_SRC)
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "my-framework", "type": "framework", "builtin": False,
                     "path": str(mod), "class": "MyFramework",
                     "version": "0.0.1", "enabled": True},
                ]
            }
        )
    )
    frameworks, agents = load_adapters(yml)
    assert agents == []
    assert len(frameworks) == 1
    assert frameworks[0].name == "my-framework"
    assert isinstance(frameworks[0], FrameworkAdapter)


def test_custom_missing_path_raises(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "x", "type": "framework", "builtin": False,
                     "class": "Foo"},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="missing required string 'path'"):
        load_adapters(yml)


def test_custom_nonexistent_path_raises(tmp_path: Path) -> None:
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "x", "type": "framework", "builtin": False,
                     "path": str(tmp_path / "nope.py"), "class": "Foo"},
                ]
            }
        )
    )
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_adapters(yml)


# ---------------------------------------------------------------------------
# load_adapters — enabled: false skip
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


def test_custom_named_like_builtin_raises(tmp_path: Path) -> None:
    mod = tmp_path / "my_framework.py"
    mod.write_text(_CUSTOM_FRAMEWORK_SRC)
    yml = tmp_path / "adapters.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "adapters": [
                    {"name": "plain", "type": "framework", "builtin": False,
                     "path": str(mod), "class": "MyFramework"},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="conflicts with a built-in"):
        load_adapters(yml)


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


# ---------------------------------------------------------------------------
# load_class_from_path — focused direct coverage
# ---------------------------------------------------------------------------


def test_load_class_from_path_success(tmp_path: Path) -> None:
    mod = tmp_path / "ok.py"
    mod.write_text(_CUSTOM_FRAMEWORK_SRC)
    base: type[FrameworkAdapter] = FrameworkAdapter  # type: ignore[type-abstract]
    cls = load_class_from_path(
        mod, "MyFramework", base,
        module_name="super_harness_user.t_ok", error_label="lbl",
    )
    assert issubclass(cls, FrameworkAdapter)
    assert cls.name == "my-framework"


def test_load_class_from_path_missing_attribute(tmp_path: Path) -> None:
    mod = tmp_path / "thin.py"
    mod.write_text(_CUSTOM_FRAMEWORK_SRC)
    base: type[FrameworkAdapter] = FrameworkAdapter  # type: ignore[type-abstract]
    with pytest.raises(AttributeError, match="has no attribute 'Missing'"):
        load_class_from_path(
            mod, "Missing", base,
            module_name="super_harness_user.t_thin", error_label="lbl",
        )


def test_load_class_from_path_wrong_base(tmp_path: Path) -> None:
    mod = tmp_path / "notadapter.py"
    mod.write_text("class NotAnAdapter:\n    pass\n")
    base: type[AgentAdapter] = AgentAdapter  # type: ignore[type-abstract]
    with pytest.raises(TypeError, match="not a AgentAdapter subclass"):
        load_class_from_path(
            mod, "NotAnAdapter", base,
            module_name="super_harness_user.t_bad", error_label="lbl",
        )
