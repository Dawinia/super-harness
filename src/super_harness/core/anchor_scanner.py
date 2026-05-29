"""Pure `@capability:<id>` sentinel scanner (Task 1.10 / B-5 fix).

Phase 8 baseline checks (`anchor-sentinel-presence`) and Phase 11 ambient sensor
(`freshness-anchor-check`) both need to walk the repo and collect every
`@capability:<id>` sentinel comment present in source. To avoid forward
dependency from Phase 8 onto a not-yet-built Phase 11 sensor — and to avoid
duplicating the regex + git-aware walk in two places — the *pure* scanner lives
here in core/ now. The Phase 11 Sensor wrapper (event emission, debouncing,
state.yaml integration) is added in its own phase.

Contract (pure function, no side effects):
- Input:  workspace root `Path`, optional list of glob patterns to filter files.
- Output: a `set[str]` of capability IDs found across all matched files.
- Does NOT emit events, does NOT touch state.yaml, does NOT write any file.
- Reads files only; safe to call concurrently with other readers.

File discovery:
- If `root` is a git repo: `git ls-files` (respects `.gitignore` + untracked
  rules). This matches spec §6.5 expectation that ignored / vendored / build
  artifacts must not contribute false-positive anchors.
- Otherwise: filesystem walk skipping dotfiles / dot-directories (`.git/`,
  `.venv/`, etc.) — pragmatic v0.1 heuristic. Not a substitute for `.gitignore`
  but adequate for ephemeral test trees that aren't git-initialized.

Glob filtering:
- `file_globs=None` or any entry equal to `"**/*"` / `"**"` means "match every
  file" — short-circuited because Python's `fnmatch` / `PurePath.match` do not
  reliably support recursive `**` in 3.10/3.11. Specific patterns (e.g.
  `"*.py"`, `"src/foo/*.ts"`) fall through to `fnmatch.fnmatch` against the
  path relative to root. Phase 8 / Phase 11 callers pass either `None` (scan
  everything `git ls-files` returned) or a per-extension list.
- Honoring this parameter is the v0.1 contract; a richer `pathspec`-based
  implementation is a v0.2 candidate if real callers need recursive `**`.

Binary file safety:
- Files that fail UTF-8 decode (or that we lack read permission on) are
  silently skipped — the scanner must never crash on weird repo content.
"""
from __future__ import annotations

import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

_SENTINEL_RE = re.compile(r"@capability:([A-Za-z0-9_-]+)")

# Glob patterns that we treat as "match every file" (avoids `**` quirks in
# fnmatch / PurePath.match on Python 3.10-3.13).
_MATCH_ALL_GLOBS = frozenset({"**/*", "**"})


def _list_files(root: Path) -> list[Path]:
    """Enumerate candidate files under `root`.

    Prefers `git ls-files` (respects `.gitignore`). Falls back to a filesystem
    walk that excludes dot-prefixed segments when `root` is not a git repo or
    the git binary is unavailable.
    """
    try:
        out: subprocess.CompletedProcess[str] = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [root / line for line in out.stdout.splitlines() if line.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a git repo (or git not installed): walk visible files only.
        # Skip any path containing a dot-prefixed segment so `.git/`, `.venv/`,
        # `.hidden` files, etc. don't pollute results — mirrors the spirit of
        # `git ls-files` for ad-hoc trees used in tests.
        root_parts_len = len(root.parts)
        results: list[Path] = []
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel_parts = p.parts[root_parts_len:]
            if any(part.startswith(".") for part in rel_parts):
                continue
            results.append(p)
        return results


def _matches_any(rel_path: Path, globs: list[str]) -> bool:
    """Return True if `rel_path` matches at least one glob in `globs`.

    Short-circuits on the "match everything" sentinels to side-step `**`
    handling gaps in fnmatch / PurePath.match.
    """
    if any(g in _MATCH_ALL_GLOBS for g in globs):
        return True
    rel_str = str(rel_path)
    return any(fnmatch(rel_str, g) for g in globs)


def scan_sentinel_locations(
    root: Path, file_globs: list[str] | None = None
) -> dict[str, list[tuple[str, int]]]:
    """Like scan_sentinels but records WHERE each `@capability:<id>` occurs.

    Returns ``{anchor_id: [(repo_relative_file, 1_based_line), ...]}``. Reuses
    ``_SENTINEL_RE`` / ``_list_files`` / ``_matches_any`` / binary-skip so the
    two scanners cannot drift. Files are walked in sorted order (``scan_sentinels``
    does not) so the index is deterministic.
    """
    locations: dict[str, list[tuple[str, int]]] = {}
    files = _list_files(root)
    if file_globs is not None:
        files = [f for f in files if _matches_any(f.relative_to(root), file_globs)]
    for f in sorted(files):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        rel = str(f.relative_to(root))
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _SENTINEL_RE.finditer(line):
                locations.setdefault(m.group(1), []).append((rel, lineno))
    return locations


def scan_sentinels(root: Path, file_globs: list[str] | None = None) -> set[str]:
    """Return every `@capability:<id>` sentinel ID found beneath `root`.

    Args:
        root: directory to scan (typically the workspace root containing
            `.harness/`). MUST exist.
        file_globs: optional list of glob patterns (relative to `root`) used to
            restrict which files are read. `None` means "no filter" — every
            file returned by `_list_files` is scanned. An empty list `[]` means
            "filter to nothing" (returns empty set) — pass `None` if you want
            "no filter." The sentinels `"**/*"` and `"**"` are treated as
            "match all" because fnmatch does not implement recursive `**`.

    Returns:
        A set of capability IDs (the `<id>` portion of `@capability:<id>`).
        Empty set if nothing is found. Never raises on binary / unreadable
        files — those are silently skipped.
    """
    found: set[str] = set()
    files = _list_files(root)
    if file_globs is not None:
        files = [f for f in files if _matches_any(f.relative_to(root), file_globs)]
    for f in files:
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        for m in _SENTINEL_RE.finditer(text):
            found.add(m.group(1))
    return found
