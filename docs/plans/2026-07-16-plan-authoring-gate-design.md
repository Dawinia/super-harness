---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: plan-authoring-gate-v2
stage: design
description: Give the PLAN_REJECTED state an authorized in-gate path to revise the plan document, so revising a rejected plan no longer requires bypassing the gate via the unhooked shell (pothole ⑩ / HG-PLAN-AUTHORING).
---

# Plan-authoring gate carve-out — Design

**Change slug:** `plan-authoring-gate-v2`
**Date:** 2026-07-16
**Track:** HG-PLAN-AUTHORING (registered in `private/OPEN-ITEMS.md`, forced out by
Codex in PR #83's plan review R2).

> **Revision note (v2):** round-1 REJECTed by both sources; round-2 split (one
> APPROVE, one REJECT — the second source found a real fail-open seam). This design
> incorporates all findings: post-resolution `.md` guard + gate-side defense-in-depth
> against symlink laundering; reducer always-reset + shape validation of
> `plan_artifacts`; **gate + snapshot validate the persisted list is a list** (a
> forged `state.yaml` `plan_artifacts: null` must not crash the gate into fail-open);
> case-insensitive `.md`; scope covers `core/state_snapshot.py`; honest re-ratify of
> `d-single-gate-policy`; honest bootstrap disclosure; and precise framing — the
> implemented recorder is **manual `plan ready`** (framework-agnostic; the marker is
> the natural boundary), with **framework-adapter AUTO-recording deferred** (openspec
> has no `change:` marker, needs its own sound design).

## Problem

The harness's core rule is *no code edits before the plan is approved*. The
pre-tool-use gate enforces it: in the three planning states —
`INTENT_DECLARED`, `AWAITING_PLAN_REVIEW`, `PLAN_REJECTED` — it blocks **every**
edit made through the `Write`/`Edit` tools.

But authoring the plan is itself an edit that must happen in those states. The gate
does not distinguish "editing the plan document" from "editing source" — it blocks
both. Today this deadlock is worked around by writing the plan through **Bash**
(`cat > plan.md`), which the gate does not hook. Codex flagged this in PR #83's
plan review as **self-bypassing the gate**, contradicting AGENTS.md's "if the gate
blocks you, stop and tell the human — do NOT bypass the gate yourself." Pothole ⑩
("gated-state Write → use Bash") had turned a hole in the gate into documented
procedure.

### The clean initial path already exists; the real gap is the reject loop

Drafting the plan **before `change start`** works today — the state is `None`, so
the gate allows the edit. This is the canonical first-authoring path, agent-agnostic.

The real gap is the **reject → revise loop**: after a plan is rejected the change
is in `PLAN_REJECTED`, and revising the (already-existing) plan document has no
authorized channel. That loop is what this change gives a legitimate path.

## Non-goals

- **Not** relaxing the gate for source edits in any state. The gate's purpose — no
  source edits before plan approval — is preserved exactly.
- **Not** a new agent-triggerable "unlock" verb (see Alternatives).
- **Not** first-authoring in `INTENT_DECLARED`. That is covered by
  draft-before-`change start`; `INTENT_DECLARED` stays fully blocked.
- **Not** framework-adapter AUTO-recording. This cut implements recording in the
  **manual `plan ready`** verb (framework-agnostic — it works for any change whose
  plan doc is a marked `.md` in declared scope; the marker is the natural boundary).
  Having an adapter emit `plan_artifacts` automatically on its own observation is
  deferred (openspec has no `change:` marker — see Deferred).

## Design

### Core idea: the change records its own plan document; the gate allows editing *that*

A change's **plan artifacts** are the files carrying a `change: <slug>` frontmatter
marker — the product's existing, path-agnostic identity substrate. We reuse that
anchor instead of matching paths by pattern.

1. **Record.** At manual `plan ready`, the emitter *derives* the plan artifact
   path(s) from the agent's declared `--scope` — validated (marked `.md`,
   frontmatter `change:` == slug), not taken as a free-form `--plan` — and records
   them into `ChangeState.plan_artifacts: list[str]` via the `plan_ready` payload.
