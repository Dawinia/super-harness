# Authoring-time conformance feedback (Stop hook, cut-1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Claude Code turn ends, run the ratified, authoring-opted-in tier-1 decision checks once; if one fails, block the stop and feed the deterministic verdict back so the agent self-corrects before the human / merge gate.

**Architecture:** A Stop hook on `super-harness-hook` runs an agent-agnostic verdict core (`core/authoring_check.py`, reusing `load_decisions` + `run_executable_checks`) once per turn. The per-agent delivery is one `AgentAdapter.format_stop_feedback(verdict)` method. Loop-safe (`stop_hook_active`), fail-open, opt-in per decision (`authoring_time: true`). The merge-gate `decision check` is unchanged and remains the floor.

**Tech Stack:** Python 3.10+, pytest. Reuses `core.decisions`, `core.check_runner`, `adapters.agent.claude_code`, `adapters.agent._settings_merge`, `daemon.hook_entry`. Verify with `PATH="$(pwd)/.venv/bin:$PATH" pytest ...` (never `uv run`).

**Design doc:** `docs/plans/2026-07-01-authoring-time-conformance-sensor-design.md` (Rev 2). H was validated by a LIVE stub before this plan; the bite-test (Task 8) re-tests it on a **transitive** violation.

---

## File Structure

**Create:**
- `src/super_harness/core/authoring_check.py` — agnostic verdict core: `Verdict`, `Violation`, `run_authoring_check(root) -> Verdict`. Reuses `run_executable_checks`; adds tri-state (`unavailable` filtering) + `authoring_time` opt-in filtering. No agent knowledge, no prose.
- `tests/unit/core/test_authoring_check.py`
- `tests/integration/daemon/test_hook_entry_stop.py`

**Modify:**
- `src/super_harness/core/decisions.py` — add `authoring_time: bool = False` frontmatter field (parse + serialize round-trip).
- `docs/decisions/d-core-is-base.md` — add `authoring_time: true` frontmatter (no body change → hash unaffected).
- `src/super_harness/adapters/agent/_settings_merge.py` — add `merge_stop_hook` (mirror the matcher-less `merge_session_start_hook`).
- `src/super_harness/adapters/__init__.py` — add `format_stop_feedback(verdict) -> str` (default `""`) + shared `_render_advisory(verdict) -> str` to `AgentAdapter`.
- `src/super_harness/adapters/agent/claude_code.py` — override `format_stop_feedback`; register the Stop hook in `install_hooks`; switch `on_uninstall` to marker-strip.
- `src/super_harness/daemon/hook_entry.py` — add `--event stop` claude-code path (loop-safe, fail-open, honors kill switch).

---

## Task 1: `authoring_time` opt-in frontmatter on Decision

**Files:**
- Modify: `src/super_harness/core/decisions.py` (Decision dataclass, `parse_decision_file`, `serialize_decision`)
- Test: `tests/unit/core/test_decisions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_decisions.py
from pathlib import Path
from super_harness.core.decisions import parse_decision_file, serialize_decision

def test_authoring_time_parsed_and_roundtrips(tmp_path: Path):
    p = tmp_path / "d-x.md"
    p.write_text("---\nid: d-x\nstatus: ratified\nauthoring_time: true\n---\nbody\n")
    d = parse_decision_file(p)
    assert d.authoring_time is True
    assert "authoring_time" in serialize_decision(d)

def test_authoring_time_absent_defaults_false(tmp_path: Path):
    p = tmp_path / "d-y.md"
    p.write_text("---\nid: d-y\nstatus: ratified\n---\nbody\n")
    assert parse_decision_file(p).authoring_time is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -k authoring_time -v`
Expected: FAIL — `Decision` has no `authoring_time`.

- [ ] **Step 3: Implement**

In `decisions.py`, add to the `Decision` dataclass:

```python
    authoring_time: bool = False
```

In `parse_decision_file`, in the `Decision(...)` constructor:

