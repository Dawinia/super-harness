<!-- super-harness section begin · v0.1.0 · DO NOT EDIT MANUALLY -->
## Super-harness conventions

This project uses super-harness to ensure AI coding reliability.

### Branch naming

Branches MUST be named matching a registered super-harness change slug.
Examples: `2026-05-26-add-l1-anchors` / `feat-mobile-auth-flow`

If you use git directly: `git checkout -b <slug>`
If you use a framework command (recommended): the framework auto-creates the branch.

### PR creation

Use your framework's native PR command:

<!-- super-harness framework: plain -->
- No framework: drive lifecycle via `super-harness change start <slug>` / `super-harness plan ready <slug>` / `super-harness done <slug>`.
<!-- /super-harness framework: plain -->

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
