from pathlib import Path

from super_harness.core.plan_paths import DEFAULT_PLAN_PATHS, load_plan_paths


def _write(root: Path, body: str) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "plan-paths.yaml").write_text(body, encoding="utf-8")


def test_missing_file_uses_builtin_default(tmp_path):
    (tmp_path / ".harness").mkdir()
    assert load_plan_paths(tmp_path) == list(DEFAULT_PLAN_PATHS)


def test_valid_patterns_are_returned(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "specs/{slug}/design.md"\n')
    assert load_plan_paths(tmp_path) == ["specs/{slug}/design.md"]


def test_pattern_without_slug_placeholder_is_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "AGENTS.md"\n  - "docs/{slug}.md"\n')
    assert load_plan_paths(tmp_path) == ["docs/{slug}.md"]


def test_pattern_not_ending_in_md_is_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "src/{slug}/**"\n')
    assert load_plan_paths(tmp_path) == []


def test_absolute_and_traversal_patterns_are_dropped(tmp_path):
    _write(
        tmp_path,
        'version: 1\nplan_paths:\n  - "/etc/{slug}.md"\n  - "../{slug}.md"\n',
    )
    assert load_plan_paths(tmp_path) == []


def test_corrupt_yaml_fails_closed_to_empty(tmp_path):
    _write(tmp_path, "plan_paths: [unclosed\n")
    assert load_plan_paths(tmp_path) == []


def test_non_list_value_fails_closed_to_empty(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths: "docs/{slug}.md"\n')
    assert load_plan_paths(tmp_path) == []


def test_non_string_entries_are_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - 42\n  - "docs/{slug}.md"\n')
    assert load_plan_paths(tmp_path) == ["docs/{slug}.md"]


def test_never_raises_on_unreadable_file(tmp_path, monkeypatch):
    _write(tmp_path, 'version: 1\nplan_paths: ["docs/{slug}.md"]\n')
    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    assert load_plan_paths(tmp_path) == []
