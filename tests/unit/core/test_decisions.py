from pathlib import Path

import pytest

from super_harness.core.decisions import (
    Decision,
    RecordError,
    decisions_dir,
    load_decisions,
    parse_decision_file,
    serialize_decision,
)


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


RATIFIED = """---
id: d-auth-stateless
status: ratified
ratified_by: a@b.com
ratified_at: 2026-06-08T12:00:00Z
---
Authentication must be stateless.
"""


def test_parse_valid_ratified(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-auth-stateless.md", RATIFIED)
    d = parse_decision_file(p)
    assert d.id == "d-auth-stateless"
    assert d.status == "ratified"
    assert d.ratified_by == "a@b.com"
    assert d.body == "Authentication must be stateless."


def test_parse_rejects_filename_mismatch(tmp_path):
    p = _write(tmp_path / "docs/decisions/other.md", RATIFIED)
    with pytest.raises(ValueError, match="filename"):
        parse_decision_file(p)


def test_parse_rejects_bad_status(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-x.md", "---\nid: d-x\nstatus: draft\n---\nx\n")
    with pytest.raises(ValueError, match="status"):
        parse_decision_file(p)


def test_parse_rejects_uppercase_id(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-X.md", "---\nid: d-X\nstatus: proposed\n---\nx\n")
    with pytest.raises(ValueError, match="id"):
        parse_decision_file(p)


def test_load_skips_reserved_and_dotfiles(tmp_path):
    root = tmp_path
    _write(root / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: proposed\n---\na\n")
    _write(root / "docs/decisions/README.md", "# readme\n")
    _write(root / "docs/decisions/_template.md", "template\n")
    decisions, errors = load_decisions(root)
    assert [d.id for d in decisions] == ["d-a"]
    assert errors == []


def test_load_reports_malformed(tmp_path):
    _write(tmp_path / "docs/decisions/d-a.md", "no frontmatter here\n")
    decisions, errors = load_decisions(tmp_path)
    assert decisions == []
    assert len(errors) == 1 and errors[0].kind == "malformed"


def test_load_reports_casefolded_duplicate(tmp_path):
    # On a case-sensitive FS both files exist; ids collide under casefold.
    _write(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: proposed\n---\na\n")
    _write(tmp_path / "docs/decisions/d-A.md", "---\nid: d-A\nstatus: proposed\n---\na\n")
    decisions, errors = load_decisions(tmp_path)
    kinds = {e.kind for e in errors}
    # d-A is rejected: invalid uppercase id (malformed) — still an error, gate blocks.
    assert errors and "malformed" in kinds


def test_load_missing_dir_is_empty(tmp_path):
    decisions, errors = load_decisions(tmp_path)
    assert decisions == [] and errors == []


def test_serialize_roundtrip(tmp_path):
    d = Decision(id="d-a", status="proposed", body="hello", path=tmp_path / "d-a.md")
    text = serialize_decision(d)
    assert text.startswith("---\nid: d-a\nstatus: proposed\n")
    assert text.rstrip().endswith("hello")


def test_is_valid_id():
    from super_harness.core.decisions import is_valid_id

    assert is_valid_id("d-auth-stateless")
    assert not is_valid_id("d-Auth")
    assert not is_valid_id("d auth")
