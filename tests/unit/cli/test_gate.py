"""Unit tests for `super-harness gate list` (Phase 3 Task 3.5 — symmetric mirror).

Covers the six mandatory shapes per the plan (mirrors sensor list):
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
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION
from super_harness.gates import Gate, GateDecision, GateFiresOn, GateResult
from super_harness.gates import registry as gates_registry
from super_harness.gates.registry import register_builtin


class _StubGate(Gate):
    name: ClassVar[str] = "stub-gate"
    version: ClassVar[str] = "0.1.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    def decide(self, action, state, events):  # type: ignore[no-untyped-def]
        return GateResult(decision=GateDecision.ALLOW)


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot `_BUILTIN` so per-test registrations don't leak.

    Uses a snapshot-copy of the current `_BUILTIN` rather than an empty
    dict so that later-phase built-in registrations (at import time)
    remain visible — keeping this aligned with the registry-test fixture
    pattern in `tests/unit/gates/test_registry.py`.
    """
    monkeypatch.setattr(gates_registry, "_BUILTIN", dict(gates_registry._BUILTIN))


@pytest.fixture
def harness_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir()
    return tmp_path


def _write_plugin_module(path: Path, class_name: str, gate_name: str) -> None:
    path.write_text(
        "from typing import ClassVar\n"
        "from super_harness.gates import Gate, GateDecision, GateResult\n"
        f"class {class_name}(Gate):\n"
        f"    name: ClassVar[str] = '{gate_name}'\n"
        "    version: ClassVar[str] = '0.0.1'\n"
        "    def decide(self, action, state, events):\n"
        "        return GateResult(decision=GateDecision.ALLOW)\n"
    )


def test_list_when_no_harness(tmp_path: Path, isolated_registry: None) -> None:
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "gate", "list"])
    assert r.exit_code == EXIT_NO_CONFIG
    assert "No .harness/" in r.stderr or "No .harness/" in r.output


def test_list_empty_registry(
    harness_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Use a genuinely empty builtin table — the `isolated_registry` fixture
    # snapshot-copies `_BUILTIN`, which now ships the `pre-tool-use` builtin,
    # so an empty-message assertion needs the table cleared outright.
    monkeypatch.setattr(gates_registry, "_BUILTIN", {})
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "gate", "list"])
    assert r.exit_code == EXIT_OK
    assert "No gates registered" in r.output


def test_list_includes_pre_tool_use_builtin(
    harness_workspace: Path, isolated_registry: None
) -> None:
    # The `pre-tool-use` gate is registered at import time (registry.py
    # bottom), so `gate list` must surface it as a built-in row.
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "gate", "list"])
    assert r.exit_code == EXIT_OK
    assert "pre-tool-use" in r.output
    assert "built-in" in r.output


def test_list_builtin_only(harness_workspace: Path, isolated_registry: None) -> None:
    register_builtin("stub-gate", _StubGate)
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "gate", "list"])
    assert r.exit_code == EXIT_OK
    assert "stub-gate" in r.output
    assert "0.1.0" in r.output
    assert "built-in" in r.output


def test_list_with_plugin_yaml(harness_workspace: Path, isolated_registry: None) -> None:
    plugin_path = harness_workspace / "my_gate.py"
    _write_plugin_module(plugin_path, "MyGate", "my-custom-gate")
    yml = harness_workspace / ".harness" / "gates.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"gates": [{"my-custom-gate": {"path": str(plugin_path), "class": "MyGate"}}]}
        )
    )
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "gate", "list"])
    assert r.exit_code == EXIT_OK
    assert "my-custom-gate" in r.output
    assert "plugin" in r.output


def test_list_json_output(harness_workspace: Path, isolated_registry: None) -> None:
    register_builtin("stub-gate", _StubGate)
    plugin_path = harness_workspace / "g.py"
    _write_plugin_module(plugin_path, "PluginGate", "p-id")
    yml = harness_workspace / ".harness" / "gates.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"gates": [{"p-id": {"path": str(plugin_path), "class": "PluginGate"}}]}
        )
    )
    r = CliRunner().invoke(
        main, ["--workspace", str(harness_workspace), "--json", "gate", "list"]
    )
    assert r.exit_code == EXIT_OK
    payload = json.loads(r.output)
    assert payload["command"] == "gate list"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == EXIT_OK
    gates = payload["data"]["gates"]
    by_name = {g["name"]: g for g in gates}
    assert "stub-gate" in by_name
    assert by_name["stub-gate"]["source"] == "built-in"
    assert by_name["stub-gate"]["version"] == "0.1.0"
    # M-4 symmetry: built-ins emit `path: null` so JSON consumers can
    # rely on the key existing on every row.
    assert "path" in by_name["stub-gate"]
    assert by_name["stub-gate"]["path"] is None
    assert "p-id" in by_name
    assert by_name["p-id"]["source"] == "plugin"
    assert by_name["p-id"]["path"] == str(plugin_path)


def test_list_marks_builtin_vs_plugin(
    harness_workspace: Path, isolated_registry: None
) -> None:
    register_builtin("stub-gate", _StubGate)
    plugin_path = harness_workspace / "g2.py"
    _write_plugin_module(plugin_path, "PGate", "p-name")
    yml = harness_workspace / ".harness" / "gates.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"gates": [{"p-name": {"path": str(plugin_path), "class": "PGate"}}]}
        )
    )
    r = CliRunner().invoke(main, ["--workspace", str(harness_workspace), "gate", "list"])
    assert r.exit_code == EXIT_OK
    assert "stub-gate" in r.output
    assert "p-name" in r.output
    assert "built-in" in r.output
    assert "plugin" in r.output


def test_list_reports_yaml_validation_errors(
    harness_workspace: Path, isolated_registry: None
) -> None:
    """Malformed gates.yaml → EXIT_VALIDATION with the registry's error.

    Regression guard for the I-1 review fix: prior to invoking
    `load_gates` unconditionally when the yaml exists, malformed
    shapes (e.g. `gates: "not-a-list"`) silently produced
    "No gates registered." with exit code 0. The error must surface.
    """
    yml = harness_workspace / ".harness" / "gates.yaml"
    yml.write_text("gates: not-a-list")
    r = CliRunner().invoke(
        main, ["--workspace", str(harness_workspace), "gate", "list"]
    )
    assert r.exit_code == EXIT_VALIDATION
    combined = r.output + (r.stderr or "")
    assert "must be a list" in combined
    assert "super-harness gate list" in combined
