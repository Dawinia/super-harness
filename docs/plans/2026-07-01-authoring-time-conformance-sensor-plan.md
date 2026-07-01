# Authoring-time decision-conformance sensor (cut-1, Claude-only) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a Claude Code edit, run the tier-1 decision check(s) relevant to the changed file and feed a deterministic, non-blocking "you violated decision X" advisory back to the agent at authoring time, so it self-corrects before the merge gate / a human.

**Architecture:** Agent-agnostic check core (changed-file → relevant tier-1 decisions → reuse `run_executable_checks` → verdict → rendered feedback text) + a per-agent delivery seam (`AgentAdapter.format_post_edit_feedback`) + a new non-blocking, fail-open PostToolUse path on the `super-harness-hook` binary. The merge-gate `decision check` is unchanged and remains the authoritative floor. Codex delivery is a separate follow-on cut.

**Tech Stack:** Python 3.10+, pytest, existing super-harness `core` (decisions, check_runner, paths), `adapters/agent` (claude_code, _settings_merge), `daemon/hook_entry`. Verify with `PATH="$(pwd)/.venv/bin:$PATH" pytest ...` (never `uv run`).

**Design doc:** `docs/plans/2026-07-01-authoring-time-conformance-sensor-design.md`. This plan implements cut-1 only. It tests hypothesis **H** (§1 of design): authoring-time advisory may be ignored the same way CLAUDE.md is — the bite-test (Task 8) decides, and a null result is a valid honest outcome.

---

## File Structure

**Create:**
- `src/super_harness/core/conformance_sensor.py` — agnostic core: relevance resolution + verdict + feedback-text rendering. One responsibility: "given a changed file, produce the authoring-time conformance verdict + advisory text."
- `tests/unit/core/test_conformance_sensor.py` — unit tests for the core.
- `tests/integration/daemon/test_hook_entry_post.py` — integration tests for the new PostToolUse hook path.

**Modify:**
- `src/super_harness/core/decisions.py` — add optional `applies_to` frontmatter field (parse + serialize round-trip).
- `docs/decisions/d-core-is-base.md` — add `applies_to` frontmatter (no body change → hash unaffected).
- `src/super_harness/adapters/agent/_settings_merge.py` — add `merge_post_tool_use_hook`.
- `src/super_harness/adapters/agent/claude_code.py` — register PostToolUse in `install_hooks`; add `format_post_edit_feedback`; update `on_uninstall` reasoning.
- `src/super_harness/adapters/__init__.py` — add `format_post_edit_feedback` to `AgentAdapter` ABC (non-abstract default = floor-only).
- `src/super_harness/daemon/hook_entry.py` — add `--event post-tool-use` parsing + a non-blocking, fail-open claude-code post path.
- `tests/unit/adapters/test_settings_merge.py`, `tests/unit/adapters/test_claude_code.py` — extend for the new merge + adapter method.

---

## Task 1: `applies_to` frontmatter field on Decision

