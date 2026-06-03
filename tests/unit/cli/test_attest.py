"""CLI tests for `super-harness attest write` / `attest verify`."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import super_harness.cli.attest as attest_mod
from super_harness.cli import main


def _init(root: Path) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "events.jsonl").write_text(
        '{"change_id":"s","type":"intent_declared","event_id":"e1",'
        '"timestamp":"2026-06-04T00:00:00Z","actor":{"type":"human","identifier":"t"},'
        '"framework":"plain","payload":{}}\n'
    )


# --------------------------------------------------------------------------- #
# attest write (Task 6)
# --------------------------------------------------------------------------- #
def test_attest_write_creates_file(tmp_path):
    _init(tmp_path)
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "attest", "write", "s"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / ".harness" / "attestations" / "s.jsonl").exists()


def test_attest_write_no_events_for_slug_errors(tmp_path):
    _init(tmp_path)
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "attest", "write", "other"]
    )
    assert r.exit_code == 1


def test_attest_write_no_config_exits_3(tmp_path):
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "attest", "write", "s"])
    assert r.exit_code == 3


# --------------------------------------------------------------------------- #
# attest verify (Task 7)
# --------------------------------------------------------------------------- #
def test_attest_verify_fails_on_uncovered(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.setattr(
        attest_mod, "_git_name_status", lambda base, head, cwd: "A\tsrc/snuck.py\n"
    )
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "attest", "verify", "--base", "main", "--head", "HEAD"],
    )
    assert r.exit_code == 2
    assert "snuck.py" in r.output


def test_attest_verify_fail_closed_on_git_error(tmp_path, monkeypatch):
    _init(tmp_path)

    def boom(base, head, cwd):
        raise attest_mod._GitError("no merge base")

    monkeypatch.setattr(attest_mod, "_git_name_status", boom)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "attest", "verify", "--base", "main", "--head", "HEAD"],
    )
    assert r.exit_code == 4


def test_attest_verify_passes_on_empty_diff(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.setattr(attest_mod, "_git_name_status", lambda base, head, cwd: "")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "attest", "verify", "--base", "main", "--head", "HEAD"],
    )
    assert r.exit_code == 0, r.output
