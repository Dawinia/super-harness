"""Unit tests for the SuperpowersAdapter (framework adapter).

The adapter discovers superpowers design/plan artifacts by a super-harness-owned
frontmatter marker (`change:` / `stage:`), NOT by superpowers' version-specific
paths/filenames — see docs/plans/2026-06-02-superpowers-framework-adapter-design.md.
"""
from __future__ import annotations

from pathlib import Path

from super_harness.adapters.framework.superpowers import (
    SuperpowersAdapter,
    _parse_frontmatter,
)


def _write(workspace: Path, rel: str, body: str) -> Path:
    """Write `body` to `workspace/rel`, creating parents. Return the path."""
    p = workspace / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _marked(change: str, *, stage: str | None = None, extra: str = "") -> str:
    """A minimal artifact body carrying the `change:` (and optional `stage:`) marker."""
    fm = f"change: {change}\n"
    if stage is not None:
        fm += f"stage: {stage}\n"
    fm += extra
    return f"---\n{fm}---\n# {change}\n"


class TestParseFrontmatter:
    def test_leading_block_parsed_as_mapping(self) -> None:
        text = "---\nchange: foo\nstage: plan\n---\n# Body\n"
        assert _parse_frontmatter(text) == {"change": "foo", "stage": "plan"}

    def test_no_frontmatter_returns_empty(self) -> None:
        assert _parse_frontmatter("# Just a heading\n\nprose\n") == {}

    def test_malformed_yaml_returns_empty_no_raise(self) -> None:
        # Unterminated flow mapping inside the block → YAMLError → {}.
        assert _parse_frontmatter("---\nchange: {unterminated\n---\n") == {}

    def test_non_mapping_frontmatter_returns_empty(self) -> None:
        # A YAML list as frontmatter is not a mapping → {}.
        assert _parse_frontmatter("---\n- a\n- b\n---\n") == {}

    def test_unclosed_block_returns_empty(self) -> None:
        # Opening `---` with no closing fence → treat as no frontmatter.
        assert _parse_frontmatter("---\nchange: foo\nno closing fence\n") == {}


class TestDetect:
    def test_true_for_marked_doc_in_docs_plans(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/2026-06-02-foo.md", _marked("foo"))
        assert SuperpowersAdapter().detect(tmp_path) is True

    def test_true_for_marked_doc_in_legacy_superpowers_dir(self, tmp_path: Path) -> None:
        # Older superpowers layout — still found, because the marker (not the
        # path) is the anchor.
        _write(tmp_path, "docs/superpowers/specs/foo.md", _marked("foo"))
        assert SuperpowersAdapter().detect(tmp_path) is True

    def test_false_when_md_has_no_change_marker(self, tmp_path: Path) -> None:
        _write(tmp_path, "docs/plans/notes.md", "# notes\n\nno marker here\n")
        assert SuperpowersAdapter().detect(tmp_path) is False

    def test_false_when_no_candidate_dirs(self, tmp_path: Path) -> None:
        assert SuperpowersAdapter().detect(tmp_path) is False
