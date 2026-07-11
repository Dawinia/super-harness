# src/super_harness/core/scope_match.py
"""Shared scope matcher + fail-closed git helpers for review bundling.

`covered_by_scope` is the segment-aware matcher extracted from
`sensors.verification_runner._covered_by_scope` (Task 2 re-points the baseline at
this copy). Unlike the advisory `scope-vs-plan-final` baseline (which fails OPEN
on git error so it never cries wolf), the helpers here that back the review
freshness gate fail CLOSED: a git error raises `GitScopeError` so the emit-time
check rejects rather than waving a stale review through.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class GitScopeError(RuntimeError):
    """A git operation backing a scope/digest computation failed (fail-closed)."""


def covered_by_scope(changed_file: str, declared_files: list[str]) -> bool:
    """True if `changed_file` is covered by any declared scope entry (segment-aware).

    Exact path equality OR a prefix landing on a path boundary. `src/foo` covers
    `src/foo/x.py` but NOT the sibling `src/foobar.py`.
    """
    for entry in declared_files:
        if changed_file == entry:
            return True
        prefix = entry if entry.endswith("/") else entry + "/"
        if changed_file.startswith(prefix):
            return True
    return False


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise GitScopeError(f"`git {' '.join(args)}` failed: {type(e).__name__}: {e}") from e
    return proc.stdout


def resolve_commit(root: Path, ref: str = "HEAD") -> str:
    """Resolve ``ref`` to one full commit SHA, failing closed."""
    return _git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Return whether ``ancestor`` reaches ``descendant``; fail closed on Git errors."""
    args = ["merge-base", "--is-ancestor", ancestor, descendant]
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise GitScopeError(f"`git {' '.join(args)}` failed: FileNotFoundError: {e}") from e
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise GitScopeError(
        f"`git {' '.join(args)}` failed with exit {proc.returncode}: {proc.stderr.strip()}"
    )


def merge_base_commit(root: Path, base: str, head: str) -> str:
    """Resolve the common ancestor used as the full-change inspection base."""
    return _git(root, "merge-base", base, head).strip()


def tracked_files_at_commit(root: Path, ref: str) -> list[str]:
    """List tracked files at ``ref`` in deterministic order."""
    out = _git(root, "ls-tree", "-r", "--name-only", ref)
    return sorted(line for line in out.splitlines() if line.strip())


def file_text_at_commit(root: Path, ref: str, path: str) -> str:
    """Read one tracked text file from ``ref``, failing closed."""
    return _git(root, "show", f"{ref}:{path}")


def split_changed_by_scope(
    root: Path, *, base: str, declared: list[str]
) -> tuple[list[str], list[str]]:
    """Return (in_scope, out_of_scope) changed files for `base...HEAD`.

    Fail-closed: any git error raises `GitScopeError`.
    """
    out = _git(root, "diff", "--name-only", f"{base}...HEAD")
    changed = [ln for ln in out.splitlines() if ln.strip()]
    in_scope = sorted(f for f in changed if covered_by_scope(f, declared))
    out_scope = sorted(f for f in changed if not covered_by_scope(f, declared))
    return in_scope, out_scope


def split_changed_by_scope_between(
    root: Path, *, base: str, head: str, declared: list[str]
) -> tuple[list[str], list[str]]:
    """Return scoped changed files for the explicit committed ``base..head`` range."""
    out = _git(root, "diff", "--name-only", f"{base}..{head}")
    changed = [line for line in out.splitlines() if line.strip()]
    in_scope = sorted(path for path in changed if covered_by_scope(path, declared))
    out_scope = sorted(path for path in changed if not covered_by_scope(path, declared))
    return in_scope, out_scope


def scope_diff_argv(base: str, head: str, in_scope: list[str]) -> list[str]:
    """Return the exact shell-free argv for inspecting one scoped commit range."""
    return ["git", "diff", f"{base}..{head}", "--", *sorted(in_scope)]


def committed_scope_digest(root: Path, *, base: str, in_scope: list[str]) -> str:
    """sha256 over the committed diff (`base...HEAD`) of the in-scope paths.

    Committed state only (reproducible / tamper-evident); working-tree content is
    deliberately NOT hashed. Empty `in_scope` → digest of empty diff (a constant);
    the caller documents that the freshness check is inert for empty scope.
    Fail-closed: git error raises `GitScopeError`.
    """
    if not in_scope:
        diff = ""
    else:
        diff = _git(root, "diff", f"{base}...HEAD", "--", *sorted(in_scope))
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def working_tree_dirty(root: Path, paths: list[str]) -> bool:
    """True if any of `paths` has uncommitted changes (modified / staged / untracked).

    Empty `paths` → False (nothing to be dirty). Fail-closed on git error.
    """
    if not paths:
        return False
    out = _git(root, "status", "--porcelain", "--", *sorted(paths))
    return bool(out.strip())
