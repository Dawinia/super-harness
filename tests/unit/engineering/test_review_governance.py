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
        "      max_automatic_rounds: 2\n"
        "    code-reviewer:\n"
        "      participants: [codex, claude]\n"
        "      min_independent: 2\n"
        "      max_automatic_rounds: 3\n"
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
                max_automatic_rounds=2,
            ),
            "code-reviewer": ReviewerRoleGovernance(
                reviewer="code-reviewer",
                participants=("codex", "claude"),
                min_independent=2,
                max_automatic_rounds=3,
            ),
        },
        require_distinct_model_families=True,
    )


def test_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    """Regression (PR#79 finding #4): a hand edit / merge-conflict leaving two
    `code-reviewer:` blocks must fail loudly, not silently last-wins to a weaker
    rule via yaml.safe_load."""
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  base_branch: main\n"
        "  sources:\n"
        "    codex:\n"
        "      kind: automated\n"
        "    claude:\n"
        "      kind: automated\n"
        "  roles:\n"
        "    code-reviewer:\n"
        "      participants: [codex, claude]\n"
        "      min_independent: 2\n"
        "      max_automatic_rounds: 2\n"
        "    code-reviewer:\n"
        "      participants: [claude]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds: 2\n",
        encoding="utf-8",
    )

    with pytest.raises(ReviewGovernanceError, match="duplicate YAML key"):
        load_review_governance(tmp_path)


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


def test_omitting_the_round_budget_is_legal_and_uses_the_role_default(
    tmp_path: Path,
) -> None:
    """Omitting the key stays legal; the value it resolves to is now per-role
    (see test_plan_reviewer_round_budget_defaults_to_six and its siblings)."""
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

    assert governance.roles["plan-reviewer"].max_automatic_rounds == 6


def _write_single_role_governance(tmp_path: Path, *, role_body: str) -> Path:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    codex: {kind: automated}\n"
        "    claude: {kind: automated}\n"
        "  roles:\n"
        "    code-reviewer:\n" + role_body,
        encoding="utf-8",
    )
    return tmp_path


def test_role_blocking_severity_defaults_to_major(tmp_path: Path) -> None:
    root = _write_single_role_governance(
        tmp_path, role_body="      participants: [codex, claude]\n"
    )

    governance = load_review_governance(root)

    assert governance.roles["code-reviewer"].blocking_severity == "major"


def test_role_blocking_severity_explicit_value_loads(tmp_path: Path) -> None:
    root = _write_single_role_governance(
        tmp_path,
        role_body=(
            "      participants: [codex, claude]\n"
            "      blocking_severity: blocker\n"
        ),
    )

    governance = load_review_governance(root)

    assert governance.roles["code-reviewer"].blocking_severity == "blocker"


def test_role_blocking_severity_rejects_unknown_value(tmp_path: Path) -> None:
    root = _write_single_role_governance(
        tmp_path,
        role_body=(
            "      participants: [codex, claude]\n"
            "      blocking_severity: nit\n"
        ),
    )

    with pytest.raises(ReviewGovernanceError, match="blocking_severity"):
        load_review_governance(root)


def test_role_blocking_severity_non_scalar_raises_governance_error(tmp_path: Path) -> None:
    # A non-scalar YAML value must raise ReviewGovernanceError, not an unhashable
    # TypeError from the set-membership test.
    root = _write_single_role_governance(
        tmp_path,
        role_body=(
            "      participants: [codex, claude]\n"
            "      blocking_severity: [major]\n"
        ),
    )

    with pytest.raises(ReviewGovernanceError, match="blocking_severity"):
        load_review_governance(root)


def _write_governance(tmp_path: Path, roles_yaml: str) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir(exist_ok=True)
    (harness / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    claude:\n"
        "      kind: automated\n"
        "  roles:\n" + roles_yaml,
        encoding="utf-8",
    )


def test_old_rounds_key_is_rejected_with_no_deprecation_period(tmp_path: Path) -> None:
    """The value's meaning changed from per-epoch to per-change accumulation. A config
    that reads identically while its `2` means something else is the same failure as a
    silently inert setting — better to make someone edit one line."""
    _write_governance(tmp_path, (
        "    plan-reviewer:\n"
        "      participants: [claude]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds_per_epoch: 2\n"
    ))

    with pytest.raises(ReviewGovernanceError) as excinfo:
        load_review_governance(tmp_path)

    message = str(excinfo.value)
    assert "max_automatic_rounds" in message
    assert "max_automatic_rounds" in message
    assert "per-change" in message


def test_plan_reviewer_round_budget_defaults_to_six(tmp_path: Path) -> None:
    _write_governance(tmp_path, (
        "    plan-reviewer:\n"
        "      participants: [claude]\n"
        "      min_independent: 1\n"
    ))
    assert load_review_governance(tmp_path).roles["plan-reviewer"].max_automatic_rounds == 6


def test_unknown_role_keeps_the_fallback_of_two(tmp_path: Path) -> None:
    """`review.roles` keys are arbitrary non-empty strings. An adopter-defined role is
    not a new error condition; it gets today's single literal."""
    _write_governance(tmp_path, (
        "    design-reviewer:\n"
        "      participants: [claude]\n"
        "      min_independent: 1\n"
    ))
    assert load_review_governance(tmp_path).roles["design-reviewer"].max_automatic_rounds == 2


def test_explicit_round_budget_overrides_the_per_role_default(tmp_path: Path) -> None:
    _write_governance(tmp_path, (
        "    plan-reviewer:\n"
        "      participants: [claude]\n"
        "      min_independent: 1\n"
        "      max_automatic_rounds: 3\n"
    ))
    assert load_review_governance(tmp_path).roles["plan-reviewer"].max_automatic_rounds == 3


def test_code_reviewer_default_is_four_because_restarts_carry_rounds_forward(
    tmp_path: Path,
) -> None:
    """Per-change counting is NOT behaviour-neutral on the code path: four events
    re-enter a state that re-fires `implementation_complete`, so a restarted change
    carries its earlier code rounds forward where the per-epoch counter washed them.
    Keeping 2 would silently tighten the code path under a rename — replayed, 2 fires
    8 times across 5 of 10 corpus changes and 4 never fires."""
    _write_governance(tmp_path, (
        "    code-reviewer:\n"
        "      participants: [claude]\n"
        "      min_independent: 1\n"
    ))
    assert load_review_governance(tmp_path).roles["code-reviewer"].max_automatic_rounds == 4
