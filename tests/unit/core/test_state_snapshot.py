# tests/unit/core/test_state_snapshot.py
"""Unit tests for load_state_snapshot — the single I/O seam for the in-process gate.

Consolidates the three historical defensive parses into ONE parse returning the
active change's reconstructed ChangeState. Every permissive branch (missing /
corrupt / non-mapping / unhashable field / no non-terminal change / unknown
override) resolves to state=None so the pure gate ALLOWs. The loader NEVER
raises and NEVER write-opens state.yaml.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from super_harness.core.state import ChangeState
from super_harness.core.state_snapshot import StateSnapshot, load_state_snapshot


def _write_state(root: Path, body: str) -> Path:
    harness = root / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_text(body, encoding="utf-8")
    return root


def test_no_state_file_is_empty_snapshot(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    assert load_state_snapshot(tmp_path) == StateSnapshot(change_id=None, state=None)


def test_recency_picks_most_recent_non_terminal(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n"
        "  old:\n    change_id: old\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n"
        "  new:\n    change_id: new\n    current_state: AWAITING_CODE_REVIEW\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path)
    assert snap.change_id == "new"
    assert isinstance(snap.state, ChangeState)
    assert snap.state.current_state == "AWAITING_CODE_REVIEW"


def test_override_selects_named_change(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n"
        "  a:\n    change_id: a\n    current_state: READY_TO_MERGE\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n"
        "  b:\n    change_id: b\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path, change_id_override="b")
    assert snap.change_id == "b"
    assert snap.state is not None and snap.state.current_state == "PLAN_APPROVED"


def test_override_names_unknown_change_yields_no_state(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path, change_id_override="ghost")
    assert snap.change_id == "ghost"
    assert snap.state is None


def test_all_terminal_yields_no_active(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n  done:\n    change_id: done\n    current_state: ARCHIVED\n"
        "    last_event_at: '2026-07-02T00:00:00Z'\n",
    )
    assert load_state_snapshot(tmp_path) == StateSnapshot(change_id=None, state=None)


@pytest.mark.parametrize("body", ["just a string\n", "- a\n- b\n", "[]\n"])
def test_non_mapping_root_is_permissive(tmp_path: Path, body: str) -> None:
    _write_state(tmp_path, body)
    assert load_state_snapshot(tmp_path).state is None


def test_corrupt_yaml_is_permissive(tmp_path: Path) -> None:
    _write_state(tmp_path, "changes: {unterminated\n")
    assert load_state_snapshot(tmp_path).state is None


def test_non_utf8_bytes_are_permissive(tmp_path: Path) -> None:
    # Invalid UTF-8 raises UnicodeDecodeError (a ValueError, NOT an OSError) — it
    # must not escape the loader. In positional mode a raise → exit 1 → BLOCK, i.e.
    # fail-CLOSED, the opposite of Axiom 1. `errors="replace"` on the read
    # neutralizes it; the mangled text then fails the YAML parse → state=None.
    harness = tmp_path / ".harness"
    harness.mkdir(parents=True, exist_ok=True)
    (harness / "state.yaml").write_bytes(b"\xff\xfe changes: {a: b}\n")
    assert load_state_snapshot(tmp_path).state is None  # must not raise


def test_record_with_unknown_field_is_permissive(tmp_path: Path) -> None:
    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n    bogus_field: 1\n",
    )
    snap = load_state_snapshot(tmp_path)
    assert snap.change_id == "a"
    assert snap.state is None


@pytest.mark.parametrize("bad", ["[x]", "{a: b}", "5"])
@pytest.mark.parametrize("override", [None, "a"])
def test_malformed_current_state_never_raises(
    tmp_path: Path, bad: str, override: str | None
) -> None:
    # A list/dict current_state is unhashable (pick_active_change's frozenset test
    # raises TypeError); an int is hashable but not a real state. BOTH resolution
    # paths must degrade to state=None WITHOUT raising: the recency path
    # (override=None) AND the override path (which SKIPS pick_active_change, so the
    # guard is the isinstance(current_state, str) check, not the frozenset test).
    # The downstream pure gate must not raise on the result either.
    from super_harness.gates import ProposedAction
    from super_harness.gates.pre_tool_use import PreToolUseGate

    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n"
        f"    current_state: {bad}\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    snap = load_state_snapshot(tmp_path, change_id_override=override)  # must not raise
    assert snap.state is None
    # And the pure policy must not raise on the (None) state either.
    PreToolUseGate().decide(ProposedAction(kind="edit", file="f.py"), snap.state, [])


def test_loader_never_write_opens_state(tmp_path: Path) -> None:
    # The decision plane must be read-only w.r.t. state.yaml. A builtins.open spy
    # is INSUFFICIENT (Path.read_text bypasses builtins.open on 3.11+, so the spy
    # sees nothing and passes vacuously). Use sys.addaudithook, which fires at the
    # C level for pathlib reads / io.open / builtins.open alike on 3.10-3.13.
    _write_state(
        tmp_path,
        "changes:\n  a:\n    change_id: a\n    current_state: PLAN_APPROVED\n"
        "    last_event_at: '2026-07-01T00:00:00Z'\n",
    )
    reads: list[str] = []
    writes: list[str] = []
    active = True  # addaudithook is process-global + permanent; gate the body so it
    #              becomes a no-op after this test (no slow leak across the session).

    def hook(event: str, args: tuple) -> None:
        if not active or event != "open":
            return
        path, mode = args[0], args[1]
        if not (isinstance(path, str) and path.endswith("state.yaml")):
            return
        # mode is None for os.open (raw fd); str for io.open/builtins.open/pathlib.
        if mode is None:
            return
        (writes if any(c in mode for c in "wax+") else reads).append(f"{path}:{mode}")

    sys.addaudithook(hook)  # cannot be removed; keep it cheap + gated by `active`
    try:
        load_state_snapshot(tmp_path)
    finally:
        active = False  # read by the closure above on every later open
    assert writes == [], f"gate loader write-opened state.yaml: {writes}"
    assert reads, "audit hook saw no read of state.yaml — the assertion would be vacuous"
