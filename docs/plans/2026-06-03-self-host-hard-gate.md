# Self-host hard gate (HG-D step 2) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task.

**Goal:** Turn on the deterministic PreToolUse edit-gate for super-harness's own
development (Claude Code, dogfood), with a robust self-lock escape hatch and
one-command onboarding.

**Architecture:** Three small production changes + enable/verify + docs.
(1) the agent adapter installs its hook into the per-machine, gitignored
`.claude/settings.local.json`; (2) the hook honors a file-based kill switch
`.harness/gate-disabled` that short-circuits to ALLOW; (3) `super-harness init`
auto-installs the detected agent adapter so onboarding is one command. Design:
`docs/plans/2026-06-03-self-host-hard-gate-design.md`.

**Tech Stack:** Python 3.10+, click, pytest. stdlib-only adapter/hook code.

**Run tests with the repo venv on PATH** (daemon/hook tests are skipped otherwise):
`PATH="$(pwd)/.venv/bin:$PATH" python -m pytest ...`

**PR-split note:** Tasks 1-3 + 5 + 6 = the gate itself (the dogfood deliverable).
Task 4 (init auto-install) is end-user onboarding UX and may land as a second PR
if the diff grows large — decide at execution time.

---

## Task 1: Agent adapter installs into `settings.local.json`

**Why:** the hook command pins a machine-specific absolute path; it belongs in
the per-machine, conventionally-gitignored `settings.local.json`, never the
committed `settings.json`. Claude Code runs hooks from both files (verified).

**Files:**
- Modify: `src/super_harness/adapters/agent/claude_code.py` (install target +
  uninstall backup target + docstrings)
- Test: `tests/unit/adapters/test_claude_code.py`
- Test: `tests/integration/adapter/test_claude_code.py`

**Step 1: Update the unit test expectations to `settings.local.json`**

In `tests/unit/adapters/test_claude_code.py`, replace every
`".claude" / "settings.json"` path reference (in
`test_install_hooks_writes_settings_entry`, `..._missing_hook_binary_raises`,
`..._missing_cli_binary_raises_before_write`, `..._idempotent`,
`..._rolls_back_on_second_merge_failure`, and any backup/uninstall test) with
`".claude" / "settings.local.json"`.

**Step 2: Run the tests to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/adapters/test_claude_code.py -q`
Expected: FAIL — code still writes `settings.json`, asserts on `settings.local.json` miss.

**Step 3: Change the install/uninstall target**

In `src/super_harness/adapters/agent/claude_code.py`:
- `install_hooks`: `settings_path = workspace / ".claude" / "settings.json"` →
  `settings_path = workspace / ".claude" / "settings.local.json"`
- `on_uninstall`: `settings_path = workspace / ".claude" / "settings.json"` →
  `settings_path = workspace / ".claude" / "settings.local.json"`
- Update the module docstring + `install_hooks`/`on_uninstall` docstrings that
  say `settings.json` to `settings.local.json`, noting the per-machine /
  gitignored rationale (one line). The backup glob
  (`settings.local.json.super-harness-backup.*`) follows `settings_path.name`
  automatically — no change needed.

**Step 4: Run tests to verify they pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py -q`
Expected: PASS. (Update any `settings.json` references in the integration test the
same way if it fails.)

**Step 5: Commit**

```bash
git add src/super_harness/adapters/agent/claude_code.py tests/unit/adapters/test_claude_code.py tests/integration/adapter/test_claude_code.py
git commit -m "feat(adapter): claude-code installs hook into settings.local.json (per-machine)"
```

---

## Task 2: Hook honors the `.harness/gate-disabled` kill switch

**Why:** the self-lock "big red button". A sentinel file short-circuits the hook
to ALLOW before any daemon/state access, toggled via ungated Bash. Robust where
`daemon stop` is not (it flaps/respawns).

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py` (`_decide`)
- Test: `tests/unit/daemon/test_hook_entry.py` (new)

**Step 1: Write the failing test**

Create `tests/unit/daemon/test_hook_entry.py`:

```python
"""Unit tests for hook_entry._decide's file-based kill switch."""
from __future__ import annotations

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
```

**Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/daemon/test_hook_entry.py -q`
Expected: FAIL — without the short-circuit, `_decide` tries the supervisor/daemon
path (and would not return the gate-disabled reason).

