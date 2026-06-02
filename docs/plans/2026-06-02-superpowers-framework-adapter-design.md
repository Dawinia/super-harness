---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: superpowers-framework-adapter
stage: design
description: Ship a version-agnostic SuperpowersAdapter so super-harness can observe superpowers design/plan artifacts via a frontmatter marker and auto-emit lifecycle events.
---

# Superpowers Framework Adapter — Design

**Status:** validated via brainstorming 2026-06-02 (revised 2026-06-03).
**Slug / change:** `superpowers-framework-adapter`

## Problem

super-harness ships only two framework adapters (`openspec`, `plain`); `superpowers` is a `--framework` enum placeholder with no implementation. The maintainer actually develops with superpowers, so self-host currently runs as `plain` (manual CLI) and never exercises the framework-adapter path (`observe()` → auto-emit events). We want a real `SuperpowersAdapter` to (a) dogfood that path end-to-end and (b) realize the HG-05 intent (auto-populate `affected_anchors`) for the framework we actually use.

## Why this is harder than OpenSpec — and the resolution

OpenSpec keeps a change's artifacts under `changes/<slug>/` (clean slug + per-change dir). Superpowers writes **flat, date-named files** (`docs/plans/YYYY-MM-DD-<name>.md` for plans, `…-design.md` for designs) — and the location has **moved between superpowers versions** (older versions used `docs/superpowers/`). The installed version lives in `~/.claude/plugins/…/superpowers/<ver>/`, NOT in the repo, so the adapter/daemon/CI **cannot reliably detect the superpowers version** from the workspace. Per-version branching is therefore rejected as fragile.

**Resolution — anchor on a super-harness-owned frontmatter marker, not on superpowers' version-specific path/filename conventions:**

### Decision 1 — slug from frontmatter `change:`
A tracked artifact carries YAML frontmatter `change: <slug>` (kebab, `core.slug` rules). The marker — not the filename or branch — is the identity. design and plan for one change share the same `change:` value, so they link reliably even when superpowers' topic/feature filenames differ.

### Decision 2 — slug is NOT bound to the git branch
Verified: branch==slug is only (a) an AGENTS.md suggestion (`agents_md_render.py:60`, harness-generated → editable) and (b) an `on-merge` *fallback* when `--change` is absent. The gate never reads the branch. Users keep their own branch naming. As a companion fix, soften the AGENTS.md "Branches MUST…" line to a suggestion, and the new superpowers `agents_md_subsection()` mandates no branch naming.

### Decision 3 — role (intent vs plan) from frontmatter `stage:`, not filename
Filename conventions (`-design.md`) are also version-fragile, so the role lives in frontmatter too:
- `stage: design` → emit `intent_declared` only (the change is born in INTENT_DECLARED; a design is enough — a plan is not required to start a change).
- `stage: plan` or omitted (default) → emit `intent_declared` (if not already present for this change) **then** `plan_ready`.

This honors the lifecycle: a change exists the moment intent is declared; the plan advances it to AWAITING_PLAN_REVIEW.

### Decision 4 — version-agnostic, multi-location discovery
`observe()` scans a fixed candidate set covering known superpowers eras — `docs/plans/`, `docs/superpowers/plans/`, `docs/superpowers/specs/` — for `.md` files, keeping only those whose frontmatter has `change:`. The candidate set absorbs cross-version location drift; the marker filter prevents false positives from ordinary docs. (A `.harness`-config path override is **deferred to v0.2** — YAGNI for now.)

### Decision 5 — `affected_anchors` from the plan artifact's frontmatter (HG-05 intent)
A `stage: plan` artifact may carry `affected_anchors` (list), `scope` (mapping), `tier_hint` (str) in its frontmatter; these populate the `plan_ready` payload (the reducer already consumes all three). Absent/malformed frontmatter → empty payload, never raises. This is HG-05 ("auto-fill anchors") realized for superpowers.

## Architecture

Mirror `OpenSpecAdapter` (`adapters/framework/openspec.py`):
- A pure `scan_artifacts(workspace, seen)` core: walk the candidate dirs, parse frontmatter, group by `change:`, and yield unseen `intent_declared` / `plan_ready` events in dependency order (intent before plan).
- `_parse_frontmatter(text) -> dict` helper (leading `--- … ---`, `yaml.safe_load`; non-mapping / `YAMLError` → `{}`).
- `_seen_from_events(workspace)` — copy OpenSpec's events.jsonl dedup.
- `SuperpowersAdapter(FrameworkAdapter)` wrapping it with `detect / observe / get_state / spec_paths / watch_paths / verification_checks / agents_md_subsection`; registered in `adapters/registry.py`.
- Actor = `Actor(type="adapter", identifier="superpowers-adapter")`, framework = `"superpowers"`, `is_fallback = False`.

**ABC method specifics:**
- `detect(workspace)` → True iff any candidate dir holds a `.md` with a `change:` frontmatter marker. (Reliable super-harness signal; false negative falls back to `plain`.)
- `get_state(change_id)` → presence/derived dict for the change's artifacts; raises `RuntimeError` if registry-built (no workspace), mirroring OpenSpec.
- `spec_paths(workspace, change_id)` → best-effort resolved design/plan paths (pure, daemon-safe).
- `watch_paths(workspace)` → the candidate dirs that exist.
- `verification_checks()` → `[]` (superpowers ships no native validate command).
- `agents_md_subsection()` → superpowers guidance: the `change:`/`stage:` frontmatter convention, where plans live, drive via writing-plans/TDD skills, and **no branch-naming mandate**.

## Dedup / edge cases
- Re-emit is legal under the lifecycle; `seen` (from events.jsonl) suppresses noise. Candidate dirs walked in sorted order for determinism.
- Same `change:` in multiple files: design (`stage: design`) → intent; plan (`stage: plan`/default) → plan_ready. Two plan artifacts for one slug is unexpected; deterministic sorted order + `seen` keep it safe.
- A plan-stage artifact with no prior design → synthesize `intent_declared` first (emit-time validation requires it).

## Out of scope / deferred
- `.harness`-config location override (v0.2).
- superpowers-version detection (rejected — not reliably available in the workspace).
- Real L1 doc regeneration (still v0.2 per existing `generate_l1_stubs` stub policy).

## Testing
Unit tests for: detect (marker present/absent, multiple locations), frontmatter parsing (valid / absent / malformed), observe mapping (design→intent, plan→intent+plan_ready, plan-without-design synthesizes intent, seen-dedup), anchors/scope/tier into plan_ready payload, the ABC methods, registry registration. Integration: `adapter list` shows superpowers; dogfood: this repo's `superpowers-framework-adapter` design+plan artifacts get observed into the right events with anchors populated.
