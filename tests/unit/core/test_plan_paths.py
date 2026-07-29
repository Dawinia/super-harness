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


def test_deeply_nested_yaml_fails_closed_instead_of_raising(tmp_path):
    # yaml.safe_load raises RecursionError (an Exception subclass, not caught by the
    # earlier narrow tuple) on deeply nested structures. This must not escape
    # load_plan_paths — the PreToolUse hook treats an uncaught exception as
    # non-blocking, i.e. fail-OPEN, which is the opposite of this module's contract.
    depth = 600
    body = "plan_paths: " + "[" * depth + "]" * depth + "\n"
    _write(tmp_path, body)
    assert load_plan_paths(tmp_path) == []


def test_windows_drive_letter_pattern_is_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "C:/Windows/{slug}.md"\n')
    assert load_plan_paths(tmp_path) == []


def test_windows_backslash_pattern_is_dropped(tmp_path):
    _write(tmp_path, 'version: 1\nplan_paths:\n  - "\\\\server\\\\share\\\\{slug}.md"\n')
    assert load_plan_paths(tmp_path) == []


def test_default_plan_paths_matches_the_shipped_skeleton() -> None:
    """The built-in default and what `init` writes must not drift apart.

    A repo initialized before this constant existed has no `plan-paths.yaml` and
    falls through to `DEFAULT_PLAN_PATHS`. If the default were narrower than the
    skeleton, every existing adopter would silently get a subset of the coverage
    the docs promise, with nothing telling them to create the file.
    """
    import yaml

    from super_harness.cli.init import _skeleton_files

    shipped = yaml.safe_load(_skeleton_files()["plan-paths.yaml"])["plan_paths"]
    assert list(DEFAULT_PLAN_PATHS) == shipped
