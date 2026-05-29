"""Unit tests for `super-harness sensor list` (Phase 3 Task 3.5).

Covers the six mandatory shapes per the plan:
  1. No `.harness/` workspace → EXIT_NO_CONFIG
  2. Empty registry, no yaml → exit 0, helpful empty message
  3. Built-in only (no yaml) → output contains the registered builtin
  4. Plugin via yaml path+class → output contains the plugin name
  5. `--json` flag → valid JSON envelope with expected schema
  6. Builtin + plugin combined → output distinguishes the two sources
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.cli.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.sensors import ActivityType, Determinism, Sensor, SensorResult
from super_harness.sensors import registry as sensors_registry
from super_harness.sensors.registry import register_builtin


class _Stub(Sensor):
    name: ClassVar[str] = "stub-runner"
    version: ClassVar[str] = "0.1.0"
    triggers_on_events: ClassVar[tuple[str, ...]] = ("plan_ready",)
    triggers_on_activities: ClassVar[tuple[ActivityType, ...]] = ()
    determinism: ClassVar[Determinism] = "computational"

    def check(self, trigger, context):  # type: ignore[no-untyped-def]
        return SensorResult(status="pass", summary="ok")


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot `_BUILTIN` so per-test registrations don't leak.

    Uses a snapshot-copy of the current `_BUILTIN` rather than an empty
    dict so that Phase 5/8/11/13 built-in registrations (which happen at
    import time) remain visible to tests that expect them — keeping this
    aligned with the registry-test fixture pattern in
    `tests/unit/sensors/test_registry.py`.
    """
    monkeypatch.setattr(sensors_registry, "_BUILTIN", dict(sensors_registry._BUILTIN))


@pytest.fixture
def harness_workspace(tmp_path: Path) -> Path:
    """Provide a tmp dir with an empty `.harness/` directory."""
    (tmp_path / ".harness").mkdir()
    return tmp_path


def _write_plugin_module(path: Path, class_name: str, sensor_name: str) -> None:
    path.write_text(
        "from typing import ClassVar\n"
        "from super_harness.sensors import Sensor, SensorResult\n"
        f"class {class_name}(Sensor):\n"
        f"    name: ClassVar[str] = '{sensor_name}'\n"
        "    version: ClassVar[str] = '0.0.1'\n"
        "    triggers_on_events: ClassVar[tuple[str, ...]] = ('plan_ready',)\n"
        "    def check(self, trigger, context):\n"
        "        return SensorResult(status='pass', summary='ok')\n"
    )


def test_list_when_no_harness(tmp_path: Path, isolated_registry: None) -> None:
    """Outside a `.harness/` workspace, exit EXIT_NO_CONFIG with a hint."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "sensor", "list"])
    assert r.exit_code == EXIT_NO_CONFIG
    assert "No .harness/" in r.stderr or "No .harness/" in r.output


def test_list_empty_registry(
    harness_workspace: Path,
    isolated_registry: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`.harness/` exists, no yaml, no builtins → exit 0 with empty message.

    Phase 8 registers `verification-runner` as the first built-in sensor at
    import time, so the registry is no longer empty by default. This test
    targets the empty-state CLI path specifically, so it clears the
    (isolated) registry snapshot to exercise the "no sensors" message.
    """
    monkeypatch.setattr(sensors_registry, "_BUILTIN", {})
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "sensor", "list"])
    assert r.exit_code == EXIT_OK
    # Contributor-helpful empty-state message
    assert "No sensors registered" in r.output


def test_list_builtin_only(harness_workspace: Path, isolated_registry: None) -> None:
    """One builtin registered, no yaml → output contains the builtin name."""
    register_builtin("stub-runner", _Stub)
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "sensor", "list"])
    assert r.exit_code == EXIT_OK
    assert "stub-runner" in r.output
    assert "0.1.0" in r.output
    assert "built-in" in r.output


def test_list_with_plugin_yaml(harness_workspace: Path, isolated_registry: None) -> None:
    """Plugin yaml entry → output contains plugin name and 'plugin' label."""
    plugin_path = harness_workspace / "my_sensor.py"
    _write_plugin_module(plugin_path, "MySensor", "my-custom")
    yml = harness_workspace / ".harness" / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"sensors": [{"my-custom": {"path": str(plugin_path), "class": "MySensor"}}]}
        )
    )
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "sensor", "list"])
    assert r.exit_code == EXIT_OK
    assert "my-custom" in r.output
    assert "plugin" in r.output


def test_list_json_output(harness_workspace: Path, isolated_registry: None) -> None:
    """`--json` → valid envelope with sensors list shape."""
    register_builtin("stub-runner", _Stub)
    plugin_path = harness_workspace / "p.py"
    _write_plugin_module(plugin_path, "PluginSensor", "p-id")
    yml = harness_workspace / ".harness" / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"sensors": [{"p-id": {"path": str(plugin_path), "class": "PluginSensor"}}]}
        )
    )
    r = CliRunner().invoke(
        main, ["--workspace", str(harness_workspace), "--json", "sensor", "list"]
    )
    assert r.exit_code == EXIT_OK
    payload = json.loads(r.output)
    assert payload["command"] == "sensor list"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == EXIT_OK
    sensors = payload["data"]["sensors"]
    by_name = {s["name"]: s for s in sensors}
    assert "stub-runner" in by_name
    assert by_name["stub-runner"]["source"] == "built-in"
    assert by_name["stub-runner"]["version"] == "0.1.0"
    # M-4 symmetry: built-ins emit `path: null` so JSON consumers can
    # rely on the key existing on every row.
    assert "path" in by_name["stub-runner"]
    assert by_name["stub-runner"]["path"] is None
    assert "p-id" in by_name
    assert by_name["p-id"]["source"] == "plugin"
    # Plugin entries expose the path so users can grep their config.
    assert by_name["p-id"]["path"] == str(plugin_path)


def test_list_marks_builtin_vs_plugin(
    harness_workspace: Path, isolated_registry: None
) -> None:
    """Mixed builtin + plugin → human output distinguishes the two sources."""
    register_builtin("stub-runner", _Stub)
    plugin_path = harness_workspace / "p2.py"
    _write_plugin_module(plugin_path, "PSensor", "p-name")
    yml = harness_workspace / ".harness" / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"sensors": [{"p-name": {"path": str(plugin_path), "class": "PSensor"}}]}
        )
    )
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "sensor", "list"])
    assert r.exit_code == EXIT_OK
    # Both names present
    assert "stub-runner" in r.output
    assert "p-name" in r.output
    # Sources differentiated
    assert "built-in" in r.output
    assert "plugin" in r.output


def test_list_reports_yaml_validation_errors(
    harness_workspace: Path, isolated_registry: None
) -> None:
    """Malformed sensors.yaml → EXIT_VALIDATION with the registry's error.

    Regression guard for the I-1 review fix: prior to invoking
    `load_sensors` unconditionally when the yaml exists, malformed
    shapes (e.g. `sensors: "not-a-list"`) silently produced
    "No sensors registered." with exit code 0. The error must surface.
    """
    yml = harness_workspace / ".harness" / "sensors.yaml"
    yml.write_text("sensors: not-a-list")
    r = CliRunner().invoke(
        main, ["--workspace", str(harness_workspace), "sensor", "list"]
    )
    assert r.exit_code == EXIT_VALIDATION
    # `core/_registry.py` raises `ValueError("... 'sensors' must be a list ...")`
    # which is then prefixed by `super-harness sensor list: ` on stderr.
    combined = r.output + (r.stderr or "")
    assert "must be a list" in combined
    assert "super-harness sensor list" in combined
