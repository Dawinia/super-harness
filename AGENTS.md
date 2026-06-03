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

<!-- super-harness framework: superpowers -->
- Drive the lifecycle with the superpowers skills (brainstorming → writing-plans → TDD). Plans live under `docs/plans/`.
- Mark an artifact for super-harness with YAML frontmatter: `change: <slug>` (identity) plus optional `stage: design|plan`.
  - `stage: design` declares intent; `stage: plan` (or omitted) means plan ready. A plan may also carry `affected_anchors` / `scope` / `tier_hint`.
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

<!-- super-harness section end -->
