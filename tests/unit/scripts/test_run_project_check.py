from __future__ import annotations

import os
import venv
from pathlib import Path
from types import SimpleNamespace

from scripts import run_project_check


def _fake_toolchain(root: Path) -> Path:
    name = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    toolchain = root / ".venv" / name
    toolchain.mkdir(parents=True)
    (toolchain / f"python{suffix}").write_bytes(b"python")
    (toolchain / f"ruff{suffix}").write_bytes(b"ruff")
    return toolchain


def test_missing_project_runtime_is_a_launch_failure(tmp_path: Path, capsys) -> None:
    assert run_project_check.run(["ruff", "check"], root=tmp_path) == 127
    assert "project runtime not found" in capsys.readouterr().err


def test_missing_project_tool_is_a_launch_failure(tmp_path: Path, capsys) -> None:
    _fake_toolchain(tmp_path)
    assert run_project_check.run(["mypy", "src"], root=tmp_path) == 127
    assert "project tool 'mypy' not found" in capsys.readouterr().err


def test_launcher_preserves_argv_and_child_exit_code(tmp_path: Path, monkeypatch) -> None:
    toolchain = _fake_toolchain(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=23)

    monkeypatch.setattr(run_project_check.subprocess, "run", fake_run)
    code = run_project_check.run(
        ["ruff", "check", "path with spaces", "中文"], root=tmp_path
    )
    assert code == 23
    assert calls == [
        (
            [str(toolchain / f"ruff{'.exe' if os.name == 'nt' else ''}"),
             "check", "path with spaces", "中文"],
            {"cwd": tmp_path, "check": False, "env": {
                **os.environ,
                "PATH": str(toolchain) + os.pathsep + os.environ.get("PATH", ""),
                "PYTHONUTF8": "1",
            }},
        )
    ]


def test_launcher_supports_project_python_module_form(tmp_path: Path, monkeypatch) -> None:
    toolchain = _fake_toolchain(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["check"] is False
        assert kwargs["env"]["PATH"].split(os.pathsep)[0] == str(toolchain)
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_project_check.subprocess, "run", fake_run)
    assert run_project_check.run(["-m", "pytest", "-q"], root=tmp_path) == 0
    assert calls == [[str(toolchain / f"python{'.exe' if os.name == 'nt' else ''}"),
                      "-m", "pytest", "-q"]]


def test_real_project_venv_python_marker_and_exact_exit_code(tmp_path: Path, capfd) -> None:
    venv.EnvBuilder(with_pip=False).create(tmp_path / ".venv")
    code = run_project_check.run(
        [
            "python",
            "-c",
            "import sys; print('TARGET_EXECUTED'); "
            "print('ERR_MARKER', file=sys.stderr); sys.exit(23)",
        ],
        root=tmp_path,
    )
    out, err = capfd.readouterr()
    assert code == 23
    assert "TARGET_EXECUTED" in out
    assert "ERR_MARKER" in err
