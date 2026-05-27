from pathlib import Path

import pytest

from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
    lock_path,
    state_path,
)


def test_find_harness_root_walks_up(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_harness_root(nested) == tmp_path


def test_find_harness_root_returns_immediate_if_at_root(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    assert find_harness_root(tmp_path) == tmp_path


def test_find_harness_root_raises_when_missing(tmp_path: Path):
    with pytest.raises(HarnessNotInitialized) as exc:
        find_harness_root(tmp_path)
    assert ".harness" in str(exc.value)


def test_path_helpers(tmp_path: Path):
    assert events_path(tmp_path) == tmp_path / ".harness" / "events.jsonl"
    assert state_path(tmp_path) == tmp_path / ".harness" / "state.yaml"
    assert lock_path(tmp_path, "state") == tmp_path / ".harness" / ".state.lock"
