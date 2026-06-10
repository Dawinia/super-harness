"""E2E happy-path lifecycle test — the v0.1 ship gate.

Drives the full openspec + claude-code lifecycle slice using real
subprocesses for every shipped surface: ``super-harness`` /
``super-harness-daemon`` / ``super-harness-hook`` binaries, real local
``git``, real ``EventWriter`` / reducer. The only mock is ``gh``
(PATH-shim, via the ``mock_gh`` fixture in ``tests/e2e/conftest.py``).

See plan §16 for the full reconcile notes covering the 10 drift items
that shaped this test (most notably: no ``review skip``/
``implementation start`` CLI verbs, ``on-merge`` not ``merged``, hook
exit codes 1 (positional) vs 2 (claude-code shim), absolute hook path
in ``.claude/settings.local.json``, verification.yaml PyYAML round-trip + a
disabled ``framework_adapter`` layer, and the three lifecycle gaps
bridged via ``EventWriter.emit(skip_validation=True)``).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.e2e.conftest import (
    MockGh,
    _derive_and_read,
    _emit_via_writer,
    _last_event_type,
    _run,
    _wait_for_completed_process,
)


@pytest.mark.e2e
def test_full_openspec_claude_lifecycle(demo_repo: Path, mock_gh: MockGh) -> None:
    """v0.1 ship gate: real daemon, real hook binary, real git, real
    subprocess invocations. ``gh`` is the only mock (PATH-shim).

    Three lifecycle gaps (``plan_approved`` / ``implementation_started`` /
    ``code_review_passed``) are bridged via ``EventWriter.emit(skip_validation=True)``
    — see plan §16 reconcile #7. These will become reviewer-subagent-
    emitted in v0.2.
    """
    # === Phase A — bootstrap ============================================
    _run(["super-harness", "init"], cwd=demo_repo)
    _run(["super-harness", "adapter", "install", "openspec"], cwd=demo_repo)
    _run(["super-harness", "adapter", "install", "claude-code"], cwd=demo_repo)
    assert (demo_repo / ".harness" / "events.jsonl").exists()
    settings = json.loads((demo_repo / ".claude" / "settings.local.json").read_text())
    pre_tool = settings["hooks"]["PreToolUse"]
    # The adapter writes the **absolute** path of the hook binary into
    # `command` (adapters/agent/claude_code.py:121 uses
    # `shutil.which("super-harness-hook")`), not the bare name, so we match
    # by substring.
    assert any(
        "super-harness-hook" in h.get("command", "")
        and "--agent claude-code" in h.get("command", "")
        for entry in pre_tool
        for h in entry["hooks"]
    ), (
        "adapter install claude-code must register "
        "`super-harness-hook --agent claude-code` in PreToolUse"
    )

    # === Phase B — daemon up (blocking start; no sleep needed) ==========
    # `super-harness daemon start` is blocking-until-ready per Phase 11.
    # NOTE: the socket path is NOT asserted at the literal `.harness/
    # daemon.sock` location — `resolve_socket_path` (daemon/_uds_path.py)
    # falls back to `$TMPDIR/super-harness-<hash>.sock` when the workspace
    # path exceeds the 104-byte UDS limit (common on macOS tmp dirs).
    # Daemon readiness is implied by `start` returning 0.
    _run(["super-harness", "daemon", "start"], cwd=demo_repo)

    # === Phase C — cold state: hook ALLOWs (no active change) ===========
    # Positional mode: exit 0 = ALLOW, exit 1 = BLOCK
    # (hook_entry.py:_run_positional; plan §16 reconcile #5).
    def _hook(*args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["super-harness-hook", *args],
            cwd=str(demo_repo),
            capture_output=True,
        )

    r = _hook("Edit", "src/foo.py")
    assert r.returncode == 0, (
        f"cold state must ALLOW; got exit {r.returncode}\n{r.stderr.decode()}"
    )

    # === Phase D — declare change → INTENT_DECLARED, hook BLOCKs ========
    _run(
        [
            "super-harness", "change", "start", "demo-feature",
            "--framework", "openspec",
            "--description", "E2E test feature",
        ],
        cwd=demo_repo,
    )
    assert _last_event_type(demo_repo) == "intent_declared"

    # In INTENT_DECLARED state the gate BLOCKs Edit (positional → exit 1).
    # Polled to absorb the HotState mtime-reload race — sub-second writes
    # to state.yaml may not be visible to the daemon on the very next
    # gate query. `_wait_for_completed_process` returns the full
    # CompletedProcess so we can also assert on the human-readable stderr.
    proc = _wait_for_completed_process(
        lambda: _hook("Edit", "src/foo.py"),
        expected=1,
    )
    assert proc.returncode == 1, (
        f"INTENT_DECLARED must BLOCK (positional exit 1); "
        f"got {proc.returncode}\n{proc.stderr.decode()}"
    )
    assert b"super-harness: BLOCK" in proc.stderr

    # === Phase E — proposal + tasks + scan-once → plan_ready ============
    proposal_dir = demo_repo / "openspec" / "changes" / "demo-feature"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    (proposal_dir / "proposal.md").write_text("# Demo feature\n")
    (proposal_dir / "tasks.md").write_text(
        "scope:\n  files: [src/hello.py]\naffected_anchors: [cap-hello]\n"
    )
    _run(["super-harness", "adapter", "scan-once", "openspec"], cwd=demo_repo)
    assert _last_event_type(demo_repo) == "plan_ready"
    # State now AWAITING_PLAN_REVIEW (plan §16 reconcile #6) — NOT
    # PLAN_APPROVED. `affected_anchors` payload is `[]` regardless of
    # tasks.md content (openspec adapter v0.1 does not extract anchors;
    # see plan §16 "Honest framing").
    assert _derive_and_read(demo_repo, "demo-feature") == "AWAITING_PLAN_REVIEW"

    # === Phase F — GAP-BRIDGE #1: plan_approved =========================
    # No v0.1 CLI/sensor emits plan_approved (plan §16 reconcile #7). In
    # v0.2 the plan-reviewer subagent emits this. E2E seeds it directly.
    _emit_via_writer(
        demo_repo,
        event_type="plan_approved",
        change_id="demo-feature",
        actor_type="human",
        actor_identifier="e2e-seed",
    )
    assert _derive_and_read(demo_repo, "demo-feature") == "PLAN_APPROVED"

    # === Phase G — GAP-BRIDGE #2: implementation_started ================
    # No v0.1 CLI/sensor emits implementation_started (plan §16 reconcile
    # #7); pr_metadata only READS this event. v0.2 reviewer integration
    # emits automatically post-plan_approved.
    _emit_via_writer(
        demo_repo,
        event_type="implementation_started",
        change_id="demo-feature",
        actor_type="human",
        actor_identifier="e2e-seed",
    )
    assert _derive_and_read(demo_repo, "demo-feature") == "IMPLEMENTATION_IN_PROGRESS"

    # === Phase H — real local git commit (no mock) ======================
    src = demo_repo / "src"
    src.mkdir(exist_ok=True)
    (src / "hello.py").write_text("# @capability:cap-hello\nprint('hi')\n")
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.t",
    }
    subprocess.run(
        ["git", "add", "src/hello.py"], cwd=str(demo_repo), check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "feat: hello"],
        cwd=str(demo_repo),
        check=True,
        env=git_env,
    )
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(demo_repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # === Phase I — seed verification.yaml, run `done` ===================
    # Two edits to the default verification.yaml:
    #   1) Add a no-op user check (proves the user_checks layer ran)
    #   2) Disable the framework_adapter layer — its only check is
    #      `openspec validate ${SLUG} --strict --json`, which would fail
    #      against our hand-crafted minimal proposal.md (not a real
    #      OpenSpec spec). The lifecycle wiring we're testing here is
    #      orthogonal to OpenSpec proposal validity; if/when v0.2 wires
    #      `affected_anchors` we'll either ship a real fixture spec or
    #      keep this layer off in E2E by policy.
    # NB: load + edit + dump via PyYAML (not string-replace) because
    # `super-harness init` reformats verification.yaml from the inline
    # template style to multi-line block style on write.
    vfile = demo_repo / ".harness" / "verification.yaml"
    vdata = yaml.safe_load(vfile.read_text())
    vdata["layers"]["framework_adapter"]["enabled"] = False
    vdata["checks"] = [
        {"id": "e2e-noop", "command": "true", "must_pass": True},
    ]
    vfile.write_text(yaml.safe_dump(vdata, sort_keys=False))
    r2 = subprocess.run(
        ["super-harness", "done", "demo-feature"],
        cwd=str(demo_repo),
        capture_output=True,
    )
    assert r2.returncode == 0, r2.stderr.decode()
    assert _derive_and_read(demo_repo, "demo-feature") == "AWAITING_CODE_REVIEW"

    # === Phase J — GAP-BRIDGE #3: code_review_passed ====================
    # v0.2 code-reviewer subagent emits this; no v0.1 CLI/sensor does.
    _emit_via_writer(
        demo_repo,
        event_type="code_review_passed",
        change_id="demo-feature",
        actor_type="human",
        actor_identifier="e2e-seed",
    )
    assert _derive_and_read(demo_repo, "demo-feature") == "READY_TO_MERGE"

    # === Phase K — on-merge (real) =====================================
    # `gh` is PATH-shimmed (mock_gh records all calls). `on-merge` emits
    # `merged` and `refresh_state_after_emit` runs before the subprocess
    # returns (cli/on_merge.py) — no sleep needed.
    _run(
        [
            "super-harness", "on-merge",
            "--commit", commit_sha,
            "--change", "demo-feature",
        ],
        cwd=demo_repo,
    )

    # === Phase L — final state ARCHIVED =================================
    assert _derive_and_read(demo_repo, "demo-feature") == "ARCHIVED", (
        "shipped lifecycle slice must reach ARCHIVED after on-merge"
    )

    _run(["super-harness", "daemon", "stop"], cwd=demo_repo)
