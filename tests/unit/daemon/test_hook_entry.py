"""Unit tests for hook_entry._decide's file-based kill switch."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml

from super_harness.daemon.hook_entry import _decide


def _init_blocking_workspace(root: Path) -> None:
    """A workspace whose active change is in a BLOCKING state (AWAITING_PLAN_REVIEW)."""
    (root / ".harness").mkdir()
    (root / ".harness" / "state.yaml").write_text(
        yaml.safe_dump(
            {"changes": {"ch1": {"change_id": "ch1",
                                 "current_state": "AWAITING_PLAN_REVIEW"}}}
        )
    )


def test_gate_disabled_sentinel_forces_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`.harness/gate-disabled` short-circuits to ALLOW even when the active
    change is in a blocking state — without contacting the daemon."""
    _init_blocking_workspace(tmp_path)
    (tmp_path / ".harness" / "gate-disabled").touch()
    monkeypatch.chdir(tmp_path)  # _decide resolves root from cwd

    decision, reason = _decide("Edit", str(tmp_path / "foo.py"))

    assert decision == "allow"
    assert "gate-disabled" in reason


def test_gate_disabled_sentinel_allows_with_corrupt_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kill switch is the design's strongest robustness claim: the sentinel
    is checked BEFORE any state read, so a missing/corrupt `.harness/state.yaml`
    can never trap the user. With a CORRUPT state and the sentinel present,
    `_decide` must short-circuit to ALLOW and never touch the daemon path."""
    (tmp_path / ".harness").mkdir()
    # CORRUPT, non-mapping state — would raise if any code tried to read it.
    (tmp_path / ".harness" / "state.yaml").write_text(": : not valid yaml : :\n")
    (tmp_path / ".harness" / "gate-disabled").touch()
    monkeypatch.chdir(tmp_path)  # _decide resolves root from cwd

    # The daemon path must NEVER run while the sentinel is present: if it does,
    # fail loudly (the hook does a late `from super_harness.daemon import
    # supervisor` inside _decide, so patch the attribute on that module).
    monkeypatch.setattr(
        "super_harness.daemon.supervisor.gate_pre_tool_use",
        lambda *a, **k: pytest.fail("daemon path must not run when gate-disabled"),
    )

    decision, reason = _decide("Edit", str(tmp_path / "foo.py"))

    assert decision == "allow"
    assert "gate-disabled" in reason


def test_codex_shim_blocks_with_deny_json(monkeypatch, capsys):
    import json

    from super_harness.daemon import hook_entry

    monkeypatch.setattr(hook_entry, "_decide", lambda tool, file: ("block", "plan not approved"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "apply_patch", "tool_input": {"command": "*** patch"}})))
    with pytest.raises(SystemExit) as exc:
        hook_entry._run_codex_shim()
    assert exc.value.code == 0  # Codex deny is in the JSON, NOT the exit code
    out = json.loads(capsys.readouterr().out)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "plan not approved" in hso["permissionDecisionReason"]


def test_codex_shim_allows_silently(monkeypatch, capsys):
    import json

    from super_harness.daemon import hook_entry

    monkeypatch.setattr(hook_entry, "_decide", lambda tool, file: ("allow", "ok"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "apply_patch", "tool_input": {"command": "x"}})))
    with pytest.raises(SystemExit) as exc:
        hook_entry._run_codex_shim()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""  # no deny JSON on allow


def test_codex_shim_malformed_stdin_fails_open(monkeypatch):
    from super_harness.daemon import hook_entry

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    with pytest.raises(SystemExit) as exc:
        hook_entry._run_codex_shim()
    assert exc.value.code == 0  # fail-open ALLOW


def test_main_routes_agent_codex(monkeypatch):
    from super_harness.daemon import hook_entry

    called = {}
    monkeypatch.setattr(hook_entry, "_run_codex_shim", lambda: called.setdefault("yes", True))
    monkeypatch.setattr("sys.argv", ["super-harness-hook", "--agent", "codex"])
    hook_entry.main()
    assert called.get("yes")


def test_kill_switch_records_gate_bypassed_event(tmp_path, monkeypatch):
    import json
    h = tmp_path / ".harness"
    h.mkdir()
    (h / "gate-disabled").touch()
    (h / "state.yaml").write_text(
        "schema_version: 1\nchanges:\n  c1:\n    state: INTENT_DECLARED\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "c1")
    from super_harness.daemon import hook_entry
    decision, _ = hook_entry._decide("apply_patch", None)
    assert decision == "allow"
    events = (h / "events.jsonl").read_text().strip().splitlines()
    parsed = [json.loads(line) for line in events]
    rec = [e for e in parsed if e["type"] == "gate_bypassed"]
    assert len(rec) == 1
    assert rec[0]["change_id"] == "c1"
    assert rec[0]["payload"]["tool"] == "apply_patch"


def test_kill_switch_with_no_active_change_records_nothing(tmp_path, monkeypatch):
    h = tmp_path / ".harness"
    h.mkdir()
    (h / "gate-disabled").touch()  # no state.yaml → no active change
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPER_HARNESS_CHANGE_ID", raising=False)
    from super_harness.daemon import hook_entry
    decision, _ = hook_entry._decide("apply_patch", None)
    assert decision == "allow"
    evpath = h / "events.jsonl"
    assert not evpath.exists() or "gate_bypassed" not in evpath.read_text()


def test_record_bypass_never_raises(tmp_path):
    from super_harness.daemon import hook_entry
    hook_entry._record_bypass(tmp_path / "nonexistent", tool="apply_patch", file=None)


def test_block_messages_do_not_teach_escape_hatch(capsys, monkeypatch):
    """All three shims' BLOCK output must halt-and-surface, never name gate-disabled."""
    import json

    from super_harness.daemon import hook_entry

    monkeypatch.setattr(
        hook_entry,
        "_decide",
        lambda tool, file: ("block", "INTENT_DECLARED: plan not drafted yet"),
    )

    # positional: argv list in, stderr out, exit 1 on block
    with pytest.raises(SystemExit):
        hook_entry._run_positional(["Edit", "a.py"])
    err = capsys.readouterr().err
    assert "gate-disabled" not in err
    assert "do not bypass" in err.lower()

    # claude-code shim: stdin JSON in, stderr out, exit 2 on block
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "a.py"}})),
    )
    with pytest.raises(SystemExit):
        hook_entry._run_claude_code_shim()
    err = capsys.readouterr().err
    assert "gate-disabled" not in err
    assert "do not bypass" in err.lower()

    # codex shim: stdin JSON in, deny reason in stdout JSON
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"tool_name": "apply_patch", "tool_input": {"command": "x"}})),
    )
    with pytest.raises(SystemExit):
        hook_entry._run_codex_shim()
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "gate-disabled" not in reason
    assert "do not bypass" in reason.lower()


