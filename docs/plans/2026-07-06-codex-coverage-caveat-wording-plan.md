---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: 2026-07-06-codex-coverage-caveat-wording
stage: plan
scope:
  files:
    - src/super_harness/adapters/agent/codex.py
    - tests/unit/adapters/test_codex.py
    - docs/plans/2026-07-06-codex-coverage-caveat-wording-plan.md
tier_hint: Micro
---

# Fix Codex coverage-caveat wording — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for the one code change. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reword the `_AGENTS_MD_SUBSECTION` coverage caveat in `codex.py` so it stops reading as self-contradictory (surfaced by PR#74 code review, logged to OPEN-ITEMS).

**Problem:** The injected AGENTS.md subsection says both "`Bash` is never gated" and "Codex PreToolUse intercepts only simple shell + `apply_patch`". These are two *different* layers conflated by the word "intercepts": (a) what Codex's PreToolUse mechanism *surfaces* to a hook = shell + `apply_patch` (it cannot see WebSearch / non-shell / non-MCP tools — the real coverage limit), and (b) what super-harness *gates* = the edit tools via matcher `^(apply_patch|Edit|Write)$`, deliberately never `Bash` (so the kill-switch works). Verified against the design doc `docs/plans/2026-06-25-codex-agent-adapter-design.md` §3.3/§4.1 and the matcher in `codex.py:36`. The fix separates "surface" (platform visibility) from "gate" (our config).

**Architecture:** One string edit + a TDD regression guard. Docs-string only; no behavior change (the hook matcher/commands are untouched). `codex.md` (merged in PR#74) already states an accurate, non-contradictory caveat, so it is out of scope. The repo's own AGENTS.md carries no Codex subsection (Codex is not installed here), so no regen. Runs through the self-host lifecycle.

**Tech Stack:** Python string. Verification via `pytest` + `super-harness doc/decision check`.

---

## Task 1: TDD — lock out the contradictory phrasing, then reword

**Files:** Modify `tests/unit/adapters/test_codex.py`, `src/super_harness/adapters/agent/codex.py`

- [ ] **Step 1: Add a failing assertion** to `test_agents_md_subsection_has_trust_and_caveat` in `tests/unit/adapters/test_codex.py`. After the existing `assert "WebSearch" in sub` line, add:

```python
    # Coverage caveat must not conflate what Codex *surfaces* to a hook with what
    # super-harness *gates*: the old "intercepts only simple shell" phrasing read as
    # a contradiction against "Bash is never gated" (PR#74 review).
    assert "simple shell" not in sub.lower()
    assert "Codex surfaces only shell commands" in sub  # caveat separates surface from gating
```

- [ ] **Step 2: Run it to confirm RED.**

Run (venv on PATH): `PYTHONPATH=src pytest tests/unit/adapters/test_codex.py::test_agents_md_subsection_has_trust_and_caveat -q`
Expected: FAIL — the current subsection contains "simple shell" (and lacks the caveat-specific "Codex surfaces only shell commands" phrase).

- [ ] **Step 3: Reword the caveat** in `src/super_harness/adapters/agent/codex.py`. Replace this exact block:

```
**Coverage caveat:** Codex PreToolUse intercepts only simple shell + `apply_patch`
— it does NOT see `WebSearch` or other non-shell/non-MCP tools, so real-time
coverage is narrower than Claude Code's. The CI cold floor backs the gap.
```

with:

```
**Coverage caveat:** Codex surfaces only shell commands and `apply_patch` to
PreToolUse hooks — it does NOT expose `WebSearch` or other non-shell/non-MCP
tools. super-harness gates `apply_patch` edits (never `Bash`, so the kill-switch
keeps working); an action taken through a tool Codex does not surface isn't caught
in real time, so real-time coverage is narrower than Claude Code's. The CI cold
floor backs the gap.
```

- [ ] **Step 4: Run to confirm GREEN.**

Run: `PYTHONPATH=src pytest tests/unit/adapters/test_codex.py -q`
Expected: PASS (the file's other assertions — `/hooks`, `apply_patch`, `WebSearch` present — still hold; `apply_patch` and `WebSearch` are retained in the new wording).

- [ ] **Step 5: Full adapter + suite check + gates.**

Run: `PYTHONPATH=src pytest -q` → expect ~1629 passed (one new assertion, same test).
Run: `super-harness doc check` → 0; `super-harness decision check` → 0.

- [ ] **Step 6: Commit.**

```bash
git add src/super_harness/adapters/agent/codex.py tests/unit/adapters/test_codex.py
git commit -m "fix(codex): reword coverage caveat to separate tool-surface from gating"
```

---

## Self-review

- [ ] Reworded caveat keeps the tokens the existing test needs (`apply_patch`, `WebSearch`, `/hooks` from the trust step) → other assertions still pass.
- [ ] No behavior change: matcher `^(apply_patch|Edit|Write)$`, hook commands, capabilities untouched.
- [ ] Accurate vs source: "surfaces shell + apply_patch, not WebSearch" = design §4.1; "gates apply_patch, never Bash" = matcher + §3.3.
- [ ] Scope = 3 files (codex.py, test_codex.py, plan). No AGENTS.md regen (repo has no Codex subsection). doc-refs N/A (Python string, not a gated doc).

## Landing

`change start` → `adapter scan-once superpowers` → plan-reviewer approve → `implementation start` → Task 1 → `done` → `review prepare` → code review (codex — it raised the finding) → code-reviewer approve `--verdict-file` → `attest write` + commit → `attest verify` → PR → CI → merge → `on-merge`. After merge: mark the codex.py wording item resolved in `private/OPEN-ITEMS.md`.
