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
