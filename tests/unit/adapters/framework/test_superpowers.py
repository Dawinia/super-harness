"""Unit tests for the SuperpowersAdapter (framework adapter).

The adapter discovers superpowers design/plan artifacts by a super-harness-owned
frontmatter marker (`change:` / `stage:`), NOT by superpowers' version-specific
paths/filenames — see docs/plans/2026-06-02-superpowers-framework-adapter-design.md.
"""
from __future__ import annotations

from super_harness.adapters.framework.superpowers import _parse_frontmatter


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
