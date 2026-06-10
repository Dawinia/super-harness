"""Thin subprocess wrapper for the ``gh`` CLI (engineering-integration spec §3.1).

Pure module — no event emission, no operation-log writing, no global state.
All subprocess calls use argv lists (``shell=False``); typed exceptions are
raised so callers can do ``except gh.GhError`` without importing ``subprocess``.

Minimum supported gh version: 2.40 (``gh pr merge --auto`` stable since then).

Install hints (spec §3.1):
  brew install gh / apt install gh / https://cli.github.com/manual/installation
  gh auth login
  gh auth refresh -s workflow
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, cast

# ---------------------------------------------------------------------------
# Public exceptions — all subclass GhError so callers catch one type
# ---------------------------------------------------------------------------


class GhError(RuntimeError):
    """Common base for all gh wrapper errors."""


class GhNotInstalled(GhError):
    """``gh`` binary not found on PATH."""


class GhNotAuthenticated(GhError):
    """``gh auth status`` failed — user is not logged in."""


class GhVersionTooOld(GhError):
    """Installed ``gh`` is older than MIN_VERSION."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_VERSION: tuple[int, int] = (2, 40)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_version(version_output: str) -> tuple[int, int]:
    """Parse ``gh --version`` first-line output into ``(major, minor)``.

    Expected format: ``gh version X.Y.Z (YYYY-MM-DD)``

    Malformed / unparseable output → raises :class:`GhVersionTooOld` (fail-safe;
    we never crash with an unhandled exception from inside the module).
    """
    try:
        first_line = version_output.splitlines()[0].strip()
        # Expecting "gh version X.Y.Z ..."
        parts = first_line.split()
        # parts[0]="gh", parts[1]="version", parts[2]="X.Y.Z"
        if len(parts) < 3 or parts[1] != "version":
            raise GhVersionTooOld(
                f"Cannot parse gh version output (expected 'gh version X.Y.Z ...'): "
                f"{first_line!r}"
            )
        version_str = parts[2]
        major_str, minor_str, *_ = version_str.split(".")
        return (int(major_str), int(minor_str))
    except GhVersionTooOld:
        raise
    except Exception as exc:
        raise GhVersionTooOld(
            f"Cannot parse gh version output: {version_output!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_gh() -> None:
    """Validate that ``gh`` exists, is recent enough, and is authenticated.

    Raises
    ------
    GhNotInstalled
        ``gh`` binary not found on PATH.
    GhVersionTooOld
        Installed version is older than :data:`MIN_VERSION` (2.40).
    GhNotAuthenticated
        ``gh auth status`` indicates the user is not logged in.
    """
    # --- Step 1: check binary exists and parse version ---
    try:
        out = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise GhNotInstalled(
            "gh CLI not found. Install: brew install gh / apt install gh / "
            "https://cli.github.com/manual/installation"
        ) from exc
    except subprocess.CalledProcessError as exc:
        # gh exists but `gh --version` returned non-zero — a broken install.
        # Keep the "no raw subprocess escape" contract total.
        raise GhNotInstalled(
            "gh CLI is installed but `gh --version` failed; the installation may "
            "be broken. Reinstall: https://cli.github.com/manual/installation"
        ) from exc

    version = _parse_version(out.stdout)
    if version < MIN_VERSION:
        raise GhVersionTooOld(
            f"gh version {version[0]}.{version[1]} is too old; "
            f"need >= {MIN_VERSION[0]}.{MIN_VERSION[1]} "
            f"(gh pr merge --auto requires 2.40+). "
            f"Upgrade: brew upgrade gh"
        )

    # --- Step 2: check authentication ---
    try:
        subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise GhNotAuthenticated(
            "gh not authenticated. Run: gh auth login\n"
            "Required scopes: repo, workflow\n"
            "To refresh scopes: gh auth refresh -s workflow"
        ) from exc


def view_pr(pr_number: int, *, fields: list[str]) -> dict[str, Any]:
    """Fetch PR data as a dict.

    Parameters
    ----------
    pr_number:
        GitHub PR number.
    fields:
        List of field names to request (passed as ``--json <f1>,<f2>,...``).

    Returns
    -------
    dict[str, Any]
        Parsed JSON response from ``gh pr view``.

    Raises
    ------
    GhError
        On subprocess failure, missing binary, or malformed JSON.
    """
    args = ["gh", "pr", "view", str(pr_number), "--json", ",".join(fields)]
    try:
        out = subprocess.run(args, capture_output=True, text=True, check=True)
        return cast(dict[str, Any], json.loads(out.stdout))
    except subprocess.CalledProcessError as exc:
        raise GhError(f"gh pr view {pr_number} failed (exit {exc.returncode})") from exc
    except FileNotFoundError as exc:
        raise GhError("gh CLI not found on PATH") from exc
    except UnicodeDecodeError as exc:
        # `subprocess.run(..., text=True)` decodes stdout at call time; a PR body
        # containing non-UTF-8 bytes will raise UnicodeDecodeError (a ValueError
        # subclass, NOT a subprocess error) before json.loads runs. Caller's
        # `except gh.GhError` would otherwise miss this and surface as a raw
        # traceback. Task 14.3 elevated the impact: PR body is now a slug-
        # resolution source for `verify --pr` / `done --pr`, not just a
        # validation read.
        raise GhError(
            f"gh pr view {pr_number} returned non-UTF-8 output "
            f"(PR body contains undecodable bytes)"
        ) from exc
    except json.JSONDecodeError as exc:
        raise GhError(f"gh pr view {pr_number} returned non-JSON output") from exc


def edit_pr_body(pr_number: int, body: str) -> None:
    """Overwrite the body of a pull request.

    Writes *body* to a temporary ``.md`` file, calls ``gh pr edit --body-file``,
    then unconditionally deletes the temp file (even on failure).

    Raises
    ------
    GhError
        On subprocess failure or missing ``gh`` binary.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        path = f.name

    try:
        subprocess.run(
            ["gh", "pr", "edit", str(pr_number), "--body-file", path],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise GhError(
            f"gh pr edit {pr_number} --body-file failed (exit {exc.returncode})"
        ) from exc
    except FileNotFoundError as exc:
        raise GhError("gh CLI not found on PATH") from exc
    finally:
        os.unlink(path)


def create_pr(
    *,
    base: str,
    head: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
    draft: bool = False,
) -> str:
    """Create a pull request and return its URL.

    Parameters
    ----------
    base:
        Target branch (e.g. ``"main"``).
    head:
        Source branch.
    title:
        PR title.
    body:
        PR description body.
    labels:
        Optional list of label names; each is passed as ``--label <name>``.
    draft:
        If ``True``, passes ``--draft`` to create a draft PR.

    Returns
    -------
    str
        The PR URL (stdout of ``gh pr create``, stripped).

    Raises
    ------
    GhError
        On subprocess failure or missing ``gh`` binary.
    """
    args: list[str] = [
        "gh", "pr", "create",
        "--base", base,
        "--head", head,
        "--title", title,
        "--body", body,
    ]
    if labels:
        for label in labels:
            args += ["--label", label]
    if draft:
        args.append("--draft")

    try:
        out = subprocess.run(args, capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise GhError(f"gh pr create failed (exit {exc.returncode})") from exc
    except FileNotFoundError as exc:
        raise GhError("gh CLI not found on PATH") from exc


def enable_repo_merge_settings() -> None:
    """Enable repo-level auto-merge + squash-merge via ``gh api`` (spec §3.1).

    Runs two PATCH calls against the current repo's settings::

        gh api -X PATCH /repos/{owner}/{repo} -f allow_auto_merge=true
        gh api -X PATCH /repos/{owner}/{repo} -f allow_squash_merge=true

    The ``{owner}`` / ``{repo}`` tokens are **gh's own placeholders** — gh
    resolves them from the current directory's default git remote, so we pass
    them literally (do NOT hand-resolve via ``gh repo view``).

    After both PATCH calls succeed, a final GET on the same endpoint verifies
    the flags actually flipped. GitHub silently no-ops some PATCH calls
    (notably ``allow_auto_merge=true`` on brand-new private repos under the
    default GITHUB_TOKEN scope — the request returns 200 but the field stays
    ``false``). Without the post-PATCH verify, a caller would report
    "merge settings enabled" while the repo still has auto-merge disabled —
    the dishonesty surfaced by smoke walkthrough v2 / OPEN-ITEMS #6 6d S8.

    Pure module contract: this helper raises :class:`GhError` on any failure
    (non-admin token, no remote, non-GitHub remote, post-PATCH verify
    detected a flag still false — all surface the same way) and captures
    subprocess output so a best-effort caller can archive the command +
    stderr to an operation-log. It does NOT write logs or emit events itself.

    Raises
    ------
    GhError
        On subprocess failure, missing ``gh`` binary, or if the post-PATCH
        verify shows either ``allow_auto_merge`` or ``allow_squash_merge`` is
        still ``false``. The error message identifies which flag did not
        flip so the caller's advisory can be specific.
    """
    for flag in ("allow_auto_merge=true", "allow_squash_merge=true"):
        # @decision:d-gh-cli-not-rest
        args = ["gh", "api", "-X", "PATCH", "/repos/{owner}/{repo}", "-f", flag]
        try:
            subprocess.run(args, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise GhError(
                f"gh api -X PATCH /repos/{{owner}}/{{repo}} -f {flag} failed "
                f"(exit {exc.returncode}): {stderr}"
            ) from exc
        except FileNotFoundError as exc:
            raise GhError("gh CLI not found on PATH") from exc

    # Post-PATCH verify: GitHub silently no-ops some PATCHes (see docstring).
    verify_args = ["gh", "api", "/repos/{owner}/{repo}"]
    try:
        out = subprocess.run(verify_args, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise GhError(
            f"gh api /repos/{{owner}}/{{repo}} (post-PATCH verify) failed "
            f"(exit {exc.returncode}): {stderr}"
        ) from exc
    except FileNotFoundError as exc:
        raise GhError("gh CLI not found on PATH") from exc

    try:
        repo: Any = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise GhError(
            f"gh api /repos/{{owner}}/{{repo}} returned non-JSON: {exc}"
        ) from exc
    if not isinstance(repo, dict):
        raise GhError(
            "gh api /repos/{owner}/{repo} returned non-object: "
            f"{type(repo).__name__}"
        )

    not_flipped = [
        name
        for name in ("allow_auto_merge", "allow_squash_merge")
        if not repo.get(name)
    ]
    if not_flipped:
        raise GhError(
            "PATCH 200 but flag(s) still false post-verify: "
            f"{', '.join(not_flipped)}. Token may lack admin scope, or "
            "GitHub silently ignored the request (known on brand-new "
            "private repos under default GITHUB_TOKEN scopes). Enable "
            "manually in repo Settings -> General -> Pull Requests."
        )


def merge_pr_auto_squash(pr_number: int, delete_branch: bool = True) -> None:
    """Auto-squash-merge a pull request.

    Uses ``gh pr merge --auto --squash`` (requires gh 2.40+ and repo-level
    auto-merge enabled).

    Parameters
    ----------
    pr_number:
        GitHub PR number to merge.
    delete_branch:
        If ``True`` (default), passes ``--delete-branch`` to clean up the
        head branch after merge.

    Raises
    ------
    GhError
        On subprocess failure or missing ``gh`` binary.
    """
    args: list[str] = ["gh", "pr", "merge", str(pr_number), "--auto", "--squash"]
    if delete_branch:
        args.append("--delete-branch")

    try:
        subprocess.run(args, check=True)
    except subprocess.CalledProcessError as exc:
        raise GhError(
            f"gh pr merge {pr_number} failed (exit {exc.returncode})"
        ) from exc
    except FileNotFoundError as exc:
        raise GhError("gh CLI not found on PATH") from exc
