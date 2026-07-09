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
- **If a tool call is blocked by the gate:** stop, and surface the block plus the
  next valid step (`super-harness status`) to the human. Do **not** try to disable
  or work around the gate yourself — overriding it is a **human-only** decision, and
  any bypass is recorded and disclosed at the merge gate. Whether to override is the
  human's call.

#### Review protocol

super-harness does NOT review for you — it enforces (via the gate) that the
configured number of independent reviewer-source verdicts is recorded before the
lifecycle proceeds, and YOU produce those verdicts. When `super-harness status
<change>` reports a review state, it prints the configured **strategy**
(`subagent` / `human` / `hybrid`) and the independent-source progress:

- **`subagent`** (default) — dispatch a genuinely independent reviewer **subagent**
  (your `Task` tool) to run the checklist, then record the verdict.
- **`human`** — do NOT self-approve. A human reviews and records the verdict; leave
  the change in its review state for them.
- **`hybrid`** — run the subagent first; escalate to a human on a fail (or a Large
  tier change) before recording.

Reviewer **sources** are configured labels from `.harness/policy.yaml`
(`reviewers.sources`). They are not commands that super-harness executes. If
`min_independent` is greater than 1, record each verdict with a different
configured source, e.g. `--source subagent` then `--source external`; the final
approval milestone is emitted only after enough distinct sources have approved.

Checklists & verdict verbs per review state:

- **`AWAITING_PLAN_REVIEW`** (plan-reviewer) — check spec coverage / design / scope /
  declared anchors. Record with `super-harness review approve <change> --reviewer
  plan-reviewer [--source <source>]` or `super-harness review reject <change>
  --reviewer plan-reviewer --reason "<why>" [--source <source>]`. Approve →
  `PLAN_APPROVED` once the configured independent-source threshold is met (gate
  then allows edits); reject → `PLAN_REJECTED` for a revised plan.
- **`AWAITING_CODE_REVIEW`** (code-reviewer) — a code-review approval now REQUIRES a
  structured verdict; a bare `super-harness review approve <change> --reviewer
  code-reviewer` is rejected. The flow:
  1. Commit the in-scope files first — the review digest is taken over the committed
     HEAD diff, so an uncommitted in-scope tree is refused.
  2. `super-harness review prepare <change> --reviewer code-reviewer` — assembles the
     bundle (in-scope diff ∩ scope, out-of-scope drift, spec/plan paths, checklist,
     committed-HEAD digest) to `.harness/pending-reviews/<change>/code-reviewer.bundle.json`.
  3. Hand that bundle to a genuinely independent reviewer **subagent** to run the
     checklist and produce a verdict file (every checklist item gets a status;
     findings required when any item fails; verdict carries the bundle's digest).
  4. `super-harness review approve <change> --reviewer code-reviewer --verdict-file
     <path> [--source <source>]` — the verdict is inlined into the recorded event.
     The approval is refused if the verdict is missing/incomplete (a checklist item
     uncovered), stale (its digest no longer matches the current in-scope committed
     diff), or has any checklist item with status `fail` (record that with `review
     reject` instead). Approve → `READY_TO_MERGE` once the configured
     independent-source threshold is met. (`review reject ... [--verdict-file
     <path>]` records a fail.)
     If the approval comes out of a REJECTED review, the verdict's `prior_findings` must
     dispose EVERY open finding from the prior `code_review_failed` verdicts
     (`disposition: resolved | wontfix`; `wontfix` needs a `note`) or the approve is refused.
  - plan-reviewer: approve/reject take an optional `--verdict-file` (inlined when
    present, never required) — but when one IS provided on an approve, a failing
    checklist item refuses it the same way (any reviewer branch).
- `super-harness review skip <change> --reviewer <name>` PASSes a stuck reviewer, but for
  `code-reviewer` a BARE skip is a MERGE-GATE BLOCKER (`attest verify` fails). To merge with
  a skip you must record a deliberate, disclosed override:
  `review skip <change> --reviewer code-reviewer --override --reason "<why>"`.

When you do run a subagent, run a genuinely independent one — don't self-rubber-stamp.
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
