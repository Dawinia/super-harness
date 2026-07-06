# Plain adapter

The Plain adapter is super-harness's *framework fallback*. When a workspace
does not match any other framework adapter's `detect()` (no `openspec/`
layout, no Spec Kit metadata, etc.), the dispatcher force-activates Plain so
the lifecycle still works — you drive it by hand instead of having artifacts
on disk do it for you.

> **v0.1 note — Plain is hand-driven.** Without framework artifacts on disk to
> drive it, you advance the lifecycle with explicit CLI verbs: `super-harness
> plan ready <slug>` emits `plan_ready` (`INTENT_DECLARED → AWAITING_PLAN_REVIEW`)
> and `super-harness review approve --reviewer plan-reviewer` advances
> `AWAITING_PLAN_REVIEW → PLAN_APPROVED`. Multi-stage (multiple sequential
> reviewers) plan review is v0.2 (see [Limitations](../limitations.md)). The
> `plan ready` references below mirror what Plain's `agents_md_subsection()`
> returns.

Plain is a framework adapter (same ABC as OpenSpec), but it is deliberately
inert: it emits no events, declares no verification checks, and watches no
paths. It exists so that the rest of the system has *some* framework adapter
to bind to, even on a fresh repo with no spec framework installed.

## Capabilities

| Capability | Implementation |
|---|---|
| `detect` | Always returns `False` — Plain is never auto-detected; the dispatcher force-activates it only when every other framework adapter's `detect()` returned `False` |
| `observe` | No-op — yields no events; the user drives the lifecycle via `super-harness change start` / `plan ready` / `done` |
| `verification_checks` | None — Plain contributes no `adapter_provided` checks |
| `agents_md_subsection` | A one-bullet briefing pointing the agent at the manual CLI lifecycle commands |

## Install

```bash
super-harness adapter install plain
```

Mechanics (intentionally minimal):

1. Looks up the built-in `PlainAdapter` (`is_fallback = True`).
2. Skips the `verification.yaml` merge — `verification_checks()` returns `[]`.
3. Persists `{name: plain, type: framework, version: 0.1.0, enabled: true}`
   into `.harness/adapters.yaml`.
4. Injects the `<!-- super-harness framework: plain -->` subsection into
   `AGENTS.md` (re-injecting it if you previously removed it).

In practice you rarely need to install Plain explicitly: `super-harness init`
already renders the Plain subsection into `AGENTS.md` as the default
framework block, and the dispatcher activates it implicitly. Run the install
command only when you want an explicit `adapters.yaml` row for Plain (for
example, to make a CI environment's adapter set match a local one verbatim).

## What it injects into AGENTS.md

The Plain subsection points the agent at the manual lifecycle CLI commands —
nothing else, because nothing else is installed. Verbatim from
`PlainAdapter.agents_md_subsection()`:

```markdown
<!-- super-harness framework: plain -->
- No framework: drive lifecycle via `super-harness change start <slug>` / `super-harness plan ready <slug>` / `super-harness done <slug>`.
<!-- /super-harness framework: plain -->
```

The marker comments are load-bearing — `adapter install` / `adapter
uninstall` and `init`'s outer-section renderer locate the block by exact
marker match. Do not edit content between the markers manually; re-run
`init --force` or `adapter install plain` if it drifts.

## Common issues

- **`super-harness status` shows no events even after I created proposal /
  task files.** Plain emits no events by design. Run `super-harness change
  start <slug>` to register a change. Advancing past `INTENT_DECLARED`
  under Plain currently requires a framework adapter that auto-emits
  `plan_ready` (e.g. OpenSpec on `tasks.md`); see the v0.1 caveat at the
  top of this page for why the `plan ready` CLI verb the AGENTS.md block
  advertises is not yet shipped. If you want automatic event emission
  from on-disk artifacts, switch to a framework adapter (`adapter install
  openspec`).
- **`super-harness adapter scan-once plain` reports `0 events emitted`.**
  Expected. `PlainAdapter.observe()` returns an empty iterator; `scan-once`
  has nothing to do.
- **`super-harness verify` runs only baseline checks.** Also expected —
  Plain contributes no `adapter_provided` checks. Add your own user checks to
  `.harness/verification.yaml` if you want more (see
  [`docs/cli-reference.md`](../cli-reference.md) for the schema).
- **AGENTS.md shows the Plain block even after I installed OpenSpec.**
  Expected only on installs from before 2026-05-31. Current behavior:
  `adapter install <non-plain-framework>` injects its subsection AND
  evicts the Plain fallback `init` wrote (so AGENTS.md does not carry
  two contradictory workflows). If you still see the Plain block,
  re-run `adapter install openspec` (idempotent — the eviction is a
  no-op when Plain is already gone). Asymmetric: `adapter uninstall
  openspec` does NOT re-inject the Plain fallback (v0.2 follow-up).
- **Plain is listed as `is_fallback: true` — should I worry?** No. The flag
  tells the dispatcher to activate Plain only when nothing else matches; it
  does not mean Plain is deprecated or unsupported.

## Uninstall

```bash
super-harness adapter uninstall plain
```

Mechanics (reverse of install):

1. Calls `PlainAdapter.on_uninstall()` (no-op — Plain installs no hooks).
2. Removes the `plain` row from `.harness/adapters.yaml`.
3. The `verification.yaml.adapter_provided` prune step is a no-op (Plain
   contributed no rows).
4. Removes the `<!-- super-harness framework: plain -->` subsection from
   `AGENTS.md`. If no other framework adapter is installed the framework slot
   falls back to the `init`-time anchor.

Uninstalling Plain does **not** disable super-harness's lifecycle commands —
the shipped v0.1 CLI surface (`change start` / `change abandon` / `change
list` / `change resume` / `verify` / `done`) still works because they are
CLI surface, not adapter surface. Uninstall only removes the documented
"how to drive this by hand" subsection from `AGENTS.md`.

## See also

- [`docs/getting-started.md`](../getting-started.md) — the end-to-end
  walkthrough; mentions Plain as the default framework block before OpenSpec
  is installed.
- [`docs/cli-reference.md`](../cli-reference.md) — the shipped v0.1 CLI
  commands (`change start`, `change abandon`, `change list`, `change
  resume`, `verify`, `done`); note that `plan ready` referenced in the
  AGENTS.md block above is NOT yet shipped (see the v0.1 caveat at the
  top of this page).
- [`docs/adapters/openspec.md`](./openspec.md) — switch to the OpenSpec
  framework adapter to get automatic event emission.
