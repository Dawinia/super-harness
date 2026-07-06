---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: 2026-07-06-quickstart-adapters-and-zh-readme
stage: plan
scope:
  files:
    - README.md
    - README.zh-CN.md
    - docs/adapters/codex.md
    - docs/overview.md
    - docs/limitations.md
    - docs/getting-started.md
    - docs/concepts.md
    - docs/adapters/claude-code.md
    - docs/README.md
    - docs/ARCHITECTURE.md
    - docs/plans/2026-07-06-quickstart-adapters-and-zh-readme-design.md
    - docs/plans/2026-07-06-quickstart-adapters-and-zh-readme-plan.md
tier_hint: Normal
---

# Quickstart Agent Adapters + Chinese README — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Design:** see `docs/plans/2026-07-06-quickstart-adapters-and-zh-readme-design.md`.

**Goal:** Show agent-adapter install (Claude Code + Codex) in the README Quickstart, ship a new `docs/adapters/codex.md`, reconcile every "Codex v0.2+ / Claude Code is the only agent adapter" mention to "Codex ships (experimental)", and add a Chinese `README.zh-CN.md` linked from the README.

**Architecture:** Docs-only. Resolves the PR#73-deferred F2 in the "Codex is a shipped experimental v0.1 agent adapter" direction. Two new files (codex.md, README.zh-CN.md) + seven edits; no code, no new decision. Cursor / Aider stay v0.2+ (not registered built-ins). Runs through the self-host lifecycle; the merge gate is satisfied by a plan-declared 12-file scope + attestation.

**Tech Stack:** Markdown prose. Verification via `super-harness doc check` / `decision check` + the existing CI suite. Codex facts sourced from `src/super_harness/adapters/agent/codex.py` (2026-07-06).

---

## Authoring invariants

- **doc-refs gate:** adapter identifiers (`claude-code`, `codex`, `openspec`, `superpowers`, `plain`) are lowercase/hyphen → safe inline. Proper nouns — Codex, Claude Code, Cursor, Aider, WebSearch, OpenAI — must be PLAIN TEXT (no inline backticks). `super-harness doc check` (Task 10) is the backstop.
- **Codex honesty:** always pair "Codex ships" with (a) the `/hooks` trust step (gate INACTIVE until trusted) and (b) the coverage caveat (PreToolUse intercepts only simple shell + `apply_patch`). Never imply Codex is a full peer of Claude Code without these.
- **One home:** the Chinese README links to the same English `docs/`; do not translate the `docs/` tree.

---

## Task 1: Create `docs/adapters/codex.md`

**Files:** Create `docs/adapters/codex.md`

- [ ] **Step 1: Write the file** with EXACTLY the literal Markdown between the markers.

=== BEGIN codex.md ===
# Codex adapter

The Codex adapter wires the OpenAI Codex CLI to super-harness. It is the second
agent adapter in v0.1 and is **experimental** (API stability may change). Claude
Code remains the reference adapter — see [Claude Code adapter](claude-code.md).

