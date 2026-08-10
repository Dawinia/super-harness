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
    assert "code review independence: self-signed" in r.output
    assert r.exit_code == 0  # disclosure NEVER changes pass/fail


def test_verify_discloses_independent_line(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "code review independence: independent — bob@x" in r.output
    assert r.exit_code == 0


def test_verify_no_validated_attestation_prints_no_independence_line(tmp_path, monkeypatch):
    # subject file but NO added covering attestation → FAIL, and no disclosure line
    r = _verify(tmp_path, monkeypatch, "M\tsrc/x.py\n")
    assert "independence:" not in r.output


def test_verify_json_has_independence_and_stays_one_line(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    r = _verify(tmp_path, monkeypatch, _DIFF, json_mode=True)
    assert "independence:" not in r.output  # human text must not leak to JSON
    payload = json.loads(r.output)  # single parseable line
    assert "independence" in payload["data"]
    assert payload["data"]["independence"][0]["classification"] == "independent"


def test_verify_tolerated_malformed_line_still_discloses(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x", junk=True)
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "code review independence:" in r.output
    assert "plan review independence:" in r.output
    assert r.exit_code == 0  # no crash out of the non-failing path


def test_verify_fail_still_discloses_validated_attestation(tmp_path, monkeypatch):
    # feat-x covers src/x.py; src/y.py is an uncovered subject → overall FAIL,
    # but the validated attestation still gets its disclosure line.
    _attestation(tmp_path, "feat-x", "alice@x", "alice@x")
    r = _verify(tmp_path, monkeypatch, _DIFF + "M\tsrc/y.py\n")
    assert r.exit_code == 2  # uncovered y.py fails the gate
    assert "code review independence: self-signed" in r.output  # still emitted


def test_verify_quiet_suppresses_disclosure(tmp_path, monkeypatch):
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    _init(tmp_path)
    monkeypatch.setattr(attest_mod, "_git_name_status", lambda b, h, c: _DIFF)
    r = CliRunner().invoke(main, [
        "--workspace", str(tmp_path), "--quiet",
        "attest", "verify", "--base", "main", "--head", "HEAD"])
    assert r.exit_code == 0
    assert "independence:" not in r.output


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
        "code_review",
        {"classification": "skipped", "reviewer": "t", "skipped": True,
         "override": True, "reason": "deadlock"})
    assert "OVERRIDE" in line
    assert "deadlock" in line


def test_independence_line_names_its_role():
    """A bare `review independence:` beside a labelled plan row would read as "the
    review" and quietly claim the plan row's meaning."""
    from super_harness.cli.attest import _independence_line
    item = {"classification": "independent", "reviewer": "bob@x", "skipped": False,
            "override": False, "reason": None}
    assert _independence_line("code_review", item).startswith("code review independence:")
    assert _independence_line("plan_review", item).startswith("plan review independence:")


# --------------------------------------------------------------------------- #
# GitHub #96: the round-budget count reaches the merge disclosure
# --------------------------------------------------------------------------- #
def _budget_hold(w: EventWriter, slug: str, *, reviewer: str, attempted: int) -> None:
    _emit_id(w, "review_budget_exceeded", slug, "review-protocol",
             {"reviewer": reviewer, "attempted_round": attempted})


def _attestation_with_holds(root: Path, slug: str, holds: list[tuple[str, int]]) -> None:
    """A complete lifecycle whose review was held by the budget `len(holds)` times."""
    _attestation(root, slug, "alice@x", "bob@x")
    w = EventWriter(root / ".harness" / "attestations" / f"{slug}.jsonl")
    for reviewer, attempted in holds:
        _budget_hold(w, slug, reviewer=reviewer, attempted=attempted)


def test_verify_prints_round_budget_holds(tmp_path, monkeypatch):
    """The count `derive_independence` computes must reach the human line.

    The change that added the counter argued in its own plan that shipping only the
    `report` half drops the half an agent cannot decline to relay — and shipped exactly
    that half. `docs/getting-started.md` already promised this line.
    """
    _attestation_with_holds(tmp_path, "feat-x", [("plan-reviewer", 7), ("plan-reviewer", 8)])
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "round budget: held 2 automatic round(s) for a human funding decision" in r.output
    assert r.exit_code == 0  # disclosure NEVER changes pass/fail


def test_verify_budget_hold_is_not_attached_to_the_independence_line(tmp_path, monkeypatch):
    """A hold must not read as a claim about either reviewer.

    `derive_independence` counts every `review_budget_exceeded` whatever role raised it,
    so the figure belongs to the change and to no single role's row. Attaching it to one
    would misattribute it (#96) and — now that there are two rows — print it twice. The
    two numbers therefore travel on separate lines and in separate envelope keys.
    """
    _attestation_with_holds(tmp_path, "feat-x", [("plan-reviewer", 7)])
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert [ln for ln in r.output.splitlines() if "independence:" in ln] == [
        "code review independence: independent — bob@x",
        'plan review independence: unattributed (legacy "cli" placeholder)',
    ]
    assert sum(ln.startswith("round budget:") for ln in r.output.splitlines()) == 1


def test_verify_without_a_hold_prints_no_budget_line(tmp_path, monkeypatch):
    """A `held 0 round(s)` line on every clean change would be noise."""
    _attestation(tmp_path, "feat-x", "alice@x", "bob@x")
    r = _verify(tmp_path, monkeypatch, _DIFF)
    assert "round budget:" not in r.output


def test_verify_json_carries_per_slug_budget_holds(tmp_path, monkeypatch):
    _attestation_with_holds(tmp_path, "feat-x", [("code-reviewer", 5)])
    r = _verify(tmp_path, monkeypatch, _DIFF, json_mode=True)
    assert "round budget:" not in r.output  # human text must not leak into the envelope
    payload = json.loads(r.output)
    assert payload["data"]["budget_holds"] == [{"slug": "feat-x", "rounds_held": 1}]
    # and the independence item keeps exactly its published shape
    assert "review_budget_rounds_held" not in payload["data"]["independence"][0]


def test_verify_attributes_each_hold_to_its_own_attestation(tmp_path, monkeypatch):
    """Two holding attestations in one base..head range must be attributable.

    The budget line sits in the SAME per-slug loop as `_independence_line`, so each hold
    follows the independence line of the change it belongs to. Emitted from a separate
    loop they were byte-identical and unattributable (code review HDS-002).
    """
    _attestation_with_holds(tmp_path, "feat-x", [("plan-reviewer", 7)])
    _attestation_with_holds(tmp_path, "feat-y", [("code-reviewer", 5), ("code-reviewer", 6)])
    diff = (
        "A\t.harness/attestations/feat-x.jsonl\n"
        "A\t.harness/attestations/feat-y.jsonl\n"
        "M\tsrc/x.py\n"
    )
    r = _verify(tmp_path, monkeypatch, diff)
    prefixes = ("code review independence:", "plan review independence:", "round budget:")
    lines = [ln for ln in r.output.splitlines() if ln.startswith(prefixes)]
    # each hold immediately follows ITS OWN change's pair of independence lines, and is
    # emitted once per attestation rather than once per role
    assert [ln.split(":")[0] for ln in lines] == [
        "code review independence", "plan review independence", "round budget",
        "code review independence", "plan review independence", "round budget",
    ]
    assert "held 1 automatic round(s)" in lines[2]
    assert "held 2 automatic round(s)" in lines[5]
