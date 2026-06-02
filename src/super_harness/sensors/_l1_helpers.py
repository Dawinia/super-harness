# L1 anchor (HG-D self-host) — @capability:capability-l1-follow-up-pr
"""Private helpers for the L1Updater sensor.

Four pure-ish utilities:
- generate_l1_stubs: write minimal capability stub markdown files.
- git_branch_commit_push: create branch off main, stage files, commit, push.
- build_l1_pr_body: compose the markdown PR body for an auto L1 update PR.
- pr_num_from_url: parse the integer PR number from a gh pr create URL.

These are internal to the sensors package; do NOT import them from outside.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

__all__ = [
    "build_l1_pr_body",
    "generate_l1_stubs",
    "git_branch_commit_push",
    "pr_num_from_url",
]

def generate_l1_stubs(root: Path, anchors: list[str]) -> list[Path]:
    """Write minimal L1 capability stub markdown files under *root*.

    Output directory: ``root / "docs" / "reference" / "capabilities"``.

    Files that already exist with byte-for-byte identical content are silently
    skipped and NOT included in the return value — this makes the function
    safely idempotent.

    The stub body is composed via f-string interpolation (NOT ``str.format``)
    so an anchor id that incidentally contains ``{`` or ``}`` is passed
    through verbatim instead of raising ``KeyError``/``IndexError`` — those
    exceptions are NOT in the L1Updater AC-7 catch tuple and would otherwise
    escape to ``sensor_crashed`` (Round-4 review hardening; production anchor
    ids are kebab so this is belt-and-braces).

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
        body = (
            f"# {aid}\n\n"
            "<!-- L1 capability stub auto-written by super-harness l1-updater. -->\n"
            "<!-- Real generation is v0.2+; this file marks the placeholder location. -->\n"
        )
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


# Sentinel segments that identify the canonical L1 capability directory.
_L1_DIR_SEGMENTS = ("docs", "reference", "capabilities")

_PR_NUM_RE = re.compile(r"/pull/(\d+)")


def _repo_relative_display(path: Path) -> str:
    """Return a display path for *path* suitable for the PR body bullet list.

    Strategy (avoids calling .relative_to() with an unknown root):
    - If the path's parts contain the canonical sequence
      ``docs/reference/capabilities``, render from that segment onward.
    - Otherwise fall back to the bare filename (path.name).
    """
    parts = path.parts
    for i in range(len(parts) - len(_L1_DIR_SEGMENTS) + 1):
        if parts[i : i + len(_L1_DIR_SEGMENTS)] == _L1_DIR_SEGMENTS:
            return "/".join(parts[i:])
    return path.name


def build_l1_pr_body(change_id: str, files: list[Path]) -> str:
    """Compose the markdown PR body for an auto L1 capability update PR.

    The output is fully deterministic: no timestamps, no PRNG, no environment
    reads.  Two calls with identical arguments produce byte-identical strings.

    Args:
        change_id: The parent change (e.g. merge-commit SHA or PR slug) that
                   triggered this L1 update.
        files: Absolute (or relative) paths of the regenerated L1 stub files,
               in the order they should appear in the bullet list.

    Returns:
        A markdown-formatted string suitable for ``gh pr create --body``.
    """
    header = (
        f"Auto-generated L1 capability update for parent change `{change_id}`.\n"
        "\n"
        "This PR was opened automatically by super-harness l1-updater after the\n"
        "parent change merged."
    )

    if files:
        bullet_lines = "\n".join(
            f"- `{_repo_relative_display(f)}`" for f in files
        )
        file_section = f" Regenerated files:\n\n{bullet_lines}"
    else:
        file_section = (
            "\n\nNo files were regenerated (all L1 stubs already current)."
        )

    footer = (
        "\n\n"
        "Human review is not expected (labels: harness-auto, no-human-review).\n"
        "Auto-merge is enabled per `.harness/policy.yaml` (l1_updater.auto_merge)."
    )

    return header + file_section + footer


def pr_num_from_url(url: str) -> int:
    """Parse the integer PR number from the URL returned by ``gh pr create``.

    ``gh pr create`` prints the new PR URL, e.g.
    ``https://github.com/owner/repo/pull/123``.  This function extracts the
    trailing integer after ``/pull/``.

    Args:
        url: The PR URL string (already stripped of trailing whitespace by the
             ``gh.create_pr`` wrapper).

    Returns:
        The PR number as a Python ``int``.

    Raises:
        ValueError: If no ``/pull/<digits>`` pattern is found in *url*.
    """
    match = _PR_NUM_RE.search(url)
    if match is None:
        raise ValueError(
            f"Could not parse PR number from URL: {url!r} — "
            "expected a '/pull/<integer>' segment."
        )
    return int(match.group(1))
