from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import ClassVar

import pytest

from super_harness.sensors import (
    Activity,
    ActivityType,
    Determinism,
    Sensor,
    SensorResult,
    WorkspaceContext,
)


class _Echo(Sensor):
    name: ClassVar[str] = "echo"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ()
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, context):  # type: ignore[no-untyped-def]
        return SensorResult(status="pass", summary="ok")


def test_sensor_subclass_instantiable() -> None:
    s = _Echo()
    assert s.name == "echo"
    assert "plan_ready" in s.triggers_on_events


def test_sensor_result_defaults() -> None:
    r = SensorResult(status="pass", summary="ok")
    assert r.emit_events == []
    assert r.details is None


def test_activity_defaults() -> None:
    a = Activity(type="commit")
    assert a.change_id is None
    assert a.payload == {}


def test_sensor_is_abstract() -> None:
    with pytest.raises(TypeError):
        Sensor()  # type: ignore[abstract]


def test_reviewer_strategy_defaults_to_none() -> None:
    assert _Echo().reviewer_strategy() is None


def test_sensor_result_is_frozen() -> None:
    r = SensorResult(status="pass", summary="ok")
    with pytest.raises(FrozenInstanceError):
        r.status = "fail"  # type: ignore[misc]


def test_activity_is_frozen() -> None:
    a = Activity(type="commit")
    with pytest.raises(FrozenInstanceError):
        a.change_id = "x"  # type: ignore[misc]


def test_workspace_context_is_frozen() -> None:
    ctx = WorkspaceContext(workspace_root=Path("/tmp"))
    with pytest.raises(FrozenInstanceError):
        ctx.git_branch = "main"  # type: ignore[misc]


def test_sensor_subclass_must_define_name() -> None:
    with pytest.raises(TypeError, match="name"):

        class _Bad(Sensor):
            version: ClassVar[str] = "0.1.0"

            def check(self, trigger, context):  # type: ignore[no-untyped-def]
                return SensorResult(status="pass", summary="ok")


def test_sensor_subclass_must_define_version() -> None:
    with pytest.raises(TypeError, match="version"):

        class _Bad2(Sensor):
            name: ClassVar[str] = "bad"
            # version defaults to "0.0.0" — should fail

            def check(self, trigger, context):  # type: ignore[no-untyped-def]
                return SensorResult(status="pass", summary="ok")
