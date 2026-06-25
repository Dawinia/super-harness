""".gitignore marker-bounded block injector (S2 fix).

`super-harness init` writes a marker-bounded block into the repo-root
`.gitignore` listing the canonical auto-generated / per-machine paths that
are NOT version-control candidates (runtime state, sensor results,
per-machine local settings, etc.).

This module follows the same marker-discipline contract as
`engineering.agents_md` (Phase 7/9/12 lesson): exactly one block expected;
multiple blocks → fail loud (never splice — data-loss guard). The injector
NEVER touches content outside its markers.

Marker grammar
--------------

- Begin: ``# >>> super-harness gitignore (do not edit between markers)``
- End:   ``# <<< super-harness gitignore``

The ``>>>`` / ``<<<`` style mirrors common gitignore conventions for
tool-managed blocks (e.g. conda, pyenv).

Behavior
--------

1. ``.gitignore`` absent → write the marker block ONLY.
2. ``.gitignore`` present + 0 super-harness marker blocks → append the block,
   preserving the user's existing content verbatim.
3. ``.gitignore`` present + exactly 1 marker block → REPLACE the body between
   the markers (idempotent re-init; a re-run on the canonical state is a
   byte-identical no-op).
4. ``.gitignore`` present + ≥2 marker blocks OR unbalanced begin/end markers
   (one begin without a matching end, or vice versa) → raise
   `GitignoreInjectionError`. Manual cleanup required. (Splicing across an
   orphan marker would silently delete trapped user content — data-loss guard.)

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = [
    "GITIGNORE_BEGIN_MARKER",
    "GITIGNORE_END_MARKER",
    "GitignoreInjectionError",
    "inject_gitignore_block",
]

GITIGNORE_BEGIN_MARKER = "# >>> super-harness gitignore (do not edit between markers)"
GITIGNORE_END_MARKER = "# <<< super-harness gitignore"

# Canonical paths that should NOT be version-controlled. Two groups:
#  1. `.harness/` runtime / derived files. (policy.yaml, sensors.yaml,
#     gates.yaml, source-paths.yaml, verification.yaml, conventions.md,
#     adapters.yaml ARE user-config and stay version-controlled —
#     they are deliberately absent.)
#  2. Per-agent local settings + their backups. `adapter install claude-code`
#     installs the gate hook into `.claude/settings.local.json` (which carries
#     a machine-specific absolute path, so it must never be committed) and
#     makes a timestamped backup of `.claude/settings.local.json` before
#     merging the PreToolUse / SessionStart hooks (defense for safe rollback
#     per Phase 5 `_settings_merge` design). `adapter install codex` does the
#     same into `.codex/hooks.json` (+ its timestamped backups); we ignore only
#     `hooks.json` (+ backups), NEVER `.codex/` wholesale — `.codex/config.toml`
#     may hold the user's own committed Codex settings. Without these gitignore
#     lines, the per-machine settings file and every run's backup file get
#     accidentally committed via `git add -A`. Smoke walkthrough v3 caught
#     the backup regression (S13). Add new patterns here when other agent
#     adapters ship a similar local-settings / backup scheme.
_CANONICAL_PATHS: tuple[str, ...] = (
    ".harness/state.yaml",
    ".harness/events.jsonl",
    ".harness/sensor-results/",
    ".harness/verification-results/",
    ".harness/operation-logs/",
    ".harness/pending-reviews/",
    ".harness/gate-disabled",
    ".claude/settings.local.json",
    ".claude/*.super-harness-backup.*",
    ".codex/hooks.json",
    ".codex/*.super-harness-backup.*",
)


class GitignoreInjectionError(Exception):
    """Raised when .gitignore is in a state we refuse to guess about.

    Currently triggered when more than one super-harness marker block is
    present (user mis-edit or duplicate markers) — manual cleanup is
    required, mirroring `AgentsMdInjectionError`.
    """


def _render_block() -> str:
    """Render the canonical marker block (begin + body + end + trailing LF)."""
    body = "\n".join(_CANONICAL_PATHS)
    return f"{GITIGNORE_BEGIN_MARKER}\n{body}\n{GITIGNORE_END_MARKER}\n"


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp sibling + ``os.replace``).

    Same pattern as `engineering.agents_md._atomic_write` — same-directory
    temp file so ``os.replace`` is an atomic same-filesystem rename. The
    temp file is cleaned up on error.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def inject_gitignore_block(path: Path) -> None:
    """Inject / replace the super-harness marker block in ``path``.

    See module docstring for full behavior. Raises `GitignoreInjectionError`
    if ``path`` already has ≥2 super-harness marker blocks (manual cleanup).
    May raise `OSError` if read/write fails (caller's responsibility to
    surface as a friendly error).

    The read catches ``(OSError, UnicodeDecodeError)`` — ``UnicodeDecodeError``
    is a ``ValueError`` (not ``OSError``), so both must be caught. A non-UTF-8
    .gitignore raises `GitignoreInjectionError` so callers' existing
    ``except (OSError, GitignoreInjectionError)`` envelope reports it cleanly.
    """
    block = _render_block()

    if not path.exists():
        _atomic_write(path, block)
        return

    try:
        existing = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise GitignoreInjectionError(
            f"{path} is not valid UTF-8 ({e}); super-harness only edits UTF-8 "
            f"`.gitignore` — convert it to UTF-8 and retry."
        ) from e

    begin_count = existing.count(GITIGNORE_BEGIN_MARKER)
    end_count = existing.count(GITIGNORE_END_MARKER)

    if begin_count >= 2 or end_count >= 2:
        raise GitignoreInjectionError(
            f"{path} has {max(begin_count, end_count)} super-harness gitignore "
            f"marker blocks; refusing to splice (manual cleanup required). "
            f"Expected exactly one block between "
            f"'{GITIGNORE_BEGIN_MARKER}' and '{GITIGNORE_END_MARKER}'."
        )

    if begin_count != end_count:
        # Unbalanced markers: a lone orphan would otherwise let a subsequent
        # run silently delete the user content trapped between an orphan begin
        # and the new block's end. Fail loud (data-loss guard).
        raise GitignoreInjectionError(
            f"{path} has unbalanced super-harness gitignore markers "
            f"({begin_count} begin, {end_count} end); manual cleanup required."
        )

    if begin_count == 1:
        # Replace only the body between the markers. Preserve everything
        # outside the markers verbatim.
        begin_idx = existing.index(GITIGNORE_BEGIN_MARKER)
        end_idx = existing.index(GITIGNORE_END_MARKER) + len(GITIGNORE_END_MARKER)
        # block already has a trailing newline. The slice we replace runs from
        # the begin marker through the end marker (no trailing newline) — so
        # we strip the trailing newline from `block` to keep newline parity
        # with what we removed.
        new = existing[:begin_idx] + block.rstrip("\n") + existing[end_idx:]
        if new == existing:
            return  # byte-identical no-op
        _atomic_write(path, new)
        return

    # Zero blocks: append the marker block, preserving user content.
    # Ensure exactly one blank-line separator between user content and our block.
    if existing and not existing.endswith("\n"):
        existing = existing + "\n"
    separator = "\n" if existing else ""
    new = existing + separator + block
    _atomic_write(path, new)
