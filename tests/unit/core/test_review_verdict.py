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


def test_read_change_events_filters_and_tolerates(tmp_path: Path) -> None:
    from super_harness.core.review_verdict import read_change_events

    f = tmp_path / "events.jsonl"
    f.write_text(
        '{"event_id":"e1","type":"intent_declared","change_id":"c",'
        '"timestamp":"2026-06-23T00:00:00Z",'
        '"actor":{"type":"human","identifier":"t"},"framework":"plain","payload":{}}\n'
        "this is not json\n"
        '{"event_id":"e2","type":"code_review_failed","change_id":"other",'
        '"timestamp":"2026-06-23T00:00:01Z",'
        '"actor":{"type":"human","identifier":"t"},"framework":"plain","payload":{}}\n'
        '{"event_id":"e3","type":"code_review_failed","change_id":"c",'
        '"timestamp":"2026-06-23T00:00:02Z",'
        '"actor":{"type":"human","identifier":"t"},"framework":"plain","payload":{}}\n'
    )
    evs = read_change_events(f, "c")
    assert [e.event_id for e in evs] == ["e1", "e3"]  # malformed skipped, "other" filtered


def test_read_change_events_missing_file_returns_empty(tmp_path: Path) -> None:
    from super_harness.core.review_verdict import read_change_events

    assert read_change_events(tmp_path / "nope.jsonl", "c") == []


# NOTE: severity FIRST so `id:` is on its own 4-space-indented line and the
# string-replace below actually strips it (B1 fix — a `- id:` inline list item
# cannot be stripped by line).
_FAIL_NO_ID = """
bundle_digest: abc123
checklist:
  - item: spec-compliance
    status: fail
findings:
  - severity: blocker
    file: src/x.py
    summary: boom
"""


def test_findings_require_id(tmp_path: Path) -> None:
    with pytest.raises(VerdictError, match="id"):
        parse_verdict_file(_write(tmp_path, _FAIL_NO_ID))


def test_prior_findings_shape_validated(tmp_path: Path) -> None:
    base = _OK + "prior_findings:\n  - id: f-001\n    disposition: resolved\n"
    assert parse_verdict_file(_write(tmp_path, base))  # resolved needs no note → ok

    bad_disp = _OK + "prior_findings:\n  - id: f-001\n    disposition: bogus\n"
    with pytest.raises(VerdictError, match="disposition"):
        parse_verdict_file(_write(tmp_path, bad_disp))

    wontfix_no_note = _OK + "prior_findings:\n  - id: f-001\n    disposition: wontfix\n"
    with pytest.raises(VerdictError, match="note"):
        parse_verdict_file(_write(tmp_path, wontfix_no_note))

    missing_id = _OK + "prior_findings:\n  - disposition: resolved\n"
    with pytest.raises(VerdictError, match="id"):
        parse_verdict_file(_write(tmp_path, missing_id))


def _failed(slug: str, findings: list[str], prior: list[tuple[str, str]] | None = None):
    from super_harness.core.events import Actor, Event
    from super_harness.core.ulid import new_event_id

    verdict = {
        "findings": [{"id": i, "severity": "major", "file": "x", "summary": "s"} for i in findings],
        "prior_findings": [{"id": i, "disposition": d, "note": "n"} for i, d in (prior or [])],
    }
    return Event(
        event_id=new_event_id(), type="code_review_failed", change_id=slug,
        timestamp="2026-06-23T00:00:00Z",
        actor=Actor(type="human", identifier="t"), framework="plain",
        payload={"verdict": verdict},
    )


def test_open_findings_single_reject() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    assert derive_open_findings([_failed("c", ["f1", "f2"])], "c") == ["f1", "f2"]


def test_open_findings_resolved_in_later_reject() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    evs = [_failed("c", ["f1", "f2"]), _failed("c", ["f3"], prior=[("f1", "resolved")])]
    assert derive_open_findings(evs, "c") == ["f2", "f3"]


def test_open_findings_resolved_then_reopened() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    # reject2 disposes f1 AND re-lists it → reopened, stays open
    evs = [_failed("c", ["f1"]), _failed("c", ["f1"], prior=[("f1", "resolved")])]
    assert derive_open_findings(evs, "c") == ["f1"]


def test_open_findings_dispose_unknown_id_is_noop() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    evs = [_failed("c", ["f1"], prior=[("ghost", "resolved")])]
    assert derive_open_findings(evs, "c") == ["f1"]


def test_open_findings_ignores_other_change_and_non_failed() -> None:
    from super_harness.core.review_verdict import derive_open_findings
    assert derive_open_findings([_failed("other", ["f1"])], "c") == []
