# tests/unit/core/test_decision_check.py
from pathlib import Path

import yaml

from super_harness.core.decision_check import fingerprint_file, run_check
from super_harness.core.decisions import compute_body_hash


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


# --- tier-2 reviewable-anchor suspect invariant ----------------------------

def _write_tier2(ws: Path, *, reconciled_anchors=None, status="ratified",
                 anchor_src="src/x.py") -> None:
    """Emit a well-formed ratified tier-2 record (``review`` block, valid
    body hash) + anchor a source file with ``# @decision:d-t2``."""
    body = "```review\nthe author must hand-review this.\n```"
    fm: dict[str, object] = {"id": "d-t2", "status": status,
                             "ratified_text_hash": compute_body_hash(body)}
    if reconciled_anchors is not None:
        fm["reconciled_anchors"] = dict(reconciled_anchors)
    fm_text = yaml.safe_dump(fm, sort_keys=False).strip()
    _w(ws / "docs/decisions/d-t2.md", f"---\n{fm_text}\n---\n{body}\n")
    _w(ws / anchor_src, "# @decision:d-t2\nx = 1\n")


def test_tier2_unreconciled_when_anchored_but_no_baseline(tmp_path):
    _write_tier2(tmp_path)  # NO reconciled_anchors
    res = run_check(tmp_path)
    assert "d-t2" in res.unreconciled_tier2
    assert not res.suspect_tier2


def test_tier2_clean_when_baseline_matches(tmp_path):
    _write_tier2(tmp_path, reconciled_anchors={})  # placeholder, fixed below
    baseline = {"src/x.py": fingerprint_file(tmp_path, "src/x.py")}
    _write_tier2(tmp_path, reconciled_anchors=baseline)
    res = run_check(tmp_path)
    assert not res.suspect_tier2 and not res.unreconciled_tier2


def test_tier2_suspect_when_anchored_file_changes(tmp_path):
    _write_tier2(tmp_path)
    baseline = {"src/x.py": fingerprint_file(tmp_path, "src/x.py")}
    _write_tier2(tmp_path, reconciled_anchors=baseline)
    # mutate the anchored file AFTER recording the baseline
    _w(tmp_path / "src/x.py", "# @decision:d-t2\nx = 1\ny = 2\n")
    res = run_check(tmp_path)
    assert [s.id for s in res.suspect_tier2] == ["d-t2"]
    assert "src/x.py" in res.suspect_tier2[0].changed_files


def test_tier2_suspect_on_new_anchor_not_in_baseline(tmp_path):
    _write_tier2(tmp_path)
    baseline = {"src/x.py": fingerprint_file(tmp_path, "src/x.py")}
    _write_tier2(tmp_path, reconciled_anchors=baseline)
    # a second anchored file absent from the baseline
    _w(tmp_path / "src/y.py", "# @decision:d-t2\nz = 3\n")
    res = run_check(tmp_path)
    assert "src/y.py" in res.suspect_tier2[0].changed_files


def test_retired_tier2_never_suspect(tmp_path):
    _write_tier2(tmp_path, status="retired")
    baseline = {"src/x.py": fingerprint_file(tmp_path, "src/x.py")}
    _write_tier2(tmp_path, reconciled_anchors=baseline, status="retired")
    _w(tmp_path / "src/x.py", "# @decision:d-t2\nx = 1\ny = 2\n")
    res = run_check(tmp_path)
    assert not res.suspect_tier2 and not res.unreconciled_tier2


def test_tier1_to_tier2_body_edit_is_integrity_violation(tmp_path):
    # A ratified tier-1 (```check) decision whose body is edited to drop ```check
    # and add ```review changes the body hash. Without re-ratify, run_check must
    # flag the body-hash mismatch as an integrity violation (the tier flip cannot
    # silently bypass the ratified claim).
    tier1_body = "```check\ntrue\n```"
    _w(tmp_path / "docs/decisions/d-flip.md",
       f"---\nid: d-flip\nstatus: ratified\nratified_by: a@b.com\n"
       f"ratified_text_hash: {compute_body_hash(tier1_body)}\n---\n{tier1_body}\n")
    res = run_check(tmp_path)
    assert all(v.id != "d-flip" for v in res.integrity_violations)  # clean first
    # rewrite the FILE body to a ```review block WITHOUT updating ratified_text_hash
    _w(tmp_path / "docs/decisions/d-flip.md",
       f"---\nid: d-flip\nstatus: ratified\nratified_by: a@b.com\n"
       f"ratified_text_hash: {compute_body_hash(tier1_body)}\n---\n"
       f"```review\nthe author must hand-review this.\n```\n")
    res = run_check(tmp_path)
    assert any(v.id == "d-flip" for v in res.integrity_violations)


def test_tier1_and_tier3_untouched_by_suspect_logic(tmp_path):
    # tier-1: ```check block
    _w(tmp_path / "docs/decisions/d-c.md",
       "---\nid: d-c\nstatus: ratified\n---\n```check\ntrue\n```\n")
    _w(tmp_path / "src/c.py", "# @decision:d-c\n")
    # tier-3: no fenced block at all
    _ratified(tmp_path, "d-ctx")
    _w(tmp_path / "src/ctx.py", "# @decision:d-ctx\n")
    res = run_check(tmp_path)
    assert not res.suspect_tier2 and not res.unreconciled_tier2
