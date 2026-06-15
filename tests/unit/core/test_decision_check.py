# tests/unit/core/test_decision_check.py
from pathlib import Path

from super_harness.core.decision_check import run_check


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _ratified(root: Path, did: str) -> None:
    _w(root / f"docs/decisions/{did}.md", f"---\nid: {did}\nstatus: ratified\n---\nx\n")


def test_clean_repo(tmp_path):
    _ratified(tmp_path, "d-a")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert r.dangling_up == [] and r.dangling_down == [] and r.errors == []
    assert r.ok is True


def test_dangling_up_anchor_no_decision(tmp_path):
    _w(tmp_path / "src/a.py", "# @decision:d-ghost\n")
    r = run_check(tmp_path)
    assert [(d.id, d.file, d.line) for d in r.dangling_up] == [("d-ghost", "src/a.py", 1)]
    assert r.ok is False


def test_anchor_to_proposed_is_dangling_up(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: proposed\n---\nx\n")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert [d.id for d in r.dangling_up] == ["d-a"]


def test_dangling_down_ratified_no_anchor(tmp_path):
    _ratified(tmp_path, "d-a")
    r = run_check(tmp_path)
    assert r.dangling_down == ["d-a"]
    assert r.dangling_up == [] and r.errors == []
    assert r.ok is True  # down is warn-only → ok stays True


def test_superseded_not_counted_down(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: superseded\n---\nx\n")
    r = run_check(tmp_path)
    assert r.dangling_down == []


def test_anchor_to_superseded_is_dangling_up(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "---\nid: d-a\nstatus: superseded\n---\nx\n")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert [d.id for d in r.dangling_up] == ["d-a"]  # leftover anchor blocks


def test_errors_surface(tmp_path):
    _w(tmp_path / "docs/decisions/d-a.md", "no frontmatter\n")
    r = run_check(tmp_path)
    assert len(r.errors) == 1 and r.ok is False
    assert r.dangling_up == [] and r.dangling_down == []


def test_docs_decisions_never_self_match(tmp_path):
    # A record mentioning its own anchor in prose must NOT count as an anchor.
    _w(tmp_path / "docs/decisions/d-a.md",
       "---\nid: d-a\nstatus: ratified\n---\nuse @decision:d-a in code\n")
    r = run_check(tmp_path)
    # no source anchor exists → d-a is dangling-down, NOT satisfied by its own prose
    assert r.dangling_down == ["d-a"] and r.dangling_up == []


def test_dangling_up_sorted(tmp_path):
    _w(tmp_path / "src/z.py", "# @decision:d-b\n")
    _w(tmp_path / "src/a.py", "# @decision:d-a\n")
    r = run_check(tmp_path)
    assert [d.id for d in r.dangling_up] == ["d-a", "d-b"]


def test_tampered_body_is_integrity_violation(tmp_path):
    body = "Passwords never stored with MD5."
    _w(tmp_path / "docs/decisions/d-pw.md",
       f"---\nid: d-pw\nstatus: ratified\nratified_by: a@b.com\n"
       f"ratified_text_hash: sha256:deadbeef\n---\n{body}\n")
    res = run_check(tmp_path)
    assert [v.id for v in res.integrity_violations] == ["d-pw"]
    assert res.ok is False


def test_matching_hash_is_clean(tmp_path):
    from super_harness.core.decisions import compute_body_hash
    body = "Passwords never stored with MD5."
    _w(tmp_path / "docs/decisions/d-pw.md",
       f"---\nid: d-pw\nstatus: ratified\nratified_by: a@b.com\n"
       f"ratified_text_hash: {compute_body_hash(body)}\n---\n{body}\n")
    res = run_check(tmp_path)
    assert res.integrity_violations == []
    assert res.ok is True


def test_violated_decision_drops_from_effective_ratified(tmp_path):
    body = "Claim X."
    _w(tmp_path / "docs/decisions/d-x.md",
       f"---\nid: d-x\nstatus: ratified\nratified_by: a@b.com\n"
       f"ratified_text_hash: sha256:deadbeef\n---\n{body}\n")
    _w(tmp_path / "src/m.py", "# @decision:d-x\nx = 1\n")
    res = run_check(tmp_path)
    assert any(d.id == "d-x" for d in res.dangling_up)  # anchor now dangles up


def test_superseded_stale_hash_is_ignored(tmp_path):
    _w(tmp_path / "docs/decisions/d-old.md",
       "---\nid: d-old\nstatus: superseded\nsuperseded_by: d-new\n"
       "ratified_text_hash: sha256:deadbeef\n---\nstale body.\n")
    _w(tmp_path / "docs/decisions/d-new.md",
       "---\nid: d-new\nstatus: proposed\n---\nx\n")
    res = run_check(tmp_path)
    assert res.integrity_violations == []


def test_violated_and_unanchored_shows_both(tmp_path):
    _w(tmp_path / "docs/decisions/d-x.md",
       "---\nid: d-x\nstatus: ratified\nratified_by: a@b.com\n"
       "ratified_text_hash: sha256:deadbeef\n---\nbody.\n")
    res = run_check(tmp_path)
    assert [v.id for v in res.integrity_violations] == ["d-x"]
    assert "d-x" in res.dangling_down


def test_ratified_without_hash_warns_not_blocks(tmp_path):
    _w(tmp_path / "docs/decisions/d-old.md",
       "---\nid: d-old\nstatus: ratified\nratified_by: a@b.com\n---\nlegacy.\n")
    res = run_check(tmp_path)
    assert res.unhashed_ratified == ["d-old"]
    assert res.integrity_violations == []
    assert res.ok is True   # warn-only, must NOT block