**Files:**
- Modify: `src/super_harness/core/decisions.py` (Decision dataclass ~line 33, `parse_decision_file` ~line 140, `serialize_decision` ~line 233)
- Test: `tests/unit/core/test_decisions.py` (extend; if absent, create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_decisions.py
from pathlib import Path
from super_harness.core.decisions import parse_decision_file, serialize_decision

def test_applies_to_parsed_and_roundtrips(tmp_path: Path):
    p = tmp_path / "d-x.md"
    p.write_text(
        "---\n"
        "id: d-x\n"
        "status: ratified\n"
        "applies_to:\n"
        "  - 'src/super_harness/core/**'\n"
        "---\n"
        "body text\n"
    )
    d = parse_decision_file(p)
    assert d.applies_to == ("src/super_harness/core/**",)
    # round-trip must preserve applies_to (serialize is an allow-list)
    assert "applies_to" in serialize_decision(d)

def test_applies_to_absent_defaults_empty(tmp_path: Path):
    p = tmp_path / "d-y.md"
    p.write_text("---\nid: d-y\nstatus: ratified\n---\nbody\n")
    assert parse_decision_file(p).applies_to == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -k applies_to -v`
Expected: FAIL — `Decision` has no attribute `applies_to`.

- [ ] **Step 3: Implement**

In `decisions.py`, add to the `Decision` dataclass (after `check`/other optional fields):

```python
    applies_to: tuple[str, ...] = ()
```

In `parse_decision_file`, where the `Decision(...)` is constructed, add:

```python
        applies_to=tuple(data.get("applies_to") or ()),
```

In `serialize_decision`, after the reconciled_anchors block and before `fm_text = ...`:

```python
    if decision.applies_to:
        fm["applies_to"] = list(decision.applies_to)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_decisions.py -k applies_to -v`
Expected: PASS.

- [ ] **Step 5: Add `applies_to` to the real decision (frontmatter only — body hash unchanged)**

Edit `docs/decisions/d-core-is-base.md` frontmatter, inserting a line after `id: d-core-is-base` (do NOT touch anything below the closing `---`, so `ratified_text_hash` stays valid):

```yaml
applies_to:
  - 'src/super_harness/core/**'
```

- [ ] **Step 6: Verify integrity gate still clean (proves hash unaffected by frontmatter)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness decision check`
Expected: no integrity/hash violation for `d-core-is-base` (exit 0 on that check; frontmatter is outside the body hash).

- [ ] **Step 7: Commit**

```bash
git add src/super_harness/core/decisions.py tests/unit/core/test_decisions.py docs/decisions/d-core-is-base.md
git commit -m "feat(decisions): optional applies_to frontmatter for file-scope relevance"
```

---

## Task 2: Relevance resolver + verdict core

**Files:**
- Create: `src/super_harness/core/conformance_sensor.py`
- Test: `tests/unit/core/test_conformance_sensor.py`

Reuses `load_decisions` (returns `(list[Decision], errors)`), `run_executable_checks` (returns `list[CheckFailure]`), and `decision_tier`/tier-1 semantics from `core`. The resolver deliberately does NOT use `select_changed` (its anchor-intersection is unsound — design §3a).

- [ ] **Step 1: Write the failing test (relevance)**

```python
# tests/unit/core/test_conformance_sensor.py
from pathlib import Path
from super_harness.core.decisions import Decision
from super_harness.core.conformance_sensor import relevant_decisions

def _d(id, applies_to=(), status="ratified", check="true"):
    return Decision(id=id, status=status, applies_to=applies_to, check=check, body="b")

def test_relevant_matches_glob():
    ds = [_d("d-core", applies_to=("src/super_harness/core/**",))]
    assert [d.id for d in relevant_decisions(ds, "src/super_harness/core/foo.py")] == ["d-core"]

def test_irrelevant_file_excluded():
    ds = [_d("d-core", applies_to=("src/super_harness/core/**",))]
    assert relevant_decisions(ds, "src/super_harness/cli/plan.py") == []

def test_no_applies_to_is_conservative_always_relevant():
    ds = [_d("d-anywhere", applies_to=())]
    assert [d.id for d in relevant_decisions(ds, "any/path.py")] == ["d-anywhere"]

def test_non_ratified_excluded():
    ds = [_d("d-prop", applies_to=("**",), status="proposed")]
    assert relevant_decisions(ds, "x.py") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_conformance_sensor.py -k relevant -v`
Expected: FAIL — module/function does not exist.

- [ ] **Step 3: Implement `relevant_decisions`**

```python
# src/super_harness/core/conformance_sensor.py
"""Authoring-time decision-conformance sensor core (design 2026-07-01).

Agent-agnostic: given the file an agent just changed, resolve which ratified
tier-1 decisions apply to it, run their checks, and render a deterministic
advisory. No agent knowledge, no daemon, no LLM. Delivery is per-agent (adapters).
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from super_harness.core.check_runner import CheckFailure, run_executable_checks
from super_harness.core.decisions import Decision, load_decisions

POST_CHECK_TIMEOUT = 15  # bounded latency budget for the authoring path (design §5)


def relevant_decisions(decisions: list[Decision], changed_file: str) -> list[Decision]:
    """Ratified decisions whose `applies_to` globs match `changed_file`.

    A decision with an empty `applies_to` is conservatively treated as always
    relevant (never silently skip a rule for lack of a scope declaration).
    Only ratified decisions are considered (proposed/retired are inert).
    `changed_file` is a workspace-relative POSIX path.
    """
    out: list[Decision] = []
    for d in decisions:
        if d.status != "ratified":
            continue
        if not d.applies_to or any(fnmatch(changed_file, g) for g in d.applies_to):
            out.append(d)
    return out
```

- [ ] **Step 4: Run to verify relevance passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_conformance_sensor.py -k relevant -v`
Expected: PASS.

- [ ] **Step 5: Write failing test (verdict + rendering)**

```python
# append to tests/unit/core/test_conformance_sensor.py
from super_harness.core.conformance_sensor import Verdict, Violation, render_feedback

def test_render_feedback_names_decision_and_detail():
    v = Verdict(violations=[Violation(
        decision_id="d-core-is-base",
        detail="src.super_harness.core is not allowed to import super_harness.sensors",
        decision_doc_path="docs/decisions/d-core-is-base.md",
    )])
    text = render_feedback(v)
    assert "d-core-is-base" in text
    assert "super_harness.sensors" in text
    assert "docs/decisions/d-core-is-base.md" in text

def test_render_feedback_empty_is_none():
    assert render_feedback(Verdict(violations=[])) is None
```

- [ ] **Step 6: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_conformance_sensor.py -k render -v`
Expected: FAIL — `Verdict`/`Violation`/`render_feedback` undefined.

- [ ] **Step 7: Implement verdict types + rendering + the top-level scan**

```python
# append to src/super_harness/core/conformance_sensor.py

@dataclass(frozen=True)
class Violation:
    decision_id: str
    detail: str            # the check's own violation output (CheckFailure.detail)
    decision_doc_path: str  # workspace-relative path to the decision record


@dataclass(frozen=True)
class Verdict:
    violations: list[Violation]


def render_feedback(verdict: Verdict) -> str | None:
    """Render the agent-agnostic advisory text, or None if there is nothing to say.

    Deliberately carries only what the mechanism actually produced (design §3b):
    the decision id, the check's own detail, and a pointer to the decision doc.
    No fabricated fix text. Framed as advisory + ignorable-if-mid-step (design §5).
    """
    if not verdict.violations:
        return None
    lines = [
        "super-harness authoring-time check — you just edited a file governed by a "
        "ratified decision, and its check is failing:",
    ]
    for v in verdict.violations:
        lines.append(f"  • {v.decision_id}: {v.detail}")
        lines.append(f"    (rule + counterexample: {v.decision_doc_path})")
    lines.append(
        "This is advice, not a block — the edit stands. If you are mid multi-step "
        "change and will fix this next, ignore it. Otherwise, correct it now; the "
        "merge gate will otherwise reject it later."
    )
    return "\n".join(lines)


def scan_changed_file(workspace_root: Path, changed_file: str) -> Verdict:
    """Run the relevant ratified tier-1 checks for `changed_file`, return a Verdict.

    Pure-ish: reads decision records + runs their checks (subprocess) read-only.
    Never raises for a normal check failure — a failing check is data, not an error.
    """
    decisions, _errors = load_decisions(workspace_root)
    relevant = relevant_decisions(decisions, changed_file)
    if not relevant:
        return Verdict(violations=[])
    failures: list[CheckFailure] = run_executable_checks(
        workspace_root, relevant, timeout=POST_CHECK_TIMEOUT
    )
    by_id = {d.id: d for d in relevant}
    violations = [
        Violation(
            decision_id=f.id,
            detail=f.detail,
            decision_doc_path=f"docs/decisions/{f.id}.md",
        )
        for f in failures
    ]
    _ = by_id  # reserved for future per-decision doc-path override
    return Verdict(violations=violations)
```

- [ ] **Step 8: Run to verify all core tests pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/core/test_conformance_sensor.py -v`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add src/super_harness/core/conformance_sensor.py tests/unit/core/test_conformance_sensor.py
git commit -m "feat(core): authoring-time conformance sensor core (relevance + verdict + render)"
```

---

## Task 3: `merge_post_tool_use_hook` in settings-merge

**Files:**
- Modify: `src/super_harness/adapters/agent/_settings_merge.py` (mirror `merge_pre_tool_use_hook` ~line 63; `__all__` ~line 36; markers ~line 45)
- Test: `tests/unit/adapters/test_settings_merge.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/adapters/test_settings_merge.py
import json
from pathlib import Path
from super_harness.adapters.agent._settings_merge import merge_post_tool_use_hook

def test_merge_post_tool_use_adds_entry(tmp_path: Path):
    hooks = tmp_path / "settings.json"
    merge_post_tool_use_hook(hooks, command="/abs/hook --event post-tool-use",
                             matcher="Edit|Write|MultiEdit", marker="post-tool-use")
    data = json.loads(hooks.read_text())
    entries = data["hooks"]["PostToolUse"]
    assert any("post-tool-use" in h["command"]
               for e in entries for h in e["hooks"])

def test_merge_post_tool_use_preserves_pretooluse(tmp_path: Path):
    hooks = tmp_path / "settings.json"
    hooks.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Edit", "hooks": [{"type": "command", "command": "keepme"}]}]}}))
    merge_post_tool_use_hook(hooks, command="/abs/hook --event post-tool-use",
                             matcher="Edit|Write|MultiEdit", marker="post-tool-use")
    data = json.loads(hooks.read_text())
    assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "keepme"
    assert "PostToolUse" in data["hooks"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_settings_merge.py -k post_tool_use -v`
Expected: FAIL — `merge_post_tool_use_hook` undefined.

- [ ] **Step 3: Implement (mirror the pre-tool-use helper)**

In `_settings_merge.py`: add `"merge_post_tool_use_hook"` to `__all__`; add a helper symmetric to `merge_pre_tool_use_hook` but writing the `"PostToolUse"` event list. Reuse the existing `_ensure_hooks_dict`, `_strip_entries`, `_hook_entry`, and the atomic write-if-different logic. Add a `_ensure_post_tool_use_list(hooks)` mirroring `_ensure_pre_tool_use_list`:

```python
def _ensure_post_tool_use_list(hooks: dict[str, Any]) -> list[Any]:
    entries = hooks.setdefault("PostToolUse", [])
    if not isinstance(entries, list):
        raise ValueError(
            f'"PostToolUse" must be a JSON array, got {type(entries).__name__}; '
            "the settings file looks corrupt — fix it before installing the hook."
        )
    return entries


def merge_post_tool_use_hook(
    path: Path, *, command: str, matcher: str, marker: str,
) -> None:
    """Add/replace super-harness's PostToolUse hook; preserve all other hooks.

    Symmetric with merge_pre_tool_use_hook: computes the desired settings
    (existing PostToolUse entry for `command` replaced by a fresh one) and writes
    it only if it differs from disk.
    """
    settings = _load_settings(path)          # reuse the same loader as pre
    hooks = _ensure_hooks_dict(settings)
    post = _ensure_post_tool_use_list(hooks)
    _strip_entries(post, marker)
    post.append(_hook_entry(command, matcher))
    _write_if_changed(path, settings)        # reuse the same atomic writer as pre
```

(Use the exact helper names present in the file — align `_load_settings` / `_write_if_changed` with whatever `merge_pre_tool_use_hook` currently calls.)

- [ ] **Step 4: Run to verify it passes**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_settings_merge.py -k post_tool_use -v`
Expected: PASS.

- [ ] **Step 5: Run the whole settings-merge suite (no regression to pre/session merges)**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_settings_merge.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/super_harness/adapters/agent/_settings_merge.py tests/unit/adapters/test_settings_merge.py
git commit -m "feat(adapters): merge_post_tool_use_hook (symmetric to pre-tool-use)"
```

---

## Task 4: `AgentAdapter.format_post_edit_feedback` seam + Claude Code delivery

**Files:**
- Modify: `src/super_harness/adapters/__init__.py` (AgentAdapter ABC — add non-abstract default method)
- Modify: `src/super_harness/adapters/agent/claude_code.py` (override + PostToolUse install + uninstall note)
- Test: `tests/unit/adapters/test_claude_code.py`, `tests/unit/adapters/test_protocol.py`

- [ ] **Step 1: Write the failing test (Claude delivery format)**

```python
# tests/unit/adapters/test_claude_code.py
import json
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

def test_format_post_edit_feedback_wraps_additional_context():
    out = ClaudeCodeAdapter().format_post_edit_feedback("VIOLATION TEXT")
    obj = json.loads(out)
    assert obj["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert obj["hookSpecificOutput"]["additionalContext"] == "VIOLATION TEXT"

def test_format_post_edit_feedback_none_is_empty_string():
    assert ClaudeCodeAdapter().format_post_edit_feedback(None) == ""
```

- [ ] **Step 2: Write the failing test (ABC default = floor-only, empty)**

```python
# tests/unit/adapters/test_protocol.py  (add)
def test_agentadapter_default_post_edit_feedback_is_empty():
    # A minimal concrete adapter that does NOT override the method degrades to
    # floor-only: it returns "" (no delivery), never raising.
    from super_harness.adapters import AgentAdapter
    from pathlib import Path

    class _Bare(AgentAdapter):
        name = "bare"; version = "0.1.0"; capabilities = {}
        def detect(self, w: Path) -> bool: return False
        def install_hooks(self, w: Path) -> None: ...
        def inject_context(self, c: str) -> str: return ""
        def agents_md_subsection(self) -> str: return ""
    assert _Bare().format_post_edit_feedback("x") == ""
```

- [ ] **Step 3: Run to verify both fail**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_claude_code.py -k post_edit tests/unit/adapters/test_protocol.py -k post_edit -v`
Expected: FAIL — method undefined.

- [ ] **Step 4: Implement the ABC default (non-abstract, floor-only)**

In `adapters/__init__.py`, add to `AgentAdapter` (a non-abstract method, like `on_uninstall`):

```python
    def format_post_edit_feedback(self, feedback_text: str | None) -> str:
        """Format an authoring-time conformance advisory for this agent's post-edit
        feedback channel; return "" to deliver nothing.

        Default = floor-only: agents whose post-tool hook cannot inject text back to
        the model (e.g. GitHub Copilot) do not override this, so they deliver nothing
        and rely on the CI cold-path floor. Agents that CAN feed back (Claude Code,
        Codex) override it. `feedback_text` is the agent-agnostic advisory (or None).
        """
        return ""
```

- [ ] **Step 5: Implement the Claude Code override**

In `claude_code.py`, add to `ClaudeCodeAdapter`:

```python
    def format_post_edit_feedback(self, feedback_text: str | None) -> str:
        """Wrap the advisory in Claude Code's PostToolUse additionalContext envelope.

        Claude Code reads `hookSpecificOutput.additionalContext` as a system-reminder
        on its next model request (self-correct channel; does NOT block the edit).
        Returns "" when there is nothing to deliver.
        """
        import json
        if not feedback_text:
            return ""
        return json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": feedback_text,
            }
        })
