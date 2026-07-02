"""Executable-check runner (design §4.2) - the impure half of Tool B.

`run_check` in decision_check.py stays pure; ALL subprocess / sandbox / git-diff
machinery lives here so the structural-integrity layer never imports subprocess.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from super_harness.core.anchor_scanner import _list_files, _matches_any
from super_harness.core.decisions import Counterexample, Decision
from super_harness.core.source_scope import load_source_scope

DEFAULT_TIMEOUT = 30  # seconds (per-check override deferred, design §4.2)


@dataclass
class CheckRun:
    satisfied: bool       # True iff the command exited 0
    exit_code: int        # -1 sentinel for timeout / spawn failure
    detail: str           # short human reason (stderr tail / "timeout" / "...")


def run_one_check(command: str, *, cwd: Path, timeout: float = DEFAULT_TIMEOUT) -> CheckRun:
    """Run a single executable check and report whether it is satisfied.

    `command` MUST be a ratified, body-hash-locked check (Tool A text-lock).
    `shell=True` is intentional: checks are deliberately shell snippets like
    `! grep ... | ...`. The trust boundary is the ratify-time bite-test + hash
    lock, NOT this primitive, which runs any string it is handed.

    On timeout the whole process GROUP is killed, not just the shell: with
    `start_new_session=True` the child is its own group leader (pgid == pid), so
    `os.killpg(proc.pid, SIGKILL)` reaps backgrounded grandchildren (e.g. the grep
    under the shell) that would otherwise be orphaned and could hold the stdout
    pipe open. We target `proc.pid` (not `os.getpgid`, which raises once the shell
    leader has exited) and bound the post-kill reap so a stuck grandchild can never
    hang the check.
    """
    try:
        proc = subprocess.Popen(
            command, shell=True, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", start_new_session=True,
        )
    except OSError as e:  # shell missing, bad cwd, etc.
        return CheckRun(False, -1, f"could not run: {e}")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()  # best effort: at least the direct child
        try:
            proc.communicate(timeout=2)  # bounded reap; a SIGKILL'd group EOFs the pipes fast
        except subprocess.TimeoutExpired:
            pass  # give up reaping; the process is killed, never hang
        return CheckRun(False, -1, f"timeout after {timeout}s")
    detail = (err or out or "").strip().splitlines()
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

    Symlink note: symlinks and non-regular files are skipped (only regular
    files are copied).
    """
    include, exclude = load_source_scope(workspace_root)
    tmp = Path(tempfile.mkdtemp(prefix="sh-bite-"))
    try:
        for f in _list_files(workspace_root):
            rel = f.relative_to(workspace_root)
            if f.is_symlink() or not f.is_file():
                continue
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
class CheckFailure:
    id: str
    exit_code: int
    detail: str


def has_runnable_check(d: Decision) -> bool:
    """A ratified decision whose executable check can actually run (tier-1).

    Shared by the CI runner (`run_executable_checks`) and the authoring-time
    fan-out (`core.authoring_check`) so "a decision whose check can run" has one
    definition and cannot drift between the two paths.
    """
    return d.status == "ratified" and d.check is not None


def select_changed(
    decisions: list[Decision],
    anchor_map: dict[str, list[tuple[str, int]]],
    changed: set[str],
) -> list[Decision]:
    """Decisions whose anchored files intersect `changed` (the --changed subset).

    Heuristic, deliberately UNSOUND: a check's real scan scope can be wider than
    its anchors, so this can MISS a violation in a non-anchored file. The full run
    (no --changed) is the soundness backstop. See design §4.2.
    """
    out: list[Decision] = []
    for d in decisions:
        files = {f for f, _ln in anchor_map.get(d.id, [])}
        if files & changed:
            out.append(d)
    return out


def run_executable_checks(
    workspace_root: Path,
    decisions: list[Decision],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[CheckFailure]:
    """Run each ratified tier-1 decision's check on the real tree (read-only).

    Skips non-ratified and tier-3 (no check). Non-satisfied (incl. timeout/broken
    -> -1 sentinel) becomes a CheckFailure. Returns failures sorted by id (stable).
    """
    failures: list[CheckFailure] = []
    for d in decisions:
        if not has_runnable_check(d) or d.check is None:  # 2nd clause narrows d.check for mypy
            continue
        run = run_one_check(d.check, cwd=workspace_root, timeout=timeout)
        if not run.satisfied:
            failures.append(CheckFailure(id=d.id, exit_code=run.exit_code, detail=run.detail))
    failures.sort(key=lambda f: f.id)
    return failures


def changed_files(workspace_root: Path) -> set[str] | None:
    """Working-tree changes vs HEAD plus untracked-not-ignored (design §4.2).

    Returns None if not a git repo / git unavailable -> caller falls back to FULL
    (never silently under-run; full is the safe direction)."""
    try:
        diff = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=str(workspace_root),
                              capture_output=True, text=True, check=True)
        others = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                                cwd=str(workspace_root), capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return {ln for ln in (diff.stdout + others.stdout).splitlines() if ln.strip()}


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
