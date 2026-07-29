"""Loader for ``.harness/plan-paths.yaml`` — the owner-controlled patterns naming
where a change's plan documents live (design 2026-07-29).

Fail-CLOSED, deliberately unlike `core/source_scope.py`: this list *grants* a gate
allowance, so a corrupt or malformed file yields `[]` (nothing allowed → the state
table blocks, today's behaviour) rather than falling back to a permissive default
the owner may have narrowed away. A **missing** file is not corruption — it means
"never configured" — and gets the built-in default.

`load_plan_paths` NEVER raises. It catches broad `Exception` (not `BaseException` —
`KeyboardInterrupt`/`SystemExit` still propagate), because `[]` is *always* a safe
answer here: this function sits on the PreToolUse hot path, and an uncaught
exception there is treated as non-blocking, i.e. fails **OPEN** — the exact
opposite of this module's contract. This is the asymmetry with `source_scope.py`:
that loader's fallback is *permissive*, so a broad except there would risk masking
a real bug behind an overly generous default; here the fallback is the strictest
possible answer, so masking a bug behind it is the safe direction. Do not
"harmonise" the two into the same narrow except tuple — they fail in opposite
directions on purpose.

Two guard rails are enforced here, not at the gate, so an invalid pattern can never
reach the decision path:

1. every pattern must contain the literal ``{slug}`` — this is what binds the
   allowance to the active change; without it a pattern could name `AGENTS.md`,
   `README.md`, or `**/*.md`;
2. every pattern must end in ``.md`` (case-insensitive), be a relative *POSIX* path
   with no ``..`` segment, contain no backslash (patterns are repo-relative POSIX
   paths — a backslash is never legitimate, and `PurePosixPath` does not treat
   backslashes as separators so one could otherwise smuggle a Windows UNC path
   through as "relative"), and not start with a Windows drive letter (`C:` reads as
   a bare relative segment to `PurePosixPath`, but is absolute on the host).

The gate re-checks the `.md` suffix on the *resolved* path afterwards — the pattern
check here cannot see through a symlink.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import yaml

from super_harness.core.state import ChangeState

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")

# MUST stay equal to the `plan-paths.yaml` skeleton `init` writes. A repo
# initialized before this constant existed has no config file and falls through
# here, so a narrower default would silently give every existing adopter a subset
# of the documented coverage, with nothing telling them to create the file.
# `test_default_plan_paths_matches_the_shipped_skeleton` pins the equality.
#
# Matches this repo's own convention: `<date>-<slug>-<suffix>.md`, and one change
# routinely has both a `-design.md` and an `-implementation.md`, so the slug sits in
# the middle and an exact `{slug}.md` would match none of them.
DEFAULT_PLAN_PATHS: tuple[str, ...] = (
    "docs/plans/*{slug}*.md",
    "openspec/changes/{slug}/*.md",
    "docs/superpowers/plans/*{slug}*.md",
    "docs/superpowers/specs/*{slug}*.md",
)

SLUG_PLACEHOLDER = "{slug}"


def plan_paths_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "plan-paths.yaml"


def _is_valid_pattern(pattern: object) -> bool:
    if not isinstance(pattern, str) or not pattern:
        return False
    if SLUG_PLACEHOLDER not in pattern:
        return False
    if not pattern.lower().endswith(".md"):
        return False
    if "\\" in pattern:
        return False
    if _DRIVE_LETTER_RE.match(pattern):
        return False
    pp = PurePosixPath(pattern)
    return not pp.is_absolute() and ".." not in pp.parts


def load_plan_paths(workspace_root: Path) -> list[str]:
    """Return the validated plan-path patterns. NEVER raises.

    Missing file → built-in default. Anything else that is not a well-formed list of
    valid patterns → `[]` (fail-closed). `[]` is always a safe answer, so the entire
    body — read, parse, *and* validate — runs under one broad `except Exception`;
    see the module docstring for why this differs from `source_scope.py`.
    """
    f = plan_paths_file(workspace_root)
    try:
        if not f.is_file():
            return list(DEFAULT_PLAN_PATHS)
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return []
        raw = data.get("plan_paths")
        if not isinstance(raw, list):
            return []
        return [p for p in raw if _is_valid_pattern(p)]
    except Exception:
        return []


def patterns_for_state(
    workspace_root: Path, state: ChangeState | None, allow_states: frozenset[str]
) -> list[str]:
    """Plan-path patterns for `state`, or `[]` when the state cannot use them.

    Deferred on purpose: this runs on the PreToolUse hot path (every agent file
    edit, fresh process), and the patterns are consulted in `allow_states` only
    (in production, `gates.decisions.PLAN_PATH_ALLOW_STATES`), so the YAML read
    below is skipped entirely elsewhere. Both gate construction sites
    (`daemon.hook_entry._decide` and `cli.gate.gate_check`) call this rather
    than repeating the condition inline — d-single-gate-policy: one policy, all
    readers, so the hook and `gate check` can never disagree.

    `allow_states` is a parameter, not an import, so this module can stay in
    `core`: `core-is-base` forbids `core` importing `gates`, and the canonical
    `PLAN_PATH_ALLOW_STATES` set is single-sourced in `gates/decisions.py` (it
    must NOT be copied here). Callers import it from `gates.decisions` and pass
    it through.

    The `load_plan_paths` call below is an intra-module reference (both
    functions live in this file), resolved dynamically via this module's own
    namespace at call time — this is what lets
    `tests/unit/daemon/test_hook_entry_decide.py` monkeypatch
    `super_harness.core.plan_paths.load_plan_paths` and observe whether this
    function reached it. Do not hoist that reference behind a re-export or an
    aliased import in another module without re-checking that test; doing so
    would let it keep passing for the wrong reason (patching a name the call
    site no longer looks up) instead of failing.
    """
    if state is None or state.current_state not in allow_states:
        return []
    return load_plan_paths(workspace_root)
