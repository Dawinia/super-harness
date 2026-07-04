---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: 2026-07-04-architecture-norm-discovery-skill
stage: plan
scope:
  files:
    - skills/discovering-architecture-norms/SKILL.md
    - examples/arch-norm-fixture/README.md
    - examples/arch-norm-fixture/package.json
    - examples/arch-norm-fixture/electron/main.ts
    - examples/arch-norm-fixture/src/renderer.tsx
    - examples/arch-norm-fixture/src/lib/exporter.ts
    - examples/arch-norm-fixture/src/lib/leaky.ts
    - examples/arch-norm-fixture/src/components/Button.tsx
    - examples/arch-norm-fixture/src/utils/format.ts
    - examples/arch-norm-fixture/src/i18n/strings.ts
    - examples/arch-norm-fixture/EXPECTED.md
    - README.md
    - docs/getting-started.md
    - src/super_harness/engineering/agents_md_render.py
    - AGENTS.md
    - tests/unit/engineering/test_agents_md_render.py
    - docs/plans/2026-07-04-architecture-norm-discovery-skill-design.md
    - docs/plans/2026-07-04-architecture-norm-discovery-skill-plan.md
tier_hint: Normal
---

# Architecture-norm Discovery Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.
> **Design:** `docs/plans/2026-07-04-architecture-norm-discovery-skill-design.md` — rationale for every decision below.

**Goal:** Ship a portable agent skill that lets an adopter's own Code Agent discover candidate architecture norms in their codebase (as hypotheses for a human to ratify), plus a reproducible fixture and pointers.

