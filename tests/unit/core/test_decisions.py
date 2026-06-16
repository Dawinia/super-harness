from pathlib import Path

import pytest

from super_harness.core.decisions import (
    Decision,
    compute_body_hash,
    load_decisions,
    normalize_body,
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


def test_load_reports_casefolded_collision(tmp_path):
    # case-sensitive FS: both exist -> duplicate_id. case-insensitive FS: second
    # write overwrites first (stem != id) -> malformed. Either way: collision
    # suppressed (no valid decision) and an error reported.
    _write(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: proposed\n---\na\n")
    _write(tmp_path / "docs/decisions/d-A.md", "---\nid: d-A\nstatus: proposed\n---\na\n")
    decisions, errors = load_decisions(tmp_path)
    assert decisions == [] and errors


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


def test_parse_reads_ratified_text_hash(tmp_path):
    text = (
        "---\nid: d-x\nstatus: ratified\nratified_by: a@b.com\n"
        "ratified_at: 2026-06-08T12:00:00Z\n"
        "ratified_text_hash: sha256:abc123\n---\nbody.\n"
    )
    p = _write(tmp_path / "docs/decisions/d-x.md", text)
    d = parse_decision_file(p)
    assert d.ratified_text_hash == "sha256:abc123"


def test_serialize_round_trips_hash(tmp_path):
    d = Decision(
        id="d-x", status="ratified", ratified_by="a@b.com",
        ratified_at="2026-06-08T12:00:00Z", ratified_text_hash="sha256:abc123",
        body="body.",
    )
    out = serialize_decision(d)
    assert "ratified_text_hash: sha256:abc123" in out


def test_parse_missing_hash_is_none(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-y.md",
               "---\nid: d-y\nstatus: ratified\nratified_by: a@b.com\n---\nb\n")
    assert parse_decision_file(p).ratified_text_hash is None


def test_normalize_collapses_only_whitespace_noise():
    a = "line one  \r\nline two\n"      # CRLF + trailing spaces + trailing newline
    b = "\n\nline one\nline two"        # leading blank lines, no trailing
    assert normalize_body(a) == normalize_body(b) == "line one\nline two"


def test_hash_is_stable_and_prefixed():
    h = compute_body_hash("hello")
    assert h.startswith("sha256:")
    assert h == compute_body_hash("hello\n")  # trailing newline is noise


def test_hash_changes_on_wording():
    # punctuation/wording is NOT normalized away — it must move the hash
    assert compute_body_hash("never MD5.") != compute_body_hash("prefer bcrypt.")


BODY = (
    "Passwords must be stored with bcrypt - never MD5.\n\n"
    "```check\n! grep -rIn \"md5(.*password\" src/\n```\n\n"
    "```counterexample path=src/auth/legacy.py\npw = md5(user.password)\n```\n"
)


def test_parse_check_extracts_command():
    from super_harness.core.decisions import parse_check
    assert parse_check(BODY) == '! grep -rIn "md5(.*password" src/'


def test_parse_counterexample_extracts_path_and_content():
    from super_harness.core.decisions import Counterexample, parse_counterexample
    ce = parse_counterexample(BODY)
    assert ce == Counterexample(path="src/auth/legacy.py", content="pw = md5(user.password)")


def test_no_blocks_returns_none():
    from super_harness.core.decisions import parse_check, parse_counterexample
    assert parse_check("just prose, tier-3 context.") is None
    assert parse_counterexample("just prose.") is None


def test_more_than_one_check_block_raises():
    from super_harness.core.decisions import parse_check
    two = "```check\na\n```\n```check\nb\n```\n"
    with pytest.raises(ValueError, match="at most one"):
        parse_check(two)


def test_counterexample_requires_path():
    from super_harness.core.decisions import parse_counterexample
    with pytest.raises(ValueError, match="path="):
        parse_counterexample("```counterexample\npw = bad\n```\n")


def test_indented_fence_is_not_a_check():
    # CommonMark allows indented fences; this parser intentionally does not -> tier-3.
    from super_harness.core.decisions import parse_check
    assert parse_check("   ```check\n! grep x\n   ```\n") is None


def test_decision_file_carries_parsed_check(tmp_path):
    p = _write(tmp_path / "docs/decisions/d-pw.md",
               f"---\nid: d-pw\nstatus: proposed\n---\n{BODY}")
    d = parse_decision_file(p)
    assert d.check == '! grep -rIn "md5(.*password" src/'
    assert d.counterexample.path == "src/auth/legacy.py"
