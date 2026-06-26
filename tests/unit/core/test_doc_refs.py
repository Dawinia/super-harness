"""Unit tests for core.doc_refs (doc dead-reference engine)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from super_harness.core.doc_refs import (
    _TOKEN_RE,
    DEFAULT_DOC_EXCLUDE,
    DEFAULT_DOC_INCLUDE,
    collect_source_identifiers,
    extract_backtick_symbols,
    load_doc_scope,
    looks_like_symbol,
    scan_doc_refs,
)
from super_harness.core.language_profile import IDENTIFIER_PATTERN_DEFAULT


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


def test_default_tokenizer_equals_old_token_re():
    """Derived default token_re must be byte-for-byte equivalent to the old
    \\b...\\b, incl @/$/?/! adjacency and digit prefixes (design §3.3/§4)."""
    token_re = re.compile(rf"(?<!\w){IDENTIFIER_PATTERN_DEFAULT}")
    for s in ["@property", "$element jQuery", "a?b:c", "foo!bar", "123abc",
              "var2name x", "self.method_name", "addNumbers PaymentProcessor",
              "@decorator\ndef f", "__init__"]:
        assert token_re.findall(s) == _TOKEN_RE.findall(s), s


def test_decoration_signal_under_ruby_pattern():
    ruby = re.compile(r"^[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?$")
    assert looks_like_symbol("valid?", ident_re=ruby) is True
    assert looks_like_symbol("charge!", ident_re=ruby) is True
    assert looks_like_symbol("total_amount", ident_re=ruby) is True
    assert looks_like_symbol("note", ident_re=ruby) is False


def test_default_decoration_unchanged():
    assert looks_like_symbol("addNumbers") is True
    assert looks_like_symbol("snake_case") is True
    assert looks_like_symbol("note") is False
    assert looks_like_symbol("valid?") is False


def test_extract_backtick_symbols_accepts_ident_re():
    ruby = re.compile(r"^[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?$")
    out = extract_backtick_symbols("call `valid?` then `note`.", ident_re=ruby)
    assert ("valid?", 1) in out
    assert all(sym != "note" for sym, _ in out)


def test_collect_source_identifiers_accepts_token_re(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.rb").write_text("def valid?\nend\n", encoding="utf-8")
    ruby_tok = re.compile(r"(?<!\w)[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?")
    idents = collect_source_identifiers(
        tmp_path, include=["**/*"], exclude=["docs/**"], token_re=ruby_tok
    )
    assert "valid?" in idents
    default_idents = collect_source_identifiers(tmp_path, include=["**/*"], exclude=["docs/**"])
    assert "valid?" not in default_idents and "valid" in default_idents


def _ruby_workspace(root):
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "account.rb").write_text(
        "class Account\n"
        "  def valid?\n    @balance >= 0\n  end\n"
        "  def total_amount\n    @balance\n  end\n"
        "  def charge!(cents)\n    @balance -= cents\n  end\n"
        "end\n",
        encoding="utf-8",
    )
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "guide.md").write_text(
        "# Guide\n\nUse `total_amount`. Check `valid?` then `charge!`.\n"
        "Legacy: `removed_helper` and `deleted?` are gone.\n",
        encoding="utf-8",
    )
    (root / ".harness").mkdir(parents=True, exist_ok=True)


def test_ruby_default_pattern_misses_suffix_dead_ref(tmp_path):
    """Documents TODAY's gap: with the C-family default, a `?`-suffix dead ref is
    invisible; only the snake_case dead ref `removed_helper` is flagged."""
    _ruby_workspace(tmp_path)
    flagged = {f.symbol for f in scan_doc_refs(tmp_path).findings}
    assert "removed_helper" in flagged
    assert "deleted?" not in flagged


def test_ruby_pattern_flags_suffix_dead_ref_and_resolves_live(tmp_path):
    """With a Ruby identifier_pattern: `deleted?` (dead) is flagged; `valid?` /
    `charge!` / `total_amount` (live) resolve; no false positive."""
    _ruby_workspace(tmp_path)
    (tmp_path / ".harness" / "language.yaml").write_text(
        "doc_refs:\n  identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'\n",
        encoding="utf-8",
    )
    flagged = {f.symbol for f in scan_doc_refs(tmp_path).findings}
    assert "deleted?" in flagged
    assert "removed_helper" in flagged
    assert "valid?" not in flagged
    assert "charge!" not in flagged
    assert "total_amount" not in flagged
