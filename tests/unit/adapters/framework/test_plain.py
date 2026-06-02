from __future__ import annotations

from pathlib import Path

from super_harness.adapters.framework.plain import PlainAdapter


def test_detect_always_returns_false(tmp_path: Path) -> None:
    assert PlainAdapter().detect(tmp_path) is False


def test_detect_returns_false_for_arbitrary_path() -> None:
    assert PlainAdapter().detect(Path("/nonexistent/workspace")) is False


def test_spec_paths_empty_no_spec_concept() -> None:
    # Plain framework has no spec/plan concept → both empty, no error.
    assert PlainAdapter().spec_paths(Path("/nonexistent"), "any-change") == {
        "spec": "",
        "plan": "",
    }


def test_observe_yields_nothing(tmp_path: Path) -> None:
    assert list(PlainAdapter().observe(tmp_path)) == []


def test_get_state_returns_none() -> None:
    assert PlainAdapter().get_state("some-change-id") is None


def test_verification_checks_returns_empty_list() -> None:
    assert PlainAdapter().verification_checks() == []


def test_is_fallback_is_true() -> None:
    assert PlainAdapter.is_fallback is True
    assert PlainAdapter().is_fallback is True


def test_agents_md_subsection_contains_opening_marker() -> None:
    section = PlainAdapter().agents_md_subsection()
    assert "<!-- super-harness framework: plain -->" in section


def test_agents_md_subsection_contains_closing_marker() -> None:
    section = PlainAdapter().agents_md_subsection()
    assert "<!-- /super-harness framework: plain -->" in section


def test_agents_md_subsection_opening_before_closing() -> None:
    section = PlainAdapter().agents_md_subsection()
    open_pos = section.index("<!-- super-harness framework: plain -->")
    close_pos = section.index("<!-- /super-harness framework: plain -->")
    assert open_pos < close_pos


def test_name_and_version() -> None:
    assert PlainAdapter.name == "plain"
    assert PlainAdapter.version == "0.1.0"


def test_on_uninstall_default_noop(tmp_path: Path) -> None:
    assert PlainAdapter().on_uninstall(tmp_path) is None


def test_watch_paths_inherits_empty_default(tmp_path: Path) -> None:
    # PlainAdapter does not override watch_paths -> inherits the ABC's [] default.
    assert PlainAdapter().watch_paths(tmp_path) == []
