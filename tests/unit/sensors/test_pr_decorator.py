"""Unit tests for PRDecorator sensor and _merge_metadata_block helper.

TDD order: tests were written before the implementation.

Pure-function helper tests (no mocking):
1. test_merge_appends_when_no_block_present
2. test_merge_replaces_when_single_balanced_block
3. test_merge_preserves_user_content_around_block
4. test_merge_raises_on_two_blocks
5. test_merge_raises_on_unbalanced_dangling_end
6. test_merge_raises_on_unbalanced_unclosed_begin
7. test_merge_no_markers_at_all_appends_clean

Sensor (mocked gh) tests:
8.  test_check_appends_when_no_existing_block
9.  test_check_replaces_when_block_already_present
10. test_check_propagates_typed_exception_on_two_blocks
11. test_check_handles_null_body
12. test_check_idempotent_on_rerun

Registration:
13. test_pr_decorator_registered_as_builtin
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from super_harness.core.events import Actor, Event
from super_harness.core.ulid import new_event_id
from super_harness.engineering.pr_metadata import (
    METADATA_BEGIN,
    METADATA_END,
    parse_metadata_block,
)
from super_harness.sensors import WorkspaceContext
from super_harness.sensors.pr_decorator import PRDecorator, PRDecoratorError, _merge_metadata_block

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_block(change_id: str) -> str:
    """Build a metadata block string for the given change_id (no file I/O)."""
    lines = [
        METADATA_BEGIN,
        f"Change: {change_id}",
        "Tier: unknown",
        "Verification: pending",
        "super-harness version: v0.0.0-test",
        METADATA_END,
    ]
    return "\n".join(lines)


def _two_blocks() -> str:
    """PR body with two well-formed metadata blocks."""
    b1 = _make_block("change-one")
    b2 = _make_block("change-two")
    return f"Some user text.\n\n{b1}\n\nMiddle text.\n\n{b2}\n"


def _pr_event(pr_number: int, change_id: str) -> Event:
    return Event(
        event_id=new_event_id(),
        type="pr_opened",
        change_id=change_id,
        timestamp="2026-05-30T10:00:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework="plain",
        payload={"pr_number": pr_number},
    )


def _workspace(tmp_path: Path) -> WorkspaceContext:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return WorkspaceContext(workspace_root=tmp_path)


# --------------------------------------------------------------------------- #
# 1. Merge helper: append when no block present
# --------------------------------------------------------------------------- #


def test_merge_appends_when_no_block_present() -> None:
    body = "This is the PR description."
    block = _make_block("ch-1")

    merged = _merge_metadata_block(body, block)

    assert merged.startswith(body.rstrip())
    assert METADATA_BEGIN in merged
    assert METADATA_END in merged
    result = parse_metadata_block(merged)
    assert result.present is True
    assert result.block_count == 1
    assert result.fields["Change"] == "ch-1"


# --------------------------------------------------------------------------- #
# 2. Merge helper: replace when single balanced block
# --------------------------------------------------------------------------- #


def test_merge_replaces_when_single_balanced_block() -> None:
    old_block = _make_block("old-change")
    new_block = _make_block("new-change")
    body = f"User header text.\n\n{old_block}\n"

    merged = _merge_metadata_block(body, new_block)

    assert "User header text." in merged
    result = parse_metadata_block(merged)
    assert result.present is True
    assert result.block_count == 1
    assert result.fields["Change"] == "new-change"


# --------------------------------------------------------------------------- #
# 3. Merge helper: user content preserved around the block
# --------------------------------------------------------------------------- #


def test_merge_preserves_user_content_around_block() -> None:
    old_block = _make_block("old-ch")
    new_block = _make_block("new-ch")
    body = f"user text above\n\n{old_block}\n\nuser text below"

    merged = _merge_metadata_block(body, new_block)

    assert "user text above" in merged
    assert "user text below" in merged
    result = parse_metadata_block(merged)
    assert result.block_count == 1
    assert result.fields["Change"] == "new-ch"


# --------------------------------------------------------------------------- #
# 4. Merge helper: raises on two blocks
# --------------------------------------------------------------------------- #


def test_merge_raises_on_two_blocks() -> None:
    body = _two_blocks()
    new_block = _make_block("latest")

    with pytest.raises(PRDecoratorError) as exc_info:
        _merge_metadata_block(body, new_block)

    assert "2" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# 5. Merge helper: raises on unbalanced dangling END
# --------------------------------------------------------------------------- #


def test_merge_raises_on_unbalanced_dangling_end() -> None:
    # METADATA_END without a preceding METADATA_BEGIN
    body = f"Some text.\n{METADATA_END}\nMore text."
    new_block = _make_block("ch")

    with pytest.raises(PRDecoratorError):
        _merge_metadata_block(body, new_block)


# --------------------------------------------------------------------------- #
# 6. Merge helper: raises on unbalanced unclosed BEGIN
# --------------------------------------------------------------------------- #


def test_merge_raises_on_unbalanced_unclosed_begin() -> None:
    # METADATA_BEGIN with no closing METADATA_END
    body = f"Some text.\n{METADATA_BEGIN}\nChange: ch-x\nMore text."
    new_block = _make_block("ch")

    with pytest.raises(PRDecoratorError):
        _merge_metadata_block(body, new_block)


# --------------------------------------------------------------------------- #
# 7. Merge helper: no markers at all → append (not raise)
# --------------------------------------------------------------------------- #


def test_merge_no_markers_at_all_appends_clean() -> None:
    body = "PR description without any harness markers."
    new_block = _make_block("clean-ch")

    # Must not raise — this is the "no block" path, not "malformed"
    merged = _merge_metadata_block(body, new_block)

    result = parse_metadata_block(merged)
    assert result.present is True
    assert result.block_count == 1


# --------------------------------------------------------------------------- #
# 8. Sensor check: appends when no existing block
# --------------------------------------------------------------------------- #


def test_check_appends_when_no_existing_block(tmp_path: Path) -> None:
    sensor = PRDecorator()
    ctx = _workspace(tmp_path)
    trigger = _pr_event(pr_number=42, change_id="ch-append")

    with (
        patch("super_harness.sensors.pr_decorator.view_pr") as mock_view,
        patch("super_harness.sensors.pr_decorator.edit_pr_body") as mock_edit,
        patch("super_harness.sensors.pr_decorator.build_metadata") as mock_build,
    ):
        mock_view.return_value = {"body": "Hello PR"}
        mock_build.return_value = _make_block("ch-append")

        result = sensor.check(trigger, ctx)

    mock_edit.assert_called_once()
    edited_body: str = mock_edit.call_args[0][1]
    assert METADATA_BEGIN in edited_body
    assert "ch-append" in edited_body
    assert result.status == "pass"
    assert "42" in result.summary
    assert result.emit_events == []


# --------------------------------------------------------------------------- #
# 9. Sensor check: replaces when block already present
# --------------------------------------------------------------------------- #


def test_check_replaces_when_block_already_present(tmp_path: Path) -> None:
    sensor = PRDecorator()
    ctx = _workspace(tmp_path)
    trigger = _pr_event(pr_number=7, change_id="ch-replace")

    old_block = _make_block("old-change-id")
    existing_body = f"User description.\n\n{old_block}\n"
    new_block = _make_block("ch-replace")

    with (
        patch("super_harness.sensors.pr_decorator.view_pr") as mock_view,
        patch("super_harness.sensors.pr_decorator.edit_pr_body") as mock_edit,
        patch("super_harness.sensors.pr_decorator.build_metadata") as mock_build,
    ):
        mock_view.return_value = {"body": existing_body}
        mock_build.return_value = new_block

        result = sensor.check(trigger, ctx)

    mock_edit.assert_called_once()
    edited_body: str = mock_edit.call_args[0][1]
    parsed = parse_metadata_block(edited_body)
    assert parsed.block_count == 1
    assert parsed.fields["Change"] == "ch-replace"
    assert result.status == "pass"


# --------------------------------------------------------------------------- #
# 10. Sensor check: propagates PRDecoratorError on two blocks
# --------------------------------------------------------------------------- #


def test_check_propagates_typed_exception_on_two_blocks(tmp_path: Path) -> None:
    sensor = PRDecorator()
    ctx = _workspace(tmp_path)
    trigger = _pr_event(pr_number=99, change_id="ch-double")

    two_block_body = _two_blocks()
    new_block = _make_block("ch-double")

    with (
        patch("super_harness.sensors.pr_decorator.view_pr") as mock_view,
        patch("super_harness.sensors.pr_decorator.edit_pr_body"),
        patch("super_harness.sensors.pr_decorator.build_metadata") as mock_build,
    ):
        mock_view.return_value = {"body": two_block_body}
        mock_build.return_value = new_block

        with pytest.raises(PRDecoratorError):
            sensor.check(trigger, ctx)


# --------------------------------------------------------------------------- #
# 11. Sensor check: handles null body (treats as empty)
# --------------------------------------------------------------------------- #


def test_check_handles_null_body(tmp_path: Path) -> None:
    sensor = PRDecorator()
    ctx = _workspace(tmp_path)
    trigger = _pr_event(pr_number=5, change_id="ch-null")
    new_block = _make_block("ch-null")

    with (
        patch("super_harness.sensors.pr_decorator.view_pr") as mock_view,
        patch("super_harness.sensors.pr_decorator.edit_pr_body") as mock_edit,
        patch("super_harness.sensors.pr_decorator.build_metadata") as mock_build,
    ):
        mock_view.return_value = {"body": None}
        mock_build.return_value = new_block

        result = sensor.check(trigger, ctx)

    # Must not crash; append path taken
    mock_edit.assert_called_once()
    edited_body: str = mock_edit.call_args[0][1]
    assert METADATA_BEGIN in edited_body
    assert result.status == "pass"


# --------------------------------------------------------------------------- #
# 12. Sensor check: idempotent on re-run
# --------------------------------------------------------------------------- #


def test_check_idempotent_on_rerun(tmp_path: Path) -> None:
    sensor = PRDecorator()
    ctx = _workspace(tmp_path)
    change_id = "ch-idem"
    pr_number = 11
    trigger = _pr_event(pr_number=pr_number, change_id=change_id)

    new_block = _make_block(change_id)
    # Simulate first run: body starts empty-ish
    initial_body = "Initial PR description."

    bodies: list[str] = [initial_body]

    def fake_view(pn: int, *, fields: list[str]) -> dict[str, Any]:
        return {"body": bodies[-1]}

    def fake_edit(pn: int, body: str) -> None:
        bodies.append(body)

    def fake_build(cid: str, root: Path) -> str:
        return new_block

    with (
        patch("super_harness.sensors.pr_decorator.view_pr", side_effect=fake_view),
        patch("super_harness.sensors.pr_decorator.edit_pr_body", side_effect=fake_edit),
        patch("super_harness.sensors.pr_decorator.build_metadata", side_effect=fake_build),
    ):
        # First call (append path)
        r1 = sensor.check(trigger, ctx)
        assert r1.status == "pass"
        assert len(bodies) == 2

        # Second call (replace path — body now has the block)
        r2 = sensor.check(trigger, ctx)
        assert r2.status == "pass"
        assert len(bodies) == 3

    # After both calls, only one block in the final body
    final = parse_metadata_block(bodies[-1])
    assert final.block_count == 1
    assert final.fields["Change"] == change_id


# --------------------------------------------------------------------------- #
# 13. Registration
# --------------------------------------------------------------------------- #


def test_pr_decorator_registered_as_builtin() -> None:
    from super_harness.sensors.registry import get_builtin

    assert get_builtin("PR-decorator") is PRDecorator


# --------------------------------------------------------------------------- #
# 14. Regression: re.sub replacement is lambda-wrapped, so `block` containing
# backslash sequences is treated as a literal string (whole-branch review
# MINOR-2 fix — was raw `block` arg, which re.sub interprets as repl with
# backreferences like `\1` / `\g<name>`).
# --------------------------------------------------------------------------- #


def test_merge_replace_treats_block_as_literal_not_backref() -> None:
    """Without the lambda wrapper, a `block` containing `\\1` makes re.sub
    raise ``re.error: invalid group reference 1`` because the substitution
    pattern has no capture group. With the lambda, the block is returned
    literally. Defensive guard for any future field value that contains
    backslashes (e.g. a future Windows-style path in a payload).
    """
    from super_harness.engineering.pr_metadata import (
        METADATA_BEGIN,
        METADATA_END,
        parse_metadata_block,
    )
    from super_harness.sensors.pr_decorator import _merge_metadata_block

    body = (
        "Some PR text\n\n"
        f"{METADATA_BEGIN}\n"
        "Change: old-slug\n"
        f"{METADATA_END}\n"
    )
    block = (
        f"{METADATA_BEGIN}\n"
        "Change: new-slug-with-\\1-literal\n"
        f"{METADATA_END}"
    )

    # No raise — the lambda made re.sub treat block as literal.
    result = _merge_metadata_block(body, block)

    assert "new-slug-with-\\1-literal" in result
    parsed = parse_metadata_block(result)
    assert parsed.block_count == 1
    assert parsed.fields["Change"] == "new-slug-with-\\1-literal"
