from __future__ import annotations

import pytest

from super_harness.adapters.reviewer.claude_cli import ClaudeCliReviewerProtocol
from super_harness.adapters.reviewer.codex_cli import CodexCliReviewerProtocol
from super_harness.adapters.reviewer.registry import (
    get_reviewer_protocol,
    list_reviewer_protocols,
)


def test_registry_resolves_builtin_reviewer_protocols_without_running_them() -> None:
    assert list_reviewer_protocols() == ["claude-cli", "codex-cli"]
    assert isinstance(
        get_reviewer_protocol("codex-cli", executable="/opt/bin/codex"),
        CodexCliReviewerProtocol,
    )
    assert isinstance(
        get_reviewer_protocol("claude-cli", executable="/opt/bin/claude"),
        ClaudeCliReviewerProtocol,
    )


def test_registry_rejects_unknown_reviewer_protocol() -> None:
    with pytest.raises(ValueError, match="unknown reviewer protocol"):
        get_reviewer_protocol("task-subagent", executable="/opt/bin/task")
