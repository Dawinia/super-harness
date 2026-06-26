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


# --------------------------------------------------------------------------- #
# HG-12 cut 1: review-independence disclosure (non-failing)
# --------------------------------------------------------------------------- #
import json  # noqa: E402

from super_harness.core.clock import utc_now_iso  # noqa: E402
from super_harness.core.events import Actor, Event  # noqa: E402
from super_harness.core.ulid import new_event_id  # noqa: E402
from super_harness.core.writer import EventWriter  # noqa: E402

_DIFF = "A\t.harness/attestations/feat-x.jsonl\nM\tsrc/x.py\n"


def _emit_id(w: EventWriter, etype: str, slug: str, ident: str, payload=None) -> None:
    w.emit(Event(
        event_id=new_event_id(), type=etype, change_id=slug, timestamp=utc_now_iso(),
        actor=Actor(type="human", identifier=ident), framework="plain",
        payload=payload or {}))


def _attestation(root: Path, slug: str, author: str, reviewer: str, *, junk=False) -> None:
    d = root / ".harness" / "attestations"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.jsonl"
    w = EventWriter(path)
    _emit_id(w, "intent_declared", slug, author)
    _emit_id(w, "plan_ready", slug, "cli", {"scope": {"files": ["src/x.py"]}})
    _emit_id(w, "plan_approved", slug, "cli")
    _emit_id(w, "implementation_started", slug, "cli")
    _emit_id(w, "verification_passed", slug, "cli")
    _emit_id(w, "implementation_complete", slug, "cli")
    _emit_id(w, "code_review_passed", slug, reviewer)
    if junk:  # tolerated-malformed line appended after a valid lifecycle
        with path.open("a", encoding="utf-8") as fh:
            fh.write("{ this is not valid json\n")


def _verify(tmp_path, monkeypatch, diff: str, *, json_mode: bool = False):
    _init(tmp_path)
    monkeypatch.setattr(attest_mod, "_git_name_status", lambda b, h, c: diff)
    args = ["--workspace", str(tmp_path)]
    if json_mode:
        args.append("--json")
    args += ["attest", "verify", "--base", "main", "--head", "HEAD"]
    return CliRunner().invoke(main, args)


def test_verify_discloses_self_signed_line(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "alice@x")  # author == reviewer
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "review independence: self-signed" in r.output
    assert r.exit_code == 0  # disclosure NEVER changes pass/fail


def test_verify_discloses_independent_line(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "review independence: independent — bob@x" in r.output
    assert r.exit_code == 0


def test_verify_no_validated_attestation_prints_no_independence_line(tmp_path, monkeypatch):
    # subject file but NO added covering attestation → FAIL, and no disclosure line
    r = _verify(tmp_path, monkeypatch, "M\tsrc/x.py\n")
    assert "review independence:" not in r.output


def test_verify_json_has_independence_and_stays_one_line(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    r = _verify(tmp_path, monkeypatch, _DIFF, json_mode=True)
    assert "review independence:" not in r.output  # human text must not leak to JSON
    payload = json.loads(r.output)  # single parseable line
    assert "independence" in payload["data"]
    assert payload["data"]["independence"][0]["classification"] == "independent"


def test_verify_tolerated_malformed_line_still_discloses(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x", junk=True)
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "review independence:" in r.output
    assert r.exit_code == 0  # no crash out of the non-failing path


def test_verify_fail_still_discloses_validated_attestation(tmp_path, monkeypatch):
    # feat-x covers src/x.py; src/y.py is an uncovered subject → overall FAIL,
    # but the validated attestation still gets its disclosure line.
    _attestation(tmp_path, "feat-x", "alice@x", "alice@x")
    r = _verify(tmp_path, monkeypatch, _DIFF + "M\tsrc/y.py\n")
    assert r.exit_code == 2  # uncovered y.py fails the gate
    assert "review independence: self-signed" in r.output  # disclosure still emitted


def test_verify_quiet_suppresses_disclosure(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    _init(tmp_path)
    monkeypatch.setattr(attest_mod, "_git_name_status", lambda b, h, c: _DIFF)
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "--quiet",
        "attest", "verify", "--base", "main", "--head", "HEAD"])
    assert r.exit_code == 0
    assert "review independence:" not in r.output


# --------------------------------------------------------------------------- #
# Part C.3: gate-bypass disclosure (merge blocker + write-side clear)
# --------------------------------------------------------------------------- #
from super_harness.core.paths import events_path  # noqa: E402


def _seed_lifecycle(root: Path, slug: str, author: str, reviewer: str) -> EventWriter:
    """Emit a full READY_TO_MERGE lifecycle for *slug* into the workspace events.jsonl."""
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    w = EventWriter(events_path(root))
    _emit_id(w, "intent_declared", slug, author)
    _emit_id(w, "plan_ready", slug, "cli", {"scope": {"files": ["src/x.py"]}})
    _emit_id(w, "plan_approved", slug, "cli")
    _emit_id(w, "implementation_started", slug, "cli")
    _emit_id(w, "verification_passed", slug, "cli")
    _emit_id(w, "implementation_complete", slug, "cli")
    _emit_id(w, "code_review_passed", slug, reviewer)
    return w


def test_attest_write_disclose_gate_bypass_clears_blocker(tmp_path, monkeypatch):
    slug = "feat-x"
    w = _seed_lifecycle(tmp_path, slug, "alice@x", "bob@x")
    # Inject an undisclosed gate bypass (same emit discipline as _record_bypass).
    _emit_id(w, "gate_bypassed", slug, "gate", {"tool": "Edit", "file": "src/x.py"})

    monkeypatch.setattr(attest_mod, "_git_name_status", lambda b, h, c: _DIFF)

    # Plain write -> verify: the undisclosed bypass BLOCKS the merge gate.
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "attest", "write", slug])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "attest", "verify", "--base", "main", "--head", "HEAD"],
    )
    assert r.exit_code == 2, r.output
    assert "without disclosure" in r.output

    # Disclose -> re-write -> verify: blocker cleared and the reason surfaced.
    r = CliRunner().invoke(
        main,
        [
            "--workspace", str(tmp_path), "attest", "write", slug,
            "--disclose-gate-bypass", "daemon was wedged",
        ],
    )
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "attest", "verify", "--base", "main", "--head", "HEAD"],
    )
    assert r.exit_code == 0, r.output
    assert "daemon was wedged" in r.output


def test_independence_line_override_skip():
    from super_harness.cli.attest import _independence_line
    line = _independence_line(
        {"classification": "skipped", "reviewer": "t", "skipped": True,
         "override": True, "reason": "deadlock"})
    assert "OVERRIDE" in line
    assert "deadlock" in line
