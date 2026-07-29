---
change: 2026-07-29-gate-authoring-space-v2
stage: design
---

# Authoring space in gated states — design

## Why: the gate contradicts the product's own onboarding

`docs/getting-started.md:315` promises:

> In `INTENT_DECLARED`, the agent can author `proposal.md` / `tasks.md` (the
> OpenSpec adapter watches for these and emits `plan_ready` automatically →
> `AWAITING_PLAN_REVIEW`).

The gate does the opposite. Measured against a repo with the real Claude Code
hook installed:

```
Write openspec/changes/<slug>/proposal.md → exit=2   (BLOCK)
Write openspec/changes/<slug>/tasks.md    → exit=2   (BLOCK)
Write docs/plans/<slug>.md                → exit=2   (BLOCK)
Write .harness/scratch/notes.md           → exit=2   (BLOCK)
Write /tmp/scratch-note.md                → exit=2   (BLOCK — out-of-repo)
```

`PRE_TOOL_USE_DECISIONS["INTENT_DECLARED"]` is a flat `block`; nothing narrows it.

This is not a documentation typo. **Both framework adapters' automatic
`plan_ready` path is built on the agent authoring plan documents in
`INTENT_DECLARED`** — OpenSpec watches `openspec/changes/<slug>/{proposal,tasks}.md`
(path convention, no marker), Superpowers watches marked `.md` under its candidate
dirs. In any repo with the hot-path gate installed, that line is dead.

### How far the fix reaches, per adapter

Being honest about coverage, since the motivation leans on both:

| adapter | how it identifies plan artifacts | covered by a `{slug}` pattern? |
|---|---|---|
| OpenSpec | path convention `openspec/changes/<slug>/…` | **yes, via a shipped-enabled default pattern** — the slug is in the path by construction |
| Superpowers | `change:` frontmatter marker; filename free-form | **only where the filename carries the slug** — the shipped defaults cover its candidate dirs, but a slug-less filename is not covered |
| plain / none | owner's own convention | by configuration |

The shipped skeleton enables all of these patterns rather than commenting them
out. `init --framework` is a no-op placeholder and `_skeleton_files()` is
framework-blind, so a commented default would leave a fresh OpenSpec repo blocked
while the docs claimed otherwise. An unused pattern whose directory does not exist
never matches, so shipping them enabled costs nothing.

Superpowers' residue is structural, not an oversight: allowing on the *marker*
would let the agent mark any `.md` (including `AGENTS.md`) as this change's plan,
which is precisely the identity source §Design/1 rejects. A superpowers repo whose
plan filenames omit the slug must configure a pattern that captures them, or keep
using draft-before-`change start`. Recorded in `docs/limitations.md`; the shipped
skeleton carries commented patterns for both superpowers candidate dirs.

The escape hatch that exists today — draft-before-`change start` — is real and is
named in the design docs, but it appears **nowhere in `getting-started.md` and
nowhere in the injected `AGENTS.md` section**. The agent that has to walk it is
never told it exists. Worse, `SUGGESTIONS["INTENT_DECLARED"]` tells the agent to
"Draft a plan" — an action the same gate then blocks, which is exactly the loop
that produced pothole ⑩ ("gated state → use Bash"), i.e. documented self-bypass.

### Which side is right

The gate is wrong. Its purpose (VISION, `d-single-gate-policy`) is **no source
edits before plan approval**. A plan document is not source. Blocking it prevents
nothing and evicts the "think it through" phase from the tool. Holding the gate
as-is would mean conceding that the adapter automation line is unimplemented, not
merely undocumented.

## Design

### One rule, stated once

> **The gate governs files that will enter git as product. It does not govern the
> change's own thinking artifacts.**

Three concrete narrowings follow from that sentence.

### 1. Plan artifacts are allowed in `INTENT_DECLARED`

Identity of "is this a plan document" must be decided by **configuration or
framework convention — never by the governed agent**. The `PLAN_REJECTED`
carve-out is safe precisely because `plan_artifacts` is recorded by the
*submission* (`plan ready --scope`); in `INTENT_DECLARED` that list is empty by
construction, so a self-declared marker would be the only alternative — and the
agent can add a marker to any `.md`, including `AGENTS.md` (the file that tells it
not to bypass the gate) and `docs/decisions/*.md` (ratified rules).

New tracked config:

```yaml
# .harness/plan-paths.yaml   (tracked — editing it must itself pass the gate)
version: 1
plan_paths:
  - "docs/plans/*{slug}*.md"            # default
  # - "openspec/changes/{slug}/**/*.md" # openspec layout
  # - "specs/{slug}/design.md"          # your own convention
```

**Guard rails (both mandatory, validated at load):**

