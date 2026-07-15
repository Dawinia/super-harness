"""Built-in reviewer producer protocol registry."""

from __future__ import annotations

from super_harness.adapters.reviewer.base import ReviewerProtocolAdapter
from super_harness.adapters.reviewer.claude_cli import ClaudeCliReviewerProtocol
from super_harness.adapters.reviewer.codex_cli import CodexCliReviewerProtocol

_BUILTINS: dict[
    str, type[ClaudeCliReviewerProtocol] | type[CodexCliReviewerProtocol]
] = {
    ClaudeCliReviewerProtocol.name: ClaudeCliReviewerProtocol,
    CodexCliReviewerProtocol.name: CodexCliReviewerProtocol,
}


def list_reviewer_protocols() -> list[str]:
    """Return built-in protocol names in stable order."""

    return sorted(_BUILTINS)


def get_reviewer_protocol(
    name: str, *, executable: str | None = None
) -> ReviewerProtocolAdapter:
    """Instantiate a built-in protocol without invoking its producer."""

    cls = _BUILTINS.get(name)
    if cls is None:
        raise ValueError(
            f"unknown reviewer protocol {name!r}; known: {list_reviewer_protocols()}"
        )
    return cls(executable=executable)
