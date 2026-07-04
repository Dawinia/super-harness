---
name: discovering-architecture-norms
description: Use when a team adopting super-harness needs to figure out which architecture norms to lock in an existing or greenfield codebase, and wants candidate rules surfaced from the code as hypotheses to judge before anything is ratified.
---

# Discovering architecture norms

## Overview

super-harness ships the mechanism to *lock* an architecture norm (`decision new` →
write a check → `ratify` → `decision check`) and the guide that teaches *how* to
write one check:
https://github.com/Dawinia/super-harness/blob/main/docs/architecture-fitness.md

This skill covers the prior step: **which rules should we lock?** In a mature
codebase the architecture already exists implicitly; the maintainer's mental model
drifts from what the code does. You sweep the code, surface candidate norms ranked by
architectural strength, and hand them to a human as **hypotheses to judge** — you
never lock anything and never assert a candidate is correct.

**Core principle: you propose, the human ratifies.** Discovery reads norms out of the
code (recover mode) or out of product intent (greenfield mode). Either way the output
is a candidate list for judgment, not a set of rules to apply.

## When to use

- A team just installed super-harness and asks "now what do I lock?"
- You want an on-ramp from "the architecture is in our heads" to a concrete list.
- Greenfield: little/no code yet, but the team wants a few load-bearing norms early.

## The output contract (load-bearing — read first)

Every candidate you emit MUST be framed as a hypothesis, carry its evidence, be
marked clean-vs-violated, pass the why/what-breaks test, and end by handing off to the
lock mechanism. You do **not** tell the human to lock, you do **not** rank by gut, and
you do **not** manufacture a rule from a zero. See Red flags below.

## Method — sweep four sources

Work these in order as a **default, not a law** (a load-bearing rule from a later
source can outrank an early one). The import graph alone is insufficient — the
highest-value norm often lives outside it (process isolation, i18n completeness).

1. **Framework / stack.** Read the manifests (package.json, lockfiles, build config)
   and ask "what boundaries does this stack imply?" Examples of stack-implied
   boundaries (plain text, adapt to what you find):

   ```
   Electron    → renderer/UI code must not import electron / fs / path /
                 child_process / os; it crosses to the main process only via a
                 preload bridge (ipcRenderer). Process isolation = security.
   Next.js     → server-only modules must not leak into client bundles.
   hexagonal / → the domain must not import the framework/adapters
     onion         (domain ⊥ infrastructure).
   layered API → higher layers may depend downward only.
   ```

   This source is often the highest yield and is invisible to a pure import-graph
   scan, so do it first and do it explicitly.

2. **Existing lint / type / script config.** Scan for norms **already encoded but not
   ratified** — a tsconfig strict flag, an ESLint/Biome rule, a custom validator under
   scripts (e.g. an i18n-completeness check). These are the *cheapest* to bind: wrap
   the existing script as a decision's check. Surface each one you find.

3. **Import dependency graph.** Build an approximate graph (a grep/heuristic scan is
   fine here — you emit hypotheses, and the precise per-language tool such as
   import-linter or dependency-cruiser is used later at the *lock* step per the guide).
   Flag when your graph is approximate rather than silently under-reporting. Rank
   candidates by **asymmetry, not raw zero counts** (see rubric below).

4. **Directory / naming patterns.** Placement and naming conventions. Lock these
   cautiously — high ceremony risk — and only if they survive the why/what-breaks test.

## The asymmetry rubric (import graph)

A zero-import fact is *descriptive*, not a rule. What earns a candidate is an
**asymmetry** that implies an intended direction:

```
strong one-way A→B, reverse ≈ 0   → a real intended layer; propose "B ⊥ A"
                                     (lock the reverse edge).
pure sink (all edges point in)    → propose "this layer imports nothing internal".
symmetric zero (A,B never touch)  → SKIP. No evidence of intent; proposing it would
                                     ossify an accident.
symmetric large (A↔B both ways)   → a cycle smell; flag it, do NOT propose it as a
                                     lockable direction rule.
```

A leak inside an otherwise-clean asymmetry is *stronger* evidence of a real rule than
a pristine zero — the direction the leak violates is the direction the team meant.

## Rank by multi-factor strength

Score each candidate on four factors, not a fixed category order:

- **protected capability** — does it guard a real quality (security/isolation,
  headless testability, replaceability, no-cycles)?
- **evidence strength** — asymmetry / sink-shape / already-mechanized-in-config beats
  a bare coincidental zero.
- **blast radius** — how much breaks if violated.
- **clean vs violated** — currently held (lockable now, prevents regression) vs
  leaking (must be handled first, see below).

The prior *framework-boundary + cross-cutting-correctness > layering > naming* is a
**useful default**, not a law: a load-bearing layering rule (domain ⊥ framework in a
hexagonal backend) can and should outrank a weak framework hint.

## The why / what-breaks test

Every candidate must answer, in one line: **why does this norm exist, and what
concretely breaks if it is violated?** ("Renderer imports fs → the sandbox is gone."
"lib imports components → logic can't run or test headless.") A candidate that cannot
answer this is ceremony — drop it.

## Clean vs violated → how to hand off

Mark each candidate's current status, because it changes the human's options:

- **Clean (currently held):** the human *may* lock it now — ratifying freezes the
  invariant and prevents regression.
- **Violated (leaking today):** do **not** suggest ratifying it as-is. Ratify runs a
  bite-test that requires the check to pass on the current tree, so a red rule cannot
  be ratified. The human's options are **fix-first** (repair the leak, then lock) or a
  **known-violations baseline / ratchet** (e.g. dependency-cruiser known-violations,
  import-linter ignore_imports) so new leaks are blocked while existing ones are
  burned down. State this for every violated candidate.

## Hand off to the lock mechanism (never lock yourself)

Close by pointing the human at the mechanism — do not restate it:

> For each candidate you choose to lock: `decision new` → write the check +
> counterexample per
> https://github.com/Dawinia/super-harness/blob/main/docs/architecture-fitness.md
> → `ratify` (bite-test) → `decision check`.

You surface and rank; the human decides which hypotheses become rules.

## Greenfield branch (detect + short route)

If the repo has little or no code, **do not mine an empty graph** — you would emit
garbage. Detect this first and route to a short branch:

- Don't mine. Instead **offer** a few high-confidence **intent** norms derived from the
  product docs / vision / chosen stack, as candidate decisions the human may ratify. A
  ratified decision needs no check — it can be a body-frozen text decision.
- Surface any validator/check that **already fits** as a candidate decision check.
- **Defer** layering rules until a skeleton exists, then switch back to recover mode.
- Advise ratifying sparingly — early architecture churns.

This branch is *intent-prescriptive* (sourced from intent, not observed from code),
but the philosophy is unchanged: you offer, the human ratifies. Keep it a short
routing branch so recover-mode stays the skill's center of gravity.

## Red flags — you are drifting, stop

- Writing "LOCK this" / "you should enforce" → you are deciding reasonableness.
  Present it as a hypothesis and hand off instead.
- Recommending ratifying a currently-violated rule → it fails the bite-test. Route to
  fix-first or a baseline/ratchet.
- Proposing a rule from a symmetric zero → you are ossifying an accident. Skip it.
- Ranking by raw import counts → rank by asymmetry, evidence, and protected capability.
- Only reading the import graph → you missed the framework-boundary and config sources,
  which are usually the highest-value norms.
- Skipping the why/what-breaks line → that is how ceremony rules sneak in.
