"""Run one verification tool from the repository's own virtual environment.

This is intentionally a small direct-execution launcher. It never searches the
ambient PATH for the requested tool, so the harness installation cannot silently
stand in for the project's toolchain.

Examples::

    python -m scripts.run_project_check ruff check src tests scripts
    python -m scripts.run_project_check -m pytest -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def toolchain_dir(root: Path) -> Path | None:
    names = ("Scripts", "bin") if os.name == "nt" else ("bin", "Scripts")
    for name in names:
        candidate = root / ".venv" / name
        if candidate.is_dir():
            return candidate
    return None


def _python_executable(toolchain: Path) -> Path | None:
    names = ("python.exe", "python") if os.name == "nt" else ("python", "python3")
    for name in names:
        candidate = toolchain / name
        if candidate.is_file():
            return candidate
    return None


def _tool_executable(toolchain: Path, name: str) -> Path | None:
    if not name or Path(name).name != name:
        return None
    candidates = [toolchain / name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        candidates.insert(0, toolchain / f"{name}.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _error(message: str) -> int:
    print(f"super-harness project check: {message}", file=sys.stderr)
    return 127


def run(argv: list[str], *, root: Path | None = None) -> int:
    root = root or project_root()
    toolchain = toolchain_dir(root)
    if toolchain is None:
        return _error(f"project runtime not found under {root / '.venv'}")
    if not argv:
        return _error("missing tool or -m module")

    python = _python_executable(toolchain)
    if python is None:
        return _error(f"project Python not found under {toolchain}")

    if argv[0] == "-m":
        if len(argv) < 2 or not argv[1]:
            return _error("-m requires a module name")
        child = [str(python), *argv]
    else:
        tool = _tool_executable(toolchain, argv[0])
        if tool is None:
            return _error(f"project tool {argv[0]!r} not found under {toolchain}")
        child = [str(tool), *argv[1:]]

    env = os.environ.copy()
    env["PATH"] = str(toolchain) + os.pathsep + env.get("PATH", "")
    env["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(child, cwd=root, check=False, env=env)
    except OSError as exc:
        return _error(f"could not launch {child[0]!r}: {exc}")
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    return run(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
