"""Reviewer-strategy policy (HG-02.C).

Reads `.harness/policy.yaml` → `reviewers.<name>.strategy`. The strategy tells the
agent how to produce a review verdict (the harness never runs the review itself):

- `subagent` (default) — dispatch a reviewer subagent (the agent's own Task tool).
- `human`             — hand off to a human, who records `review approve|reject`.
- `hybrid`            — subagent first, escalate to a human on a fail / Large tier.

A user sets `human` when a token budget rules out subagent review for everything.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Lifecycle review state → the reviewer responsible for it (SSOT).
REVIEW_STATE_REVIEWER: dict[str, str] = {
    "AWAITING_PLAN_REVIEW": "plan-reviewer",
    "AWAITING_CODE_REVIEW": "code-reviewer",
}

_STRATEGIES = ("subagent", "human", "hybrid")
_DEFAULT_STRATEGY = "subagent"


class ReviewerPolicyError(ValueError):
    """`.harness/policy.yaml` is present but its reviewers block is malformed."""


def load_reviewer_strategy(root: Path, reviewer: str) -> str:
    """Return the configured strategy for `reviewer` (default `subagent`).

    Tolerant of an absent policy.yaml / absent `reviewers` block / absent reviewer
    (→ default). Raises `ReviewerPolicyError` on unparseable YAML or a strategy value
    outside {subagent, human, hybrid}.
    """
    policy_path = root / ".harness" / "policy.yaml"
    if not policy_path.is_file():
        return _DEFAULT_STRATEGY
    try:
        parsed: Any = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ReviewerPolicyError(f"policy.yaml is not valid YAML: {e}") from e
    if not isinstance(parsed, dict):
        return _DEFAULT_STRATEGY
    reviewers = parsed.get("reviewers")
    if not isinstance(reviewers, dict):
        return _DEFAULT_STRATEGY
    block = reviewers.get(reviewer)
    if not isinstance(block, dict) or "strategy" not in block:
        return _DEFAULT_STRATEGY
    strategy = block["strategy"]
    if strategy not in _STRATEGIES:
        raise ReviewerPolicyError(
            f"reviewers.{reviewer}.strategy={strategy!r} is not one of {list(_STRATEGIES)}"
        )
    return str(strategy)
