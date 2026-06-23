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

When a tool call is blocked by the gate:
- Run `super-harness status` to see the current change, its state, and why the
  edit was rejected, plus the next valid step.
- Resume context for a change with `super-harness change resume <change_id>`.
- **Escape hatch (if the gate is wrong):** from the repo root, `touch
  .harness/gate-disabled` to disable enforcement immediately, and `rm
  .harness/gate-disabled` to re-enable. This works even when edits are blocked
  (the gate never blocks `Bash`).

#### Review protocol

super-harness does NOT review for you — it enforces (via the gate) that a review
verdict is recorded before the lifecycle proceeds, and YOU produce the verdict.
When `super-harness status <change>` reports a review state, it also prints the
configured **strategy** for that reviewer (`subagent` / `human` / `hybrid`):

- **`subagent`** (default) — dispatch a genuinely independent reviewer **subagent**
  (your `Task` tool) to run the checklist, then record the verdict.
- **`human`** — do NOT self-approve. A human reviews and records the verdict; leave
  the change in its review state for them.
- **`hybrid`** — run the subagent first; escalate to a human on a fail (or a Large
  tier change) before recording.

Checklists & verdict verbs per review state:

- **`AWAITING_PLAN_REVIEW`** (plan-reviewer) — check spec coverage / design / scope /
  declared anchors. Record with `super-harness review approve <change> --reviewer
  plan-reviewer` or `super-harness review reject <change> --reviewer plan-reviewer
  --reason "<why>"`. Approve → `PLAN_APPROVED` (gate then allows edits); reject →
  `PLAN_REJECTED` for a revised plan.
- **`AWAITING_CODE_REVIEW`** (code-reviewer) — check the diff against the plan (spec
  compliance / anchors planted / quality). Record with `super-harness review approve
  <change> --reviewer code-reviewer` (or `review reject ...`). Approve → `READY_TO_MERGE`.
- `super-harness review skip <change> --reviewer <name>` is an escape hatch (records an
  approval with `reason=manual_skip`) for when a reviewer is stuck.

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
  POSIX BRE/ERE; it **must exit nonzero on violation** (`! grep ...` inverts
  grep's exit).
- A denylist is coarse by construction (`^import` misses `as` / `from` forms);
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

<!-- super-harness section end -->