**Architecture:** Docs/skill change. New `skills/discovering-architecture-norms/SKILL.md` (authored via the writing-skills skill's skill-TDD); a gate-inert vendored fixture under `examples/arch-norm-fixture/` with a golden `EXPECTED.md`; GitHub-URL pointers in README / getting-started / the injected AGENTS.md template. The only Python touched is a string edit to the AGENTS.md section template (+ its test).

**Tech Stack:** Markdown skill + a small TS/Electron sample fixture. Verified via the fixture/EXPECTED oracle, `super-harness doc check` / `decision check` / `verify` / `sync --check`, and the existing suite.

---

## File structure

| File | Responsibility |
|---|---|
| `skills/discovering-architecture-norms/SKILL.md` (new) | The portable skill: 4-source sweep + strength ranking + hypotheses-not-rules + hand-off. |
| `examples/arch-norm-fixture/**` (new) | Reproducible test oracle: a tiny TS/Electron sample with a clean framework boundary, a planted layering leak, a pure sink, and a coincidental zero. |
| `examples/arch-norm-fixture/EXPECTED.md` (new) | Golden candidate list the skill must produce on the fixture. |
| `README.md`, `docs/getting-started.md` (modify) | GitHub-URL pointers to the skill. |
| `src/super_harness/engineering/agents_md_render.py` (modify) | One pointer line in the injected AGENTS.md "Decision conformance" section. |
| `AGENTS.md` (regenerated) | This repo's own AGENTS.md, re-rendered from the edited template. |
| `tests/unit/engineering/test_agents_md_render.py` (modify) | Assert the new pointer line renders. |

## Authoring invariants (apply throughout)

- **doc-refs rule (SKILL.md and the README/getting-started edits ARE doc-scanned).** The gate flags inline single-backtick spans that are a single identifier with TitleCase/camelCase/snake_case shape absent from source. So keep `Electron`, `Biome`, `child_process`, `mayDependOn`, `ipcRenderer`, etc. **inside fenced blocks or as plain (un-backticked) text**. Hyphen/dot/lowercase names (`lint-imports`, `dependency-cruiser`, `.importlinter`, `depcruise`, `grep`) are safe inline. `super-harness doc check` is the backstop. (Fixture files under `examples/**` are doc-excluded — no constraint there.)
- **Pointer URLs are absolute GitHub URLs**, base `https://github.com/Dawinia/super-harness/blob/main/…`, with an honest "(private during v0.1; requires repo access until the public flip)" note — never repo-relative paths (the wheel ships only `src/`).
- **Hypotheses framing is load-bearing**: the skill presents candidates for the human to judge and hands off to `docs/architecture-fitness.md` + `decision` verbs; it never tells the human to lock.
- **Judging gate results by exit code, not grep.** The lifecycle commands emit benign `unknown event type: l1_update_completed` warnings (pre-existing events replayed) — ignore them visually. Do **NOT** pipe a gate command through `grep` to hide them: `cmd | grep` returns grep's exit status and can mask a real gate failure. Read the command's own exit code / final verdict line.
- **Edits are gate-blocked until `IMPLEMENTATION_IN_PROGRESS`.** Run Task 5 Steps 1–2 (`change start` → plan approve → `implementation start`) BEFORE creating/committing any file in Tasks 1–4; the in-process gate blocks Edit/Write in `AWAITING_PLAN_REVIEW`. (Lifecycle commands themselves run via Bash, which is ungated.)

---

## Task 1: Vendored fixture + golden EXPECTED.md (the test oracle)

Build this FIRST (as authoring order) — it is the acceptance oracle Task 2 verifies
against. **Prerequisite: the change must already be in `IMPLEMENTATION_IN_PROGRESS`
(Task 5 Steps 1–2) before any file edit here, or the gate blocks it.**

**Files:** create everything under `examples/arch-norm-fixture/`.

- [ ] **Step 1: Fixture manifest + framework signal.** Create `examples/arch-norm-fixture/package.json`:

```json
{
  "name": "arch-norm-fixture",
  "private": true,
  "description": "Fixture for the discovering-architecture-norms skill. Not a real app.",
  "dependencies": { "electron": "*", "react": "*" }
}
```

- [ ] **Step 2: Electron main + renderer (framework-boundary norm, CLEAN).** The renderer must NOT import node/electron builtins — it uses a preload bridge. Create `examples/arch-norm-fixture/electron/main.ts`:

```ts
import { app } from "electron";
import { readFileSync } from "fs";
export function start() { app.whenReady().then(() => readFileSync("/tmp/x")); }
```

Create `examples/arch-norm-fixture/src/renderer.tsx`:

```tsx
// Renderer: talks to main ONLY through the preload bridge, never imports electron/fs/path.
declare const bridge: { save(data: string): Promise<void> };
import { formatSize } from "./utils/format";
export function App() { return bridge.save(formatSize(1024)); }
```

- [ ] **Step 3: Layering — pure sink + clean logic + PLANTED LEAK.** Create `examples/arch-norm-fixture/src/utils/format.ts` (pure sink — imports nothing internal):

```ts
export function formatSize(n: number): string { return `${n} B`; }
```

Create `examples/arch-norm-fixture/src/lib/exporter.ts` (logic — depends only on utils, the intended direction):

```ts
import { formatSize } from "../utils/format";
export function summary(bytes: number): string { return `export ${formatSize(bytes)}`; }
```

Create `examples/arch-norm-fixture/src/lib/leaky.ts` (logic — PLANTED violation: imports UI):

```ts
// VIOLATION (planted): lib must not import components — logic reaches up into UI.
import { Button } from "../components/Button";
export const widget = Button;
```

Create `examples/arch-norm-fixture/src/components/Button.tsx` (UI — allowed to depend downward on lib/utils):

```tsx
import { summary } from "../lib/exporter";
import { formatSize } from "../utils/format";
export function Button() { return `${summary(1)} / ${formatSize(1)}`; }
```

- [ ] **Step 4: Coincidental symmetric zero.** Create `examples/arch-norm-fixture/src/i18n/strings.ts` — imports nothing, and nothing in `lib` imports it (so `i18n ↔ lib` is a symmetric zero the skill must SKIP, not propose):

```ts
export const strings = { title: "Fixture" };
```

- [ ] **Step 5: Golden candidate list.** Create `examples/arch-norm-fixture/EXPECTED.md`:

```markdown
# Golden output — discovering-architecture-norms on this fixture

A correct run produces a ranked candidate list equivalent to:

1. **[framework-boundary · CLEAN · top-tier]** Renderer (`src/`) must not import
   Electron/Node builtins (electron/fs/path); it goes through the preload bridge.
   Evidence: `electron/main.ts` uses them; `src/renderer.tsx` uses `bridge`, zero
   builtin imports. Why/breaks: process isolation / security — a direct import
   breaks the sandbox. Status: currently clean → lockable now.
2. **[layering · VIOLATED]** `src/lib` must not import `src/components` (logic ⊥ UI).
   Evidence: `src/lib/leaky.ts` imports `../components/Button` (1 leak inside a
   components→lib asymmetry). Why/breaks: logic can't run/test headless. Status:
   violated → fix-first or baseline before locking.
3. **[layering/sink · CLEAN]** `src/utils` imports nothing internal (pure sink).
   Evidence: `format.ts` has no internal imports; imported by lib/components/renderer.

Must NOT appear as a proposed rule:
- `i18n ⊥ lib` or `lib ⊥ i18n` — a coincidental symmetric zero (no evidence of an
  intended direction); proposing it would ossify an accident.

Every item above is a HYPOTHESIS for a human to judge, not an auto-locked rule.
```

- [ ] **Step 6: Fixture README.** Create `examples/arch-norm-fixture/README.md`:

```markdown
# arch-norm-fixture

A trimmed, non-runnable TS/Electron-style sample used to verify the
`discovering-architecture-norms` skill. It deliberately encodes: a clean
framework-boundary norm, a layering rule with one planted leak, a pure sink, and a
coincidental symmetric zero. The golden expected candidate list is in `EXPECTED.md`.
This directory is not built or tested; it is fixture data.
```

- [ ] **Step 7: Confirm the fixture is gate-inert.**

Run: `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check && super-harness decision check`
Expected: both clean. `examples/**` is doc-excluded, and the fixture has no `@decision:` anchors, so neither gate touches it.

- [ ] **Step 8: Commit.** `git add examples/arch-norm-fixture && git commit -m "test: vendored fixture + golden EXPECTED for arch-norm discovery skill"`

---

## Task 2: Author `skills/discovering-architecture-norms/SKILL.md` via writing-skills

Author with the **writing-skills** skill (skill-TDD: baseline → write → verify → refactor). The fixture from Task 1 is the GREEN oracle.

**Files:** create `skills/discovering-architecture-norms/SKILL.md`.

- [ ] **Step 1: Baseline (RED).** Using writing-skills, run a pressure scenario: give a fresh subagent the fixture repo (`examples/arch-norm-fixture/`) and the prompt "discover architecture norms I could lock with super-harness" WITHOUT the skill. Record the failure modes (e.g. it only looks at the import graph and misses the Electron boundary; it proposes the coincidental `i18n ⊥ lib` zero; it ranks by raw zero count; it recommends locking instead of presenting hypotheses). These are the loopholes the skill must close.

- [ ] **Step 2: Write the skill (GREEN).** Create `skills/discovering-architecture-norms/SKILL.md` with YAML frontmatter and a body covering exactly the design §5–§8 method. Required content:
  - Frontmatter: `name: discovering-architecture-norms`; `description:` a "Use when…" trigger, e.g. "Use when a team adopting super-harness wants to discover which architecture norms to ratify in an existing codebase — sweeps the repo and proposes candidate norms as hypotheses for a human to judge."
  - **Four-source sweep** (framework/stack → config → import-graph → naming), framed as a default order, not a law. State that discovery-time graphing may be approximate (grep/heuristic OK; the precise tool comes at the lock step).
  - **Multi-factor strength ranking**: protected-capability × evidence-strength (asymmetry / sink-shape / already-in-config vs coincidental zero) × blast-radius × clean-vs-violated. Category order (framework-boundary + cross-cutting > layering > naming) is a default prior; a load-bearing layering rule can top it.
  - **Asymmetry rubric**: strong one-way → lock reverse; pure sink → imports-nothing; symmetric zero → SKIP (don't ossify); symmetric large → cycle smell (flag).
  - **"why + what breaks" test** required per candidate.
  - **Hypotheses framing** (load-bearing): present candidates, mark clean/violated, NEVER auto-lock or tell the human to lock; hand off to `decision new`/`ratify`/`check` and `docs/architecture-fitness.md` (GitHub URL) for the locking mechanics.
  - **Greenfield detect+route branch** (short): if the repo has little/no code, don't mine; instead OFFER a few high-confidence intent norms (from product docs/stack) as candidate decisions the human may text-lock, surface any already-fitting check, defer layering until a skeleton exists. Flag it as intent-prescriptive (still human-ratified).
  - Obey the doc-refs authoring rule (Authoring invariants) — API/proper-noun identifiers only in fenced blocks.

- [ ] **Step 3: Verify (GREEN) against the oracle.** Give a fresh subagent the skill + `examples/arch-norm-fixture/` and confirm its output matches `EXPECTED.md`: (a) framework-boundary norm surfaced top-tier, (b) `lib ⊥ components` flagged VIOLATED, (c) ranked by strength/asymmetry not raw zeros, (d) `i18n ⊥ lib` NOT proposed, (e) everything presented as hypotheses with hand-off to `decision`/the guide. If any miss, refactor the skill (close the loophole) and re-run.

- [ ] **Step 4: doc-refs check.** Run: `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check`. Expected: no dead-reference finding for `skills/discovering-architecture-norms/SKILL.md`. If an identifier is flagged, move it into a fence / make it plain text.

- [ ] **Step 5: Commit.** `git add skills/discovering-architecture-norms/SKILL.md && git commit -m "feat(skill): discovering-architecture-norms — mine candidate norms as hypotheses"`

---

## Task 3: README + getting-started pointers

**Files:** modify `README.md`, `docs/getting-started.md`.

- [ ] **Step 1: README `## Links` entry.** In `README.md`'s `## Links` list, immediately after the "Arm an architecture rule" line, insert:

```
- [Discover architecture norms (skill)](https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md)
```

- [ ] **Step 2: getting-started pointer.** In `docs/getting-started.md`, at the end of the `## 11. Next steps` list, add a bullet:

```
- **Discover which rules to arm**: point your Code Agent at the
  [discovering-architecture-norms skill](https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md)
  to sweep your codebase and propose candidate architecture norms (hypotheses you
  then judge and ratify). The super-harness repo is private during v0.1, so the
  link requires repo access until the public release.
```

- [ ] **Step 3: Verify.** `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check` — no findings for README.md / getting-started.md. (The URLs contain no backticked identifiers, so they are safe.)

- [ ] **Step 4: Commit.** `git add README.md docs/getting-started.md && git commit -m "docs: link the discovering-architecture-norms skill from README + getting-started"`

---

## Task 4: AGENTS.md-template pointer (reaches every adopter's in-repo agent)

**Files:** modify `src/super_harness/engineering/agents_md_render.py`, `tests/unit/engineering/test_agents_md_render.py`; regenerate `AGENTS.md`.

- [ ] **Step 1: Write the failing test.** In `tests/unit/engineering/test_agents_md_render.py`, add:

```python
def test_section_points_to_norm_discovery_skill(tmp_path):
    from super_harness.engineering.agents_md_render import render_super_harness_section
    agents = tmp_path / "AGENTS.md"
    render_super_harness_section(tmp_path, agents, "0.1.0")
    text = agents.read_text(encoding="utf-8")
    assert "discovering-architecture-norms" in text
```

- [ ] **Step 2: Run it — verify it fails.** Run: `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/engineering/test_agents_md_render.py::test_section_points_to_norm_discovery_skill -q`. Expected: FAIL (the string isn't in the template yet).

- [ ] **Step 3: Add the pointer to the template.** In `src/super_harness/engineering/agents_md_render.py`, inside `_AGENTS_MD_SECTION_TEMPLATE`, at the very end of the `### Decision conformance` content — **immediately before the `<!-- super-harness section end -->` marker, after the last "Keep the armed authoring set small" bullet** (there is no further `###` subsection) — add:

```
- **Not sure which decisions to make?** To discover candidate architecture norms
  in an existing codebase, point your agent at the discovering-architecture-norms
  skill: https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md
```

(A URL, agent-agnostic; no backticked identifiers.)

- [ ] **Step 4: Run the new test + the whole render test file.** Run: `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" python -m pytest tests/unit/engineering/test_agents_md_render.py -q`. Expected: PASS (new test green; existing marker/subsection assertions unaffected).

- [ ] **Step 5: Regenerate this repo's own AGENTS.md.** The committed `AGENTS.md` must match the new template or `sync --check` fails. Run: `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" super-harness sync --agents-md`. Then verify no drift: `super-harness sync --check` (exit 0).

- [ ] **Step 6: Commit.** `git add src/super_harness/engineering/agents_md_render.py tests/unit/engineering/test_agents_md_render.py AGENTS.md && git commit -m "feat(agents-md): point adopters' agents at the norm-discovery skill"`

---

## Task 5: Lifecycle + full verification (self-host)

**Files:** none (lifecycle events + gates). The branch `2026-07-04-architecture-norm-discovery-skill` already exists with the design + plan committed.

- [ ] **Step 1: Start the change + emit plan_ready.** (The plan frontmatter uses a `---` YAML fence so the Superpowers adapter detects it.) Run: `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" super-harness change start 2026-07-04-architecture-norm-discovery-skill` then `super-harness adapter scan-once superpowers` then `super-harness status`. Expected: `AWAITING_PLAN_REVIEW` with scope = the frontmatter files.
- [ ] **Step 2: Plan review (two actors, out-of-band: Claude subagent + `codex exec --sandbox read-only`), then:** `super-harness review approve 2026-07-04-architecture-norm-discovery-skill --reviewer plan-reviewer` (bare OK for plan-reviewer) && `super-harness implementation start 2026-07-04-architecture-norm-discovery-skill`. (Do Tasks 1–4 in `IMPLEMENTATION_IN_PROGRESS`, where edits are permitted.)
- [ ] **Step 3: Full local gate suite** (after Tasks 1–4 committed). `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check && super-harness decision check && super-harness sync --check && python -m pytest -q && python -m mypy src`. Expected: all green (only the AGENTS.md template + its test changed under `src/`/`tests/`; no logic delta).
- [ ] **Step 4: `done` → code review (verdict-file) → attest.**
  - `super-harness done 2026-07-04-architecture-norm-discovery-skill` → `AWAITING_CODE_REVIEW`.
  - `super-harness review prepare 2026-07-04-architecture-norm-discovery-skill --reviewer code-reviewer` (writes the bundle; requires a clean in-scope tree).
  - Two-actor code review vs that bundle; produce a verdict JSON (`bundle_digest` copied from the bundle; every checklist item pass/na; findings) per `docs/cli-reference.md`.
  - `super-harness review approve 2026-07-04-architecture-norm-discovery-skill --reviewer code-reviewer --verdict-file <verdict.json>` → `READY_TO_MERGE`. (Bare code-reviewer approve is rejected — `--verdict-file` is mandatory.)
  - `super-harness attest write 2026-07-04-architecture-norm-discovery-skill` then commit `.harness/attestations/`.
- [ ] **Step 5: Confirm merge gate.** `cd <repo> && PATH="$(pwd)/.venv/bin:$PATH" super-harness attest verify --base main --head HEAD`. Expected PASS — every scope file covered. If a "not in scope" blocker appears, add the path to this plan's frontmatter `scope.files`, re-attest, re-run.
- [ ] **Step 6: Open the PR** with the metadata block naming the slug (getting-started §7).

---

## Self-review (author checklist — completed)

- **Spec coverage:** SKILL.md (Task 2) covers design §3/§5/§6/§7/§8; fixture+EXPECTED (Task 1) covers design §10 reproducible acceptance; pointers (Tasks 3–4) cover design §4/§9 as GitHub URLs; lifecycle+scope+gates (Task 5) covers design §9/§10. ✓
- **Placeholder scan:** fixture files + EXPECTED + test are literal; the SKILL.md body is authored via writing-skills TDD against the concrete fixture oracle (correct granularity — its exact prose emerges RED-GREEN-REFACTOR, its required content + acceptance are pinned). No TBD/TODO. ✓
- **doc-refs safety:** SKILL.md + README/getting-started edits keep identifiers out of inline backticks; pointer URLs carry none; `doc check` is the backstop (Task 2 Step 4, Task 3 Step 3). ✓
- **Consistency:** slug, scope files, and GitHub-URL base are identical across frontmatter and all tasks. AGENTS.md regen (Task 4 Step 5) keeps `sync --check` green. ✓
- **Scope covers all changed files:** frontmatter lists SKILL.md + 8 fixture files + EXPECTED + README + getting-started + agents_md_render.py + AGENTS.md + the render test + design + plan; `attest verify` (Task 5 Step 5) is the backstop. ✓
