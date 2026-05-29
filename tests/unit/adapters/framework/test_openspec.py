from __future__ import annotations

from pathlib import Path

from super_harness.adapters.framework.openspec import OpenSpecAdapter


def test_detect_returns_true_when_both_dirs_exist(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    assert OpenSpecAdapter().detect(tmp_path) is True


def test_detect_returns_false_when_changes_missing(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "specs").mkdir(parents=True)
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_specs_missing(tmp_path: Path) -> None:
    (tmp_path / "openspec" / "changes").mkdir(parents=True)
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_both_missing(tmp_path: Path) -> None:
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_changes_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "openspec").mkdir(parents=True)
    (tmp_path / "openspec" / "changes").write_text("not a dir")
    (tmp_path / "openspec" / "specs").mkdir()
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_when_specs_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "openspec").mkdir(parents=True)
    (tmp_path / "openspec" / "changes").mkdir()
    (tmp_path / "openspec" / "specs").write_text("not a dir")
    assert OpenSpecAdapter().detect(tmp_path) is False


def test_detect_returns_false_for_nonexistent_workspace() -> None:
    assert OpenSpecAdapter().detect(Path("/nonexistent/workspace")) is False


def test_name_and_version() -> None:
    assert OpenSpecAdapter.name == "openspec"
    assert OpenSpecAdapter.version == "0.1.0"


def test_is_fallback_is_false() -> None:
    assert OpenSpecAdapter.is_fallback is False
    assert OpenSpecAdapter().is_fallback is False


def test_observe_yields_nothing(tmp_path: Path) -> None:
    assert list(OpenSpecAdapter().observe(tmp_path)) == []


def test_get_state_returns_none() -> None:
    assert OpenSpecAdapter().get_state("some-change-id") is None


def test_verification_checks_returns_empty_list() -> None:
    assert OpenSpecAdapter().verification_checks() == []


def test_agents_md_subsection_returns_string() -> None:
    assert isinstance(OpenSpecAdapter().agents_md_subsection(), str)


def test_on_uninstall_default_noop(tmp_path: Path) -> None:
    assert OpenSpecAdapter().on_uninstall(tmp_path) is None
