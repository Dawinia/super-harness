"""Executable-check runner (design §4.2) - the impure half of Tool B.

`run_check` in decision_check.py stays pure; ALL subprocess / sandbox / git-diff
machinery lives here so the structural-integrity layer never imports subprocess.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 30  # seconds (per-check override deferred, design §4.2)


@dataclass
class CheckRun:
    satisfied: bool       # True iff the command exited 0
    exit_code: int        # -1 sentinel for timeout / spawn failure
    detail: str           # short human reason (stderr tail / "timeout" / "...")


def run_one_check(command: str, *, cwd: Path, timeout: int = DEFAULT_TIMEOUT) -> CheckRun:
    """Run a single executable check and report whether it is satisfied.

    `command` MUST be a ratified, body-hash-locked check (Tool A text-lock).
    `shell=True` is intentional: checks are deliberately shell snippets like
    `! grep ... | ...`. The trust boundary is the ratify-time bite-test + hash
    lock, NOT this primitive, which runs any string it is handed.
    """
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd),
            capture_output=True, text=True, errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CheckRun(False, -1, f"timeout after {timeout}s")
    except OSError as e:  # shell missing, bad cwd, etc.
        return CheckRun(False, -1, f"could not run: {e}")
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail = detail[-1] if detail else f"exited {proc.returncode}"
    return CheckRun(proc.returncode == 0, proc.returncode, tail)
