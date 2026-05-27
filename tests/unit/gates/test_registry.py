from __future__ import annotations

import logging
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from super_harness.gates import (
    Gate,
    GateDecision,
    GateFiresOn,
    GateResult,
)
from super_harness.gates.registry import load_gates, register_builtin


class _StubGate(Gate):
    name: ClassVar[str] = "stub-gate"
    version: ClassVar[str] = "0.1.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    def decide(self, action, state, events):  # type: ignore[no-untyped-def]
        return GateResult(decision=GateDecision.ALLOW)


@pytest.fixture(autouse=True)
def _register_stub_builtin() -> None:
    register_builtin("stub-gate", _StubGate)


def test_load_returns_empty_when_yaml_missing(tmp_path: Path) -> None:
    assert load_gates(tmp_path / "missing.yaml") == []


def test_register_builtin_then_load(tmp_path: Path) -> None:
    yml = tmp_path / "gates.yaml"
    yml.write_text(yaml.safe_dump({"gates": ["stub-gate"]}))
    gates = load_gates(yml, builtin_only=True)
    assert len(gates) == 1
    assert isinstance(gates[0], _StubGate)


def test_load_skips_unknown_builtin_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    yml = tmp_path / "gates.yaml"
    yml.write_text(yaml.safe_dump({"gates": ["typo-gate"]}))
    with caplog.at_level(logging.WARNING):
        gates = load_gates(yml)
    assert gates == []
    assert any("typo-gate" in rec.message for rec in caplog.records)


def test_load_custom_plugin(tmp_path: Path) -> None:
    mod = tmp_path / "my_gate.py"
    mod.write_text(
        "from typing import ClassVar\n"
        "from super_harness.gates import Gate, GateDecision, GateResult\n"
        "class MyGate(Gate):\n"
        "    name: ClassVar[str] = 'my-gate'\n"
        "    version: ClassVar[str] = '0.0.1'\n"
        "    def decide(self, action, state, events):\n"
        "        return GateResult(decision=GateDecision.ALLOW)\n"
    )
    yml = tmp_path / "gates.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"gates": [{"my-id": {"path": str(mod), "class": "MyGate", "enabled": True}}]}
        )
    )
    gates = load_gates(yml)
    assert any(g.name == "my-gate" for g in gates)


def test_load_rejects_non_gate_plugin_class(tmp_path: Path) -> None:
    mod = tmp_path / "bad_gate.py"
    mod.write_text("class NotAGate:\n    pass\n")
    yml = tmp_path / "gates.yaml"
    yml.write_text(
        yaml.safe_dump(
            {"gates": [{"bad": {"path": str(mod), "class": "NotAGate", "enabled": True}}]}
        )
    )
    with pytest.raises(TypeError, match="not a Gate subclass"):
        load_gates(yml)


def test_load_rejects_non_list_entries(tmp_path: Path) -> None:
    yml = tmp_path / "gates.yaml"
    yml.write_text(yaml.safe_dump({"gates": 42}))
    with pytest.raises(ValueError, match="must be a list"):
        load_gates(yml)
