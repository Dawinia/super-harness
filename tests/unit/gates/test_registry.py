"""Gate registry tests — Gate-specific symmetry only.

Schema / error-path coverage for the shared loader (`core/_registry.py`)
lives in `tests/unit/sensors/test_registry.py`. This file verifies the
Gate-side wrapper handles the gate-specific contract (top-key="gates",
builtin resolution, and the v0.1 builtin-only guarantee — a dict/plugin entry
is rejected without executing its module).
"""
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
from super_harness.gates import registry as gates_registry
from super_harness.gates.registry import (
    get_builtin,
    list_builtins,
    load_gates,
    register_builtin,
)


class _StubGate(Gate):
    name: ClassVar[str] = "stub-gate"
    version: ClassVar[str] = "0.1.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    def decide(self, action, state, events):  # type: ignore[no-untyped-def]
        return GateResult(decision=GateDecision.ALLOW)


@pytest.fixture(autouse=True)
def _stub_builtin_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Snapshot `_BUILTIN` and register a stub gate, restoring on teardown.

    Without this, registrations leak into the module-global `_BUILTIN` dict
    and pollute subsequent tests (and any Phase 3.5 CLI test that
    enumerates the gate registry).
    """
    monkeypatch.setattr(gates_registry, "_BUILTIN", dict(gates_registry._BUILTIN))
    register_builtin("stub-gate", _StubGate)


def test_load_returns_empty_when_yaml_missing(tmp_path: Path) -> None:
    assert load_gates(tmp_path / "missing.yaml") == []


def test_register_builtin_then_load(tmp_path: Path) -> None:
    yml = tmp_path / "gates.yaml"
    yml.write_text(yaml.safe_dump({"gates": ["stub-gate"]}))
    gates = load_gates(yml)
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
    # Pin both level AND message — a future log.debug downgrade should fail
    # this test (contributor-UX regression guard).
    assert any(
        rec.levelno == logging.WARNING and "typo-gate" in rec.message
        for rec in caplog.records
    )


def test_plugin_entry_is_rejected_without_executing(tmp_path: Path) -> None:
    """A dict (plugin path+class) gate entry must raise AND never import (exec)
    its module. v0.1 is builtin-only — this is the F12 no-arbitrary-code guard.
    """
    sentinel = tmp_path / "EXECUTED"
    mod = tmp_path / "evil_gate.py"
    mod.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('pwned')\n",
        encoding="utf-8",
    )
    yml = tmp_path / "gates.yaml"
    yml.write_text(
        "gates:\n"
        "  - my-custom:\n"
        f"      path: {mod}\n"
        "      class: Evil\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="custom plugins are not supported"):
        load_gates(yml)
    assert not sentinel.exists(), "plugin module was executed — RCE surface still open"


def test_load_rejects_non_list_entries(tmp_path: Path) -> None:
    yml = tmp_path / "gates.yaml"
    yml.write_text(yaml.safe_dump({"gates": 42}))
    with pytest.raises(ValueError, match="must be a list"):
        load_gates(yml)


def test_load_rejects_non_mapping_top_level(tmp_path: Path) -> None:
    """A valid-but-non-mapping top level (bare list/scalar) → ValueError, not a
    leaked AttributeError from `cfg.get(...)`."""
    yml = tmp_path / "gates.yaml"
    yml.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="top level must be a mapping"):
        load_gates(yml)


def test_get_builtin_known_and_unknown() -> None:
    """Direct coverage for `get_builtin` (I-3 review fix).

    Previously only exercised indirectly via the Phase 3.5 CLI tests. The
    `_StubGate` is registered as `stub-gate` by the autouse fixture above,
    which already snapshot-copies `_BUILTIN`.
    """
    assert get_builtin("stub-gate") is _StubGate
    assert get_builtin("definitely-not-registered") is None


def test_list_builtins_returns_registered_names_sorted() -> None:
    # _StubGate is registered by the autouse fixture; add a second to verify
    # ordering. Both registrations are isolated to this test by monkeypatch.
    class _Another(Gate):
        name: ClassVar[str] = "another-gate"
        version: ClassVar[str] = "0.1.0"
        fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

        def decide(self, action, state, events):  # type: ignore[no-untyped-def]
            return GateResult(decision=GateDecision.ALLOW)

    register_builtin("another-gate", _Another)
    names = list_builtins()
    assert "stub-gate" in names
    assert "another-gate" in names
    assert names == sorted(names)