```

- [ ] **Step 6: Run to verify format tests pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_claude_code.py -k post_edit tests/unit/adapters/test_protocol.py -k post_edit -v`
Expected: PASS.

- [ ] **Step 7: Register the PostToolUse hook in `install_hooks`**

In `claude_code.py`'s `install_hooks`, after the existing PreToolUse + SessionStart merges, add a PostToolUse merge. Import `merge_post_tool_use_hook` alongside the existing merge imports. Use a distinct marker constant (e.g. `_POST_MARKER = "--event post-tool-use"` or a dedicated marker string) and the same tool matcher used for edits:

```python
        merge_post_tool_use_hook(
            settings_path,
            command=f"{resolved_hook} --agent claude-code --event post-tool-use",
            matcher=_MATCHER,      # same Edit|Write|MultiEdit matcher used for pre
            marker="--event post-tool-use",
        )
```

(Match the exact `settings_path` variable + `resolved_hook` resolution `install_hooks` already uses for the PreToolUse entry.)

- [ ] **Step 8: Write + run an install test (three hooks land, all preserved)**

```python
# tests/unit/adapters/test_claude_code.py
import json
from pathlib import Path
from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter

def test_install_registers_post_tool_use(tmp_path: Path, monkeypatch):
    # ClaudeCodeAdapter.install_hooks resolves the hook binary via shutil.which;
    # stub it so the test does not depend on an installed console script.
    import super_harness.adapters.agent.claude_code as cc
    monkeypatch.setattr(cc.shutil, "which", lambda _n: "/abs/super-harness-hook")
    (tmp_path / ".claude").mkdir()
    ClaudeCodeAdapter().install_hooks(tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
    events = settings["hooks"]
    assert "PreToolUse" in events and "PostToolUse" in events
    assert any("post-tool-use" in h["command"]
               for e in events["PostToolUse"] for h in e["hooks"])
```

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_claude_code.py -k install_registers_post -v`
Expected: PASS. (Adjust the settings filename/`which` target to match the adapter's real code if the stub differs.)

- [ ] **Step 9: Update `on_uninstall` reasoning for the third hook**

The current `on_uninstall` (`claude_code.py:257`) restores the earliest backup based on "install writes two merges." Adding PostToolUse makes it three merges. Update the comment + any backup-count logic so uninstall still restores the pre-install state (or explicitly strips all three markers). Add a test asserting `on_uninstall` leaves no super-harness PostToolUse entry:

```python
def test_uninstall_removes_post_tool_use(tmp_path: Path, monkeypatch):
    import super_harness.adapters.agent.claude_code as cc
    monkeypatch.setattr(cc.shutil, "which", lambda _n: "/abs/super-harness-hook")
    (tmp_path / ".claude").mkdir()
    a = ClaudeCodeAdapter()
    a.install_hooks(tmp_path)
    a.on_uninstall(tmp_path)
    settings_file = tmp_path / ".claude" / "settings.local.json"
    if settings_file.exists():
        settings = json.loads(settings_file.read_text())
        post = settings.get("hooks", {}).get("PostToolUse", [])
        assert not any("post-tool-use" in h["command"]
                       for e in post for h in e["hooks"])
