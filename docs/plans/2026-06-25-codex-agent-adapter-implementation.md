# Codex Agent Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `CodexAdapter` so a workspace driven by OpenAI Codex CLI gets the same real-time PreToolUse gate + SessionStart context injection the Claude Code adapter gives today.

**Architecture:** Zero changes to the core gate/state-machine/supervisor. The Codex binding is one new shim mode on the existing `super-harness-hook` entry point + a new `CodexAdapter` that mirrors `ClaudeCodeAdapter`, reusing a *generalized* `_settings_merge.py` (Codex `.codex/hooks.json` is the same shape as Claude `settings.json`). One CLI-layer change de-Claude-ifies the install message. Design: `docs/plans/2026-06-25-codex-agent-adapter-design.md`.

**Tech Stack:** Python 3.10+, click CLI, stdlib `json`/`shutil`/`subprocess`, pytest. Verification: `PATH="$(pwd)/.venv/bin:$PATH" ruff check src tests && mypy src && python -m pytest -q`.

---

## File Structure

- `src/super_harness/adapters/agent/_settings_merge.py` — **modify**: add `matcher` + `marker` kwargs (Claude-preserving defaults). Now agent-neutral.
- `src/super_harness/daemon/hook_entry.py` — **modify**: add `--agent codex` arm + `_run_codex_shim()`.
- `src/super_harness/adapters/agent/codex.py` — **create**: `CodexAdapter`.
- `src/super_harness/adapters/__init__.py` — **modify**: add two non-abstract `AgentAdapter` methods (`local_config_relpath`, `installed_detail`).
- `src/super_harness/adapters/agent/claude_code.py` — **modify**: implement the two new methods (returns its current strings).
- `src/super_harness/cli/adapter.py` — **modify**: print adapter-driven install messages instead of `.claude/` literals.
- `src/super_harness/adapters/registry.py` — **modify**: register `CodexAdapter`.
- `src/super_harness/engineering/gitignore_injector.py` — **modify**: two new canonical `.codex/` paths.
- `.gitignore` (root) — **modify**: regenerated via `sync --gitignore` (must match `_render_block()`).
- Tests: `tests/unit/adapters/test_settings_merge.py`, `tests/unit/daemon/test_hook_entry.py`, `tests/integration/daemon/test_hook_entry.py`, `tests/unit/adapters/test_codex.py` (new), `tests/unit/adapters/test_registry.py`, `tests/unit/engineering/test_gitignore_injector.py`, `tests/integration/cli/test_adapter_install.py`.
- `private/OPEN-ITEMS.md` — record deferrals.
- `AGENTS.md` — regenerated via `sync --agents-md`.

Verification shorthand used below: `V=PATH="$(pwd)/.venv/bin:$PATH"`.

---

## Task 1: Generalize `_settings_merge.py` (matcher + marker kwargs)

**Files:**
- Modify: `src/super_harness/adapters/agent/_settings_merge.py`
- Test: `tests/unit/adapters/test_settings_merge.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/adapters/test_settings_merge.py`:

```python
def test_merge_pre_tool_use_respects_custom_matcher_and_marker(tmp_path):
    from super_harness.adapters.agent._settings_merge import merge_pre_tool_use_hook
    import json

    p = tmp_path / "hooks.json"
    merge_pre_tool_use_hook(
        p,
        command="/abs/super-harness-hook --agent codex",
        matcher="^(apply_patch|Edit|Write)$",
        marker="--agent codex",
    )
    data = json.loads(p.read_text())
    entry = data["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "^(apply_patch|Edit|Write)$"
    assert entry["hooks"][0]["command"] == "/abs/super-harness-hook --agent codex"


def test_codex_marker_does_not_strip_claude_pre_tool_use(tmp_path):
    """A codex re-merge must not remove a co-resident claude-code entry."""
    from super_harness.adapters.agent._settings_merge import merge_pre_tool_use_hook
    import json

    p = tmp_path / "hooks.json"
    # Pre-seed a claude-code entry (foreign marker).
    p.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Edit", "hooks": [
            {"type": "command", "command": "/x super-harness-hook --agent claude-code"}]}
    ]}}))
    merge_pre_tool_use_hook(
        p, command="/abs/h --agent codex",
        matcher="^(apply_patch|Edit|Write)$", marker="--agent codex",
    )
    cmds = [h["command"] for e in json.loads(p.read_text())["hooks"]["PreToolUse"]
            for h in e["hooks"]]
    assert any("--agent claude-code" in c for c in cmds)  # foreign preserved
    assert any("--agent codex" in c for c in cmds)        # ours added
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$V python -m pytest tests/unit/adapters/test_settings_merge.py::test_merge_pre_tool_use_respects_custom_matcher_and_marker -v`
Expected: FAIL — `merge_pre_tool_use_hook() got an unexpected keyword argument 'matcher'`.

