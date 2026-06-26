from pathlib import Path

from super_harness.core.language_profile import (
    IDENTIFIER_PATTERN_DEFAULT,
    load_identifier_pattern,
)


def _write(root: Path, text: str) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "language.yaml").write_text(text, encoding="utf-8")


def test_absent_file_returns_default(tmp_path):
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_valid_ruby_pattern_returned(tmp_path):
    _write(tmp_path, "doc_refs:\n  identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'\n")
    assert load_identifier_pattern(tmp_path) == r"[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?"


def test_corrupt_yaml_returns_default(tmp_path):
    _write(tmp_path, "doc_refs: [this is: not: valid")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_non_dict_top_level_returns_default(tmp_path):
    _write(tmp_path, "- just\n- a\n- list\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_missing_key_returns_default(tmp_path):
    _write(tmp_path, "doc_refs:\n  something_else: 1\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_empty_string_pattern_returns_default(tmp_path):
    _write(tmp_path, "doc_refs:\n  identifier_pattern: ''\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_invalid_regex_returns_default(tmp_path):
    _write(tmp_path, "doc_refs:\n  identifier_pattern: '[unterminated'\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_default_is_c_family():
    assert IDENTIFIER_PATTERN_DEFAULT == r"[A-Za-z_][A-Za-z0-9_]*"
