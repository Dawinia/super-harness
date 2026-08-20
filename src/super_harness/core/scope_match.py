# src/super_harness/core/scope_match.py
"""Shared scope matcher + fail-closed git helpers for review bundling.

`covered_by_scope` is a segment-aware **prefix** matcher, so a declared `tests/`
entry covers `tests/unit/x.py`. Callers:

* `core.review_bundle` (line ~84) picks which `.md` files enter a review bundle.
  There the loose semantics really are a convenience — a wider `.md` selection only
  gives the reviewer more to read.
* `split_changed_by_scope` / `split_changed_by_scope_between` below, which back
  `review_bundle.assemble_bundle` and `engineering.review_contract`. Their
  `in_scope` half feeds the frozen inspection ranges and the review digest; their
  `out_of_scope` half is printed by `cli.review` (`review prepare`) as the
  reviewer's scope-drift warning.

That last one is NOT merely a convenience: because the matcher is loose, a declared
`tests/` entry keeps `tests/unit/x.py` out of the `out_of_scope` list, so `review
prepare` still UNDER-REPORTS drift the merge gate would block on — the same
under-report this module's strict-side alignment removed from the `verify` baseline.
That is a known residual, deliberately not fixed here (changing it changes the
frozen inspection ranges and every stored bundle digest); it is not something this
docstring may assert away.

**It is deliberately NOT the scope matcher used for verdicts.** The merge gate
(`engineering.attestation.verify_attestations`) matches changed files against
`scope.files` by canonical-path SET MEMBERSHIP, where `tests/` covers only a file
literally named `tests/`, and the advisory `scope-vs-plan-final` baseline in
`sensors.verification_runner` uses that same set membership so a clean local
`verify` cannot promise something the merge boundary then refuses. Two semantics
with different jobs: do not "unify" them.

Unlike that baseline (which fails OPEN on git error so it never cries wolf), the
helpers here that back the review freshness gate fail CLOSED: a git error raises
`GitScopeError` so the emit-time check rejects rather than waving a stale review
through.
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
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
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
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
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
    if not in_scope:
        return []
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
