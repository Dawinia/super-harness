"""Integration tests for `super-harness status` (Phase 2 Task 2.4).

Read-only command: replays events.jsonl through reducer and renders per-change
state. No event emission, no post_emit_refresh wiring needed.

Coverage map:
- test_status_no_harness_exits_3        — exit 3 when .harness/ missing
- test_status_with_slug_shows_change    — `status <slug>` for known slug
- test_status_unknown_slug_empty_ok     — unknown slug → exit 0 + empty result
                                          (query succeeded; result empty)
- test_status_default_first_active      — no args + no flag → first active
- test_status_all_includes_terminal     — `--all` includes ARCHIVED/ABANDONED
- test_status_json_envelope_schema      — `--json` shape: envelope.data.changes[]
"""
import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def _init(tmp_path: Path) -> None:
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])


def _start(tmp_path: Path, slug: str) -> None:
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "change", "start", slug])


def _abandon(tmp_path: Path, slug: str) -> None:
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "change", "abandon", slug])


def test_status_no_harness_exits_3(tmp_path: Path) -> None:
    """No `.harness/` → HarnessNotInitialized → exit 3 (EXIT_NO_CONFIG)."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status"])
    assert r.exit_code == 3


def test_status_with_slug_shows_change(tmp_path: Path) -> None:
    """`status <slug>` renders that single change's current_state line."""
    _init(tmp_path)
    _start(tmp_path, "ch-alpha")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "status", "ch-alpha"]
    )
    assert r.exit_code == 0
    assert "ch-alpha" in r.output
    assert "INTENT_DECLARED" in r.output


def test_status_unknown_slug_empty_ok(tmp_path: Path) -> None:
    """Unknown slug: query succeeded, result is empty → exit 0 (mirrors list)."""
    _init(tmp_path)
    _start(tmp_path, "ch-known")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "status", "ch-missing"],
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["data"]["changes"] == []


def test_status_default_first_active(tmp_path: Path) -> None:
    """No args + no `--all` → fall back to first active change.

    v0.1 simple fallback per plan. NOT git-branch parsing — comment in
    cli/status.py is honest about the simplification.
    """
    _init(tmp_path)
    _start(tmp_path, "ch-first")
    _start(tmp_path, "ch-second")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status"]
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    changes = payload["data"]["changes"]
    assert len(changes) == 1
    # Insertion order of `derive_state` reflects events.jsonl line order, so the
    # first emitted slug is the "first active" the fallback picks.
    assert changes[0]["change_id"] == "ch-first"


def test_status_all_includes_terminal(tmp_path: Path) -> None:
    """`--all` returns every change, including ABANDONED (terminal state)."""
    _init(tmp_path)
    _start(tmp_path, "ch-active")
    _start(tmp_path, "ch-doomed")
    _abandon(tmp_path, "ch-doomed")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "--all"]
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    slugs = {c["change_id"] for c in payload["data"]["changes"]}
    assert slugs == {"ch-active", "ch-doomed"}
    # Verify the terminal one is actually marked ABANDONED (proves `--all`
    # isn't just "active + something else"; it's truly everything).
    by_id = {c["change_id"]: c for c in payload["data"]["changes"]}
    assert by_id["ch-doomed"]["current_state"] == "ABANDONED"


def test_status_json_envelope_schema(tmp_path: Path) -> None:
    """`--json` emits the standard 6-key envelope; `data.changes` is a list of dicts."""
    _init(tmp_path)
    _start(tmp_path, "ch-only")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "ch-only"]
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["command"] == "status"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == 0
    assert payload["errors"] == []
    assert isinstance(payload["data"]["changes"], list)
    assert len(payload["data"]["changes"]) == 1
    entry = payload["data"]["changes"][0]
    # ChangeState fields the contract leans on (per cli/status.py text format)
    assert entry["change_id"] == "ch-only"
    assert entry["current_state"] == "INTENT_DECLARED"
    assert entry["last_event_type"] == "intent_declared"
    assert entry["last_event_at"]
