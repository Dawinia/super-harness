"""The shared native process runner used by checks and doc generators.

There are two deliberate command forms:

* a sequence of strings is executed directly (``shell=False``); and
* a string is accepted only with ``shell="sh"`` and is executed by a POSIX
  shell.  On Windows that shell is Git for Windows' native ``usr/bin/sh.exe``.

The runner captures bytes before decoding so ordinary checks can be tolerant of
legacy output while document generators can request strict UTF-8.  It also owns
the timeout cleanup contract shared by decision checks and verification checks.

Lives in ``core`` because it is stdlib-only and is shared by the upper layers.
API stability: **experimental** (v0.1).
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CommandResult",
    "ShellResult",
    "run_command",
    "run_shell",
    "scrubbed_environ",
]

_HARNESS_ENV_PREFIX = "SUPER_HARNESS_"
_WINDOWS_GIT_ENV_VARS = ("GIT_INSTALL_ROOT",)


@dataclass(frozen=True)
class CommandResult:
    """Raw outcome of one command execution; callers shape the domain result."""

    exit_code: int          # -1 sentinel when timed_out or spawn_error
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    spawn_error: str | None  # command was not started
    decode_error: str | None = None  # strict output decoding failed


# Existing callers use this name. Keep it as an alias, not a second result
# model, so old and new call sites share the same fields and semantics.
ShellResult = CommandResult


def scrubbed_environ() -> dict[str, str]:
    """Ambient ``os.environ`` minus every ``SUPER_HARNESS_*`` knob."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_HARNESS_ENV_PREFIX)
    }


def run_command(
    command: str | Sequence[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
    shell: str | None = None,
    required_tools: Sequence[str] = (),
    decode_errors: str = "replace",
) -> CommandResult:
    """Run a direct argv command or an explicitly selected POSIX shell command.

    ``env=None`` inherits the ambient environment. A supplied mapping replaces
    the child environment, so PATH-resolved direct commands require PATH in the
    mapping. Missing executables, missing shells, invalid cwd, and launch errors
    become ``spawn_error`` records; this function never turns a launch failure
    into a successful shell exit.

    ``required_tools`` is used by the ratified decision checks to preflight
    utilities such as ``grep`` before running a negated shell expression. A
    missing utility is therefore a structured launch failure rather than the
    POSIX ``! nonexistent`` zero exit-value trap.
    """
    t0 = time.perf_counter()
    child_env = dict(os.environ if env is None else env)

    argv, prepared_env, error = _prepare_command(
        command,
        shell=shell,
        cwd=cwd,
        env=child_env,
        required_tools=required_tools,
    )
    if error is not None or argv is None:
        return _result_error(error or "invalid command", t0)

    try:
        proc = subprocess.Popen(
            argv,
            shell=False,
            cwd=str(cwd),
            env=prepared_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            creationflags=_creation_flags(),
        )
    except (OSError, ValueError) as exc:
        return _result_error(str(exc), t0)

    timed_out = False
    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout_bytes = _as_bytes(exc.output)
        stderr_bytes = _as_bytes(exc.stderr)
        _terminate_process_tree(proc)
        try:
            more_stdout, more_stderr = proc.communicate(timeout=2)
            stdout_bytes = _as_bytes(more_stdout) or stdout_bytes
            stderr_bytes = _as_bytes(more_stderr) or stderr_bytes
        except subprocess.TimeoutExpired as reap_exc:
            # A descendant that inherited a pipe can keep communicate() alive
            # even after the leader is gone. Make a second best-effort kill and
            # leave the caller with a bounded timeout result.
            _kill_process(proc)
            stdout_bytes = _as_bytes(reap_exc.output) or stdout_bytes
            stderr_bytes = _as_bytes(reap_exc.stderr) or stderr_bytes
            try:
                proc.communicate(timeout=0.5)
            except (subprocess.TimeoutExpired, OSError):
                pass

    stdout, stdout_error = _decode(stdout_bytes, decode_errors, "stdout")
    stderr, stderr_error = _decode(stderr_bytes, decode_errors, "stderr")
    decode_error = stdout_error or stderr_error
    exit_code = -1 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        spawn_error=None,
        decode_error=decode_error,
    )


def run_shell(
    command: str,
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
) -> ShellResult:
    """Compatibility wrapper for an explicitly selected ``sh`` command."""
    return run_command(command, shell="sh", cwd=cwd, timeout=timeout, env=env)


