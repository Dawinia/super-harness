# Codex Stop delivery (authoring-time conformance, cut-2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver cut-1's turn-end authoring-conformance feedback to Codex (the second agent) by factoring the Stop path along its true responsibility boundary — an agent-agnostic orchestrator plus adapter-owned Stop protocol — so both Claude Code and Codex ride one seam.

**Architecture:** `hook_entry._run_stop(adapter)` becomes fully agnostic (root → kill-switch → `adapter.stop_should_check(payload)` → `run_authoring_check` → `adapter.format_stop_feedback(verdict)` → stdout, fail-open). Each adapter owns its full Stop protocol; the shared `{"decision":"block","reason"}` impl and the `stop_hook_active` guard live in a Claude-Code-hook **family** helper (`adapters/agent/_stop_protocol.py`), NOT the base class (base stays agnostic). This moves cut-1's hard-coded `stop_hook_active` guard out of `hook_entry` (intrinsic to generalizing, not creep). Codex's Stop protocol was spiked byte-identical to Claude's — see `private/research/2026-07-01-codex-stop-spike.md`.

**Tech Stack:** Python 3.10+, pytest, existing `_settings_merge` hook installers, `run_authoring_check` verdict core, Codex CLI 0.142.2 (`codex exec`) for the LIVE step.

**Design doc:** `docs/plans/2026-07-01-codex-stop-delivery-design.md`. Read it first.

---

## File structure

- **Create** `src/super_harness/adapters/agent/_stop_protocol.py` — Claude-Code-hook family helper: `is_continuation(payload)` + `block_feedback(verdict)`. One responsibility: the shared Stop wire protocol.
- **Modify** `src/super_harness/adapters/__init__.py` — add `AgentAdapter.stop_should_check(payload)` default; add 9th canonical capability key `turn_end_feedback_hook` to the contract docstring.
- **Modify** `src/super_harness/adapters/agent/claude_code.py` — `format_stop_feedback` + `stop_should_check` delegate to `_stop_protocol`; add `turn_end_feedback_hook: True` cap.
- **Modify** `src/super_harness/adapters/agent/codex.py` — add `format_stop_feedback` + `stop_should_check`; install Stop hook; update AGENTS.md subsection; caps: `post_tool_use_hook: True`, `turn_end_feedback_hook: True`.
- **Modify** `src/super_harness/daemon/hook_entry.py` — refactor `_run_claude_code_stop` → agnostic `_run_stop(adapter)`; dispatch both agents' `--event stop` to it.
- **Modify** tests: `test_protocol.py`, `test_claude_code.py`, `test_codex.py`, `test_hook_entry_stop.py`; **create** `test_stop_protocol.py`.

---

## Task 1: Family helper `_stop_protocol.py`

**Files:**
- Create: `src/super_harness/adapters/agent/_stop_protocol.py`
- Test: `tests/unit/adapters/test_stop_protocol.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/adapters/test_stop_protocol.py
from __future__ import annotations

import json

from super_harness.adapters.agent import _stop_protocol as sp
from super_harness.core.authoring_check import Verdict, Violation


def test_is_continuation_true_only_when_flag_is_true():
    assert sp.is_continuation({"stop_hook_active": True}) is True
    assert sp.is_continuation({"stop_hook_active": False}) is False
    assert sp.is_continuation({}) is False              # absent → first fire
    assert sp.is_continuation({"stop_hook_active": "true"}) is False  # STRICT: only bool True


def test_block_feedback_empty_when_clean():
    assert sp.block_feedback(Verdict(violations=[])) == ""


def test_block_feedback_is_decision_block_reason_naming_the_decision():
    v = Verdict(violations=[Violation(
        decision_id="d-core-is-base", detail="core imports sensors",
        decision_doc_path="docs/decisions/d-core-is-base.md")])
    obj = json.loads(sp.block_feedback(v))
    assert obj["decision"] == "block"
    assert "d-core-is-base" in obj["reason"]
    assert set(obj) == {"decision", "reason"}  # reason-ONLY (spike: extra fields break Codex Stop)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/adapters/test_stop_protocol.py -v`
Expected: FAIL with `ModuleNotFoundError: super_harness.adapters.agent._stop_protocol`

- [ ] **Step 3: Write the helper**

