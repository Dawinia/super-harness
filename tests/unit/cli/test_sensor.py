"""Unit tests for `super-harness sensor list` (Phase 3 Task 3.5).

v0.1 is builtin-only. Covers:
  1. No `.harness/` workspace → EXIT_NO_CONFIG
  2. Empty registry, no yaml → exit 0, helpful empty message
  3. Built-in only (no yaml) → output contains the registered builtin
  4. `--json` flag → valid JSON envelope with expected schema (built-in rows only)
  5. A dict/plugin entry → EXIT_VALIDATION ("not supported in v0.1"), no exec
  6. Malformed yaml (non-list) → EXIT_VALIDATION with the registry's error
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
import yaml
from click.testing import CliRunner

from super_harness.cli import main
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
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


def test_list_json_output(harness_workspace: Path, isolated_registry: None) -> None:
    """`--json` → valid envelope with builtin-only sensors list shape."""
    register_builtin("stub-runner", _Stub)
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
    # Built-ins emit `path: null` so JSON consumers can rely on the key
    # existing on every row.
    assert "path" in by_name["stub-runner"]
    assert by_name["stub-runner"]["path"] is None


def test_list_rejects_plugin_entry(
    harness_workspace: Path, isolated_registry: None
) -> None:
    """A dict/plugin sensors.yaml entry → EXIT_VALIDATION, module never exec'd."""
    sentinel = harness_workspace / "EXECUTED"
    plugin_path = harness_workspace / "evil.py"
    plugin_path.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('pwned')\n"
    )
    yml = harness_workspace / ".harness" / "sensors.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"sensors": [{"my-custom": {"path": str(plugin_path), "class": "Evil"}}]}
        )
    )
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "sensor", "list"])
    assert r.exit_code == EXIT_VALIDATION
    assert "not supported in v0.1" in (r.output + (r.stderr or ""))
    assert not sentinel.exists(), "plugin module was executed — RCE surface still open"


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


def test_list_corrupt_yaml_exits_validation_not_traceback(
    harness_workspace: Path, isolated_registry: None
) -> None:
    """Syntactically corrupt sensors.yaml → EXIT_VALIDATION, not an uncaught
    `yaml.YAMLError` traceback (the loader's `yaml.safe_load` is unguarded)."""
    yml = harness_workspace / ".harness" / "sensors.yaml"
    yml.write_text("sensors: [unclosed\n")  # invalid YAML
    r = CliRunner().invoke(
        main, ["--workspace", str(harness_workspace), "sensor", "list"]
    )
    assert r.exit_code == EXIT_VALIDATION
    assert r.exception is None or isinstance(r.exception, SystemExit)