1. every pattern must contain `{slug}` — binds the allowance to *this* change;
   `**/*.md`, `AGENTS.md`, `README.md`, `src/**` are all rejected by construction,
   so no blacklist has to be maintained;
2. the resolved path must end in `.md` (case-insensitive), checked **after**
   canonicalization so a `docs/plans/x.md → src/evil.py` symlink cannot launder.

The default is a glob, not an exact name, because this repo's own convention puts
the slug in the middle: `2026-07-20-init-wizard-progressive-disclosure-design.md`
for change `init-wizard-progressive-disclosure`, and one change routinely has both
a `-design.md` and an `-implementation.md`. An exact `{slug}.md` matches **zero**
of this repo's existing plan documents.

Config lives in the **tracked** family (`source-paths.yaml`, `gates.yaml`,
`review-governance.yaml`), never the gitignored family
(`review-profiles.local.yaml`, `state.yaml`) — so widening the allowance is itself
a gated edit.

### 2. A scratch area, allowed in every state

`.harness/scratch/<slug>/**` is allowed regardless of lifecycle state.

Rationale: it never enters git, never enters a review bundle, never reaches a
merge gate. Blocking it prevents nothing; it only pushes the agent toward the
shell (pothole ⑩'s other half). Allowing it in **all** states — rather than
enumerating "the states where thinking is appropriate" — is what lets the gate
keep a one-sentence rule instead of a second table.

Consequence: a vibe session has somewhere to put option comparisons, code-reading
notes, and throwaway sketches without inflating the conversation context (the
context-rot concern this project's own doc-lifecycle research raised) and without
losing them when the session ends.

Added to the managed `.gitignore` block.

### 3. `gitignore`-based allowance is REJECTED

The tempting simplification — "if git doesn't track it, let it through" — opens a
fatal hole. These are all gitignored:

| path | what it is |
|---|---|
| `.claude/settings.local.json` | the gate's own hook registration |
| `.codex/hooks.json` | same, for Codex |
| `.harness/state.yaml` | the state the gate reads |
| `.harness/gate-disabled` | **the kill switch** |

Under that rule a blocked agent writes `.harness/gate-disabled` and the gate is
gone. The allowance must be a **hard-coded path whitelist**, and the whitelist
must be compared **after** `canonical_relpath` resolves `..` and symlinks, so
`.harness/scratch/../gate-disabled` resolves outside the whitelist and blocks.

### Out-of-repo paths stay blocked

`canonical_relpath` returns `None` for anything outside the workspace, and the
gate falls through to the table (BLOCK). That is deliberate fail-safe behaviour
(the same reason Codex's `file=None` can never be allowed) and is **not** relaxed
here. The scratch area is a *designated* safe location; "outside the repo" is an
unbounded one that includes `~/.ssh` and `~/.claude`.

## Non-goals

- **Not** relaxing source edits in any state. The gate's core purpose is untouched.
- **Not** relaxing `AWAITING_PLAN_REVIEW`. Editing the plan mid-review desyncs the
  reviewer's frozen target; the scratch area covers "draft my response to the
  review" during that window.
- **Not** an agent-triggerable unlock verb — the allowance is derived from tracked
  config + the active slug, never from a state the agent can set.
- **Not** removing draft-before-`change start`. It keeps working; it stops being
  the *only* path.

## Known taxes

- **`d-single-gate-policy` re-ratification.** `gates/decisions.py` carries
  `@decision:d-single-gate-policy`, whose ratified body says policy lives in *two*
  literals in that module. This change adds a third (the scratch whitelist) plus
  the plan-path resolution. Editing the body trips the text lock → `decision
  ratify` again (bite-test re-runs) and reconcile. Potholes ⑥/⑰.
- **Self-reference.** The gate this change fixes is still the old one while the
  change is being planned, so this very design document had to be written *before*
  `change start` — the exact path being retired. Same shape as PR #85, which
  authorized in-gate plan revision but could not use it on itself.
- **Derived docs.** New CLI surface (`change start --plan`, if kept) feeds the
  autogenerated `docs/cli-reference.md`; `sync --agents-md` must re-render the
  injected section so the agent is finally told about the authoring space.
- **Docs to correct.** `getting-started.md:315` (currently false), the
  `SUGGESTIONS["INTENT_DECLARED"]` string (currently sends the agent into a loop),
  `limitations.md`, and the `AGENTS.md` subsection.

## Open decision to record

The one-sentence rule — *the gate governs files that will enter git as product* —
plus the rejection of gitignore-based allowance (with the `gate-disabled`
evidence) is hard to reverse, surprising without context, and the result of a real
trade-off. It should become a ratified decision record in `docs/decisions/`, armed
if a non-hollow check can be written for it.
