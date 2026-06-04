# L1 anchor (HG-12 cut 1) — @capability:capability-actor-identity
"""Resolve the identity recorded on emitted events' ``actor.identifier``.

Today every CLI emit uses the placeholder ``"cli"``; this resolves a real
identity so review independence can be disclosed at the merge boundary
(see docs/plans/2026-06-04-review-identity-substrate-design.md §3.1). This is
substrate for HG-12 — disclosure, not enforcement: the identity is self-asserted
(default ``git config user.email``) and a solo owner can set it freely.

The ``git config`` call is an isolated, mockable seam — every failure mode (no
repo, unset email, no git binary) falls through to ``"cli"`` and never raises.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ENV_VAR = "SUPER_HARNESS_ACTOR"
FALLBACK_IDENTITY = "cli"


def _git_config_email(workspace: Path) -> str | None:
    """Return ``git config user.email`` for ``workspace`` stripped, or None.

    Swallows every failure mode — non-zero exit (not-a-repo / unset email),
    ``FileNotFoundError`` (no git binary), whitespace-only output — returning
    None so the caller falls through. Never raises.
    """
    try:
        proc = subprocess.run(
            ["git", "config", "user.email"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    email = proc.stdout.strip()
    return email or None


def resolve_identity(workspace: Path, override: str | None = None) -> str:
    """Resolve identity: override > env SUPER_HARNESS_ACTOR > git email > "cli".

    First non-empty value (after ``.strip()``) wins. Always returns a non-empty
    string.
    """
    if override and override.strip():
        return override.strip()
    env = os.environ.get(_ENV_VAR)
    if env and env.strip():
        return env.strip()
    git = _git_config_email(workspace)
    if git:
        return git
    return FALLBACK_IDENTITY
