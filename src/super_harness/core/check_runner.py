"""Executable-check runner (design §4.2) - the impure half of Tool B.

`run_check` in decision_check.py stays pure; ALL subprocess / sandbox / git-diff
machinery lives here so the structural-integrity layer never imports subprocess.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from super_harness.core.anchor_scanner import _list_files, _matches_any
from super_harness.core.decisions import Counterexample
from super_harness.core.source_scope import load_source_scope

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


@contextmanager
def build_sandbox(workspace_root: Path, counterexample: Counterexample) -> Iterator[Path]:
    """Copy the in-scope working tree to a tempdir + inject the counterexample.

    The bite side of the bite-test must let the check see a bad snippet WITHOUT
    mutating the real working tree, so a crash mid-run never leaves a poison file
    in src/. We copy in-scope files into a tempdir, write the counterexample
    there, yield the tempdir (use as cwd), then discard it on exit.

    Known limitation: `_list_files` lists *tracked* files (`git ls-files`), so a
    brand-new untracked source file the agent just created is NOT copied into the
    sandbox. The bite side still works (the counterexample is injected
    explicitly) and the pass side (Task 4) runs on the real tree anyway; CI runs
    against the committed PR tree, where the file is tracked.

    Symlink note: `shutil.copy2` follows symlinks (copies the target's content,
    not the link itself); acceptable for v0.1.
    """
    include, exclude = load_source_scope(workspace_root)
    tmp = Path(tempfile.mkdtemp(prefix="sh-bite-"))
    try:
        for f in _list_files(workspace_root):
            rel = f.relative_to(workspace_root)
            if not _matches_any(rel, include) or _matches_any(rel, exclude):
                continue
            dest = tmp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)          # fcopyfile/COW on APFS, plain copy elsewhere
        ce_path = tmp / counterexample.path
        if not ce_path.resolve().is_relative_to(tmp.resolve()):
            raise ValueError(f"counterexample path escapes sandbox: {counterexample.path!r}")
        ce_path.parent.mkdir(parents=True, exist_ok=True)
        ce_path.write_text(counterexample.content + "\n", encoding="utf-8")
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class BiteVerdict:
    ok: bool
    reason: str
    pass_side: CheckRun
    bite_side: CheckRun


def bite_test(
    workspace_root: Path, command: str, counterexample: Counterexample,
    *, timeout: int = DEFAULT_TIMEOUT,
) -> BiteVerdict:
    """Two-sided anti-hollow proof run at ratify (design §4.2 - the crux).

    A decision's check must (a) PASS on the current real code AND (b) FAIL when
    the counterexample is present. The two sides are deliberately asymmetric.
    """
    # Pass side: run the RAW command on the UNFILTERED real tree, read-only
    # (cwd=repo_root, no source_scope). This is deliberate - it is the ONLY
    # reason pollution self-detection works: an over-wide check (e.g. `grep . `)
    # scans the inline counterexample sitting in docs/decisions/<id>.md and
    # fails here. Do NOT add source_scope filtering to the pass side. (Same run
    # a normal `decision check` does - the runner is shared.)
    p = run_one_check(command, cwd=workspace_root, timeout=timeout)
    if not p.satisfied:
        return BiteVerdict(False, f"check fails on current code ({p.detail}) - fix the "
                                  f"code, or scope the check away from the counterexample",
                           p, CheckRun(False, -1, "not run (pass side failed)"))
    # Bite side: sandbox with the counterexample injected.
    with build_sandbox(workspace_root, counterexample) as sb:
        b = run_one_check(command, cwd=sb, timeout=timeout)
    if b.satisfied:
        return BiteVerdict(False, "check did not bite: it still passed with the "
                                  "counterexample present - either the check is too weak "
                                  "or the counterexample snippet doesn't contain what the "
                                  "check looks for", p, b)
    return BiteVerdict(True, "bites", p, b)