- [ ] **Step 3: Generalize the merge functions.** In `_settings_merge.py`:

Change `merge_pre_tool_use_hook` signature + body to thread the two values:

```python
def merge_pre_tool_use_hook(
    settings_path: Path,
    *,
    command: str,
    matcher: str = _MATCHER,
    marker: str = _OURS_MARKER,
) -> None:
    existed = settings_path.exists()
    original = _read_settings(settings_path) if existed else {}
    settings = copy.deepcopy(original)
    hooks = _ensure_hooks_dict(settings)
    pre_tool_use = _ensure_pre_tool_use_list(hooks)

    _strip_entries(pre_tool_use, marker)
    pre_tool_use.append(_hook_entry(command, matcher))

    if settings == original:
        return
    if existed:
        _write_backup(settings_path)
    else:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
```

Change `merge_session_start_hook` to accept a `marker` kwarg (default `_SESSION_OURS_MARKER`) and pass it to `_strip_entries(session_start, marker)` (replacing the `_strip_session_start_entries` call).

Change `_hook_entry` to take the matcher:

```python
def _hook_entry(command: str, matcher: str = _MATCHER) -> dict[str, Any]:
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command, "timeout": _TIMEOUT}],
    }
```

Leave `_strip_super_harness_entries` / `_strip_session_start_entries` defined (harmless) OR delete them if now unused — verify no other caller with `grep -rn "_strip_super_harness_entries\|_strip_session_start_entries" src tests` and delete only if zero hits. Keep `_MATCHER`, `_OURS_MARKER`, `_SESSION_OURS_MARKER` as the default constants.

- [ ] **Step 4: Run tests to verify pass + no regression**

