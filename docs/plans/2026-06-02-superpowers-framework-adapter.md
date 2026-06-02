---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: superpowers-framework-adapter
stage: plan
affected_anchors:
  - capability-framework-adapter-builtin
  - capability-adapter-protocol
scope:
  files:
    - src/super_harness/adapters/framework/superpowers.py
    - src/super_harness/adapters/registry.py
    - src/super_harness/engineering/agents_md_render.py
tier_hint: Normal
---

# Superpowers Framework Adapter — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or subagent-driven-development) to implement this plan task-by-task.
> **Design:** see `docs/plans/2026-06-02-superpowers-framework-adapter-design.md` for rationale on every decision below.

**Goal:** Ship a version-agnostic `SuperpowersAdapter` that discovers superpowers design/plan artifacts by a `change:`/`stage:` frontmatter marker (NOT by superpowers' version-specific paths/filenames) and auto-emits `intent_declared` / `plan_ready`; then dogfood it on this repo.

**Architecture:** Mirror `OpenSpecAdapter`. Pure `_parse_frontmatter` + `scan_artifacts(workspace, seen)` core; `SuperpowersAdapter(FrameworkAdapter)` wrapper; registered in `adapters/registry.py`. `plan_ready` payload carries `affected_anchors`/`scope`/`tier_hint` from the plan artifact's frontmatter (HG-05 intent).

**Tech Stack:** Python 3.10+, `pyyaml` (existing dep), pytest. No new deps.

**Candidate dirs (version-agnostic):** `docs/plans/`, `docs/superpowers/plans/`, `docs/superpowers/specs/`. Marker = frontmatter `change:`. `.harness`-config override deferred to v0.2.

---

### Task 1: `_parse_frontmatter` helper (red→green)

**Files:** Create `src/super_harness/adapters/framework/superpowers.py`; Test `tests/unit/adapters/framework/test_superpowers.py`.

**Step 1 — failing tests:** leading `---\n…\n---` block → parsed mapping; no frontmatter → `{}`; malformed YAML → `{}` (no raise); non-mapping frontmatter (e.g. a list) → `{}`.

**Step 2 — run → FAIL** (module missing).

**Step 3 — implement** the `@capability:capability-framework-adapter-builtin` leading-comment sentinel + `_parse_frontmatter(text: str) -> dict`: if `text` starts with `---`, split out the block to the next `\n---`, `yaml.safe_load`; wrap in `try/except yaml.YAMLError`; coerce non-`dict` → `{}`.

**Step 4 — run → PASS. Step 5 — commit** `feat(v0.1): superpowers adapter — frontmatter parser`.

---

### Task 2: `detect()` — marker-based, multi-location (red→green)

**Step 1 — failing tests:** `docs/plans/x.md` with `change: foo` frontmatter → detect True; same marker under `docs/superpowers/specs/` → True; a `.md` with no `change:` → False; no candidate dirs → False.

**Step 2 — FAIL.**

**Step 3 — implement** `_CANDIDATE_DIRS = ("docs/plans", "docs/superpowers/plans", "docs/superpowers/specs")`; a `_iter_marked(workspace)` generator yielding `(path, frontmatter)` for every `.md` under existing candidate dirs whose frontmatter has a string `change:`. `detect()` = `any(_iter_marked(workspace))`. `SuperpowersAdapter(FrameworkAdapter)` with `name="superpowers"`, `version="0.1.0"`, `is_fallback=False`, `__init__(workspace=None)` (mirror OpenSpec).

**Step 4 — PASS. Step 5 — commit.**

---

### Task 3: `observe()` core — stage → events (red→green)

**Step 1 — failing tests** (set up `.harness/` + marked files):
- `stage: design` artifact → emits `intent_declared` only (change_id = its `change:` slug).
- `stage: plan` (or omitted) artifact, no prior design → emits `intent_declared` then `plan_ready`.
- design + plan sharing one `change:` slug → `intent_declared` (once) then `plan_ready`.
- events already in events.jsonl (`seen`) are not re-emitted.

**Step 2 — FAIL.**

**Step 3 — implement** `scan_artifacts(workspace, seen)`: collect `_iter_marked`, group by `change:` slug (sorted for determinism). Per slug: emit `intent_declared` (description from frontmatter `description:` or first `# ` heading or slug) unless `(slug,"intent_declared")` in `seen`; if any artifact's `stage` is plan/omitted, emit `plan_ready` unless seen. Guarantee intent precedes plan. `_seen_from_events` copied from OpenSpec. `observe()` = `yield from scan_artifacts(workspace, _seen_from_events(workspace))`.

**Step 4 — PASS. Step 5 — commit.**

---

### Task 4: `plan_ready` payload from plan frontmatter (HG-05) (red→green)

**Step 1 — failing tests:** plan artifact with `affected_anchors: [capability-event-stream]` (+ `scope`, `tier_hint`) → those keys appear in the `plan_ready` payload; absent → empty payload; malformed → empty (no raise).

**Step 2 — FAIL. Step 3 — implement:** in `scan_artifacts`, build the `plan_ready` payload from the plan artifact frontmatter keys actually present among `affected_anchors`/`scope`/`tier_hint`. **Step 4 — PASS. Step 5 — commit.**

---

### Task 5: remaining ABC methods (red→green)

`get_state(change_id)` (presence dict for the slug's artifacts; `RuntimeError` if registry-built), `spec_paths(workspace, change_id)` (best-effort resolved design/plan paths, pure), `watch_paths(workspace)` (existing candidate dirs), `verification_checks()` → `[]`, `agents_md_subsection()` (superpowers block documenting the `change:`/`stage:` frontmatter convention + **no branch mandate**). One failing test each → implement → PASS. **Commit.**

---

### Task 6: register + integration (red→green)

**Files:** Modify `adapters/registry.py` (add `register_builtin("superpowers", SuperpowersAdapter)`); Test `tests/integration/adapter/test_superpowers_registered.py`.

Test: `get_builtin("superpowers")` / `list_builtins()` include it; `super-harness adapter list` (CliRunner) shows it. Run full `ruff`/`mypy`/`pytest`. **Commit.**

---

### Task 7: decouple slug from branch (companion fix)

**Files:** Modify `src/super_harness/engineering/agents_md_render.py` (~line 60).

Soften "Branches MUST be named matching a registered super-harness change slug." → a suggestion ("Branches MAY follow your own naming; the slug is carried in the plan frontmatter / PR metadata, not the branch name."). Update/confirm the affected `agents_md` test fixtures. The new superpowers `agents_md_subsection()` (Task 5) likewise mandates no branch naming. Regenerate any committed `AGENTS.md`. **Commit.**

---

### Task 8: self-host dogfood + spec-defect note

1. In this repo: ensure the design+plan artifacts (`docs/plans/2026-06-02-superpowers-framework-adapter*.md`) carry the markers (they do). Run the observe entry (`super-harness adapter scan-once`, or the OpenSpec-equivalent) and assert it emits `intent_declared` + `plan_ready` for `superpowers-framework-adapter`, with `affected_anchors=[capability-framework-adapter-builtin, capability-adapter-protocol]` in the `plan_ready` payload.
2. `super-harness status superpowers-framework-adapter` → `AWAITING_PLAN_REVIEW`, anchors populated.
3. `super-harness verify superpowers-framework-adapter --layer baseline` → `anchor-sentinel-presence-final` PASS (both anchors have real sentinels).
4. Log in `private/OPEN-ITEMS.md`: specs (lifecycle-event-model §3.2, adapter-architecture §170, engineering-integration §385) assume `docs/superpowers/{plans,specs}/`; real superpowers moved to `docs/plans/`. The adapter is version-agnostic (marker-based) so it tolerates both; specs should be annotated.

Record the observed evidence in the PR. **Commit. Open PR.**

---

## Verification (whole-plan)
- `ruff check src tests` + `mypy src` clean; full `pytest` green (`$(pwd)/.venv/bin` on PATH).
- `adapter list` shows superpowers; dogfood change reaches AWAITING_PLAN_REVIEW with anchors; `anchor-sentinel-presence-final` passes.
- No reliance on superpowers version or its version-specific paths/filenames — discovery is marker-driven across candidate dirs.
