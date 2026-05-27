import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def _init(tmp_path: Path):
    return CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])


def test_change_start_emits_intent_declared(tmp_path: Path):
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        [
            "--workspace",
            str(tmp_path),
            "change",
            "start",
            "2026-05-27-add-foo",
            "--description",
            "Add foo",
        ],
    )
    assert r.exit_code == 0
    events_file = tmp_path / ".harness" / "events.jsonl"
    assert events_file.exists()
    line = events_file.read_text().splitlines()[0]
    assert json.loads(line)["type"] == "intent_declared"
    assert json.loads(line)["change_id"] == "2026-05-27-add-foo"


def test_change_start_rejects_invalid_slug(tmp_path: Path):
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "start", "Has Spaces"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION


def test_change_list_shows_active(tmp_path: Path):
    _init(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["--workspace", str(tmp_path), "change", "start", "ch1"])
    runner.invoke(main, ["--workspace", str(tmp_path), "state", "rebuild"])
    r = runner.invoke(
        main,
        ["--workspace", str(tmp_path), "--json", "change", "list"],
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert any(c["change_id"] == "ch1" for c in payload["data"]["changes"])


def test_change_list_rejects_invalid_state(tmp_path: Path):
    """Fix I-2: --state must be one of the 11 canonical (uppercase) states.

    Before this fix, `--state intent_declared` silently matched nothing and
    returned exit 0 — a typo bug user couldn't see. click.Choice rejects with
    UsageError → exit 2 (which conveniently matches our EXIT_VALIDATION).
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "list", "--state", "intent_declared"],
    )
    assert r.exit_code == 2  # Click's UsageError exit code == EXIT_VALIDATION


def test_change_abandon_rejects_invalid_slug(tmp_path: Path):
    """Fix I-3: `abandon` validates slug symmetrically with `start`.

    Without this, `change abandon "Has Spaces"` falls through to the writer
    and surfaces a lifecycle-state-rule error the user can't act on.
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "abandon", "Has Spaces"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION


def test_change_list_rejects_conflicting_state_flags(tmp_path: Path):
    """Fix I-4 option (a): --active / --archived / --abandoned are mutex.

    Combining them always yields an empty set; reject at parse time with an
    actionable message instead of silently returning [].
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "change", "list", "--active", "--archived"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION
    assert "mutually exclusive" in r.stderr
