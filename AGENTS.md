<!-- super-harness section begin · v0.1.0 · DO NOT EDIT MANUALLY -->
## Super-harness conventions

This project uses super-harness to ensure AI coding reliability.

### Branch naming

Branch naming is YOURS — keep whatever convention your team already uses.
super-harness identifies a change by its **slug**, which it carries explicitly
in the PR metadata block (and, for framework adapters, the artifact frontmatter)
— NOT in the branch name. Naming a branch after the slug
(e.g. `2026-05-26-add-l1-anchors`) is a convenient default that lets CI resolve
the change with zero config, but it is optional, not required.

### PR creation

Use your framework's native PR command:

<!-- super-harness framework: plain -->
- No framework: drive lifecycle via `super-harness change start <slug>` / `super-harness plan ready <slug>` / `super-harness done <slug>`.
<!-- /super-harness framework: plain -->

<!-- super-harness framework: superpowers -->
- Drive the lifecycle with the superpowers skills (brainstorming → writing-plans → TDD). Plans live under `docs/plans/`.
- Mark an artifact for super-harness with YAML frontmatter: `change: <slug>` (identity) plus optional `stage: design|plan`.
  - `stage: design` declares intent; `stage: plan` (or omitted) means plan ready. A plan may also carry `scope` / `tier_hint`.
- Branch naming is yours — the slug travels in the `change:` frontmatter (and PR metadata), not the branch name.
<!-- /super-harness framework: superpowers -->

super-harness will automatically append a metadata block to your PR description
between `<!-- super-harness:metadata -->` markers.
**Do not modify content between those markers manually.**

### Agent-specific guidance

<!-- super-harness agent: claude-code -->
### super-harness (Claude Code)

A **PreToolUse** hook is enabled for this workspace. `Edit` / `Write` /
`MultiEdit` / `NotebookEdit` tool calls are blocked by super-harness when the
current change state forbids the mutation (deterministic gate enforcement).

A **Stop** hook also runs a turn-end authoring-time conformance check: when you
finish a turn, any ratified decision that opted in (`authoring_time: true`) has its
check run once, and a failing check blocks the stop with a **non-blocking advisory**
naming the violated decision — so you can self-correct before the merge gate. It never
undoes your edit and never blocks twice (it nudges once per turn); the merge gate is
the authoritative floor.

When a tool call is blocked by the gate:
- Run `super-harness status` to see the current change, its state, and why the
  edit was rejected, plus the next valid step.
- Resume context for a change with `super-harness change resume <change_id>`.
- **Revising a rejected plan is authorized in-gate:** in `PLAN_REJECTED`, editing
  the change's own recorded plan document (a marked `.md` in the declared scope) is
  ALLOWED through the normal `Edit`/`Write` tools — that is the intended reject-loop
  path, not a bypass. Source files stay blocked. Do not write plan revisions through
  the shell to dodge the gate.
- **If a tool call is blocked by the gate:** stop, and surface the block plus the
  next valid step (`super-harness status`) to the human. Do **not** try to disable
  or work around the gate yourself — overriding it is a **human-only** decision, and
  any bypass is recorded and disclosed at the merge gate. Whether to override is the
  human's call.

#### Review protocol

super-harness does NOT start, spawn, or host reviewers. It compiles immutable
contracts and records independent receipts. Tracked project requirements live in
`.harness/review-governance.yaml`; each user's explicit models and producer options
live in the gitignored `.harness/review-profiles.local.yaml`. Do not assume a Claude
Code `Task` subagent is the review protocol, and do not substitute an in-session
self-review for an external or human source.

For each review epoch:

1. Commit the exact in-scope change, then run `super-harness review prepare
   <change> --reviewer <name>` once.
2. Run `super-harness review begin <change> --reviewer <name>` to freeze the
   automated round. The command returns per-run prompt, schema, output, and
   invocation files; it never invokes the producer.
3. The caller runs every issued invocation outside super-harness, unchanged and in
   listed order. Apply the source's explicit model and agent-specific options
   verbatim. Do not edit while any issued run is pending.
4. Import each completed output with `super-harness review result import ...`; if a
   producer crashes, record it once with `super-harness review run fail ...`.
   Collect every source before responding to findings, even if one reports a
   blocker. Then batch the fixes and prepare one follow-up round.

