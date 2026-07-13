from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.engineering.review_governance import (
    ReviewerRoleGovernance,
    ReviewerSourceGovernance,
    ReviewGovernance,
    ReviewGovernanceError,
    load_review_governance,
)


def test_loads_tracked_review_governance(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  base_branch: trunk\n"
        "  sources:\n"
        "    codex:\n"
        "      kind: automated\n"
        "    claude:\n"
        "      kind: automated\n"
        "    human:\n"
        "      kind: human\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [codex, claude]\n"
        "      min_independent: 2\n"
        "      max_automatic_rounds_per_epoch: 2\n"
        "    code-reviewer:\n"
        "      participants: [codex, claude]\n"
        "      min_independent: 2\n"
        "      max_automatic_rounds_per_epoch: 3\n"
        "  require_distinct_model_families: true\n",
        encoding="utf-8",
    )

    assert load_review_governance(tmp_path) == ReviewGovernance(
        version=1,
        base_branch="trunk",
        sources={
            "codex": ReviewerSourceGovernance(name="codex", kind="automated"),
            "claude": ReviewerSourceGovernance(name="claude", kind="automated"),
            "human": ReviewerSourceGovernance(name="human", kind="human"),
        },
        roles={
            "plan-reviewer": ReviewerRoleGovernance(
                reviewer="plan-reviewer",
                participants=("codex", "claude"),
                min_independent=2,
                max_automatic_rounds_per_epoch=2,
            ),
            "code-reviewer": ReviewerRoleGovernance(
                reviewer="code-reviewer",
                participants=("codex", "claude"),
                min_independent=2,
                max_automatic_rounds_per_epoch=3,
            ),
        },
        require_distinct_model_families=True,
    )


def test_rejects_unknown_participant(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    codex: {kind: automated}\n"
        "  roles:\n"
        "    code-reviewer:\n"
        "      participants: [codex, claude]\n"
        "      min_independent: 2\n",
        encoding="utf-8",
    )

    with pytest.raises(ReviewGovernanceError, match=r"unknown participant.*claude"):
        load_review_governance(tmp_path)


def test_rejects_duplicate_participant(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    codex: {kind: automated}\n"
        "  roles:\n"
        "    code-reviewer:\n"
        "      participants: [codex, codex]\n",
        encoding="utf-8",
    )

    with pytest.raises(ReviewGovernanceError, match="duplicate participant"):
        load_review_governance(tmp_path)


def test_rejects_threshold_that_weakens_participant_set(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    codex: {kind: automated}\n"
        "    claude: {kind: automated}\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [codex, claude]\n"
        "      min_independent: 1\n",
        encoding="utf-8",
    )

    with pytest.raises(ReviewGovernanceError, match="must match participants count"):
        load_review_governance(tmp_path)


def test_legacy_policy_requires_manual_update(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "policy.yaml").write_text("reviewers: {}\n", encoding="utf-8")

    with pytest.raises(
        ReviewGovernanceError,
        match=r"legacy \.harness/policy\.yaml.*review-governance\.yaml",
    ):
        load_review_governance(tmp_path)


def test_defaults_automatic_round_budget_to_two(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    human: {kind: human}\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [human]\n",
        encoding="utf-8",
    )

    governance = load_review_governance(tmp_path)

    assert governance.roles["plan-reviewer"].max_automatic_rounds_per_epoch == 2
