"""Best-effort plain-text operation-log writer (rule-of-three factor).

Used by callers that need to persist an audit trail when a primary I/O path
(``gh`` API call, follow-up PR creation, etc.) fails AND the failure must NOT
escalate to a crash. The shared mechanism is intentionally tiny:

1. Build ``<harness>/operation-logs/<subdir>/`` (mkdir -p).
2. Sanitize the UTC timestamp's ``:`` to ``-`` for cross-filesystem portability
   (Windows / SMB-mounted dirs reject ``:`` in filenames).
3. Write the caller-composed *body* string verbatim.
4. **Swallow ``OSError``** — operation-logging is itself best-effort; a log-write
   failure must NOT turn the original non-fatal degradation into a hard error.

Callers compose the body (call sites have very different schemas: e.g.
setup-github audits gh-api commands).

History: introduced in Phase 13 Task 13.5 when a second operation-log writer
was added after Phase 12's ``init --setup-github`` (cli/init.py).
"""

from __future__ import annotations

from pathlib import Path

from super_harness.core.clock import utc_now_iso

__all__ = ["write_operation_log"]


def write_operation_log(harness: Path, subdir: str, body: str) -> None:
    """Write *body* to ``<harness>/operation-logs/<subdir>/<utc-ts>.log``.

    Best-effort: any ``OSError`` along the way (full disk, unwritable parent,
    sub-path occupied by a regular file, etc.) is swallowed so the caller's
    non-fatal path stays non-fatal.

    Args:
        harness: The ``.harness/`` directory of the workspace.
        subdir: Domain-specific subdirectory under ``operation-logs/`` (e.g.
            ``"setup-github"``).
        body: Pre-composed log body (caller owns the format).
    """
    try:
        log_dir = harness / "operation-logs" / subdir
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = utc_now_iso().replace(":", "-")
        (log_dir / f"{ts}.log").write_text(body, encoding="utf-8")
    except OSError:
        pass
