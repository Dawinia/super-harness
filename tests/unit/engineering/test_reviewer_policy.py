"""Unit tests for reviewer-strategy policy (HG-02.C).

`load_reviewer_strategy` reads `.harness/policy.yaml` → `reviewers.<name>.strategy`,
defaulting to "subagent" so a user only sets it when they need human review (e.g.
under a token budget). The harness does not enforce the strategy — it tells the
agent whether to dispatch a Task subagent or hand off to a human.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.engineering.reviewer_policy import (
    REVIEW_STATE_REVIEWER,
    ReviewerPolicyError,
    load_reviewer_strategy,
)


def _write_policy(root: Path, body: str) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "policy.yaml").write_text(body)


def test_default_subagent_when_no_file(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "subagent"


def test_default_subagent_when_no_reviewers_block(tmp_path: Path) -> None:
    _write_policy(tmp_path, "gates:\n  pre_tool_use: {}\n")
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "subagent"


def test_reads_configured_human_strategy(tmp_path: Path) -> None:
    _write_policy(tmp_path, "reviewers:\n  plan-reviewer:\n    strategy: human\n")
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "human"


def test_per_reviewer_independent(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        "reviewers:\n  plan-reviewer:\n    strategy: human\n"
        "  code-reviewer:\n    strategy: hybrid\n",
    )
    assert load_reviewer_strategy(tmp_path, "plan-reviewer") == "human"
    assert load_reviewer_strategy(tmp_path, "code-reviewer") == "hybrid"


def test_bad_strategy_raises(tmp_path: Path) -> None:
    _write_policy(tmp_path, "reviewers:\n  plan-reviewer:\n    strategy: robot\n")
    with pytest.raises(ReviewerPolicyError):
        load_reviewer_strategy(tmp_path, "plan-reviewer")


def test_state_to_reviewer_mapping() -> None:
    assert REVIEW_STATE_REVIEWER["AWAITING_PLAN_REVIEW"] == "plan-reviewer"
    assert REVIEW_STATE_REVIEWER["AWAITING_CODE_REVIEW"] == "code-reviewer"
