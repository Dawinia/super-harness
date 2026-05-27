from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from super_harness.sensors import (
    ActivityType,
    Determinism,
    Sensor,
    SensorResult,
)
from super_harness.sensors.registry import load_sensors, register_builtin


class _Stub(Sensor):
    name: ClassVar[str] = "stub-runner"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ()
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, context):  # type: ignore[no-untyped-def]
        return SensorResult(status="pass", summary="ok")


@pytest.fixture(autouse=True)
def _register_stub_builtin() -> None:
    """Register a stub builtin for the duration of each test."""
    register_builtin("stub-runner", _Stub)


def test_load_returns_empty_when_yaml_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    assert load_sensors(missing) == []


def test_load_builtin_by_name(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": ["stub-runner"]}))
    sensors = load_sensors(yml, builtin_only=True)
    names = {s.name for s in sensors}
    assert "stub-runner" in names


def test_register_builtin_then_load(tmp_path: Path) -> None:
    class _Other(Sensor):
        name: ClassVar[str] = "other-builtin"
        version: ClassVar[str] = "0.1.0"
        triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
        determinism: ClassVar[Determinism] = "computational"

        def check(self, trigger, context):  # type: ignore[no-untyped-def]
            return SensorResult(status="pass", summary="ok")

    register_builtin("other-builtin", _Other)
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": ["other-builtin"]}))
    sensors = load_sensors(yml)
    assert len(sensors) == 1
    assert isinstance(sensors[0], _Other)


def test_load_skips_unknown_builtin_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": ["typo-name-not-builtin"]}))
    with caplog.at_level(logging.WARNING):
        sensors = load_sensors(yml)
    assert sensors == []
    assert any("typo-name-not-builtin" in rec.message for rec in caplog.records)


def test_load_custom_plugin(tmp_path: Path) -> None:
    mod = tmp_path / "my_sensor.py"
    mod.write_text(
        "from typing import ClassVar\n"
        "from super_harness.sensors import Sensor, SensorResult\n"
        "class MySensor(Sensor):\n"
        "    name: ClassVar[str] = 'my'\n"
        "    version: ClassVar[str] = '0.0.1'\n"
        "    triggers_on_events: ClassVar[tuple[str, ...]] = ('plan_ready',)\n"
        "    determinism: ClassVar[str] = 'computational'\n"
        "    def check(self, trigger, context):\n"
        "        return SensorResult(status='pass', summary='ok')\n"
    )
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"sensors": [{"my-custom": {"path": str(mod), "class": "MySensor", "enabled": True}}]}
        )
    )
    sensors = load_sensors(yml, builtin_only=False)
    assert any(s.name == "my" for s in sensors)


def test_load_skips_disabled_plugin(tmp_path: Path) -> None:
    mod = tmp_path / "disabled_sensor.py"
    mod.write_text(
        "from typing import ClassVar\n"
        "from super_harness.sensors import Sensor, SensorResult\n"
        "class DisabledSensor(Sensor):\n"
        "    name: ClassVar[str] = 'disabled'\n"
        "    version: ClassVar[str] = '0.0.1'\n"
        "    triggers_on_events: ClassVar[tuple[str, ...]] = ('plan_ready',)\n"
        "    def check(self, trigger, context):\n"
        "        return SensorResult(status='pass', summary='ok')\n"
    )
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "sensors": [
                    {
                        "disabled-one": {
                            "path": str(mod),
                            "class": "DisabledSensor",
                            "enabled": False,
                        }
                    }
                ]
            }
        )
    )
    sensors = load_sensors(yml)
    assert sensors == []


def test_load_skips_all_plugins_when_builtin_only(tmp_path: Path) -> None:
    mod = tmp_path / "plug.py"
    mod.write_text(
        "from typing import ClassVar\n"
        "from super_harness.sensors import Sensor, SensorResult\n"
        "class PlugSensor(Sensor):\n"
        "    name: ClassVar[str] = 'plug'\n"
        "    version: ClassVar[str] = '0.0.1'\n"
        "    triggers_on_events: ClassVar[tuple[str, ...]] = ('plan_ready',)\n"
        "    def check(self, trigger, context):\n"
        "        return SensorResult(status='pass', summary='ok')\n"
    )
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "sensors": [
                    "stub-runner",
                    {"plug-id": {"path": str(mod), "class": "PlugSensor", "enabled": True}},
                ]
            }
        )
    )
    sensors = load_sensors(yml, builtin_only=True)
    names = {s.name for s in sensors}
    assert names == {"stub-runner"}


def test_load_rejects_non_sensor_plugin_class(tmp_path: Path) -> None:
    mod = tmp_path / "bad_sensor.py"
    mod.write_text("class NotASensor:\n    pass\n")
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"sensors": [{"bad": {"path": str(mod), "class": "NotASensor", "enabled": True}}]}
        )
    )
    with pytest.raises(TypeError, match="not a Sensor subclass"):
        load_sensors(yml)


def test_load_rejects_plugin_with_missing_path(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": [{"bad": {"class": "X", "enabled": True}}]}))
    with pytest.raises((KeyError, ValueError)):
        load_sensors(yml)


def test_load_rejects_plugin_with_missing_class_key(tmp_path: Path) -> None:
    mod = tmp_path / "x.py"
    mod.write_text("class X: pass\n")
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": [{"bad": {"path": str(mod), "enabled": True}}]}))
    with pytest.raises((KeyError, ValueError)):
        load_sensors(yml)


def test_load_rejects_plugin_with_nonexistent_path(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {
                "sensors": [
                    {
                        "bad": {
                            "path": str(tmp_path / "nonexistent.py"),
                            "class": "X",
                            "enabled": True,
                        }
                    }
                ]
            }
        )
    )
    with pytest.raises((FileNotFoundError, ImportError)):
        load_sensors(yml)


def test_load_rejects_plugin_with_class_not_in_module(tmp_path: Path) -> None:
    mod = tmp_path / "thin.py"
    mod.write_text(
        "from typing import ClassVar\n"
        "from super_harness.sensors import Sensor, SensorResult\n"
        "class Present(Sensor):\n"
        "    name: ClassVar[str] = 'present'\n"
        "    version: ClassVar[str] = '0.0.1'\n"
        "    triggers_on_events: ClassVar[tuple[str, ...]] = ('plan_ready',)\n"
        "    def check(self, trigger, context):\n"
        "        return SensorResult(status='pass', summary='ok')\n"
    )
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"sensors": [{"bad": {"path": str(mod), "class": "Missing", "enabled": True}}]}
        )
    )
    with pytest.raises((AttributeError, ValueError)):
        load_sensors(yml)


def test_load_rejects_plugin_with_multiple_keys(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    # Two top-level keys on the dict entry — the typo case
    yml.write_text(
        "sensors:\n  - my-custom:\n      path: ./foo.py\n      class: Foo\n    enabled: true\n"
    )
    with pytest.raises(ValueError, match="exactly one key"):
        load_sensors(yml)


def test_load_rejects_non_list_entries(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": "single-string"}))
    with pytest.raises(ValueError, match="must be a list"):
        load_sensors(yml)


def test_load_handles_null_top_key(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text("sensors:\n")  # null
    # null becomes [] (default), no error
    assert load_sensors(yml) == []


def test_load_handles_empty_yaml_file(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text("")
    assert load_sensors(yml) == []
