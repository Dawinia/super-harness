"""Private helpers for the L1Updater sensor.

Two pure-ish utilities:
- generate_l1_stubs: write minimal capability stub markdown files.
- git_branch_commit_push: create branch off main, stage files, commit, push.

These are internal to the sensors package; do NOT import them from outside.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["generate_l1_stubs", "git_branch_commit_push"]

_STUB_TEMPLATE = (
    "# {aid}\n\n"
    "<!-- L1 capability stub auto-written by super-harness l1-updater. -->\n"
    "<!-- Real generation is v0.2+; this file marks the placeholder location. -->\n"
)


def generate_l1_stubs(root: Path, anchors: list[str]) -> list[Path]:
    """Write minimal L1 capability stub markdown files under *root*.

    Output directory: ``root / "docs" / "reference" / "capabilities"``.

    Files that already exist with byte-for-byte identical content are silently
    skipped and NOT included in the return value — this makes the function
    safely idempotent.

    Args:
        root: Workspace root (arbitrary filesystem path).
        anchors: Ordered list of anchor IDs; one ``.md`` file per entry.

    Returns:
        Absolute paths of files actually written, preserving input order.
    """
    out_dir = root / "docs" / "reference" / "capabilities"
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for aid in anchors:
        target = out_dir / f"{aid}.md"
        body = _STUB_TEMPLATE.format(aid=aid)
        if target.exists() and target.read_text() == body:
            # Byte-for-byte identical — skip to preserve mtime.
            continue
        target.write_text(body)
        written.append(target.resolve())

    return written


def git_branch_commit_push(
    root: Path,
    branch: str,
    files: list[Path],
    message: str,
    *,
    skip_push: bool = False,
) -> None:
    """Create a new branch off ``main``, stage *files*, commit, and push.

    Steps (each runs with ``check=True``; ``CalledProcessError`` propagates
    to the caller — the L1Updater owns the AC-7 transactional boundary):

    1. ``git checkout -b <branch> main``
    2. ``git add <relative-path>`` for every file in *files*.
    3. ``git commit -m <message>``
    4. ``git push origin <branch>`` (skipped when ``skip_push=True``).

    All commands use ``cwd=root`` and ``shell=False``.

    Args:
        root: Workspace root; all relative paths are resolved against it.
        branch: Name of the new branch to create.
        files: Absolute paths of files to stage; converted to paths relative
               to *root* when passed to ``git add``.
        message: Commit message string.
        skip_push: When ``True``, omit the ``git push`` step (useful in tests
                   and dry-run scenarios).
    """
    subprocess.run(
        ["git", "checkout", "-b", branch, "main"],
        cwd=root,
        check=True,
    )
    for f in files:
        subprocess.run(
            ["git", "add", str(f.relative_to(root))],
            cwd=root,
            check=True,
        )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=root,
        check=True,
    )
    if not skip_push:
        subprocess.run(
            ["git", "push", "origin", branch],
            cwd=root,
            check=True,
        )