def _prepare_command(
    command: str | Sequence[str],
    *,
    shell: str | None,
    cwd: Path,
    env: dict[str, str],
    required_tools: Sequence[str],
) -> tuple[list[str] | None, dict[str, str], str | None]:
    if isinstance(command, str):
        if not command.strip():
            return None, env, "command string is empty"
        if shell is None:
            return None, env, "string command requires an explicit shell (shell='sh')"
        if shell != "sh":
            return None, env, f"unknown shell {shell!r}; only shell='sh' is supported"
        shell_path, shell_env, error = _resolve_shell(env, required_tools)
        if error is not None or shell_path is None:
            return None, env, error or "POSIX shell is unavailable"
        return [str(shell_path), "-c", command], shell_env, None

    if shell is not None:
        return None, env, "shell is only valid for a string command"
    try:
        argv = list(command)
    except TypeError:
        return None, env, "direct command must be a non-empty sequence of strings"
    if not argv or any(not isinstance(arg, str) or arg == "" for arg in argv):
        return None, env, "direct command must contain non-empty strings"

    executable = argv[0]
    resolved: str | None
    if _has_path(executable):
        resolved = executable
    else:
        resolved = shutil.which(executable, path=env.get("PATH"))
        if resolved is None:
            return None, env, f"executable not found on PATH: {executable!r}"
    argv[0] = resolved
    return argv, env, None


def _resolve_shell(
    env: dict[str, str], required_tools: Sequence[str]
) -> tuple[str | None, dict[str, str], str | None]:
    if os.name == "nt":
        runtime = _find_git_for_windows(env.get("PATH", ""), env)
        if runtime is None:
            return None, env, (
                "Git for Windows POSIX runtime unavailable: require "
                "usr/bin/sh.exe and usr/bin/grep.exe"
            )
        shell_path, utility_dir = runtime
        shell_env = dict(env)
        existing_path = shell_env.get("PATH", "")
        shell_env["PATH"] = (
            f"{existing_path}{os.pathsep}{utility_dir}"
            if existing_path
            else str(utility_dir)
        )
    else:
        shell_path = Path("/bin/sh")
        if not shell_path.is_file():
            return None, env, "POSIX shell unavailable: /bin/sh was not found"
        shell_env = env

    for tool in required_tools:
        if not isinstance(tool, str) or not tool:
            return None, shell_env, "required shell tool names must be non-empty strings"
        if shutil.which(tool, path=shell_env.get("PATH")) is None:
            return None, shell_env, f"required shell tool not found on PATH: {tool!r}"
    return str(shell_path), shell_env, None


def _find_git_for_windows(
    path_value: str, env: dict[str, str]
) -> tuple[Path, Path] | None:
    """Return Git for Windows' native ``(sh.exe, usr/bin)`` pair.

    Every candidate is verified by the two files required by the contract. We
    do not accept a standalone MSYS/Cygwin ``sh`` or a shell found by PATH.
    """
    roots: list[Path] = []
    git = shutil.which("git", path=path_value)
    if git:
        roots.extend(Path(git).resolve().parents)

    for variable in _WINDOWS_GIT_ENV_VARS:
        value = env.get(variable) or os.environ.get(variable)
        if value:
            roots.append(Path(value))

    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            roots.append(Path(base) / "Git")
            if variable == "LOCALAPPDATA":
                roots.append(Path(base) / "Programs" / "Git")

    seen: set[Path] = set()
    for root in roots:
        # Walking parents of the git executable is intentional: the executable
        # may be under Git/cmd or Git/mingw64/bin.
        for candidate_root in (root, *root.parents):
            candidate_root = candidate_root.resolve()
            if candidate_root in seen:
                continue
            seen.add(candidate_root)
            utility_dir = candidate_root / "usr" / "bin"
            shell_path = utility_dir / "sh.exe"
            grep_path = utility_dir / "grep.exe"
            if shell_path.is_file() and grep_path.is_file():
                return shell_path, utility_dir
    return None


def _has_path(executable: str) -> bool:
    return bool(os.path.dirname(executable) or "/" in executable or "\\" in executable)


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )


def _terminate_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        killpg = getattr(os, "killpg", None)
        sigkill = getattr(signal, "SIGKILL", None)
        try:
            if killpg is not None and sigkill is not None:
                killpg(proc.pid, sigkill)
                return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    elif os.name == "nt":
        taskkill = _taskkill_path()
        if taskkill is not None:
            try:
                subprocess.run(
                    [str(taskkill), "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=2,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
            except (OSError, subprocess.TimeoutExpired):
                pass
    _kill_process(proc)


def _taskkill_path() -> Path | None:
    system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if system_root:
        candidate = Path(system_root) / "System32" / "taskkill.exe"
        if candidate.is_file():
            return candidate
    found = shutil.which("taskkill")
    return Path(found) if found else None


def _kill_process(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.kill()
    except (OSError, ProcessLookupError):
        pass


def _as_bytes(value: bytes | str | None) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8", errors="replace")


def _decode(data: bytes, errors: str, stream: str) -> tuple[str, str | None]:
    try:
        return data.decode("utf-8", errors=errors), None
    except UnicodeDecodeError:
        return "", f"invalid UTF-8 {stream}"
    except LookupError:
        return "", f"unknown output decoding error handler: {errors!r}"


def _result_error(message: str, t0: float) -> CommandResult:
    return CommandResult(
        exit_code=-1,
        stdout="",
        stderr="",
        timed_out=False,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        spawn_error=message,
    )