# --- Agnostic Stop orchestrator (`_run_stop`) --------------------------------
# A stub adapter that guards on a MADE-UP field (`my_custom_guard`), NOT
# `stop_hook_active` — so if the orchestrator honors this guard, it cannot be
# reading a hard-coded Claude field. Proves `_run_stop` is agent-agnostic.
class _StubAdapter:
    def stop_should_check(self, payload):
        return payload.get("my_custom_guard") is not True

    def format_stop_feedback(self, verdict):
        return "STUB_OUT" if verdict.violations else ""


def _drive_stop(monkeypatch, tmp_path, payload):
    import sys

    (tmp_path / ".harness").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


def test_run_stop_emits_adapter_output_on_violation(tmp_path, monkeypatch, capsys):
    from super_harness.core.authoring_check import Verdict, Violation
    from super_harness.daemon import hook_entry
    _drive_stop(monkeypatch, tmp_path, {"my_custom_guard": False})
    monkeypatch.setattr(
        "super_harness.core.authoring_check.run_authoring_check",
        lambda root: Verdict(violations=[Violation("d-x", "detail", "docs/decisions/d-x.md")]),
    )
    with pytest.raises(SystemExit) as e:
        hook_entry._run_stop(_StubAdapter())
    assert e.value.code == 0
    assert capsys.readouterr().out == "STUB_OUT"


def test_run_stop_honors_adapter_guard_not_stop_hook_active(tmp_path, monkeypatch, capsys):
    from super_harness.core.authoring_check import Verdict, Violation
    from super_harness.daemon import hook_entry
    # Continuation per the stub's OWN field (stop_hook_active absent) → allow, no output.
    _drive_stop(monkeypatch, tmp_path, {"my_custom_guard": True})
    monkeypatch.setattr(
        "super_harness.core.authoring_check.run_authoring_check",
        lambda root: Verdict(violations=[Violation("d-x", "d", "p")]),
    )
    with pytest.raises(SystemExit) as e:
        hook_entry._run_stop(_StubAdapter())
    assert e.value.code == 0
    assert capsys.readouterr().out == ""


def test_run_stop_fails_open_on_check_error(tmp_path, monkeypatch, capsys):
    from super_harness.daemon import hook_entry
    _drive_stop(monkeypatch, tmp_path, {"my_custom_guard": False})

    def _boom(root):
        raise RuntimeError("graph engine exploded")

    monkeypatch.setattr("super_harness.core.authoring_check.run_authoring_check", _boom)
    with pytest.raises(SystemExit) as e:
        hook_entry._run_stop(_StubAdapter())
    assert e.value.code == 0            # fail-open: never break the agent
    assert capsys.readouterr().out == ""
