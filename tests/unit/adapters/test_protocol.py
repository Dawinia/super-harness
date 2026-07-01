from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from super_harness.adapters import AgentAdapter, FrameworkAdapter
from super_harness.core.events import Event


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
        "turn_end_feedback_hook": False,
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
        "turn_end_feedback_hook",
    }
    assert set(a.capabilities) == expected


# ---------------------------------------------------------------------------
# FrameworkAdapter tests
# ---------------------------------------------------------------------------


class _MinimalFrameworkAdapter(FrameworkAdapter):
    name: ClassVar[str] = "minimal-framework"
    version: ClassVar[str] = "0.1.0"

    def detect(self, workspace: Path) -> bool:
        return False

    def observe(self, workspace: Path) -> Iterator[Event]:
        return iter([])

    def get_state(self, change_id: str) -> dict[str, Any] | None:
        return None

    def verification_checks(self) -> list[dict[str, Any]]:
        return []

    def agents_md_subsection(self) -> str:
        return ""


def test_framework_adapter_is_abstract() -> None:
    with pytest.raises(TypeError):
        FrameworkAdapter()  # type: ignore[abstract]


def test_framework_adapter_subclass_missing_abstractmethods_not_instantiable() -> None:
    class _Incomplete(FrameworkAdapter):
        name: ClassVar[str] = "incomplete-framework"
        version: ClassVar[str] = "0.1.0"

        def detect(self, workspace: Path) -> bool:  # missing the other 4
            return False

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_framework_adapter_subclass_must_define_name() -> None:
    with pytest.raises(TypeError, match="name"):

        class _Bad(FrameworkAdapter):
            version: ClassVar[str] = "0.1.0"

            def detect(self, workspace: Path) -> bool:
                return False

            def observe(self, workspace: Path) -> Iterator[Event]:
                return iter([])

            def get_state(self, change_id: str) -> dict[str, Any] | None:
                return None

            def verification_checks(self) -> list[dict[str, Any]]:
                return []

            def agents_md_subsection(self) -> str:
                return ""


def test_framework_adapter_subclass_must_define_version() -> None:
    with pytest.raises(TypeError, match="version"):

        class _Bad2(FrameworkAdapter):
            name: ClassVar[str] = "bad-framework"
            # version defaults to "0.0.0" — should fail

            def detect(self, workspace: Path) -> bool:
                return False

            def observe(self, workspace: Path) -> Iterator[Event]:
                return iter([])

            def get_state(self, change_id: str) -> dict[str, Any] | None:
                return None

            def verification_checks(self) -> list[dict[str, Any]]:
                return []

            def agents_md_subsection(self) -> str:
                return ""


def test_framework_adapter_valid_subclass_instantiable() -> None:
    f = _MinimalFrameworkAdapter()
    assert f.name == "minimal-framework"
    assert f.version == "0.1.0"
    assert f.detect(Path(".")) is False
    assert list(f.observe(Path("."))) == []
    assert f.get_state("c1") is None
    assert f.verification_checks() == []
    assert f.agents_md_subsection() == ""


def test_framework_adapter_on_uninstall_default_is_noop() -> None:
    f = _MinimalFrameworkAdapter()
    assert f.on_uninstall(Path(".")) is None


def test_framework_adapter_watch_paths_default_empty() -> None:
    # Additive non-abstract default: no live watch unless a subclass overrides.
    f = _MinimalFrameworkAdapter()
    assert f.watch_paths(Path(".")) == []


def test_framework_adapter_is_fallback_defaults_false() -> None:
    assert _MinimalFrameworkAdapter.is_fallback is False
    f = _MinimalFrameworkAdapter()
    assert f.is_fallback is False


def test_framework_adapter_is_fallback_can_be_true() -> None:
    class _FallbackAdapter(FrameworkAdapter):
        name: ClassVar[str] = "plain"
        version: ClassVar[str] = "0.1.0"
        is_fallback: ClassVar[bool] = True

        def detect(self, workspace: Path) -> bool:
            return True

        def observe(self, workspace: Path) -> Iterator[Event]:
            return iter([])

        def get_state(self, change_id: str) -> dict[str, Any] | None:
            return None

        def verification_checks(self) -> list[dict[str, Any]]:
            return []

        def agents_md_subsection(self) -> str:
            return ""

    f = _FallbackAdapter()
    assert f.is_fallback is True


# ---------------------------------------------------------------------------
# WorkspaceContext re-export test
# ---------------------------------------------------------------------------


def test_workspace_context_is_reexported_from_sensors() -> None:
    from super_harness.adapters import WorkspaceContext as AdapterWC
    from super_harness.sensors import WorkspaceContext as SensorWC

    assert AdapterWC is SensorWC


def test_default_format_stop_feedback_is_empty():
    from super_harness.adapters import AgentAdapter
    from super_harness.core.authoring_check import Verdict, Violation

    class _Bare(AgentAdapter):
        name: ClassVar[str] = "bare"
        version: ClassVar[str] = "0.1.0"
        capabilities: ClassVar[dict[str, bool]] = {}

        def detect(self, w: Path) -> bool:
            return False

        def install_hooks(self, w: Path) -> None: ...

        def inject_context(self, c: str) -> str:
            return ""

        def agents_md_subsection(self) -> str:
            return ""

    v = Verdict(violations=[Violation("d", "x", "docs/decisions/d.md")])
    assert _Bare().format_stop_feedback(v) == ""


def test_stop_should_check_defaults_true() -> None:
    # A conforming agent that does not override runs the check on every Stop.
    assert _MinimalAdapter().stop_should_check({"stop_hook_active": True}) is True
    assert _MinimalAdapter().stop_should_check({}) is True