2. **Allow.** In `PLAN_REJECTED`, the gate adds a bounded exception: it ALLOWs an
   edit whose `action.file` resolves to one of the change's recorded `plan_artifacts`
   (a marked `.md`). Everything else (source, governance docs, unmarked files, every
   other state) BLOCKs as before. The exception *narrows the blocked set* — i.e. it
   grants a precise, validated authorization — and never touches source.

### Why this is sound (the crux) — with the review-hardened guards

The only new ALLOW is "edit a file this change already registered as its plan
document." Registration is **ungameable for source**, defended at three layers:

- **Detection is marked-`.md`-only, checked *after* canonicalization.** A plan
  artifact is recorded only if BOTH the declared-scope entry ends in `.md` AND its
  **canonicalized** (symlink/`..`-resolved) path *also* ends in `.md` (suffix compared
  case-insensitively, so `PLAN.MD` on macOS is not silently dropped). This closes the
  **symlink-laundering** hole: `docs/plans/c.md → ../../src/evil.py` canonicalizes to
  `src/evil.py`, which fails the post-resolution `.md` check → not recorded.
- **Gate-side defense-in-depth.** The `PLAN_REJECTED` ALLOW additionally requires
  `state.plan_artifacts` to *be a list* (guards a forged/corrupt `state.yaml`) and
  `resolved_path` to end in `.md` (case-insensitive). Even if a non-`.md` somehow
  reached `plan_artifacts`, the gate would not honor it. A `.py` can never end in `.md`.