Run: `$V python -m pytest tests/unit/adapters/test_settings_merge.py -v`
Expected: PASS — both new tests AND every pre-existing test (Claude defaults unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/agent/_settings_merge.py tests/unit/adapters/test_settings_merge.py
git commit -m "refactor: make _settings_merge agent-neutral (matcher/marker kwargs)"
```

---

## Task 2: Codex shim in `hook_entry.py`

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py`
- Test: `tests/unit/daemon/test_hook_entry.py` (in-process unit) — **add `import io` at top** (not currently imported).
- Test: `tests/integration/daemon/test_hook_entry.py` (subprocess end-to-end) — this is where the pre-existing claude-shim / `main()` / positional regression lives; the codex block contract is proven here too.

> **Heads-up (corrects a wrong assumption):** `tests/unit/daemon/test_hook_entry.py` holds ONLY `_decide` kill-switch tests — it has no claude-shim/`main()`/positional tests and no `import io`. Those regression tests are subprocess-style in `tests/integration/daemon/test_hook_entry.py`. So the in-process unit tests below are a *new* style for the unit file (valid — the functions `sys.exit`), and we ADD a subprocess codex test to the integration file to prove the deny-JSON contract end-to-end (parity with the claude exit-2 integration test).

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/daemon/test_hook_entry.py` (ensure `import io` and `import pytest` are at the top — add `import io` if missing):

```python
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
```

Ensure `import io` and `import pytest` are present at the top of the test module (add `import io` — it is missing).

Also add a **subprocess** end-to-end test to `tests/integration/daemon/test_hook_entry.py` (mirrors the existing `test_claude_code_shim_exits_2_on_block` right above it — reuses that module's `workspace` fixture, `write_state`, `_start_daemon`, `kill_daemon`):

```python
def test_codex_shim_denies_via_stdout_json_on_block(workspace: Path) -> None:
    """Codex shim: blocking state → exit 0 + deny JSON on STDOUT (not stderr/exit-2)."""
    write_state(workspace, "c1", "AWAITING_PLAN_REVIEW")
    _start_daemon(workspace)
    try:
        env = {**os.environ, "SUPER_HARNESS_CHANGE_ID": "c1"}
        stdin = '{"tool_name":"apply_patch","tool_input":{"command":"*** patch"}}'
        result = subprocess.run(
            ["super-harness-hook", "--agent", "codex"],
            cwd=workspace, capture_output=True, env=env,
            input=stdin.encode(), timeout=5.0,
        )
        assert result.returncode == 0, result.stderr.decode()
        out = json.loads(result.stdout.decode())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "AWAITING_PLAN_REVIEW" in out["hookSpecificOutput"]["permissionDecisionReason"]
    finally:
        kill_daemon(workspace)
```

(Ensure `import json` is at the top of the integration module — add if missing.)

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/daemon/test_hook_entry.py -k codex tests/integration/daemon/test_hook_entry.py -k codex -v`
Expected: FAIL — `module 'super_harness.daemon.hook_entry' has no attribute '_run_codex_shim'` (unit) / non-zero or no deny JSON (integration).

- [ ] **Step 3: Implement the shim.** In `hook_entry.py`, add the codex arm in `main()` (inside the existing `if argv[:1] == ["--agent"]:` block, before the unknown-agent fail-open):

```python
        if agent == "claude-code":
            _run_claude_code_shim()
            return
        if agent == "codex":
            _run_codex_shim()
            return
```

Add the shim function (mirrors `_run_claude_code_shim`, but deny-via-stdout-JSON + exit 0):

```python
def _run_codex_shim() -> None:
    """Codex mode: stdin JSON in; deny via stdout JSON `permissionDecision`.

    Codex feeds PreToolUse input as a JSON object on stdin (`tool_name`,
    `tool_input.command`) and treats `hookSpecificOutput.permissionDecision:
    "deny"` printed on stdout as a block (the decision lives in the JSON, not the
    exit code — so we exit 0). Malformed / non-object / missing tool → fail-open
    ALLOW (exit 0, no output). Codex gives a `command`, not a `file_path`, so the
    gate decides on lifecycle state with `file=None`.
    """
    import json

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool = data.get("tool_name") or ""
    if not tool:
        sys.exit(0)

    decision, reason = _decide(tool, None)
    if decision == "block":
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"super-harness: {reason} — escape hatch: "
                        f"touch .harness/gate-disabled to disable the gate"
                    ),
                }
            },
            sys.stdout,
        )
        sys.exit(0)
    sys.exit(0)
```

Also update the module docstring's "Two invocation modes" note to mention the codex mode (deny-JSON + exit 0).

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/daemon/test_hook_entry.py tests/integration/daemon/test_hook_entry.py -v`
Expected: PASS — the new codex unit + integration tests, AND the pre-existing claude-shim/`main()`/positional regression (which lives in the integration file).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/unit/daemon/test_hook_entry.py tests/integration/daemon/test_hook_entry.py
git commit -m "feat: codex PreToolUse shim (deny via stdout permissionDecision)"
```

---

## Task 3: `AgentAdapter` install-detail methods + Claude impl

**Files:**
- Modify: `src/super_harness/adapters/__init__.py`
- Modify: `src/super_harness/adapters/agent/claude_code.py`
- Test: `tests/unit/adapters/test_claude_code.py`

- [ ] **Step 1: Write the failing test** — append to `tests/unit/adapters/test_claude_code.py`:

```python
def test_claude_adapter_install_detail_strings():
    from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

    a = ClaudeCodeAdapter()
    assert a.local_config_relpath() == ".claude/settings.local.json"
    assert a.installed_detail() == "PreToolUse gate hook registered in .claude/settings.local.json"
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/adapters/test_claude_code.py::test_claude_adapter_install_detail_strings -v`
Expected: FAIL — `'ClaudeCodeAdapter' object has no attribute 'local_config_relpath'`.

- [ ] **Step 3: Add the two non-abstract methods to the ABC.** In `adapters/__init__.py`, inside `AgentAdapter`, after `on_uninstall` (these are additive defaults — exactly the `watch_paths`/`spec_paths` pattern, NOT `@abstractmethod`):

```python
    def local_config_relpath(self) -> str:  # noqa: B027
        """Workspace-relative path of the per-machine hook config this adapter
        writes (e.g. ``.claude/settings.local.json``). Default ``""`` = none.
        Used only for CLI install messaging."""
        return ""

    def installed_detail(self) -> str:
        """One-line post-install summary for the CLI (e.g. where the gate hook
        landed + any required follow-up). Default is generic."""
        return "agent hooks registered"
```

In `claude_code.py`, add to `ClaudeCodeAdapter`:

```python
    def local_config_relpath(self) -> str:
        return ".claude/settings.local.json"

    def installed_detail(self) -> str:
        return "PreToolUse gate hook registered in .claude/settings.local.json"
```

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/adapters/test_claude_code.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/__init__.py src/super_harness/adapters/agent/claude_code.py tests/unit/adapters/test_claude_code.py
git commit -m "feat: adapter-driven install-detail methods on AgentAdapter"
```

---

## Task 4: De-Claude-ify `cli/adapter.py` install message

**Files:**
- Modify: `src/super_harness/cli/adapter.py:141-226`
- Test: `tests/integration/cli/test_adapter_install.py`

- [ ] **Step 1: Write the failing test** — append to `tests/integration/cli/test_adapter_install.py`. Use the exact `CliRunner` pattern already in that module (`import shutil` + `from click.testing import CliRunner` + `from super_harness.cli import main` are already imported there):

```python
def test_install_message_is_adapter_driven_not_claude_hardcoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex install announces .codex/hooks.json, never .claude/ paths."""
    (tmp_path / ".harness").mkdir()
    (tmp_path / ".codex").mkdir()  # codex adapter detect()s on .codex/
    monkeypatch.setattr(shutil, "which", lambda _name: f"/usr/local/bin/{_name}")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "adapter", "install", "codex"],
    )
    assert r.exit_code == 0, r.output
    assert ".codex/hooks.json" in r.output
    assert ".claude/" not in r.output
```

Note: this end-to-end test needs Task 5's registration to resolve `codex`; the *messaging code* under test is added in Step 3 here. If executing strictly in order, it fails until Task 5 lands — re-run it at the end of Task 5 (Task 5 Step 4 already does).

- [ ] **Step 2: Run to verify fail** (after Task 5, or expect the `get_builtin` assert to fail now)

Run: `$V python -m pytest tests/integration/cli/test_adapter_install.py -k adapter_driven -v`
Expected: FAIL.

- [ ] **Step 3: Replace the hardcoded message block.** In `cli/adapter.py`, rename `created_claude_dir` → `created_config_dir`. There are THREE occurrences: `:141` (init `created_claude_dir = False`), `:145` (`created_claude_dir = not adapter.detect(root)`), and `:217` (inside the message block). Rename the first two by hand; the third is replaced wholesale by the new block below. **After editing, `grep -n created_claude_dir src/super_harness/cli/adapter.py` MUST return zero** (a leftover → `NameError` at runtime). Then replace lines 216-226:

```python
    if not ctx.obj.get("quiet"):
        if isinstance(adapter, AgentAdapter):
            rel = adapter.local_config_relpath()
            if created_config_dir and rel:
                parent = rel.rsplit("/", 1)[0] if "/" in rel else rel
                click.echo(f"Created {rel} (no {parent}/ existed).")
            detail = adapter.installed_detail()
        else:
            detail = "framework adapter registered"
        click.echo(
            f"Installed {name} adapter ({kind}): {detail}; "
            f"recorded in .harness/adapters.yaml."
        )
    sys.exit(EXIT_OK)
```

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/integration/cli/test_adapter_install.py -v`
Expected: PASS (the existing claude-code install test still sees its `.claude/...` detail via `installed_detail()`).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/cli/adapter.py tests/integration/cli/test_adapter_install.py
git commit -m "fix: adapter-driven install message (no .claude hardcode for all agents)"
```

---

## Task 5: `CodexAdapter` + registry registration

**Files:**
- Create: `src/super_harness/adapters/agent/codex.py`
- Modify: `src/super_harness/adapters/registry.py:35,260`
- Test: `tests/unit/adapters/test_codex.py` (create), `tests/unit/adapters/test_registry.py`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/adapters/test_codex.py`:

```python
import json
import shutil

import pytest

from super_harness.adapters.agent.codex import CodexAdapter


def test_detect_requires_codex_dir(tmp_path):
    a = CodexAdapter()
    assert a.detect(tmp_path) is False
    (tmp_path / ".codex").mkdir()
    assert a.detect(tmp_path) is True


def test_install_writes_pre_tool_use_and_session_start(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    pre = data["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "^(apply_patch|Edit|Write)$"
    assert pre["hooks"][0]["command"] == "/abs/super-harness-hook --agent codex"
    ss = data["hooks"]["SessionStart"][0]
    assert ss["hooks"][0]["command"] == "/abs/super-harness change resume"


def test_install_aborts_when_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)
    (tmp_path / ".codex").mkdir()
    with pytest.raises(RuntimeError):
        CodexAdapter().install_hooks(tmp_path)
    assert not (tmp_path / ".codex" / "hooks.json").exists()  # no write before abort


def test_install_does_not_touch_gitignore(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    assert not (tmp_path / ".gitignore").exists()  # gitignore is sync/init's job


def test_install_detail_and_relpath():
    a = CodexAdapter()
    assert a.local_config_relpath() == ".codex/hooks.json"
    assert "/hooks" in a.installed_detail()  # trust reminder present


def test_agents_md_subsection_has_trust_and_caveat():
    sub = CodexAdapter().agents_md_subsection()
    assert "/hooks" in sub  # trust step
    assert "apply_patch" in sub
    assert "WebSearch" in sub  # coverage caveat


def test_on_uninstall_restores_earliest_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": []}}))  # pristine user file
    CodexAdapter().install_hooks(tmp_path)  # writes a backup of pristine
    CodexAdapter().on_uninstall(tmp_path)
    assert json.loads(hooks.read_text()) == {"hooks": {"PreToolUse": []}}
```

Add to `tests/unit/adapters/test_registry.py`:

```python
def test_codex_is_registered():
    from super_harness.adapters.registry import get_builtin
    from super_harness.adapters.agent.codex import CodexAdapter
    assert get_builtin("codex") is CodexAdapter
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/adapters/test_codex.py tests/unit/adapters/test_registry.py::test_codex_is_registered -v`
Expected: FAIL — `No module named 'super_harness.adapters.agent.codex'`.

- [ ] **Step 3: Create `CodexAdapter`.** Write `src/super_harness/adapters/agent/codex.py`:

```python
"""AgentAdapter for OpenAI Codex CLI (portability axis B).

Registers a PreToolUse hook (deny via stdout `permissionDecision`) + a
SessionStart hook (stdout = developer context) into `<repo>/.codex/hooks.json`,
reusing the agent-neutral `_settings_merge`. Codex's hooks.json has the same
shape as Claude's settings.json hooks block; only the matcher + marker differ.

Trust caveat: Codex skips new/changed hooks until a human runs `/hooks` to trust
them — the gate is INACTIVE until then. See design 2026-06-25 §4.3.

API stability: experimental (v0.1).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from super_harness.adapters import AgentAdapter
from super_harness.adapters.agent._settings_merge import (
    merge_pre_tool_use_hook,
    merge_session_start_hook,
)

__all__ = ["CodexAdapter"]

_HOOK_BINARY = "super-harness-hook"
_CLI_BINARY = "super-harness"
_CODEX_MATCHER = "^(apply_patch|Edit|Write)$"
_CODEX_MARKER = "--agent codex"

_AGENTS_MD_BEGIN = "<!-- super-harness agent: codex -->"
_AGENTS_MD_END = "<!-- /super-harness agent: codex -->"
_AGENTS_MD_SUBSECTION = f"""{_AGENTS_MD_BEGIN}
### super-harness (Codex)

A **PreToolUse** hook gates this workspace. `apply_patch` edits are blocked by
super-harness when the current change state forbids the mutation (deterministic
gate enforcement). `Bash` is never gated, so the kill-switch always works.

**REQUIRED trust step:** after `adapter install codex`, the gate is INACTIVE
until you run `/hooks` in Codex and trust the super-harness hook. Codex skips
new/changed hooks until trusted (trust is keyed to the hook's hash); if you
reinstall or relocate the binary, re-trust it. On a pre-existing repo also run
`super-harness sync --gitignore` so `.codex/hooks.json` is ignored.

**Coverage caveat:** Codex PreToolUse intercepts only simple shell + `apply_patch`
— it does NOT see `WebSearch` or other non-shell/non-MCP tools, so real-time
coverage is narrower than Claude Code's. The CI cold floor backs the gap.

When a tool call is blocked:
- Run `super-harness status` to see the change, its state, and the next step.
- Resume context with `super-harness change resume <change_id>`.
- Escape hatch (if the gate is wrong): `touch .harness/gate-disabled` to disable,
  `rm .harness/gate-disabled` to re-enable (the gate never blocks `Bash`).

#### Review protocol

super-harness does NOT review for you — it enforces (via the gate) that a review
verdict is recorded before the lifecycle proceeds, and YOU produce the verdict.
Run `super-harness status <change>` to see the required reviewer + strategy, then
record verdicts with `super-harness review approve/reject <change> --reviewer
<name>` (code-reviewer approval requires a `--verdict-file` from a genuinely
independent reviewer subagent; see `super-harness status` output). Run a real
independent reviewer — don't self-rubber-stamp.
{_AGENTS_MD_END}"""


class CodexAdapter(AgentAdapter):
    name: ClassVar[str] = "codex"
    version: ClassVar[str] = "0.1.0"
    capabilities: ClassVar[dict[str, bool]] = {
        "pre_tool_use_hook": True,
        "post_tool_use_hook": False,
        "session_start_hook": True,
        "session_end_hook": False,
        "pre_commit_hook": False,
        "rules_file_injection": True,
        "mcp_server": True,
        "subprocess_execution": True,
    }

    def detect(self, workspace: Path) -> bool:
        return (workspace / ".codex").is_dir()

    def install_hooks(self, workspace: Path) -> None:
        resolved_hook = shutil.which(_HOOK_BINARY)
        if resolved_hook is None:
            raise RuntimeError(
                f"{_HOOK_BINARY} not found on PATH; reinstall super-harness "
                f"(e.g. `pipx reinstall super-harness`) before installing the "
                f"Codex adapter."
            )
        resolved_cli = shutil.which(_CLI_BINARY)
        if resolved_cli is None:
            raise RuntimeError(
                f"{_CLI_BINARY} not found on PATH; reinstall super-harness "
                f"(e.g. `pipx reinstall super-harness`) before installing the "
                f"Codex adapter."
            )

        hooks_path = workspace / ".codex" / "hooks.json"
        pre_command = f"{resolved_hook} --agent codex"
        session_command = f"{resolved_cli} change resume"

        snapshot: str | None = hooks_path.read_text() if hooks_path.exists() else None
        try:
            merge_pre_tool_use_hook(
                hooks_path, command=pre_command,
                matcher=_CODEX_MATCHER, marker=_CODEX_MARKER,
            )
            merge_session_start_hook(hooks_path, command=session_command)
        except BaseException:
            self._restore_snapshot(hooks_path, snapshot)
            raise

    @staticmethod
    def _restore_snapshot(hooks_path: Path, snapshot: str | None) -> None:
        if snapshot is None:
            hooks_path.unlink(missing_ok=True)
        else:
            hooks_path.write_text(snapshot)

    def inject_context(self, change_id: str) -> str:
        result = subprocess.run(
            [_CLI_BINARY, "change", "resume", change_id],
            capture_output=True, text=True, check=False,
        )
        return result.stdout or ""

    def agents_md_subsection(self) -> str:
        return _AGENTS_MD_SUBSECTION

    def local_config_relpath(self) -> str:
        return ".codex/hooks.json"

    def installed_detail(self) -> str:
        return (
            "PreToolUse + SessionStart hooks registered in .codex/hooks.json — "
            "run `/hooks` in Codex to trust the hook before the gate is active"
        )

    def on_uninstall(self, workspace: Path) -> None:
        hooks_path = workspace / ".codex" / "hooks.json"
        backups = sorted(
            hooks_path.parent.glob(f"{hooks_path.name}.super-harness-backup.*"),
            key=_backup_sort_key,
        )
        if not backups:
            return
        hooks_path.write_text(backups[0].read_text())


def _backup_sort_key(path: Path) -> int:
    suffix = path.name.rsplit(".", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return -1
```

In `registry.py`: add the import after the claude import (line ~35):

```python
from super_harness.adapters.agent.codex import CodexAdapter
```

and the registration after `register_builtin("claude-code", ClaudeCodeAdapter)` (line ~260):

```python
register_builtin("codex", CodexAdapter)
```

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/adapters/test_codex.py tests/unit/adapters/test_registry.py -v`
Expected: PASS. Then run Task 4's deferred test: `$V python -m pytest tests/integration/cli/test_adapter_install.py -k adapter_driven -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/agent/codex.py src/super_harness/adapters/registry.py tests/unit/adapters/test_codex.py tests/unit/adapters/test_registry.py
git commit -m "feat: CodexAdapter (PreToolUse + SessionStart) + registry registration"
```

---

## Task 6: Gitignore canonical paths for `.codex/`

**Files:**
- Modify: `src/super_harness/engineering/gitignore_injector.py:70-80`
- Modify: `tests/unit/engineering/test_gitignore_injector.py:30-40` — the test keeps its OWN hardcoded copy of `_CANONICAL_PATHS`; it MUST be updated in lockstep (two tests assert exact equality).
- Modify: root `.gitignore` — regenerated via `sync --gitignore` (the committed block must stay byte-identical to `_render_block()`; `test_committed_repo_gitignore_block_matches_injector` enforces it).

- [ ] **Step 1: Write the failing test** — append:

```python
def test_canonical_block_covers_codex_hook_config():
    from super_harness.engineering.gitignore_injector import _render_block
    body = _render_block()
    assert ".codex/hooks.json" in body
    assert ".codex/*.super-harness-backup.*" in body
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/engineering/test_gitignore_injector.py::test_canonical_block_covers_codex_hook_config -v`
Expected: FAIL.

- [ ] **Step 3: Add the two paths.** In `gitignore_injector.py`, extend `_CANONICAL_PATHS` after the `.claude/...` entries:

```python
    ".claude/settings.local.json",
    ".claude/*.super-harness-backup.*",
    ".codex/hooks.json",
    ".codex/*.super-harness-backup.*",
)
```

(Update the block comment at lines ~60-69 to mention `.codex/` alongside `.claude/`.)

- [ ] **Step 4: Update the test's local copy (MANDATORY — not optional).** `test_gitignore_injector.py:30-40` has its OWN `_CANONICAL_PATHS` tuple that two tests assert exact equality against (`test_block_contains_all_canonical_paths` → `lines == list(_CANONICAL_PATHS)`). Add the same two lines after the `.claude/...` entries in that test copy:

```python
    ".claude/settings.local.json",
    ".claude/*.super-harness-backup.*",
    ".codex/hooks.json",
    ".codex/*.super-harness-backup.*",
)
```

- [ ] **Step 5: Regenerate the committed root `.gitignore` (MANDATORY).** `test_committed_repo_gitignore_block_matches_injector` asserts the committed `.gitignore` block is byte-identical to `_render_block()`, so it fails until the root file is regenerated:

Run: `$V super-harness sync --gitignore`
Then `$V python -m pytest tests/unit/engineering/test_gitignore_injector.py -v`
Expected: PASS (all — including the two exact-equality tests and the committed-block test).

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/engineering/gitignore_injector.py tests/unit/engineering/test_gitignore_injector.py .gitignore
git commit -m "feat: gitignore .codex/ hook config + backups"
```