```

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit/adapters/test_claude_code.py -v`
Expected: PASS (all).

- [ ] **Step 10: Commit**

```bash
git add src/super_harness/adapters/__init__.py src/super_harness/adapters/agent/claude_code.py tests/unit/adapters/
git commit -m "feat(adapters): post-edit feedback seam + Claude Code PostToolUse delivery"
```

---

## Task 5: Non-blocking PostToolUse path on the hook binary

**Files:**
- Modify: `src/super_harness/daemon/hook_entry.py` (add `--event` parse in `main`; add `_run_claude_code_post`)
- Test: `tests/integration/daemon/test_hook_entry_post.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/daemon/test_hook_entry_post.py
import json, subprocess, sys, textwrap
from pathlib import Path

def _run_hook(cwd: Path, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c",
         "from super_harness.daemon.hook_entry import main; main()",
         "--agent", "claude-code", "--event", "post-tool-use"],
        input=json.dumps(payload), capture_output=True, text=True, cwd=str(cwd),
    )

def test_post_no_harness_root_is_silent_allow(tmp_path: Path):
    r = _run_hook(tmp_path, {"tool_name": "Edit",
                             "tool_input": {"file_path": "x.py"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""   # nothing to deliver

def test_post_violation_emits_additional_context(tmp_path: Path):
    # Minimal harness workspace with one ratified tier-1 decision whose check
    # always fails and applies to the edited file.
    (tmp_path / ".harness").mkdir()
    dec = tmp_path / "docs" / "decisions"
    dec.mkdir(parents=True)
    (dec / "d-fail.md").write_text(textwrap.dedent("""\
        ---
        id: d-fail
        status: ratified
        applies_to:
          - 'src/**'
        ---
        always fails
        ```check
        false
        ```
        ```counterexample path=src/_ce.py
        x = 1
        ```
        """))
    r = _run_hook(tmp_path, {"tool_name": "Edit",
                             "tool_input": {"file_path": "src/foo.py"}})
    assert r.returncode == 0                       # NON-blocking, always exit 0
    obj = json.loads(r.stdout)
    assert obj["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "d-fail" in obj["hookSpecificOutput"]["additionalContext"]

def test_post_irrelevant_file_is_silent(tmp_path: Path):
    (tmp_path / ".harness").mkdir()
    dec = tmp_path / "docs" / "decisions"; dec.mkdir(parents=True)
    (dec / "d-fail.md").write_text(textwrap.dedent("""\
        ---
        id: d-fail
        status: ratified
        applies_to:
          - 'src/**'
        ---
        body
        ```check
        false
        ```
        ```counterexample path=src/_ce.py
        x = 1
        ```
        """))
    r = _run_hook(tmp_path, {"tool_name": "Edit",
                             "tool_input": {"file_path": "docs/notes.md"}})
    assert r.returncode == 0
    assert r.stdout.strip() == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/daemon/test_hook_entry_post.py -v`
