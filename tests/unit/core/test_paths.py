from pathlib import Path

import pytest

from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
    gates_yaml_path,
    lock_path,
    sensors_yaml_path,
    state_path,
    verification_results_dir,
    verification_yaml_path,
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


def test_sensors_and_gates_yaml_paths(tmp_path: Path):
    """Direct coverage for the registry-config path helpers (I-3 review fix).

    Prior to this test the helpers were only exercised indirectly via the
    Phase 3.5 CLI tests; if Phase 5/8/11/13 change the signature we now get
    a localized failure here rather than a cascade through the CLI suite.
    """
    assert sensors_yaml_path(tmp_path) == tmp_path / ".harness" / "sensors.yaml"
    assert gates_yaml_path(tmp_path) == tmp_path / ".harness" / "gates.yaml"


def test_verification_path_helpers(tmp_path: Path):
    """Phase 8 verification path helpers (verification.yaml + per-run archive)."""
    assert (
        verification_yaml_path(tmp_path)
        == tmp_path / ".harness" / "verification.yaml"
    )
    # Hyphen in the `verification-results` segment; change_id then ts nest under it.
    assert verification_results_dir(tmp_path, "my-change", "2026-05-29T00:00:00Z") == (
        tmp_path / ".harness" / "verification-results" / "my-change" / "2026-05-29T00:00:00Z"
    )