---

## Task 7: Regenerate AGENTS.md + record deferrals + full-suite green

**Files:**
- Modify: `AGENTS.md` (regenerated)
- Modify: `private/OPEN-ITEMS.md`

- [ ] **Step 1: Regenerate AGENTS.md.** The Codex subsection is injected when the codex adapter is installed in THIS repo, but the committed `AGENTS.md` is regenerated from the adapter sources via sync. Run:

```bash
$V super-harness sync --agents-md
$V super-harness sync --check
$V super-harness doc check
```

Expected: `sync --check` and `doc check` exit 0. If `sync --agents-md` changed `AGENTS.md`, that change is in-scope.

- [ ] **Step 2: Record deferrals** — append to `private/OPEN-ITEMS.md` under the appropriate section:

```markdown
- [DOABLE-NOW, deferred] Codex adapter: file-level scope enforcement — the shim
  passes file=None (Codex PreToolUse gives `tool_input.command`, not a clean
  file_path). Gate is state-only for Codex. Extract edited paths from an
  apply_patch body to enable per-file scope. (2026-06-25 codex-agent-adapter)
- [BLOCKED-on-Codex] Wider Codex real-time coverage — PreToolUse covers only
  simple shell + apply_patch, not WebSearch / non-shell-non-MCP tools. Upstream
  limitation; CI cold floor backs the gap.
- [DOABLE-NOW, deferred] Codex PostToolUse hook (result inspection) — not wired
  this cut.
```

