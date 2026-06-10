from super_harness.core.source_scope import (
    DEFAULT_EXCLUDE,
    DEFAULT_INCLUDE,
    load_source_scope,
)


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_missing_file_returns_defaults(tmp_path):
    inc, exc = load_source_scope(tmp_path)
    assert inc == DEFAULT_INCLUDE and exc == DEFAULT_EXCLUDE


def test_reads_nested_source_paths_key(tmp_path):
    _write(
        tmp_path / ".harness/source-paths.yaml",
        "source_paths:\n  include:\n    - 'src/**'\n  exclude:\n    - 'src/vendor/**'\n",
    )
    inc, exc = load_source_scope(tmp_path)
    assert inc == ["src/**"] and exc == ["src/vendor/**"]


def test_corrupt_yaml_falls_back_to_defaults(tmp_path):
    _write(tmp_path / ".harness/source-paths.yaml", "source_paths: [::: bad")
    inc, exc = load_source_scope(tmp_path)
    assert inc == DEFAULT_INCLUDE and exc == DEFAULT_EXCLUDE


def test_missing_keys_fall_back(tmp_path):
    _write(tmp_path / ".harness/source-paths.yaml", "source_paths: {}\n")
    inc, exc = load_source_scope(tmp_path)
    assert inc == DEFAULT_INCLUDE and exc == DEFAULT_EXCLUDE


def test_non_list_value_falls_back(tmp_path):
    _write(
        tmp_path / ".harness/source-paths.yaml",
        "source_paths:\n  include: 'src/**'\n",
    )
    inc, exc = load_source_scope(tmp_path)
    assert inc == DEFAULT_INCLUDE and exc == DEFAULT_EXCLUDE