```python
# src/super_harness/adapters/agent/_stop_protocol.py
"""Claude-Code-hook *family* Stop protocol (shared by Claude Code + Codex).

Codex deliberately clones the Claude Code hook interface (see codex.py docstring),
so both agents' turn-end Stop hooks use the SAME payload guard field
(`stop_hook_active`) and the SAME feedback envelope (`{"decision":"block","reason"}`).
This module is that shared protocol — NOT a universal truth: a third agent with a
different turn-end mechanism must NOT reuse this; it writes its own. Verified against
`codex exec` in private/research/2026-07-01-codex-stop-spike.md (reason is the ONLY
channel that reaches the model; systemMessage / additionalContext break Codex's Stop).

`block_feedback` composes `AgentAdapter._render_advisory` (agnostic Verdict->prose). That
prose deliberately stays on the base class, NOT relocated to `core.authoring_check`
(considered + rejected: it would touch core, and core owns structured verdicts not
presentation prose — see design 2026-07-01-codex-stop-delivery-design.md §4.1).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from super_harness.adapters import AgentAdapter

if TYPE_CHECKING:
    from super_harness.core.authoring_check import Verdict


def is_continuation(payload: dict[str, Any]) -> bool:
    """True on the continuation turn a prior block created — the re-entrancy guard.
    STRICT: only the literal bool ``True`` counts (a ``"true"`` string does not)."""
    return payload.get("stop_hook_active") is True


def block_feedback(verdict: Verdict) -> str:
    """The family Stop envelope: ``{"decision":"block","reason": advisory}`` when a
    violation is present, ``""`` when clean. reason-ONLY by design (spike §Q4)."""
    if not verdict.violations:
        return ""
    return json.dumps(
        {"decision": "block", "reason": AgentAdapter._render_advisory(verdict)}
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/adapters/test_stop_protocol.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/agent/_stop_protocol.py tests/unit/adapters/test_stop_protocol.py
git commit -m "feat(adapters): Claude-Code-hook family Stop protocol helper"
```

---

## Task 2: `AgentAdapter.stop_should_check` contract method

**Files:**
- Modify: `src/super_harness/adapters/__init__.py` (add method near `format_stop_feedback`, ~line 151)
- Test: `tests/unit/adapters/test_protocol.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/unit/adapters/test_protocol.py
def test_stop_should_check_defaults_true():
    # A conforming agent that does not override runs the check on every Stop.
    from tests.unit.adapters.test_protocol import _ConformingAgent  # or the local fixture class
    assert _ConformingAgent().stop_should_check({"stop_hook_active": True}) is True
    assert _ConformingAgent().stop_should_check({}) is True
```

