# tests/unit/core/test_review_verdict.py
"""Unit tests for core.review_verdict parse + coverage."""
from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.core.review_verdict import (
    VerdictError,
    check_coverage,
    parse_verdict_file,
)

_OK = """
bundle_digest: abc123
checklist:
  - item: spec-compliance
    status: pass
  - item: scope-adherence
    status: pass
  - item: code-quality
    status: pass
  - item: edge-cases
    status: pass
findings: []
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "v.yaml"
    p.write_text(text)
    return p


def test_parse_ok(tmp_path: Path) -> None:
    v = parse_verdict_file(_write(tmp_path, _OK))
    assert v["bundle_digest"] == "abc123"
    assert len(v["checklist"]) == 4


def test_parse_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(VerdictError, match="not found"):
        parse_verdict_file(tmp_path / "nope.yaml")


def test_parse_rejects_non_mapping(tmp_path: Path) -> None:
    with pytest.raises(VerdictError, match="mapping"):
        parse_verdict_file(_write(tmp_path, "- a\n- b\n"))


def test_parse_rejects_bad_status(tmp_path: Path) -> None:
    bad = _OK.replace("status: pass", "status: maybe", 1)
    with pytest.raises(VerdictError, match="status"):
        parse_verdict_file(_write(tmp_path, bad))


def test_parse_rejects_findings_required_when_a_check_fails(tmp_path: Path) -> None:
    # a checklist item fails but findings empty → invalid
    text = """
bundle_digest: x
checklist:
  - item: spec-compliance
    status: fail
findings: []
"""
    with pytest.raises(VerdictError, match="findings"):
        parse_verdict_file(_write(tmp_path, text))


def test_check_coverage_missing_item(tmp_path: Path) -> None:
    v = parse_verdict_file(_write(tmp_path, _OK))
    # require an item the verdict didn't cover
    missing = check_coverage(v, ["spec-compliance", "scope-adherence", "code-quality",
                                 "edge-cases", "security"])
    assert missing == ["security"]


def test_check_coverage_complete(tmp_path: Path) -> None:
    v = parse_verdict_file(_write(tmp_path, _OK))
    assert check_coverage(v, ["spec-compliance", "scope-adherence",
                              "code-quality", "edge-cases"]) == []