- **Persisted state is validated, not trusted.** `state.yaml` is normally the
  reducer's output (always a list), but a hand-forged `plan_artifacts: null`/`42`
  must not crash the gate. The gate's `isinstance(list)` guard turns any such value
  into a clean BLOCK (never a `TypeError` that the hook's outer `try` would fail-open),
  and `load_state_snapshot` coerces a non-list `plan_artifacts` to `[]`.
- **Detection is bounded to the declared `--scope`** — not a repo-wide scan and not
  an agent-supplied `--plan <path>`. There is no path the agent can name to unlock
  `src/evil.py`.
- **Every plan edit is re-reviewed.** Revising the plan in `PLAN_REJECTED` requires
  `plan ready` again → `AWAITING_PLAN_REVIEW` → the plan scope-adherence review
  re-runs. No plan change escapes review.

**Residual (documented, accepted):** a **hardlink** `docs/plans/c.md` ↔ `src/x.py`
shares an inode that `resolve()` cannot detect, so `docs/plans/c.md` would record as
itself and an edit *to that name* could touch the shared inode. This requires the
**shell** to create the hardlink (the same unhooked primitive pothole ⑩ already
concedes), most edit tools write-temp-then-rename (breaking the inode share), and the
`.md`-named content still goes through plan review + code review + attestation. It is
not a *new* capability beyond the conceded shell primitive. Noted in `limitations.md`.

### Why the carve-out is `PLAN_REJECTED`-only

`AWAITING_PLAN_REVIEW` stays frozen (editing the plan mid-review desyncs the
reviewer's target; re-submission isn't legal from there anyway). `INTENT_DECLARED`
(pre-first-submit) has no recorded artifact yet — draft-before-`change start` covers
it. From `PLAN_APPROVED` onward the state table **already ALLOWs all edits**
(`PLAN_APPROVED` / `IMPLEMENTATION_IN_PROGRESS` / `CODE_REVIEW_REJECTED`), so the
carve-out is moot there and adds nothing. `PLAN_REJECTED` is the *only* state that
both blocks by default and is where plan revision legitimately happens.

### Why path-based allow, not a `plan edit` verb

A `plan edit` verb would make the gate read a dynamic "authorized" state the agent
can set for itself — a new self-bypass surface (declare unlock → edit source), the
same failure class as the kill-switch escape-hatch hole. The recorded-artifact
approach adds no agent-triggerable state: the artifact list is set by the (reviewed)
plan submission and matched by a cheap in-process comparison.

### Why recorded paths, not an owner-configured glob

An owner-configured `plan_paths` glob is portable but **drifts**: a spec-tool
upgrade changes the layout, the config lags, and the reject loop silently gets
blocked again → back to Bash. Recording the path the change actually submitted is
drift-proof and needs no owner configuration.

## Components (manual `plan ready` recorder; adapter auto-recording deferred)

| File | Change |
|------|--------|
| `core/state.py` | `ChangeState.plan_artifacts: list[str]` (default empty). |
| `core/reducer.py` | `plan_ready` **always** sets `plan_artifacts` (shape-validated list-of-str, else `[]`); `plan_redeclared` clears it. |
| `core/state_snapshot.py` | Coerce a non-list persisted `plan_artifacts` to `[]` on load (defense against a forged `state.yaml`; keeps the "NEVER raises" contract). |
| `core/paths.py` | `canonical_relpath(root, file) -> str \| None` shared resolver. |
| `cli/plan.py` (`ready`) | Detect marked `.md` artifacts among `--scope` files (pre- AND post-canonicalization `.md`, case-insensitive, frontmatter `change:` == slug); write paths into the `plan_ready` payload. |
| `gates/__init__.py` | `ProposedAction.resolved_path: str \| None` (canonical repo-relative). |
| `gates/decisions.py` | Add `PLAN_ARTIFACT_ALLOW_STATES` to the single policy module; docstring notes the carve-out. |
| `gates/pre_tool_use.py` | After the `state is None` guard: in a `PLAN_ARTIFACT_ALLOW_STATES` state, ALLOW iff `state.plan_artifacts` is a list, `resolved_path` ends in `.md` (case-insensitive) AND is in it; else the table (BLOCK). |
| `daemon/hook_entry.py` (`_decide`) | Set `resolved_path=canonical_relpath(root, file)`. |
| `cli/gate.py` (`gate_check`) | Same `resolved_path` wiring. |
| `src/super_harness/adapters/agent/claude_code.py` | `_AGENTS_MD_SUBSECTION`: the authorized reject-loop revise path (source of the regenerated `AGENTS.md`). |
| `AGENTS.md` | Regenerated via `sync --agents-md` (NOT hand-edited). |
| `docs/decisions/d-single-gate-policy.md` | Prose updated ("policy = `PRE_TOOL_USE_DECISIONS` + `PLAN_ARTIFACT_ALLOW_STATES`, both in `gates.decisions`; no reader forks") + **re-ratified** (owner). |
| `private/specs/2026-05-26-lifecycle-event-model.md` | §3.7 gate matrix gains the `PLAN_REJECTED` plan-artifact exception; §3.2 `plan_ready` payload gains `plan_artifacts`. |
| `docs/concepts.md`, `docs/getting-started.md`, `docs/limitations.md` | Authorized reject-loop path; the `change:` marker + declared-scope recording requirement; honest limitations (Codex, hardlink, manual-`plan ready`-only / adapter auto-recording deferred). |

## Data flow

```
plan ready <slug> --scope @scope        (INTENT_DECLARED / PLAN_REJECTED; manual emit)
  └─ detect scope `.md` (pre+post canonicalize) with frontmatter change:<slug>
  └─ plan_ready payload { scope, tier_hint?, plan_artifacts: [canonical paths...] }
       └─ reducer (shape-validated, always reset) → ChangeState.plan_artifacts
                       └─ state.yaml snapshot
                              └─ PreToolUseGate.decide(action, state)
                                   PLAN_REJECTED and resolved_path.endswith(".md")
                                     and resolved_path ∈ plan_artifacts → ALLOW
                                   otherwise                            → BLOCK
```

## Edge cases & fail-safe

- **Absolute vs relative.** Claude Code often supplies an absolute `file_path`;
  `plan_artifacts` are repo-relative. Both sides canonicalize via the SAME
  `canonical_relpath` (POSIX, under `root.resolve()`).
- **Any uncertainty → BLOCK.** Un-normalizable path, path escaping root
  (`canonical_relpath` → None), empty artifact list, non-`.md` → the carve-out does
  not fire; the table's BLOCK stands. The exception only ever *narrows*.
- **Malformed payload / forged state.** The reducer accepts `plan_artifacts` only
  as a list of `str`; anything else → `[]` (a mapping like `{"src/x.py": true}`
  cannot smuggle a path in). Independently, the snapshot loader coerces a non-list
  persisted value to `[]`, and the gate guards `isinstance(list)` — so a hand-forged
  `state.yaml` (`plan_artifacts: null`) yields a clean BLOCK, never a `TypeError`
  that the hook's outer `try` would turn into fail-open.
- **Stale authorization.** Every `plan_ready` **replaces** `plan_artifacts` (no
  merge); `plan_redeclared` clears it. An empty re-submit revokes prior authorization.
- **Outer fail-open untouched.** "No harness / unknown call shape" still ALLOWs,
  exactly as before. We add only a *narrowing* branch inside an already-BLOCKing state.

## Codex asymmetry (accepted, documented)

The Codex shim supplies `file=None`, so a path-based allow structurally cannot fire
for Codex — it stays blocked in `PLAN_REJECTED`. Accepted: the direction is
**fail-safe** (Codex is *more* restricted, never less), and draft-before-`change
start` works for Codex too. Documented in `limitations.md`.

## Bootstrap disclosure (honest — this change cannot self-prove zero-Bash on itself)

This change's own plan phase runs **before** the fix is live: Tasks land only after
`plan ready` + plan approval, by which point this change's own reject loop is over.
Therefore **we do NOT claim "zero-Bash live proof" on this change's own reject
loop.** The honest handling used here:

- Plan convergence happens at state `None` (abandon → revise → re-`change start`),
  the design's own endorsed draft-before-start path — no shell bypass of the gate.
- The live proof is the **e2e test** (a synthetic `PLAN_REJECTED` change with a
  recorded artifact, exercising the real hook path) plus the install being
  **editable** — so the moment the code lands in the working tree, the gate honors
  the carve-out for *subsequent* changes. Any future change's reject loop is the
  in-anger demonstration.

## Testing (TDD)

- **Headline safety** (`PLAN_REJECTED`): editing **source** still BLOCKs; a recorded
  plan artifact ALLOWs; an unmarked/unrecorded `.md` BLOCKs; absolute-path and
  root-escaping targets BLOCK; a non-`.md` recorded path (constructed) is rejected by
  the gate `.md` guard.
- **Anti-forgery:** `src/evil.py` in scope (even with injected frontmatter) → not
  recorded; **symlink** `docs/plans/c.md → ../../src/evil.py` → not recorded
  (post-resolution `.md` guard); **hardlink** case documented (residual).
- **Reducer:** `plan_ready` populates + **replaces**; empty re-submit clears;
  malformed (mapping) payload → `[]`; `plan_redeclared` clears.
- **Snapshot/gate robustness:** a forged `state.yaml` with `plan_artifacts: null`
  loads as `[]` (snapshot) and the gate returns BLOCK (never raises).
- **Codex asymmetry:** `file=None` in `PLAN_REJECTED` → BLOCK.
- **canonical_relpath:** absolute-under-root, relative rooted, **true** root-escape →
  None, absolute-outside → None, None input → None, symlink resolution.
- **End-to-end:** synthetic reject loop — `Write` to the artifact allowed, `Write` to
  source blocked, zero Bash.

## Honest limitations (for `docs/limitations.md`)

- **Recording is via manual `plan ready` only** this cut; framework-adapter
  auto-recording is deferred. A plan doc must be a **marked `.md`** in the declared
  scope to be recorded (the marker is the boundary, so this is framework-agnostic but
  needs the marker — an openspec change with no marker records nothing until the
  deferred adapter cut).
- **Codex** plan revision in-gate unsupported (`file=None`); draft-before-`change
  start` is the fallback.
- **Hardlink** residual (requires shell; content still reviewed/attested).
- `INTENT_DECLARED` not relaxed; first-authoring is draft-before-`change start`.

## Deferred (record for a later cut)

- **Framework-adapter AUTO-recording (superpowers + openspec).** superpowers must
  record only the marked `.md` selected by the plan-review bundle (not every marked
  doc for the slug); openspec has **no `change:` marker** (identity is the change
  directory) so recording `proposal.md`/`tasks.md` needs a distinct, explicitly
  sound design that does not violate the marked-`.md`-only invariant. Registered in
  `private/OPEN-ITEMS.md`.

## Alternatives considered

- **Owner-configured `plan_paths` glob.** Portable but drifts; silently reopens the
  Bash hole. Rejected.
- **Hardcoded `docs/plans/**` prefix.** Not portable across spec tools/versions.
  Rejected — identity anchors on the marker, not paths.
- **`plan edit` authorization verb.** Agent-triggerable unlock surface. Rejected on
  soundness.
- **Gate reads target-file frontmatter at decision time.** Puts file I/O + YAML
  parsing on the fail-open hot path. Rejected in favor of recording once at submit.