(If `_ConformingAgent` is a module-local class, call it directly instead of importing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/adapters/test_protocol.py::test_stop_should_check_defaults_true -v`
Expected: FAIL with `AttributeError: 'ConformingAgent' object has no attribute 'stop_should_check'`

- [ ] **Step 3: Add the default method**

In `src/super_harness/adapters/__init__.py`, immediately BEFORE `format_stop_feedback` (~line 151):

```python
    def stop_should_check(self, payload: dict[str, Any]) -> bool:
        """Whether to run the authoring check for this turn-end (Stop) event.

        Default ``True`` (check every turn end). Agents whose Stop payload carries a
        re-entrancy guard override this to skip the continuation turn a prior block
        created, so a nudge never loops. This is the FIRST half of an agent's Stop
        protocol; :meth:`format_stop_feedback` is the second — both live on the adapter
        so the orchestrator (`hook_entry._run_stop`) stays free of agent field names."""
        return True
```

Ensure `Any` is imported (it is — `from typing import TYPE_CHECKING, Any, ClassVar`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/adapters/test_protocol.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/__init__.py tests/unit/adapters/test_protocol.py
git commit -m "feat(adapters): add AgentAdapter.stop_should_check contract method (default True)"
```

---

## Task 3: 9th canonical capability key + Codex post_tool_use correction

Adds `turn_end_feedback_hook` to the canonical set (cut-1 shipped the Claude Stop hook without describing it — this closes that gap) and flips Codex `post_tool_use_hook` to `True` (spike proved Codex PostToolUse fires; the key describes agent capability).

**Exact touch-points (verified against source — review S4/NIT):**
- `src/super_harness/adapters/__init__.py`: the string "canonical 8 keys" appears **three** times — lines **22, 70, 76** (all → "9 keys"); the key list is ~line 24-31.
- `src/super_harness/adapters/agent/claude_code.py` caps dict ~line 146; `src/super_harness/adapters/agent/codex.py` caps dict ~line 76.
- `tests/unit/adapters/test_protocol.py`: the fixture is **`_MinimalAdapter`** (~line 16, NOT `_ConformingAgent`); `test_capabilities_canonical_keys` (~line 111) asserts `set(_MinimalAdapter().capabilities) == expected` — it checks ONLY that fixture, not the real adapters.
- `tests/unit/adapters/test_claude_code.py`: module constant **`_CANONICAL_CAPABILITY_KEYS`** at line **59** (used at :91) AND the inline dict at **:92-100**.
- `docs/adapters/claude-code.md` line **~41**: a hand-maintained caps list that will go stale.

- [ ] **Step 1: Make the canonical-keys test expect 9 keys (RED first)**

In `tests/unit/adapters/test_protocol.py::test_capabilities_canonical_keys`, add to the `expected` set **only** (leave the `_MinimalAdapter` fixture unchanged for now, so the test goes red):

```python
        "turn_end_feedback_hook",
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/adapters/test_protocol.py::test_capabilities_canonical_keys -v`
Expected: FAIL — `_MinimalAdapter`'s caps lack the key, so `set(a.capabilities) == expected` is False (the fixture is the ONLY thing this test checks).

- [ ] **Step 3: Add the key to `_MinimalAdapter` + the contract docstring**

In `tests/unit/adapters/test_protocol.py`, `_MinimalAdapter` capabilities fixture (~line 16-24) add:

```python
        "turn_end_feedback_hook": False,
```

In `src/super_harness/adapters/__init__.py`, in the canonical-keys list (~after `subprocess_execution`, line 31) add:

```
    turn_end_feedback_hook  # turn-end (Stop) authoring-conformance advisory
```

Change all **three** "canonical 8 keys" occurrences (lines 22, 70, 76) to "canonical 9 keys".

- [ ] **Step 4: Add the key to both adapters + flip Codex post_tool_use**

In `src/super_harness/adapters/agent/claude_code.py` caps dict, add:

```python
        "turn_end_feedback_hook": True,  # Claude Code Stop hook (cut-1)
```

In `src/super_harness/adapters/agent/codex.py` caps dict, change `post_tool_use_hook` and add the new key:

```python
        "post_tool_use_hook": True,  # spike-verified: fires under `codex exec` (2026-07-01)
        ...
        "turn_end_feedback_hook": True,  # Codex Stop hook (cut-2)
```

- [ ] **Step 5: Update the Claude caps assertions + the stale adapter doc**

In `tests/unit/adapters/test_claude_code.py`: add `"turn_end_feedback_hook"` to the `_CANONICAL_CAPABILITY_KEYS` set (line 59) AND `"turn_end_feedback_hook": True` to the inline dict (line 92-100).
In `tests/unit/adapters/test_codex.py` add (no caps assertion exists there yet):

```python
def test_codex_capabilities():
    caps = CodexAdapter().capabilities
    assert caps["post_tool_use_hook"] is True       # spike-verified
    assert caps["turn_end_feedback_hook"] is True    # cut-2 Stop delivery
```

In `docs/adapters/claude-code.md` (~line 41), add `turn_end_feedback_hook` to the "all `True`" group of the hand-maintained capability list.

- [ ] **Step 6: Run all affected tests**

Run: `pytest tests/unit/adapters/test_protocol.py tests/unit/adapters/test_claude_code.py tests/unit/adapters/test_codex.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/super_harness/adapters/__init__.py src/super_harness/adapters/agent/claude_code.py src/super_harness/adapters/agent/codex.py tests/unit/adapters/ docs/adapters/claude-code.md
git commit -m "feat(adapters): add turn_end_feedback_hook canonical key; correct Codex post_tool_use_hook"
```

---

## Task 4: Claude Code delegates to the family helper (no behavior change)

**Files:**
- Modify: `src/super_harness/adapters/agent/claude_code.py` (`format_stop_feedback` ~line 237; add `stop_should_check`)
- Test: `tests/unit/adapters/test_claude_code.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/adapters/test_claude_code.py
import json
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
from super_harness.core.authoring_check import Verdict, Violation


def test_claude_stop_should_check_skips_continuation():
    a = ClaudeCodeAdapter()
    assert a.stop_should_check({"stop_hook_active": True}) is False
    assert a.stop_should_check({"stop_hook_active": False}) is True


def test_claude_format_stop_feedback_delegates_to_family():
    v = Verdict(violations=[Violation(
        decision_id="d-x", detail="d", decision_doc_path="docs/decisions/d-x.md")])
    obj = json.loads(ClaudeCodeAdapter().format_stop_feedback(v))
    assert obj["decision"] == "block" and "d-x" in obj["reason"]
    assert ClaudeCodeAdapter().format_stop_feedback(Verdict(violations=[])) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/adapters/test_claude_code.py::test_claude_stop_should_check_skips_continuation -v`
Expected: FAIL by ASSERTION (not `AttributeError` — Task 2 added the base default `stop_should_check`, which returns `True` for the continuation payload, so `is False` fails).

- [ ] **Step 3: Delegate both methods to `_stop_protocol`**

In `src/super_harness/adapters/agent/claude_code.py`, add the import near the top (module scope is fine — claude_code is not the hot hook_entry path):

```python
from super_harness.adapters.agent import _stop_protocol
```

Replace the body of `format_stop_feedback` (~line 237-247) with a delegation, and add `stop_should_check` next to it:

```python
    def stop_should_check(self, payload: dict) -> bool:
        """Skip the continuation turn a prior block created (loop-safety)."""
        return not _stop_protocol.is_continuation(payload)

    def format_stop_feedback(self, verdict: Verdict) -> str:
        """Claude Code Stop feedback = the shared Claude-Code-hook family envelope
        (`{"decision":"block","reason": ...}`; reason reaches the model next turn, the
        edit is never undone). ``""`` when clean. Loop-safety lives in
        :meth:`stop_should_check` / the hook entry, not here."""
        return _stop_protocol.block_feedback(verdict)
```

Remove the now-unused local `import json` inside the old `format_stop_feedback` if present.

- [ ] **Step 4: Run tests (new + existing regression)**

Run: `pytest tests/unit/adapters/test_claude_code.py -v`
Expected: PASS (new delegation tests + all pre-existing `format_stop_feedback` tests still green — behavior unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/agent/claude_code.py tests/unit/adapters/test_claude_code.py
git commit -m "refactor(claude): delegate Stop protocol to family helper (no behavior change)"
```

---

## Task 5: Agnostic `_run_stop(adapter)` orchestrator

Refactor cut-1's `_run_claude_code_stop` into an agent-agnostic runner. It must contain NO agent field names — the `stop_hook_active` guard now comes from `adapter.stop_should_check`.

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py` (`_run_claude_code_stop` ~line 167-197; `main()` dispatch ~line 70-75)
- Test: `tests/integration/daemon/test_hook_entry_stop.py` (existing Claude tests must still pass); `tests/unit/daemon/test_hook_entry.py` (add agnostic-orchestrator stub test)

- [ ] **Step 1: Write the failing agnostic-orchestrator tests (stub adapter, DIFFERENT guard field)**

Three tests, each proving the orchestrator is agent-free. **Patch target matters (review S1):** `_run_stop` imports `run_authoring_check` **function-locally** (Step 3), so patching `hook_entry.run_authoring_check` is a silent no-op — patch the SOURCE module `super_harness.core.authoring_check.run_authoring_check` so the local import picks it up.

```python
# tests/unit/daemon/test_hook_entry.py
import io, json, sys
import pytest
from super_harness.daemon import hook_entry
from super_harness.core.authoring_check import Verdict, Violation


class _StubAdapter:
    """Guards on a MADE-UP field (`my_custom_guard`), not `stop_hook_active` — so if the
    orchestrator honors this guard, it cannot be reading a hard-coded Claude field."""
    def stop_should_check(self, payload):
        return payload.get("my_custom_guard") is not True
    def format_stop_feedback(self, verdict):
        return "STUB_OUT" if verdict.violations else ""


def _drive_stop(monkeypatch, tmp_path, payload: dict):
    (tmp_path / ".harness").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


def test_run_stop_emits_adapter_output_on_violation(tmp_path, monkeypatch, capsys):
    # NON-continuation payload → check runs → adapter renders → stdout carries it.
    _drive_stop(monkeypatch, tmp_path, {"my_custom_guard": False})
    monkeypatch.setattr("super_harness.core.authoring_check.run_authoring_check",
                        lambda root: Verdict(violations=[Violation("d-x", "detail", "docs/decisions/d-x.md")]))
    with pytest.raises(SystemExit) as e:
        hook_entry._run_stop(_StubAdapter())
    assert e.value.code == 0
    assert capsys.readouterr().out == "STUB_OUT"


def test_run_stop_honors_adapter_guard_not_stop_hook_active(tmp_path, monkeypatch, capsys):
    # Continuation per the stub's OWN field (stop_hook_active absent) → allow, no output.
    _drive_stop(monkeypatch, tmp_path, {"my_custom_guard": True})
    monkeypatch.setattr("super_harness.core.authoring_check.run_authoring_check",
                        lambda root: Verdict(violations=[Violation("d-x", "d", "p")]))
    with pytest.raises(SystemExit) as e:
        hook_entry._run_stop(_StubAdapter())
    assert e.value.code == 0
    assert capsys.readouterr().out == ""


def test_run_stop_fails_open_on_check_error(tmp_path, monkeypatch, capsys):
    _drive_stop(monkeypatch, tmp_path, {"my_custom_guard": False})
    def _boom(root):
        raise RuntimeError("graph engine exploded")
    monkeypatch.setattr("super_harness.core.authoring_check.run_authoring_check", _boom)
    with pytest.raises(SystemExit) as e:
        hook_entry._run_stop(_StubAdapter())
    assert e.value.code == 0            # fail-open: never break the agent
    assert capsys.readouterr().out == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/daemon/test_hook_entry.py -k run_stop -v`
Expected: FAIL (all three) — `_run_stop` does not exist yet.

- [ ] **Step 3: Refactor to `_run_stop(adapter)`**

In `src/super_harness/daemon/hook_entry.py`, replace `_run_claude_code_stop` (~line 167-197) with:

```python
def _run_stop(adapter) -> None:
    """Agent-agnostic turn-end (Stop) authoring-check orchestrator. ALWAYS exits 0 (the
    turn's edits stand). The adapter owns the agent-specific Stop protocol: the
    re-entrancy guard (`adapter.stop_should_check`) and the feedback envelope
    (`adapter.format_stop_feedback`). This function contains NO agent field names.
    Fail-open on any error / no harness / kill switch (Axiom 1)."""
    import json

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    try:
        root = find_harness_root(Path.cwd())
    except HarnessNotInitialized:
        sys.exit(0)
    if (root / ".harness" / "gate-disabled").exists():
        sys.exit(0)  # kill switch → allow
    try:
        if not adapter.stop_should_check(data):
            sys.exit(0)  # continuation turn (or adapter opts out) → allow
        from super_harness.core.authoring_check import run_authoring_check

        verdict = run_authoring_check(root)
        out = adapter.format_stop_feedback(verdict)
    except Exception:
        sys.exit(0)  # fail-open: never let the check break the agent
    if out:
        sys.stdout.write(out)
    sys.exit(0)
```

In `main()` (~line 70-71), change the Claude dispatch:

```python
        if agent == "claude-code":
            if event == "stop":
                from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
                _run_stop(ClaudeCodeAdapter())
            else:
                _run_claude_code_shim()
            return
```

- [ ] **Step 4: Run tests (agnostic stub + Claude regression)**

Run: `pytest tests/unit/daemon/test_hook_entry.py tests/integration/daemon/test_hook_entry_stop.py -v`
Expected: PASS — the new agnostic test AND every existing Claude Stop test (unchanged behavior).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/unit/daemon/test_hook_entry.py
git commit -m "refactor(hook_entry): agnostic _run_stop(adapter); guard via adapter.stop_should_check"
```

---

## Task 6: Codex adapter — Stop methods + install + AGENTS.md

**Files:**
- Modify: `src/super_harness/adapters/agent/codex.py` (import `_stop_protocol` + `merge_stop_hook`; add methods; `install_hooks`; `_AGENTS_MD_SUBSECTION`)
- Test: `tests/unit/adapters/test_codex.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/adapters/test_codex.py
import json, shutil
from super_harness.adapters.agent.codex import CodexAdapter
from super_harness.core.authoring_check import Verdict, Violation


def test_codex_stop_should_check_and_feedback_delegate():
    a = CodexAdapter()
    assert a.stop_should_check({"stop_hook_active": True}) is False
    assert a.stop_should_check({"stop_hook_active": False}) is True
    v = Verdict(violations=[Violation("d-core-is-base", "x", "docs/decisions/d-core-is-base.md")])
    obj = json.loads(a.format_stop_feedback(v))
    assert obj["decision"] == "block" and "d-core-is-base" in obj["reason"]
    assert set(obj) == {"decision", "reason"}  # reason-ONLY (spike: extra fields break Codex Stop)


def test_codex_install_writes_stop_hook(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    stop = data["hooks"]["Stop"][0]
    assert stop["hooks"][0]["command"] == "/abs/super-harness-hook --agent codex --event stop"


def test_codex_install_stop_is_idempotent_on_reinstall(tmp_path, monkeypatch):
    # BLOCKER regression (review B1): a Codex-specific marker must make reinstall REPLACE,
    # not append. Two Stop entries → two JSON objects on stdout → Codex "Stop Failed".
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    CodexAdapter().install_hooks(tmp_path)
    CodexAdapter().install_hooks(tmp_path)  # reinstall
    data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert len(data["hooks"]["Stop"]) == 1  # replaced, not duplicated


def test_codex_uninstall_round_trips_stop_hook(tmp_path, monkeypatch):
    # S3: install onto a PRE-EXISTING hooks.json, then uninstall → earliest backup restored,
    # Stop entry gone. (Fresh-install-absent-file uninstall leak is pre-existing / OUT.)
    monkeypatch.setattr(shutil, "which", lambda _n: f"/abs/{_n}")
    (tmp_path / ".codex").mkdir()
    hooks = tmp_path / ".codex" / "hooks.json"
    hooks.write_text('{"hooks": {}}\n')  # pristine pre-existing file
    CodexAdapter().install_hooks(tmp_path)
    assert "Stop" in json.loads(hooks.read_text())["hooks"]
    CodexAdapter().on_uninstall(tmp_path)
    assert json.loads(hooks.read_text()) == {"hooks": {}}  # restored, Stop gone


def test_codex_installed_detail_mentions_stop():
    assert "Stop" in CodexAdapter().installed_detail()


def test_codex_agents_md_mentions_stop_authoring_check():
    sub = CodexAdapter().agents_md_subsection()
    assert "Stop" in sub
    assert "authoring" in sub.lower()
    assert "/hooks" in sub  # trust caveat still present
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/adapters/test_codex.py -k "stop or idempotent or uninstall_round or installed_detail_mentions or agents_md_mentions_stop" -v`
Expected: FAIL (`AttributeError` for the delegate methods; `KeyError: 'Stop'` for BOTH install tests — including the idempotency one — until the Stop merge exists; installed_detail/AGENTS.md string asserts).

- [ ] **Step 3: Implement in `codex.py`**

Add imports (module scope — codex.py is not the hot hook_entry path):

```python
from super_harness.adapters.agent import _stop_protocol
from super_harness.adapters.agent._settings_merge import (
    merge_pre_tool_use_hook,
    merge_session_start_hook,
    merge_stop_hook,   # NEW
)
```

Add a Codex-specific Stop marker constant near the existing `_CODEX_MARKER` (~line 31) — **REQUIRED (review B1):** `merge_stop_hook` defaults `marker=_STOP_OURS_MARKER` which is the *Claude* pair `"--agent claude-code --event stop"`; a default call makes Codex reinstall append instead of replace:

```python
_CODEX_STOP_MARKER = "--agent codex --event stop"
```

In `install_hooks`, after the `merge_session_start_hook(...)` call and inside the same `try`, pass that marker explicitly (mirrors the existing `merge_pre_tool_use_hook(..., marker=_CODEX_MARKER)` call):

```python
            merge_stop_hook(
                hooks_path,
                command=f"{resolved_hook} --agent codex --event stop",
                marker=_CODEX_STOP_MARKER,
            )
```

Add the two protocol methods to `CodexAdapter` (e.g. after `inject_context`):

```python
    def stop_should_check(self, payload: dict) -> bool:
        """Skip the continuation turn a prior block created (loop-safety). Codex's Stop
        payload carries `stop_hook_active`, spiked identical to Claude Code's."""
        return not _stop_protocol.is_continuation(payload)

    def format_stop_feedback(self, verdict: Verdict) -> str:
        """Codex Stop feedback = the shared Claude-Code-hook family envelope
        (`{"decision":"block","reason": ...}`). Spike-verified under `codex exec`:
        `reason` is the ONLY channel that reaches the model; adding systemMessage /
        additionalContext makes Codex report "Stop Failed" and drop the continuation."""
        return _stop_protocol.block_feedback(verdict)
```

Add a `Verdict` type import under the existing `TYPE_CHECKING` block (codex.py has `from __future__ import annotations`, so annotate under TYPE_CHECKING):

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from super_harness.core.authoring_check import Verdict
```

Extend `_AGENTS_MD_SUBSECTION` — add this paragraph before `{_AGENTS_MD_END}`:

```
A **Stop** hook also runs a turn-end authoring-time conformance check: when you finish
a turn, any ratified decision that opted in (`authoring_time: true`) has its check run
once; a failure is fed back as a non-blocking advisory so you self-correct next turn.
Like the PreToolUse gate, the Stop hook is INACTIVE until you `/hooks`-trust it.
```

Update `installed_detail()` (~line 141) to mention the Stop hook:

```python
    def installed_detail(self) -> str:
        return (
            "PreToolUse + SessionStart + Stop hooks registered in .codex/hooks.json — "
            "run `/hooks` in Codex to trust the hooks before the gate is active"
        )
```

- [ ] **Step 4: Run tests + full Codex suite (install rollback regression)**

Run: `pytest tests/unit/adapters/test_codex.py -v`
Expected: PASS — new tests plus the pre-existing install / rollback / detect tests (Stop merge participates in the same snapshot-rollback `try`).

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/adapters/agent/codex.py tests/unit/adapters/test_codex.py
git commit -m "feat(codex): Stop authoring-check hook — install + delegate to family protocol"
```

---

## Task 7: Codex Stop dispatch in `hook_entry`

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py` (`main()` codex branch ~line 73-77)
- Test: `tests/integration/daemon/test_hook_entry_stop.py`

- [ ] **Step 1: Write the failing integration tests (mirror the Claude Stop tests for `--agent codex`)**

```python
# tests/integration/daemon/test_hook_entry_stop.py
def _run_codex_stop(cwd: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["super-harness-hook", "--agent", "codex", "--event", "stop"],
        input=json.dumps(payload), capture_output=True, text=True, cwd=str(cwd),
    )


def test_codex_stop_violation_blocks_with_reason(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_codex_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0
    obj = json.loads(r.stdout)
    assert obj["decision"] == "block" and "d-fail" in obj["reason"]
    assert set(obj) == {"decision", "reason"}  # reason-ONLY


def test_codex_stop_already_nudged_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    r = _run_codex_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": True})
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_codex_stop_kill_switch_allows(tmp_path: Path):
    _workspace_with_failing_opted_check(tmp_path)
    (tmp_path / ".harness" / "gate-disabled").touch()
    r = _run_codex_stop(tmp_path, {"hook_event_name": "Stop", "stop_hook_active": False})
    assert r.returncode == 0 and r.stdout.strip() == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/integration/daemon/test_hook_entry_stop.py -k codex -v`
Expected: FAIL — the codex `--event stop` branch is still the `sys.exit(0)` placeholder, so `test_codex_stop_violation_blocks_with_reason` gets empty stdout.

- [ ] **Step 3: Wire the codex Stop dispatch**

In `src/super_harness/daemon/hook_entry.py` `main()`, replace the codex branch (~line 73-77):

```python
        if agent == "codex":
            if event == "stop":
                from super_harness.adapters.agent.codex import CodexAdapter
                _run_stop(CodexAdapter())
            else:
                _run_codex_shim()
            return
```

- [ ] **Step 4: Run tests (codex + claude, both agents through one runner)**

Run: `pytest tests/integration/daemon/test_hook_entry_stop.py -v`
Expected: PASS — all codex tests + all pre-existing claude tests.

- [ ] **Step 5: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/integration/daemon/test_hook_entry_stop.py
git commit -m "feat(hook_entry): dispatch codex --event stop through agnostic _run_stop"
```

---

## Task 8: Regenerate AGENTS.md + derived docs

The Codex `agents_md_subsection` content changed. Per repo convention, AGENTS.md is regenerated via `sync --agents-md` (NOT `doc check --fix`), and both gates run in CI.

**Files:**
- Modify (generated): `AGENTS.md` and any derived docs
- Reference: memory `reference-agents-md-regen-via-sync`

- [ ] **Step 1: Regenerate**

Run:
```bash
super-harness sync --agents-md -y
super-harness doc check --fix
```

- [ ] **Step 2: Verify sync is clean**

Run: `super-harness sync --check && super-harness doc check`
Expected: exit 0, no drift.

- [ ] **Step 3: Review the diff**

Run: `git diff -- AGENTS.md`
Expected: only the Codex subsection gains the Stop authoring-check paragraph (no unrelated churn).

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "chore(docs): regen AGENTS.md for Codex Stop authoring-check subsection"
```

---

## Task 9: LIVE seam-generality confirmation (the value evidence)

Prove the seam works through the REAL Codex adapter (not the throwaway spike hook): Stop fires under `codex exec`, the advisory naming a decision reaches the model via `reason`, and `stop_hook_active` stops the second nudge. A null self-correction result is a valid, reported outcome (the floor still catches it).

**Files:**
- Create: `private/research/2026-07-01-codex-stop-livecheck.md`

- [ ] **Step 1: Build a live workspace with the real adapter installed**

In an isolated scratchpad dir (git-init, `.codex/` present), install super-harness, `super-harness init`, install the Codex adapter (`super-harness adapter install codex`), and add ONE ratified `authoring_time: true` tier-1 decision whose check fails on a transitive violation (the `d-core-is-base` / `core → adapters → sensors` shape from cut-1's bite-test, or a minimal `false`-check decision if a graph fixture is impractical). Confirm `.codex/hooks.json` has the Stop entry from Task 6.

- [ ] **Step 2: Run codex exec and capture**

Run (trust the hook via `--dangerously-bypass-hook-trust` for the automated check only):
```bash
codex exec --dangerously-bypass-hook-trust --sandbox workspace-write -C <ws> \
  "Make a trivial edit, then finish." > <ws>/live.txt 2>&1
```
Record: (a) did Stop fire (hook logged / `live.txt` shows a continuation) — **and explicitly watch for a first-invocation non-fire** (the spike's run-1 anomaly; note it if it recurs rather than assuming it away); (b) did the advisory naming the decision appear in the model's continuation; (c) did a second Stop with `stop_hook_active:true` allow (no infinite loop); (d) latency; (e) did the model self-correct.

- [ ] **Step 3: Write the livecheck record**

Document the five observations in `private/research/2026-07-01-codex-stop-livecheck.md`, honestly (H supported / null / falsified). Note this is the first evidence for/against "a third party can contribute an agent on this seam."

- [ ] **Step 4: Commit**

```bash
git add private/research/2026-07-01-codex-stop-livecheck.md
git commit -m "docs(research): LIVE Codex Stop seam-generality confirmation"
```

---

## Final verification

- [ ] **Full suite:** `pytest -q` — all green.
- [ ] **Agnostic invariant:** `grep -n "stop_hook_active" src/super_harness/daemon/hook_entry.py` returns NOTHING (the guard moved to adapters). This is the mechanical proof the orchestrator is agent-free.
- [ ] **Both agents ride one runner:** `grep -n "_run_stop" src/super_harness/daemon/hook_entry.py` shows both claude-code and codex dispatching to it; no `_run_codex_stop` / `_run_claude_code_stop` duplicate exists.
- [ ] **Lint/agents drift:** `super-harness sync --check && super-harness doc check` exit 0.

---

## Self-review (author checklist — done)

- **Spec coverage:** design §4.1 decomposition → Tasks 1/2/4/5/6/7; §5 caps corrections → Task 3; §6 trust caveat → Task 6 AGENTS.md; §7 tests → per-task + Task 9 LIVE; §4.3 hot-path (no registry) → preserved (lazy per-branch imports in Tasks 5/7). Every design section maps to a task.
- **Placeholder scan:** no TBD/TODO; every code step shows the code.
- **Type consistency:** `stop_should_check(payload: dict) -> bool` and `format_stop_feedback(verdict: Verdict) -> str` used identically in base (Task 2), family helper (Task 1), Claude (Task 4), Codex (Task 6), orchestrator (Task 5). `_stop_protocol.is_continuation` / `block_feedback` names consistent across Tasks 1/4/6.
- **Known ripple flagged:** the 9th canonical key touches `adapters/__init__.py` (three "8 keys" strings: lines 22/70/76), `test_protocol.py::test_capabilities_canonical_keys` + its `_MinimalAdapter` fixture, `test_claude_code.py` `_CANONICAL_CAPABILITY_KEYS` (:59) + inline dict (:92), and the hand-maintained `docs/adapters/claude-code.md` caps list — all enumerated in Task 3. No other hardcoded key-set exists (`cli/adapter.py` iterates dynamically).

## Review incorporation (two independent reviews, 2026-07-01)

Revised after a Claude subagent review + a Codex `exec` cross-review (both verified against source). Folded in:
- **B1 (BLOCKER, both):** Codex Stop install now passes an explicit `_CODEX_STOP_MARKER` (`merge_stop_hook` defaults to the *Claude* marker → reinstall would append a 2nd Stop entry → two JSON objects → Codex "Stop Failed", feature lost). Task 6 adds the marker + a reinstall-idempotency test.
- **S1 (both):** Task 5's agnostic test rewritten — patch `super_harness.core.authoring_check.run_authoring_check` (function-local import; the old `hook_entry.run_authoring_check` patch was a silent no-op), and add tests that actually exercise output emission + exception fail-open.
- **S2 (Claude, adjudicated):** keep `_render_advisory` on the base (NOT moved to core) — reasoned rejection recorded in design §4.1 + Task 1 docstring (touches core / core owns structured verdicts not prose / helper→base is the sanctioned direction). Codex concurred.
- **S3 + uninstall oversell (both):** Task 6 adds a Codex install→uninstall round-trip test; design §5 reworded to "best-effort, absent-file leak pre-existing/OUT."
- **S4 + docs (both):** exact caps touch-points enumerated (3 occurrences, `_MinimalAdapter` fixture name, `_CANONICAL_CAPABILITY_KEYS`, `docs/adapters/claude-code.md`); `installed_detail()` updated + tested.
- **N1 (Claude):** Task 9 LIVE watches for a first-invocation non-fire; design §2 notes the spike anomaly.
- **N2 (Claude):** `SystemExit`-vs-`except Exception` confirmed correct — no change.
