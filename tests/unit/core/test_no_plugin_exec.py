"""Guard: the arbitrary-code-execution plugin primitive stays deleted (F12).

v0.1 is builtin-only. If v0.2 reintroduces plugin loading it must ship with a
sandbox and this guard must be updated deliberately — not silently regressed.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import super_harness

# Import-from-path / dynamic-exec primitives that would reopen the RCE surface.
_FORBIDDEN = (
    "exec_module(",
    "spec_from_file_location(",
    "load_class_from_path",
    "_plugin_loader",
)


def test_plugin_loader_module_is_gone() -> None:
    try:
        importlib.import_module("super_harness.core._plugin_loader")
    except ModuleNotFoundError:
        return
    raise AssertionError("core._plugin_loader was reintroduced without review")


def test_no_load_from_path_primitive_in_package() -> None:
    pkg_root = Path(super_harness.__file__).parent
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for needle in _FORBIDDEN:
            if needle in text:
                offenders.append(f"{py.relative_to(pkg_root)}: {needle}")
    assert not offenders, (
        "arbitrary-code-execution plugin primitive reappeared in the package "
        f"(F12 regression): {offenders}"
    )
