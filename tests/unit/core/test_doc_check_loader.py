from pathlib import Path

from super_harness.core.doc_check import DerivedDoc, load_derived_docs


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _reg(root: Path, body: str) -> None:
    _w(root / ".harness/derived-docs.yaml", body)


def test_absent_file_is_clean_not_error(tmp_path):
    docs, errors = load_derived_docs(tmp_path)
    assert docs == [] and errors == []


def test_valid_registry_parses(tmp_path):
    _reg(tmp_path, "derived_docs:\n"
                   "  - path: docs/a.md\n    command: echo hi\n"
                   "  - path: docs/b.md\n    command: python -m x --emit\n")
    docs, errors = load_derived_docs(tmp_path)
    assert errors == []
    assert docs == [DerivedDoc(path="docs/a.md", command="echo hi"),
                    DerivedDoc(path="docs/b.md", command="python -m x --emit")]


def test_unparseable_yaml_is_malformed(tmp_path):
    _reg(tmp_path, "derived_docs: [unclosed\n")
    docs, errors = load_derived_docs(tmp_path)
    assert docs == [] and [e.code for e in errors] == ["malformed_registry"]


def test_top_not_mapping_is_malformed(tmp_path):
    _reg(tmp_path, "- just\n- a\n- list\n")
    _, errors = load_derived_docs(tmp_path)
    assert [e.code for e in errors] == ["malformed_registry"]


def test_derived_docs_not_a_list_is_malformed(tmp_path):
    _reg(tmp_path, "derived_docs: 7\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_entry_not_mapping_is_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - just-a-string\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_missing_or_nonstring_keys_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: docs/a.md\n")  # no command
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_empty_command_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: docs/a.md\n    command: '   '\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_absolute_path_is_escape(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: /etc/x.md\n    command: echo hi\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["path_escape"]


def test_dotdot_escape(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: ../x.md\n    command: echo hi\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["path_escape"]


def test_duplicate_path_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n"
                   "  - path: docs/a.md\n    command: echo 1\n"
                   "  - path: docs/a.md\n    command: echo 2\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["duplicate_path"]


def test_duplicate_path_after_normalization(tmp_path):
    _reg(tmp_path, "derived_docs:\n"
                   "  - path: docs/a.md\n    command: echo 1\n"
                   "  - path: ./docs/a.md\n    command: echo 2\n")
    docs, errors = load_derived_docs(tmp_path)
    assert [d.path for d in docs] == ["docs/a.md"]
    assert [e.code for e in errors] == ["duplicate_path"]


def test_empty_path_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: ''\n    command: echo hi\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_whitespace_path_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: '   '\n    command: echo hi\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_dot_path_resolves_to_root_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: '.'\n    command: echo hi\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_mixed_valid_and_invalid_aggregates(tmp_path):
    _reg(tmp_path, "derived_docs:\n"
                   "  - path: docs/good.md\n    command: echo ok\n"
                   "  - path: /abs.md\n    command: echo bad\n"
                   "  - path: docs/good.md\n    command: echo dup\n")
    docs, errors = load_derived_docs(tmp_path)
    assert [d.path for d in docs] == ["docs/good.md"]
    assert [e.code for e in errors] == ["path_escape", "duplicate_path"]