Expected: FAIL — `--event` not parsed; post path routes into the pre gate (or errors).

- [ ] **Step 3: Parse `--event` in `main` and route the post path**

In `hook_entry.py` `main()`, before the current `--agent` handling, extract an optional `--event` flag (default `pre-tool-use`). Keep the existing pre path untouched when `--event` is absent or `pre-tool-use`. When `--event post-tool-use` + `--agent claude-code`, call the new `_run_claude_code_post()`:

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
            if event == "post-tool-use":
                _run_claude_code_post()
            else:
                _run_claude_code_shim()
            return
        if agent == "codex":
            _run_codex_shim()   # post path for codex is a later cut
            return
        sys.stderr.write(f"super-harness-hook: unknown --agent {agent!r}\n")
        sys.exit(0)
    _run_positional(argv)
```

- [ ] **Step 4: Implement `_run_claude_code_post` (non-blocking, fail-open)**

```python
def _run_claude_code_post() -> None:
    """Claude Code PostToolUse: read stdin JSON, run the authoring-time conformance
    check for the changed file, and emit an additionalContext advisory on stdout.

    ALWAYS exits 0 (non-blocking: the edit already happened). Emits nothing on any
    error / no harness / no violation (fail-open — Axiom 1). Never blocks.
    """
    import json
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)
    tool_input = data.get("tool_input")
    changed = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not changed:
        sys.exit(0)
    try:
        root = find_harness_root(Path.cwd())
    except HarnessNotInitialized:
        sys.exit(0)
    try:
        rel = _to_workspace_rel(root, changed)
        from super_harness.core.conformance_sensor import render_feedback, scan_changed_file
        from super_harness.adapters.agent.claude_code import ClaudeCodeAdapter
        verdict = scan_changed_file(root, rel)
        text = render_feedback(verdict)
        out = ClaudeCodeAdapter().format_post_edit_feedback(text)
    except Exception:
        sys.exit(0)   # fail-open: never let the sensor break the agent
    if out:
        sys.stdout.write(out)
    sys.exit(0)


