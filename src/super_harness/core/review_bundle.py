# src/super_harness/core/review_bundle.py
"""Assemble a deterministic review bundle for `review prepare`.

The bundle is the harness-assembled context a reviewer subagent reviews against:
the in-scope committed diff, out-of-scope drift, spec/plan paths, the resolved
checklist, and a committed-HEAD digest tying a later verdict to this diff state.
No LLM, no inference — pure derivation. Requires a clean in-scope working tree
(the digest is over committed HEAD; see design §4.C "commit obligation").
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from super_harness.core.frontmatter import split_frontmatter
from super_harness.core.paths import events_path
from super_harness.core.reducer import derive_state
from super_harness.core.review_checklist import resolve_checklist
from super_harness.core.scope_match import (
    GitScopeError,
    committed_scope_digest,
    covered_by_scope,
    file_text_at_commit,
    split_changed_by_scope,
    tracked_files_at_commit,
    working_tree_dirty,
)

_DEFAULT_BASE = "main"


class BundleError(ValueError):
    """The review bundle cannot be assembled (dirty tree, git failure, etc.)."""


def load_base_branch(root: Path) -> str:
    """Read tracked review governance's base branch, else ``main``.

    Tolerant: absent/corrupt yaml → default. This is the single config location
    for the base branch so the implementer never re-hardcodes `main`.
    """
    f = root / ".harness" / "review-governance.yaml"
    if not f.is_file():
        return _DEFAULT_BASE
    try:
        parsed: Any = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return _DEFAULT_BASE
    if not isinstance(parsed, dict):
        return _DEFAULT_BASE
    review = parsed.get("review")
    if isinstance(review, dict):
        base = review.get("base_branch")
        if isinstance(base, str):
            return base
    return _DEFAULT_BASE


# A spec/plan path resolver: (framework, root, change_id) -> (spec_path, plan_path).
# Injected by the caller so core stays free of any `adapters` import (decision
# d-core-is-base: core is the base layer). cli/review.py wires the adapters-backed
# resolver (adapters.registry.resolve_spec_plan_paths).
SpecPlanResolver = Callable[[str | None, Path, str], tuple[str, str]]


def _no_spec_plan(framework: str | None, root: Path, change_id: str) -> tuple[str, str]:
    """Default resolver: no framework spec/plan paths.

    Used when the caller wires no resolver (e.g. core-only tests). Keeps the
    bundle shape stable (``spec_path``/``plan_path`` present but empty).
    """
    return "", ""


def resolve_declared_artifact_paths(
    root: Path, declared: list[str], change_id: str, *, ref: str = "HEAD"
) -> tuple[str, str]:
    spec_path = ""
    plan_path = ""
    for relative in tracked_files_at_commit(root, ref):
        if not relative.endswith(".md") or not covered_by_scope(relative, declared):
            continue
        parsed = split_frontmatter(file_text_at_commit(root, ref, relative))
        if parsed is None:
            continue
        frontmatter, _ = parsed
        if frontmatter.get("change") != change_id:
            continue
        if frontmatter.get("stage") == "design":
            spec_path = relative
        elif frontmatter.get("stage") in {None, "plan"}:
            plan_path = relative
    return spec_path, plan_path


def assemble_bundle(
    root: Path,
    *,
    change_id: str,
    reviewer: str,
    base: str | None = None,
    spec_plan_resolver: SpecPlanResolver | None = None,
) -> dict[str, Any]:
    """Build the review bundle dict for `change_id` / `reviewer`.

    Raises `BundleError` on a dirty in-scope tree or any git failure (fail-closed).
    """
    resolved_base = base or load_base_branch(root)
    cs = derive_state(events_path(root)).get(change_id)
    declared = list(cs.scope.get("files", [])) if cs is not None else []
    framework = cs.framework if cs is not None else None

    if working_tree_dirty(root, declared):
        raise BundleError(
            "in-scope files have uncommitted changes — commit them first "
            "(the review digest is over the committed HEAD diff)."
        )
    try:
        in_scope, out_scope = split_changed_by_scope(root, base=resolved_base, declared=declared)
        digest = committed_scope_digest(root, base=resolved_base, in_scope=in_scope)
    except GitScopeError as e:
        raise BundleError(str(e)) from e

    resolve = spec_plan_resolver or _no_spec_plan
    spec_path, plan_path = resolve(framework, root, change_id)
    try:
        declared_spec, declared_plan = resolve_declared_artifact_paths(
            root, declared, change_id
        )
    except GitScopeError as e:
        raise BundleError(str(e)) from e
    spec_path = spec_path or declared_spec
    plan_path = plan_path or declared_plan
    return {
        "change": change_id,
        "reviewer": reviewer,
        "base": resolved_base,
        "diff_in_scope": in_scope,
        "out_of_scope": out_scope,
        "spec_path": spec_path,
        "plan_path": plan_path,
        "checklist": resolve_checklist(root, reviewer),
        "bundle_digest": digest,
    }