**Step 3: Add the short-circuit in `_decide`**

In `src/super_harness/daemon/hook_entry.py`, immediately AFTER the
`find_harness_root` try/except block resolves `root` (before resolving
`change_id`):

```python
    # File-based kill switch (self-host hard-gate escape hatch): a sentinel file
    # short-circuits to ALLOW before any daemon/state access, so a wedged daemon
    # or corrupt state can never trap the user. Toggle via ungated Bash
    # (`touch .harness/gate-disabled` / `rm`). Robust where `daemon stop` is not
    # — the unreachable path respawns the daemon, so stop only reprieves one edit.
    if (root / ".harness" / "gate-disabled").exists():
        return "allow", "gate disabled (.harness/gate-disabled present)"
```

**Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/daemon/test_hook_entry.py -q`
Expected: PASS.

**Step 5: Surface the switch in the block message**

In both `_run_positional` and `_run_claude_code_shim`, extend the BLOCK stderr to
mention the escape hatch, e.g.:

```python
        sys.stderr.write(
            f"super-harness: BLOCK ({reason})\n"
            f"  escape hatch: touch .harness/gate-disabled to disable the gate\n"
        )
```

Run the existing hook tests to confirm no regression on the message:
Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/integration/daemon/test_hook_entry.py -q`
Expected: PASS (adjust any exact-stderr-match assertions to substring matches if
one breaks).