def _to_workspace_rel(root: Path, changed: str) -> str:
    """Normalise the hook-provided path to a workspace-relative POSIX string.

    Claude Code sends `tool_input.file_path` (absolute or cwd-relative). We derive
    it relative to the harness root; a path outside the root falls back to its
    given form (relevance globs simply won't match)."""
    p = Path(changed)
    p = p if p.is_absolute() else (Path.cwd() / p)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(changed).as_posix()
```

- [ ] **Step 5: Run to verify the post path tests pass**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/daemon/test_hook_entry_post.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Verify the existing PreToolUse path is untouched**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/integration/daemon/test_hook_entry.py -v`
Expected: PASS (no regression — `--event` absent still routes to the pre gate).

- [ ] **Step 7: Commit**

```bash
git add src/super_harness/daemon/hook_entry.py tests/integration/daemon/test_hook_entry_post.py
git commit -m "feat(hook): non-blocking PostToolUse authoring-time conformance path (claude-code)"
```

---

## Task 6: Full suite + AGENTS.md / docs sync

**Files:**
- Possibly Modify: `AGENTS.md` (regen), `docs/adapters/claude-code.md` (document the new PostToolUse advisory)

- [ ] **Step 1: Run the full unit + integration suite**

Run: `PATH="$(pwd)/.venv/bin:$PATH" pytest tests/unit tests/integration -q`
Expected: PASS (no regressions).

