# OpenSpec adapter

The OpenSpec adapter teaches super-harness to read changes maintained with the
[OpenSpec](https://www.npmjs.com/package/@fission-ai/openspec) Node CLI
(verified against `openspec@1.3.1`). It is a *framework* adapter: super-harness
reads OpenSpec's on-disk artifacts and emits the matching lifecycle events
(`intent_declared`, `plan_ready`) so the rest of the harness — gates,
verification, state — works without OpenSpec needing to know super-harness
exists. The adapter does not call OpenSpec at runtime except through one
verification check (`openspec validate`).

OpenSpec is auto-detected when the workspace contains both
`openspec/changes/` and `openspec/specs/` directories (created by
`openspec init`).

## Capabilities

| Capability | Implementation |
|---|---|
| `detect` | `openspec/changes/` AND `openspec/specs/` directories exist at the workspace root |
| `observe` | Scans `openspec/changes/<slug>/` (skipping the `archive/` subdir); emits `intent_declared` from `proposal.md` and `plan_ready` from `tasks.md` |
| `verification_checks` | One adapter-provided check: `openspec validate <slug> --strict --json` |
| `agents_md_subsection` | Three-line briefing telling the agent where the change lives, how to validate it, and how to archive after merge |

## Install

```bash
super-harness adapter install openspec
```

Mechanics:

1. Looks up the built-in `OpenSpecAdapter` (no `.claude/`-style hook step —
   framework adapters do not write to `.claude/`).
2. Merges its `verification_checks()` entry into `.harness/verification.yaml`
   under `adapter_provided:` (keyed on the check `id`, so re-installs replace
   in place rather than accumulating duplicates).
3. Persists `{name: openspec, type: framework, version: 0.1.0, enabled: true}`
   into `.harness/adapters.yaml`.
4. Injects the `<!-- super-harness framework: openspec -->` subsection into
   `AGENTS.md` AND evicts the `<!-- super-harness framework: plain -->`
   block `init` wrote (so AGENTS.md doesn't advertise two contradictory
   framework workflows). Eviction is idempotent — re-running `adapter
   install openspec` is a no-op for the plain block once gone. Asymmetric:
   `adapter uninstall openspec` does NOT re-inject the plain fallback
   (v0.2 follow-up — until then, removing your last non-plain framework
   leaves the framework slot empty until you re-run `init --force` or
   `adapter install plain`).

Idempotent: re-running the command rewrites the same yaml row and the same
AGENTS.md block in place. If `AGENTS.md` is absent (you never ran `init`), the
install completes but the subsection is skipped silently — re-run `init`, then
re-run `adapter install openspec` to inject it.

## What it injects into AGENTS.md

The OpenSpec subsection is short by design — the agent already knows OpenSpec
semantics; the subsection just pins the per-workspace commands and reminds it
to archive after merge. Verbatim from
`OpenSpecAdapter.agents_md_subsection()`:

```markdown
<!-- super-harness framework: openspec -->
- OpenSpec change lives in `openspec/changes/<slug>/` (proposal.md / tasks.md / specs/ deltas).
- Validate before push: `openspec validate <slug> --strict`.
- After merge, fold spec deltas into `openspec/specs/`: `openspec archive <slug>`.
<!-- /super-harness framework: openspec -->
```

The marker comments are load-bearing — `adapter install` / `adapter uninstall`
locate the block by exact marker match. Do not edit content between the
markers manually; re-run `adapter install openspec` if it drifts.

## Common issues

- **`openspec: command not found` when running `verify`.** The verification
  check shells out to the OpenSpec Node CLI; super-harness does not bundle it.
  Install it explicitly: `npm install -g @fission-ai/openspec@1.3.1`.
- **`openspec validate` exits non-zero in CI.** Run it locally first:
  `openspec validate <slug> --strict --json`. Exit `0` is pass; non-zero with
  JSON output on stdout is a validation failure (the JSON body lists the
  offending sections of `proposal.md` / `tasks.md` / `specs/`).
- **`adapter scan-once openspec` reports `0 events emitted` after editing
  `proposal.md`.** The adapter only emits each `(change_id, event_type)` pair
  once — re-emits are deduped against `.harness/events.jsonl`. Inspect with
  `super-harness event log <slug>` to confirm `intent_declared` already
  exists.
- **`emit failed for change <slug> event 'plan_ready'`.** OpenSpec lets you
  create `tasks.md` before `proposal.md`, but super-harness's lifecycle
  requires `intent_declared` to precede `plan_ready`. Add the missing
  `proposal.md` and re-run `adapter scan-once openspec`.
- **Worried that archived changes will re-emit events?** They will not —
  `scan_changes` explicitly skips `openspec/changes/archive/` (see
  `openspec.py:106`), so archived changes never generate fresh events.

## Uninstall

```bash
super-harness adapter uninstall openspec
```

Mechanics (reverse of install):

1. Calls `OpenSpecAdapter.on_uninstall()` (no-op — framework adapters touch
   no hook files).
2. Removes the `openspec` row from `.harness/adapters.yaml`.
3. Prunes the `openspec-validate` row from
   `.harness/verification.yaml.adapter_provided` by `(provided_by, id)` match.
4. Removes the `<!-- super-harness framework: openspec -->` subsection from
   `AGENTS.md`, restoring the framework slot to the `plain` block.

Your `openspec/` directory is left untouched — uninstall removes only the
super-harness wiring, not OpenSpec's own artifacts.

## See also

- [`docs/getting-started.md`](../getting-started.md) — the end-to-end
  walkthrough that installs this adapter alongside `claude-code`.
- [`docs/cli-reference.md`](../cli-reference.md) — the full
  `super-harness adapter` command surface.
- [`examples/demo-openspec-claude/`](../../examples/demo-openspec-claude/) —
  a runnable demo wiring OpenSpec + Claude Code through the full lifecycle.