**Step 6: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/unit/daemon/test_hook_entry.py
git commit -m "feat(hook): file-based kill switch .harness/gate-disabled (self-lock escape)"
```

---

## Task 3: Add `.harness/gate-disabled` to the managed `.gitignore` block

**Why:** the kill-switch file is a transient local toggle — never committed.

**Files:**
- Modify: `src/super_harness/engineering/gitignore_injector.py` (`_CANONICAL_PATHS`)
- Test: `tests/unit/engineering/test_gitignore_injector.py` (local `_CANONICAL_PATHS` copy)

**Step 1: Update the test's canonical-paths copy**

In `tests/unit/engineering/test_gitignore_injector.py`, add
`".harness/gate-disabled",` to the local `_CANONICAL_PATHS` tuple (keep ordering
consistent with the source — append after `.harness/pending-reviews/`, before
the `.claude/*.super-harness-backup.*` entry, matching source order).

**Step 2: Run to verify failure**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/engineering/test_gitignore_injector.py -q`
Expected: FAIL — source block lacks `.harness/gate-disabled`; the
`lines == list(_CANONICAL_PATHS)` equality (≈line 277) fails.

**Step 3: Add the path to the source**

In `src/super_harness/engineering/gitignore_injector.py`, add
`".harness/gate-disabled",` to `_CANONICAL_PATHS` at the SAME position.

**Step 4: Run to verify pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/engineering/test_gitignore_injector.py -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add src/super_harness/engineering/gitignore_injector.py tests/unit/engineering/test_gitignore_injector.py
git commit -m "feat(init): gitignore the .harness/gate-disabled kill switch"
```

---

## Task 4: `super-harness init` auto-installs the detected agent adapter

**Why:** end-user onboarding becomes one command (`pipx install` → `init`). Reuses
existing helpers; the gate stays dormant until a change is active (no surprise).

**Approach (DRY):** `render_super_harness_section` already re-injects the AGENTS.md
subsection for every adapter in `adapters.yaml`. So `init` only needs to, BEFORE
that render call: detect the agent, `install_hooks`, and persist the
`adapters.yaml` entry — the subsection injection then comes for free.

**Files:**
- Modify: `src/super_harness/cli/init.py` (new `--no-agent` option; agent
  auto-install block before `render_super_harness_section`)
- Reuse: `_persist_install_entry`, `_merge_verification_checks` from
  `src/super_harness/cli/adapter.py`; `ClaudeCodeAdapter` from the registry
- Test: `tests/integration/cli/test_init.py`

**Step 1: Write the failing tests**

In `tests/integration/cli/test_init.py`, add:

```python
def test_init_auto_installs_agent_hook_when_claude_dir_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init with `.claude/` present installs the PreToolUse hook into
    settings.local.json and registers claude-code in adapters.yaml."""
    monkeypatch.setattr(
        "super_harness.adapters.agent.claude_code.shutil.which",
        lambda name: f"/abs/bin/{name}",
    )
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    settings = (tmp_path / ".claude" / "settings.local.json")
    assert settings.exists()
    assert "--agent claude-code" in settings.read_text()
    adapters = (tmp_path / ".harness" / "adapters.yaml").read_text()
    assert "claude-code" in adapters


def test_init_no_agent_flag_skips_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".claude").mkdir()
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init", "--no-agent"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_init_no_claude_dir_is_agent_noop(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--workspace", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".claude").exists()
```

(Confirm the test module's existing imports for `main` / `CliRunner` / `Path`;
add `pytest` if needed.)

**Step 2: Run to verify failure**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/integration/cli/test_init.py -q -k "auto_installs or no_agent or agent_noop"`
Expected: FAIL — init does not yet install agent hooks / lacks `--no-agent`.

**Step 3: Add the `--no-agent` option + auto-install block**

In `src/super_harness/cli/init.py`:
- Add option: `@click.option("--no-agent", is_flag=True, help="Skip auto-installing the detected agent's gate hook.")`
  and the `no_agent: bool` param to `init_cmd`.
- Add imports: `from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter`
  and `from super_harness.cli.adapter import _persist_install_entry, _merge_verification_checks`.
- Insert BEFORE `render_super_harness_section(...)` (≈line 212):

```python
    # Auto-install the detected agent adapter's gate hook (one-command onboarding).
    # Runs BEFORE render_super_harness_section so the renderer injects the agent's
    # AGENTS.md subsection from the freshly-persisted adapters.yaml entry. The gate
    # is dormant until a change is active (no active change → allow), so this never
    # surprises a fresh init by blocking edits. Non-fatal: a missing hook binary
    # warns and leaves the gate uninstalled rather than aborting init.
    if not no_agent:
        agent = ClaudeCodeAdapter()
        if agent.detect(root):
            try:
                _merge_verification_checks(root, agent)  # no-op for claude-code
                agent.install_hooks(root)
                _persist_install_entry(
                    root, name=agent.name, kind="agent", version=agent.version
                )
            except RuntimeError as e:
                click.echo(
                    format_error(
                        subcommand="init",
                        message=f"agent gate hook not installed: {e}",
                        hint="reinstall super-harness so super-harness-hook is on PATH, "
                             "then `super-harness adapter install claude-code`.",
                    ),
                    err=True,
                )
                # Non-fatal: continue init without the gate.
```

**Step 4: Run to verify pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/integration/cli/test_init.py -q`
Expected: PASS (all init tests, including the three new ones).

**Step 5: Run the full suite (no regressions)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest -q`
Expected: all pass. (`adapter install claude-code` integration tests still pass —
its target is now settings.local.json from Task 1.) Then `ruff check src tests`
+ `mypy src` clean.

**Step 6: Commit**

```bash
git add src/super_harness/cli/init.py tests/integration/cli/test_init.py
git commit -m "feat(init): auto-install the detected agent gate hook (--no-agent to skip)"
```

---

## Task 5: Enable on this repo + dogfood verification

**Why:** flip the gate ON for maintainer development and prove it enforces +
escapes correctly.

**Files:** none in src; this is an operation + a manual verification log.

**Step 1: Re-run init to wire the gate locally**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness init --force
```
Expected: `.claude/settings.local.json` now contains the `super-harness-hook
--agent claude-code` PreToolUse entry with the absolute `.venv/bin` path;
`.harness/adapters.yaml` lists `claude-code`. Confirm `.claude/settings.local.json`
is gitignored (`git check-ignore .claude/settings.local.json`) and that the
managed `.gitignore` block now lists `.harness/gate-disabled`.

**Step 2: Verify ENFORCE (block) in a blocking state**

```bash
# create a change and leave it in a blocking state (INTENT_DECLARED)
PATH="$(pwd)/.venv/bin:$PATH" super-harness change start dogfood-gate-check
PATH="$(pwd)/.venv/bin:$PATH" super-harness daemon start
# simulate the Claude Code PreToolUse call:
echo '{"tool_name":"Edit","tool_input":{"file_path":"README.md"}}' \
  | PATH="$(pwd)/.venv/bin:$PATH" super-harness-hook --agent claude-code; echo "exit=$?"
```
Expected: stderr `super-harness: BLOCK (...)` + the gate-disabled hint; `exit=2`.

**Step 3: Verify ALLOW in PLAN_APPROVED**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness plan ready dogfood-gate-check
PATH="$(pwd)/.venv/bin:$PATH" super-harness review approve dogfood-gate-check --reviewer plan-reviewer
echo '{"tool_name":"Edit","tool_input":{"file_path":"README.md"}}' \
  | PATH="$(pwd)/.venv/bin:$PATH" super-harness-hook --agent claude-code; echo "exit=$?"
```
Expected: `exit=0` (PLAN_APPROVED allows edits).

**Step 4: Verify the kill switch**

```bash
# back to a blocking state by starting a second change, or reuse one mid-review;
# then prove the switch forces allow:
touch .harness/gate-disabled
echo '{"tool_name":"Edit","tool_input":{"file_path":"README.md"}}' \
  | PATH="$(pwd)/.venv/bin:$PATH" super-harness-hook --agent claude-code; echo "exit=$?"   # expect 0
rm .harness/gate-disabled
```
Expected: `exit=0` with the kill switch present, regardless of change state.

**Step 5: Clean up the dogfood change + daemon**

```bash
PATH="$(pwd)/.venv/bin:$PATH" super-harness change abandon dogfood-gate-check  # or the correct terminal verb
PATH="$(pwd)/.venv/bin:$PATH" super-harness daemon stop
rm -f .harness/gate-disabled
```
Record the observed outputs in the PR description (evidence-before-assertions).

**Step 6: No commit** (runtime artifacts are gitignored; `.claude/settings.local.json`
is gitignored). Verify `git status` is clean of unintended files.

---

## Task 6: Docs + records

**Files:**
- Modify: `AGENTS.md` (or `conventions.md`) — document the kill switch + the
  plan-doc workflow under the gate (design §6)
- Modify: spec doc that asserts `settings.json` (adapter-architecture §3.5) — note
  the per-machine `settings.local.json` target
- Update: `private/OPEN-ITEMS.md` (mark HG-D step 2 done; record the roadmap
  open-items from design §7), `private/HARNESS-GAPS.md` Self-host section
- Update: memory `project-self-host-next.md`

**Step 1: Document the escape hatch + workflow**

Add a short subsection (kill switch: `touch .harness/gate-disabled`; the
plan-doc-before-change workflow; CLI verbs run via ungated Bash). Regenerate
AGENTS.md if it is render-managed, or edit `conventions.md` if that is the SSOT.

**Step 2: Annotate the spec + open-items**

Note the `settings.local.json` target in the adapter spec; append design §7
roadmap items (multi-agent adapters, Codex/Cursor Layer-2 fallback, init
framework auto-install, portable-command team-enforcement) to `private/OPEN-ITEMS.md`.

**Step 3: Commit**

```bash
git add AGENTS.md conventions.md docs/  # whichever changed (private/ is gitignored)
git commit -m "docs: self-host hard gate — kill switch, workflow, spec note"
```

---

## Final verification (before PR)

Run: `PATH="$(pwd)/.venv/bin:$PATH" python -m pytest -q` → all green (twice).
Run: `PATH="$(pwd)/.venv/bin:$PATH" ruff check src tests scripts && mypy src` → clean.
Open the PR with the Task 5 verification evidence in the body (gh pr edit can't
change title/body — write them correctly at `gh pr create` time).
</content>