- [ ] **Step 2: If CLI/adapter surface changed, regenerate AGENTS.md + doc-check**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md -y && PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check --fix`
Expected: clean (no drift). Review any AGENTS.md diff — the Claude Code subsection may now mention the authoring-time advisory.

- [ ] **Step 3: Commit any sync output**

```bash
git add AGENTS.md docs/adapters/claude-code.md
git commit -m "docs: regenerate AGENTS.md for authoring-time PostToolUse advisory"
```

---

## Task 7: LIVE spike — Claude `additionalContext` reaches the model (load-bearing)

Not a unit test — a real verification (design §7): `post_tool_use_hook: True` only means the hook exists, not that the feedback lands. Do this in a scratch workspace so a bug can't affect the real repo.

- [ ] **Step 1: Set up a scratch workspace** with super-harness initialised, the Claude Code adapter installed (`super-harness adapter install claude-code`), and one ratified tier-1 decision whose check fails for a known file.

- [ ] **Step 2: Drive Claude Code** (headless `-p` or interactive) to edit that file. Put a unique marker string in the rendered advisory (temporarily) and confirm Claude's next turn reflects having read it (echoes/reacts to the marker).

- [ ] **Step 3: Record the result** in `private/research/` (LIVE evidence): did the advisory reach the model, and was the edit non-blocked? If it does NOT land, that is a design-invalidating finding — stop and surface it (do not ship a sensor whose feedback never arrives).

---

## Task 8: Dogfood bite-test — the H experiment (value-bleed proof)

The experiment that decides hypothesis H (design §1/§7). A null result is a valid, reportable outcome.

- [ ] **Step 1: In a live self-host change**, with the sensor installed, have Claude Code edit a `core/` file to import from `sensors/` (a genuine `d-core-is-base` violation — mirror the real `core.review_bundle -> sensors` edge the decision documents).

- [ ] **Step 2: Observe + record:** (a) did the authoring-time advisory fire naming `d-core-is-base`; (b) did Claude **self-correct before** the merge gate / a human; (c) the **measured latency** of the whole-graph import-linter run on the post path (design §5); (d) how much intermediate-state noise occurred during any multi-file work.

- [ ] **Step 3: Write the honest verdict** into `private/research/` and the capability-convergence ledger: H supported (self-corrected earlier than the floor would have — value bleed) OR H falsified (ignored like CLAUDE.md — sensor is token-noise over the floor). Either is a valid deliverable; only an oversold result is a failure.

---

## Self-review checklist (run before execution)

- **Spec coverage:** design §2 IN items → Task 1–5 (relevance/verdict, non-blocking hook, Claude delivery, no fabricated fix); §3a relevance → Task 1–2; §3b honest verdict → Task 2; §4 install/uninstall + input-from-tool_input → Task 4–5; §5 latency budget (`POST_CHECK_TIMEOUT`) → Task 2, measured in Task 8; §7 LIVE spikes → Task 7–8; §8 success criteria → Tasks 5/7/8. Codex delivery + 9th capability key are correctly ABSENT (out of scope).
- **Placeholder scan:** no TBD/TODO; every code step has concrete code; test steps have real assertions.
- **Type consistency:** `Verdict`/`Violation`/`render_feedback`/`scan_changed_file`/`relevant_decisions` used consistently across Task 2 and Task 5; `format_post_edit_feedback` signature identical in ABC (Task 4 step 4), Claude override (step 5), and hook call site (Task 5 step 4); `merge_post_tool_use_hook` signature identical in Task 3 and the install call (Task 4 step 7).
