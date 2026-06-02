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

<!-- super-harness no-agent-adapter-installed -->

### Before opening PR

Ensure `super-harness verify` passes (tests / lint / build / anchor sentinels).
If using a `done` skill, run `super-harness done <slug>` instead—it triggers
verify and emits the lifecycle event automatically.

### File scope

When implementing a change, edit only files in the declared `scope.files`
(see the plan artifact). Edits outside scope trigger drift warnings.

<!-- super-harness section end -->
