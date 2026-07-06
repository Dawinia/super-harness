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
from super_harness.sensors import registry as sensors_registry
from super_harness.sensors.registry import (
    get_builtin,
    list_builtins,
    load_sensors,
    register_builtin,
)


class _Stub(Sensor):
    name: ClassVar[str] = "stub-runner"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ()
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, context):  # type: ignore[no-untyped-def]
        return SensorResult(status="pass", summary="ok")


@pytest.fixture(autouse=True)
def _stub_builtin_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot `_BUILTIN` and register a stub builtin, restoring on teardown.

    Without this, every `register_builtin(...)` call leaks into the
    module-global `_BUILTIN` dict and pollutes subsequent tests (and any
    Phase 3.5 CLI test that enumerates the registry).
    """
    # monkeypatch.setattr auto-reverts the dict reference at teardown.
    monkeypatch.setattr(sensors_registry, "_BUILTIN", dict(sensors_registry._BUILTIN))
    register_builtin("stub-runner", _Stub)


def test_load_returns_empty_when_yaml_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    assert load_sensors(missing) == []


def test_load_builtin_by_name(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": ["stub-runner"]}))
    sensors = load_sensors(yml)
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
    # Pin both level AND message — a future log.debug downgrade should fail
    # this test (contributor-UX regression guard).
    assert any(
        rec.levelno == logging.WARNING and "typo-name-not-builtin" in rec.message
        for rec in caplog.records
    )


def test_plugin_entry_is_rejected_without_executing(tmp_path: Path) -> None:
    """A dict (plugin path+class) sensor entry must raise AND never import (exec)
    its module. v0.1 is builtin-only — this is the F12 no-arbitrary-code guard.
    """
    sentinel = tmp_path / "EXECUTED"
    mod = tmp_path / "evil_sensor.py"
    mod.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('pwned')\n",
        encoding="utf-8",
    )
    yml = tmp_path / "sensors.yaml"
    yml.write_text(
        "sensors:\n"
        "  - my-custom:\n"
        f"      path: {mod}\n"
        "      class: Evil\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="custom plugins are not supported"):
        load_sensors(yml)
    assert not sentinel.exists(), "plugin module was executed — RCE surface still open"


def test_load_rejects_non_list_entries(tmp_path: Path) -> None:
    yml = tmp_path / "sensors.yaml"
    yml.write_text(yaml.safe_dump({"sensors": "single-string"}))
    with pytest.raises(ValueError, match="must be a list"):
        load_sensors(yml)


def test_load_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    """A valid-but-non-mapping top level (bare list/scalar) → ValueError, not a
    leaked AttributeError from `cfg.get(...)`."""
    yml = tmp_path / "sensors.yaml"
    yml.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="top level must be a mapping"):
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


def test_get_builtin_known_and_unknown() -> None:
    """Direct coverage for `get_builtin` (I-3 review fix).

    Previously only exercised indirectly via the Phase 3.5 CLI tests. The
    `_Stub` is registered as `stub-runner` by the autouse fixture above,
    which already snapshot-copies `_BUILTIN`.
    """
    assert get_builtin("stub-runner") is _Stub
    assert get_builtin("definitely-not-registered") is None


def test_list_builtins_returns_registered_names_sorted() -> None:
    # _Stub is registered by the autouse fixture; add a second name to verify
    # ordering. Both registrations are isolated to this test by monkeypatch.
    class _Another(Sensor):
        name: ClassVar[str] = "another-stub"
        version: ClassVar[str] = "0.1.0"
        triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
        determinism: ClassVar[Determinism] = "computational"

        def check(self, trigger, context):  # type: ignore[no-untyped-def]
            return SensorResult(status="pass", summary="ok")

    register_builtin("another-stub", _Another)
    names = list_builtins()
    assert "stub-runner" in names
    assert "another-stub" in names
    assert names == sorted(names)
