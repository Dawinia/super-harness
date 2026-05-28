from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from super_harness.adapters import AgentAdapter


class _MinimalAdapter(AgentAdapter):
    name: ClassVar[str] = "minimal"
    version: ClassVar[str] = "0.1.0"
    capabilities: ClassVar[dict[str, bool]] = {
        "pre_tool_use_hook": True,
        "post_tool_use_hook": False,
        "session_start_hook": False,
        "session_end_hook": False,
        "pre_commit_hook": False,
        "rules_file_injection": True,
        "mcp_server": False,
        "subprocess_execution": True,
    }

    def detect(self, workspace: Path) -> bool:
        return False

    def install_hooks(self, workspace: Path) -> None:
        return None

    def inject_context(self, change_id: str) -> str:
        return ""

    def agents_md_subsection(self) -> str:
        return ""


def test_agent_adapter_is_abstract() -> None:
    with pytest.raises(TypeError):
        AgentAdapter()  # type: ignore[abstract]


def test_subclass_missing_abstractmethods_not_instantiable() -> None:
    class _Incomplete(AgentAdapter):
        name: ClassVar[str] = "incomplete"
        version: ClassVar[str] = "0.1.0"

        def detect(self, workspace: Path) -> bool:  # missing the other 3
            return False

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_subclass_must_define_name() -> None:
    with pytest.raises(TypeError, match="name"):

        class _Bad(AgentAdapter):
            version: ClassVar[str] = "0.1.0"

            def detect(self, workspace: Path) -> bool:
                return False

            def install_hooks(self, workspace: Path) -> None:
                return None

            def inject_context(self, change_id: str) -> str:
                return ""

            def agents_md_subsection(self) -> str:
                return ""


def test_subclass_must_define_version() -> None:
    with pytest.raises(TypeError, match="version"):

        class _Bad2(AgentAdapter):
            name: ClassVar[str] = "bad"
            # version defaults to "0.0.0" — should fail

            def detect(self, workspace: Path) -> bool:
                return False

            def install_hooks(self, workspace: Path) -> None:
                return None

            def inject_context(self, change_id: str) -> str:
                return ""

            def agents_md_subsection(self) -> str:
                return ""


def test_valid_subclass_instantiable() -> None:
    a = _MinimalAdapter()
    assert a.name == "minimal"
    assert a.version == "0.1.0"
    assert a.detect(Path(".")) is False
    assert a.install_hooks(Path(".")) is None
    assert a.inject_context("c1") == ""
    assert a.agents_md_subsection() == ""


def test_on_uninstall_default_is_noop() -> None:
    a = _MinimalAdapter()
    assert a.on_uninstall(Path(".")) is None


def test_capabilities_canonical_keys() -> None:
    a = _MinimalAdapter()
    expected = {
        "pre_tool_use_hook",
        "post_tool_use_hook",
        "session_start_hook",
        "session_end_hook",
        "pre_commit_hook",
        "rules_file_injection",
        "mcp_server",
        "subprocess_execution",
    }
    assert set(a.capabilities) == expected
