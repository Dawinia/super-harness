"""Unit tests for engineering.operation_log.write_operation_log.

Rule-of-three factor: the helper was introduced when L1Updater (Phase 13)
became the second operation-log writer after `init --setup-github` (Phase 12).
The two writers share the same on-disk mechanism (parent-mkdir + colon-
sanitized timestamp filename + best-effort swallow on OSError) but compose
different bodies.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from super_harness.engineering.operation_log import write_operation_log


def test_write_operation_log_creates_subdir_and_file(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()
    body = "operation: example\noutcome: OK\n"

    write_operation_log(harness, "example-op", body)

    subdir = harness / "operation-logs" / "example-op"
    assert subdir.is_dir()
    files = list(subdir.glob("*.log"))
    assert len(files) == 1
    assert files[0].read_text() == body


def test_write_operation_log_sanitizes_colons_in_filename(tmp_path: Path) -> None:
    harness = tmp_path / ".harness"
    harness.mkdir()

    # Force a deterministic timestamp with colons in it.
    with patch(
        "super_harness.engineering.operation_log.utc_now_iso",
        return_value="2026-05-30T12:34:56Z",
    ):
        write_operation_log(harness, "sub", "body")

    file = harness / "operation-logs" / "sub" / "2026-05-30T12-34-56Z.log"
    assert file.exists()
    assert file.read_text() == "body"


def test_write_operation_log_swallows_oserror(tmp_path: Path) -> None:
    """Pre-create a file where the subdir should live → mkdir raises OSError."""
    harness = tmp_path / ".harness"
    harness.mkdir()
    # Block subdir creation: put a regular file at the operation-logs dir path.
    (harness / "operation-logs").write_text("not a directory")

    # Must not raise.
    write_operation_log(harness, "sub", "body")


def test_write_operation_log_swallows_write_oserror(tmp_path: Path) -> None:
    """Even if mkdir succeeds, a subsequent write OSError must be swallowed."""
    harness = tmp_path / ".harness"
    harness.mkdir()

    with patch(
        "super_harness.engineering.operation_log.Path.write_text",
        side_effect=OSError("disk full"),
    ):
        # Must not raise.
        write_operation_log(harness, "sub", "body")