- [ ] **Step 3: Full suite green**

Run: `$V ruff check src tests && $V mypy src && $V python -m pytest -q`
Expected: all green.

- [ ] **Step 4: REQUIRED manual smoke (pre-trust release gate).** This is NOT optional and NOT auto-testable — it is the only proof the deny contract actually blocks:
  1. In a scratch repo with `.codex/` + super-harness initialized, `super-harness adapter install codex`.
  2. Start a Codex session; run `/hooks` and trust the super-harness hook.
  3. Put the change in a gate-blocking state (e.g. `INTENT_DECLARED`, before plan approval).
  4. Ask Codex to edit a file (triggers `apply_patch`). CONFIRM the edit is denied with the super-harness reason surfaced.
  5. `touch .harness/gate-disabled`; confirm the edit now proceeds. Record the result in the change's smoke note.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md private/OPEN-ITEMS.md
git commit -m "docs: regen AGENTS.md (codex subsection) + record codex deferrals"
```

---

## Self-host merge sequence (after all tasks green)

Per project discipline (NEXT-SESSION + `project-self-host-pr-attest-scope`): the scope MUST cover every vs-main changed file (the two design/plan docs included).

```bash
super-harness change start codex-agent-adapter
# Scope MUST cover EVERY vs-main changed file. Do NOT trust a prose count —
# enumerate from `git diff --name-only main` at plan-ready time. It includes:
#   8 src files (_settings_merge, hook_entry, codex, adapters/__init__,
#     claude_code, cli/adapter, registry, gitignore_injector)
#   + root .gitignore
#   + 7 test files (test_settings_merge, unit+integration test_hook_entry,
#     test_codex, test_registry, test_gitignore_injector, test_adapter_install)
#   + AGENTS.md + private/OPEN-ITEMS.md
#   + docs/plans/2026-06-25-codex-agent-adapter-design.md + -implementation.md
super-harness plan ready codex-agent-adapter --tier-hint Normal --scope '[<enumerate from git diff --name-only main>]'
super-harness review approve codex-agent-adapter --reviewer plan-reviewer
super-harness implementation start codex-agent-adapter
# ... implement Tasks 1-7, full suite green ...
super-harness done codex-agent-adapter            # NOTE: pass the slug explicitly
super-harness review prepare codex-agent-adapter --reviewer code-reviewer
# independent reviewer subagent runs the checklist (incl. doc-impact) → verdict file
super-harness review approve codex-agent-adapter --reviewer code-reviewer --verdict-file <path>
super-harness attest write codex-agent-adapter && git add .harness && git commit -m "chore: attestation"
super-harness attest verify --base main --head HEAD       # local dry-run
git push -u origin <branch>
gh pr create   # token lacks read:org → write title/body correctly first time
# CI green → squash merge →
super-harness on-merge --commit <sha> --change codex-agent-adapter
```

---

## Self-Review (completed)

- **Spec coverage:** §3.1 shim → Task 2; §3.2 CodexAdapter → Task 5; §3.3 generalized merge → Task 1; §3.4 gitignore → Task 6; §3.5 AGENTS.md subsection → Task 5 (+ regen Task 7); §3.6 CLI message → Tasks 3+4; §4 limits → AGENTS.md (Task 5) + OPEN-ITEMS (Task 7); §5 tests → every task's tests + Task 7 smoke; §6 files → all covered; §7 verified facts → encoded in matcher/shim/merge. No gaps.
- **Type/name consistency:** `_run_codex_shim`, `local_config_relpath()`, `installed_detail()`, `_CODEX_MATCHER`/`_CODEX_MARKER`, `merge_pre_tool_use_hook(matcher=, marker=)` used identically across tasks.
- **Ordering note:** Task 4's end-to-end assertion depends on Task 5's registration — flagged inline; the messaging CODE is added in Task 4, verified after Task 5.
- **No placeholders:** every code step shows real code; the only `<...>` are the scope file list (intentional, filled at merge time) and the smoke-test repo specifics.
