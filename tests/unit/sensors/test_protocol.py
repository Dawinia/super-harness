from __future__ import annotations

from typing import ClassVar

import pytest

from super_harness.sensors import Activity, ActivityType, Determinism, Sensor, SensorResult


class _Echo(Sensor):
    name: ClassVar[str] = "echo"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[list[str]] = ["plan_ready"]
    triggers_on_activities: ClassVar[list[ActivityType]] = []
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
