"""Unit tests for the Layer-2 merge-gate domain logic (HG-DF C)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.attestation import (
    DiffEntry,
    canonical_path,
    check_attestation,
    extract_change_events,
    parse_name_status,
    verify_attestations,
    write_attestation,
)


# --------------------------------------------------------------------------- #
# Task 1: canonical_path
# --------------------------------------------------------------------------- #
def test_canonical_path_strips_dot_slash_and_normalizes():
    assert canonical_path("./src/x.py") == "src/x.py"
    assert canonical_path("src/x.py") == "src/x.py"
    assert canonical_path("src/../src/x.py") == "src/x.py"
    assert canonical_path("docs/a/") == "docs/a"
    assert canonical_path("  src/x.py  ") == "src/x.py"
    assert canonical_path(".") == ""


# --------------------------------------------------------------------------- #
# Task 2: parse_name_status
# --------------------------------------------------------------------------- #
def test_parse_name_status_handles_amd_and_rename():
    raw = "A\tsrc/new.py\nM\tdocs/x.md\nD\told.py\nR096\tsrc/a.py\tsrc/b.py\n\n"
    entries = parse_name_status(raw)
    assert entries == [
        DiffEntry(status="A", paths=("src/new.py",)),
        DiffEntry(status="M", paths=("docs/x.md",)),
        DiffEntry(status="D", paths=("old.py",)),
        DiffEntry(status="R096", paths=("src/a.py", "src/b.py")),
    ]


def test_parse_name_status_canonicalizes_paths():
    entries = parse_name_status("A\t./src/x.py\n")
    assert entries[0].paths == ("src/x.py",)


# --------------------------------------------------------------------------- #
# Task 3: extract + write
# --------------------------------------------------------------------------- #
def _ev_line(change_id: str, etype: str) -> str:
    return json.dumps({"change_id": change_id, "type": etype, "event_id": "x"})


def test_extract_filters_by_change_id(tmp_path):
    ef = tmp_path / "events.jsonl"
    ef.write_text(
        _ev_line("a", "intent_declared") + "\n" + _ev_line("b", "intent_declared") + "\n"
    )
    assert extract_change_events(ef, "a") == [_ev_line("a", "intent_declared")]


def test_extract_raises_when_no_match(tmp_path):
    ef = tmp_path / "events.jsonl"
    ef.write_text(_ev_line("b", "intent_declared") + "\n")
    with pytest.raises(ValueError):
        extract_change_events(ef, "a")


def test_extract_raises_when_file_missing(tmp_path):
    with pytest.raises(ValueError):
        extract_change_events(tmp_path / "nope.jsonl", "a")


def test_write_attestation_roundtrips(tmp_path):
    ef = tmp_path / "events.jsonl"
    ef.write_text(_ev_line("a", "intent_declared") + "\n")
    out = write_attestation(ef, tmp_path / "att", "a")
    assert out.name == "a.jsonl"
    assert out.read_text().strip() == _ev_line("a", "intent_declared")


# --------------------------------------------------------------------------- #
# Shared lifecycle-stream helpers (emit real ordered events via EventWriter)
# --------------------------------------------------------------------------- #
def _emit(writer: EventWriter, etype: str, slug: str, payload=None) -> None:
    writer.emit(
        Event(
            event_id=new_event_id(),
            type=etype,
            change_id=slug,
            timestamp=utc_now_iso(),
            actor=Actor(type="human", identifier="t"),
            framework="plain",
            payload=payload or {},
        )
    )


def _ready_stream(path: Path, slug: str) -> None:
    w = EventWriter(path)
    _emit(w, "intent_declared", slug)
    _emit(w, "plan_ready", slug, {"scope": {"files": ["src/x.py"]}})
    _emit(w, "plan_approved", slug)
    _emit(w, "implementation_started", slug)
    _emit(w, "verification_passed", slug)
    _emit(w, "implementation_complete", slug)
    _emit(w, "code_review_passed", slug)


# --------------------------------------------------------------------------- #
# Task 4: check_attestation
# --------------------------------------------------------------------------- #
def test_check_attestation_clean_ready_stream_passes(tmp_path):
    att = tmp_path / "s.jsonl"
    _ready_stream(att, "s")
    assert check_attestation(att, "s") == []


def test_check_attestation_not_ready_fails(tmp_path):
    att = tmp_path / "s.jsonl"
    w = EventWriter(att)
    _emit(w, "intent_declared", "s")
    _emit(w, "plan_ready", "s", {"scope": {"files": ["src/x.py"]}})
    blockers = check_attestation(att, "s")
    assert any("READY_TO_MERGE" in b for b in blockers)


def test_check_attestation_filename_content_mismatch_fails(tmp_path):
    att = tmp_path / "wrong.jsonl"
    _ready_stream(att, "s")  # content change_id is "s"; filename slug is "wrong"
    blockers = check_attestation(att, "wrong")
    assert any("does not match" in b for b in blockers)


def test_check_attestation_withdrawn_shortcut_fails_milestone(tmp_path):
    att = tmp_path / "s.jsonl"
    w = EventWriter(att)
    _emit(w, "intent_declared", "s")
    _emit(w, "plan_ready", "s", {"scope": {"files": ["src/x.py"]}})
    _emit(w, "plan_approved", "s")
    _emit(w, "implementation_started", "s")
    _emit(w, "verification_passed", "s")
    _emit(w, "implementation_complete", "s")
    _emit(w, "implementation_withdrawn", "s")  # → READY_TO_MERGE without review
    blockers = check_attestation(att, "s")
    assert any("milestone" in b for b in blockers)


# --------------------------------------------------------------------------- #
# Task 5: verify_attestations
# --------------------------------------------------------------------------- #
def _ready_with_scope(root: Path, slug: str, files: list[str]) -> None:
    att_dir = root / ".harness" / "attestations"
    att_dir.mkdir(parents=True, exist_ok=True)
    w = EventWriter(att_dir / f"{slug}.jsonl")
    _emit(w, "intent_declared", slug)
    _emit(w, "plan_ready", slug, {"scope": {"files": files}})
    _emit(w, "plan_approved", slug)
    _emit(w, "implementation_started", slug)
    _emit(w, "verification_passed", slug)
    _emit(w, "implementation_complete", slug)
    _emit(w, "code_review_passed", slug)


def test_verify_covered_subject_passes(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])
    diff = [
        DiffEntry("A", (".harness/attestations/s.jsonl",)),
        DiffEntry("M", ("src/x.py",)),
    ]
    v = verify_attestations(tmp_path, diff)
    assert v.ok, v.blockers


def test_verify_uncovered_subject_fails_bypass(tmp_path):
    # A file changed with NO attestation at all — the Bash-bypass case.
    diff = [DiffEntry("A", ("src/snuck_in.py",))]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("snuck_in.py" in b for b in v.blockers)


def test_verify_scope_drift_fails(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])
    diff = [
        DiffEntry("A", (".harness/attestations/s.jsonl",)),
        DiffEntry("M", ("src/x.py",)),
        DiffEntry("M", ("src/UNDECLARED.py",)),
    ]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("UNDECLARED" in b for b in v.blockers)


def test_verify_modified_attestation_fails(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])
    diff = [
        DiffEntry("M", (".harness/attestations/s.jsonl",)),
        DiffEntry("M", ("src/x.py",)),
    ]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("only newly-ADDED" in b for b in v.blockers)


def test_verify_attestation_only_diff_fails(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py"])  # x.py NOT in this diff
    diff = [DiffEntry("A", (".harness/attestations/s.jsonl",))]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("covers no file in this diff" in b for b in v.blockers)


def test_verify_deletion_must_be_in_scope(tmp_path):
    _ready_with_scope(tmp_path, "s", ["src/x.py", "src/gone.py"])
    diff = [
        DiffEntry("A", (".harness/attestations/s.jsonl",)),
        DiffEntry("M", ("src/x.py",)),
        DiffEntry("D", ("src/gone.py",)),
    ]
    v = verify_attestations(tmp_path, diff)
    assert v.ok, v.blockers  # deletion declared in scope → covered


def test_verify_empty_scope_attestation_covers_nothing(tmp_path):
    att_dir = tmp_path / ".harness" / "attestations"
    att_dir.mkdir(parents=True, exist_ok=True)
    w = EventWriter(att_dir / "s.jsonl")
    # full ordered lifecycle but NO --scope → scope == {}
    _emit(w, "intent_declared", "s")
    _emit(w, "plan_ready", "s")
    _emit(w, "plan_approved", "s")
    _emit(w, "implementation_started", "s")
    _emit(w, "verification_passed", "s")
    _emit(w, "implementation_complete", "s")
    _emit(w, "code_review_passed", "s")
    diff = [
        DiffEntry("A", (".harness/attestations/s.jsonl",)),
        DiffEntry("M", ("src/x.py",)),
    ]
    v = verify_attestations(tmp_path, diff)
    assert not v.ok
    assert any("src/x.py" in b for b in v.blockers)
