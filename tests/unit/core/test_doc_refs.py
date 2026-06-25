"""Unit tests for core.doc_refs (doc dead-reference engine)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from super_harness.core.doc_refs import (
    DEFAULT_DOC_EXCLUDE,
    DEFAULT_DOC_INCLUDE,
    collect_source_identifiers,
    extract_backtick_symbols,
    load_doc_scope,
    looks_like_symbol,
    scan_doc_refs,
)


def _harness(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root, check=True,
    )


# --- Task 1: doc-scope loader --------------------------------------------------


def test_doc_scope_defaults_when_no_config(tmp_path: Path) -> None:
    _harness(tmp_path)
    include, exclude = load_doc_scope(tmp_path)
    assert include == DEFAULT_DOC_INCLUDE
    assert exclude == DEFAULT_DOC_EXCLUDE


def test_doc_scope_override(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "doc-paths.yaml").write_text(
        "doc_paths:\n  include:\n    - 'docs/**/*.md'\n  exclude:\n    - 'docs/legacy/**'\n"
    )
    include, exclude = load_doc_scope(root)
    assert include == ["docs/**/*.md"]
    assert exclude == ["docs/legacy/**"]


def test_doc_scope_corrupt_yaml_falls_back(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / ".harness" / "doc-paths.yaml").write_text("doc_paths: [unbalanced\n")
    include, exclude = load_doc_scope(root)
    assert include == DEFAULT_DOC_INCLUDE
    assert exclude == DEFAULT_DOC_EXCLUDE


# --- Task 2: backtick extraction + code-shape heuristic ------------------------


def test_looks_like_symbol_accepts_code_shaped() -> None:
    assert looks_like_symbol("_format_rows")
    assert looks_like_symbol("derive_state")
    assert looks_like_symbol("DocRefsResult")
    assert looks_like_symbol("assembleBundle")


def test_looks_like_symbol_rejects_prose_and_non_symbols() -> None:
    assert not looks_like_symbol("ok")          # bare lowercase word
    assert not looks_like_symbol("id")
    assert not looks_like_symbol("TODO")         # all-caps, no internal boundary
    assert not looks_like_symbol("--json")       # flag
    assert not looks_like_symbol("core.reducer.fold")  # dotted -> deferred warn tier
    assert not looks_like_symbol("a/b.py")       # path
    assert not looks_like_symbol("two words")
    assert not looks_like_symbol("")


def test_extract_strips_trailing_parens_and_records_line() -> None:
    text = "intro\nsee `derive_state()` and `_format_rows` here\nplain `ok` word\n"
    found = extract_backtick_symbols(text)
    assert found == [("derive_state", 2), ("_format_rows", 2)]


# --- Task 3: source-identifier collection --------------------------------------


def test_collect_source_identifiers_reads_source_excludes_docs(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def derive_state():\n    return _format_rows\n")
    (root / "docs").mkdir()
    # an identifier that exists ONLY in docs must NOT count as present-in-source
    (root / "docs" / "x.md").write_text("`only_in_docs`\n")
    _git_init(root)

    idents = collect_source_identifiers(root, include=["**/*"], exclude=["docs/**"])
    assert "derive_state" in idents
    assert "_format_rows" in idents
    assert "only_in_docs" not in idents


# --- Task 4: scan_doc_refs orchestrator ----------------------------------------


def test_scan_doc_refs_flags_dead_backtick_ref(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (root / "docs").mkdir()
    # _render exists; _format_rows was renamed away -> dead
    (root / "docs" / "guide.md").write_text(
        "use `_render` now; old `_format_rows` is gone\n"
    )
    (root / "docs" / "plans").mkdir()
    (root / "docs" / "plans" / "old.md").write_text("`_format_rows` archived\n")
    _git_init(root)

    result = scan_doc_refs(root)
    dead = [(f.symbol, f.doc_file) for f in result.findings]
    assert ("_format_rows", "docs/guide.md") in dead
    assert all(f.symbol != "_render" for f in result.findings)
    # archival docs/plans/** is excluded by default -> not reported
    assert all("plans/" not in f.doc_file for f in result.findings)
    assert all(f.confidence == "high" for f in result.findings)


def test_scan_doc_refs_clean_when_all_resolve(tmp_path: Path) -> None:
    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("call `_render` to draw\n")
    _git_init(root)

    assert scan_doc_refs(root).findings == []


def test_scan_doc_refs_catches_top_level_md(tmp_path: Path) -> None:
    """Top-level README.md/AGENTS.md must be scanned (the `*.md` include glob)."""
    root = _harness(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("def _render():\n    return 1\n")
    (root / "README.md").write_text("legacy `_format_rows` is gone\n")
    _git_init(root)

    dead = [(f.symbol, f.doc_file) for f in scan_doc_refs(root).findings]
    assert ("_format_rows", "README.md") in dead