It registers three hooks into `<repo>/.codex/hooks.json` (the same shape as
Claude's settings.json hooks block; only the matcher + marker differ): a
**PreToolUse** gate, a **SessionStart** context hook, and a turn-end **Stop**
authoring-conformance advisory.

## Installed hooks

- **PreToolUse gate** — blocks `apply_patch` (and `Edit` / `Write`) edits when the
  current lifecycle state forbids the mutation, via stdout `permissionDecision`.
- **SessionStart** — emits developer context at the start of a session.
- **Stop (turn-end feedback)** — runs the authoring-time conformance check for any
  ratified decision that opted in (`authoring_time: true`) and feeds a failure back
  as a non-blocking advisory.

## Install

```bash
super-harness adapter install codex
```

This writes the hooks into `.codex/hooks.json`.

> **Required trust step.** After `adapter install codex` the gate is **INACTIVE**
> until you run `/hooks` in Codex and trust the super-harness hook. Codex skips
> new or changed hooks until a human trusts them (trust is keyed to the hook's
> hash); if you reinstall or relocate the `super-harness-hook` binary, re-trust it.

On a pre-existing repo, also run `super-harness sync --gitignore` so
`.codex/hooks.json` is ignored by git.

**Coverage caveat.** Codex PreToolUse intercepts only simple shell +
`apply_patch` — it does not see WebSearch or other non-shell / non-MCP tools, so
real-time coverage is narrower than Claude Code's. The CI cold-path gates back the
gap: even an edit the hot path misses is caught before merge.

## What it injects into AGENTS.md

`adapter install codex` injects a super-harness (Codex) subsection into
`AGENTS.md` telling the agent the conventions: a PreToolUse hook gates the
workspace; when a tool call is blocked, run `super-harness status` for the next
step and `super-harness change resume <change_id>` to restore context; never work
around the gate (overriding is a human-only decision, recorded and disclosed at
the merge gate); the review protocol (super-harness enforces that a verdict is
recorded, you produce it); and the turn-end authoring check.

## Common issues

- **Edits aren't blocked** — you haven't `/hooks`-trusted the hook yet, or you
  reinstalled/moved the binary and need to re-trust it. The gate is INACTIVE until
  trusted.
- **`.codex/hooks.json` shows up in `git status`** — run `super-harness sync
  --gitignore`.

## See also

- [Claude Code adapter](claude-code.md) — the reference agent adapter.
- [Getting started](../getting-started.md) — the full lifecycle walkthrough.
- [Limitations & FAQ](../limitations.md) — v0.1 boundaries.
=== END codex.md ===

- [ ] **Step 2: Commit.**

```bash
git add docs/adapters/codex.md
git commit -m "docs: add Codex adapter doc (experimental v0.1 agent adapter)"
```

---

## Task 2: Update `README.md` (Quickstart adapter step + language switcher + Links)

**Files:** Modify `README.md`

- [ ] **Step 1: Add the language switcher** immediately under the tagline. Replace:

```
# super-harness

> The missing CI layer for spec-driven AI coding workflows.
```

with:

```
# super-harness

> The missing CI layer for spec-driven AI coding workflows.

**English** | [简体中文](README.zh-CN.md)
```

- [ ] **Step 2: Add the agent-adapter install step in the Quickstart.** Replace this exact block:

```
pipx install super-harness
cd your-repo && super-harness init          # create the .harness/ data plane
super-harness change start "my-change"      # → INTENT_DECLARED
# now have your agent (or you) try to edit code → the gate blocks it,
# because no plan review has happened yet. That block is the product.
```

with:

```
pipx install super-harness
cd your-repo && super-harness init            # create the .harness/ data plane
super-harness adapter install claude-code     # wire your agent (auto-done by init if .claude/ exists)
#   or, for Codex:  super-harness adapter install codex   → then run /hooks in Codex to trust it
super-harness change start "my-change"        # → INTENT_DECLARED
# now have your agent (or you) try to edit code → the gate blocks it,
# because no plan review has happened yet. That block is the product.
```

- [ ] **Step 3: Add the adapter-doc pointers to Links.** Replace:

```
- [CLI reference](docs/cli-reference.md)
- [Architecture](docs/ARCHITECTURE.md)
```

with:

```
- [Agent adapters](docs/adapters/) — [Claude Code](docs/adapters/claude-code.md) · [Codex](docs/adapters/codex.md) (experimental)
- [CLI reference](docs/cli-reference.md)
- [Architecture](docs/ARCHITECTURE.md)
```

- [ ] **Step 4: Commit.**

```bash
git add README.md
git commit -m "docs: Quickstart wires agent adapters (claude-code + codex) + zh link"
```

---

## Task 3: Create `README.zh-CN.md`

**Files:** Create `README.zh-CN.md`

- [ ] **Step 1: Write the file** with EXACTLY the literal Markdown between the markers (a faithful translation of the slimmed English README; code blocks, identifiers, and proper nouns kept as-is).

=== BEGIN README.zh-CN.md ===
# super-harness

> 面向 spec 驱动 AI 编码工作流的、缺失的那层 CI。

[English](README.md) | **简体中文**

## super-harness 是什么?

一个开源、CI 优先、框架无关、agent 无关的 harness,让 AI 编码变得确定、可靠。
Spec 驱动的工具(如 Spec Kit、OpenSpec、Superpowers)用 markdown 描述规则,agent 读了之后(以概率)遵守;harness 则把这些
约束嵌进环境本身 —— hooks、CI、git、进程 —— 于是违规是被**确定性地拦下**,而不只是
被劝阻。它长在你现有的 spec 框架和 agent 之上,不替代其中任何一个。

关于它解决的问题、v0.1 交付了什么、以及跟邻近工具的关系,见
[Overview](docs/overview.md)。

## 安装

```bash
pipx install super-harness
brew install gh && gh auth login   # gh 是 init --setup-github 的前置依赖
```

## Quickstart

引导一个仓库,亲眼看门拦住一次"越出生命周期"的编辑 —— 这正是这个工具的意义所在:

```bash
pipx install super-harness
cd your-repo && super-harness init            # 创建 .harness/ 数据面
super-harness adapter install claude-code     # 接入你的 agent(若有 .claude/,init 会自动装)
#   Codex 用法:     super-harness adapter install codex   → 再在 Codex 里跑 /hooks 信任它
super-harness change start "my-change"        # → INTENT_DECLARED
# 现在让你的 agent(或你)去改代码 → 门会拦住,
# 因为还没经过 plan review。这一拦,就是产品本身。
```

这是"看见 super-harness 工作"的最短路径。完整流程 —— 装框架适配器、过 plan review、
实现、验证、评审、合并 —— 是 10 分钟的 [Getting started](docs/getting-started.md) 走查。
想不跑任何东西就看一个预置的非平凡 `.harness/` 状态,见仓内示例
[`examples/demo-openspec-claude/`](examples/demo-openspec-claude/)。

## 链接

- [文档索引](docs/README.md)
- [Overview](docs/overview.md) —— 它是什么、v0.1 交付了什么、邻近工具
- [Getting started](docs/getting-started.md) —— 完整端到端走查
- [Concepts](docs/concepts.md) —— 生命周期,以及 harness *不*替你做的事
- [Adopting](docs/adopting.md) —— 在你自己的项目里锁住架构规则
- [Limitations & FAQ](docs/limitations.md)
- [Agent 适配器](docs/adapters/) —— [Claude Code](docs/adapters/claude-code.md) · [Codex](docs/adapters/codex.md)(实验性)
- [CLI reference](docs/cli-reference.md)
- [Architecture](docs/ARCHITECTURE.md)

> 说明:深度文档(`docs/`)目前仅有英文;本页是面向中文读者的入口,链接指向同一份英文文档。

## License

MIT —— 见 [`LICENSE`](LICENSE)。
=== END README.zh-CN.md ===

- [ ] **Step 2: Commit.**

```bash
git add README.zh-CN.md
git commit -m "docs: add Chinese README (README.zh-CN.md) with language switcher"
```

---

## Task 4: Reconcile `docs/overview.md`

**Files:** Modify `docs/overview.md`

- [ ] **Step 1:** Replace:

```
- **Agent adapter** — Claude Code (PreToolUse + SessionStart hooks, injects an
  AGENTS.md subsection).
```

with:

```
- **Agent adapters** — Claude Code (PreToolUse + SessionStart + Stop hooks,
  injects an AGENTS.md subsection) and Codex (experimental; same hook surface,
  requires a one-time `/hooks` trust step). See [Adapter docs](adapters/).
```

- [ ] **Step 2: Commit.**

```bash
git add docs/overview.md
git commit -m "docs: overview lists Codex as an experimental agent adapter"
```

---

## Task 5: Reconcile `docs/limitations.md`

**Files:** Modify `docs/limitations.md`

- [ ] **Step 1:** Replace:

```
**Agent adapters (v0.2+):**
- Cursor / Codex / Aider agent adapters — platform hook capabilities vary;
  Claude Code is the reference adapter for v0.1.
```

with:

```
**Agent adapters (v0.2+):**
- Cursor / Aider agent adapters — platform hook capabilities vary. Claude Code is
  the reference v0.1 adapter; Codex ships as an experimental second adapter (see
  [Codex adapter](adapters/codex.md)).
```

- [ ] **Step 2: Commit.**

```bash
git add docs/limitations.md
git commit -m "docs: move Codex out of the not-yet-shipped agent adapters list"
```

---

## Task 6: Reconcile `docs/getting-started.md`

**Files:** Modify `docs/getting-started.md`

- [ ] **Step 1:** Replace:

```
- **Agent adapters:** `claude-code`.
```

with:

```
- **Agent adapters:** `claude-code` and `codex` (experimental).
```

- [ ] **Step 2: Commit.**

```bash
git add docs/getting-started.md
git commit -m "docs: getting-started lists codex among v0.1 agent adapters"
```

---

## Task 7: Reconcile `docs/adapters/claude-code.md`

**Files:** Modify `docs/adapters/claude-code.md`

- [ ] **Step 1:** Replace:

```
The Claude Code adapter is super-harness's reference *agent* adapter and the
only agent adapter shipped in v0.1. It wires Claude Code's runtime to the
```

with:

```
The Claude Code adapter is super-harness's reference *agent* adapter in v0.1
(Codex ships as an experimental second adapter — see [Codex adapter](codex.md)).
It wires Claude Code's runtime to the
```

- [ ] **Step 2: Commit.**

```bash
git add docs/adapters/claude-code.md
git commit -m "docs: claude-code adapter no longer claims to be the only v0.1 agent adapter"
```

---

## Task 8: Reconcile `docs/README.md`

**Files:** Modify `docs/README.md`

- [ ] **Step 1:** Replace:

```
- [Adapter docs](adapters/) — OpenSpec, Claude Code, Plain.
```

with:

```
- [Adapter docs](adapters/) — OpenSpec, Claude Code, Codex (experimental), Plain.
```

- [ ] **Step 2: Commit.**

```bash
git add docs/README.md
git commit -m "docs: docs index lists the Codex adapter"
```

---

## Task 9: Reconcile `docs/ARCHITECTURE.md`

**Files:** Modify `docs/ARCHITECTURE.md`

- [ ] **Step 1:** Replace:

```
- **Agent adapters** wire the harness into an agent's hook surface. `claude-code`
  writes the PreToolUse + SessionStart hooks and injects an `AGENTS.md` section that
  tells the agent the conventions (lifecycle, review protocol, scope discipline).
```

with:

```
- **Agent adapters** wire the harness into an agent's hook surface. `claude-code`
  and `codex` write the PreToolUse + SessionStart + Stop hooks and inject an `AGENTS.md`
  section that tells the agent the conventions (lifecycle, review protocol, scope
  discipline); Codex additionally needs a one-time `/hooks` trust step.
```

- [ ] **Step 2: Commit.**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: architecture note lists codex among agent adapters"
```

---

## Task 10: Reconcile hot-path "Claude Code only" framing

Three live-doc statements describe the hot-path PreToolUse gate as Claude-Code-only;
after Codex ships (its PreToolUse hook blocks `apply_patch`), they are misleading.

**Files:** Modify `docs/concepts.md`, `docs/overview.md`, `docs/ARCHITECTURE.md`

- [ ] **Step 1: `docs/concepts.md`.** Replace:

```
- **Hot path** — the PreToolUse gate, decided in-process from a single
  `state.yaml` snapshot, blocks Edit / Write tool calls in Claude Code when the
  current state forbids them. No resident process is on the decision path.
```

with:

```
- **Hot path** — the PreToolUse gate, decided in-process from a single
  `state.yaml` snapshot, blocks Edit / Write tool calls in Claude Code (and
  `apply_patch` in Codex, experimental — see [Adapter docs](adapters/)) when the
  current state forbids them. No resident process is on the decision path.
```

- [ ] **Step 2: `docs/overview.md`.** Replace:

```
- **Hot-path PreToolUse gate** — decided in-process from one state snapshot;
  blocks Edit / Write tool calls in Claude Code when the current lifecycle state
  forbids them.
```

with:

```
- **Hot-path PreToolUse gate** — decided in-process from one state snapshot;
  blocks Edit / Write tool calls in Claude Code (and `apply_patch` in Codex,
  experimental) when the current lifecycle state forbids them.
```

- [ ] **Step 3: `docs/ARCHITECTURE.md`.** Replace:

```
- **Hot-path (local, fast feedback — in-process):** the Claude Code adapter installs
  a **PreToolUse** hook (`super-harness-hook`) that makes the gate decision
```

with:

```
- **Hot-path (local, fast feedback — in-process):** the Claude Code adapter (and
  the experimental Codex adapter) installs a **PreToolUse** hook
  (`super-harness-hook`) that makes the gate decision
```

- [ ] **Step 4: Commit.**

```bash
git add docs/concepts.md docs/overview.md docs/ARCHITECTURE.md
git commit -m "docs: hot-path framing includes the experimental Codex adapter"
```

---

## Task 11: Verification

**Files:** none

- [ ] **Step 1: doc-refs / drift gate.** Run (venv on PATH): `super-harness doc check` — expect exit 0. If a proper noun (Codex / Claude Code / Cursor / Aider / WebSearch) was inline-backticked, unwrap it and re-run.
- [ ] **Step 2: decision gate.** Run: `super-harness decision check` — expect exit 0 (ignore benign `unknown event type` stderr; judge by exit code).
- [ ] **Step 3: no stale agent-adapter framing remains.** Run:

```bash
grep -rn "only agent adapter\|Cursor / Codex\|Codex / Aider" README.md docs/*.md docs/adapters/*.md | grep -v docs/plans/
grep -rn "in Claude Code when\|Claude Code adapter installs" README.md docs/*.md docs/adapters/*.md | grep -v docs/plans/
```

Expect: first grep no matches; second grep no matches (hot-path framing now names Codex too).

- [ ] **Step 4: all relative links resolve** (incl. codex.md and the two-way README switcher):

```bash
python3 - <<'PY'
import re, pathlib
files = ["README.md","README.zh-CN.md","docs/README.md","docs/overview.md","docs/limitations.md","docs/concepts.md",
         "docs/getting-started.md","docs/adapters/claude-code.md","docs/adapters/codex.md","docs/ARCHITECTURE.md"]
bad=0
for f in files:
    d=pathlib.Path(f).parent
    for m in re.finditer(r"\]\(([^)]+)\)", pathlib.Path(f).read_text()):
        t=m.group(1).split("#")[0].strip()
        if t.startswith("http") or t.startswith("mailto") or not t: continue
        if not (d/t).resolve().exists(): print("BROKEN",f,"->",t); bad+=1
print("broken:",bad)
PY
```

Expect: `broken: 0`.

- [ ] **Step 5: Quickstart command sequence still valid.** In a throwaway repo (venv on PATH): `init` → `adapter install claude-code` → `change start x` → confirm `status` shows INTENT_DECLARED and a gated edit is blocked (proven in PR#73; re-confirm the added `adapter install` command parses and exits 0).
- [ ] **Step 6: Full suite.** Run (venv on PATH): `PYTHONPATH=src pytest -q` — expect the baseline (~1628 passed); docs-only, no code delta.

---

## Self-review checklist

- [ ] **Coverage:** design §3.3 table rows → Tasks 4–9; Quickstart → T2; codex.md → T1; zh README → T3; hot-path framing → T10; verification → T11. ✅
- [ ] **F2 resolved consistently:** no live doc says Codex is v0.2+ or that Claude Code is the only v0.1 agent adapter (T10 Step 3). Codex always paired with the `/hooks` trust step + coverage caveat.
- [ ] **Cursor/Aider still v0.2+** (verified: not registered built-ins).
- [ ] **doc-refs clean:** proper nouns plain text; T10 Step 1 backstop.
- [ ] **Scope:** fence lists all 12 files (added concepts.md per plan review). No code / no AGENTS.md / no derived-doc.

---

## Landing (self-host lifecycle)

`change start 2026-07-06-quickstart-adapters-and-zh-readme` → `adapter scan-once superpowers` (fence → `plan_ready`) → plan-reviewer approve → `implementation start` → Tasks 1–11 → `done` → `review prepare` → two-actor code review → code-reviewer approve `--verdict-file` → `attest write` + commit → `attest verify --base main --head HEAD` → push + PR (`Change: 2026-07-06-quickstart-adapters-and-zh-readme`) → CI → merge → `on-merge`. After merge: mark F2 resolved in `private/OPEN-ITEMS.md`.