The frozen inspection target is strict: findings may address only its exact range
and files. A reviewer may read unchanged repository material as supporting context.
It must continue the whole target after finding a blocker. If the target itself is
insufficient, return `scope_sufficient: false` with a finding; never widen it to the
whole PR ad hoc. A code-only finding fix does not trigger plan review unless the
approved plan, scope, or requirements changed; use `plan redeclare` when they did.

Human review is first-class: use `review human inspect`, validate a verdict with
`review human draft`, then leave `review human confirm` to a human in a TTY. An
agent must never confirm the human nonce. `review skip` remains a disclosed escape
hatch; a code-review skip needs an explicit override and reason to pass attestation.
<!-- /super-harness agent: claude-code -->

### Before opening PR

Ensure `super-harness verify` passes (tests / lint / build / anchor sentinels).
If using a `done` skill, run `super-harness done <slug>` instead—it triggers
verify and emits the lifecycle event automatically.

### File scope

When implementing a change, edit only files in the declared `scope.files`
(see the plan artifact). Edits outside scope trigger drift warnings.

### Decision conformance

Ratified decisions under `docs/decisions/` are binding: super-harness
hash-locks each decision's text and, where configured, attaches an executable
check. Treat `super-harness decision check` as a LOCAL SENSOR you consult while
you work — CI runs it too as the un-bypassable floor, so keep it green locally.

- **At natural checkpoints** (a chunk done, before you commit) run
  `super-harness decision check --changed`. A non-zero exit means you violated a
  ratified decision or edited a ratified decision's body text — fix it before
  continuing; don't push the drift downstream to CI.
- **Don't hand-edit the body of a ratified decision.** Its text is hash-locked;
  re-ratifying (`super-harness decision ratify <id>`) is the only unlock, and is
  a deliberate, recorded act.
- **Attaching an executable check to a decision?** Before you propose it, run
  `super-harness decision ratify <id> --dry-run` to confirm the check actually
  bites (runs the bite-test without ratifying).
- `super-harness decision check` (full) and `super-harness doc check` are also
  CI gates — keep both green locally so a push never bounces.

**Arming a decision with a check (the craft).** A check is a shell snippet that
exits nonzero when a decision is violated; `ratify` bite-tests it so it can't be
hollow. Writing one that catches violations without false positives is judgment —
yours, not the tool's — and the recipe is:

- Pick the **brittle one-token signature** of a violation, not a broad word
  (`^import requests`, not `requests`, which also hits prose / yaml).
- Prefer import/access patterns over bare substrings to dodge prose false positives.
- The check runs through the host's `/bin/sh` and `grep`, so prefer portable
  patterns (avoid GNU-only `grep` extensions); it **must exit nonzero on
  violation** (`! grep ...` inverts grep's exit).
- A denylist is coarse by construction (`^import` misses `from X import …` forms);
  widen deliberately and record the ceiling in the decision body.
- **Scope the grep to source paths (e.g. `src/`), never `.`** — at ratify the
  check runs over the whole tree, so a bare `.` scans the decision file itself
  (which holds the counterexample) and reports "check fails on current code".
- Add a check + a minimal counterexample, then
  `super-harness decision ratify <id> --dry-run` until it reports `bites`:

  ```check
  ! grep -rn '<brittle pattern>' <scoped paths>
  ```

  ```counterexample path=<relative/path>
  <one minimal violating line the check above must catch>
  ```

- **If there is no brittle signature, leave it context-only (tier-3)** — do not
  invent a hollow check just to have one.
- **The check MUST be read-only and reentrant.** With `authoring_time: true` the
  check runs concurrently with the other armed checks on every turn end, so it must
  not write source, caches, `.pyc`, lock files, or any temp under the working tree
  (two checks writing the same path would race). A conformance check should be a pure
  predicate — e.g. use `lint-imports --no-cache` so no cache file is written.
- **Keep the armed authoring set small.** Each armed check spawns a subprocess
  concurrently every turn end; a large armed set misuses the interactive budget (CI
  is the exhaustive path).
- **Not sure which decisions to make?** To discover candidate architecture norms
  in an existing codebase, point your agent at the discovering-architecture-norms
  skill: https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md
  (private repo during v0.1 — the link needs repo access until the public release).

<!-- super-harness section end -->