```python
        authoring_time=bool(data.get("authoring_time", False)),
```

In `serialize_decision`, after the reconciled_anchors block and before `fm_text = ...`:

```python
    if decision.authoring_time:
        fm["authoring_time"] = True
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -k authoring_time -v`
Expected: PASS.

- [ ] **Step 5: Opt the real decision in (frontmatter only — body hash unchanged)**

Edit `docs/decisions/d-core-is-base.md` frontmatter, add after `id: d-core-is-base` (do NOT touch anything below the closing `---`):

```yaml
authoring_time: true
```

- [ ] **Step 6: Verify integrity gate still clean**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check`
Expected: no integrity/hash violation for `d-core-is-base` (frontmatter is outside `compute_body_hash`).

- [ ] **Step 7: Commit**

```bash
git add src/super_harness/core/decisions.py tests/unit/core/test_decisions.py docs/decisions/d-core-is-base.md
git commit -m "feat(decisions): authoring_time opt-in frontmatter for the interactive loop"
```

---

## Task 2: Verdict core (`authoring_check.py`)

**Files:**
- Create: `src/super_harness/core/authoring_check.py`
- Test: `tests/unit/core/test_authoring_check.py`

Reuses `load_decisions` (returns `(list[Decision], errors)`) and `run_executable_checks` (returns `list[CheckFailure(id, exit_code, detail)]`, already skips non-ratified + `check is None`). Adds the `authoring_time` filter and the tri-state (`exit_code == -1` = `unavailable`, not a violation).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_authoring_check.py
from pathlib import Path
import textwrap
from super_harness.core.authoring_check import run_authoring_check, Verdict, Violation

def _write_decision(root: Path, id: str, check: str, authoring: bool):
    d = root / "docs" / "decisions"; d.mkdir(parents=True, exist_ok=True)
    at = "authoring_time: true\n" if authoring else ""
    (d / f"{id}.md").write_text(textwrap.dedent(f"""\
        ---
        id: {id}
        status: ratified
        {at}---
        body
        ```check
        {check}
        ```
        ```counterexample path=src/_ce.py
        x = 1
        ```
        """))

def test_failing_opted_in_check_is_a_violation(tmp_path: Path):
    _write_decision(tmp_path, "d-fail", check="false", authoring=True)
    v = run_authoring_check(tmp_path)
    assert [x.decision_id for x in v.violations] == ["d-fail"]
    assert v.violations[0].decision_doc_path == "docs/decisions/d-fail.md"

def test_not_opted_in_is_skipped(tmp_path: Path):
    _write_decision(tmp_path, "d-fail", check="false", authoring=False)
    assert run_authoring_check(tmp_path).violations == []

def test_passing_check_is_clean(tmp_path: Path):
    _write_decision(tmp_path, "d-ok", check="true", authoring=True)
    assert run_authoring_check(tmp_path).violations == []

def test_unavailable_is_not_a_violation():
    # `unavailable` = timeout/spawn (runner returns exit_code -1) OR the check TOOL is
    # missing/not-executable (shell exits 126/127 — e.g. `lint-imports` not installed).
    # A real nonzero (e.g. 1) IS a violation. Assert the filter directly.
    from super_harness.core.authoring_check import _to_violations
    from super_harness.core.check_runner import CheckFailure
    fails = [CheckFailure(id="d-timeout", exit_code=-1, detail="timeout"),
             CheckFailure(id="d-missing", exit_code=127, detail="lint-imports: not found"),
             CheckFailure(id="d-b", exit_code=1, detail="real")]
    ids = [v.decision_id for v in _to_violations(fails)]
    assert ids == ["d-b"]   # -1 (timeout/spawn) and 126/127 (tool missing) filtered out
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_authoring_check.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

```python
# src/super_harness/core/authoring_check.py
"""Authoring-time conformance verdict (design 2026-07-01, Rev 2).

Agent-agnostic: run the ratified, authoring-opted-in tier-1 decision checks once and
return a structured Verdict. Reused by the Stop-hook path. No agent knowledge, no
prose, no daemon. This is deliberately NOT a `Sensor` (no dispatcher / event emission)
— it is a synchronous verdict producer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from super_harness.core.check_runner import CheckFailure, run_executable_checks
from super_harness.core.decisions import load_decisions

# Inner check budget; MUST stay strictly below the hook's outer timeout
# (adapters.agent._settings_merge._TIMEOUT = 10s) so a slow graph degrades to
# `unavailable` (silent) rather than a hard kill (design §5).
AUTHORING_CHECK_TIMEOUT = 8


@dataclass(frozen=True)
class Violation:
    decision_id: str
    detail: str            # the check's own output (CheckFailure.detail)
    decision_doc_path: str


@dataclass(frozen=True)
class Verdict:
    violations: list[Violation]


# Exit codes that mean "the check could not run" (NOT a violation): timeout/spawn
# failure (runner sentinel -1) and tool-not-found / not-executable under shell=True
# (126/127, e.g. `lint-imports` absent). Design §3/§5: never emit a false "you violated".
_UNAVAILABLE_EXIT_CODES = frozenset({-1, 126, 127})


def _to_violations(failures: list[CheckFailure]) -> list[Violation]:
    """Map real check failures to violations, dropping `unavailable` results (a check
    that could not run is NOT 'you violated X' — design §3)."""
    return [
        Violation(
            decision_id=f.id,
            detail=f.detail,
            decision_doc_path=f"docs/decisions/{f.id}.md",
        )
        for f in failures
        if f.exit_code not in _UNAVAILABLE_EXIT_CODES
    ]


def _integrity_ok(d) -> bool:
    """True if the decision's body still matches its ratified hash. Mirror the CI floor
    (cli/decision.py): a tamper-detected decision must NOT have its arbitrary shell check
    run automatically in the interactive loop (design §4 trust control)."""
    from super_harness.core.decisions import compute_body_hash
    if not d.ratified_text_hash:
        return True
    return compute_body_hash(d.body) == d.ratified_text_hash


def run_authoring_check(workspace_root: Path) -> Verdict:
    """Run the ratified, `authoring_time`, integrity-clean tier-1 checks once; return a Verdict.

    Only decisions that opted into the interactive loop (`authoring_time: true`) AND whose
    body still matches their ratified hash run — the safety control (design §4). Never
    raises for a check failure (failure is data).
    """
    decisions, _errors = load_decisions(workspace_root)
    opted = [d for d in decisions if d.authoring_time and _integrity_ok(d)]
    if not opted:
        return Verdict(violations=[])
    # run_executable_checks already skips non-ratified + `check is None`.
    failures = run_executable_checks(workspace_root, opted, timeout=AUTHORING_CHECK_TIMEOUT)
    return Verdict(violations=_to_violations(failures))
```

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_authoring_check.py -v`
Expected: PASS (all).

- [ ] **Step 5: Assert core stays adapter-free (guard the layering)**

Add to `tests/unit/core/test_authoring_check.py`:

```python
def test_core_module_imports_no_adapters():
    import super_harness.core.authoring_check as m
    src = Path(m.__file__).read_text()
    assert "adapters" not in src   # core must not import the adapter layer (core-is-base)
```

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_authoring_check.py -k adapters_free -v` (rename to match) → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/core/authoring_check.py tests/unit/core/test_authoring_check.py
git commit -m "feat(core): authoring-time conformance verdict (reuse run_executable_checks, tri-state)"
```

---

## Task 3: `merge_stop_hook` in settings-merge (mirror the matcher-less session_start helper)

**Files:**
- Modify: `src/super_harness/adapters/agent/_settings_merge.py`
- Test: `tests/unit/adapters/test_settings_merge.py`

**Read first:** open `_settings_merge.py` and read `merge_session_start_hook` end-to-end. Stop hooks (like SessionStart) take **no matcher**, so `merge_stop_hook` mirrors `merge_session_start_hook` exactly — NOT the PreToolUse helper. Reuse the existing generic `_ensure_event_list(hooks, event)` and the file's real loader/backup/write logic (the idempotent write is **inlined** in each merge fn: existed-guard → `_read_settings` or `{}` → deepcopy → strip-by-marker → append → `if settings == original: return` → conditional `_write_backup` → `write_text`). Do NOT invent `_load_settings` / `_write_if_changed` — they do not exist.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/adapters/test_settings_merge.py
import json
from pathlib import Path
from super_harness.adapters.agent._settings_merge import merge_stop_hook

def test_merge_stop_adds_entry(tmp_path: Path):
    hooks = tmp_path / "settings.json"
    merge_stop_hook(hooks, command="/abs/super-harness-hook --agent claude-code --event stop")
    data = json.loads(hooks.read_text())
    entries = data["hooks"]["Stop"]
    assert any("--event stop" in h["command"] for e in entries for h in e["hooks"])
    # Stop entries carry NO matcher (mirror SessionStart)
    assert all("matcher" not in e for e in entries)

def test_merge_stop_preserves_existing_hooks(tmp_path: Path):
    hooks = tmp_path / "settings.json"
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Edit", "hooks": [{"type": "command", "command": "keepme"}]}]}}))
    merge_stop_hook(hooks, command="/abs/super-harness-hook --agent claude-code --event stop")
    data = json.loads(hooks.read_text())
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "keepme"
    assert "Stop" in data["hooks"]

def test_merge_stop_idempotent(tmp_path: Path):
    hooks = tmp_path / "settings.json"
    cmd = "/abs/super-harness-hook --agent claude-code --event stop"
    merge_stop_hook(hooks, command=cmd)
    first = hooks.read_text()
    merge_stop_hook(hooks, command=cmd)
    assert hooks.read_text() == first   # no duplicate entry, stable output
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_settings_merge.py -k stop -v`
Expected: FAIL — `merge_stop_hook` undefined.

- [ ] **Step 3: Implement (mirror `merge_session_start_hook`)**

Add `"merge_stop_hook"` to `__all__`. Add a `_STOP_MARKER` constant (a stable substring identifying our Stop command, e.g. `"--event stop"`). Copy the body of `merge_session_start_hook` verbatim and change: the event list to `_ensure_event_list(hooks, "Stop")`, the marker to `_STOP_MARKER`, and the appended entry to the matcher-less command entry (reuse the same entry-builder `merge_session_start_hook` uses — e.g. `_session_start_entry(command)` or the shared no-matcher builder; if it is session-specific, add a tiny `_stop_entry(command)` mirroring it with the Stop command + `_TIMEOUT`). Signature: `merge_stop_hook(settings_path: Path, *, command: str, marker: str = _STOP_MARKER) -> None` — mirror the real `merge_session_start_hook` parameter name (`settings_path`) and its inlined existed-guard/backup/write structure exactly.

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_settings_merge.py -k stop -v`
Expected: PASS.

- [ ] **Step 5: Full settings-merge suite (no regression to pre/session merges)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_settings_merge.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/adapters/agent/_settings_merge.py tests/unit/adapters/test_settings_merge.py
git commit -m "feat(adapters): merge_stop_hook (matcher-less, mirrors session_start)"
```

---

## Task 4: Adapter seam + Claude Code delivery + install/uninstall

**Files:**
- Modify: `src/super_harness/adapters/__init__.py` (ABC: `format_stop_feedback` default + shared `_render_advisory`)
- Modify: `src/super_harness/adapters/agent/claude_code.py` (override + install Stop hook + marker-strip uninstall)
- Test: `tests/unit/adapters/test_claude_code.py`, `tests/unit/adapters/test_protocol.py`

- [ ] **Step 1: Write the failing tests (ABC default + shared render + Claude override)**

```python
# tests/unit/adapters/test_claude_code.py
import json
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.core.authoring_check import Verdict, Violation

def _v():
    return Verdict(violations=[Violation("d-core-is-base",
        "core is not allowed to import super_harness.sensors",
        "docs/decisions/d-core-is-base.md")])

def test_claude_format_stop_feedback_blocks_with_reason():
    out = ClaudeCodeAdapter().format_stop_feedback(_v())
    obj = json.loads(out)
    assert obj["decision"] == "block"
    assert "d-core-is-base" in obj["reason"]
    assert "super_harness.sensors" in obj["reason"]
    assert "docs/decisions/d-core-is-base.md" in obj["reason"]

def test_claude_format_stop_feedback_clean_is_empty():
    assert ClaudeCodeAdapter().format_stop_feedback(Verdict(violations=[])) == ""
```

```python
# tests/unit/adapters/test_protocol.py  (add)
def test_default_format_stop_feedback_is_empty():
    from super_harness.adapters import AgentAdapter
    from super_harness.core.authoring_check import Verdict, Violation
    from pathlib import Path

    class _Bare(AgentAdapter):
        name = "bare"; version = "0.1.0"; capabilities = {}
        def detect(self, w: Path) -> bool: return False
        def install_hooks(self, w: Path) -> None: ...
        def inject_context(self, c: str) -> str: return ""
        def agents_md_subsection(self) -> str: return ""
    v = Verdict(violations=[Violation("d", "x", "docs/decisions/d.md")])
    assert _Bare().format_stop_feedback(v) == ""   # floor-only default
```

- [ ] **Step 2: Run to verify they fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_claude_code.py -k stop_feedback tests/unit/adapters/test_protocol.py -k format_stop -v`
Expected: FAIL — method undefined.

- [ ] **Step 3: Implement the ABC default + shared renderer**

In `adapters/__init__.py`, add to `AgentAdapter` (non-abstract, like `on_uninstall`):

```python
    def format_stop_feedback(self, verdict: "Verdict") -> str:
        """Format a turn-end conformance verdict for this agent's Stop-hook feedback
        channel; return "" to deliver nothing.

        Default = floor-only: agents whose Stop hook cannot feed text back to the model
        do not override this and rely on the CI cold-path floor. Agents that can
        (Claude Code, Codex) override it. Takes the STRUCTURED verdict so an agent can
        choose channel/fields; use `_render_advisory` for the shared prose."""
        return ""

    @staticmethod
    def _render_advisory(verdict: "Verdict") -> str:
        """Shared agent-agnostic advisory prose (design §3b): decision id + the check's
        own detail + decision-doc pointer. No fabricated fix text."""
        lines = [
            "super-harness authoring-time check — a ratified decision's check is failing "
            "for your changes:",
        ]
        for v in verdict.violations:
            lines.append(f"  • {v.decision_id}: {v.detail}")
            lines.append(f"    (rule + counterexample: {v.decision_doc_path})")
        lines.append(
            "Correct it before finishing this turn; the merge gate will otherwise reject "
            "it later. (If this is a deliberate, disclosed exception, proceed.)"
        )
        return "\n".join(lines)
```

Add the TYPE_CHECKING import for `Verdict` at the top of `adapters/__init__.py`:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from super_harness.core.authoring_check import Verdict
```

(Note: `core.authoring_check` imports only `core`, and this is a TYPE_CHECKING-only import in the adapter layer, so no runtime `core → adapters` or `adapters → core` cycle is introduced at import time.)

- [ ] **Step 4: Implement the Claude Code override**

In `claude_code.py`, add to `ClaudeCodeAdapter`:

```python
    def format_stop_feedback(self, verdict) -> str:
        """Block the stop and feed the advisory back via Claude Code's Stop-hook JSON
        protocol: `{"decision":"block","reason": ...}` (the reason reaches the model on
        its next turn). Returns "" when clean (allow the stop)."""
        import json
        if not verdict.violations:
            return ""
        return json.dumps({"decision": "block", "reason": self._render_advisory(verdict)})
```

- [ ] **Step 5: Run to verify format tests pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_claude_code.py -k stop_feedback tests/unit/adapters/test_protocol.py -k format_stop -v`
Expected: PASS.

- [ ] **Step 6: Register the Stop hook in `install_hooks`**

In `claude_code.py`'s `install_hooks`, after the existing PreToolUse + SessionStart merges, add (import `merge_stop_hook` alongside the existing merge imports; use the same `settings_path` + resolved hook binary the PreToolUse install uses):

```python
        merge_stop_hook(
            settings_path,
            command=f"{resolved_hook} --agent claude-code --event stop",
        )
```

- [ ] **Step 7: Do NOT change `on_uninstall` — the existing restore-earliest already removes the Stop hook.**

Cross-review found: `on_uninstall` restores the *earliest* backup, which is the pristine
settings from before merge 1. Adding a third merge (Stop) does not change that — restoring
the pristine backup removes **all** super-harness hooks, Stop included. So **leave
`on_uninstall` untouched** (do NOT switch to marker-strip; that would break the 3 existing
backup round-trip tests and cannot reproduce a pristine `hooks`-free file without extra
pruning). The pre-existing absent-settings leak (`test_on_uninstall_no_backup_is_noop`) is a
**pre-existing** issue, explicitly OUT of this cut's scope. No code change in this step.

- [ ] **Step 8: Write + run install/uninstall tests (Stop lands; round-trip removes it)**

```python
# tests/unit/adapters/test_claude_code.py
import json
from pathlib import Path
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

def _install_into(tmp_path, monkeypatch, pre_existing: dict | None):
    import super_harness.adapters.agent.claude_code as cc
    # real install resolves BOTH super-harness-hook and super-harness
    monkeypatch.setattr(cc.shutil, "which", lambda n: f"/abs/{n}")
    (tmp_path / ".claude").mkdir()
    f = tmp_path / ".claude" / "settings.local.json"
    if pre_existing is not None:
        f.write_text(json.dumps(pre_existing))
    ClaudeCodeAdapter().install_hooks(tmp_path)
    return f

def test_install_registers_stop(tmp_path, monkeypatch):
    f = _install_into(tmp_path, monkeypatch, pre_existing=None)
    events = json.loads(f.read_text())["hooks"]
    assert "Stop" in events and "PreToolUse" in events
    assert any("--event stop" in h["command"] for e in events["Stop"] for h in e["hooks"])

def test_uninstall_round_trip_removes_stop(tmp_path, monkeypatch):
    # Install into PRE-EXISTING settings so a pristine backup exists; the existing
    # restore-earliest on_uninstall then restores pristine (Stop gone).
    pristine = {"model": "x", "permissions": {}}
    f = _install_into(tmp_path, monkeypatch, pre_existing=pristine)
    assert "Stop" in json.loads(f.read_text())["hooks"]
    ClaudeCodeAdapter().on_uninstall(tmp_path)
    assert json.loads(f.read_text()) == pristine   # no Stop, no super-harness hooks
```

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_claude_code.py -v`
Expected: PASS (all — including the 3 pre-existing backup round-trip tests, since `on_uninstall` is unchanged).

- [ ] **Step 9: Commit**

```bash
git add src/super_harness/adapters/__init__.py src/super_harness/adapters/agent/claude_code.py tests/unit/adapters/
git commit -m "feat(adapters): Stop-feedback seam (Verdict) + Claude delivery + Stop hook install"
```

---

## Task 5: `--event stop` path on the hook binary (loop-safe, fail-open)

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py` (`--event` parse in `main`; `_run_claude_code_stop`)
- Test: `tests/integration/daemon/test_hook_entry_stop.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/daemon/test_hook_entry_stop.py
import json, subprocess, textwrap
from pathlib import Path

def _run_stop(cwd: Path, payload: dict) -> subprocess.CompletedProcess:
    # invoke the real console script (matches tests/integration/daemon/test_hook_entry.py)
    return subprocess.run(
        ["super-harness-hook", "--agent", "claude-code", "--event", "stop"],
        input=json.dumps(payload), capture_output=True, text=True, cwd=str(cwd),
    )

def _workspace_with_failing_opted_check(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    d = tmp_path / "docs" / "decisions"; d.mkdir(parents=True)
    (d / "d-fail.md").write_text(textwrap.dedent("""\
        ---
        id: d-fail
        status: ratified
        authoring_time: true
        ---
        body
        ```check
        false
        ```
        ```counterexample path=src/_ce.py
        x = 1
        ```
        """))

def test_stop_violation_blocks_with_reason(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["decision"] == "block"
    assert "d-fail" in obj["reason"]

def test_stop_already_nudged_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": True})
    assert r.returncode == 0
    assert r.stdout.strip() == ""     # loop-safe: don't block twice

def test_stop_no_harness_is_silent(tmp_path: Path):
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0 and r.stdout.strip() == ""

def test_stop_kill_switch_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    (tmp_path / ".harness" / "gate-disabled").touch()
    r = _run_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0 and r.stdout.strip() == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/daemon/test_hook_entry_stop.py -v`
Expected: FAIL — `--event stop` not handled.

- [ ] **Step 3: Parse `--event` in `main` and route the stop path**

In `hook_entry.py` `main()`, extract an optional `--event` (default `pre-tool-use`) before the `--agent` dispatch (keep the existing pre path when absent). Route `--event stop` + `--agent claude-code` to `_run_claude_code_stop()`:

```python
def main() -> None:
    argv = sys.argv[1:]
    event = "pre-tool-use"
    if "--event" in argv:
        i = argv.index("--event")
        event = argv[i + 1] if i + 1 < len(argv) else event
        argv = argv[:i] + argv[i + 2:]
    if argv[:1] == ["--agent"]:
        agent = argv[1] if len(argv) > 1 else ""
        if agent == "claude-code":
            _run_claude_code_stop() if event == "stop" else _run_claude_code_shim()
            return
        if agent == "codex":
            if event == "stop":
                sys.exit(0)     # codex Stop delivery is a follow-on cut → explicit no-op
            _run_codex_shim()
            return
        sys.stderr.write(f"super-harness-hook: unknown --agent {agent!r}\n")
        sys.exit(0)
    _run_positional(argv)
```

- [ ] **Step 4: Implement `_run_claude_code_stop` (loop-safe, fail-open, kill-switch)**

```python
def _run_claude_code_stop() -> None:
    """Claude Code Stop hook: run the authoring-time check once at turn end and, on a
    violation, block the stop with an advisory. ALWAYS exit 0. Loop-safe (never block
    twice — `stop_hook_active`). Fail-open on any error / no harness / kill switch."""
    import json
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict) or data.get("stop_hook_active") is True:
        sys.exit(0)     # loop-safe: we already nudged once, or malformed → allow stop
    try:
        root = find_harness_root(Path.cwd())
    except HarnessNotInitialized:
        sys.exit(0)
    if (root / ".harness" / "gate-disabled").exists():
        sys.exit(0)     # kill switch → allow
    try:
        from super_harness.core.authoring_check import run_authoring_check
        from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
        verdict = run_authoring_check(root)
        out = ClaudeCodeAdapter().format_stop_feedback(verdict)
    except Exception:
        sys.exit(0)     # fail-open: never let the check break the agent
    if out:
        sys.stdout.write(out)
    sys.exit(0)
```

- [ ] **Step 5: Run to verify the stop path tests pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/daemon/test_hook_entry_stop.py -v`
Expected: PASS (all four).

- [ ] **Step 6: Verify the existing PreToolUse path is untouched**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/daemon/test_hook_entry.py -v`
Expected: PASS (no regression — `--event` absent still routes to the pre gate).

- [ ] **Step 7: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/integration/daemon/test_hook_entry_stop.py
git commit -m "feat(hook): loop-safe fail-open Stop-hook authoring-time path (claude-code)"
```

---

## Task 6: Full suite + AGENTS.md / docs sync

- [ ] **Step 1: Full unit + integration suite**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit tests/integration -q`
Expected: PASS (no regressions).

- [ ] **Step 2: Regenerate AGENTS.md + doc-check (adapter surface changed)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md -y && PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix`
Expected: clean. Review any diff (the Claude Code subsection may now mention the turn-end advisory). Document only the mechanical fact ("a Stop hook runs the authoring check") — behavioral efficacy is Task 8's to report.

- [ ] **Step 3: Commit any sync output**

```bash
git add AGENTS.md docs/adapters/claude-code.md
git commit -m "docs: regenerate AGENTS.md for the Stop-hook authoring advisory"
```

---

## Task 7: LIVE re-confirm through the real adapter

The mechanism was proven by the pre-implementation stub. Re-confirm it end-to-end through the *real* installed adapter (design §7).

- [ ] **Step 1:** In a scratch workspace, `super-harness init` + `super-harness adapter install claude-code`, with one `authoring_time: true` decision whose check fails for a known state.
- [ ] **Step 2:** Drive Claude Code (headless `-p`) to finish a turn while the check fails; confirm the `decision:block` advisory reaches the model (it references the decision) and the stop is blocked, then allowed once `stop_hook_active`.
- [ ] **Step 3:** Record evidence in `private/research/`. If the feedback does NOT reach the model through the real adapter, stop and surface it.

---

## Task 8: Dogfood bite-test — H on a TRANSITIVE violation (value-bleed proof)

The experiment (design §1/§7). Target a **transitive** edge — the case a strong model cannot self-police — not a blatant direct import.

- [ ] **Step 1:** In a live self-host change, with the Stop hook installed and `d-core-is-base` opted in, induce a **transitive** `core → adapters → sensors` edge (mirror the real #56 shape: e.g. a `core` module importing an `adapters` symbol that re-exports something from `sensors`).
- [ ] **Step 2: Record:** (a) did the turn-end verdict name `d-core-is-base`; (b) did Claude self-correct before merge/human; (c) measured whole-graph import-linter latency (design §5); (d) any noise / loop behavior.
- [ ] **Step 3: Write the honest verdict** into `private/research/` + the capability-convergence ledger: H supported (self-corrected on a transitive violation it could not have self-policed — real value bleed) or falsified (ignored — token noise over the floor). Either is a valid deliverable; only an oversold result is a failure.

---

## Self-review checklist (run before execution)

- **Spec coverage:** design §3 IN → Task 2 (verdict core, tri-state, opt-in filter), Task 1 (`authoring_time`), Task 5 (loop-safe fail-open Stop path + kill switch), Task 4 (Verdict-shaped seam + Claude delivery + marker-strip uninstall), Task 8 (transitive bite-test). §4 safety (opt-in + kill switch) → Task 1 + Task 5. §5 latency (`AUTHORING_CHECK_TIMEOUT=8` < outer 10) → Task 2, measured in Task 8. §6 naming (`authoring_check`, not `sensor`) → Task 2. Codex delivery, 9th capability key, per-edit/PostToolUse, `applies_to`/relevance are correctly ABSENT.
- **Placeholder scan:** none — every code step has concrete code; test steps have real assertions.
- **Type consistency:** `Verdict`/`Violation`/`run_authoring_check`/`_to_violations` used consistently (Task 2 ↔ Task 4 ↔ Task 5); `format_stop_feedback(verdict)` signature identical in ABC (Task 4 s3), Claude override (s4), and hook call (Task 5 s4); `merge_stop_hook(settings_path, *, command, marker=_STOP_MARKER)` identical in Task 3 and the install call (Task 4 s6). All `_settings_merge` internals (`_read_settings`, inlined backup/write, `_ensure_event_list`, `_strip_entries`, markers) are referenced as "mirror the real `merge_session_start_hook`" rather than invented names.
```
